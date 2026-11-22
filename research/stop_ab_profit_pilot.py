"""Read-only A/B: original 3-bar stop vs research-only 2-bar stop.

Both variants are re-evaluated at *identical authentic scan snapshots* with
the same market context, tick, spread, signal expiry and risk guards. 1m
historical OHLCV is retrieved later, solely for scenario-based execution.
Never treat modelled prices as exchange fills or proven net profitability.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics

from entry_trigger import MAX_RISK_ATR
from research.futures_scenario import Scenario
from research.minute_execution import evaluate, first_full_minute
from research.okx_history_backfill import retrieve
from research.point_in_time_candidates import inspect_snapshots

ALLOW = "GATILHO_CONDICIONAL"
WINDOW_MS = 900_000
METHODS = ("BREAKOUT","RETEST")
SIDES = ("LONG","SHORT")
VARIANTS = (3, 2)


def key(row):
    return (row["symbol"],row["signal_close_ms"],row["side"],row["method"])


def _mean(values):
    return round(statistics.mean(values),8) if values else None


def _profit_stats(details, risk_dollars=100):
    resolved = [r["net_r_scenario"] for r in details
                if r["modelled_execution"] and r.get("net_r_scenario") is not None]
    good = [v for v in resolved if v > 0]
    bad = [v for v in resolved if v < 0]
    gross_gains = sum(good)
    gross_losses = -sum(bad)
    return {
        "conditionally_eligible":len(details),
        "modelled_exits_not_fills":len(resolved),
        "censored_or_not_triggered":len(details)-len(resolved),
        "outcomes":dict(sorted(Counter(r["outcome"] for r in details).items())),
        "positive_net_r_scenarios":len(good),
        "negative_net_r_scenarios":len(bad),
        "zero_net_r_scenarios":len(resolved)-len(good)-len(bad),
        "modelled_win_rate_pct":round(100*len(good)/len(resolved),3)
            if resolved else None,
        "mean_net_r_modelled_only":_mean(resolved),
        "median_net_r_modelled_only":round(statistics.median(resolved),8)
            if resolved else None,
        "sum_net_r_modelled_only":round(sum(resolved),8),
        "profit_factor_modelled_only":round(gross_gains/gross_losses,8)
            if gross_losses>0 else None,
        "sum_illustrative_usd_at_fixed_100_risk":round(
            risk_dollars*sum(resolved),2),
        "usd_amount_is_account_return":False,
        "real_fills":0,
    }


def _compare_sources(paths):
    orig = inspect_snapshots(paths, research_stop_bars=3)
    two = inspect_snapshots(paths, research_stop_bars=2)
    if (orig["snapshots_analyzed"]!=two["snapshots_analyzed"] or
        orig["research_stop_bars"]!=3 or two["research_stop_bars"]!=2):
        raise ValueError("source snapshots or model parameters not aligned")
    a={key(x):x for x in orig["candidates"]}
    b={key(x):x for x in two["candidates"]}
    if len(a)!=orig["confirmed_candidates"] or len(b)!=two["confirmed_candidates"]:
        raise ValueError("duplicate candidate keys")
    if a.keys()!=b.keys():
        raise ValueError("2/3 candle option changed pattern confirmation universe")
    for k in a:
        for field in ("entry","first_observed_at_ms","signal_close_ms",
                      "spread_pct_observed","tick_size_observed"):
            if a[k][field]!=b[k][field]:
                raise ValueError(f"mismatched snapshot provenance: {k} {field}")
        # A shorter stop can worsen estimated NET R:R (fixed fees/spread),
        # or move a pivot in front of TP1. Accepted three-bar plans may then
        # fail legitimate unchanged risk/strategy gates in the two-bar variant.
        # Same entry means a narrower stop cannot increase price distance.
        if (a[k]["stop"] is not None and b[k]["stop"] is not None and
            ((b[k]["stop"] < a[k]["stop"]-1e-8) if k[2]=="LONG"
             else (b[k]["stop"] > a[k]["stop"]+1e-8))):
            raise ValueError("two-candle stop widened risk unexpectedly")
    return orig,a,b


def _plan_cost_consistent(row):
    """All other gates remain active. No accidental bypass of >3ATR."""
    if row["decision_at_snapshot"]==ALLOW:
        if "stop distante > 3 ATR" in row["reasons"]:
            raise ValueError("risk guard bypassed")
        if row["entry"] is None or row["stop"] is None or row["tp1"] is None:
            raise ValueError("conditional candidate lacks plan")


def inspect_ab(paths):
    """Preflight without contacting OKX: counts and eligible keys."""
    orig,a,b=_compare_sources(paths)
    changed=Counter()
    eligible=[]
    for k in sorted(a):
        x,y=a[k],b[k]
        _plan_cost_consistent(x)
        _plan_cost_consistent(y)
        passed_a=x["decision_at_snapshot"]==ALLOW
        passed_b=y["decision_at_snapshot"]==ALLOW
        transition="both" if passed_a and passed_b else (
            "only_2bar" if passed_b else "only_3bar" if passed_a else "neither")
        changed[transition]+=1
        if passed_a or passed_b:
            eligible.append(k)
    return {"orig":orig,"a":a,"b":b,"eligible":eligible,
            "transition_counts":dict(changed)}


def _group_summary(records):
    output={}
    for bars in VARIANTS:
        chosen=[d[str(bars)] for d in records if d[str(bars)] is not None]
        output[str(bars)] = {
            "all":_profit_stats(chosen),
            "by_side":{side:_profit_stats([
                d[str(bars)] for d in records if d[str(bars)] is not None
                and d["side"]==side]) for side in SIDES},
            "by_method":{method:_profit_stats([
                d[str(bars)] for d in records if d[str(bars)] is not None
                and d["method"]==method]) for method in METHODS},
        }
    both=[d for d in records if d["3"] is not None and d["2"] is not None]
    both_resolved=[d for d in both
                   if d["3"].get("net_r_scenario") is not None
                   and d["2"].get("net_r_scenario") is not None
                   and d["3"]["modelled_execution"]
                   and d["2"]["modelled_execution"]]
    diffs=[d["2"]["net_r_scenario"]-d["3"]["net_r_scenario"]
           for d in both_resolved]
    output["matched_candidate_subset"]={
        "both_eligible":len(both),
        "both_modelled":len(both_resolved),
        "mean_2_minus_3_r_on_both_modelled":_mean(diffs),
        "positive_delta_count":sum(x>0 for x in diffs),
        "negative_delta_count":sum(x<0 for x in diffs),
        "equal_delta_count":sum(x==0 for x in diffs),
        "matched_sample_not_independent_or_inferential":True,
    }
    return output


def run(paths,max_pairs=160,max_pages=5,fetch_history=retrieve):
    if type(max_pairs) is not int or not 1<=max_pairs<=500:
        raise ValueError("invalid A/B API budget")
    if type(max_pages) is not int or not 1<=max_pages<=100:
        raise ValueError("invalid page budget")
    pre=inspect_ab(paths)
    if len(pre["eligible"])>max_pairs:
        raise ValueError(f"A/B candidate budget exceeded: {len(pre['eligible'])}>{max_pairs}")
    sc=Scenario(fee_bps_side=5.,slippage_bps_side=5.,
                assumed_funding_per_8h=0.,leverage=20.,
                max_hold_bars=16)
    details=[]
    rows_fetched=0
    for k in pre["eligible"]:
        a,b=pre["a"][k],pre["b"][k]
        stamp=a["first_observed_at_ms"]
        start=first_full_minute(stamp)
        expiry=a["signal_close_ms"]+WINDOW_MS
        end=expiry+sc.max_hold_bars*WINDOW_MS
        if start>=expiry:
            rows=[]
            meta={"backtest_ready":False,"candles_retrieved":0,"pages_requested":0,
                  "stopped_because":"no_full_minute_before_expiry"}
        else:
            meta,rows=fetch_history(a["symbol"],"1m",start,end,max_pages=max_pages)
            if meta.get("backtest_ready") is not False or meta.get("candles_retrieved")!=len(rows):
                raise ValueError("non-historical or inconsistent OKX 1m source")
            if any(r.get("point_in_time_eligible") is not False for r in rows):
                raise ValueError("unexpected source eligibility")
        rows_fetched+=len(rows)
        result={}
        for bars, candidate in ((3,a),(2,b)):
            result[str(bars)]=(evaluate(candidate,rows,sc)
                               if candidate["decision_at_snapshot"]==ALLOW else None)
        details.append({
            "symbol":k[0],"signal_close_ms":k[1],"side":k[2],"method":k[3],
            "first_observed_at_ms":stamp,"first_evaluable_minute_ms":start,
            "original_stop_3":a["stop"],"research_stop_2":b["stop"],
            "original_tp1_3":a["tp1"],"research_tp1_2":b["tp1"],
            "original_decision_3":a["decision_at_snapshot"],
            "research_decision_2":b["decision_at_snapshot"],
            "original_reasons_3":a["reasons"],"research_reasons_2":b["reasons"],
            "source_tick_size_observed":a["tick_size_observed"],
            "source_spread_pct_observed":a["spread_pct_observed"],
            "retrieved_1m_candles_in_window":len(rows),
            "historical_data_retrieved_after_signal":True,
            "3":result["3"],"2":result["2"],
        })
    group_stats=_group_summary(details)
    sorted_times=sorted({k[1] for k in pre["eligible"]})
    pivot=sorted_times[math.ceil(len(sorted_times)*.7)-1] if sorted_times else None
    splits={}
    for label,subset in (
        ("first_70pct_time_exploratory",
         [d for d in details if pivot is not None and d["signal_close_ms"]<=pivot]),
        ("last_30pct_time_exploratory",
         [d for d in details if pivot is not None and d["signal_close_ms"]>pivot]),
    ):
        splits[label]=_group_summary(subset)
    return {
        "status":"ASSUMPTION_HEAVY_POST_SNAPSHOT_1M_AB_STOP_COMPARISON",
        "snapshots_analyzed":pre["orig"]["snapshots_analyzed"],
        "confirmed_same_signal_universe":pre["orig"]["confirmed_candidates"],
        "eligible_union_checked":len(pre["eligible"]),
        "transition_counts":pre["transition_counts"],
        "retrieved_1m_observations":rows_fetched,
        "cost_scenario":{"fee_bps_side":sc.fee_bps_side,
                         "slippage_bps_side":sc.slippage_bps_side,
                         "spread_pct":"each archived snapshot as proxy",
                         "funding_per_8h_assumed":sc.assumed_funding_per_8h,
                         "max_hold_15m_bars":sc.max_hold_bars,
                         "leverage_nominal":sc.leverage,
                         "illustrative_risk_usd_per_modelled_trade":100},
        "comparison":group_stats,
        "chronological_exploratory_splits":splits,
        "details":details,
        "verified_exchange_fills":0,"actual_profit_proven":False,
        "new_stop_strategy_approved":False,"production_changed":False,
        "warnings":[
            "A/B is in-sample sensitivity analysis, not walk-forward validation or evidence of alpha",
            "2-bar stop may be less structurally protective; changes TP and estimated net RR",
            "Each modelled net R includes assumed fees/slippage, snapshot spread and zero assumed funding",
            "Illustrative USD uses $100 independent planned risk per modelled trade, not account equity or portfolio PnL",
            "Untriggered, ambiguous, incomplete and liquidation-proxy cases are excluded from resolved PnL",
            "Population of modelled executions differs between variants; paired sample is separately shown",
            "1m OHLCV is downloaded after the event and cannot prove fills or intraminute ordering",
            "Signals and executions may overlap across symbols; trades are correlated",
            "Chronological split is exploratory and not an untouched holdout; no parameter selection is authorized",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--report",type=Path,required=True)
    p.add_argument("--max-pairs",type=int,default=160)
    p.add_argument("--max-pages",type=int,default=5)
    p.add_argument("--preflight-only",action="store_true")
    args=p.parse_args(argv)
    if args.preflight_only:
        pre=inspect_ab(args.snapshots)
        summary={
            "status":"NO_EXCHANGE_CALLS",
            "snapshots":pre["orig"]["snapshots_analyzed"],
            "confirmed":pre["orig"]["confirmed_candidates"],
            "eligible_union":len(pre["eligible"]),
            "transitions":pre["transition_counts"],
            "profits_computed":False,
        }
        print("STOP_AB_PREFLIGHT",json.dumps(summary,sort_keys=True))
        report=summary
    else:
        report=run(args.snapshots,args.max_pairs,args.max_pages)
        print("STOP_AB_RESULTS",json.dumps({
            "snapshots":report["snapshots_analyzed"],
            "confirmed":report["confirmed_same_signal_universe"],
            "transitions":report["transition_counts"],
            "1m_rows":report["retrieved_1m_observations"],
            "original":report["comparison"]["3"]["all"],
            "variant":report["comparison"]["2"]["all"],
            "matched":report["comparison"]["matched_candidate_subset"],
            "actual_profit_proven":False,
        },sort_keys=True))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
