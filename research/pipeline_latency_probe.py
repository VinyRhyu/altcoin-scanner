"""Read-only timing probe for a research scanner dry-run (never orders).

Distinct events are recorded with wall and monotonic clocks on ONE Actions
runner. The probe measures command execution durations and quote staleness.
It CANNOT independently determine GitHub queue delay, alert publication, user
receipt or exchange fills. No external/user trade data is accepted or logged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import time

EVENTS = (
    "runner_started",
    "collector_started",
    "allowlist_started",
    "allowlist_complete",
    "ticker_fetch_started",
    "ticker_fetch_complete",
    "prescreen_15m_started",
    "prescreen_15m_complete",
    "selected_multitf_started",
    "selected_multitf_complete",
    "collector_complete",
    "decision_started",
    "decision_complete",
    "artifact_handoff_started",
)
REQUIRED = (
    "runner_started", "collector_started", "collector_complete",
    "decision_started", "decision_complete", "artifact_handoff_started",
)
DURATION_PAIRS = (
    ("runner_to_collector_start","runner_started","collector_started"),
    ("allowlist","allowlist_started","allowlist_complete"),
    ("ticker_discovery","ticker_fetch_started","ticker_fetch_complete"),
    ("prescreen_15m","prescreen_15m_started","prescreen_15m_complete"),
    ("selected_multitf","selected_multitf_started","selected_multitf_complete"),
    ("collector_wall","collector_started","collector_complete"),
    ("decision_wall","decision_started","decision_complete"),
    ("runner_to_artifact_handoff","runner_started","artifact_handoff_started"),
)
BAR_MS=900_000


def _parse_utc(text):
    d=datetime.fromisoformat(str(text).replace("Z","+00:00"))
    if d.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return round(d.astimezone(timezone.utc).timestamp()*1000)


def mark_event(log, event, *, wall_ms=None, monotonic_ns=None):
    """Append one event; opt-in from research Action or collector only.

    Test clocks can be supplied to verify time calculations without sleeping.
    """
    if event not in EVENTS:
        raise ValueError("unknown timing event")
    row={
        "event":event,
        "wall_ms":int(time.time_ns()//1_000_000 if wall_ms is None else wall_ms),
        "monotonic_ns":int(time.monotonic_ns() if monotonic_ns is None else monotonic_ns),
    }
    if row["wall_ms"]<=0 or row["monotonic_ns"]<0:
        raise ValueError("invalid clock values")
    path=Path(log)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a",encoding="utf-8") as f:
        f.write(json.dumps(row,sort_keys=True)+"\n")
    return row


def read_events(log):
    rows=[json.loads(line) for line in Path(log).read_text(encoding="utf-8").splitlines()
          if line.strip()]
    if not rows:
        raise ValueError("empty research timing trace")
    found={}
    previous_wall=previous_mono=None
    for row in rows:
        if set(row)!={"event","wall_ms","monotonic_ns"}:
            raise ValueError("timing trace contains unknown fields")
        event=row["event"]
        if event not in EVENTS or event in found:
            raise ValueError("duplicate or unknown timing event")
        if type(row["wall_ms"]) is not int or type(row["monotonic_ns"]) is not int:
            raise ValueError("invalid numeric clock types")
        if (previous_mono is not None and
            row["monotonic_ns"]<previous_mono):
            raise ValueError("monotonic clock moved backward")
        if (previous_wall is not None and
            row["wall_ms"]<previous_wall-2000):
            raise ValueError("wall clock moved backward >2 seconds")
        previous_wall,previous_mono=row["wall_ms"],row["monotonic_ns"]
        found[event]=row
    if not all(name in found for name in REQUIRED):
        raise ValueError("missing required timing checkpoints")
    if any(EVENTS.index(a)>=EVENTS.index(b) for a,b in zip(
            [x["event"] for x in rows],[x["event"] for x in rows][1:])):
        raise ValueError("checkpoints out of declared order")
    return found


def _percentile(vals,p):
    if not vals:
        return None
    ordered=sorted(vals)
    return round(ordered[max(0,math.ceil(len(ordered)*p)-1)],3)


def summarize(log, market_path, decision_path):
    seen=read_events(log)
    market=json.loads(Path(market_path).read_text(encoding="utf-8"))
    decisions=json.loads(Path(decision_path).read_text(encoding="utf-8"))
    if (market.get("status")!="COLLECTED" or
        market.get("market_type")!="USDT_PERPETUAL"):
        raise ValueError("data collection not complete")
    if not isinstance(decisions,dict) or decisions.get("order_allowed") is not False:
        raise ValueError("decision result could authorize trading")
    captured_ms=_parse_utc(market["collected_utc"])
    if abs(captured_ms-seen["collector_started"]["wall_ms"])>120_000:
        raise ValueError("collector snapshot initialization differs from command start")
    if captured_ms>seen["collector_complete"]["wall_ms"]:
        raise ValueError("collection timestamp comes after collector completion")
    durations={}
    for name,a,b in DURATION_PAIRS:
        if (a in seen)!=(b in seen):
            raise ValueError("timing span has exactly one checkpoint")
        if a in seen:
            dur=(seen[b]["monotonic_ns"]-seen[a]["monotonic_ns"])/1e9
            if dur<0:
                raise ValueError("negative monotonic duration")
            durations[name+"_seconds"]=round(dur,3)
    ticker_ages=[]
    missing_ts=0
    symbols=set()
    for t in market.get("universe",[]):
        if not isinstance(t,dict) or not isinstance(t.get("symbol"),str):
            raise ValueError("invalid symbol in collector output")
        if t["symbol"] in symbols:
            raise ValueError("duplicate tickers")
        symbols.add(t["symbol"])
        if t.get("ticker_time_utc") is None:
            missing_ts+=1
            continue
        ts=_parse_utc(t["ticker_time_utc"])
        age=(seen["decision_started"]["wall_ms"]-ts)/1000
        if age < -5:
            raise ValueError("future exchange quote timestamp")
        ticker_ages.append(age)
    # A dry-run job can begin at any point within a 15m candle. Never
    # interpret this as observed scheduled queue time or successful trading.
    collected_lag=(captured_ms//BAR_MS)*BAR_MS
    bars_to_close=[]
    for symbol in symbols:
        hist=market.get("candles",{}).get(symbol,{}).get("15m",[])
        if hist:
            last=int(hist[-1][0])
            if last%BAR_MS:
                raise ValueError("unaligned last 15m candle")
            bars_to_close.append(last+BAR_MS)
    if not bars_to_close:
        raise ValueError("no 15m source candles")
    modal,n=Counter(bars_to_close).most_common(1)[0]
    if not modal<=captured_ms:
        raise ValueError("source 15m candle not yet closed")
    return {
        "status":"RESEARCH_ONLY_TIMED_OKX_DRY_RUN",
        "event_name":os.getenv("GITHUB_EVENT_NAME","local"),
        "run_id":os.getenv("GITHUB_RUN_ID","local"),
        "timepoints_utc":{k:datetime.fromtimestamp(
            v["wall_ms"]/1000,timezone.utc).isoformat()
            for k,v in seen.items()},
        "durations":durations,
        "snapshot_capture_initialized_utc":market["collected_utc"],
        "snapshot_capture_initiated_not_completed":True,
        "last_closed_15m_modal_utc":datetime.fromtimestamp(
            modal/1000,timezone.utc).isoformat(),
        "capture_initiated_seconds_after_last_closed_bar":round(
            (captured_ms-modal)/1000,3),
        "captured_ticker_count":len(symbols),
        "ticker_timestamps_missing":missing_ts,
        "quote_age_at_decision_start_seconds":{
            "count":len(ticker_ages),
            "median":round(statistics.median(ticker_ages),3)
                     if ticker_ages else None,
            "p90":_percentile(ticker_ages,.9),
            "max":round(max(ticker_ages),3) if ticker_ages else None,
        },
        "selected_15m_streams_at_same_latest_close":n,
        "selected_15m_streams_with_different_close":len(bars_to_close)-n,
        "cron_queue_latency_verified":False,
        "publication_completed":False,
        "alert_delivery_verified":False,
        "order_execution_verified":False,
        "manual_or_push_run_not_equivalent_to_scheduled_runner":True,
        "personal_trades_recorded":False,
        "production_changed":False,
        "limitations":[
            "This workflow is push/dispatch on a TEST branch, NOT a real scheduled production run",
            "The first step is not the true GitHub Actions job start or event queue entry",
            "Market collected_utc marks when collector starts, not data retrieval completion",
            "Each quote timestamp is exchange reported; API transport and book changes remain unobserved",
            "No market-data branch publication, alert delivery or exchange order is performed",
            "Actual +60s cron latency needs separately instrumented production job or timestamps from Actions API",
            "Artifacts are uploaded after the handoff checkpoint and delivery completion is not measured",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    m=sub.add_parser("mark")
    m.add_argument("--log",type=Path,required=True)
    m.add_argument("--event",choices=EVENTS,required=True)
    s=sub.add_parser("report")
    s.add_argument("--log",type=Path,required=True)
    s.add_argument("--market-data",type=Path,required=True)
    s.add_argument("--decisions",type=Path,required=True)
    s.add_argument("--output",type=Path,required=True)
    a=p.parse_args(argv)
    if a.command=="mark":
        e=mark_event(a.log,a.event)
        print("PIPELINE_STAGE_MARKED",e["event"],e["wall_ms"])
    else:
        r=summarize(a.log,a.market_data,a.decisions)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
        print("PIPELINE_TIMING_RESEARCH",json.dumps({
            "status":r["status"],"durations":r["durations"],
            "quote_age_at_decision":r["quote_age_at_decision_start_seconds"],
            "bar_close_to_collection_initialized_seconds":
                r["capture_initiated_seconds_after_last_closed_bar"],
            "delivery_verified":False,"orders":0,
        },sort_keys=True))


if __name__=="__main__":
    main()
