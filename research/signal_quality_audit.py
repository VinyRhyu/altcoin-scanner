"""Pre-signal quality, scan-latency and outcome associations (read-only).

Extracts features strictly from the exact earliest archived scanner snapshot
that generated each candidate. Replays only LATER (never earlier) hypothetical
activation for the same fixed plan using already-downloaded after-snapshot 1m
candles. Output is descriptive; no thresholds, model fit, fills or trading.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import statistics

from experimental_scan import features
from research.futures_scenario import Scenario
from research.market_history_audit import _collected_ms, resolve_paths, DURATIONS_MS
from research.minute_execution import evaluate, first_full_minute, MINUTE_MS

DELAY_MINUTES=(0,1,3)
FEATURES=(
    "relative_volume_15m",
    "candle_body_fraction",
    "directional_close_location",
    "directional_last_body_atr",
    "directional_three_candle_momentum_atr",
    "candle_range_atr",
    "atr_pct_of_close",
    "risk_atr",
    "snapshot_remaining_trigger_atr",
    "snapshot_directional_move_since_close_atr",
    "spread_pct_snapshot",
)
LAG_BUCKETS=("under_3m","3_to_under_6m","6_to_under_10m","10_to_under_15m")


def key(row):
    return row["symbol"],row["signal_close_ms"],row["side"],row["method"]


def _safe(v):
    if not math.isfinite(v):
        raise ValueError("non-finite feature from archived candles")
    return round(v,8)


def _relationship(trend, side):
    aligned="alta" if side=="LONG" else "baixa"
    opposite="baixa" if side=="LONG" else "alta"
    return "aligned" if trend==aligned else (
        "opposed" if trend==opposite else "mixed_or_undefined")


def extract_features(history, ticker, candidate, collected_ms):
    """Read only candles already fully CLOSED at archived observation time."""
    if collected_ms!=candidate["first_observed_at_ms"]:
        raise ValueError("feature snapshot timestamp mismatch")
    if candidate["decision_at_snapshot"] not in ("GATILHO_CONDICIONAL","NAO_ENTRAR"):
        raise ValueError("unexpected candidate decision")
    for tf,duration in DURATIONS_MS.items():
        bars=history.get(tf,[])
        if len(bars)<35 or type(bars[-1][0]) is not int:
            raise ValueError("missing closed timeframe")
        if bars[-1][0]+duration>collected_ms:
            raise ValueError("future/in-progress multi-timeframe candle")
        if any(type(x[0]) is not int or x[0]+duration>collected_ms for x in bars):
            raise ValueError("non-closed historical candle in snapshot")
    m=history["15m"]
    if m[-1][0]+DURATIONS_MS["15m"]!=candidate["signal_close_ms"]:
        raise ValueError("candidate confirmation candle does not match snapshot")
    info=features(history)
    last=m[-1]
    op,hi,lo,close=(float(x) for x in last[1:5])
    atr=info["atr_15m"]
    side=candidate["side"]
    direction=1 if side=="LONG" else -1
    if side not in ("LONG","SHORT") or hi<=lo or atr<=0:
        raise ValueError("invalid direction or candle range")
    if candidate["entry"] is None or candidate["stop"] is None:
        raise ValueError("no valid candidate price plan")
    price=float(ticker.get("price",close))
    entry=float(candidate["entry"])
    stop=float(candidate["stop"])
    spread=float(candidate["spread_pct_observed"])
    if not (0<price and 0<entry and 0<stop and spread>=0):
        raise ValueError("invalid quote or candidate cost")
    pos=(close-lo)/(hi-lo)
    direction_position=pos if direction==1 else 1-pos
    volume_ratio=info["relative_volume_15m"]
    price_source=("ticker_snapshot" if ticker.get("price") is not None
                  else "closed_candle_fallback")
    vals={
        "relative_volume_15m":volume_ratio,
        "candle_body_fraction":abs(close-op)/(hi-lo),
        "directional_close_location":direction_position,
        "directional_last_body_atr":direction*(close-op)/atr,
        "directional_three_candle_momentum_atr":
            direction*(close-float(m[-4][4]))/atr,
        "candle_range_atr":(hi-lo)/atr,
        "atr_pct_of_close":100*atr/close,
        "risk_atr":abs(entry-stop)/atr,
        "snapshot_remaining_trigger_atr":direction*(entry-price)/atr,
        "snapshot_directional_move_since_close_atr":
            direction*(price-close)/atr,
        "spread_pct_snapshot":spread,
    }
    relations={
        "trend_1h_relation":_relationship(info["trend_1h"],side),
        "trend_2h_relation":_relationship(info["trend_2h"],side),
        "structure_1h_relation":_relationship(info["structure_1h"],side),
    }
    return {
        "numeric":{k:_safe(v) for k,v in vals.items()},
        "context":relations,
        "snapshot_ticker_price_source":price_source,
        "feature_observed_at_ms":collected_ms,
        "feature_uses_future_minute_candles":False,
    }


def extract_first_snapshot_features(paths, candidates):
    """Fail closed on mismatch; never attach features from a later re-scan."""
    selected={key(x):x for x in candidates}
    if len(selected)!=len(candidates):
        raise ValueError("duplicate unique candidate keys")
    needed=defaultdict(list)
    for k,x in selected.items():
        needed[x["first_observed_at_ms"]].append(k)
    found={}
    for path in resolve_paths(paths):
        snapshot=json.loads(path.read_text(encoding="utf-8"))
        if snapshot.get("status")!="COLLECTED" or snapshot.get("market_type")!="USDT_PERPETUAL":
            continue
        stamp=_collected_ms(snapshot.get("collected_utc"))
        if stamp not in needed:
            continue
        universe={t["symbol"]:t for t in snapshot.get("universe",[])
                  if isinstance(t,dict) and t.get("symbol")}
        candle_sets=snapshot.get("candles",{})
        for k in needed[stamp]:
            symbol=k[0]
            if symbol not in universe or symbol not in candle_sets:
                continue
            extracted=extract_features(
                candle_sets[symbol],universe[symbol],selected[k],stamp)
            if k in found and found[k]!=extracted:
                raise ValueError("conflicting same-time archived signal features")
            found[k]=extracted
    if set(found)!=set(selected):
        missing=sorted(set(selected)-set(found))
        raise ValueError(f"cannot reconcile earliest archived snapshots: {missing[:3]}")
    return found


def _median(vals):
    return round(statistics.median(vals),6) if vals else None


def summarize_group(records):
    numeric={}
    for name in FEATURES:
        vals=[r["features"]["numeric"][name] for r in records]
        numeric[name]={
            "n":len(vals),"median":_median(vals),
            "min":round(min(vals),6) if vals else None,
            "max":round(max(vals),6) if vals else None,
        }
    context={}
    for name in ("trend_1h_relation","trend_2h_relation","structure_1h_relation"):
        context[name]=dict(sorted(Counter(
            r["features"]["context"][name] for r in records).items()))
    resolved=[r for r in records if r.get("outcome",{}).get("modelled_execution")]
    return {
        "n":len(records),
        "modelled_exits_not_fills":len(resolved),
        "median_snapshot_lag_seconds":_median([
            r["lag_seconds"] for r in records if "lag_seconds" in r]),
        "median_net_r_resolved_only":_median([
            r["outcome"]["net_r_scenario"] for r in resolved]),
        "features":numeric,
        "context":context,
    }


def _lag_label(seconds):
    if not 0<=seconds<900:
        raise ValueError("lag outside 15m confirmation window")
    if seconds<180: return LAG_BUCKETS[0]
    if seconds<360: return LAG_BUCKETS[1]
    if seconds<600: return LAG_BUCKETS[2]
    return LAG_BUCKETS[3]


def _latency_report(records):
    buckets={}
    for label in LAG_BUCKETS:
        subset=[r for r in records if r["latency_group"]==label]
        buckets[label]={
            "candidates":len(subset),
            "modelled_exits_not_fills":sum(
                bool(r["outcome"]["modelled_execution"]) for r in subset),
            "outcomes":dict(sorted(Counter(
                r["outcome"]["outcome"] for r in subset).items())),
            "mean_net_r_resolved_only":(
                round(statistics.mean([
                    r["outcome"]["net_r_scenario"] for r in subset
                    if r["outcome"]["modelled_execution"]]),8)
                if any(r["outcome"]["modelled_execution"] for r in subset)
                else None),
            "median_arming_minutes_left":_median([
                r["arming_minutes_left"] for r in subset]),
        }
    return buckets


def _delay_report(records):
    result={}
    for d in DELAY_MINUTES:
        scenarios=[r["hypothetical_extra_delay"][str(d)] for r in records]
        resolved=[s["net_r_scenario"] for s in scenarios
                  if s["modelled_execution"]]
        result[str(d)]={
            "extra_minutes_after_first_full_minute":d,
            "candidate_count":len(scenarios),
            "modelled_exits_not_fills":len(resolved),
            "censored_or_no_trigger":len(scenarios)-len(resolved),
            "outcomes":dict(sorted(Counter(s["outcome"] for s in scenarios).items())),
            "mean_net_r_modelled_only":(
                round(statistics.mean(resolved),8) if resolved else None),
            "sum_net_r_nonportfolio":round(sum(resolved),8),
            "real_fills":0,
        }
    return result


def analyze(paths, source, windows, diagnosed):
    """All outcomes evaluated from the same fetched 1m rows, no extra API calls."""
    all_candidates=source["candidates"]
    features_by_key=extract_first_snapshot_features(paths,all_candidates)
    conditional={key(x):x for x in all_candidates
                 if x["decision_at_snapshot"]=="GATILHO_CONDICIONAL"}
    diag_by_key={key(x):x for x in diagnosed}
    window_by_key={key(x):x for x in windows}
    if len(diag_by_key)!=len(diagnosed) or len(window_by_key)!=len(windows):
        raise ValueError("duplicate diagnosed candidate")
    if set(conditional)!=set(window_by_key) or set(conditional)!=set(diag_by_key):
        raise ValueError("missing conditional result, cannot analyze quality")
    rows=[]
    for k in sorted(conditional):
        c=conditional[k]
        diag=diag_by_key[k]
        window=window_by_key[k]
        if diag["first_observed_at_ms"]!=c["first_observed_at_ms"]:
            raise ValueError("outcome timestamp is not the feature timestamp")
        stamp=c["first_observed_at_ms"]
        full=first_full_minute(stamp)
        lag=(stamp-c["signal_close_ms"])/1000.
        base=diag["base"]
        hypothetical={}
        for delay in DELAY_MINUTES:
            # Metadata shift is explicitly hypothetical, NEVER a re-scan or a
            # live quote update. All eligible minutes remain AFTER original stamp.
            later=full+delay*MINUTE_MS
            if later<stamp:
                raise ValueError("counterfactual activation precedes scan")
            rec=dict(c,first_observed_at_ms=later)
            new=evaluate(rec,window["minute_rows"],Scenario())
            if delay==0 and (new["outcome"]!=base["outcome"] or
                new["net_r_scenario"]!=base["net_r_scenario"] or
                new["modelled_execution"]!=base["modelled_execution"]):
                raise ValueError("zero-delay replay disagrees with base execution")
            hypothetical[str(delay)]={
                "outcome":new["outcome"],
                "net_r_scenario":new["net_r_scenario"],
                "modelled_execution":new["modelled_execution"],
                "verified_fill":False,
            }
        rows.append({
            "symbol":k[0],"signal_close_ms":k[1],"side":k[2],
            "method":k[3],"first_observed_at_ms":stamp,
            "feature_time_ms":features_by_key[k]["feature_observed_at_ms"],
            "lag_seconds":round(lag,3),
            "latency_group":_lag_label(lag),
            "arming_minutes_left":max(0,(
                c["signal_close_ms"]+900_000-full)//MINUTE_MS),
            "features":features_by_key[k],
            "outcome":{
                "outcome":base["outcome"],
                "net_r_scenario":base["net_r_scenario"],
                "modelled_execution":base["modelled_execution"]},
            "hypothetical_extra_delay":hypothetical,
        })
    by_outcome={}
    for outcome in sorted(set(r["outcome"]["outcome"] for r in rows)):
        by_outcome[outcome]=summarize_group([
            r for r in rows if r["outcome"]["outcome"]==outcome])
    winners=[r for r in rows if r["outcome"]["modelled_execution"]
             and r["outcome"]["net_r_scenario"]>0]
    losers=[r for r in rows if r["outcome"]["modelled_execution"]
            and r["outcome"]["net_r_scenario"]<0]
    all_features=[{"features":features_by_key[key(c)]} for c in all_candidates]
    accepted_features=[{"features":features_by_key[key(c)]}
        for c in all_candidates if c["decision_at_snapshot"]=="GATILHO_CONDICIONAL"]
    blocked_features=[{"features":features_by_key[key(c)]}
        for c in all_candidates if c["decision_at_snapshot"]!="GATILHO_CONDICIONAL"]
    return {
        "status":"PRE_SIGNAL_DESCRIPTIVE_FEATURE_ASSOCIATION_AND_DELAY_STRESS",
        "confirmed_technical_sample":len(all_candidates),
        "conditional_sample":len(rows),
        "blocked_technical_sample":len(blocked_features),
        "all_confirmed_features":summarize_group(all_features),
        "accepted_vs_blocked_at_snapshot":{
            "accepted":summarize_group(accepted_features),
            "blocked":summarize_group(blocked_features),
        },
        "by_outcome":by_outcome,
        "resolved_net_positive":summarize_group(winners),
        "resolved_net_negative":summarize_group(losers),
        "by_side":{s:summarize_group([r for r in rows if r["side"]==s])
                   for s in ("LONG","SHORT")},
        "by_method":{m:summarize_group([r for r in rows if r["method"]==m])
                     for m in ("BREAKOUT","RETEST")},
        "observation_lag_buckets":_latency_report(rows),
        "extra_latency_fixed_plan_sensitivity":_delay_report(rows),
        "features_declared_before_examining_result":list(FEATURES),
        "hypothetical_extra_delays_minutes":list(DELAY_MINUTES),
        "features_extracted_from_exact_earliest_snapshot":True,
        "post_snapshot_1m_used_only_for_labels_and_delay_replays":True,
        "new_price_at_delayed_observation_known":False,
        "signal_revalidation_at_delayed_activation_performed":False,
        "real_exchange_fills_verified":0,
        "predictive_threshold_recommended":False,
        "profitability_or_causality_proven":False,
        "production_changed":False,
        "rows":rows,
        "limitations":[
            "Small number of modelled exits, many censored/untriggered candidates",
            "Momentum/volume are correlated with pattern admission, selection bias expected",
            "All indicators use already closed snapshot candles, NOT 1m after the signal",
            "Volume ratio compares quote volume of confirmation bar to median previous 20",
            "Latency groups are descriptive, not randomized; later scans see different markets",
            "Delay sensitivity freezes the original plan and spread; no updated quote, market depth or live revalidation",
            "Delay scenarios cannot establish whether early entry was really executable",
            "Different symbol, regime, trend and side composition confound feature contrasts",
            "No p-values/optimization: do not promote best-looking thresholds based on these rows",
        ],
    }
