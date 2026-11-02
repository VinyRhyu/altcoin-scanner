
"""Altcoin Scanner V3 — first breakout and first pullback.

Research only: NO orders or notifications.
Uses verified closed 2h, 1h and 15m candles.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from collector import CRYPTO_BASES, BLOCKED_BASES

DUR = {"2h": 7_200_000, "1h": 3_600_000, "15m": 900_000}
MAX_BOS_AGE_1H = 18
MAX_TRIGGER_AGE_MS = 30 * 60_000
MAX_RETEST_BARS_15M = 4
MAX_BOS_TO_RETEST_MS = 8 * 3_600_000


def as_utc(ms):
    return datetime.fromtimestamp(
        ms / 1000, timezone.utc
    ).isoformat()


def candles_from(data, name):
    raw = data[name]
    out = []

    for x in raw:
        if isinstance(x, dict):
            r = [
                x["open_ms"], x["open"], x["high"],
                x["low"], x["close"], x["volume"],
                x.get("turnover_quote", x["volume"]),
            ]
        else:
            r = list(x)
            if len(r) == 6:
                r.append(r[5])

        if len(r) < 7:
            raise ValueError("Missing candle fields")

        r = [int(r[0])] + [float(v) for v in r[1:7]]

        if (
            not all(math.isfinite(v) for v in r)
            or r[0] % DUR[name]
            or not (
                0 < r[3] <= min(r[1], r[4])
                <= max(r[1], r[4]) <= r[2]
                and r[5] >= 0
                and r[6] >= 0
            )
        ):
            raise ValueError("Invalid OHLCV")

        out.append(r)

    if len(out) < 60 or any(
        b[0] - a[0] != DUR[name]
        for a, b in zip(out, out[1:])
    ):
        raise ValueError("Too few candles / timestamp gaps")

    return out


def atr(data, period=14):
    trs = []

    for i in range(
        max(1, len(data) - period), len(data)
    ):
        prev, cur = data[i - 1], data[i]
        trs.append(
            max(
                cur[2] - cur[3],
                abs(cur[2] - prev[4]),
                abs(cur[3] - prev[4]),
            )
        )

    return statistics.mean(trs)


def pivots(rows, idx, window=2):
    highs, lows = [], []

    for i in range(window, idx - window):
        cur = rows[i]
        other = (
            rows[i - window:i]
            + rows[i + 1:i + window + 1]
        )

        if all(cur[2] > x[2] for x in other):
            highs.append((i, cur[2]))

        if all(cur[3] < x[3] for x in other):
            lows.append((i, cur[3]))

    return highs, lows


def structural_events(
    hourly, direction, lookback=MAX_BOS_AGE_1H
):
    """Find first closes crossing confirmed swing levels."""
    events = []

    start = max(5, len(hourly) - lookback)

    for i in range(start, len(hourly)):
        highs, lows = pivots(hourly, i)
        swings = highs if direction == "LONG" else lows

        if not swings:
            continue

        level = swings[-1][1]
        before = hourly[i - 1][4]
        after = hourly[i][4]

        if direction == "LONG":
            crossed = before <= level < after
        else:
            crossed = before >= level > after

        if crossed:
            events.append({
                "index": i,
                "level": level,
                "closed_ms": hourly[i][0] + DUR["1h"],
            })

    return events[::-1]


def latest_bos(hourly, direction):
    """Compatibility with the existing V2 tests."""
    events = structural_events(
        hourly, direction, lookback=6
    )

    if not events:
        return None

    return events[0]["index"], events[0]["level"]


def first_retest_trigger(
    quart, event, direction,
    tolerance, a15, snapshot_ms
):
    level = event["level"]

    start_idx = next(
        (
            i for i, row in enumerate(quart)
            if row[0] >= event["closed_ms"]
        ),
        len(quart),
    )

    if start_idx >= len(quart):
        return None

    first_touch = None

    for i in range(start_idx, len(quart)):
        candle = quart[i]
        touched = (
            candle[3] <= level + tolerance
            and candle[2] >= level - tolerance
        )

        if touched:
            first_touch = i
            break

    if first_touch is None:
        return None

    if first_touch < len(quart) - MAX_RETEST_BARS_15M:
        return None

    if (
        quart[first_touch][0] - event["closed_ms"]
        > MAX_BOS_TO_RETEST_MS
    ):
        return None

    for row in quart[start_idx:]:
        if (
            direction == "LONG"
            and row[4] < level - 0.50 * a15
        ):
            return None

        if (
            direction == "SHORT"
            and row[4] > level + 0.50 * a15
        ):
            return None

    end = min(
        first_touch + MAX_RETEST_BARS_15M,
        len(quart),
    )

    for i in range(first_touch, end):
        if i < 1 or i < len(quart) - 2:
            continue

        candle = quart[i]
        prev = quart[i - 1]
        close_ms = candle[0] + DUR["15m"]

        if not (
            0 <= snapshot_ms - close_ms
            <= MAX_TRIGGER_AGE_MS
        ):
            continue

        if direction == "LONG":
            accepted = (
                candle[4] > candle[1]
                and candle[4] > prev[2]
                and candle[4] > level
            )
        else:
            accepted = (
                candle[4] < candle[1]
                and candle[4] < prev[3]
                and candle[4] < level
            )

        if not accepted:
            continue

        rest = quart[i + 1:]

        invalidated = any(
            (
                x[4] < level
                if direction == "LONG"
                else x[4] > level
            )
            for x in rest
        )

        if invalidated:
            continue

        retest_rows = quart[first_touch:i + 1]

        return {
            "touch_index": first_touch,
            "trigger_index": i,
            "trigger_close_ms": close_ms,
            "retest_low": min(
                r[3] for r in retest_rows
            ),
            "retest_high": max(
                r[2] for r in retest_rows
            ),
        }

    return None


def find_targets(
    two, hourly, entry_worst,
    direction, risk, a1
):
    high2, low2 = pivots(two, len(two))
    high1, low1 = pivots(hourly, len(hourly))

    raw = (
        [x[1] for x in high2 + high1]
        if direction == "LONG"
        else [x[1] for x in low2 + low1]
    )

    obstacles = sorted(
        set(round(float(x), 10) for x in raw),
        reverse=(direction == "SHORT"),
    )

    obstacles = [
        x for x in obstacles
        if (
            x > entry_worst
            if direction == "LONG"
            else x < entry_worst
        )
    ]

    if len(obstacles) < 2 or risk <= 0:
        return None

    first, second = obstacles[:2]
    pad = max(a1 * 0.08, entry_worst * 0.0005)

    if direction == "LONG":
        tp1 = first - pad
        tp2 = second - pad
        r1 = tp1 - entry_worst
        r2 = tp2 - entry_worst
    else:
        tp1 = first + pad
        tp2 = second + pad
        r1 = entry_worst - tp1
        r2 = entry_worst - tp2

    if (
        r1 / risk < 0.65
        or r2 / risk < 1.6
        or r2 <= r1
    ):
        return None

    return tp1, tp2, r2 / risk


def evaluate(
    item, frame, collected_ms,
    report_stale=False
):
    result = {
        "symbol": item["symbol"],
        "status": "REJECTED",
        "reasons": [],
    }

    base = item.get("base") or (
        item["symbol"].split("-")[0]
        if "-" in item["symbol"]
        else item["symbol"].removesuffix("USDT")
    )

    if (
        base not in CRYPTO_BASES
        or base in BLOCKED_BASES
    ):
        result["reasons"].append(
            "Not an eligible cryptocurrency"
        )
        return result

    try:
        two, hour, quart = (
            candles_from(frame, k)
            for k in ("2h", "1h", "15m")
        )
    except (ValueError, KeyError, TypeError) as exc:
        result["reasons"].append(
            f"Unverified candles: {exc}"
        )
        return result

    for tf, series in (
        ("2h", two),
        ("1h", hour),
        ("15m", quart),
    ):
        end_ms = series[-1][0] + DUR[tf]

        if not (
            0 <= collected_ms - end_ms <= DUR[tf]
        ):
            result["reasons"].append(
                f"Stale or future {tf} closed candle"
            )
            return result

    if not report_stale:
        current_ms = (
            datetime.now(timezone.utc).timestamp()
            * 1000
        )
        if abs(current_ms - collected_ms) > 20 * 60_000:
            result["reasons"].append(
                "Market snapshot is stale (>20 min)"
            )
            return result

    price = float(item.get("price", 0))

    if not math.isfinite(price) or price <= 0:
        result["reasons"].append(
            "Invalid ticker price"
        )
        return result

    stamp = item.get("ticker_time_utc")

    if stamp and not report_stale:
        try:
            observed = (
                datetime.fromisoformat(
                    stamp.replace("Z", "+00:00")
                ).timestamp() * 1000
            )

            if not (
                0 <= collected_ms - observed <= 180_000
            ):
                result["reasons"].append(
                    "Ticker timestamp stale/future"
                )
                return result

        except (ValueError, TypeError):
            result["reasons"].append(
                "Ticker timestamp invalid"
            )
            return result

    a1 = atr(hour)
    a15 = atr(quart)

    if min(a1, a15, atr(two)) <= 0:
        result["reasons"].append("ATR invalid")
        return result

    two_h, two_l = pivots(two, len(two))

    if not two_h or not two_l:
        result["reasons"].append(
            "Missing 2h swing structure"
        )
        return result

    attempted = []

    for direction in ("LONG", "SHORT"):
        if (
            direction == "SHORT"
            and item.get("market_type") == "SPOT"
        ):
            attempted.append(
                "SHORT: spot market cannot "
                "assume margin execution"
            )
            continue

        events = structural_events(hour, direction)

        if not events:
            attempted.append(
                f"{direction}: no confirmed 1h "
                f"structural break in last "
                f"{MAX_BOS_AGE_1H} bars"
            )
            continue

        failure = (
            f"{direction}: no first 15m retest "
            "with fresh closed-candle trigger"
        )

        for event in events:
            level = event["level"]

            if abs(price - level) > min(
                1.35 * a1, 2.6 * a15
            ):
                failure = (
                    f"{direction}: extended away "
                    "from 1h origin (ATR filter)"
                )
                continue

            retest = first_retest_trigger(
                quart,
                event,
                direction,
                0.35 * a1,
                a15,
                collected_ms,
            )

            if not retest:
                continue

            trigger = quart[retest["trigger_index"]]

            band = max(
                0.18 * a15,
                price * 0.0005,
            )

            entry_low = trigger[4] - band
            entry_high = trigger[4] + band

            if not entry_low <= price <= entry_high:
                failure = (
                    f"{direction}: ticker outside "
                    "structural trigger entry band"
                )
                continue

            if direction == "LONG":
                stop = min(
                    level, retest["retest_low"]
                ) - 0.15 * a15
                worst = entry_high
                risk = worst - stop
            else:
                stop = max(
                    level, retest["retest_high"]
                ) + 0.15 * a15
                worst = entry_low
                risk = stop - worst

            if (
                risk <= 0
                or not 0.7 * a15 <= risk <= 2.2 * a1
            ):
                failure = (
                    f"{direction}: invalid "
                    "structural SL distance"
                )
                continue

            targets = find_targets(
                two, hour, worst,
                direction, risk, a1
            )

            if targets is None:
                failure = (
                    f"{direction}: nearby obstacle "
                    "or RR below 1:1.6"
                )
                continue

            tp1, tp2, rr = targets
            impulse = hour[event["index"]]

            if direction == "LONG":
                impulse_body = (
                    impulse[4] - impulse[1]
                )
                trigger_body = (
                    trigger[4] - trigger[1]
                )
            else:
                impulse_body = (
                    impulse[1] - impulse[4]
                )
                trigger_body = (
                    trigger[1] - trigger[4]
                )

            momentum = (
                impulse_body >= 0.2 * a1
                and trigger_body >= 0.15 * a15
            )

            index = retest["trigger_index"]

            prev15 = [
                x[6]
                for x in quart[
                    max(0, index - 20):index
                ]
            ]

            prev1 = [
                x[6]
                for x in hour[
                    max(0, event["index"] - 20):
                    event["index"]
                ]
            ]

            med15 = (
                statistics.median(prev15)
                if prev15 else 0
            )

            med1 = (
                statistics.median(prev1)
                if prev1 else 0
            )

            volume15 = (
                trigger[6] / med15
                if med15 else 0
            )

            volume1 = (
                impulse[6] / med1
                if med1 else 0
            )

            participation = (
                volume15 >= 0.8
                and max(volume15, volume1) >= 1.2
            )

            if not participation:
                failure = (
                    f"{direction}: insufficient "
                    "participation at first retest"
                )
                continue

            score = (
                1
                + int(momentum)
                + int(participation)
                + 1
            )

            if score < 3:
                failure = (
                    f"{direction}: score below 3/4"
                )
                continue

            return {
                "symbol": item["symbol"],
                "direction": direction,
                "status": "PAPER_CANDIDATE",
                "entry_min": round(entry_low, 10),
                "entry_max": round(entry_high, 10),
                "sl": round(stop, 10),
                "tp1": round(tp1, 10),
                "tp2": round(tp2, 10),
                "rr_worst_to_tp2": round(rr, 3),
                "score": score,
                "structure": (
                    "Confirmed hourly BOS; first "
                    "15m retest and candle break"
                ),
                "bos_utc": as_utc(
                    event["closed_ms"]
                ),
                "first_retest_utc": as_utc(
                    quart[
                        retest["touch_index"]
                    ][0] + DUR["15m"]
                ),
                "trigger_utc": as_utc(
                    retest["trigger_close_ms"]
                ),
                "ticker_observed_utc": stamp,
                "observed_price": price,
                "volume_ratio_15m": round(
                    volume15, 2
                ),
                "volume_ratio_bos_1h": round(
                    volume1, 2
                ),
                "market_type": item.get(
                    "market_type"
                ),
                "replay": report_stale,
            }

        attempted.append(failure)

    result["reasons"] = (
        attempted or ["No qualified setup"]
    )
    return result


def parse_snapshot_ms(payload):
    stamp = (
        payload.get("collected_utc")
        or payload.get("collected_at_utc")
    )

    return int(
        datetime.fromisoformat(
            stamp.replace("Z", "+00:00")
        ).timestamp() * 1000
    )


def analyze_payload(payload, report_stale=False):
    stamp = parse_snapshot_ms(payload)
    results = []

    for item in payload["universe"]:
        symbol = item["symbol"]

        if symbol not in payload["candles"]:
            continue

        item = {
            **item,
            "market_type": payload.get("market_type"),
        }

        results.append(
            evaluate(
                item,
                payload["candles"][symbol],
                stamp,
                report_stale,
            )
        )

    approved = [
        x for x in results
        if x["status"] == "PAPER_CANDIDATE"
    ]

    return {
        "status": "DRY_RUN",
        "signals_enabled": False,
        "market": payload.get(
            "market_type", payload.get("venue")
        ),
        "snapshot_utc": payload.get(
            "collected_utc"
        ),
        "evaluated": len(results),
        "paper_candidates": approved[:3],
        "all_paper_candidates_count": len(approved),
        "rejected_count": (
            len(results) - len(approved)
        ),
        "audit_rejections": [
            {
                "symbol": x["symbol"],
                "reasons": x.get("reasons", []),
            }
            for x in results
            if x["status"] == "REJECTED"
        ],
        "note": (
            "Research only. NO alerts or "
            "trades. Replay and paper trading "
            "required."
        ),
    }


def main():
    file = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "output/market_data.json"
    )

    payload = json.loads(
        file.read_text(encoding="utf-8")
    )

    out = analyze_payload(payload)

    Path("output").mkdir(exist_ok=True)

    Path("output/analysis.json").write_text(
        json.dumps(out, indent=2),
        encoding="utf-8",
    )

    print(
        f"V3 DRY RUN: {out['evaluated']} evaluated; "
        f"{out['all_paper_candidates_count']} "
        "paper candidates; no notifications"
    )


if __name__ == "__main__":
    main()
