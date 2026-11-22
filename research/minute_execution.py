"""Conservative 1-minute, post-snapshot, hypothetical order-sequence analysis.

Uses actual archived scan timestamps and subsequently downloaded OKX OHLCV.
No exchange orders, no tick/quote timestamps, no verified fills or expectancy.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import math

from research.futures_scenario import Scenario, _check_plan, _pnl

MINUTE_MS = 60_000
SIGNAL_MS = 900_000


def first_full_minute(observed_ms):
    """Discard the minute containing a scan unless it began at the exact scan time."""
    if type(observed_ms) is not int or observed_ms < 0:
        raise ValueError("invalid archived observation time")
    return ((observed_ms + MINUTE_MS - 1) // MINUTE_MS) * MINUTE_MS


def _result(name, **extra):
    return {"outcome": name, "modelled_execution": False,
            "exchange_fill_verified": False, "net_r_scenario": None, **extra}


def _parse(rows, symbol, starting_ms, end_ms):
    """Fail closed for unexpected source, gaps, duplicates and invalid OHLC."""
    normalized = []
    for r in rows:
        if (r.get("source") != "okx_history_candles" or
            r.get("point_in_time_eligible") is not False or
            r.get("symbol") != symbol or r.get("timeframe") != "1m"):
            raise ValueError("1m data provenance not verified")
        ts = r.get("open_ms")
        if type(ts) is not int or ts % MINUTE_MS:
            raise ValueError("invalid minute timestamp")
        o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
        if (not all(math.isfinite(x) for x in (o,h,l,c)) or
            not 0 < l <= min(o,c) <= max(o,c) <= h):
            raise ValueError("invalid minute OHLC")
        if starting_ms <= ts < end_ms:
            normalized.append((ts,o,h,l,c))
    normalized.sort(key=lambda r:r[0])
    if len(set(r[0] for r in normalized)) != len(normalized):
        raise ValueError("duplicate minute bar")
    return normalized


def evaluate(candidate, minute_rows, scenario=None):
    """Classify a conditional snapshot plan with 1m bars available *after* observation.

    An OHLC minute containing entry and stop/target is ambiguous even if only
    one barrier is reached, because the barrier may have occurred before entry.
    An ambiguous result is excluded from the modelled PnL sample.
    """
    sc = scenario if scenario is not None else Scenario()
    if candidate.get("decision_at_snapshot") != "GATILHO_CONDICIONAL":
        return _result("BLOCKED_IN_ARCHIVED_SCAN")
    plan = {k: candidate[k] for k in
            ("symbol", "method", "side", "signal_close_ms",
             "entry", "stop", "tp1")}
    _check_plan(plan)
    stamp = candidate.get("first_observed_at_ms")
    start = first_full_minute(stamp)
    signal_close_ms = plan["signal_close_ms"]
    expiry = signal_close_ms + SIGNAL_MS
    if not signal_close_ms <= stamp < expiry:
        return _result("INVALID_OR_EXPIRED_OBSERVATION")
    if start >= expiry:
        return _result("NO_COMPLETE_MINUTE_BEFORE_EXPIRY")
    try:
        spread = float(candidate.get("spread_pct_observed"))
    except (ValueError, TypeError):
        return _result("MISSING_SNAPSHOT_SPREAD")
    if not math.isfinite(spread) or spread < 0:
        return _result("MISSING_SNAPSHOT_SPREAD")
    try:
        sc = replace(sc, assumed_spread_pct=spread)
    except ValueError:
        return _result("INVALID_SCENARIO")
    entry, stop, tp = (float(plan[k]) for k in ("entry", "stop", "tp1"))
    direction = 1 if plan["side"] == "LONG" else -1
    approx_liq_distance = 1 / sc.leverage - sc.maintenance_margin_fraction
    if abs(entry-stop)/entry >= approx_liq_distance:
        return _result("STOP_BEYOND_APPROX_LIQUIDATION")
    horizon_end = expiry + sc.max_hold_bars * SIGNAL_MS
    try:
        bars = _parse(minute_rows, plan["symbol"], start, horizon_end)
    except (ValueError, TypeError, KeyError):
        return _result("INVALID_OR_UNVERIFIED_MINUTE_DATA")
    if not bars:
        return _result("NO_MINUTE_DATA")

    filled_minute = None
    required_ts = start
    for ts, o, hi, lo, close in bars:
        if ts != required_ts:
            return _result("MINUTE_DATA_GAP", gap_from_ms=required_ts,
                           gap_to_ms=ts)
        required_ts += MINUTE_MS
        if filled_minute is None and ts >= expiry:
            return _result("NOT_TRIGGERED_DURING_ACTIVE_WINDOW")
        stop_hit = lo <= stop if direction == 1 else hi >= stop
        target_hit = hi >= tp if direction == 1 else lo <= tp
        if filled_minute is None:
            crossed = hi >= entry if direction == 1 else lo <= entry
            if not crossed:
                continue
            open_beyond = o > entry if direction == 1 else o < entry
            if open_beyond:
                return _result("GAP_BEYOND_EXACT_TRIGGER", entry_minute_ms=ts)
            if stop_hit or target_hit:
                return _result("ENTRY_MINUTE_AMBIGUOUS", entry_minute_ms=ts,
                               stop_touched=stop_hit, target_touched=target_hit,
                               worst_case_net_r_scenario=_pnl(
                                   entry, stop, ts, ts+MINUTE_MS, plan, sc)["net_r"])
            filled_minute = ts
            continue
        if stop_hit and target_hit:
            return _result("EXIT_MINUTE_AMBIGUOUS",
                           entry_minute_ms=filled_minute, exit_minute_ms=ts,
                           worst_case_net_r_scenario=_pnl(
                               entry, stop, filled_minute, ts+MINUTE_MS,
                               plan, sc)["net_r"])
        if stop_hit or target_hit:
            # A price gap beyond the stop is worse than the stop level.
            if stop_hit:
                raw_exit = min(o,stop) if direction == 1 else max(o,stop)
                outcome = "MODELLED_STOP"
            else:
                raw_exit = tp  # no positive price improvement assumed
                outcome = "MODELLED_TP1"
            values = _pnl(entry, raw_exit, filled_minute,
                          ts+MINUTE_MS, plan, sc)
            return _result(outcome, modelled_execution=True,
                           entry_minute_ms=filled_minute,
                           exit_minute_ms=ts, raw_exit_price=raw_exit,
                           net_r_scenario=values["net_r"],
                           applied_snapshot_spread_pct=spread,
                           **{k:v for k,v in values.items() if k!="net_r"})
        if ts+MINUTE_MS >= filled_minute + sc.max_hold_bars*SIGNAL_MS:
            values = _pnl(entry, close, filled_minute,
                          ts+MINUTE_MS, plan, sc)
            return _result("MODELLED_TIME_EXIT", modelled_execution=True,
                           entry_minute_ms=filled_minute, exit_minute_ms=ts,
                           raw_exit_price=close, net_r_scenario=values["net_r"],
                           applied_snapshot_spread_pct=spread,
                           **{k:v for k,v in values.items() if k!="net_r"})
    if filled_minute is None:
        if required_ts < expiry:
            return _result("INSUFFICIENT_MINUTE_DATA_FOR_TRIGGER")
        return _result("NOT_TRIGGERED_DURING_ACTIVE_WINDOW")
    return _result("INSUFFICIENT_MINUTE_DATA_FOR_EXIT",
                   entry_minute_ms=filled_minute)


def summarize(candidates, outcomes):
    if len(candidates) != len(outcomes):
        raise ValueError("candidates/outcomes length mismatch")
    by_method = {}
    for method in ("BREAKOUT","RETEST"):
        selected = [r for c,r in zip(candidates,outcomes) if c["method"]==method]
        by_method[method] = {
            "archived_candidates": len(selected),
            "modelled_exits_not_fills": sum(x["modelled_execution"] for x in selected),
            "ambiguous_minutes": sum("AMBIGUOUS" in x["outcome"] for x in selected),
            "outcomes": dict(Counter(x["outcome"] for x in selected)),
        }
    return {"status": "POST_SNAPSHOT_1M_RESEARCH_ONLY",
            "candidate_count": len(candidates), "by_method": by_method,
            "exchange_fills_verified": 0, "proven_expectancy": False,
            "trading_allowed": False,
            "limitations": [
                "1m OHLC cannot establish intra-minute event ordering",
                "entry activation skips any partial 1m bar at scan time",
                "snapshot spread is not observed bid/ask at assumed entry/exit",
                "taxes and slippage are scenario assumptions; funding is not historical",
                "price touches are not exchange fills; no order book or liquidation evidence",
                "sequential events may be correlated or overlap across instruments",
            ]}
