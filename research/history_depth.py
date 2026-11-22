"""Summarize continuous research history depth without claiming backtest readiness."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from research.market_history_audit import DURATIONS_MS


def coverage(report):
    segments = report["segments"]
    grouped = defaultdict(list)
    per_symbol = defaultdict(lambda: defaultdict(list))
    for segment in segments:
        tf = segment["timeframe"]
        duration = DURATIONS_MS[tf]
        bars = int(segment["candles"])
        if bars < 1 or segment["end_open_ms"] - segment["start_open_ms"] != (bars-1) * duration:
            raise ValueError("segment has a gap or invalid count")
        grouped[tf].append(segment)
        per_symbol[segment["symbol"]][tf].append(segment)

    timeframes = {}
    for tf in DURATIONS_MS:
        data = grouped[tf]
        lengths = [x["candles"] for x in data]
        duration = DURATIONS_MS[tf]
        hours = lambda n: round(n * duration / 3_600_000, 3)
        longest = max(lengths, default=0)
        timeframes[tf] = {
            "segments": len(data),
            "symbols": len({x["symbol"] for x in data}),
            "total_candles": sum(lengths),
            "total_segment_hours": hours(sum(lengths)),  # across assets, not calendar span
            "longest_segment_bars": longest,
            "longest_segment_hours": hours(longest),
            "segments_at_least_7_days": sum(x*duration >= 7*86_400_000 for x in lengths),
            "segments_at_least_30_days": sum(x*duration >= 30*86_400_000 for x in lengths),
            "top_five": [
                {"symbol": x["symbol"], "start_open_ms": x["start_open_ms"],
                 "end_open_ms": x["end_open_ms"], "candles": x["candles"],
                 "continuous_hours": hours(x["candles"])}
                for x in sorted(data, key=lambda row: (-row["candles"], row["symbol"]))[:5]
            ],
        }

    symbol_depth = []
    for symbol, series in sorted(per_symbol.items()):
        symbol_depth.append({
            "symbol": symbol,
            "longest_contiguous_hours": {
                tf: round(max((x["candles"] for x in series.get(tf, ())), default=0)
                          * DURATIONS_MS[tf] / 3_600_000, 3)
                for tf in DURATIONS_MS
            },
        })
    return {
        "schema_version": 1,
        "source_snapshot_count": report["source_snapshot_count"],
        "source_integrity_passed": report["source_integrity_passed"],
        "source_segments": len(segments),
        "timeframes": timeframes,
        "symbol_depth": symbol_depth,
        "profitability_backtest_ready": False,
        "notes": [
            "sum of segment hours across symbols is not chronological history length",
            "long segments may still reflect survivorship or dynamic universe selection",
            "length thresholds are diagnostics, not validity thresholds for a trading strategy",
            "no historical bid/ask, tick regime, funding or execution records are inferred",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("continuous_report", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    report = coverage(json.loads(args.continuous_report.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("HISTORY_DEPTH", json.dumps({k: {
        "segments": v["segments"], "longest_segment_hours": v["longest_segment_hours"],
        "segments_at_least_7_days": v["segments_at_least_7_days"],
        "segments_at_least_30_days": v["segments_at_least_30_days"]
    } for k, v in report["timeframes"].items()}, sort_keys=True))


if __name__ == "__main__":
    main()
