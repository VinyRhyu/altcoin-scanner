"""Capture authentic scanner decisions at archived snapshot times, without hindsight fills.

Outputs observed BREAKOUT/RETEST candidates. Candles after the snapshot are
never used in detection. Candidate existence is NOT execution proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from entry_trigger import analyze_both
from experimental_scan import features
from research.market_history_audit import _collected_ms, resolve_paths


def inspect_snapshots(paths):
    candidate_by_key = {}
    issues = Counter()
    seen = 0
    for path in resolve_paths(paths):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") != "COLLECTED" or data.get("market_type") != "USDT_PERPETUAL":
            issues["invalid_snapshot"] += 1
            continue
        collected_ms = _collected_ms(data.get("collected_utc"))
        now = datetime.fromtimestamp(collected_ms/1000, timezone.utc)
        seen += 1
        for ticker in data.get("universe", []):
            symbol = ticker.get("symbol")
            candles = data.get("candles", {}).get(symbol)
            if not isinstance(candles, dict):
                issues["missing_history"] += 1
                continue
            bars = candles.get("15m", [])
            if len(bars) < 35:
                issues["insufficient_history"] += 1
                continue
            signal_close_ms = int(bars[-1][0]) + 900000
            if not signal_close_ms <= collected_ms < signal_close_ms + 900000:
                issues["stale_or_future_15m"] += 1
                continue
            try:
                info = features(candles)
                decisions = [x for side in ("LONG", "SHORT")
                             for x in analyze_both(candles, info, ticker,
                                                   side, now, False)]
            except (ValueError, KeyError, TypeError, IndexError, ZeroDivisionError):
                issues["signal_evaluation_error"] += 1
                continue
            for decision in decisions:
                if decision["status"] != "CONFIRMADO":
                    continue
                key = (symbol, signal_close_ms, decision["side"], decision["method"])
                record = {
                    "symbol": symbol, "signal_close_ms": signal_close_ms,
                    "first_observed_at_ms": collected_ms,
                    "side": decision["side"], "method": decision["method"],
                    "decision_at_snapshot": decision["decision"],
                    "entry": decision["entry_trigger_price"],
                    "stop": decision["stop_loss"], "tp1": decision["tp1_projected"],
                    "tick_size_observed": ticker.get("tick_size"),
                    "spread_pct_observed": ticker.get("spread_pct"),
                    "reasons": decision.get("reasons", []),
                    "order_allowed": False,
                    "execution_verified": False,
                    "next_bar_already_in_progress_at_snapshot": collected_ms > signal_close_ms,
                }
                if key not in candidate_by_key or collected_ms < candidate_by_key[key]["first_observed_at_ms"]:
                    candidate_by_key[key] = record

    candidates = sorted(candidate_by_key.values(),
                        key=lambda x: (x["signal_close_ms"], x["symbol"],
                                       x["side"], x["method"]))
    per_method = {}
    for method in ("BREAKOUT", "RETEST"):
        rows = [x for x in candidates if x["method"] == method]
        per_method[method] = {
            "confirmed_at_snapshot": len(rows),
            "conditionally_eligible_at_snapshot": sum(
                x["decision_at_snapshot"] == "GATILHO_CONDICIONAL" for x in rows),
            "blocked_at_snapshot": sum(
                x["decision_at_snapshot"] != "GATILHO_CONDICIONAL" for x in rows),
        }
    report = {
        "status": "OBSERVED_SIGNAL_CANDIDATES_NOT_FILLS",
        "snapshots_analyzed": seen, "confirmed_candidates": len(candidates),
        "by_method": per_method, "issues": dict(sorted(issues.items())),
        "candidates": candidates,
        "eligible_for_futures_pnl_backtest": False,
        "caveats": [
            "candidates are deduplicated by symbol/signal close/side/method",
            "tick/spread are authentic at the snapshot, not proven at earlier candle close",
            "the next 15m bar has already begun when the archived scan arrives",
            "without intrabar prints after snapshot, next-bar trigger timing is unknown",
            "funding, fills, liquidation and execution costs not verified",
        ],
    }
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots", nargs="+")
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args(argv)
    report = inspect_snapshots(args.snapshots)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print("POINT_IN_TIME_SIGNALS", json.dumps({
        k: report[k] for k in ("snapshots_analyzed", "confirmed_candidates",
                                "by_method", "issues")}, sort_keys=True))


if __name__ == "__main__":
    main()
