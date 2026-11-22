"""All provider fixtures are public contract metadata, never personal trades."""
from io import BytesIO
import json
import unittest

from research.mexc_public_coverage import ENDPOINT, interpret, request_contract


def active():
    return {
        "success":True,"code":0,
        "data":{
            "symbol":"ORCA_USDT","baseCoin":"ORCA","quoteCoin":"USDT",
            "settleCoin":"USDT","futureType":1,"state":0,
            "maxLeverage":50,"priceUnit":.001,"contractSize":1,
            "apiAllowed":False,
        },
    }


class MexcPublicCoverageTests(unittest.TestCase):
    def test_current_enabled_contract_and_cross_venue_flag_not_personal_trade(self):
        r=interpret("ORCA",active(),checked_at_utc="2026-11-22T18:00:00Z")
        self.assertTrue(r["current_contract_found"])
        self.assertEqual(r["current_contract_query_status"],"ENABLED_CURRENT_CONTRACT")
        self.assertEqual(r["contract"]["max_leverage_metadata"],50)
        self.assertFalse(r["contract"]["current_api_trading_allowed_flag"])
        self.assertEqual(r["contract"]["price_tick"],.001)
        self.assertFalse(r["okx_scanner_curated_allowlist_includes_base"])
        self.assertFalse(r["historical_contract_availability_verified"])
        self.assertFalse(r["same_scanner_signal_inferred"])
        self.assertFalse(r["external_signal_or_personal_trade_saved"])
        self.assertEqual(r["orders_submitted"],0)

    def test_not_checked_is_unknown_not_absent(self):
        r=interpret("ORCA",checked_at_utc="2026-11-22T18:00:00Z")
        self.assertIsNone(r["current_contract_found"])
        self.assertEqual(r["current_contract_query_status"],"NOT_CHECKED")

    def test_explicit_successful_empty_response_is_not_found(self):
        r=interpret("ORCA",{"success":True,"code":0,"data":[]})
        self.assertFalse(r["current_contract_found"])
        self.assertEqual(r["current_contract_query_status"],
                         "NOT_IN_SUCCESSFUL_PROVIDER_RESPONSE")

    def test_present_but_paused_is_not_enabled(self):
        p=active()
        p["data"]["state"]=4
        r=interpret("ORCA",p)
        self.assertTrue(r["current_contract_found"])
        self.assertFalse(r["contract"]["current_trading_enabled"])
        self.assertEqual(r["current_contract_query_status"],
                         "PRESENT_NOT_CURRENTLY_ENABLED")

    def test_reject_spot_or_wrong_quote_even_if_base_name_matches(self):
        p=active()
        p["data"]["futureType"]=2
        with self.assertRaises(ValueError):
            interpret("ORCA",p)
        p=active()
        p["data"]["settleCoin"]="USDC"
        with self.assertRaises(ValueError):
            interpret("ORCA",p)

    def test_provider_error_must_not_be_reported_as_missing_contract(self):
        with self.assertRaises(ValueError):
            interpret("ORCA",{"success":False,"code":51001,"data":[]})
        with self.assertRaises(ValueError):
            interpret("ORCA",{"success":True,"code":0,"data":None})

    def test_exact_match_only_and_no_duplicate_contract(self):
        p=active()
        p["data"]=[{**p["data"],"symbol":"ORCAS_USDT"}]
        r=interpret("ORCA",p)
        self.assertFalse(r["current_contract_found"])
        p=active()
        p["data"]=[p["data"],p["data"]]
        with self.assertRaises(ValueError):
            interpret("ORCA",p)

    def test_public_http_request_is_one_get_without_credentials(self):
        urls=[]
        def open_public(request,timeout):
            urls.append((request.full_url,request.get_method(),timeout,
                         dict(request.header_items())))
            return BytesIO(json.dumps(active()).encode("utf-8"))
        p=request_contract("ORCA",opener=open_public)
        self.assertEqual(p["data"]["symbol"],"ORCA_USDT")
        self.assertEqual(len(urls),1)
        self.assertEqual(urls[0][0],ENDPOINT+"?symbol=ORCA_USDT")
        self.assertEqual(urls[0][1],"GET")
        self.assertEqual(urls[0][2],15)
        self.assertFalse(any(x.lower()=="authorization" for x in urls[0][3]))
        with self.assertRaises(ValueError):
            request_contract("ORCA/../../private",opener=open_public)


if __name__=="__main__":
    unittest.main()
