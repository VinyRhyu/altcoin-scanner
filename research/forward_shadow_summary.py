"""Aggregate repeat receipt-vs-legacy shadows from distinct real public scans.

At most 10 snapshots. All used the SAME response per A/B candidate and
retained original market filters. Unsuccessful scans are explicit censored
cases, never zero tickers, and no trade returns are inferred.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics


def _ts(iso):
    dt=datetime.fromisoformat(str(iso).replace("Z","+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive scan timestamp")
    return dt.astimezone(timezone.utc)


def aggregate(records):
    if not isinstance(records,list) or not 1<=len(records)<=10:
        raise ValueError("requires 1-10 predeclared scan slots")
    seen=set()
    complete=[]
    failures=[]
    for idx,r in enumerate(records):
        if not isinstance(r,dict) or r.get("status") not in (
            "SAME_PAYLOAD_RECEIPT_CLOCK_SHADOW_ONLY","UNAVAILABLE"):
            raise ValueError("missing or malformed recorded scan slot")
        if r["status"]=="UNAVAILABLE":
            failures.append(idx)
            continue
        if (r.get("any_orders_submitted") is not False or
            r.get("profitability_verified") is not False or
            r.get("master_or_market_data_changed") is not False or
            r.get("baseline_shadow_is_subset_not_independent_full_legacy_collection") is not True):
            raise ValueError("shadow safety flag mismatch")
        t=_ts(r["scan_started_utc"])
        u=_ts(r["ticker_fetch_completed_utc"])
        v=_ts(r["data_ready_utc"])
        if not t<=u<=v:
            raise ValueError("scan data was not available in time")
        if t in seen:
            raise ValueError("duplicated scan snapshot")
        seen.add(t)
        if not (0<=r["old_full_candle_validated_conditional_triggers"]<=
                r["receipt_full_candle_validated_conditional_triggers"]):
            raise ValueError("incompatible signal counts")
        if not (r["receipt_full_candle_validated_conditional_triggers"]-
                r["old_full_candle_validated_conditional_triggers"] ==
                r["new_conditional_triggers_due_to_recovered_tickers"]):
            raise ValueError("incompatible recovered signal accounting")
        if (r["pre_fetch_eligible_tickers"]<0 or
            r["receipt_eligible_tickers"]<0 or
            r["recovered_tickers_at_first_filter"]<0):
            raise ValueError("invalid eligible tickers")
        complete.append(r)
    def total(key):
        return sum(x[key] for x in complete)
    return {
        "status":"MULTIPLE_RECEIPT_CLOCK_SHADOW_SCANS_READ_ONLY",
        "planned_scan_slots":len(records),
        "completed_scans":len(complete),
        "failed_or_censored_slots":len(failures),
        "failed_slot_indices_not_trades":failures,
        "first_scan_started_utc":min((x["scan_started_utc"] for x in complete),
                                     default=None),
        "last_scan_started_utc":max((x["scan_started_utc"] for x in complete),
                                    default=None),
        "pre_fetch_ticker_eligibility_total_nonunique":total("pre_fetch_eligible_tickers"),
        "receipt_ticker_eligibility_total_nonunique":total("receipt_eligible_tickers"),
        "first_gate_recovered_tickers_nonunique":total("recovered_tickers_at_first_filter"),
        "validated_selected_only_due_to_receipt_nonunique":total(
            "validated_selected_only_due_to_receipt"),
        "recovered_not_selected_or_validated_nonunique":total(
            "recovered_tickers_not_selected_or_validated"),
        "old_conditional_triggers_nonunique":total(
            "old_full_candle_validated_conditional_triggers"),
        "receipt_conditional_triggers_nonunique":total(
            "receipt_full_candle_validated_conditional_triggers"),
        "new_conditional_triggers_nonunique":total(
            "new_conditional_triggers_due_to_recovered_tickers"),
        "scans_with_nonzero_ticker_recovery":sum(
            x["recovered_tickers_at_first_filter"]>0 for x in complete),
        "scans_with_nonzero_added_signal":sum(
            x["new_conditional_triggers_due_to_recovered_tickers"]>0
            for x in complete),
        "periods_analysed_are_not_independent":True,
        "data_available_for_net_ev_or_real_fills":False,
        "net_expectancy_proven":False,
        "ready_for_production_approval":False,
        "no_order_submission":True,
        "production_changed":False,
        "notes":[
            "A censored scan is not zero eligible or zero signals",
            "The old baseline is a subset of the receipt OHLCV-validated sample",
            "Counts aggregate repeated snapshots; repeated symbols are not independent trades",
            "No entry/exit prices after signal, actual fees, funding or fill latency were measured",
            "This batch is a diagnostic spot check, not a continuous 1-minute monitor",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report",type=Path,required=True)
    p.add_argument("inputs",nargs="+",type=Path)
    a=p.parse_args(argv)
    records=[json.loads(path.read_text(encoding="utf-8")) for path in a.inputs]
    result=aggregate(records)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("RECEIPT_SHADOW_BATCH",json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
