"""Bounded, opt-in OKX historical candle export for offline research.

No API keys, orders, production publication, or synthetic candles.
Historical retrieval is *not* proof of point-in-time tradability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from research.market_history_audit import DURATIONS_MS

OKX_URL = "https://www.okx.com/api/v5/market/history-candles"
BAR_CODES = {"15m": "15m", "1h": "1H", "2h": "2H"}


def utc_ms(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            raise ValueError("timezone required")
        return int(d.astimezone(timezone.utc).timestamp() * 1000)
    except (TypeError, OverflowError, ValueError) as exc:
        raise ValueError("UTC ISO date required, e.g. 2026-11-01T00:00:00Z") from exc


def parse_row(raw, duration):
    """Validate OKX [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm] swap row."""
    if not isinstance(raw, list) or len(raw) < 9:
        raise ValueError("unexpected OKX candle schema")
    if str(raw[8]) != "1":
        return None  # exchange has not confirmed this bar
    try:
        ts = int(raw[0])
        op, hi, lo, close, base_volume, quote_volume = [
            float(raw[n]) for n in (1, 2, 3, 4, 5, 7)]
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("non-numeric OKX candle") from exc
    if (ts < 0 or ts % duration or not all(map(math.isfinite,
        (op, hi, lo, close, base_volume, quote_volume))) or
        not (0 < lo <= min(op, close) <= max(op, close) <= hi) or
        min(base_volume, quote_volume) < 0):
        raise ValueError("invalid OKX OHLCV")
    return {"open_ms": ts, "open": op, "high": hi, "low": lo,
            "close": close, "base_volume": base_volume,
            "quote_volume": quote_volume}


def fetch_page(symbol, tf, after_ms):
    params = urllib.parse.urlencode({
        "instId": symbol, "bar": BAR_CODES[tf], "after": str(after_ms), "limit": "100",
    })
    request = urllib.request.Request(OKX_URL + "?" + params, headers={
        "User-Agent": "CryptoFuturesScannerResearch/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=18) as response:
        payload = json.load(response)
    if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
        raise ValueError("invalid OKX history-candles response")
    return payload["data"]


def retrieve(symbol, tf, start_ms, end_ms, max_pages=3,
             fetch=fetch_page, clock=None):
    if (not isinstance(symbol, str) or not symbol.endswith("-USDT-SWAP") or
        any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in symbol)):
        raise ValueError("invalid USDT perpetual instrument")
    if tf not in DURATIONS_MS or not isinstance(max_pages, int) or not 1 <= max_pages <= 100:
        raise ValueError("timeframe or page budget invalid")
    duration = DURATIONS_MS[tf]
    if (type(start_ms) is not int or type(end_ms) is not int or
        start_ms < 0 or end_ms <= start_ms or
        start_ms % duration or end_ms % duration):
        raise ValueError("window must be positive and aligned to candle timeframes")
    clock = clock or (lambda: int(datetime.now(timezone.utc).timestamp()*1000))
    requested_at_ms = clock()
    if end_ms > requested_at_ms:
        raise ValueError("historical window cannot be in the future")
    cursor = end_ms  # OKX after=ts retrieves candles older than ts
    seen = {}
    pages = 0
    stopped = "max_pages_reached"

    for page in range(max_pages):
        data = fetch(symbol, tf, cursor)
        pages += 1
        if not data:
            stopped = "exchange_end_of_history"
            break
        times = []
        for raw in data:
            # Validated raw opening timestamp even for unconfirmed rows
            if not isinstance(raw, list) or not raw:
                raise ValueError("invalid OKX page row")
            ts = int(raw[0])
            if ts >= cursor:
                raise ValueError("unexpected OKX pagination order")
            times.append(ts)
            candle = parse_row(raw, duration)
            if candle is None or not start_ms <= ts < end_ms:
                continue
            if ts + duration > requested_at_ms:
                continue
            old = seen.setdefault(ts, candle)
            if old != candle:
                raise ValueError("conflicting OKX historical candles")
        oldest = min(times)
        if oldest >= cursor:
            raise ValueError("non-progressing OKX pagination")
        cursor = oldest
        if oldest < start_ms:
            stopped = "start_reached"
            break
    ordered = [seen[ts] for ts in sorted(seen)]
    for row in ordered:
        row.update({"symbol": symbol, "timeframe": tf,
                    "source": "okx_history_candles",
                    "point_in_time_eligible": False,
                    "retrieved_at_utc": datetime.fromtimestamp(
                        requested_at_ms/1000, timezone.utc).isoformat()})
    return {"symbol": symbol, "timeframe": tf,
            "requested_start_ms": start_ms, "requested_end_ms_exclusive": end_ms,
            "retrieved_at_utc": datetime.fromtimestamp(
                requested_at_ms/1000, timezone.utc).isoformat(),
            "pages_requested": pages, "candles_retrieved": len(ordered),
            "stopped_because": stopped, "backtest_ready": False,
            "warning": "Historical data fetched later: no point-in-time ticker/spread/funding proof.",
            }, ordered


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", required=True)
    p.add_argument("--tf", choices=tuple(DURATIONS_MS), required=True)
    p.add_argument("--start", required=True, help="ISO UTC, inclusive, e.g. 2026-10-01T00:00:00Z")
    p.add_argument("--end", required=True, help="ISO UTC, exclusive, e.g. 2026-10-08T00:00:00Z")
    p.add_argument("--max-pages", type=int, default=3)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args(argv)
    meta, rows = retrieve(args.symbol, args.tf, utc_ms(args.start), utc_ms(args.end),
                          max_pages=args.max_pages)
    for path, payload in (
        (args.output, "".join(json.dumps(row)+"\n" for row in rows)),
        (args.report, json.dumps(meta, indent=2)+"\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print("OKX_HISTORY_ONLY", json.dumps(meta, sort_keys=True))


if __name__ == "__main__":
    main()
