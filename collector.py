
"""Altcoin Scanner V2 - coleta publica de OHLCV."""
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
BINANCE = "https://data-api.binance.vision/api/v3"
COINGECKO = "https://api.coingecko.com/api/v3"

INTERVALS = {"2h": 7200, "1h": 3600, "15m": 900}
MIN_VOLUME_USDT = float(os.getenv("MIN_VOLUME_USDT", "10000000"))
MIN_VALIDATED = int(os.getenv("MIN_VALIDATED", "15"))
UNIVERSE_SIZE = int(os.getenv("UNIVERSE_SIZE", "20"))
MAX_PRESCREEN = int(os.getenv("MAX_PRESCREEN", "65"))
MARKET_PROVIDER = os.getenv("MARKET_PROVIDER", "auto")
CG_KEY = os.getenv("COINGECKO_DEMO_API_KEY", "")

CRYPTO_BASES = set("""
SOL XRP BNB DOGE ADA AVAX LINK SUI TRX TON LTC BCH DOT UNI AAVE
NEAR ICP ATOM FIL ETC XLM HBAR ALGO XMR ZEC TAO INJ RENDER RNDR
OP ARB WLD ONDO ENA PENDLE SEI TIA FET GRT SAND MANA APT STX
IMX RUNE KAS JUP JTO PYTH WIF PEPE BONK SHIB FLOKI CRV COMP
LDO MKR SKY DYDX ETHFI STRK ZRO ZK POL GALA CHZ FLOW THETA VET
IOTA KAVA XTZ NEO MINA ROSE HYPE MORPHO AERO VIRTUAL JASMY ZIL
KSM DASH SNX ENS FTM S AXS CAKE GNO ZRX NMR LPT RSR INIT BIO
PYR SUSHI BERA PENGU POPCAT TURBO
""".split())

BLOCKED_BASES = set("""
BTC ETH USDC USDT DAI USDE FDUSD TUSD USDD PYUSD USD1 WBTC
WETH STETH WSTETH WEETH WBETH CBBTC RLUSD XAUT PAXG XAG
XAU XPT SOXL
""".split())


def utc(ms):
    return datetime.fromtimestamp(
        ms / 1000, timezone.utc
    ).isoformat()


def finite_positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Nonpositive/nonfinite field")
    return number


def request_json(url, params=None, headers=None, retries=3):
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
            with urllib.request.urlopen(req, timeout=18) as response:
                return json.load(response)

        except urllib.error.HTTPError as exc:
            if exc.code in (403, 451):
                raise RuntimeError(
                    f"HTTP {exc.code}: provider restricted access"
                ) from exc

            if (
                exc.code not in (429, 500, 502, 503, 504)
                or attempt == retries - 1
            ):
                raise RuntimeError(f"HTTP {exc.code}") from exc

        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
        ) as exc:
            if attempt == retries - 1:
                raise RuntimeError(
                    f"Network or JSON failure: {exc}"
                ) from exc

        time.sleep(1.0 + attempt)

    raise RuntimeError("Request failed")


def okx(path, **params):
    result = request_json(f"{OKX}/{path}", params)
    if (
        result.get("code") != "0"
        or not isinstance(result.get("data"), list)
    ):
        raise RuntimeError(
            f"OKX response rejected: {str(result)[:100]}"
        )
    return result["data"]


def binance(path, **params):
    result = request_json(f"{BINANCE}/{path}", params)
    if isinstance(result, dict) and "code" in result:
        raise RuntimeError(
            f"Binance response rejected: {str(result)[:100]}"
        )
    return result


def eligible_bases():
    """CoinGecko opcional; sem chave usa lista curada."""
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
            raise ValueError("Incomplete CoinGecko top-100 response")

        counts = {}
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            counts[symbol] = counts.get(symbol, 0) + 1

        allowed = {
            symbol
            for symbol in counts
            if counts[symbol] == 1
            and symbol in CRYPTO_BASES
            and symbol not in BLOCKED_BASES
        }

        if len(allowed) < MIN_VALIDATED:
            raise ValueError("Insufficient eligible cryptocurrencies")

        return (
            allowed,
            "coingecko_live_top100_intersect_curated_crypto_allowlist",
        )

    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        print(
            f"CoinGecko unavailable ({exc}); using curated list.",
            flush=True,
        )
        return (
            CRYPTO_BASES,
            "coingecko_unavailable_curated_allowlist_NOT_live_top100",
        )


def tickers(venue, allowed, now_ms):
    if venue == "okx_swap":
        live = {
            x["instId"]
            for x in okx("public/instruments", instType="SWAP")
            if x.get("state") == "live"
            and x.get("instId", "").endswith("-USDT-SWAP")
        }
        raw = okx("market/tickers", instType="SWAP")
    else:
        live = {
            x["symbol"]
            for x in binance("exchangeInfo")["symbols"]
            if x.get("status") == "TRADING"
            and x.get("quoteAsset") == "USDT"
        }
        raw = binance("ticker/24hr")

    result = []

    for item in raw:
        key = "instId" if venue == "okx_swap" else "symbol"
        symbol = item.get(key, "")

        if venue == "okx_swap":
            base = (
                symbol[:-10]
                if symbol.endswith("-USDT-SWAP")
                else ""
            )
        else:
            base = (
                symbol[:-4]
                if symbol.endswith("USDT")
                else ""
            )

        if (
            symbol not in live
            or base not in allowed
            or base in BLOCKED_BASES
            or base.startswith("1000")
        ):
            continue

        try:
            if venue == "okx_swap":
                price = finite_positive(item["last"])
                open24 = finite_positive(item["open24h"])
                turnover = (
                    finite_positive(item["volCcy24h"]) * price
                )
                change = 100 * (price / open24 - 1)
                stamp = int(item["ts"])
                bid = finite_positive(item["bidPx"])
                ask = finite_positive(item["askPx"])
                spread = (ask - bid) / price * 100

                if (
                    ask < bid
                    or spread > 0.25
                    or not 0 <= now_ms - stamp <= 300_000
                ):
                    continue

            else:
                price = finite_positive(item["lastPrice"])
                turnover = finite_positive(item["quoteVolume"])
                change = float(item["priceChangePercent"])
                stamp = now_ms
                spread = None

            high = finite_positive(
                item["high24h"]
                if venue == "okx_swap"
                else item["highPrice"]
            )
            low = finite_positive(
                item["low24h"]
                if venue == "okx_swap"
                else item["lowPrice"]
            )

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
                "turnover_24h_usdt_est": round(turnover, 2),
                "range_24h_pct": round(
                    (high - low) / price * 100, 3
                ),
                "spread_pct": (
                    round(spread, 4)
                    if spread is not None
                    else None
                ),
                "ticker_time_utc": utc(stamp),
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


def parse_candles(rows, venue, tf, now_ms):
    duration = INTERVALS[tf] * 1000
    output = []

    for row in rows:
        if venue == "okx_swap":
            if len(row) < 9 or str(row[8]) != "1":
                continue

        if len(row) < 8:
            raise ValueError("Bad kline response")

        stamp = int(row[0])
        o, h, l, c, v = map(float, row[1:6])
        turnover = float(row[7])

        if stamp + duration > now_ms:
            continue

        values = (o, h, l, c, v, turnover)

        if (
            stamp % duration
            or not all(math.isfinite(x) for x in values)
            or not (
                0 < l <= min(o, c)
                <= max(o, c) <= h
                and v >= 0
                and turnover >= 0
            )
        ):
            raise ValueError("Malformed candle")

        output.append([stamp, o, h, l, c, v, turnover])

    output.sort(key=lambda x: x[0])

    if len(output) < 60 or any(
        b[0] - a[0] != duration
        for a, b in zip(output, output[1:])
    ):
        raise ValueError(
            "Missing/duplicate candles or insufficient history"
        )

    if now_ms - (output[-1][0] + duration) > duration:
        raise ValueError("Stale latest closed candle")

    return output


def candles(venue, symbol, tf, now_ms):
    if venue == "okx_swap":
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
    else:
        rows = binance(
            "klines",
            symbol=symbol,
            interval=tf,
            limit=100,
        )

    return parse_candles(rows, venue, tf, now_ms)


def intraday_anomaly(candles15):
    """Compara ultimo 15m fechado com 20 anteriores."""
    last = candles15[-1]
    previous = candles15[-21:-1]

    median_volume = statistics.median(
        row[6] for row in previous
    )
    median_range = statistics.median(
        (row[2] - row[3]) / row[4]
        for row in previous
    )

    volume_ratio = (
        last[6] / median_volume
        if median_volume
        else 0
    )
    range_ratio = (
        ((last[2] - last[3]) / last[4]) / median_range
        if median_range
        else 0
    )

    return round(volume_ratio, 3), round(range_ratio, 3)


def prescreen(candidates, max_size):
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

    chosen, seen = [], set()

    for i in range(len(candidates)):
        for bucket in buckets:
            if i >= len(bucket):
                continue

            item = bucket[i]

            if item["symbol"] not in seen:
                seen.add(item["symbol"])
                chosen.append(item)

            if len(chosen) == max_size:
                return chosen

    return chosen


def choose_universe(pool, n):
    gainers = sorted(
        [
            x for x in pool
            if x["change_24h_pct"] > 0
        ],
        key=lambda x: -x["change_24h_pct"],
    )

    losers = sorted(
        [
            x for x in pool
            if x["change_24h_pct"] < 0
        ],
        key=lambda x: x["change_24h_pct"],
    )

    anomalies = sorted(
        [
            x for x in pool
            if max(
                x["rel_volume_15m"],
                x["rel_range_15m"],
            ) >= 1.6
        ],
        key=lambda x: -max(
            x["rel_volume_15m"],
            x["rel_range_15m"],
        ),
    )

    chosen, seen = [], set()

    quotas = [
        ("gainers24h", gainers, 7),
        ("losers24h", losers, 7),
        ("intraday_anomaly", anomalies, 6),
    ]

    for group_name, bucket, quota in quotas:
        count = 0

        for item in bucket:
            if count >= quota:
                break

            if item["symbol"] not in seen:
                chosen.append({
                    **item,
                    "group": group_name,
                })
                seen.add(item["symbol"])
                count += 1

    for item in sorted(
        pool,
        key=lambda x: -x["turnover_24h_usdt_est"],
    ):
        if len(chosen) >= n:
            break

        if item["symbol"] not in seen:
            chosen.append({
                **item,
                "group": "liquidity_backfill",
            })
            seen.add(item["symbol"])

    return chosen[:n]


def collect(venue, allowed, source, now_ms):
    available = tickers(venue, allowed, now_ms)

    if len(available) < MIN_VALIDATED:
        raise RuntimeError(
            f"Only {len(available)} eligible liquid tickers"
        )

    pre = prescreen(available, MAX_PRESCREEN)

    pool = []
    rejected = {}
    prescreen_candles = {}

    for raw_item in pre:
        item = dict(raw_item)
        symbol = item["symbol"]

        try:
            c15 = candles(
                venue, symbol, "15m", now_ms
            )
            vol_ratio, range_ratio = intraday_anomaly(c15)

            item["rel_volume_15m"] = vol_ratio
            item["rel_range_15m"] = range_ratio

            prescreen_candles[symbol] = c15
            pool.append(item)

        except (
            ValueError,
            RuntimeError,
            TypeError,
        ) as exc:
            rejected[symbol] = str(exc)

        time.sleep(0.10)

    selected = choose_universe(pool, UNIVERSE_SIZE)
    selected_names = {
        item["symbol"] for item in selected
    }

    fallback = [
        {
            **item,
            "group": "liquidity_backfill",
        }
        for item in sorted(
            pool,
            key=lambda x: -x["turnover_24h_usdt_est"],
        )
        if item["symbol"] not in selected_names
    ]

    validated = {}
    universe = []

    for item in selected + fallback:
        if len(validated) >= UNIVERSE_SIZE:
            break

        symbol = item["symbol"]

        try:
            series = {
                "15m": prescreen_candles[symbol],
            }

            for tf in ("2h", "1h"):
                series[tf] = candles(
                    venue, symbol, tf, now_ms
                )

            validated[symbol] = series
            universe.append(item)

        except (
            ValueError,
            RuntimeError,
            TypeError,
        ) as exc:
            rejected[symbol] = str(exc)

        time.sleep(0.10)

    if len(validated) < MIN_VALIDATED:
        raise RuntimeError(
            f"Only {len(validated)} full OHLCV validated "
            f"(minimum {MIN_VALIDATED})"
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
        "venue": venue,
        "exchange": (
            "OKX"
            if venue == "okx_swap"
            else "Binance"
        ),
        "market_type": (
            "USDT_PERPETUAL"
            if venue == "okx_swap"
            else "SPOT"
        ),
        "top100_filter": source,
        "collected_utc": utc(now_ms),
        "universe": universe,
        "candles": validated,
        "rejected": rejected,
        "diagnostics": {
            "available_liquid": len(available),
            "prescreen": len(pre),
            "initial_selected": len(selected),
            "validated": len(validated),
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
    Path("output").mkdir(exist_ok=True)

    now_ms = int(time.time() * 1000)
    allowed, source = eligible_bases()

    venues = (
        ["okx_swap", "binance_spot"]
        if MARKET_PROVIDER == "auto"
        else [MARKET_PROVIDER]
    )

    errors = {}

    for venue in venues:
        try:
            if venue not in (
                "okx_swap",
                "binance_spot",
            ):
                raise ValueError("Unknown MARKET_PROVIDER")

            print(
                f"Collecting {venue}; whitelist={source}",
                flush=True,
            )

            output = collect(
                venue, allowed, source, now_ms
            )

            output["provider_failures"] = errors

            Path("output/market_data.json").write_text(
                json.dumps(output, indent=2),
                encoding="utf-8",
            )

            print(
                f"SUCCESS {venue}: "
                f"{output['diagnostics']}; "
                "signals still disabled",
                flush=True,
            )
            return

        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            urllib.error.URLError,
        ) as exc:
            errors[venue] = str(exc)
            print(
                f"{venue} failed: {exc}",
                flush=True,
            )

    Path("output/collector_status.json").write_text(
        json.dumps({
            "status": "FAILED",
            "signals_allowed": False,
            "errors": errors,
            "utc": utc(now_ms),
        }, indent=2),
        encoding="utf-8",
    )

    raise RuntimeError(
        "No market provider returned 15 "
        "fully verified crypto instruments"
    )


if __name__ == "__main__":
    main()
