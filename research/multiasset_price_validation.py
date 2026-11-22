"""Multi-symbol chronological validation of unchanged 3-bar scanner scenarios.

Predeclared 60-day period: 2026-09-22..2026-11-21 UTC, 30d exploration
followed by 30d temporal evaluation. BTC, ETH, XRP are independent symbols
from SOL pilot but not independent market regimes. All input bars are OKX
public history fetched AFTER the fact. Zero real exchange fills.

The 15m future-bar model is intentionally stricter than the legacy pilot:
if entry and exit may both occur in the first 15m bar, it CENSORS the result,
rather than inventing an intra-bar ordering. Last 4 hours of each period
are excluded to avoid using later-period prices to label earlier decisions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics

from research.futures_scenario import Scenario, _pnl, simulate
from research.market_history_audit import DURATIONS_MS
from research.retrospective_price_pilot import load_history, pilot
from research.okx_history_backfill import utc_ms

SYMBOLS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "XRP-USDT-SWAP")
TICKS = {"BTC-USDT-SWAP": "0.1", "ETH-USDT-SWAP": "0.01",
         "XRP-USDT-SWAP": "0.0001"}
PERIOD_START = utc_ms("2026-09-22T00:00:00Z")
SPLIT = utc_ms("2026-10-22T00:00:00Z")
PERIOD_END = utc_ms("2026-11-21T00:00:00Z")
DAY_MS = 86_400_000
BAR_MS = DURATIONS_MS["15m"]
BASE = Scenario(fee_bps_side=5, slippage_bps_side=5,
                assumed_spread_pct=.0141,
                assumed_funding_per_8h=0,
                leverage=20, max_hold_bars=16)
STRESS = Scenario(fee_bps_side=5, slippage_bps_side=10,
                  assumed_spread_pct=.05,
                  assumed_funding_per_8h=0,
                  leverage=20, max_hold_bars=16)


def conservative_exit(plan, sc):
    """Reject entry/exit ordering that 15m OHLC cannot establish."""
    r = simulate(plan, sc)
    if (r["executed"] and r.get("bars_to_exit") == 1 and
        r["outcome"] in ("TP1_TOUCHED", "STOP_TOUCHED")):
        return {"outcome": "ENTRY_EXIT_SAME_FIRST_15M_AMBIGUOUS",
                "executed": False, "net_r": None,
                "legacy_optimistic_outcome": r["outcome"],
                "reason": "entry and exit touches in same 15m OHLC cannot be ordered"}
    if r["executed"] and r["outcome"] == "STOP_TOUCHED":
        # A stop may gap past its price after entry. Do not grant perfect protection.
        index = r["bars_to_exit"]-1
        o = float(plan["future_15m"][index][1])
        stop = float(plan["stop"])
        direction = 1 if plan["side"] == "LONG" else -1
        worse = min(o,stop) if direction == 1 else max(o,stop)
        if worse != stop:
            exit_ms = int(plan["future_15m"][index][0])+BAR_MS
            values = _pnl(float(plan["entry"]), worse,
                          plan["signal_close_ms"], exit_ms, plan, sc)
            r = {**r, **values, "raw_exit_price": worse,
                 "adverse_stop_gap_modelled": True}
    return r


def _groups(rows):
    vals = [r["net_r"] for r in rows if r["executed"] and r["net_r"] is not None]
    positives=[r for r in vals if r>0]
    negatives=[r for r in vals if r<0]
    by_day = defaultdict(list)
    for r in rows:
        if r["executed"] and r["net_r"] is not None:
            by_day[r["signal_close_ms"]//DAY_MS].append(r["net_r"])
    # Clustered bootstrap resamples signal days, not independent trades;
    # only report CI with enough days and scenarios.
    ci=None
    if len(vals)>=20 and len(by_day)>=10:
        random_gen=random.Random(20261122)
        days=list(sorted(by_day))
        estimates=[]
        for _ in range(1000):
            sampled=[v for _ in days
                     for v in by_day[random_gen.choice(days)]]
            estimates.append(statistics.mean(sampled))
        estimates.sort()
        ci=[round(estimates[24],7),round(estimates[975],7)]
    gross_gains=sum(positives)
    gross_losses=-sum(negatives)
    return {
        "signals_eligible_and_evaluated":len(rows),
        "modelled_exits_not_fills":len(vals),
        "unresolved_or_unfilled":len(rows)-len(vals),
        "outcome_counts":dict(sorted(Counter(r["outcome"] for r in rows).items())),
        "wins_modelled_net":len(positives),
        "losses_modelled_net":len(negatives),
        "win_rate_modelled_pct":round(100*len(positives)/len(vals),3) if vals else None,
        "mean_net_r_modelled_only":round(statistics.mean(vals),8) if vals else None,
        "sum_net_r_modelled_only":round(sum(vals),8),
        "profit_factor_modelled_only":round(gross_gains/gross_losses,8)
            if gross_losses else None,
        "signal_days_with_modelled_exits":len(by_day),
        "mean_net_r_cluster_bootstrap_95pct":ci,
        "ci_is_descriptive_not_proof_of_edge":True,
        "max_sequential_drawdown_r_not_portfolio":_drawdown(vals),
    }


def _drawdown(vals):
    high=equity=peak_to_valley=0.0
    for v in vals:
        equity+=v
        high=max(high,equity)
        peak_to_valley=max(peak_to_valley, high-equity)
    return round(peak_to_valley,7)


def _summarize(rows):
    return {
        "overall":_groups(rows),
        "by_method":{m:_groups([x for x in rows if x["method"]==m])
                     for m in ("BREAKOUT","RETEST")},
        "by_side":{s:_groups([x for x in rows if x["side"]==s])
                   for s in ("LONG","SHORT")},
    }


def _validate_full_history(history):
    for tf,duration in DURATIONS_MS.items():
        rows=history[tf]
        expected=(PERIOD_END-PERIOD_START)//duration
        if len(rows)!=expected:
            raise ValueError(f"incomplete {tf} history: {len(rows)} not {expected}")
        if (rows[0][0]!=PERIOD_START or
            rows[-1][0]+duration!=PERIOD_END):
            raise ValueError(f"incorrect {tf} fixed endpoints")
        if any(b[0]-a[0]!=duration for a,b in zip(rows,rows[1:])):
            raise ValueError(f"gap or duplicate in {tf} history")


def _stress_for_side(side):
    # 1 bp adverse funding per 8h for BOTH sides. Positive funding: longs
    # pay; negative funding: shorts pay. It is a stress proxy, not real funding.
    return replace(STRESS, assumed_funding_per_8h=0.0001
                   if side=="LONG" else -0.0001)


def analyze_asset(history,symbol):
    if symbol not in SYMBOLS:
        raise ValueError("unexpected asset or unregistered research tick proxy")
    _validate_full_history(history)
    original=pilot(history,TICKS[symbol],BASE,symbol=symbol,
                   include_research_plans=True)
    plans=original.pop("research_only_plans_with_future_bars")
    trades=[]
    excluded=Counter()
    for plan in plans:
        ts=plan["signal_close_ms"]
        period=("exploration" if ts<SPLIT else "temporal_evaluation")
        bound=SPLIT if period=="exploration" else PERIOD_END
        if ts<PERIOD_START or ts+BASE.max_hold_bars*BAR_MS>bound:
            excluded[period+"_would_cross_boundary"]+=1
            continue
        if plan["future_15m"][0][0]!=ts:
            raise ValueError("15m outcome starts before or after signal close")
        base=conservative_exit(plan,BASE)
        stress=conservative_exit(plan,_stress_for_side(plan["side"]))
        if (base["outcome"]!=stress["outcome"] or
            bool(base["executed"])!=bool(stress["executed"])):
            raise ValueError("scenario prices are not matched between costs")
        # Strictly no future financial result may certify exchange execution.
        trades.append({
            "symbol":symbol,"method":plan["method"],"side":plan["side"],
            "period":period,"signal_close_ms":ts,
            "entry_proxy":plan["entry"],"stop_proxy":plan["stop"],
            "tp1_proxy":plan["tp1"],
            "base":base,"stress":stress,
            "exchange_fill_verified":False,
        })
    # Exclude all outcomes from the other side of temporal boundary.
    grouped={}
    for period in ("exploration","temporal_evaluation"):
        sub=[x for x in trades if x["period"]==period]
        grouped[period]={
            "base":_summarize([{**x["base"],"signal_close_ms":x["signal_close_ms"],
                                "method":x["method"],"side":x["side"]} for x in sub]),
            "cost_stress":_summarize(
                [{**x["stress"],"signal_close_ms":x["signal_close_ms"],
                  "method":x["method"],"side":x["side"]} for x in sub]),
        }
    return {
        "status":"MULTISYMBOL_RETROSPECTIVE_SCENARIO_NO_REAL_FILLS",
        "symbol":symbol,"tick_proxy_not_exchange_historical_tick":TICKS[symbol],
        "start_ms":PERIOD_START,"split_ms":SPLIT,"end_ms":PERIOD_END,
        "history_counts":{tf:len(history[tf]) for tf in DURATIONS_MS},
        "evaluated_15m_closes":original["evaluated_15m_closes"],
        "signal_detection":original["detection_summary"],
        "plans_created":len(plans),
        "candidates_excluded_at_time_boundaries":dict(excluded),
        "trades_scenarios":trades,
        "periods":grouped,
        "fees_bps_side":BASE.fee_bps_side,
        "base_slippage_bps_side":BASE.slippage_bps_side,
        "stress_slippage_bps_side":STRESS.slippage_bps_side,
        "base_spread_pct_proxy":BASE.assumed_spread_pct,
        "stress_spread_pct_proxy":STRESS.assumed_spread_pct,
        "stress_adverse_funding_per_8h":0.0001,
        "max_holding_15m_bars":BASE.max_hold_bars,
        "net_edge_proven":False,
        "historical_fills_verified":0,
        "limitations":[
            "Multiasset results use later-retrieved 15m/1h/2h candles, NOT authentic archived scan snapshots",
            "Ticker at scan +1min is falsely proxied by candle close: retroactive assumption, not historical quote",
            "Model arms at exact 15m close, earlier than real scan availability; cannot certify entry timing",
            "Entry and exit in same first 15m candle censored, but true intrabar ordering requires ticks",
            "Tick size and spreads are explicitly hypothetical historical proxies",
            "Funding is a signed adverse stress proxy, not historical payments",
            "15m stop fills are hypothetical even after adverse-opening gap adjustments",
            "No book liquidity, mark-price liquidation, actual order fill or portfolio overlapping positions",
            "30d temporal evaluation is predeclared for THIS experiment, but not pristine untouched out of sample",
            "Coin returns share market regimes; clustered events are not independent trades",
        ],
    }


def _assert_consistent_asset(r):
    if r["symbol"] not in SYMBOLS:
        raise ValueError("unexpected asset")
    if (r["start_ms"],r["split_ms"],r["end_ms"])!=(
        PERIOD_START,SPLIT,PERIOD_END):
        raise ValueError("mismatched time window")
    if r["net_edge_proven"] is not False or r["historical_fills_verified"]!=0:
        raise ValueError("invalid factual evidence claims")
    if any(x["exchange_fill_verified"] for x in r["trades_scenarios"]):
        raise ValueError("unexpected claim of exchange fills")
    for x in r["trades_scenarios"]:
        if x["period"]!=("exploration" if x["signal_close_ms"]<SPLIT
                       else "temporal_evaluation"):
            raise ValueError("incorrect chronological partition")
    if sum(r["periods"][p]["base"]["overall"]["signals_eligible_and_evaluated"]
           for p in ("exploration","temporal_evaluation"))!=len(r["trades_scenarios"]):
        raise ValueError("candidate accounting mismatch")


def aggregate(reports):
    if len(reports)!=len(SYMBOLS):
        raise ValueError("all three predeclared assets required")
    keyed={}
    for r in reports:
        _assert_consistent_asset(r)
        if r["symbol"] in keyed:
            raise ValueError("duplicate asset")
        keyed[r["symbol"]]=r
    if set(keyed)!=set(SYMBOLS):
        raise ValueError("missing predeclared asset")
    combined={}
    for period in ("exploration","temporal_evaluation"):
        for cost,detail_key in (("base","base"),("cost_stress","stress")):
            rows=[]
            for symbol in SYMBOLS:
                r=keyed[symbol]
                rows.extend({
                    **trade[detail_key],
                    "signal_close_ms":trade["signal_close_ms"],
                    "method":trade["method"],"side":trade["side"]
                } for trade in r["trades_scenarios"] if trade["period"]==period)
            combined.setdefault(period,{})[cost]=_summarize(
                sorted(rows,key=lambda x:x["signal_close_ms"]))
    return {
        "status":"PREDECLARED_MULTI_ASSET_CHRONOLOGICAL_RESEARCH_ONLY",
        "assets":list(SYMBOLS),"time_start_ms":PERIOD_START,
        "split_ms":SPLIT,"time_end_ms":PERIOD_END,
        "per_asset":{s:{
            "evaluated_15m_closes":keyed[s]["evaluated_15m_closes"],
            "plans_created":keyed[s]["plans_created"],
            "periods":keyed[s]["periods"],
        } for s in SYMBOLS},
        "combined":combined,
        "no_real_fills":True,"net_edge_proven":False,
        "all_parameters_predeclared_in_code":True,
        "nonindependent_coins_and_events":True,
        "real_ticker_at_signal_available":False,
        "limitations":keyed[SYMBOLS[0]]["limitations"],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest="mode",required=True)
    a=subs.add_parser("asset")
    a.add_argument("--directory",type=Path,required=True)
    a.add_argument("--symbol",choices=SYMBOLS,required=True)
    a.add_argument("--output",type=Path,required=True)
    ag=subs.add_parser("aggregate")
    ag.add_argument("--directory",type=Path,required=True)
    ag.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if args.mode=="asset":
        history=load_history(args.directory,symbol=args.symbol,days=60)
        r=analyze_asset(history,args.symbol)
        print("MULTIASSET_ASSET",json.dumps({
            "symbol":r["symbol"],"plans":r["plans_created"],
            "exploration":r["periods"]["exploration"]["base"]["overall"],
            "temporal_evaluation":r["periods"]["temporal_evaluation"]["base"]["overall"],
            "edge_proven":False,
        },sort_keys=True))
    else:
        reports=[json.loads((args.directory/(symbol.split("-")[0].lower()+
                    "_report.json")).read_text(encoding="utf-8"))
                 for symbol in SYMBOLS]
        r=aggregate(reports)
        for period in ("exploration","temporal_evaluation"):
            print("MULTIASSET_PERIOD",json.dumps({
                "period":period,
                "base":r["combined"][period]["base"]["overall"],
                "stress":r["combined"][period]["cost_stress"]["overall"],
                "edge_proven":False,
            },sort_keys=True))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
