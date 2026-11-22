"""Shadow comparison: baseline conditional scans vs sole-strategy-filter blocks.

Only hypothetical model checks using archived snapshot timestamps and OKX 1m
OHLCV retrieved later. It does NOT disable production filters or trade.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from research.filter_ablation import (
    STRATEGY_FILTERS, analyze_report,
)
from research.futures_scenario import Scenario
from research.minute_execution import evaluate, first_full_minute
from research.okx_history_backfill import retrieve
from research.point_in_time_candidates import inspect_snapshots


def shadow_compare(paths, max_candidates=40, max_pages=6, fetch_history=retrieve):
    if type(max_candidates) is not int or max_candidates < 1:
        raise ValueError("invalid candidate budget")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("invalid historical API page budget")
    observed = inspect_snapshots(paths)
    audit = analyze_report(observed)
    baseline = [dict(x, source_decision="GATILHO_CONDICIONAL",
                     shadow_group="baseline", counterfactual_only=False)
                for x in observed["candidates"]
                if x["decision_at_snapshot"]=="GATILHO_CONDICIONAL"]
    counterfactual = []
    for filter_name in STRATEGY_FILTERS:
        for row in audit["counterfactual_exclusive_candidates"].get(filter_name, []):
            # This is a HYPOTHETICAL plan. Never claim that it was actionable.
            candidate = dict(row, decision_at_snapshot="GATILHO_CONDICIONAL",
                             source_decision="NAO_ENTRAR",
                             shadow_group=filter_name,
                             counterfactual_only=True)
            counterfactual.append(candidate)
    selected=baseline+counterfactual
    if len(selected)>max_candidates:
        raise ValueError(f"candidate budget exceeded: {len(selected)} > {max_candidates}")
    scenario=Scenario()
    details=[]
    for c in selected:
        stamp=c["first_observed_at_ms"]
        start=first_full_minute(stamp)
        expiry=c["signal_close_ms"]+900_000
        end=expiry+scenario.max_hold_bars*900_000
        if start>=expiry:
            meta,rows={"pages_requested":0,"candles_retrieved":0,
                       "backtest_ready":False},[]
        else:
            meta,rows=fetch_history(c["symbol"],"1m",start,end,
                                    max_pages=max_pages)
            if meta.get("backtest_ready") is not False or meta.get("candles_retrieved")!=len(rows):
                raise ValueError("inconsistent historical-only API evidence")
        outcome=evaluate(c,rows,scenario)
        details.append({
            "shadow_group":c["shadow_group"],
            "counterfactual_only":c["counterfactual_only"],
            "original_scan_decision":c["source_decision"],
            "symbol":c["symbol"],"side":c["side"],"method":c["method"],
            "signal_close_ms":c["signal_close_ms"],
            "first_observed_at_ms":stamp,
            "first_evaluable_minute_ms":start,
            "retrieved_minute_rows":len(rows),
            "pages_requested":meta.get("pages_requested",0),
            **outcome,
        })
    groups=defaultdict(list)
    for r in details:
        groups[r["shadow_group"]].append(r)
    def stats(rows):
        return {
            "candidates":len(rows),
            "modelled_exits_not_fills":sum(bool(x["modelled_execution"]) for x in rows),
            "outcomes":dict(sorted(Counter(x["outcome"] for x in rows).items())),
            "modelled_pnl_sample_size":sum(x["net_r_scenario"] is not None for x in rows),
            "actual_exchange_fills_verified":0,
        }
    summarized={"baseline":stats(groups["baseline"])}
    for filter_name in STRATEGY_FILTERS:
        summarized[filter_name]=stats(groups[filter_name])
    return {
        "status":"HYPOTHETICAL_SINGLE_FILTER_SHADOW_COMPARISON",
        "source_snapshot_count":observed["snapshots_analyzed"],
        "archived_confirmed":audit["baseline"]["confirmed"],
        "archived_conditional":audit["baseline"]["conditional_baseline"],
        "baseline_checked":len(baseline),
        "counterfactual_checked":len(counterfactual),
        "shadow_groups":summarized,
        "retrieved_minute_observations":sum(x["retrieved_minute_rows"] for x in details),
        "details":details,
        "safety_guards_modified":False,
        "production_filters_modified":False,
        "actual_exchange_fills_verified":0,
        "proven_expectancy":False,
        "warnings":[
            "Removing a lone recorded strategy blocker is a hypothetical comparison, not an order recommendation",
            "Controls are not randomized; populations, assets and volatility regimes differ",
            "1m OHLC path cannot certify trade ordering inside each minute",
            "Historical spreads, funding, slippage, book depth and mark price are unavailable",
            "Some retrospective prices are not proven point-in-time trade prices",
            "No success-rate or profitability conclusions from this non-random small sample",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--report",type=Path,required=True)
    p.add_argument("--max-candidates",type=int,default=40)
    p.add_argument("--max-pages",type=int,default=6)
    args=p.parse_args(argv)
    r=shadow_compare(args.snapshots,args.max_candidates,args.max_pages)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print("FILTER_SHADOW_1M",json.dumps({
        "snapshots":r["source_snapshot_count"],
        "confirmed":r["archived_confirmed"],
        "baseline":r["baseline_checked"],
        "counterfactual":r["counterfactual_checked"],
        "observations_1m":r["retrieved_minute_observations"],
        "groups":r["shadow_groups"],
        "proven_expectancy":r["proven_expectancy"],
    },sort_keys=True))


if __name__=="__main__":
    main()
