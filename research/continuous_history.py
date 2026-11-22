"""Separate continuous historical series and examine observable gap provenance.

Read-only, offline research. Does not fill prices, trade, or certify profitability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from research.market_history_audit import DURATIONS_MS, _collected_ms, audit, resolve_paths


def snapshot_observations(paths):
    """Read actual collection-time universe membership; never infer historical listings."""
    events = []
    for path in resolve_paths(paths):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if obj.get("status") != "COLLECTED" or obj.get("market_type") != "USDT_PERPETUAL":
                continue
            ts = _collected_ms(obj["collected_utc"])
            universe = obj["universe"]
            if not isinstance(universe, list) or not isinstance(obj["candles"], dict):
                continue
            symbols = frozenset(x["symbol"] for x in universe
                                if isinstance(x, dict) and isinstance(x.get("symbol"), str))
            events.append((ts, symbols))
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            continue
    return sorted(events, key=lambda event: event[0])


def observed_gap_context(symbol, start_missing_ms, next_open_ms, duration, events):
    """Classify only archived observations made after the first missing bar closed.

    These are descriptions of evidence, NOT established causes of a gap.
    """
    present = absent = 0
    for collected_ms, universe in events:
        if start_missing_ms + duration <= collected_ms < next_open_ms:
            if symbol in universe:
                present += 1
            else:
                absent += 1
    if present and absent:
        label = "mixed_universe_presence"
    elif present:
        label = "symbol_present_in_snapshots"
    elif absent:
        label = "symbol_absent_from_snapshots"
    else:
        label = "no_snapshot_during_missing_interval"
    return {"classification": label,
            "snapshots_with_symbol": present,
            "snapshots_without_symbol": absent}


def analyze(paths, min_bars=100):
    """Return complete report, all segment metadata, and only qualifying OHLCV rows.

    A segment is contiguous at its own candle timeframe. Qualifying by length
    permits *price-continuity research* only; it is not evidence of an edge or a
    point-in-time tradable universe. Bars are never interpolated.
    """
    if type(min_bars) is not int or min_bars < 1:
        raise ValueError("min_bars must be a positive integer")

    paths = resolve_paths(paths)
    base_report, normalized = audit(paths)
    events = snapshot_observations(paths)
    grouped = defaultdict(list)
    for bar in normalized:
        grouped[(bar["symbol"], bar["timeframe"])].append(bar)

    segments, gaps, eligible_rows = [], [], []
    breakdown = Counter()
    by_timeframe = defaultdict(lambda: {"series": 0, "segments": 0, "eligible_segments": 0,
                                        "candles": 0, "eligible_candles": 0,
                                        "missing_candles": 0, "gaps": 0})

    def add_segment(symbol, tf, part):
        if not part:
            return
        duration = DURATIONS_MS[tf]
        eligible = len(part) >= min_bars
        segment_id = f"{symbol}:{tf}:{part[0]['open_ms']}"
        meta = {"segment_id": segment_id, "symbol": symbol, "timeframe": tf,
                "start_open_ms": part[0]["open_ms"],
                "end_open_ms": part[-1]["open_ms"],
                "candles": len(part), "duration_hours": len(part) * duration / 3_600_000,
                "first_bar_seen_utc": part[0]["first_seen_utc"],
                "last_bar_seen_utc": part[-1]["first_seen_utc"],
                "meets_min_bars": eligible}
        segments.append(meta)
        stats = by_timeframe[tf]
        stats["segments"] += 1
        if eligible:
            stats["eligible_segments"] += 1
            stats["eligible_candles"] += len(part)
            for index, bar in enumerate(part):
                eligible_rows.append({"segment_id": segment_id, "segment_index": index, **bar})

    for (symbol, tf), bars in sorted(grouped.items()):
        duration = DURATIONS_MS[tf]
        stats = by_timeframe[tf]
        stats["series"] += 1
        bars = sorted(bars, key=lambda bar: bar["open_ms"])
        stats["candles"] += len(bars)
        part = [bars[0]]
        previous_ms = bars[0]["open_ms"]
        for bar in bars[1:]:
            current_ms = bar["open_ms"]
            if current_ms <= previous_ms or (current_ms - previous_ms) % duration:
                raise ValueError(f"invalid reconstructed sequence: {(symbol, tf, current_ms)}")
            if current_ms - previous_ms > duration:
                add_segment(symbol, tf, part)
                start_missing = previous_ms + duration
                missing = (current_ms - previous_ms) // duration - 1
                context = observed_gap_context(symbol, start_missing, current_ms,
                                               duration, events)
                gap = {"symbol": symbol, "timeframe": tf,
                       "previous_open_ms": previous_ms, "next_open_ms": current_ms,
                       "first_missing_open_ms": start_missing,
                       "last_missing_open_ms": current_ms - duration,
                       "missing_candles": missing, **context}
                gaps.append(gap)
                breakdown[context["classification"]] += 1
                stats["gaps"] += 1
                stats["missing_candles"] += missing
                part = []
            part.append(bar)
            previous_ms = current_ms
        add_segment(symbol, tf, part)

    series_by_timeframe = dict(sorted(by_timeframe.items()))
    eligible_count = sum(x["meets_min_bars"] for x in segments)
    report = {
        "schema_version": 1, "min_bars": min_bars,
        "source_snapshot_count": base_report["snapshot_count"],
        "archived_universe_observations": len(events),
        "source_distinct_candles": base_report["distinct_candles"],
        "source_conflicting_candles": base_report["conflicting_candles"],
        "source_integrity_passed": base_report["integrity_passed"],
        "series_count": len(grouped), "segments_total": len(segments),
        "eligible_segments": eligible_count, "eligible_candles": len(eligible_rows),
        "gaps_total": len(gaps),
        "missing_candles_total": sum(x["missing_candles"] for x in gaps),
        "gap_classifications": dict(sorted(breakdown.items())),
        "by_timeframe": series_by_timeframe,
        "segments": segments, "gaps": gaps,
        "backtest_ready": False,
        "caveats": [
            "gap classifications describe archived snapshot evidence, not causal explanations",
            "no archived snapshot in a gap does not prove exchange data was absent",
            "segment length alone is not adequate for a profitability backtest",
            "snapshot universe is dynamic and can introduce point-in-time selection bias",
            "historical funding, fill prices, spread and order book are not reconstructed",
            "OHLCV is never interpolated; missing candles split segments",
        ],
    }
    assert sum(x["candles"] for x in segments) == len(normalized)
    assert sum(x["missing_candles"] for x in gaps) == base_report["missing_candles_between_observations"]
    assert len(eligible_rows) == sum(x["candles"] for x in segments if x["meets_min_bars"])
    return report, eligible_rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="+", help="Archived market_data*.json or folders")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candles-jsonl", type=Path)
    parser.add_argument("--segments-jsonl", type=Path)
    parser.add_argument("--min-bars", type=int, default=100)
    args = parser.parse_args(argv)
    report, rows = analyze(args.snapshots, args.min_bars)
    outputs = [(args.report, json.dumps(report, indent=2) + "\n")]
    if args.candles_jsonl:
        outputs.append((args.candles_jsonl, "".join(json.dumps(x) + "\n" for x in rows)))
    if args.segments_jsonl:
        outputs.append((args.segments_jsonl, "".join(
            json.dumps(x) + "\n" for x in report["segments"])))
    for path, content in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print("CONTIGUOUS_HISTORY", json.dumps({
        k: report[k] for k in ("source_snapshot_count", "series_count",
             "segments_total", "eligible_segments", "eligible_candles",
             "gaps_total", "missing_candles_total", "gap_classifications",
             "by_timeframe")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
