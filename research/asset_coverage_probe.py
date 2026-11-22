"""Explain why a generic instrument may be absent from the scanner universe.

Pure research. Public OKX instrument metadata is optional and current only,
never evidence of any individual's trade or historical instrument availability.
No positions, performance, order endpoints, private keys or account data.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from collector import BLOCKED_BASES, CRYPTO_BASES, OKX, request_json

_PATTERN=re.compile(r"^[A-Z0-9]{2,30}$")


def request_current_public_instrument(base):
    """A 51001 response explicitly means unknown current instrument ID."""
    base=base.upper()
    if not _PATTERN.fullmatch(base):
        raise ValueError("invalid public asset ticker")
    raw=request_json(f"{OKX}/public/instruments",{
        "instType":"SWAP","instId":f"{base}-USDT-SWAP"})
    code=str(raw.get("code"))
    data=raw.get("data")
    if code=="0" and isinstance(data,list):
        return data,code
    if code=="51001" and data==[]:
        return [],code
    raise RuntimeError("public OKX instrument query did not return valid metadata")


def explain(base, *, market_snapshot=None, instruments=None,
            provider_response_code=None):
    base=base.upper()
    if not _PATTERN.fullmatch(base):
        raise ValueError("invalid public asset ticker")
    full=f"{base}-USDT-SWAP"
    curated=base in CRYPTO_BASES
    blocked=base in BLOCKED_BASES
    if instruments is None and provider_response_code is not None:
        raise ValueError("response code without provider evidence")
    provider=None
    if instruments is not None:
        matched=[i for i in instruments if i.get("instId")==full]
        if len(matched)>1:
            raise ValueError("duplicate exchange contract identifier")
        provider={
            "provider_response_code":provider_response_code,
            "current_usdt_swap_contract_exists":bool(matched),
            "current_usdt_swap_contract_live":(
                matched[0].get("state")=="live" if matched else False),
            "current_official_tick_size":matched[0].get("tickSz") if matched else None,
            "current_provider_state":matched[0].get("state") if matched else "not_found",
            "historical_listing_at_signal_time_verified":False,
        }
    selected=None
    snapshot_time=None
    if market_snapshot is not None:
        if (market_snapshot.get("status")!="COLLECTED" or
            market_snapshot.get("market_type")!="USDT_PERPETUAL"):
            raise ValueError("invalid comparison snapshot")
        selected=full in {
            item["symbol"] for item in market_snapshot.get("universe",[])
            if isinstance(item,dict) and item.get("symbol")
        }
        snapshot_time=market_snapshot.get("collected_utc")
    return {
        "asset_symbol":base,
        "instrument_symbol_okx_usdt_swap":full,
        "is_in_curated_base_allowlist":curated,
        "is_in_blocked_base_list":blocked,
        "can_enter_collector_curated_selection":curated and not blocked,
        "is_in_given_scanner_snapshot":selected,
        "comparison_snapshot_collected_utc":snapshot_time,
        "public_okx_contract_check":provider,
        "provider_status_unknown_if_not_checked":instruments is None,
        "external_venue_equivalence_proven":False,
        "historical_entry_and_exit_prices_known":False,
        "user_trade_or_return_recorded":False,
        "real_trade_verified":False,
        "scanner_trade_generated":False,
        "filters_or_production_changed":False,
        "notes":[
            "Curated discovery is a gate before prescreen, liquidity and 50-asset universe",
            "A missing base can be excluded before ANY LONG/SHORT pattern evaluation",
            "Even a listed swap might fail liquidity, prescreen or 50-asset constraints",
            "Current public contract metadata does not prove availability at an earlier time",
            "This does not compare the external signal methodology with the scanner setup",
            "No personal trading data is sent to, or preserved in, this report",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol",required=True)
    p.add_argument("--market-data",type=Path)
    p.add_argument("--check-provider",action="store_true")
    p.add_argument("--report",type=Path,required=True)
    a=p.parse_args(argv)
    snap=(json.loads(a.market_data.read_text(encoding="utf-8"))
          if a.market_data else None)
    rows,provider_code=(request_current_public_instrument(a.symbol)
                        if a.check_provider else (None,None))
    report=explain(a.symbol,market_snapshot=snap,instruments=rows,
                   provider_response_code=provider_code)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print("PUBLIC_INSTRUMENT_COVERAGE",json.dumps({
        "symbol":report["asset_symbol"],
        "curated_allowlist":report["is_in_curated_base_allowlist"],
        "present_in_snapshot":report["is_in_given_scanner_snapshot"],
        "public_contract_check":report["public_okx_contract_check"],
        "personal_trade_recorded":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
