"""Retrospective SOL price-only pilot for BREAKOUT vs RETEST.

The candidate detector uses closed 15m/1h/2h bars, but tick, spread, funding
and exact order activation timing are assumptions. NOT a validated backtest.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from entry_trigger import analyze_both, is_breakout
from experimental_scan import atr14, features
from research.futures_scenario import Scenario, summarize
from research.market_history_audit import DURATIONS_MS


def load_history(directory, symbol="SOL-USDT-SWAP"):
    history = {}
    for tf, duration in DURATIONS_MS.items():
        path = Path(directory) / f"sol_{tf}_30d.jsonl"
        values = [json.loads(line) for line in path.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        if not values:
            raise ValueError(f"empty historical series: {tf}")
        bars = []
        for row in values:
            if (row.get("source") != "okx_history_candles" or
                row.get("point_in_time_eligible") is not False or
                row.get("symbol") != symbol or row.get("timeframe") != tf):
                raise ValueError("missing provenance for historical-only data")
            bars.append([row["open_ms"], row["open"], row["high"], row["low"],
                         row["close"], row["volume_contracts"], row["quote_volume"]])
        if any(b[0]-a[0] != duration for a,b in zip(bars,bars[1:])):
            raise ValueError(f"noncontiguous historical {tf} sequence")
        history[tf] = bars
    return history


def pilot(history, assumed_tick, scenario):
    if not isinstance(assumed_tick, (str, float)) or float(assumed_tick) <= 0:
        raise ValueError("tick proxy must be explicitly provided")
    timeframe = history["15m"]
    available_closes = {}
    for tf in ("1h", "2h"):
        available_closes[tf] = [
            b[0] + DURATIONS_MS[tf] for b in history[tf]]
    stats = {method: Counter() for method in ("BREAKOUT", "RETEST")}
    plans = []
    evaluated_closes = 0
    avoided_retest_atr = 0

    for i in range(34, len(timeframe) - scenario.max_hold_bars):
        close_ms = timeframe[i][0]+DURATIONS_MS["15m"]
        aligned = {"15m": timeframe[:i+1]}
        for tf in ("1h", "2h"):
            n = bisect_right(available_closes[tf], close_ms)
            if n < 35 or close_ms - available_closes[tf][n-1] >= DURATIONS_MS[tf]:
                aligned = None
                break
            aligned[tf] = history[tf][:n]
        if aligned is None:
            continue
        try:
            info = features(aligned)
        except (ValueError, IndexError, ZeroDivisionError):
            continue
        evaluated_closes += 1
        as_of = datetime.fromtimestamp((close_ms+60000)/1000, timezone.utc)
        # Price at the candle close is NOT a historical ticker quote at as_of.
        ticker_proxy = {
            "price": float(timeframe[i][4]), "tick_size": str(assumed_tick),
            "spread_pct": scenario.assumed_spread_pct,
        }
        for side in ("LONG", "SHORT"):
            decisions = analyze_both(aligned, info, ticker_proxy, side, as_of, False)
            for decision in decisions:
                method = decision["method"]
                if decision["status"] != "CONFIRMADO":
                    continue
                stats[method]["confirmed"] += 1
                if method == "RETEST":
                    # Master detector uses current ATR for earlier breakout.
                    # Reject any retest whose prior breakout fails with its own ATR.
                    prev_atr = atr14(aligned["15m"][:-1])
                    prior_passed, _ = is_breakout(
                        aligned["15m"], i-1, side, prev_atr)
                    if not prior_passed:
                        avoided_retest_atr += 1
                        stats[method]["rejected_prior_atr_check"] += 1
                        continue
                if decision["decision"] != "GATILHO_CONDICIONAL":
                    stats[method]["blocked"] += 1
                    continue
                stats[method]["conditional"] += 1
                plans.append({
                    "symbol": "SOL-USDT-SWAP", "side": side,
                    "method": method, "signal_close_ms": close_ms,
                    "entry": decision["entry_trigger_price"],
                    "stop": decision["stop_loss"],
                    "tp1": decision["tp1_projected"],
                    "future_15m": timeframe[i+1:i+1+scenario.max_hold_bars],
                })
    simulated = summarize(plans, scenario)
    return {
        "status": "RETROSPECTIVE_ASSUMPTION_HEAVY_PRICE_SCENARIO",
        "symbol": "SOL-USDT-SWAP",
        "closed_15m_bars": len(timeframe), "evaluated_15m_closes": evaluated_closes,
        "assumed_tick_size": str(assumed_tick),
        "assumed_spread_pct": scenario.assumed_spread_pct,
        "prior_atr_inconsistent_retests_discarded": avoided_retest_atr,
        "detection_summary": {k: dict(v) for k,v in stats.items()},
        "execution_scenario": simulated,
        "is_real_backtest": False, "proven_expectancy": False,
        "warnings": [
            "Candles downloaded later from OKX history API; no point-in-time trade records",
            "Simulated ticker uses 15m candle close at +1min; not historical market price",
            "Static tick and spread proxy may differ from actual market",
            "Signal is hypothetically armed at exact candle close, before +1min scan",
            "Funding is a signed, linear scenario assumption; not actual settlement events",
            "Prior-ATR retest prefilter removes some time-leakage, but master detector may still miss setups",
            "No liquidity/mark price/liquidation execution or fill timestamp verification",
            "Small single-asset window and overlapping signals invalidate edge inference",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", type=Path, required=True)
    p.add_argument("--tick-proxy", required=True)
    p.add_argument("--assumed-spread-pct", type=float, default=.05)
    p.add_argument("--assumed-funding-per-8h", type=float, default=0.)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    history = load_history(args.directory)
    sc = Scenario(assumed_spread_pct=args.assumed_spread_pct,
                  assumed_funding_per_8h=args.assumed_funding_per_8h)
    report = pilot(history, args.tick_proxy, sc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print("RETROSPECTIVE_PILOT", json.dumps({
        "evaluated_15m_closes": report["evaluated_15m_closes"],
        "detection": report["detection_summary"],
        "comparison": report["execution_scenario"]["comparison"],
        "is_real_backtest": report["is_real_backtest"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
