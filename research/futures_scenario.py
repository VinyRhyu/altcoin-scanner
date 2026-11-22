"""Execution-only, conservative hypothetical USDT-perpetual scenario backtester.

Consumes precomputed BREAKOUT/RETEST plans. This module does NOT discover signals,
fetch prices, execute orders, or establish historical profitability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path

BAR_MS = 900_000
EIGHT_HOURS_MS = 28_800_000


@dataclass(frozen=True)
class Scenario:
    fee_bps_side: float = 5.
    slippage_bps_side: float = 5.
    assumed_spread_pct: float = .05
    assumed_funding_per_8h: float = 0.
    leverage: float = 20.
    maintenance_margin_fraction: float = .005
    max_hold_bars: int = 16

    def __post_init__(self):
        vals = (self.fee_bps_side, self.slippage_bps_side,
                self.assumed_spread_pct, self.assumed_funding_per_8h,
                self.leverage, self.maintenance_margin_fraction)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals):
            raise ValueError("non-finite scenario parameter")
        if (self.fee_bps_side < 0 or self.slippage_bps_side < 0 or
            self.assumed_spread_pct < 0 or self.leverage < 1 or
            self.maintenance_margin_fraction < 0 or
            self.maintenance_margin_fraction >= 1/self.leverage or
            not isinstance(self.max_hold_bars, int) or
            not 1 <= self.max_hold_bars <= 100):
            raise ValueError("invalid scenario cost, margin or horizon")


def _check_plan(p):
    if p.get("method") not in ("BREAKOUT", "RETEST"):
        raise ValueError("unsupported signal method")
    if p.get("side") not in ("LONG", "SHORT"):
        raise ValueError("unsupported side")
    if not isinstance(p.get("signal_close_ms"), int) or p["signal_close_ms"] % BAR_MS:
        raise ValueError("signal timestamp must be a closed 15m boundary")
    if not isinstance(p.get("symbol"), str) or not p["symbol"].endswith("-USDT-SWAP"):
        raise ValueError("invalid swap symbol")
    prices = [p.get(k) for k in ("entry", "stop", "tp1")]
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
               for v in prices):
        raise ValueError("invalid numeric plan")
    e, st, tp = prices
    if not (st < e < tp if p["side"] == "LONG" else tp < e < st):
        raise ValueError("stop/entry/target inconsistent with side")


def _pnl(entry, raw_exit, entry_open_ms, exit_close_ms, plan, sc):
    """Net R on notional, with *assumed* spread, slippage and linear funding."""
    side = 1 if plan["side"] == "LONG" else -1
    half_spread = sc.assumed_spread_pct/200
    slip = sc.slippage_bps_side/10000
    buy_sell_friction = half_spread + slip
    ep = entry * (1 + side*buy_sell_friction)
    xp = raw_exit * (1 - side*buy_sell_friction)
    gross = side*(xp-ep)/ep
    fee = (sc.fee_bps_side/10000)*(1+xp/ep)
    # Not funding settlements: time-proportional scenario; signed positive longs pay.
    hold = max(0, exit_close_ms - entry_open_ms)
    funding = side*sc.assumed_funding_per_8h*(hold/EIGHT_HOURS_MS)
    net = gross - fee - funding
    nominal_risk_fraction = abs(entry-plan["stop"])/ep
    if nominal_risk_fraction <= 0:
        raise ValueError("invalid risk")
    return {"net_r": round(net/nominal_risk_fraction, 9),
            "margin_return_pct_scenario": round(net*sc.leverage*100, 7),
            "gross_return_pct": round(gross*100, 7),
            "fee_cost_pct_notional": round(fee*100, 7),
            "funding_cost_pct_notional_scenario": round(funding*100, 7)}


def simulate(plan, sc):
    """Evaluate one conditional entry for next bar only; unknown intrabar order is not invented."""
    _check_plan(plan)
    direction = 1 if plan["side"] == "LONG" else -1
    e, stop, tp = (float(plan[k]) for k in ("entry", "stop", "tp1"))
    liq_buffer = 1/sc.leverage - sc.maintenance_margin_fraction
    if (e-stop)/e >= liq_buffer if direction == 1 else (stop-e)/e >= liq_buffer:
        return {"outcome": "STOP_BEYOND_APPROX_LIQUIDATION", "executed": False,
                "net_r": None}
    future = plan.get("future_15m", [])
    if not isinstance(future, list) or not future:
        return {"outcome": "INSUFFICIENT_FUTURE_DATA", "executed": False,
                "net_r": None}

    def hit(row):
        high, low = float(row[2]), float(row[3])
        return (low <= stop, high >= tp) if direction == 1 else (
            high >= stop, low <= tp)

    close_ms = plan["signal_close_ms"]
    for index, bar in enumerate(future[:sc.max_hold_bars]):
        if not isinstance(bar, list) or len(bar) < 5:
            return {"outcome": "INVALID_FUTURE_CANDLE", "executed": False,
                    "net_r": None}
        timestamp = bar[0]
        o, hi, lo, cl = [float(v) for v in bar[1:5]]
        if (type(timestamp) is not int or timestamp != close_ms+index*BAR_MS or
            not all(map(math.isfinite, (o, hi, lo, cl))) or
            not (0 < lo <= min(o, cl) <= max(o, cl) <= hi)):
            return {"outcome": "DATA_GAP_OR_INVALID_CANDLE", "executed": False,
                    "net_r": None}
        triggered = hi >= e if direction == 1 else lo <= e
        if index == 0:
            if not triggered:
                return {"outcome": "NOT_TRIGGERED_NEXT_15M", "executed": False,
                        "net_r": None}
            if (o > e if direction == 1 else o < e):
                return {"outcome": "GAP_BEYOND_EXACT_ENTRY", "executed": False,
                        "net_r": None}
        stop_hit, tp_hit = hit(bar)
        exit_close_ms = timestamp+BAR_MS
        if stop_hit and (tp_hit or (index == 0 and o != e)):
            return {"outcome": "INTRABAR_AMBIGUOUS", "executed": False,
                    "net_r": None,
                    "worst_case_net_r_scenario": _pnl(
                        e, stop, close_ms, exit_close_ms, plan, sc)["net_r"]}
        if stop_hit or tp_hit:
            at = stop if stop_hit else tp
            values = _pnl(e, at, close_ms, exit_close_ms, plan, sc)
            return {"outcome": "STOP_TOUCHED" if stop_hit else "TP1_TOUCHED",
                    "executed": True, "bars_to_exit": index+1,
                    "raw_exit_price": at, **values}
        if index+1 == sc.max_hold_bars:
            values = _pnl(e, cl, close_ms, exit_close_ms, plan, sc)
            return {"outcome": "TIME_EXIT", "executed": True,
                    "bars_to_exit": index+1, "raw_exit_price": cl, **values}
    return {"outcome": "UNRESOLVED_IN_AVAILABLE_WINDOW", "executed": False,
            "net_r": None}


def summarize(records, scenario):
    results = []
    for plan in records:
        outcome = simulate(plan, scenario)
        results.append({"symbol": plan["symbol"], "method": plan["method"],
                        "side": plan["side"], "signal_close_ms": plan["signal_close_ms"],
                        **outcome})
    grouped = defaultdict(list)
    for result in results:
        grouped[result["method"]].append(result)
    summary = {}
    for method in ("BREAKOUT", "RETEST"):
        rows = grouped[method]
        resolved = [x["net_r"] for x in rows if x["executed"]]
        wins, losses = [v for v in resolved if v > 0], [v for v in resolved if v < 0]
        summary[method] = {
            "candidates": len(rows), "scenario_resolved": len(resolved),
            "unresolved_or_unfilled": len(rows)-len(resolved),
            "outcomes": dict(Counter(x["outcome"] for x in rows)),
            "mean_net_r_scenario": round(sum(resolved)/len(resolved), 8) if resolved else None,
            "profit_factor_scenario": round(sum(wins)/-sum(losses), 8) if losses else None,
            "ambiguous_worst_case_net_r": round(sum(
                x.get("worst_case_net_r_scenario", 0) for x in rows), 8),
        }
    return {
        "status": "HYPOTHETICAL_EXECUTION_SCENARIOS_ONLY",
        "scenario": vars(scenario), "comparison": summary, "trades": results,
        "is_real_backtest": False, "proven_expectancy": False,
        "warnings": [
            "Input plans must be generated without lookahead from verified point-in-time data",
            "assumed spread/tick/funding may be different from exchange historical conditions",
            "no order-book liquidity, partial fills, real mark-price liquidation or actual funding settlements",
            "ambiguous candles are NOT treated as confirmed trades; worst-case R is shown separately",
            "overlapping signals and portfolio equity curve are NOT modeled",
            "margin returns are illustrative, not actual account returns",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plans-jsonl", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--fee-bps-side", type=float, default=5)
    p.add_argument("--slippage-bps-side", type=float, default=5)
    p.add_argument("--assumed-spread-pct", type=float, default=.05)
    p.add_argument("--assumed-funding-per-8h", type=float, default=0)
    p.add_argument("--leverage", type=float, default=20)
    p.add_argument("--max-hold-bars", type=int, default=16)
    args = p.parse_args(argv)
    scenario = Scenario(fee_bps_side=args.fee_bps_side,
                        slippage_bps_side=args.slippage_bps_side,
                        assumed_spread_pct=args.assumed_spread_pct,
                        assumed_funding_per_8h=args.assumed_funding_per_8h,
                        leverage=args.leverage, max_hold_bars=args.max_hold_bars)
    rows = [json.loads(line) for line in args.plans_jsonl.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    report = summarize(rows, scenario)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print("HYPOTHETICAL_REPLAY", json.dumps(report["comparison"], sort_keys=True))


if __name__ == "__main__":
    main()
