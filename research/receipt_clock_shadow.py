"""Research-only baseline vs receipt-clock conditional decisions on SAME snapshot.

Input is one collector run in explicit receipt mode; no second network query,
orders, price alteration, hidden threshold search or retrospective fills.
Baseline is limited to the subset of RESEARCH snapshot symbols which passed
the original pre-fetch ticker filter; if ticker was not fully OHLCV-validated
under legacy, this is a shadow lower bound, not the actual legacy run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from collector import INTERVALS
from trade_decision import analyze


def _time(iso):
    dt=datetime.fromisoformat(str(iso).replace("Z","+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive timestamp")
    return round(dt.timestamp()*1000)


def compare(market, *, at_utc=None):
    if market.get("status")!="COLLECTED" or market.get("signals_allowed") is not False:
        raise ValueError("not a completed read-only snapshot")
    if market.get("venue")!="okx_swap":
        raise ValueError("not the OKX scanner")
    p=market.get("research_clock_provenance")
    if not isinstance(p,dict) or p.get("ticker_clock_mode")!="receipt_research_only":
        raise ValueError("must explicitly use research receipt clock")
    begin=_time(p["scan_started_utc"])
    tick_start=_time(p["ticker_fetch_started_utc"])
    tick_end=_time(p["ticker_fetch_completed_utc"])
    ready=_time(p["decision_data_ready_utc"])
    stamped=_time(market["collected_utc"])
    if not begin<=tick_start<=tick_end<=ready==stamped:
        raise ValueError("snapshot claims availability before input receipt")
    if ready-begin>1_800_000:
        raise ValueError("research collection exceeded maximum 30m")
    tickers=market["universe"]
    symbols=[x["symbol"] for x in tickers]
    if len(set(symbols))!=len(symbols):
        raise ValueError("duplicate final ticker")
    if len(tickers)<15:
        raise ValueError("incomplete source universe")
    if set(market["candles"])!=set(symbols):
        raise ValueError("candle universe does not match final selection")
    for item in tickers:
        stamp=_time(item["ticker_time_utc"])
        if not 0<=tick_end-stamp<=300000 or not 0<=ready-stamp<=300000:
            raise ValueError("stale/future quote in final snapshot")
        hist=market["candles"][item["symbol"]]
        for tf,window in INTERVALS.items():
            if tf not in ("15m","1h","2h"):
                continue
            values=hist[tf]
            if not values:
                raise ValueError("missing last closed candle")
            close=int(values[-1][0])+window*1000
            if close>begin or close>ready:
                raise ValueError("future candle in research snapshot")
            if ready-close>window*1000:
                raise ValueError("research snapshot has stale last candle")
    legacy_eligible=p["legacy_eligible_tickers"]
    current_eligible=p["receipt_eligible_tickers"]
    added=set(p["shadow_added_symbols"])
    removed=set(p["shadow_removed_symbols"])
    if len(added)!=len(p["shadow_added_symbols"]) or len(removed)!=len(p["shadow_removed_symbols"]):
        raise ValueError("duplicate shadow symbols")
    if added & removed:
        raise ValueError("shadow sets overlap")
    # Some tickers recovered at the first gate never finish liquidity/candle
    # validation; explicitly account for them instead of silently ignoring.
    selected=set(symbols)
    recovered_not_selected=added-selected
    baseline_symbols=selected-added
    control=dict(market)
    control["universe"]=[x for x in tickers if x["symbol"] in baseline_symbols]
    control["candles"]={
        sym:data for sym,data in market["candles"].items()
        if sym in baseline_symbols
    }
    now=at_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("evaluation time must be timezone-aware")
    if _time(now.isoformat())<ready:
        raise ValueError("evaluation before source available")
    experimental=analyze(market,now_utc=now)
    baseline=analyze(control,now_utc=now)
    if any(r["order_allowed"] is not False for r in (baseline,experimental)):
        raise ValueError("prohibited order permission")
    base_keys={(r["symbol"],r["side"],r["method"])
               for r in baseline["confirmed_entry_triggers"]}
    new_keys={(r["symbol"],r["side"],r["method"])
              for r in experimental["confirmed_entry_triggers"]}
    if not base_keys.issubset(new_keys):
        raise ValueError("one shared-source candidate lost without changed filters")
    if any(row["symbol"] not in added for row in experimental["confirmed_entry_triggers"]
           if (row["symbol"],row["side"],row["method"]) not in base_keys):
        raise ValueError("new conditional trigger not solely from recovered ticker")
    if p["legacy_eligible_tickers"]<len(baseline_symbols):
        raise ValueError("shadow baseline cannot have more selected than source")
    if p["receipt_eligible_tickers"]<len(symbols):
        raise ValueError("shadow receipt count below final source universe")
    return {
        "status":"SAME_PAYLOAD_RECEIPT_CLOCK_SHADOW_ONLY",
        "scan_started_utc":p["scan_started_utc"],
        "ticker_fetch_completed_utc":p["ticker_fetch_completed_utc"],
        "data_ready_utc":p["decision_data_ready_utc"],
        "pre_fetch_eligible_tickers":legacy_eligible,
        "receipt_eligible_tickers":current_eligible,
        "recovered_tickers_at_first_filter":len(added),
        "lost_tickers_at_first_filter":len(removed),
        "first_filter_recovered_symbols":sorted(added),
        "full_candle_validated_selected_symbols":len(symbols),
        "validated_selected_from_legacy_ticker_set":len(baseline_symbols),
        "validated_selected_only_due_to_receipt":len(set(symbols)&added),
        "recovered_tickers_not_selected_or_validated":len(recovered_not_selected),
        "baseline_shadow_is_subset_not_independent_full_legacy_collection":True,
        "source_current_for_new_entries":experimental["current_for_new_entries"],
        "base_source_current_for_new_entries":baseline["current_for_new_entries"],
        "old_full_candle_validated_conditional_triggers":len(base_keys),
        "receipt_full_candle_validated_conditional_triggers":len(new_keys),
        "new_conditional_triggers_due_to_recovered_tickers":len(new_keys-base_keys),
        "new_conditional_trigger_symbols":sorted({
            x[0] for x in new_keys-base_keys}),
        "old_analyzed_assets":baseline["analyzed_asset_count"],
        "receipt_analyzed_assets":experimental["analyzed_asset_count"],
        "any_orders_submitted":False,
        "profitability_verified":False,
        "master_or_market_data_changed":False,
        "warnings":[
            "One matching set of OHLCV and tickers; no counterfactual real fills",
            "The legacy shadow is the intersection with receipt-clock candle-validated universe, NOT an independent legacy collection",
            "Old-only first-filter tickers are not downloaded in this research run",
            "Any newly counted conditional setup is not automatically executable",
            "Runner time is not publication time; cron scheduler remains unaudited by this run",
            "No parameter, market gate, cost or risk guard was relaxed",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--market-data",type=Path,required=True)
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    snapshot=json.loads(args.market_data.read_text(encoding="utf-8"))
    result=compare(snapshot)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("RECEIPT_CLOCK_SHADOW",json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
