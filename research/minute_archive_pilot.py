"""Audit conditional snapshot signals against subsequently retrieved OKX 1m candles.

The CLI uses only the 100 archived snapshots supplied as input, then GETs
public historical 1m bars. It never writes back to market-data or executes trades.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from research.futures_scenario import Scenario
from research.minute_execution import MINUTE_MS, first_full_minute, evaluate, summarize
from research.okx_history_backfill import retrieve
from research.point_in_time_candidates import inspect_snapshots


def run(paths, max_candidates=25, max_pages=6, fetch_history=retrieve):
    if type(max_candidates) is not int or max_candidates < 1:
        raise ValueError("invalid candidate budget")
    observed = inspect_snapshots(paths)
    selected = [x for x in observed["candidates"]
                if x["decision_at_snapshot"] == "GATILHO_CONDICIONAL"]
    if len(selected) > max_candidates:
        raise ValueError("candidate budget exceeded: explicit review required")
    scenario = Scenario()
    outcomes, windows = [], []
    for item in selected:
        start = first_full_minute(item["first_observed_at_ms"])
        expiry = item["signal_close_ms"] + 900_000
        end = expiry + scenario.max_hold_bars*900_000
        if start >= expiry:
            rows, meta = [], {"pages_requested": 0, "candles_retrieved": 0,
                              "stopped_because": "no_complete_minute_before_expiry"}
        else:
            meta, rows = fetch_history(item["symbol"], "1m", start, end,
                                       max_pages=max_pages)
            if meta.get("backtest_ready") is not False:
                raise ValueError("missing historical-only provenance")
            if meta.get("candles_retrieved") != len(rows):
                raise ValueError("metadata and 1m candles disagree")
        outcome = evaluate(item, rows, scenario)
        record = {
            "symbol": item["symbol"], "method": item["method"],
            "side": item["side"], "signal_close_ms": item["signal_close_ms"],
            "observed_at_ms": item["first_observed_at_ms"],
            "activation_full_minute_ms": start,
            "trigger_expires_ms": expiry, "exchange_fill_verified": False,
            "retrieval": meta, **outcome,
        }
        outcomes.append(record)
        windows.append({"symbol": item["symbol"], "method": item["method"],
                        "side": item["side"], "signal_close_ms": item["signal_close_ms"],
                        "data_retrieved_after_the_fact": True, "minute_rows": rows})
    summary = summarize(selected, outcomes)
    report = {
        "source_snapshot_count": observed["snapshots_analyzed"],
        "source_confirmed_candidates": observed["confirmed_candidates"],
        "source_conditional_candidates": len(selected),
        "source_by_method": observed["by_method"],
        "retrieved_minute_rows": sum(len(x["minute_rows"]) for x in windows),
        "details": outcomes,
        **summary,
    }
    return report, windows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots", nargs="+")
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--windows-jsonl", type=Path)
    p.add_argument("--max-candidates", type=int, default=25)
    p.add_argument("--max-pages-per-candidate", type=int, default=6)
    args = p.parse_args(argv)
    report, windows = run(args.snapshots, args.max_candidates,
                          args.max_pages_per_candidate)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    if args.windows_jsonl:
        args.windows_jsonl.parent.mkdir(parents=True, exist_ok=True)
        args.windows_jsonl.write_text("".join(json.dumps(x)+"\n" for x in windows),
                                      encoding="utf-8")
    print("POST_SNAPSHOT_MINUTE_PILOT", json.dumps({
        "snapshot_count": report["source_snapshot_count"],
        "confirmed_candidates": report["source_confirmed_candidates"],
        "conditional_candidates": report["source_conditional_candidates"],
        "minute_rows": report["retrieved_minute_rows"],
        "by_method": report["by_method"], "proven_expectancy": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
