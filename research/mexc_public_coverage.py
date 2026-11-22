"""Read-only public MEXC futures contract coverage, separate from OKX scanner.

No trading/position API, no credentials, no user's personal trade stored.
A currently enabled futures contract does NOT establish historical listing
at any earlier signal time, executed return or scanner eligibility.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from collector import CRYPTO_BASES, BLOCKED_BASES

ENDPOINT="https://api.mexc.com/api/v1/contract/detail"
BASE_PATTERN=re.compile(r"^[A-Z0-9]{2,30}$")


def _positive_optional(value):
    if value is None:
        return None
    try:
        x=float(value)
    except (TypeError,ValueError,OverflowError) as exc:
        raise ValueError("invalid public contract numeric field") from exc
    if not math.isfinite(x) or x<=0:
        raise ValueError("invalid public contract numeric field")
    return x


def request_contract(base, *, opener=None):
    """Only one bounded PUBLIC GET to MEXC /api/v1/contract/detail."""
    if not BASE_PATTERN.fullmatch(base):
        raise ValueError("invalid asset")
    url=ENDPOINT+"?"+urllib.parse.urlencode({"symbol":base+"_USDT"})
    req=urllib.request.Request(url,headers={
        "User-Agent":"CryptoFuturesScannerResearch/1.0",
        "Accept":"application/json",
    },method="GET")
    op=opener or urllib.request.urlopen
    with op(req,timeout=15) as response:
        raw=json.load(response)
    return raw


def interpret(base, payload=None, *, checked_at_utc=None):
    if not BASE_PATTERN.fullmatch(base):
        raise ValueError("invalid asset")
    if checked_at_utc is None:
        checked_at_utc=datetime.now(timezone.utc).isoformat()
    target=base+"_USDT"
    currently_listed=None
    contract=None
    status="NOT_CHECKED"
    if payload is not None:
        if not isinstance(payload,dict):
            raise ValueError("invalid public MEXC payload")
        if str(payload.get("code"))!="0" or payload.get("success") is not True:
            raise ValueError("MEXC did not confirm successful contract metadata retrieval")
        data=payload.get("data")
        if isinstance(data,dict):
            records=[data]
        elif isinstance(data,list):
            records=data
        else:
            raise ValueError("unexpected public contract response body")
        if any(not isinstance(x,dict) for x in records):
            raise ValueError("malformed public contract metadata")
        matched=[x for x in records if x.get("symbol")==target]
        if len(matched)>1:
            raise ValueError("multiple exact matching public contracts")
        if not matched:
            currently_listed=False
            status="NOT_IN_SUCCESSFUL_PROVIDER_RESPONSE"
        else:
            c=matched[0]
            if c.get("baseCoin")!=base or c.get("quoteCoin")!="USDT" or c.get("settleCoin")!="USDT":
                raise ValueError("unexpected base/quote/settlement asset")
            if type(c.get("futureType")) is not int or c["futureType"]!=1:
                raise ValueError("not an established perpetual contract")
            if type(c.get("state")) is not int:
                raise ValueError("contract trading state unavailable")
            currently_listed=True
            state=c["state"]
            status="ENABLED_CURRENT_CONTRACT" if state==0 else "PRESENT_NOT_CURRENTLY_ENABLED"
            contract={
                "public_symbol":target,
                "contract_type":"USDT_SETTLED_PERPETUAL",
                "current_state_code":state,
                "current_trading_enabled":state==0,
                "max_leverage_metadata":_positive_optional(c.get("maxLeverage")),
                "price_tick":_positive_optional(c.get("priceUnit")),
                "contract_size":_positive_optional(c.get("contractSize")),
                "current_api_trading_allowed_flag":(
                    c.get("apiAllowed") if type(c.get("apiAllowed")) is bool else None),
                "fees_not_proven_at_historical_signal":True,
            }
    return {
        "status":"MEXC_PUBLIC_FUTURES_COVERAGE_READ_ONLY",
        "checked_at_utc":checked_at_utc,
        "symbol":base,
        "expected_public_contract_symbol":target,
        "current_contract_query_status":status,
        "current_contract_found":currently_listed,
        "contract":contract,
        "okx_scanner_curated_allowlist_includes_base":base in CRYPTO_BASES,
        "okx_scanner_blocklist_includes_base":base in BLOCKED_BASES,
        "historical_contract_availability_verified":False,
        "suitable_for_price_execution_backtest":False,
        "same_scanner_signal_inferred":False,
        "external_signal_or_personal_trade_saved":False,
        "production_changed":False,
        "orders_submitted":0,
        "limitations":[
            "MEXC and OKX use different instruments, liquidity and timestamps",
            "One successful public metadata response confirms current state only",
            "Provider state/fees/leverage limits may depend on jurisdiction and account",
            "A contract listing alone cannot prove scanner pattern confirmation, tradable fills or user returns",
            "No private account, order, position, funding or exact historical quote data used",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",default="ORCA")
    p.add_argument("--check-provider",action="store_true")
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    base=args.symbol.strip().upper()
    if not BASE_PATTERN.fullmatch(base):
        raise ValueError("invalid asset")
    payload=request_contract(base) if args.check_provider else None
    result=interpret(base,payload)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print("MEXC_PUBLIC_COVERAGE",json.dumps({
        "symbol":base,
        "current_contract_query_status":result["current_contract_query_status"],
        "current_contract_found":result["current_contract_found"],
        "contract":result["contract"],
        "external_signal_or_personal_trade_saved":False,
        "orders_submitted":0,
    },sort_keys=True))


if __name__=="__main__":
    main()
