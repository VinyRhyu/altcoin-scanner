"""Pre-registered multi-symbol, 3x30-day historical scenario comparison.

Fixed date boundaries, tick proxies and costs. Signals are historical-only
retrospective reconstructions, not authentic fills or point-in-time order quotes.
The last block is NOT an untouched forward out-of-sample validation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import statistics

from research.futures_scenario import Scenario
from research.retrospective_price_pilot import load_history, pilot

DAY_MS=86_400_000
BLOCK_MS=30*DAY_MS
DAYS=90
SYMBOLS=("BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP")
# Explicit research price increment proxies, NEVER asserted as official exchange tickSz.
TICKS={"BTC-USDT-SWAP":"0.1","ETH-USDT-SWAP":"0.01","SOL-USDT-SWAP":"0.01"}
BASE_SPREAD_PCT=0.025
STRESS_SPREAD_PCT=0.08
BASE_FEE_BPS=5.
BASE_SLIPPAGE_BPS=5.
STRESS_SLIPPAGE_BPS=12.5
MIN_RESOLVED_FOR_TENTATIVE_RELIABILITY=100


def _utc_ms(s):
    parsed=datetime.fromisoformat(str(s).replace("Z","+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds()!=0:
        raise ValueError("strict UTC start/end required")
    return int(parsed.timestamp()*1000)


def _stats(rows):
    resolved=[r for r in rows if r["executed"] and r["net_r"] is not None]
    net=[r["net_r"] for r in resolved]
    positive=[x for x in net if x>0]
    negative=[x for x in net if x<0]
    total_gain=sum(positive)
    total_loss=-sum(negative)
    # A sum in R is NOT a portfolio equity curve. Positions may overlap.
    return {
        "conditional_plans":len(rows),
        "modelled_resolutions_not_fills":len(resolved),
        "non_resolved_or_non_triggered":len(rows)-len(resolved),
        "outcomes":dict(sorted(Counter(r["outcome"] for r in rows).items())),
        "net_r_scenario_mean_resolved":round(statistics.mean(net),8) if net else None,
        "net_r_scenario_median_resolved":round(statistics.median(net),8) if net else None,
        "net_r_scenario_sum_non_portfolio":round(sum(net),8),
        "win_rate_pct_resolved":round(100*len(positive)/len(net),3) if net else None,
        "profit_factor_scenario":round(total_gain/total_loss,8)
            if total_loss>0 else None,
        "worst_resolved_net_r":min(net) if net else None,
        "best_resolved_net_r":max(net) if net else None,
        "resolved_sample_below_100":len(net)<MIN_RESOLVED_FOR_TENTATIVE_RELIABILITY,
        "exchange_fills_verified":0,
    }


def _pair_costs(base, stress):
    a={(r["symbol"],r["method"],r["side"],r["signal_close_ms"]):r for r in base}
    b={(r["symbol"],r["method"],r["side"],r["signal_close_ms"]):r for r in stress}
    if len(a)!=len(base) or len(b)!=len(stress) or a.keys()!=b.keys():
        raise ValueError("stressed cost replay does not match baseline plan keys")
    for k in a:
        if a[k]["outcome"]!=b[k]["outcome"] or a[k]["executed"]!=b[k]["executed"]:
            raise ValueError("changing only transaction costs unexpectedly changed exit ordering")
    matched=[(a[k],b[k]) for k in a if a[k]["executed"]]
    differences=[y["net_r"]-x["net_r"] for x,y in matched]
    if any(d>1e-7 for d in differences):
        raise ValueError("cost stress unexpectedly improved net R")
    return {
        "matched_resolved":len(matched),
        "mean_stress_minus_base_r":round(statistics.mean(differences),8)
            if differences else None,
        "stress_improved_any_net_r":any(d>1e-7 for d in differences),
    }


def multi_asset_report(directory,start_utc,end_utc,days=DAYS):
    if days!=90:
        raise ValueError("fixed pre-registered 90-day window only")
    start,end=_utc_ms(start_utc),_utc_ms(end_utc)
    if end-start!=days*DAY_MS or start%DAY_MS or end%DAY_MS:
        raise ValueError("expected exactly 90 complete UTC days")
    ends=[start+BLOCK_MS*(i+1) for i in range(3)]
    if ends[-1]!=end:
        raise ValueError("invalid 3-block split")
    base_scenario=Scenario(fee_bps_side=BASE_FEE_BPS,
                           slippage_bps_side=BASE_SLIPPAGE_BPS,
                           assumed_spread_pct=BASE_SPREAD_PCT,
                           assumed_funding_per_8h=0)
    stress_scenario=Scenario(fee_bps_side=BASE_FEE_BPS,
                             slippage_bps_side=STRESS_SLIPPAGE_BPS,
                             assumed_spread_pct=STRESS_SPREAD_PCT,
                             assumed_funding_per_8h=0)

    by_asset={}
    all_trades=[]
    all_stress=[]
    for symbol in SYMBOLS:
        history=load_history(directory,symbol=symbol,days=days)
        for tf,duration in (("15m",900000),("1h",3600000),("2h",7200000)):
            rows=history[tf]
            expected=(end-start)//duration
            if (len(rows)!=expected or rows[0][0]!=start or
                rows[-1][0]+duration!=end):
                raise ValueError(f"nonaligned/incomplete {tf} history for {symbol}")
        report=pilot(history,TICKS[symbol],base_scenario,symbol=symbol,
                     stress_scenario=stress_scenario)
        base=report["execution_scenario"]["trades"]
        stressed=report["cost_stress_scenario"]["trades"]
        paired=_pair_costs(base,stressed)
        by_asset[symbol]={
            "tick_size_proxy_not_official":TICKS[symbol],
            "closed_15m":report["closed_15m_bars"],
            "evaluated_15m_closes":report["evaluated_15m_closes"],
            "detection_summary":report["detection_summary"],
            "baseline":_stats(base),
            "cost_stress":_stats(stressed),
            "matched_cost_sensitivity":paired,
        }
        all_trades.extend(base)
        all_stress.extend(stressed)

    by_block={}
    for i in range(3):
        lo=start+i*BLOCK_MS
        hi=ends[i]
        candidates=[r for r in all_trades if lo<=r["signal_close_ms"]<hi]
        stressed=[r for r in all_stress if lo<=r["signal_close_ms"]<hi]
        by_block[f"block_{i+1}_days_{i*30+1}_to_{(i+1)*30}"]={
            "window_start_utc":datetime.fromtimestamp(lo/1000,timezone.utc).isoformat(),
            "window_end_utc_exclusive":datetime.fromtimestamp(hi/1000,timezone.utc).isoformat(),
            "baseline":_stats(candidates),
            "cost_stress":_stats(stressed),
            "matched_cost_sensitivity":_pair_costs(candidates,stressed),
            "by_symbol":{s:_stats([r for r in candidates if r["symbol"]==s])
                         for s in SYMBOLS},
        }
    by_method={
        m: {"baseline":_stats([r for r in all_trades if r["method"]==m]),
            "cost_stress":_stats([r for r in all_stress if r["method"]==m])}
        for m in ("BREAKOUT","RETEST")
    }
    by_side={
        side:{"baseline":_stats([r for r in all_trades if r["side"]==side]),
              "cost_stress":_stats([r for r in all_stress if r["side"]==side])}
        for side in ("LONG","SHORT")
    }
    base_all=_stats(all_trades)
    stressed_all=_stats(all_stress)
    selected_counts=[a["baseline"]["modelled_resolutions_not_fills"]
                     for a in by_asset.values()]
    qualifies_for_sample_size=all(x>=MIN_RESOLVED_FOR_TENTATIVE_RELIABILITY
                                  for x in selected_counts)
    positive_all=(base_all["net_r_scenario_mean_resolved"] is not None
                  and base_all["net_r_scenario_mean_resolved"]>0
                  and stressed_all["net_r_scenario_mean_resolved"] is not None
                  and stressed_all["net_r_scenario_mean_resolved"]>0)
    return {
        "status":"MULTI_ASSET_RETROSPECTIVE_PRICE_SCENARIOS_ONLY",
        "period_start_utc":start_utc, "period_end_utc_exclusive":end_utc,
        "days":days,"symbols":list(SYMBOLS),
        "assumptions":{
            "ticker_price_proxy":"last completed 15m candle close at scan+1minute",
            "trigger_activation_proxy":"exact confirmation close (optimistic)",
            "tick_size":"assumed static per asset, NOT authenticated historical tick",
            "base_fee_bps_per_side":BASE_FEE_BPS,
            "base_slippage_bps_per_side":BASE_SLIPPAGE_BPS,
            "base_spread_pct":BASE_SPREAD_PCT,
            "stress_fee_bps_per_side":BASE_FEE_BPS,
            "stress_slippage_bps_per_side":STRESS_SLIPPAGE_BPS,
            "stress_spread_pct":STRESS_SPREAD_PCT,
            "funding_per_8h_proxy":0,
        },
        "aggregated":{
            "baseline":base_all, "cost_stress":stressed_all,
            "matched_cost_sensitivity":_pair_costs(all_trades,all_stress),
        },
        "by_symbol":by_asset,"by_30day_block":by_block,
        "by_method":by_method,"by_side":by_side,
        "per_candidate_base":[{k:r[k] for k in (
            "symbol","method","side","signal_close_ms","outcome",
            "executed","net_r")} for r in all_trades],
        "minimum_100_resolutions_per_asset_reached":qualifies_for_sample_size,
        "net_positive_under_both_proxy_cost_sets":positive_all,
        "proven_positive_expectancy":False,
        "portfolio_pnl_verified":False,"historical_real_fills_verified":0,
        "production_changes":False,
        "warnings":[
            "All three assets have correlated market regimes; independent symbols are not independent trade samples",
            "Periods and instruments chosen before this run; however, historical period blocks are NOT untouched forward validation",
            "Historical OHLCV retrieved after the fact is NOT authentic archived scanner decisions",
            "Scanner's hypothetical ticker at close+1minute is NOT an authentic bid/ask quote",
            "Entry is assumed armed immediately at confirmation close, BEFORE actual scan latency; lookahead-to-execution remains",
            "15m OHLCV cannot identify entry/exit sequencing in same candle; ambiguous cases are excluded",
            "Static spread, tick, slippage, zero funding and 20x leverage may differ materially from real executions",
            "No orders, actual fills, mark-price liquidation or order book; no real account equity curve",
            "Scenario profit factors and mean R on resolved subset ignore censored and untriggered outcomes",
            "Costs stress applies to identical already-selected signal plans rather than reselecting signals",
            "Overlapping candidates and symbol-time clustering limit inference; positive samples alone prove no edge",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory",type=Path,required=True)
    p.add_argument("--start",required=True)
    p.add_argument("--end",required=True)
    p.add_argument("--days",type=int,default=90)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    r=multi_asset_report(args.directory,args.start,args.end,args.days)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print("MULTI_ASSET_90D_VALIDATION",json.dumps({
        "period":[r["period_start_utc"],r["period_end_utc_exclusive"]],
        "symbols":r["symbols"],"all":r["aggregated"],
        "by_symbol":{k:{"baseline":v["baseline"],"cost_stress":v["cost_stress"]}
                     for k,v in r["by_symbol"].items()},
        "blocks":{k:{"baseline":v["baseline"],"cost_stress":v["cost_stress"]}
                  for k,v in r["by_30day_block"].items()},
        "qualified_sample":r["minimum_100_resolutions_per_asset_reached"],
        "expectancy_proven":r["proven_positive_expectancy"],
    },sort_keys=True))


if __name__=="__main__":
    main()
