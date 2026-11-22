"""Stage 2: breakdown of the previously evaluated 60d BTC/ETH/XRP price scenarios.

Consumes the exact per-symbol research reports, not trades or exchange logs.
No candidate selection, parameter optimization or new execution assumptions.
Groups describe WHICH model outcomes lost R, not WHY they causally occurred.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics

from research.multiasset_price_validation import SYMBOLS, PERIOD_START, SPLIT, PERIOD_END

PERIODS=("exploration","temporal_evaluation")
OUTCOMES=("STOP_TOUCHED","TIME_EXIT","TP1_TOUCHED")


def _stats(nums):
    return {
        "count":len(nums),
        "mean":round(statistics.mean(nums),8) if nums else None,
        "sum":round(sum(nums),8),
        "median":round(statistics.median(nums),8) if nums else None,
    }


def analyze_rows(trades):
    resolved=[]
    excluded=Counter()
    for t in trades:
        if t.get("exchange_fill_verified") is not False:
            raise ValueError("exchange execution evidence misrepresented")
        base=t["base"]
        stressed=t["stress"]
        if (base["outcome"]!=stressed["outcome"] or
            base["executed"]!=stressed["executed"]):
            raise ValueError("scenarios have different price outcomes")
        if not base["executed"]:
            if base.get("net_r") is not None or stressed.get("net_r") is not None:
                raise ValueError("censored scenario must have no PnL")
            excluded[base["outcome"]]+=1
            continue
        if (base["outcome"] not in OUTCOMES or
            base.get("net_r") is None or stressed.get("net_r") is None):
            raise ValueError("unknown resolved scenario")
        original=float(base["net_r"])
        stress=float(stressed["net_r"])
        if not math.isfinite(original) or not math.isfinite(stress):
            raise ValueError("nonfinite hypothetical return")
        if stress > original+1e-8:
            raise ValueError("adverse cost scenario unexpectedly improved PnL")
        entry=float(t["entry_proxy"])
        tp=float(t["tp1_proxy"])
        stop=float(t["stop_proxy"])
        raw_exit=float(base["raw_exit_price"])
        if not min(entry,stop,tp,raw_exit)>0:
            raise ValueError("invalid hypothetical price")
        direction=1 if t["side"]=="LONG" else -1
        if not (stop<entry<tp if direction==1 else tp<entry<stop):
            raise ValueError("wrong trade direction")
        gain_at_exit=direction*(raw_exit-entry)
        planned_target=abs(tp-entry)
        remains=(direction*(tp-raw_exit))/planned_target
        resolved.append({
            "symbol":t["symbol"],
            "method":t["method"],"side":t["side"],
            "signal_close_ms":t["signal_close_ms"],
            "exit_category":base["outcome"],
            "modelled_net_r":original,"cost_stressed_net_r":stress,
            "extra_assumed_cost_penalty_r":round(original-stress,8),
            "raw_exit_relative_to_entry_r":round(
                gain_at_exit/abs(entry-stop),8),
            "fraction_of_planned_tp_left_at_exit":round(remains,8),
            "bars_to_exit":base["bars_to_exit"],
            "hypothetical_exit_price":raw_exit,
            "verified_exchange_fill":False,
        })
    def grouped(xs):
        by_exit={}
        for kind in OUTCOMES:
            sub=[r for r in xs if r["exit_category"]==kind]
            by_exit[kind]={
                "resolved":len(sub),
                "net_r":_stats([r["modelled_net_r"] for r in sub]),
                "stressed_net_r":_stats([r["cost_stressed_net_r"] for r in sub]),
                "positives":sum(r["modelled_net_r"]>0 for r in sub),
                "negatives":sum(r["modelled_net_r"]<0 for r in sub),
                "mean_bars_to_exit":_stats([r["bars_to_exit"] for r in sub])["mean"],
            }
            if kind=="TIME_EXIT":
                by_exit[kind]["remaining_tp_fraction_at_close"]=_stats(
                    [r["fraction_of_planned_tp_left_at_exit"] for r in sub])
                by_exit[kind]["mean_exit_price_move_r_before_costs"]=_stats(
                    [r["raw_exit_relative_to_entry_r"] for r in sub])["mean"]
        return {
            "conditional_candidates":len(xs),
            "modelled_exits":len(xs),
            "modelled_net_r":_stats([r["modelled_net_r"] for r in xs]),
            "cost_stressed_net_r":_stats([r["cost_stressed_net_r"] for r in xs]),
            "extra_cost_penalty_r":_stats([
                r["extra_assumed_cost_penalty_r"] for r in xs]),
            "positive_modelled_net_r":sum(x["modelled_net_r"]>0 for x in xs),
            "negative_modelled_net_r":sum(x["modelled_net_r"]<0 for x in xs),
            "positive_becoming_negative_under_stress":sum(
                x["modelled_net_r"]>0 and x["cost_stressed_net_r"]<0 for x in xs),
            "by_exit":by_exit,
        }
    by_symbol={symbol:grouped([
        x for x in resolved if x["symbol"]==symbol]) for symbol in SYMBOLS}
    by_method={m:grouped([x for x in resolved if x["method"]==m])
               for m in ("BREAKOUT","RETEST")}
    by_side={s:grouped([x for x in resolved if x["side"]==s])
             for s in ("LONG","SHORT")}
    return {
        "candidates_total":len(trades),
        "modelled_exits":len(resolved),
        "nonexecutions_or_ambiguous":len(trades)-len(resolved),
        "unresolved_outcomes":dict(sorted(excluded.items())),
        "overall":grouped(resolved),
        "by_symbol":by_symbol,
        "by_method":by_method,
        "by_side":by_side,
        "per_modelled_exit":resolved,
        "actual_verified_exchange_fills":0,
    }


def diagnose(reports):
    if len(reports)!=len(SYMBOLS):
        raise ValueError("requires exactly three preselected assets")
    by_sym={}
    for r in reports:
        s=r["symbol"]
        if s not in SYMBOLS or s in by_sym:
            raise ValueError("unexpected/duplicate asset")
        if (r["start_ms"],r["split_ms"],r["end_ms"])!=(
            PERIOD_START,SPLIT,PERIOD_END):
            raise ValueError("noncomparable time window")
        if r["net_edge_proven"] is not False or r["historical_fills_verified"]!=0:
            raise ValueError("invalid provenance")
        if not all(t["symbol"]==s for t in r["trades_scenarios"]):
            raise ValueError("mislabeled trades")
        by_sym[s]=r
    if set(by_sym)!=set(SYMBOLS):
        raise ValueError("incomplete asset set")
    periods={}
    for p in PERIODS:
        selected=sorted((
            t for s in SYMBOLS for t in by_sym[s]["trades_scenarios"]
            if t["period"]==p),key=lambda x:(x["signal_close_ms"],x["symbol"]))
        for t in selected:
            ts=t["signal_close_ms"]
            if (p=="exploration")!=(ts<SPLIT):
                raise ValueError("period boundary leak")
        summary=analyze_rows(selected)
        previous_count=sum(by_sym[s]["periods"][p]["base"]["overall"]
                           ["modelled_exits_not_fills"] for s in SYMBOLS)
        if previous_count!=summary["modelled_exits"]:
            raise ValueError("loss breakdown does not reconcile previous returns")
        periods[p]=summary
    return {
        "status":"DESCRIPTIVE_15M_PRICE_SCENARIO_LOSS_ATTRIBUTION",
        "selected_symbols":list(SYMBOLS),
        "period_start_ms":PERIOD_START,"temporal_split_ms":SPLIT,
        "period_end_ms":PERIOD_END,
        "exploratory_and_temporal_evaluation":periods,
        "original_policy_modified":False,
        "actual_verified_exchange_fills":0,
        "causal_loss_attribution_proven":False,
        "profitable_edge_proven":False,
        "warnings":[
            "Grouping exit outcomes shows financial contribution but not the causal source of losses",
            "This 15m price-only pilot used hypothetical historical ticker/spread and immediately armed entries",
            "Stops/targets/time exits are not verified fills or account PnL",
            "Time exit close proximity to target is not proof that the target was actionable",
            "Adverse cost stress uses assumed spreads/slippage/funding, not actual exchange settlement",
            "Signals are correlated; no optimization on the evaluation period is justified",
            "The separate authentic-snapshot 1m diagnostic tests timing fidelity",
        ],
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    reports=[]
    for s in SYMBOLS:
        path=args.reports_dir/(s.split("-")[0].lower()+"_report.json")
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    result=diagnose(reports)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    for p in PERIODS:
        r=result["exploratory_and_temporal_evaluation"][p]
        print("LOSS_ATTRIBUTION_15M",json.dumps({
            "period":p,"candidates":r["candidates_total"],
            "modelled_exits":r["modelled_exits"],
            "outcomes":r["unresolved_outcomes"],
            "by_exit":r["overall"]["by_exit"],
            "costs":r["overall"]["extra_cost_penalty_r"],
            "causal_proof":False,
        },sort_keys=True))


if __name__=="__main__":
    main()
