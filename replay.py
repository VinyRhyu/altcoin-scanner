
"""Historical replay — research only, no real trades."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from analyzer import DUR, evaluate

HORIZON_BARS_15M = 12


def path_outcome(signal, future, direction, entry_close):
    stop = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    touched_tp1 = False

    for bar in future:
        low, high = bar[3], bar[2]

        stopped = (
            low <= stop
            if direction == "LONG"
            else high >= stop
        )

        if stopped:
            return "STOP", touched_tp1

        reached_tp2 = (
            high >= tp2
            if direction == "LONG"
            else low <= tp2
        )

        if reached_tp2:
            return "TP2", True

        reached_tp1 = (
            high >= tp1
            if direction == "LONG"
            else low <= tp1
        )

        if reached_tp1:
            touched_tp1 = True

    return (
        "EXPIRED_TP1" if touched_tp1
        else "EXPIRED"
    ), touched_tp1


def replay_payload(payload):
    events = []
    reasons = Counter()
    snapshots = 0

    for item in payload["universe"]:
        symbol = item["symbol"]

        if symbol not in payload["candles"]:
            continue

        source = payload["candles"][symbol]
        m15 = source["15m"]
        seen = set()

        for j in range(59, len(m15) - 1):
            cutoff = m15[j][0] + DUR["15m"]

            frame = {
                tf: [
                    r for r in source[tf]
                    if r[0] + DUR[tf] <= cutoff
                ]
                for tf in DUR
            }

            if any(
                len(frame[tf]) < 60
                for tf in DUR
            ):
                continue

            snapshots += 1

            replay_item = dict(
                item,
                price=frame["15m"][-1][4],
                market_type=payload.get("market_type"),
                ticker_time_utc=(
                    datetime.fromtimestamp(
                        cutoff / 1000,
                        timezone.utc
                    ).isoformat()
                ),
            )

            verdict = evaluate(
                replay_item,
                frame,
                cutoff,
                report_stale=True,
            )

            if verdict["status"] != "PAPER_CANDIDATE":
                for reason in verdict["reasons"]:
                    reasons[reason] += 1
                continue

            signature = (
                symbol,
                verdict["direction"],
                verdict["bos_utc"],
                verdict["trigger_utc"],
            )

            if signature in seen:
                continue

            seen.add(signature)

            future = m15[
                j + 1:
                j + 1 + HORIZON_BARS_15M
            ]

            if len(future) < HORIZON_BARS_15M:
                status = "INSUFFICIENT_FUTURE_DATA"
                tp1_hit = False
            else:
                status, tp1_hit = path_outcome(
                    verdict,
                    future,
                    verdict["direction"],
                    replay_item["price"],
                )

            events.append({
                "symbol": symbol,
                "direction": verdict["direction"],
                "setup_utc": verdict["trigger_utc"],
                "entry_close_assumed": replay_item[
                    "price"
                ],
                "rr_worst_to_tp2": verdict[
                    "rr_worst_to_tp2"
                ],
                "outcome": status,
                "tp1_touched": tp1_hit,
            })

    return {
        "mode": "POINT_IN_TIME_REPLAY_RESEARCH_ONLY",
        "market": payload.get(
            "market_type", payload.get("venue")
        ),
        "snapshots_evaluated": snapshots,
        "candidate_count": len(events),
        "outcomes": dict(
            Counter(x["outcome"] for x in events)
        ),
        "events": events[:100],
        "rejections_top": reasons.most_common(12),
        "caveats": [
            "Limited historical window; selection bias.",
            "No real fills, fees, funding or slippage.",
            "Same-bar stop and TP: stop takes priority.",
            "No execution or notifications.",
        ],
    }


def main():
    path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else "output/market_data.json"
    )

    payload = json.loads(
        path.read_text(encoding="utf-8")
    )

    result = replay_payload(payload)

    Path("output").mkdir(exist_ok=True)

    Path("output/replay.json").write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )

    print(
        f"REPLAY: {result['snapshots_evaluated']} "
        f"snapshots; {result['candidate_count']} "
        "hypothetical events; no alerts"
    )


if __name__ == "__main__":
    main()
