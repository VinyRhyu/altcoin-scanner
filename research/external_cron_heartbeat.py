"""Read-only cron-job.org history vs GitHub workflow metadata.

This does NOT configure the external scheduler, replay requests, read secrets,
or assert that a successful HTTP check means a scan or publication succeeded.
Input cron-job.org JSON is kept in memory: URL, body, headers, identifiers and
credentials NEVER copied to the sanitized output. Optional GitHub runs are a
different evidence channel, not an attribution of dispatch to cron-job.org.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics


def _percentile(values, p):
    if not values:
        return None
    return round(sorted(values)[max(0, min(len(values)-1, int((len(values)-1)*p + 0.999999)))], 3)


def audit(history=None, runs=None, *, expected_interval_seconds=60):
    if expected_interval_seconds!=60:
        raise ValueError("external heartbeat is declared one minute; not a GitHub cron")
    result={
        "status":"EXTERNAL_CRONJOB_ORG_HEARTBEAT_READ_ONLY",
        "scheduler_declared_by_user":"cron-job.org",
        "scheduler_reported_interval_seconds":60,
        "cronjob_history_available":history is not None,
        "cronjob_observed":None,
        "github_observed":None,
        "external_target_url_known":False,
        "github_workflow_trigger_proven_from_external_history":False,
        "external_requests_equal_full_market_scans":False,
        "http_success_proves_market_publication":False,
        "root_cause_of_missing_scan_proven":False,
        "production_changed":False,
        "user_trade_data_recorded":False,
        "orders_executed":0,
        "limitations":[
            "cron-job.org can call an arbitrary URL; the request target is not supplied",
            "A one-minute external heartbeat need not imply a full 15m market refresh",
            "GitHub Actions event=schedule counts only GitHub schedule, not cron-job.org HTTP calls",
            "GitHub workflow_dispatch can originate from multiple sources; cannot attribute to cron-job.org without request linkage",
            "cron-job.org history API returns only a bounded recent sample; unlisted minutes are not proven missed",
            "HTTP success only demonstrates that target responded, not any downstream data was published",
        ],
    }
    if history is not None:
        if not isinstance(history,dict) or not isinstance(history.get("history"),list):
            raise ValueError("invalid cron-job.org history payload")
        entries=history["history"]
        if len(entries)>10000:
            raise ValueError("unbounded or unexpected history")
        plans=[]; starts=[]; jitters=[]; durations=[]; status=Counter()
        ids=set()
        for item in entries:
            if not isinstance(item,dict):
                raise ValueError("malformed history row")
            plan=item.get("datePlanned")
            actual=item.get("date")
            event_id=item.get("jobLogId")
            st=item.get("status")
            if (type(plan) is not int or type(actual) is not int or
                plan<=0 or actual<=0 or actual<plan-60 or
                type(st) is not int or not 0<=st<=10 or
                type(event_id) is not int or event_id in ids):
                raise ValueError("invalid or duplicate job execution")
            ids.add(event_id)
            jitter=(actual-plan)*1000
            duration=item.get("duration")
            if type(duration) is not int or duration<0:
                raise ValueError("invalid observed external request duration")
            plans.append(plan);starts.append(actual);jitters.append(jitter)
            durations.append(duration)
            code=item.get("httpStatus")
            status[(st,code if type(code) is int else None)]+=1
        sorted_plans=sorted(plans)
        result["cronjob_observed"]={
            "recorded_requests":len(entries),
            "earliest_planned_utc":datetime.fromtimestamp(
                min(plans),timezone.utc).isoformat() if plans else None,
            "latest_planned_utc":datetime.fromtimestamp(
                max(plans),timezone.utc).isoformat() if plans else None,
            "actual_calls_with_status_ok":sum(1 for e in entries if e["status"]==1),
            "actual_calls_with_non_ok_status":sum(1 for e in entries if e["status"]!=1),
            "actual_calls_http_2xx":sum(1 for e in entries
                if type(e.get("httpStatus")) is int and
                200<=e["httpStatus"]<300),
            "p50_schedule_jitter_ms":round(statistics.median(jitters),3) if jitters else None,
            "p90_schedule_jitter_ms":_percentile(jitters,.9),
            "p50_request_duration_ms":round(statistics.median(durations),3) if durations else None,
            "status_counts":{f"{s}:{c if c is not None else 'none'}":n
                             for (s,c),n in sorted(status.items(),key=lambda x:str(x[0]))},
            "adjacent_recorded_plans_one_minute_apart":sum(
                b-a==expected_interval_seconds
                for a,b in zip(sorted_plans,sorted_plans[1:])),
            "adjacent_recorded_plans_over_one_minute_apart":sum(
                b-a>expected_interval_seconds
                for a,b in zip(sorted_plans,sorted_plans[1:])),
            "unrecorded_minute_slots_must_not_be_labelled_failed":True,
            "external_target_url_redacted":True,
        }
    if runs is not None:
        if not isinstance(runs,dict) or not isinstance(runs.get("workflow_runs"),list):
            raise ValueError("invalid GitHub public workflow run input")
        rows=runs["workflow_runs"]
        if len(rows)>2000:
            raise ValueError("unbounded GitHub run page")
        counts=Counter()
        by_name=Counter()
        latest=None
        for r in rows:
            if not isinstance(r,dict) or not r.get("created_at"):
                raise ValueError("invalid GitHub run metadata")
            event=r.get("event","unknown")
            if event not in ("schedule","workflow_dispatch","repository_dispatch","push","other"):
                event="other"
            counts[event]+=1
            if r.get("name")=="OKX - Coleta e publicacao de dados":
                by_name[event]+=1
            stamp=datetime.fromisoformat(r["created_at"].replace("Z","+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("GitHub run time lacks timezone")
            if latest is None or stamp>latest:
                latest=stamp
        result["github_observed"]={
            "api_page_run_count":len(rows),
            "api_total_count_if_available":runs.get("total_count"),
            "events_in_api_page":dict(sorted(counts.items())),
            "scanner_event_counts_in_page":dict(sorted(by_name.items())),
            "latest_run_created_utc":latest.astimezone(timezone.utc).isoformat()
                if latest else None,
            "page_may_not_cover_a_full_minute_range":True,
            "github_scheduled_count_is_not_external_success_rate":True,
        }
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--history",type=Path,help="Optional PRIVATE cron-job.org history JSON, never uploaded")
    p.add_argument("--github-runs",type=Path,help="Optional public GitHub runs JSON")
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    cron=json.loads(args.history.read_text(encoding="utf-8")) if args.history else None
    github=json.loads(args.github_runs.read_text(encoding="utf-8")) if args.github_runs else None
    result=audit(cron,github)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("EXTERNAL_CRON_HEALTH_SANITIZED",json.dumps({
        "history_present":result["cronjob_history_available"],
        "external_request_stats":result["cronjob_observed"],
        "github_event_counts":result["github_observed"],
        "actual_job_target_verified":False,
        "production_changed":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
