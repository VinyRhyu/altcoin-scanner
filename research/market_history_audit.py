"""Reconstruct closed OKX candles from overlapping market_data snapshots.

Offline research only. No order execution, network access, or signal changes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path

DURATIONS_MS = {"15m": 900_000, "1h": 3_600_000, "2h": 7_200_000}


def _collected_ms(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone missing")
        return round(dt.astimezone(timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("invalid collected_utc") from exc


def _validated_candle(raw, duration, collected_ms):
    if not isinstance(raw, list) or len(raw) < 7:
        raise ValueError("missing OHLCV columns")
    if type(raw[0]) is not int or raw[0] < 0 or raw[0] % duration:
        raise ValueError("invalid candle open timestamp")
    values = tuple(float(v) for v in raw[1:7])
    if not all(map(math.isfinite, values)):
        raise ValueError("non-finite OHLCV")
    op, hi, lo, cl, vol, quote = values
    if not 0 < lo <= min(op, cl) <= max(op, cl) <= hi or vol < 0 or quote < 0:
        raise ValueError("invalid OHLC or volume")
    if raw[0] + duration > collected_ms:
        raise ValueError("candle not closed at snapshot time")
    return (raw[0], *values)


def resolve_paths(paths):
    files = []
    for item in paths:
        p = Path(item)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(p.glob("market_data*.json"))
        else:
            raise FileNotFoundError(str(p))
    files = sorted(set(p.resolve() for p in files))
    if not files:
        raise ValueError("no market_data*.json snapshots found")
    return files


def audit(paths):
    """Return (report, normalized candles). Never choose conflicting versions."""
    canonical = {}
    first_seen = {}
    conflicted = set()
    counts = defaultdict(int)
    examples = []
    collected_times = []

    def flag(kind, file, symbol=None, tf=None, open_ms=None, detail=None):
        counts[kind] += 1
        if len(examples) < 30:
            examples.append({"type": kind, "file": str(file), "symbol": symbol,
                             "timeframe": tf, "open_ms": open_ms, "detail": detail})

    for path in resolve_paths(paths):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if obj.get("status") != "COLLECTED" or obj.get("market_type") != "USDT_PERPETUAL":
                raise ValueError("unexpected market type/status")
            time_ms = _collected_ms(obj.get("collected_utc"))
            universe, candles = obj["universe"], obj["candles"]
            if not isinstance(universe, list) or not isinstance(candles, dict):
                raise ValueError("universe/candles missing")
        except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
            flag("invalid_snapshot", path, detail=str(exc))
            continue
        collected_times.append(time_ms)
        symbols = [x.get("symbol") for x in universe if isinstance(x, dict)]
        if len(symbols) != len(set(symbols)):
            flag("duplicate_symbols", path)
        for symbol in sorted(set(symbols), key=str):
            if not isinstance(symbol, str) or not symbol.endswith("-USDT-SWAP"):
                flag("invalid_symbol", path, symbol)
                continue
            hist = candles.get(symbol, {})
            for tf, duration in DURATIONS_MS.items():
                bars = hist.get(tf) if isinstance(hist, dict) else None
                if not isinstance(bars, list) or not bars:
                    flag("missing_series", path, symbol, tf)
                    continue
                prior = None
                for raw in bars:
                    opened = raw[0] if isinstance(raw, list) and raw else None
                    try:
                        bar = _validated_candle(raw, duration, time_ms)
                    except (ValueError, TypeError, OverflowError) as exc:
                        flag("invalid_candle", path, symbol, tf, opened, str(exc))
                        continue
                    if prior is not None and bar[0] <= prior:
                        flag("unordered_candle", path, symbol, tf, bar[0])
                    prior = bar[0]
                    key = (symbol, tf, bar[0])
                    if key in conflicted:
                        continue
                    if key not in canonical:
                        canonical[key], first_seen[key] = bar, time_ms
                    elif canonical[key] == bar:
                        counts["duplicate_observations"] += 1
                        first_seen[key] = min(first_seen[key], time_ms)
                    else:
                        del canonical[key]
                        del first_seen[key]
                        conflicted.add(key)
                        flag("conflicting_candle", path, symbol, tf, bar[0])

    timestamps = defaultdict(list)
    normalized = []
    for key, bar in sorted(canonical.items()):
        symbol, tf, opened = key
        timestamps[(symbol, tf)].append(opened)
        normalized.append({"symbol": symbol, "timeframe": tf, "open_ms": opened,
                           "open": bar[1], "high": bar[2], "low": bar[3],
                           "close": bar[4], "base_volume": bar[5],
                           "quote_volume": bar[6],
                           "first_seen_utc": datetime.fromtimestamp(
                               first_seen[key] / 1000, timezone.utc).isoformat()})
    per_series = []
    missing_total = 0
    for (symbol, tf), times in sorted(timestamps.items()):
        duration = DURATIONS_MS[tf]
        gaps = [(a, b, (b-a)//duration-1) for a, b in zip(times, times[1:])
                if b-a > duration]
        missing = sum(g[2] for g in gaps)
        missing_total += missing
        per_series.append({"symbol": symbol, "timeframe": tf,
                           "candles": len(times), "gaps": len(gaps),
                           "missing_candles": missing, "first_open_ms": times[0],
                           "last_open_ms": times[-1]})
        for a, b, count in gaps[:5]:
            flag("gap", "reconstruction", symbol, tf, a,
                 f"{count} candles missing before {b}")
    serious = ("invalid_snapshot", "duplicate_symbols", "invalid_symbol",
               "missing_series", "invalid_candle", "unordered_candle",
               "conflicting_candle")
    passed = bool(collected_times) and missing_total == 0 and all(
        counts[k] == 0 for k in serious)
    report = {
        "schema_version": 1, "snapshot_count": len(collected_times),
        "symbols": len(set(k[0] for k in canonical)),
        "distinct_candles": len(normalized),
        "duplicate_observations": counts["duplicate_observations"],
        "conflicting_candles": len(conflicted),
        "missing_candles_between_observations": missing_total,
        "integrity_passed": passed,
        "profitability_backtest_ready": False,
        "limitations": ["historical orderbook/spread/funding not in these snapshots",
                        "integrity is not sufficient sample size or proven edge",
                        "dynamic asset selection may cause survivorship/selection bias"],
        "issues": dict(sorted(counts.items())), "issue_examples": examples,
        "per_series": per_series,
    }
    return report, normalized


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="+", help="JSON snapshots or directory")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--normalized", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    report, rows = audit(args.snapshots)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.normalized:
        args.normalized.parent.mkdir(parents=True, exist_ok=True)
        args.normalized.write_text("".join(json.dumps(row) + "\n" for row in rows),
                                   encoding="utf-8")
    print(f"snapshots={report['snapshot_count']} unique_candles={len(rows)} "
          f"missing={report['missing_candles_between_observations']} "
          f"conflicts={report['conflicting_candles']} "
          f"integrity_passed={report['integrity_passed']}")
    return 2 if args.strict and not report["integrity_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
