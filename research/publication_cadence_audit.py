"""Observe market-data publication recency independent of cron-job.org polls.

GitHub commits/manifest prove publication timing, but cannot identify who
called cron-job.org, whether its target replied, or any successful trade.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import statistics


def _time(value):
    x=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    if x.tzinfo is None:
        raise ValueError("timezone required")
    return x.astimezone(timezone.utc)


def audit(commits,manifest,*,checked_at_utc):
    if not isinstance(commits,list) or not commits or len(commits)>100:
        raise ValueError("requires bounded source commit history")
    if manifest.get("status")!="OK" or manifest.get("file")!="market_data.json":
        raise ValueError("invalid market-data manifest")
    now=_time(checked_at_utc)
    observed=[]
    for c in commits:
        if not isinstance(c,dict):
            raise ValueError("invalid commit")
        message=c.get("commit",{}).get("message","")
        if message.startswith("data: coleta OKX"):
            observed.append(_time(c["commit"]["committer"]["date"]))
    if not observed:
        raise ValueError("no published scanner commits in source history")
    if len(set(observed))!=len(observed):
        raise ValueError("duplicate publication timestamps")
    observed.sort(reverse=True)
    pub=observed[0]
    collected=_time(manifest["collected_utc"])
    if pub>now or collected>now or collected>pub:
        raise ValueError("market-data manifest falsely future-dated")
    delay=[(a-b).total_seconds()/60
           for a,b in zip(observed,observed[1:])]
    return {
        "status":"PUBLIC_MARKET_DATA_PUBLICATION_HEALTH_READ_ONLY",
        "publication_commit_count_in_api_page":len(observed),
        "last_market_data_published_utc":pub.isoformat(),
        "last_market_data_collected_utc":collected.isoformat(),
        "publication_age_minutes_at_check":round((now-pub).total_seconds()/60,3),
        "collected_data_age_minutes_at_check":round((now-collected).total_seconds()/60,3),
        "last_publication_pipeline_lag_seconds":round((pub-collected).total_seconds(),3),
        "median_gap_between_observed_publications_minutes":round(
            statistics.median(delay),3) if delay else None,
        "maximum_gap_between_observed_publications_minutes":round(
            max(delay),3) if delay else None,
        "source_sample_page_limit":100,
        "external_cronjob_org_job_id_known":False,
        "external_one_minute_http_checks_observed":False,
        "external_http_checks_equal_publications":False,
        "github_schedule_event_counts_not_cronjob_success":True,
        "market_data_update_timestamps_proven":True,
        "expected_publication_rate_not_assumed":True,
        "publication_failures_causally_assigned":False,
        "trading_orders_seen":0,
        "production_changed":False,
    }


def main(argv=None):
    import argparse
    from pathlib import Path
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--commits",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--checked-at-utc",required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    r=audit(json.loads(args.commits.read_text()),json.loads(args.manifest.read_text()),
            checked_at_utc=args.checked_at_utc)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(r,indent=2)+"\n")
    print("PUBLICATION_HEALTH",json.dumps(r,sort_keys=True))


if __name__=="__main__":
    main()
