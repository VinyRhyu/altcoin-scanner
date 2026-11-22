"""Quantify bulk collection timestamps vs 15m candle close and cron target.

Read-only 150 historical market-data snapshots. GitHub scheduled workflow is
cron 1,16,31,46 UTC (nominal +60s after candle close). The collected_utc time
is a DATA CAPTURE timestamp, NOT job start, exchange ticker arrival, publication
completion, alert delivery or a trade timestamp. Do not assign a root cause.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics

from research.market_history_audit import _collected_ms, resolve_paths

BAR_MS=900000
NOMINAL_AFTER_CLOSE_S=60
BUCKETS=("under_3m","3_to_under_6m","6_to_under_10m","10_to_under_15m")


def _percentile(values,p):
    if not values:
        return None
    xs=sorted(values)
    return round(xs[max(0,math.ceil(len(xs)*p)-1)],3)


def _bucket(lag):
    if not 0<=lag<900:
        raise ValueError("out-of-range collection lag")
    if lag<180:return BUCKETS[0]
    if lag<360:return BUCKETS[1]
    if lag<600:return BUCKETS[2]
    return BUCKETS[3]


def audit(paths):
    rows=[]
    excluded=Counter()
    for path in resolve_paths(paths):
        data=json.loads(path.read_text(encoding="utf-8"))
        if data.get("status")!="COLLECTED" or data.get("market_type")!="USDT_PERPETUAL":
            excluded["invalid_status_or_market"]+=1
            continue
        stamp=_collected_ms(data.get("collected_utc"))
        collection=[]
        for ticker in data.get("universe",[]):
            symbol=ticker.get("symbol") if isinstance(ticker,dict) else None
            bars=data.get("candles",{}).get(symbol,{}).get("15m",[])
            if not bars:
                continue
            last=bars[-1]
            if not isinstance(last,list) or type(last[0]) is not int or last[0]%BAR_MS:
                raise ValueError("invalid 15m last candle open timestamp")
            close=last[0]+BAR_MS
            collection.append(close)
        if not collection:
            excluded["no_15m_candles"]+=1
            continue
        dominant,n=Counter(collection).most_common(1)[0]
        delta_s=(stamp-dominant)/1000
        if not 0<=delta_s<900:
            excluded["stale_or_future_dominant_15m"]+=1
            continue
        rows.append({
            "collected_at_ms":stamp,
            "dominant_last_closed_15m_ms":dominant,
            "candidate_15m_streams":len(collection),
            "streams_same_last_close":n,
            "streams_different_close":len(collection)-n,
            "observed_lag_seconds":round(delta_s,3),
            "excess_vs_cron_plus_60s_seconds":round(delta_s-NOMINAL_AFTER_CLOSE_S,3),
            "lag_bucket":_bucket(delta_s),
            "source_artifact_not_delivery_timestamp":True,
        })
    lags=[r["observed_lag_seconds"] for r in rows]
    return {
        "status":"ARCHIVED_COLLECTION_TIMESTAMP_VS_NOMINAL_CRON",
        "evaluated_archived_collections":len(rows),
        "excluded_snapshot_counts":dict(sorted(excluded.items())),
        "cron_nominal_minute_after_close":1,
        "nominal_target_lag_seconds":NOMINAL_AFTER_CLOSE_S,
        "p10_collected_lag_seconds":_percentile(lags,.1),
        "median_collected_lag_seconds":round(statistics.median(lags),3) if lags else None,
        "p90_collected_lag_seconds":_percentile(lags,.9),
        "p95_collected_lag_seconds":_percentile(lags,.95),
        "median_excess_vs_nominal_schedule_seconds":(
            round(statistics.median(lags)-NOMINAL_AFTER_CLOSE_S,3) if lags else None),
        "collections_at_or_before_nominal_60s":sum(x<=60 for x in lags),
        "collections_more_than_3m_after_close":sum(x>=180 for x in lags),
        "collections_more_than_10m_after_close":sum(x>=600 for x in lags),
        "lag_buckets":dict(sorted(Counter(r["lag_bucket"] for r in rows).items())),
        "collections_with_divergent_15m_close":sum(
            r["streams_different_close"]>0 for r in rows),
        "rows":rows,
        "root_cause_of_delay_verified":False,
        "delivery_latency_measured":False,
        "order_or_execution_time_measured":False,
        "production_changed":False,
        "limitations":[
            "cron schedule targets 1 minute after UTC 15m close, but Actions can delay jobs",
            "collected_utc is bulk snapshot capture time, not Actions job start",
            "cannot separate scheduler queue, setup, API work or quote processing",
            "the post-collection workflow still needs tests, view generation and market-data publication",
            "earlier or later availability of a particular individual ticker is not established",
            "only valid snapshots with dominant 15m close within a 15m window contribute",
            "all values are observational, not evidence of trading latency or fill quality",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    report=audit(args.snapshots)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("COLLECTION_SCHEDULE_AUDIT",json.dumps({
        key:report[key] for key in (
            "evaluated_archived_collections","excluded_snapshot_counts",
            "median_collected_lag_seconds","p90_collected_lag_seconds",
            "median_excess_vs_nominal_schedule_seconds",
            "collections_more_than_3m_after_close",
            "collections_more_than_10m_after_close",
            "lag_buckets","delivery_latency_measured",
            "root_cause_of_delay_verified",
        )
    },sort_keys=True))


if __name__=="__main__":
    main()
