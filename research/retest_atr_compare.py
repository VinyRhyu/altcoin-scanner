"""Compare the original RETEST detection to prior-close-ATR correction.

Both versions use only each archived snapshot's OHLCV and ticker inputs.
The legacy detector is reproduced here for counterfactual comparison only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from unittest.mock import patch

import entry_trigger
from research.point_in_time_candidates import inspect_snapshots


def legacy_is_retest(bars, side, atr):
    """Exact original pre-fix logic: breakout at t-1 checked with ATR at t."""
    prev_passed, level = entry_trigger.is_breakout(
        bars, len(bars)-2, side, atr)
    if not prev_passed:
        return False, level
    last = bars[-1]
    o, hi, lo, close = map(float, last[1:5])
    vol = entry_trigger.relative_volume(bars, len(bars)-1)
    if side == "LONG":
        touched = lo <= level + .3*atr
        reclaimed = close > level+.05*atr and close >= o
    else:
        touched = hi >= level - .3*atr
        reclaimed = close < level-.05*atr and close <= o
    return bool(touched and reclaimed and entry_trigger.quality(last, side, .20)
                and vol >= .65), level


def _key(row):
    return (row["symbol"],row["signal_close_ms"],row["side"],row["method"])


def compare(paths):
    # Restore original implementation immediately, even on evaluation errors.
    with patch.object(entry_trigger, "is_retest", legacy_is_retest):
        legacy=inspect_snapshots(paths)
    fixed=inspect_snapshots(paths)
    if legacy["snapshots_analyzed"] != fixed["snapshots_analyzed"]:
        raise ValueError("snapshot sets changed between comparisons")
    before={_key(row):row for row in legacy["candidates"]}
    after={_key(row):row for row in fixed["candidates"]}
    old_breakouts={k:v for k,v in before.items() if k[3]=="BREAKOUT"}
    new_breakouts={k:v for k,v in after.items() if k[3]=="BREAKOUT"}
    if old_breakouts != new_breakouts:
        raise ValueError("BREAKOUT changed unexpectedly during RETEST correction")
    old_retests={k:v for k,v in before.items() if k[3]=="RETEST"}
    new_retests={k:v for k,v in after.items() if k[3]=="RETEST"}
    added=set(new_retests)-set(old_retests)
    dropped=set(old_retests)-set(new_retests)
    shared=set(old_retests)&set(new_retests)
    changed_decision=[
        k for k in shared
        if old_retests[k]["decision_at_snapshot"]!=new_retests[k]["decision_at_snapshot"]
    ]

    def counts(rows):
        c=Counter()
        for row in rows:
            c["confirmed"]+=1
            c["conditional" if row["decision_at_snapshot"]=="GATILHO_CONDICIONAL"
              else "blocked"]+=1
            c["long" if row["side"]=="LONG" else "short"]+=1
        return dict(c)

    def format_keys(keys, dataset):
        return [{"symbol":key[0],"signal_close_ms":key[1],"side":key[2],
                 "method":key[3],
                 "decision_at_snapshot":dataset[key]["decision_at_snapshot"],
                 "historical_synthetic_fill_verified":False}
                for key in sorted(keys)]

    return {
        "status":"OFFLINE_RETEST_ATR_TEMPORAL_CORRECTION_AUDIT",
        "source_snapshot_count":fixed["snapshots_analyzed"],
        "legacy_all_confirmed":legacy["confirmed_candidates"],
        "fixed_all_confirmed":fixed["confirmed_candidates"],
        "breakout_unchanged_count":len(old_breakouts),
        "retest_legacy":counts(old_retests.values()),
        "retest_fixed":counts(new_retests.values()),
        "retest_newly_confirmed_count":len(added),
        "retest_no_longer_confirmed_count":len(dropped),
        "shared_retest_decision_changes":len(changed_decision),
        "newly_confirmed":format_keys(added,new_retests),
        "no_longer_confirmed":format_keys(dropped,old_retests),
        "changed_decision":format_keys(changed_decision,new_retests),
        "atr_causality_fixed":True,
        "production_data_modified":False,
        "exchange_fills_verified":0,
        "profitability_proven":False,
        "warnings":[
            "This comparison changes confirmation timing logic, not trade execution evidence",
            "All comparisons are on archived snapshots, not profit or hit-rate outcomes",
            "Original decisions were re-evaluated offline, not taken from historical order logs",
            "Historical snapshot universe may be dynamically selected",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    result=compare(args.snapshots)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("RETEST_ATR_COMPARISON",json.dumps({
        key:result[key] for key in (
            "source_snapshot_count","legacy_all_confirmed",
            "fixed_all_confirmed","breakout_unchanged_count",
            "retest_legacy","retest_fixed","retest_newly_confirmed_count",
            "retest_no_longer_confirmed_count","shared_retest_decision_changes",
            "profitability_proven")
    },sort_keys=True))


if __name__=="__main__":
    main()
