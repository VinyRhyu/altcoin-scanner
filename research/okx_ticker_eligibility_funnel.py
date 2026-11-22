"""Reproduce the OKX ticker eligibility gate as a read-only diagnostic.

Shares the source filters' constants and the same public data shape.
Reports EXCLUSIVE first-failure counts for market/tickers; no change to
collector thresholds, shortlist, orders or 15m candle validation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import collector

REASONS=(
    "non_usdt_perpetual",
    "not_live_in_instruments",
    "outside_curated_or_live_top100",
    "blocked_base",
    "1000_prefixed",
    "invalid_price_open_or_turnover",
    "invalid_bid_ask",
    "spread_over_0_25_pct",
    "stale_or_future_exchange_ticker",
    "invalid_daily_range",
    "volume_below_minimum",
    "eligible_ticker",
)


def _number(value):
    x=float(value)
    if not math.isfinite(x) or x<=0:
        raise ValueError("not positive and finite")
    return x


def inspect(instruments,tickers,allowed,now_ms, *, response_ms=None):
    if type(now_ms) is not int or now_ms<=0:
        raise ValueError("invalid quote reference clock")
    if response_ms is not None and (
        type(response_ms) is not int or response_ms<now_ms):
        raise ValueError("response timestamp precedes request reference")
    if not isinstance(instruments,list) or not isinstance(tickers,list):
        raise ValueError("invalid public OKX datasets")
    live={
        x["instId"] for x in instruments
        if isinstance(x,dict) and x.get("state")=="live"
        and isinstance(x.get("instId"),str)
        and x["instId"].endswith("-USDT-SWAP")
    }
    if len(live)!=sum(
        1 for x in instruments if isinstance(x,dict) and
        x.get("state")=="live" and isinstance(x.get("instId"),str)
        and x["instId"].endswith("-USDT-SWAP")
    ):
        raise ValueError("duplicate public live OKX instruments")
    if not isinstance(allowed,(set,frozenset)):
        raise ValueError("invalid eligible base universe")
    outcomes=Counter()
    ticker_time_failure=Counter()
    eligible_ticker_ages_ms=[]
    symbols=set()
    eligible=[]
    for item in tickers:
        if not isinstance(item,dict) or not isinstance(item.get("instId"),str):
            raise ValueError("invalid OKX ticker item")
        symbol=item["instId"]
        if symbol in symbols:
            raise ValueError("duplicate exchange ticker symbol")
        symbols.add(symbol)
        reason=None
        if not symbol.endswith("-USDT-SWAP"):
            reason="non_usdt_perpetual"
        else:
            base=symbol[:-10]
            if symbol not in live:
                reason="not_live_in_instruments"
            elif base not in allowed:
                reason="outside_curated_or_live_top100"
            elif base in collector.BLOCKED_BASES:
                reason="blocked_base"
            elif base.startswith("1000"):
                reason="1000_prefixed"
            else:
                try:
                    price=_number(item["last"])
                    open24=_number(item["open24h"])
                    turnover=_number(item["volCcy24h"])*price
                    if not math.isfinite(turnover):
                        raise ValueError("invalid turnover")
                except (TypeError,ValueError,OverflowError,KeyError):
                    reason="invalid_price_open_or_turnover"
                if reason is None:
                    try:
                        bid=_number(item["bidPx"])
                        ask=_number(item["askPx"])
                        spread=(ask-bid)/price*100
                        if ask<bid:
                            reason="invalid_bid_ask"
                        elif spread>0.25:
                            reason="spread_over_0_25_pct"
                    except (TypeError,ValueError,OverflowError,KeyError):
                        reason="invalid_bid_ask"
                if reason is None:
                    try:
                        tick_ms=int(item["ts"])
                        age=now_ms-tick_ms
                        if not 0<=age<=300_000:
                            reason="stale_or_future_exchange_ticker"
                            if age<0:
                                ticker_time_failure["provider_ts_after_pre_fetch_clock"]+=1
                            else:
                                ticker_time_failure["provider_ts_older_than_300s"]+=1
                        else:
                            eligible_ticker_ages_ms.append(age)
                    except (TypeError,ValueError,OverflowError,KeyError):
                        reason="stale_or_future_exchange_ticker"
                if reason is None:
                    try:
                        hi=_number(item["high24h"])
                        lo=_number(item["low24h"])
                        change=100*(price/open24-1)
                        if (hi<max(price,lo) or lo>price
                                or not math.isfinite(change)):
                            reason="invalid_daily_range"
                    except (TypeError,ValueError,OverflowError,KeyError):
                        reason="invalid_daily_range"
                if reason is None:
                    if turnover<collector.MIN_VOLUME_USDT:
                        reason="volume_below_minimum"
                    else:
                        reason="eligible_ticker"
                        eligible.append(symbol)
        outcomes[reason]+=1
    if sum(outcomes.values())!=len(tickers):
        raise ValueError("eligibility accounting mismatch")
    if any(name not in REASONS for name in outcomes):
        raise ValueError("unrecognized eligibility category")
    return {
        "status":"OKX_PUBLIC_TICKER_ELIGIBILITY_DIAGNOSTICS_ONLY",
        "source_total_public_tickers":len(tickers),
        "source_live_usdt_perpetual_instruments":len(live),
        "source_allowed_base_symbols":len(allowed),
        "reference_market_clock_ms":now_ms,
        "first_rejection_reason_counts":{name:outcomes[name] for name in REASONS},
        "ticker_eligible_count":len(eligible),
        "ticker_eligible_symbols_public_only":sorted(eligible),
        "timestamp_rejection_breakdown":{
            "provider_ts_after_pre_fetch_clock":
                ticker_time_failure["provider_ts_after_pre_fetch_clock"],
            "provider_ts_older_than_300s":
                ticker_time_failure["provider_ts_older_than_300s"],
        },
        "counterfactual_end_of_public_fetch":(
            {
                "reference_ms":response_ms,
                "eligible_ticker_count":inspect(
                    instruments,tickers,allowed,response_ms)["ticker_eligible_count"],
                "time_shift_ms":response_ms-now_ms,
                "not_a_change_to_production":True,
            }
            if response_ms is not None else None
        ),
        "minimum_validated_unchanged":collector.MIN_VALIDATED,
        "full_candle_validation_performed":False,
        "filter_thresholds_changed":False,
        "order_placement_enabled":False,
        "production_changed":False,
        "personal_trades_recorded":False,
        "limitations":[
            "This is one public-API sample, NOT identical in time to previous 47/9/8 snapshots",
            "Only tests ticker eligibility, not prescreen, 15m liquidity, candle continuity or final universe",
            "Rejection reasons are EXCLUSIVE, in the same precedence as collector.tickers",
            "Counterfactual filters, technical entries and orders were not modified",
            "A future-ticker timestamp measured against the pre-fetch clock is not evidence of exchange staleness",
            "Counterfactual end-of-fetch check uses the same payload: diagnostic only, not production clock adjustment",
            "Large variations across samples cannot be assigned to cause without matched-time evidence",
        ],
    }


def live_read(*, ticker_fetch=None, clock_ms=None):
    clk=clock_ms or (lambda:int(time.time()*1000))
    now_ms=clk()
    allowed,allowed_source=collector.eligible_bases()
    fetch=ticker_fetch or collector.okx
    instruments=fetch("public/instruments",instType="SWAP")
    raw=fetch("market/tickers",instType="SWAP")
    after_fetch_ms=clk()
    result=inspect(instruments,raw,allowed,now_ms,response_ms=after_fetch_ms)
    result["allowlist_source"]=allowed_source
    result["checked_at_utc"]=datetime.fromtimestamp(
        now_ms/1000,timezone.utc).isoformat()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args(argv)
    r=live_read()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(r,indent=2)+"\n",encoding="utf-8")
    print("OKX_FUNNEL",json.dumps({
        "raw_tickers":r["source_total_public_tickers"],
        "eligible":r["ticker_eligible_count"],
        "min_required_for_collector":r["minimum_validated_unchanged"],
        "reasons":r["first_rejection_reason_counts"],
        "time_breakdown":r["timestamp_rejection_breakdown"],
        "same_payload_retimed_after_fetch":r["counterfactual_end_of_public_fetch"],
        "private_trade_data":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
