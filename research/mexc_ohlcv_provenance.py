"""Read-only MEXC USDT perpetual OHLCV probe for provenance and coverage.

Strictly separated from the OKX scanner and ALL actual user trades. This
checks contiguous OHLCV bars at their OWN venue and excludes unfinished bars.
No historical bid/ask, funding, fees, real fills, signal, or profitability.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request

ENDPOINT="https://api.mexc.com/api/v1/contract/kline/"
DURATIONS={"Min1":60,"Min15":900,"Min60":3600}
ALLOWED_SYMBOLS={"ORCA_USDT"}


def retrieve(symbol,interval,start_s,end_s,*,opener=None):
    if symbol not in ALLOWED_SYMBOLS or interval not in DURATIONS:
        raise ValueError("unauthorized source instrument or interval")
    if any(type(x) is not int for x in (start_s,end_s)):
        raise ValueError("unix second timestamps required")
    if not 0<start_s<end_s or end_s-start_s>86400:
        raise ValueError("query must cover <=24h, no uncontrolled historical scraping")
    params=urllib.parse.urlencode({
        "interval":interval,"start":start_s,"end":end_s})
    url=ENDPOINT+symbol+"?"+params
    req=urllib.request.Request(url,method="GET",headers={
        "User-Agent":"CryptoFuturesScannerResearch/1.0",
        "Accept":"application/json"})
    op=opener or urllib.request.urlopen
    with op(req,timeout=15) as response:
        return json.load(response)


def validate(symbol,interval,start_s,end_s,payload,*,observed_at_s):
    if symbol not in ALLOWED_SYMBOLS or interval not in DURATIONS:
        raise ValueError("unapproved venue/interval")
    if type(observed_at_s) is not int or observed_at_s<end_s:
        raise ValueError("observation before requested period ends")
    if type(start_s) is not int or type(end_s) is not int or not 0<start_s<end_s:
        raise ValueError("bad UTC period")
    if payload.get("success") is not True or str(payload.get("code"))!="0":
        raise ValueError("provider did not attest success")
    source=payload.get("data")
    if not isinstance(source,dict):
        raise ValueError("missing provider OHLCV data")
    fields=("time","open","high","low","close","vol")
    if any(not isinstance(source.get(f),list) for f in fields):
        raise ValueError("incomplete parallel OHLCV arrays")
    n=len(source["time"])
    if n>2000 or any(len(source[f])!=n for f in fields):
        raise ValueError("mismatched or too many OHLCV arrays")
    duration=DURATIONS[interval]
    rows=[]
    out_of_requested_range=0
    for i in range(n):
        ts=source["time"][i]
        if type(ts) is not int or ts%duration:
            raise ValueError("invalid provider candle open seconds")
        if not start_s<=ts<end_s:
            # MEXC sometimes returns an inclusive end or a broader window.
            # Discard those bars; NEVER let them enter this sample.
            out_of_requested_range+=1
            continue
        prices=[]
        for name in ("open","high","low","close"):
            try:
                p=float(source[name][i])
            except (TypeError,ValueError,OverflowError) as exc:
                raise ValueError("malformed provider price") from exc
            if not math.isfinite(p) or p<=0:
                raise ValueError("invalid provider candle price")
            prices.append(p)
        op,hi,lo,cl=prices
        if not (lo<=min(op,cl)<=max(op,cl)<=hi):
            raise ValueError("inconsistent provider OHLC")
        volume=float(source["vol"][i])
        if not math.isfinite(volume) or volume<0:
            raise ValueError("invalid volume")
        # Source API can include the current unfinished candle.
        if ts+duration<=observed_at_s:
            rows.append((ts,op,hi,lo,cl,volume))
    if len({x[0] for x in rows})!=len(rows):
        raise ValueError("duplicate completed source candles")
    rows.sort(key=lambda r:r[0])
    gaps=sum(
        int((b[0]-a[0])/duration-1) for a,b in zip(rows,rows[1:])
        if b[0]-a[0]>duration)
    if any(b[0]<=a[0] or (b[0]-a[0])%duration for a,b in zip(rows,rows[1:])):
        raise ValueError("noncontiguous OHLCV chronology")
    all_times=[r[0] for r in rows]
    return {
        "status":"MEXC_PUBLIC_OHLCV_PROVENANCE_ONLY",
        "venue":"mexc_usdt_perpetual",
        "instrument":symbol,"interval":interval,
        "requested_start_utc":datetime.fromtimestamp(start_s,timezone.utc).isoformat(),
        "requested_end_utc":datetime.fromtimestamp(end_s,timezone.utc).isoformat(),
        "observed_at_utc":datetime.fromtimestamp(observed_at_s,timezone.utc).isoformat(),
        "source_reported_bars":n,
        "provider_out_of_requested_range_bars_excluded":out_of_requested_range,
        "provider_returned_only_requested_range":out_of_requested_range==0,
        "completed_bars":len(rows),
        "excluded_incomplete_or_future_bars":n-len(rows),
        "first_closed_bar_open_utc":datetime.fromtimestamp(
            all_times[0],timezone.utc).isoformat() if rows else None,
        "latest_closed_bar_open_utc":datetime.fromtimestamp(
            all_times[-1],timezone.utc).isoformat() if rows else None,
        "missing_contiguous_candles_between_first_last":gaps,
        "all_bars_from_mexc_only":True,
        "all_bars_closed_at_observation":True,
        "historical_instrument_listing_qualified":False,
        "point_in_time_tradable_bid_ask_verified":False,
        "funding_fees_liquidation_verified":False,
        "signals_or_fills_estimated":0,
        "profitable_edge_proven":False,
        "production_changed":False,
        "notes":[
            "Venue OHLCV quality only; not OKX price series",
            "Volume unit may be contracts; not assumed USDT quote volume",
            "Return is aggregated metadata, not user trade prices or inferred returns",
            "Provider bars beyond request boundaries are excluded, not interpreted as this sample",
            "Empty/sparse provider history does not prove contract was absent",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interval",choices=sorted(DURATIONS),default="Min15")
    p.add_argument("--start",type=int,required=True,help="UTC Unix seconds")
    p.add_argument("--end",type=int,required=True,help="UTC Unix seconds")
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    data=retrieve("ORCA_USDT",args.interval,args.start,args.end)
    outcome=validate("ORCA_USDT",args.interval,args.start,args.end,data,
                     observed_at_s=int(datetime.now(timezone.utc).timestamp()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(outcome,indent=2)+"\n",encoding="utf-8")
    print("MEXC_CANDLE_PROVENANCE",json.dumps({
        "interval":outcome["interval"],"completed_bars":outcome["completed_bars"],
        "gaps":outcome["missing_contiguous_candles_between_first_last"],
        "incomplete_bars_excluded":outcome["excluded_incomplete_or_future_bars"],
        "profitable_edge_proven":False,"real_fills":0,
    },sort_keys=True))


if __name__=="__main__":
    main()
