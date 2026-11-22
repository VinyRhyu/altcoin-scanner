"""Read-only reliability audit of PUBLIC GitHub Actions scheduled runs.

Counts observed *created* workflow runs against expected cron slots in a
fixed UTC window. It does NOT claim which particular cron slots were dropped,
nor prove scheduler delay root cause: created_at != intended trigger time.
No private trade, market order, webhook or account information is processed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics

TARGET_WORKFLOW="OKX - Coleta e publicacao de dados"
CRON_MINUTES=(1,16,31,46)


def utc(value):
    x=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if x.tzinfo is None:
        raise ValueError("timestamp needs timezone")
    return x.astimezone(timezone.utc)


def audit(raw,start,end):
    start,end=utc(start),utc(end)
    if start>=end or end-start>timedelta(days=7):
        raise ValueError("invalid bounded UTC window")
    runs=raw.get("workflow_runs")
    if not isinstance(runs,list) or not runs:
        raise ValueError("missing workflow runs")
    if len(runs)>100:
        raise ValueError("unexpected page size")
    created=[]
    for r in runs:
        if not isinstance(r,dict) or not r.get("created_at"):
            raise ValueError("incomplete GitHub workflow run")
        created.append(utc(r["created_at"]))
    if min(created)>start:
        raise ValueError("latest public API page does not cover start of window")
    # Cron minute targets are deliberately fully inside the fixed window.
    expected=[]
    t=start.replace(second=0,microsecond=0)
    while t<end:
        if t.minute in CRON_MINUTES:
            expected.append(t)
        t+=timedelta(minutes=1)
    relevant=sorted((
        r for r in runs if r.get("name")==TARGET_WORKFLOW and
        r.get("event")=="schedule" and
        start<=utc(r["created_at"])<end
    ),key=lambda r:r["created_at"])
    observed=[utc(r["created_at"]) for r in relevant]
    intervals=[round((b-a).total_seconds(),3)
               for a,b in zip(observed,observed[1:])]
    statuses={}
    for r in relevant:
        name=r.get("conclusion") or r.get("status") or "unknown"
        statuses[name]=statuses.get(name,0)+1
    return {
        "status":"GITHUB_SCHEDULED_RUNS_RESEARCH_ONLY",
        "window_start_utc":start.isoformat(),
        "window_end_exclusive_utc":end.isoformat(),
        "configured_cron_minutes_utc":list(CRON_MINUTES),
        "expected_nominal_cron_slots_in_window":len(expected),
        "actual_scheduled_workflow_runs_CREATED_in_window":len(relevant),
        "observed_runs_per_expected_slot_ratio":round(
            len(relevant)/len(expected),5) if expected else None,
        "actual_observed_create_times_utc":[x.isoformat() for x in observed],
        "observed_run_conclusions":statuses,
        "max_gap_between_observed_runs_minutes":round(
            max(intervals)/60,3) if intervals else None,
        "individual_missing_trigger_slots_established":False,
        "github_scheduler_root_cause_proven":False,
        "job_queue_delay_directly_measured":False,
        "production_cron_definition_assumed_current_through_window":True,
        "production_changed":False,
        "user_trade_data_read":False,
        "limitations":[
            "Run created_at is not the actual intended cron occurrence timestamp",
            "Runs may be delayed across the fixed window edges or schedule definition may have changed",
            "Total number of observed runs can differ from expected slots; do not attribute cause without job/queue telemetry",
            "Comparing runs against one current cron definition assumes it also applied during the window",
            "This is a read-only audit of GitHub Actions metadata, not price or trading data",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--start",required=True)
    p.add_argument("--end",required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    r=audit(json.loads(args.input.read_text(encoding="utf-8")),
            args.start,args.end)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print("SCHEDULE_RELIABILITY",json.dumps({
        k:r[k] for k in (
            "expected_nominal_cron_slots_in_window",
            "actual_scheduled_workflow_runs_CREATED_in_window",
            "observed_runs_per_expected_slot_ratio",
            "actual_observed_create_times_utc",
            "github_scheduler_root_cause_proven",
        )
    },sort_keys=True))


if __name__=="__main__":
    main()
