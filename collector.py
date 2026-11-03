
"""Coletor OKX: candles nativos 2h, 1h e 15m.

Apenas dados de mercado. Sem sinais, ordens ou notificações.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OKX = "https://www.okx.com/api/v5"
COINGECKO = "https://api.coingecko.com/api/v3"

INTERVALS = {
    "2h": 7200,
    "1h": 3600,
    "15m": 900,
}

MIN_VOLUME_USDT = float(
    os.getenv("MIN_VOLUME_USDT", "2000000")
)
PRIORITY_VOLUME_USDT = float(
    os.getenv("PRIORITY_VOLUME_USDT", "5000000")
)
MIN_VALIDATED = int(os.getenv("MIN_VALIDATED", "15"))
UNIVERSE_SIZE = int(os.getenv("UNIVERSE_SIZE", "50"))
MAX_PRESCREEN = int(os.getenv("MAX_PRESCREEN", "90"))
MARKET_PROVIDER = os.getenv("MARKET_PROVIDER", "okx_swap")
CG_KEY = os.getenv("COINGECKO_DEMO_API_KEY", "")

CRYPTO_BASES = set("""
SOL XRP BNB DOGE ADA AVAX LINK SUI TRX TON LTC BCH DOT UNI AAVE
NEAR ICP ATOM FIL ETC XLM HBAR ALGO XMR ZEC TAO INJ RENDER RNDR
OP ARB WLD ONDO ENA PENDLE SEI TIA FET GRT SAND MANA APT STX
IMX RUNE KAS JUP JTO PYTH WIF PEPE BONK SHIB FLOKI CRV COMP LDO
MKR SKY DYDX ETHFI STRK ZRO ZK POL GALA CHZ FLOW THETA VET IOTA
KAVA XTZ NEO MINA ROSE HYPE MORPHO AERO VIRTUAL JASMY ZIL KSM
DASH SNX ENS FTM S AXS CAKE GNO ZRX NMR LPT RSR INIT BIO PYR
SUSHI BERA PENGU POPCAT TURBO
""".split())

BLOCKED_BASES = set("""
BTC ETH USDC USDT DAI USDE FDUSD TUSD USDD PYUSD USD1 WBTC WETH
STETH WSTETH WEETH WBETH CBBTC RLUSD XAUT PAXG XAG XAU XPT SOXL
""".split())


def utc(ms):
    return datetime.fromtimestamp(
        ms / 1000, timezone.utc
    ).isoformat()


def finite_positive(value):
    number = float(value)

    if not math.isfinite(number) or number <= 0:
        raise ValueError("Invalid numeric field")

    return number


def request_json(
    url, params=None, headers=None, retries=3
):
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AltcoinScanner/1.0",
            "Accept": "application/json",
            **(headers or {}),
        },
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                req, timeout=18
            ) as response:
                return json.load(response)

        except urllib.error.HTTPError as exc:
            if exc.code in (403, 451):
                raise RuntimeError(
                    f"Provider access restricted: {exc.code}"
                ) from exc

            if (
                exc.code not in (429, 500, 502, 503, 504)
                or attempt == retries - 1
            ):
                raise RuntimeError(
                    f"HTTP {exc.code}"
                ) from exc

        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
        ) as exc:
            if attempt == retries - 1:
                raise RuntimeError(
                    f"Network/JSON error: {exc}"
                ) from exc

        time.sleep(1.0 + attempt)

    raise RuntimeError("Request failed")


def okx(path, **params):
    result = request_json(
        f"{OKX}/{path}", params
    )

    if (
        result.get("code") != "0"
        or not isinstance(result.get("data"), list)
    ):
        raise RuntimeError(
            f"Invalid OKX response: {str(result)[:100]}"
        )

    return result["data"]


def eligible_bases():
    if not CG_KEY:
        return (
            CRYPTO_BASES,
            "curated_cryptocurrency_allowlist_NOT_live_top100",
        )

    try:
        rows = request_json(
            f"{COINGECKO}/coins/markets",
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 100,
                "page": 1,
                "sparkline": "false",
            },
            headers={"x-cg-demo-api-key": CG_KEY},
        )

        if not isinstance(rows, list) or len(rows) < 80:
            raise ValueError("Incomplete CoinGecko response")

        counts = {}

        for row in rows:
            symbol = str(
                row.get("symbol", "")
            ).upper()
            counts[symbol] = counts.get(symbol, 0) + 1

        allowed = {
            symbol
            for symbol, count in counts.items()
            if count == 1
            and symbol in CRYPTO_BASES
            and symbol not in BLOCKED_BASES
        }

        if len(allowed) < MIN_VALIDATED:
            raise ValueError("Insufficient assets")

        return (
            allowed,
            "coingecko_live_top100_intersect_curated",
        )

    except (RuntimeError, ValueError, TypeError, KeyError):
        return (
            CRYPTO_BASES,
            "coingecko_unavailable_curated_allowlist",
        )


def tickers(allowed, now_ms):
    instruments = okx(
        "public/instruments", instType="SWAP"
    )

    live = {
        item["instId"]
        for item in instruments
        if item.get("state") == "live"
        and item.get("instId", "").endswith("-USDT-SWAP")
    }

    raw = okx(
        "market/tickers", instType="SWAP"
    )

    result = []

    for item in raw:
        symbol = item.get("instId", "")

        if not symbol.endswith("-USDT-SWAP"):
            continue

        base = symbol[:-10]

        if (
            symbol not in live
            or base not in allowed
            or base in BLOCKED_BASES
            or base.startswith("1000")
        ):
            continue

        try:
            price = finite_positive(item["last"])
            open24 = finite_positive(item["open24h"])
            turnover = (
                finite_positive(item["volCcy24h"])
                * price
            )

            change = 100 * (price / open24 - 1)
            timestamp = int(item["ts"])

            bid = finite_positive(item["bidPx"])
            ask = finite_positive(item["askPx"])

            spread = (ask - bid) / price * 100

            if (
                ask < bid
                or spread > 0.25
                or not 0 <= now_ms - timestamp <= 300_000
            ):
                continue

            high = finite_positive(item["high24h"])
            low = finite_positive(item["low24h"])

            if (
                high < max(price, low)
                or low > price
                or not math.isfinite(change)
                or turnover < MIN_VOLUME_USDT
            ):
                continue

            result.append({
                "symbol": symbol,
                "base": base,
                "price": price,
                "change_24h_pct": round(change, 3),
                "turnover_24h_usdt_est": round(
                    turnover, 2
                ),
                "range_24h_pct": round(
                    (high - low) / price * 100, 3
                ),
                "spread_pct": round(spread, 4),
                "ticker_time_utc": utc(timestamp),
            })

        except (
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            ZeroDivisionError,
        ):
            continue

    return result


def parse_candles(rows, tf, now_ms):
    duration = INTERVALS[tf] * 1000
    result = []

    for row in rows:
        if len(row) < 9 or str(row[8]) != "1":
            continue

        timestamp = int(row[0])
        o, h, l, c, volume = map(
            float, row[1:6]
        )
        turnover = float(row[7])

        if timestamp + duration > now_ms:
            continue

        if (
            timestamp % duration
            or not all(
                math.isfinite(x)
                for x in (
                    o, h, l, c, volume, turnover
                )
            )
            or not (
                0 < l <= min(o, c)
                <= max(o, c) <= h
                and volume >= 0
                and turnover >= 0
            )
        ):
            raise ValueError("Malformed candle")

        result.append([
            timestamp, o, h, l,
            c, volume, turnover
        ])

    result.sort(key=lambda x: x[0])

    if len(result) < 60:
        raise ValueError("Insufficient candles")

    if any(
        b[0] - a[0] != duration
        for a, b in zip(result, result[1:])
    ):
        raise ValueError("Missing/duplicate candles")

    if (
        now_ms - (result[-1][0] + duration)
        > duration
    ):
        raise ValueError("Stale closed candle")

    return result


def candles(symbol, tf, now_ms):
    bar = {
        "2h": "2H",
        "1h": "1H",
        "15m": "15m",
    }[tf]

    rows = okx(
        "market/candles",
        instId=symbol,
        bar=bar,
        limit="100",
    )

    return parse_candles(
        rows, tf, now_ms
    )


def intraday_anomaly(series):
    last = series[-1]
    previous = series[-21:-1]

    median_volume = statistics.median(
        x[6] for x in previous
    )
    median_range = statistics.median(
        (x[2] - x[3]) / x[4]
        for x in previous
    )

    volume_ratio = (
        last[6] / median_volume
        if median_volume else 0
    )
    range_ratio = (
        ((last[2] - last[3]) / last[4])
        / median_range
        if median_range else 0
    )

    return (
        round(volume_ratio, 3),
        round(range_ratio, 3),
    )


def validated_liquidity(series, ticker_estimate):
    if len(series) < 96:
        raise ValueError(
            "Insufficient 24h candle history"
        )

    turnover = sum(
        float(row[6]) for row in series[-96:]
    )

    ticker = float(ticker_estimate)

    if not all(
        math.isfinite(x) and x > 0
        for x in (turnover, ticker)
    ):
        raise ValueError("Invalid turnover")

    if turnover < MIN_VOLUME_USDT:
        raise ValueError("Volume below discovery minimum")

    if not 0.5 <= ticker / turnover <= 2.0:
        raise ValueError("Ticker/candle volume mismatch")

    tier = (
        "priority"
        if turnover >= PRIORITY_VOLUME_USDT
        else "exploratory"
    )

    return round(turnover, 2), tier


def prescreen(candidates):
    buckets = [
        sorted(
            candidates,
            key=lambda x: -x["change_24h_pct"],
        ),
        sorted(
            candidates,
            key=lambda x: x["change_24h_pct"],
        ),
        sorted(
            candidates,
            key=lambda x: -x["range_24h_pct"],
        ),
        sorted(
            candidates,
            key=lambda x: -x["turnover_24h_usdt_est"],
        ),
    ]

    selected = []
    seen = set()

    for i in range(len(candidates)):
        for bucket in buckets:
            if i >= len(bucket):
                continue

            item = bucket[i]

            if item["symbol"] not in seen:
                selected.append(item)
                seen.add(item["symbol"])

            if len(selected) >= MAX_PRESCREEN:
                return selected

    return selected


def choose_universe(pool):
    gainers = sorted(
        (
            x for x in pool
            if x["change_24h_pct"] > 0
        ),
        key=lambda x: -x["change_24h_pct"],
    )

    losers = sorted(
        (
            x for x in pool
            if x["change_24h_pct"] < 0
        ),
        key=lambda x: x["change_24h_pct"],
    )

    anomalies = sorted(
        (
            x for x in pool
            if max(
                x["rel_volume_15m"],
                x["rel_range_15m"],
            ) >= 1.6
        ),
        key=lambda x: -max(
            x["rel_volume_15m"],
            x["rel_range_15m"],
        ),
    )

    quotas = [
        ("gainers24h", gainers, 6),
        ("losers24h", losers, 8),
        ("intraday_anomaly", anomalies, 6),
    ]

    selected = []
    seen = set()

    for group, bucket, limit in quotas:
        count = 0

        for item in bucket:
            if count >= limit:
                break

            if item["symbol"] not in seen:
                selected.append({
                    **item,
                    "group": group,
                })
                seen.add(item["symbol"])
                count += 1

    for item in sorted(
        pool,
        key=lambda x: -x["turnover_24h_usdt_est"],
    ):
        if len(selected) >= UNIVERSE_SIZE:
            break

        if item["symbol"] not in seen:
            selected.append({
                **item,
                "group": "liquidity_backfill",
            })
            seen.add(item["symbol"])

    return selected[:UNIVERSE_SIZE]


def collect(now_ms):
    allowed, source = eligible_bases()

    candidates = tickers(allowed, now_ms)

    if len(candidates) < MIN_VALIDATED:
        raise RuntimeError(
            f"Only {len(candidates)} eligible tickers"
        )

    pool = []
    rejected = {}
    cached_15m = {}

    for original in prescreen(candidates):
        item = dict(original)
        symbol = item["symbol"]

        try:
            series = candles(
                symbol, "15m", now_ms
            )
            ticker_volume = item[
                "turnover_24h_usdt_est"
            ]

            turnover, tier = validated_liquidity(
                series, ticker_volume
            )

            item["turnover_24h_usdt_est_ticker"] = (
                ticker_volume
            )
            item["turnover_24h_usdt_est"] = turnover
            item["liquidity_tier"] = tier

            vol_ratio, range_ratio = (
                intraday_anomaly(series)
            )

            item["rel_volume_15m"] = vol_ratio
            item["rel_range_15m"] = range_ratio
            item["has_intraday_anomaly"] = (
                max(vol_ratio, range_ratio) >= 1.6
            )

            cached_15m[symbol] = series
            pool.append(item)

        except (ValueError, RuntimeError, TypeError) as exc:
            rejected[symbol] = str(exc)

        time.sleep(0.10)

    selected = choose_universe(pool)
    selected_symbols = {
        x["symbol"] for x in selected
    }

    fallback = [
        {**x, "group": "liquidity_backfill"}
        for x in sorted(
            pool,
            key=lambda x: -x["turnover_24h_usdt_est"],
        )
        if x["symbol"] not in selected_symbols
    ]

    validated = {}
    universe = []

    for item in selected + fallback:
        if len(validated) >= UNIVERSE_SIZE:
            break

        symbol = item["symbol"]

        try:
            series = {
                "15m": cached_15m[symbol],
                "2h": candles(symbol, "2h", now_ms),
                "1h": candles(symbol, "1h", now_ms),
            }

            validated[symbol] = series
            universe.append(item)

        except (ValueError, RuntimeError, TypeError) as exc:
            rejected[symbol] = str(exc)

        time.sleep(0.10)

    if len(validated) < MIN_VALIDATED:
        raise RuntimeError(
            f"Only {len(validated)} complete OHLCV assets"
        )

    groups = (
        "gainers24h",
        "losers24h",
        "intraday_anomaly",
        "liquidity_backfill",
    )

    return {
        "status": "COLLECTED",
        "signals_allowed": False,
        "venue": "okx_swap",
        "exchange": "OKX",
        "market_type": "USDT_PERPETUAL",
        "top100_filter": source,
        "collected_utc": utc(now_ms),
        "universe": universe,
        "candles": validated,
        "rejected": rejected,
        "diagnostics": {
            "available_liquid": len(candidates),
            "prescreen": len(pool),
            "validated": len(validated),
            "liquidity_priority": sum(
                x["liquidity_tier"] == "priority"
                for x in universe
            ),
            "liquidity_exploratory": sum(
                x["liquidity_tier"] == "exploratory"
                for x in universe
            ),
            "discovery_groups": {
                group: sum(
                    x["group"] == group
                    for x in universe
                )
                for group in groups
            },
        },
    }


def main():
    if MARKET_PROVIDER not in ("okx_swap", "auto"):
        raise ValueError(
            "Only OKX perpetual is supported"
        )

    Path("output").mkdir(exist_ok=True)

    now_ms = int(time.time() * 1000)

    try:
        payload = collect(now_ms)

        Path("output/market_data.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

        print(
            f"SUCCESS: {len(payload['universe'])} "
            "OKX perpetual assets. Signals disabled.",
            flush=True,
        )

    except Exception as exc:
        Path("output/collector_status.json").write_text(
            json.dumps({
                "status": "FAILED",
                "signals_allowed": False,
                "collected_utc": utc(now_ms),
                "reason": str(exc),
            }, indent=2),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
