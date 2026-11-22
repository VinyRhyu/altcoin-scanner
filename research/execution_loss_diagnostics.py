"""Stage 1/2: execution-fidelity and loss-driver audit of archived scans.

Use REAL snapshot observation timestamps, then read-only OKX historical 1m
OHLCV retrieved later. Do NOT infer trades or intraminute event ordering.
Loss categories are descriptive hypotheses, not causal explanations.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
import json
import math
from pathlib import Path
import statistics

from research.futures_scenario import Scenario
from research.minute_archive_pilot import run as minute_pilot
from research.minute_execution import (
    MINUTE_MS, first_full_minute, evaluate, _parse,
)
from research.okx_history_backfill import retrieve
from research.point_in_time_candidates import inspect_snapshots
from research.signal_quality_audit import analyze as analyze_signal_quality

HOUR_MS=3_600_000
SC=Scenario()
STRESS_SPREAD_ADDER_PCT=.0359  # fixed additional 3.59 bps, not actual spread
FUNDING_ADVERSE_PER_8H=.0001  # signed per side; simulation only

ISSUES = {
    "MODELLED_STOP":"stop_modelled",
    "MODELLED_TP1":"target_modelled",
    "MODELLED_TIME_EXIT":"time_exit_modelled",
    "NOT_TRIGGERED_DURING_ACTIVE_WINDOW":"trigger_never_armed",
    "NO_COMPLETE_MINUTE_BEFORE_EXPIRY":"late_snapshot_no_full_minute",
    "STOP_BEYOND_APPROX_LIQUIDATION":"approx_liquidation_guard",
    "ENTRY_MINUTE_AMBIGUOUS":"intraminute_order_unknown",
    "EXIT_MINUTE_AMBIGUOUS":"intraminute_order_unknown",
    "GAP_BEYOND_EXACT_TRIGGER":"gap_crossed_exact_trigger",
    "MINUTE_DATA_GAP":"minute_data_gap",
    "NO_MINUTE_DATA":"minute_data_missing",
    "INSUFFICIENT_MINUTE_DATA_FOR_TRIGGER":"minute_data_missing",
    "INSUFFICIENT_MINUTE_DATA_FOR_EXIT":"minute_data_missing",
    "INVALID_OR_UNVERIFIED_MINUTE_DATA":"invalid_history_provenance",
    "INVALID_OR_EXPIRED_OBSERVATION":"invalid_observation",
    "MISSING_SNAPSHOT_SPREAD":"missing_quote_proxy",
}


def key(row):
    return (row["symbol"],row["signal_close_ms"],row["side"],row["method"])


def quantile(values,p):
    if not values:
        return None
    v=sorted(values)
    return round(v[min(len(v)-1,math.ceil(len(v)*p)-1)],6)


def _risk_interiors(candidate,window,outcome):
    """MFE/MAE *partial* excursions over complete minutes BETWEEN entry/exit.

    Excludes entry and exit minute, never assumes a path within any candle.
    These are incomplete path summaries and must not be used to label wins.
    """
    if not outcome.get("modelled_execution"):
        return None
    start=first_full_minute(candidate["first_observed_at_ms"])
    end=candidate["signal_close_ms"]+900_000+SC.max_hold_bars*900_000
    rows=_parse(window["minute_rows"],candidate["symbol"],start,end)
    entry_minute=outcome["entry_minute_ms"]
    exit_minute=outcome["exit_minute_ms"]
    if exit_minute<entry_minute:
        raise ValueError("exit precedes entry")
    interior=[r for r in rows if entry_minute<r[0]<exit_minute]
    entry=float(candidate["entry"])
    risk=abs(entry-float(candidate["stop"]))
    direction=1 if candidate["side"]=="LONG" else -1
    if interior:
        high=max(r[2] for r in interior)
        low=min(r[3] for r in interior)
        mfe=((high-entry) if direction==1 else (entry-low))/risk
        mae=((entry-low) if direction==1 else (high-entry))/risk
        excursions={
            "interior_full_minutes":len(interior),
            "mfe_r_lower_bound_interior_only":round(max(0.,mfe),6),
            "mae_r_lower_bound_interior_only":round(max(0.,mae),6),
        }
    else:
        excursions={
            "interior_full_minutes":0,
            "mfe_r_lower_bound_interior_only":None,
            "mae_r_lower_bound_interior_only":None,
        }
    if outcome["outcome"]=="MODELLED_TIME_EXIT":
        price=outcome["raw_exit_price"]
        tp=float(candidate["tp1"])
        target_dist=abs(tp-entry)
        remaining=(tp-price) if direction==1 else (price-tp)
        excursions["target_fraction_remaining_at_time_exit"]=round(
            remaining/target_dist,6)
    return excursions


def _group(records):
    resolved=[r for r in records if r["base"]["modelled_execution"]]
    losses=[r for r in resolved if r["base"]["net_r_scenario"]<0]
    timeouts=[r for r in resolved if r["base"]["outcome"]=="MODELLED_TIME_EXIT"]
    vals=[r["base"]["net_r_scenario"] for r in resolved]
    stress_vals=[r["stress"]["net_r_scenario"] for r in resolved]
    favorable_time=[r for r in timeouts if r["base"]["net_r_scenario"]>0]
    unfavorable_time=[r for r in timeouts if r["base"]["net_r_scenario"]<0]
    mfer=[r["excursions"]["mfe_r_lower_bound_interior_only"]
          for r in resolved if r["excursions"] and
          r["excursions"]["mfe_r_lower_bound_interior_only"] is not None]
    stopminutes=[r["minutes_after_entry_to_exit"]
                 for r in resolved if r["base"]["outcome"]=="MODELLED_STOP"]
    return {
        "candidates":len(records),
        "outcomes":dict(sorted(Counter(r["base"]["outcome"] for r in records).items())),
        "diagnostic_categories":dict(sorted(Counter(r["issue_category"] for r in records).items())),
        "modelled_exits_not_fills":len(resolved),
        "non_modelled_or_censored":len(records)-len(resolved),
        "modelled_net_losses":len(losses),
        "modelled_net_positive":sum(v>0 for v in vals),
        "mean_net_r_modelled_only":round(statistics.mean(vals),8) if vals else None,
        "mean_stressed_net_r_modelled_only":round(statistics.mean(stress_vals),8) if stress_vals else None,
        "profit_factor_modelled_only":round(sum(v for v in vals if v>0)/
            -sum(v for v in vals if v<0),8) if any(v<0 for v in vals) else None,
        "time_exits_positive":len(favorable_time),
        "time_exits_negative":len(unfavorable_time),
        "time_exits_zero":len(timeouts)-len(favorable_time)-len(unfavorable_time),
        "p50_snapshot_delay_seconds":quantile(
            [r["snapshot_lag_s"] for r in records],.5),
        "p90_snapshot_delay_seconds":quantile(
            [r["snapshot_lag_s"] for r in records],.9),
        "p50_wait_from_snapshot_to_first_full_minute_s":quantile(
            [r["next_full_minute_wait_s"] for r in records],.5),
        "p50_stop_exit_minutes_after_entry":quantile(stopminutes,.5),
        "p50_interior_only_mfe_r":quantile(mfer,.5),
        "number_of_complete_interior_mfe_records":len(mfer),
        "actual_exchange_fills":0,
    }


def diagnose(paths,max_candidates=80,max_pages=6,fetch_history=retrieve):
    if type(max_candidates) is not int or not 1<=max_candidates<=250:
        raise ValueError("invalid candidate budget")
    if type(max_pages) is not int or not 1<=max_pages<=100:
        raise ValueError("invalid page budget")
    report,windows=minute_pilot(paths,max_candidates,max_pages,fetch_history)
    source=inspect_snapshots(paths)
    lookup={key(x):x for x in source["candidates"]
            if x["decision_at_snapshot"]=="GATILHO_CONDICIONAL"}
    if len(lookup)!=report["source_conditional_candidates"]:
        raise ValueError("archived candidate keys are not unique")
    if len(windows)!=len(lookup) or len(report["details"])!=len(windows):
        raise ValueError("execution windows do not match archived candidates")
    details=[]
    for base,window in zip(report["details"],windows):
        k=key(window)
        c=lookup[k]
        for f,source_f in (("observed_at_ms","first_observed_at_ms"),
                           ("signal_close_ms","signal_close_ms")):
            if base[f]!=c[source_f]:
                raise ValueError("candidate snapshot changed during retrieval")
        if any(base[f]!=c[f] for f in ("symbol","side","method")):
            raise ValueError("candidate identity mismatch")
        expiry=c["signal_close_ms"]+900_000
        lag_ms=c["first_observed_at_ms"]-c["signal_close_ms"]
        first=first_full_minute(c["first_observed_at_ms"])
        if lag_ms<0 or lag_ms>=900_000 or first<c["first_observed_at_ms"]:
            raise ValueError("snapshot timing violates causality")
        stressed=dict(c,spread_pct_observed=(
            float(c["spread_pct_observed"])+STRESS_SPREAD_ADDER_PCT))
        stress_sc=replace(SC,slippage_bps_side=10.,
                          assumed_funding_per_8h=(
                              FUNDING_ADVERSE_PER_8H if c["side"]=="LONG"
                              else -FUNDING_ADVERSE_PER_8H))
        stress=evaluate(stressed,window["minute_rows"],stress_sc)
        baseline=dict(base)
        baseline_outcome={fld:baseline[fld] for fld in (
            "outcome","modelled_execution","net_r_scenario")}
        if any(baseline_outcome[f]!=stress[f] for f in (
            "outcome","modelled_execution")):
            raise ValueError("cost-only experiment changed event path or eligibility")
        if (baseline["modelled_execution"] and
            (stress["net_r_scenario"] is None or
             stress["net_r_scenario"]>baseline["net_r_scenario"]+1e-8)):
            raise ValueError("adverse cost stress did not worsen modelled PnL")
        if not baseline["modelled_execution"] and stress["net_r_scenario"] is not None:
            raise ValueError("non-execution was assigned a trade return")
        excursions=_risk_interiors(c,window,baseline)
        timing={
            "snapshot_lag_s":round(lag_ms/1000,3),
            "next_full_minute_wait_s":round((
                first-c["first_observed_at_ms"])/1000,3),
            "minutes_after_snapshot_to_modelled_entry":round((
                baseline["entry_minute_ms"]-c["first_observed_at_ms"])/MINUTE_MS,4)
                if baseline.get("modelled_execution") else None,
            "minutes_after_entry_to_exit":round((
                baseline["exit_minute_ms"]-baseline["entry_minute_ms"])/MINUTE_MS,3)
                if baseline.get("modelled_execution") else None,
            "minutes_of_arming_left_at_first_full_minute":max(
                0,(expiry-first)//MINUTE_MS),
        }
        details.append({
            "symbol":c["symbol"],"side":c["side"],"method":c["method"],
            "signal_close_ms":c["signal_close_ms"],
            "first_observed_at_ms":c["first_observed_at_ms"],
            "first_evaluable_minute_ms":first,
            "snapshot_spread_pct":c["spread_pct_observed"],
            "official_tick_size_from_snapshot":c["tick_size_observed"],
            "risk_pct_from_exact_trigger":round(100*abs(
                c["entry"]-c["stop"])/c["entry"],6),
            "entry":c["entry"],"stop":c["stop"],"tp1":c["tp1"],
            **timing,
            "issue_category":ISSUES.get(base["outcome"],"other_unresolved"),
            "retrieved_1m_rows":len(window["minute_rows"]),
            "base":{field:value for field,value in baseline.items()
                    if field not in ("retrieval","observed_at_ms",
                                     "activation_full_minute_ms",
                                     "trigger_expires_ms",
                                     "symbol","side","method","signal_close_ms")},
            "stress":{field:value for field,value in stress.items()},
            "excursions":excursions,
            "exchange_fill_verified":False,
        })
    by_symbol=defaultdict(list)
    for row in details:
        by_symbol[row["symbol"]].append(row)
    quality=analyze_signal_quality(paths,source,windows,details)
    if quality["conditional_sample"]!=len(details):
        raise ValueError("signal quality sample does not match 1m audit")
    return {
        "status":"AUTHENTIC_SNAPSHOT_POST_OBSERVATION_1M_LOSS_DIAGNOSTICS",
        "source_snapshot_count":report["source_snapshot_count"],
        "source_confirmed_candidates":report["source_confirmed_candidates"],
        "source_conditional_candidates":report["source_conditional_candidates"],
        "retrieved_1m_observations_may_overlap":report["retrieved_minute_rows"],
        "reference_scenario":{"fee_bps_side":SC.fee_bps_side,
            "slippage_bps_side":SC.slippage_bps_side,
            "spread_pct":"observed snapshot proxy, NOT execution spread",
            "funding_per_8h":0.},
        "stressed_scenario":{"fee_bps_side":SC.fee_bps_side,
            "slippage_bps_side":10.,
            "added_spread_pct_over_archived_snapshot":STRESS_SPREAD_ADDER_PCT,
            "adverse_funding_per_8h":FUNDING_ADVERSE_PER_8H},
        "overall":_group(details),
        "by_side":{side:_group([x for x in details if x["side"]==side])
                   for side in ("LONG","SHORT")},
        "by_method":{m:_group([x for x in details if x["method"]==m])
                     for m in ("BREAKOUT","RETEST")},
        "by_symbol":{s:_group(rows) for s,rows in sorted(by_symbol.items())},
        "details":details,
        "signal_quality_and_lag_stress":quality,
        "source_snapshot_metadata_reused_not_reconstructed_fill_prices":True,
        "source_quote_at_actual_execution_available":False,
        "real_exchange_fills_verified":0,
        "net_profitability_proven":False,
        "production_filters_changed":False,
        "warnings":[
            "This is Stage 1/2 diagnosis, not causal proof of why a trade won/lost",
            "Historical 1m OHLCV is retrieved after observation, not exchange order prints",
            "Observed ticker is a snapshot quote, not proof of executable price",
            "No evidence of real bid/ask at simulated entry, depth, historical funding or actual liquidation",
            "Partial minute at scan is excluded; we cannot know any intraminute order of touches",
            "Interior-only MFE and MAE exclude entry and exit minutes and are incomplete diagnostic proxies",
            "Modelled outcomes may be correlated, overlap and be selected by dynamic scan universe",
            "No signal may be approved or filters modified on the basis of these descriptive groups",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--max-candidates",type=int,default=80)
    p.add_argument("--max-pages",type=int,default=6)
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    r=diagnose(args.snapshots,args.max_candidates,args.max_pages)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print("ENTRY_LOSS_DIAGNOSTICS",json.dumps({
        "snapshots":r["source_snapshot_count"],
        "confirmed":r["source_confirmed_candidates"],
        "conditional":r["source_conditional_candidates"],
        "1m_observations":r["retrieved_1m_observations_may_overlap"],
        "overall":r["overall"],
        "by_side":r["by_side"],
        "by_method":r["by_method"],
        "no_real_fills":r["real_exchange_fills_verified"]==0,
    },sort_keys=True))


if __name__=="__main__":
    main()
