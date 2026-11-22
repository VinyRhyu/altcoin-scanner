"""Reconstruct stop geometry for confirmed archived signals (offline, read only).

Diagnoses LONG/SHORT asymmetry with the exact unchanged entry/stop formulas.
Does not optimize stops, bypass risk gates, or infer trading profitability.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics

from entry_trigger import (
    MAX_RISK_ATR, is_breakout, is_retest, tick_decimal, tick_round,
)
from experimental_scan import atr14
from research.market_history_audit import _collected_ms, resolve_paths
from research.point_in_time_candidates import inspect_snapshots

RISK_REASON = "stop distante > 3 ATR"


def _quantiles(values):
    if not values:
        return {"n":0, "median":None, "p90":None, "max":None}
    sorted_values=sorted(values)
    return {
        "n":len(values),
        "median":round(statistics.median(sorted_values),6),
        "p90":round(sorted_values[math.ceil(.9*len(values))-1],6),
        "max":round(sorted_values[-1],6),
    }


def stop_components(bars,side,method,tick_size,entry):
    """Compute actual stop and both alternative *components*, never new policy."""
    if side not in ("LONG","SHORT") or method not in ("BREAKOUT","RETEST"):
        raise ValueError("invalid method/side")
    tick=tick_decimal(tick_size)
    if tick is None:
        raise ValueError("no official tick size")
    atr=atr14(bars)
    if method=="BREAKOUT":
        _,level=is_breakout(bars,len(bars)-1,side,atr)
    else:
        _,level=is_retest(bars,side,atr)
    if level is None or not all(math.isfinite(x) for x in (atr,level,entry)):
        raise ValueError("invalid ATR or setup level")
    recent=bars[-3:]
    if side=="LONG":
        a=tick_round(level-.4*atr,tick,"down")
        b=tick_round(min(float(c[3]) for c in recent)-.15*atr,tick,"down")
        predicted_stop=min(a,b)
        level_risk=(entry-a)/atr
        recent_risk=(entry-b)/atr
        dominance="level" if a<b else "recent" if b<a else "tie"
        risk_atr=(entry-predicted_stop)/atr
    else:
        a=tick_round(level+.4*atr,tick,"up")
        b=tick_round(max(float(c[2]) for c in recent)+.15*atr,tick,"up")
        predicted_stop=max(a,b)
        level_risk=(a-entry)/atr
        recent_risk=(b-entry)/atr
        dominance="level" if a>b else "recent" if b>a else "tie"
        risk_atr=(predicted_stop-entry)/atr
    return {
        "atr":atr,"level":level,"level_component_stop":a,
        "recent_component_stop":b,"predicted_stop":predicted_stop,
        "level_component_risk_atr":level_risk,
        "recent_component_risk_atr":recent_risk,
        "risk_atr":risk_atr,"dominant_stop_anchor":dominance,
    }


def audit(paths):
    source=inspect_snapshots(paths)
    selected={
        (r["symbol"],r["signal_close_ms"],r["side"],r["method"]):r
        for r in source["candidates"]
    }
    observed=set()
    rows=[]
    unavailable=Counter()
    for path in resolve_paths(paths):
        snap=json.loads(path.read_text(encoding="utf-8"))
        if snap.get("status")!="COLLECTED" or snap.get("market_type")!="USDT_PERPETUAL":
            continue
        stamp=_collected_ms(snap.get("collected_utc"))
        histories=snap.get("candles",{})
        for ticker in snap.get("universe",[]):
            symbol=ticker.get("symbol")
            history=histories.get(symbol)
            if not isinstance(history,dict) or not history.get("15m"):
                continue
            bars=history["15m"]
            signal_close=int(bars[-1][0])+900000
            for side in ("LONG","SHORT"):
                for method in ("BREAKOUT","RETEST"):
                    key=(symbol,signal_close,side,method)
                    candidate=selected.get(key)
                    if candidate is None or candidate["first_observed_at_ms"]!=stamp:
                        continue
                    if key in observed:
                        continue
                    observed.add(key)
                    if (candidate["entry"] is None or candidate["stop"] is None or
                        ticker.get("tick_size") is None):
                        unavailable["missing_tick_or_plan"]+=1
                        continue
                    geometry=stop_components(
                        bars,side,method,ticker["tick_size"],float(candidate["entry"]))
                    tick=tick_decimal(ticker["tick_size"])
                    tol=float(tick)*1e-6+1e-12
                    if abs(geometry["predicted_stop"]-float(candidate["stop"]))>tol:
                        raise ValueError(f"stop reconstruction mismatch: {key}")
                    above=geometry["risk_atr"]>MAX_RISK_ATR
                    was_blocked=RISK_REASON in candidate["reasons"]
                    if above!=was_blocked:
                        raise ValueError(f"risk filter mismatch: {key}")
                    a=geometry["level_component_risk_atr"]>MAX_RISK_ATR
                    b=geometry["recent_component_risk_atr"]>MAX_RISK_ATR
                    explanation=("both_over_3atr" if a and b else
                        "level_only_over_3atr" if a else
                        "recent_only_over_3atr" if b else
                        "neither_over_3atr")
                    rows.append({
                        "symbol":symbol,"side":side,"method":method,
                        "signal_close_ms":signal_close,"first_observed_at_ms":stamp,
                        "atr":round(geometry["atr"],12),
                        "entry":candidate["entry"],"stop":candidate["stop"],
                        "level_stop":geometry["level_component_stop"],
                        "recent_stop":geometry["recent_component_stop"],
                        "risk_atr":round(geometry["risk_atr"],8),
                        "level_risk_atr":round(
                            geometry["level_component_risk_atr"],8),
                        "recent_risk_atr":round(
                            geometry["recent_component_risk_atr"],8),
                        "stop_above_3atr":above,
                        "dominant_anchor":geometry["dominant_stop_anchor"],
                        "threshold_driver":explanation,
                        "production_trade_executed":False,
                    })
    if len(observed)!=len(selected):
        raise ValueError("cannot reconcile candidate keys with source snapshots")
    if len(rows)+sum(unavailable.values())!=len(selected):
        raise AssertionError("incomplete stop accounting")

    def group_stats(group):
        blocked=[r for r in group if r["stop_above_3atr"]]
        return {
            "confirmed_with_valid_plan":len(group),
            "over_3atr":len(blocked),
            "over_3atr_rate_pct":round(
                len(blocked)*100/len(group),3) if group else None,
            "risk_atr_distribution":_quantiles([r["risk_atr"] for r in group]),
            "blocked_risk_atr_distribution":_quantiles(
                [r["risk_atr"] for r in blocked]),
            "dominant_anchor_all":dict(Counter(
                r["dominant_anchor"] for r in group)),
            "dominant_anchor_over_3atr":dict(Counter(
                r["dominant_anchor"] for r in blocked)),
            "threshold_driver_over_3atr":dict(Counter(
                r["threshold_driver"] for r in blocked)),
            "distinct_symbols":len({r["symbol"] for r in group}),
        }
    by_side={side:group_stats([r for r in rows if r["side"]==side])
             for side in ("LONG","SHORT")}
    by_method_side={
        method:{side:group_stats([
            r for r in rows if r["method"]==method and r["side"]==side])
                for side in ("LONG","SHORT")}
        for method in ("BREAKOUT","RETEST")
    }
    return {
        "status":"DESCRIPTIVE_STOP_GEOMETRY_ONLY",
        "snapshot_count":source["snapshots_analyzed"],
        "confirmed_candidates":source["confirmed_candidates"],
        "plan_reconstruction_rows":len(rows),
        "unavailable":dict(unavailable),
        "by_side":by_side,
        "by_method_side":by_method_side,
        "blocked_by_symbol":dict(Counter(
            r["symbol"] for r in rows if r["stop_above_3atr"])),
        "rows":rows,
        "stop_formula_changed":False,
        "risk_limit_changed":False,
        "proven_expectancy":False,
        "warnings":[
            "3 ATR is measured from exact trigger to selected worst of two structural stop anchors",
            "Overlapping signals across dynamic universe and periods are not independent",
            "The sample's LONG/SHORT asymmetry need not be a formula defect",
            "Observed price trends and volatility regimes may be confounded with side",
            "An alternative stop formula would need independent risk validation; no stop was altered",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots",nargs="+")
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    result=audit(args.snapshots)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("STOP_GEOMETRY",json.dumps({
        "confirmed":result["confirmed_candidates"],
        "unavailable":result["unavailable"],
        "by_side":result["by_side"],
        "by_method_side":result["by_method_side"],
        "risk_limit_changed":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
