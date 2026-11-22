"""Validate real research collector *availability* chronology, never trade fills.

This is a fail-closed gate before interpreting signal counts. Ticker values
obtained after scan-start must not be attributed to that earlier timestamp.
The real scanner and market-data branch remain untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import collector


def _ms(utc_text):
    if not isinstance(utc_text,str):
        raise ValueError("missing ISO timezone-aware time")
    d=datetime.fromisoformat(utc_text.replace("Z","+00:00"))
    if d.tzinfo is None:
        raise ValueError("naive clock is not valid")
    return round(d.astimezone(timezone.utc).timestamp()*1000)


def verify(market,decision):
    if market.get("status")!="COLLECTED" or market.get("market_type")!="USDT_PERPETUAL":
        raise ValueError("incomplete or unexpected market")
    if market.get("signals_allowed") is not False:
        raise ValueError("research market cannot authorize signals/orders")
    if decision.get("order_allowed") is not False:
        raise ValueError("decision must disable orders")
    p=market.get("research_clock_provenance")
    if not isinstance(p,dict) or p.get("ticker_clock_mode")!="receipt_research_only":
        raise ValueError("not a research post-fetch source")
    names=("scan_started_utc","ticker_fetch_started_utc",
           "ticker_fetch_completed_utc","decision_data_ready_utc")
    times=[_ms(p[n]) for n in names]
    scan,started,received,ready=times
    if not scan<=started<=received<=ready:
        raise ValueError("time provenance is not causal")
    if _ms(market["collected_utc"])!=ready:
        raise ValueError("market misleadingly dated before data was ready")
    if p.get("collected_at_means_data_ready") is not True:
        raise ValueError("no declared data-ready timestamp")
    if p.get("maximum_quote_age_seconds")!=300:
        raise ValueError("research cannot relax five-minute freshness")
    if p.get("no_order_execution") is not True:
        raise ValueError("missing explicit order guard")
    if _ms(decision["source_collected_utc"])!=ready:
        raise ValueError("decision source does not match research snapshot")
    decided=_ms(decision["evaluated_at_utc"])
    if decided<ready:
        raise ValueError("decision performed before data was available")
    if p.get("legacy_eligible_tickers",0)<0 or p.get("receipt_eligible_tickers",0)<0:
        raise ValueError("negative eligible counts")
    old=set(p["shadow_removed_symbols"])
    added=set(p["shadow_added_symbols"])
    if old & added:
        raise ValueError("impossible shadow overlap")
    if len(added)>p["receipt_eligible_tickers"]:
        raise ValueError("inconsistent ticker counts")
    if market["universe"] is None or market["candles"] is None:
        raise ValueError("missing market features")
    total_quote_age=[]
    selected_added=0
    all_selected=set()
    for ticker in market["universe"]:
        name=ticker["symbol"]
        if name in all_selected:
            raise ValueError("duplicate market symbol")
        all_selected.add(name)
        selected_added+=name in added
        q=_ms(ticker["ticker_time_utc"])
        if not q<=received<=ready:
            raise ValueError("selected quote was not available at receipt")
        age=ready-q
        if not 0<=age<=300000:
            raise ValueError("selected quote is stale at decision availability")
        total_quote_age.append(round(age/1000,3))
        hist=market["candles"].get(name)
        if not isinstance(hist,dict):
            raise ValueError("missing archived candle source")
        for tf in ("15m","1h","2h"):
            bars=hist.get(tf)
            if not bars or not isinstance(bars,list):
                raise ValueError("missing closed timeframe")
            for bar in bars:
                close=int(bar[0])+collector.INTERVALS[tf]*1000
                if close>scan:
                    raise ValueError("candle only closed AFTER scan start")
                if close>decided:
                    raise ValueError("future candle relative to decision")
    if p["receipt_eligible_tickers"] < len(all_selected):
        raise ValueError("selected more tickers than eligible")
    if decision.get("source_asset_count")!=len(all_selected):
        raise ValueError("decision count differs from source")
    if decision.get("retained_asset_count")!=len(all_selected):
        raise ValueError("research decision unexpectedly excluded source assets")
    return {
        "status":"RECEIPT_CLOCK_SHADOW_CAUSALITY_VERIFIED",
        "pre_fetch_eligible_tickers":p["legacy_eligible_tickers"],
        "post_fetch_eligible_tickers":p["receipt_eligible_tickers"],
        "ticker_symbols_regained":len(added),
        "ticker_symbols_removed_due_to_age_or_clock":len(old),
        "selected_symbols_using_regained_tick":selected_added,
        "selected_ohlcv_complete_symbols":len(all_selected),
        "capture_start_utc":p["scan_started_utc"],
        "ticker_fetch_start_utc":p["ticker_fetch_started_utc"],
        "ticker_fetch_complete_utc":p["ticker_fetch_completed_utc"],
        "snapshot_available_utc":p["decision_data_ready_utc"],
        "decision_completed_at_utc":decision["evaluated_at_utc"],
        "decision_available_after_all_used_data":True,
        "quotes_not_future_relative_to_receipt":True,
        "no_candle_closed_after_scan_start":True,
        "quote_ages_seconds_at_data_ready":total_quote_age,
        "no_personal_trade_read":True,
        "real_exchange_fills_verified":0,
        "profitability_proven":False,
        "production_unchanged":True,
        "limitations":[
            "Only compares ticker eligibility using same response; full competing scanner universes are not reconstructed",
            "Research quotes are exchange public ticker prices, not executable bid/ask fills",
            "Snapshot available time is after collection but still before actual downstream alert publication",
            "Candles held to original scan-start cutoff; older candle could make the current live opportunity expire",
            "Matched real-world signal win rate and eventual PnL are not estimated",
        ],
    }


def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--market",type=Path,required=True)
    a.add_argument("--decision",type=Path,required=True)
    a.add_argument("--report",type=Path,required=True)
    args=a.parse_args(argv)
    r=verify(json.loads(args.market.read_text(encoding="utf-8")),
             json.loads(args.decision.read_text(encoding="utf-8")))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print("RECEIPT_CLOCK_CAUSALITY",json.dumps({
        "status":r["status"],
        "eligible_before":r["pre_fetch_eligible_tickers"],
        "eligible_after":r["post_fetch_eligible_tickers"],
        "rescued":r["ticker_symbols_regained"],
        "rescued_in_validated_universe":r["selected_symbols_using_regained_tick"],
        "complete_symbols":r["selected_ohlcv_complete_symbols"],
        "profitability_proven":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
