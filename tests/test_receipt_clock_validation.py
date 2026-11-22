"""Synthetic, no-network tests: correct quote clock without look-ahead."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from research.receipt_clock_validation import verify

SCAN=1_800_000_090_000
SYM="SOL-USDT-SWAP"


def iso(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()


def market_and_decision():
    def bars(duration):
        close=SCAN//duration*duration
        return [[close-duration,100,101,99,100,100,10000]]
    ready=SCAN+20000
    source={
        "status":"COLLECTED","market_type":"USDT_PERPETUAL",
        "signals_allowed":False,"collected_utc":iso(ready),
        "universe":[{"symbol":SYM,"ticker_time_utc":iso(SCAN+200)}],
        "candles":{SYM:{
            tf:bars(duration) for tf,duration in [
                ("15m",900000),("1h",3600000),("2h",7200000)]
        }},
        "research_clock_provenance":{
            "ticker_clock_mode":"receipt_research_only",
            "scan_started_utc":iso(SCAN),
            "ticker_fetch_started_utc":iso(SCAN+100),
            "ticker_fetch_completed_utc":iso(SCAN+400),
            "decision_data_ready_utc":iso(ready),
            "collected_at_means_data_ready":True,
            "legacy_eligible_tickers":0,
            "receipt_eligible_tickers":1,
            "maximum_quote_age_seconds":300,
            "shadow_added_symbols":[SYM],
            "shadow_removed_symbols":[],
            "no_order_execution":True,
        },
    }
    decision={
        "order_allowed":False,
        "source_collected_utc":source["collected_utc"],
        "evaluated_at_utc":iso(SCAN+30000),
        "source_asset_count":1,
        "retained_asset_count":1,
    }
    return source,decision


class ResearchReceiptCausalityTests(unittest.TestCase):
    def test_accepts_source_tick_after_scan_start_but_before_actual_receipt(self):
        source,decision=market_and_decision()
        report=verify(source,decision)
        self.assertEqual(report["status"],
                         "RECEIPT_CLOCK_SHADOW_CAUSALITY_VERIFIED")
        self.assertEqual(report["pre_fetch_eligible_tickers"],0)
        self.assertEqual(report["post_fetch_eligible_tickers"],1)
        self.assertEqual(report["selected_symbols_using_regained_tick"],1)
        self.assertTrue(report["decision_available_after_all_used_data"])
        self.assertFalse(report["profitability_proven"])
        self.assertEqual(report["real_exchange_fills_verified"],0)

    def test_rejects_snapshot_incorrectly_dated_to_scan_start(self):
        a,b=market_and_decision()
        a["collected_utc"]=iso(SCAN)
        b["source_collected_utc"]=iso(SCAN)
        with self.assertRaises(ValueError):
            verify(a,b)

    def test_rejects_exchange_quote_after_fetch_received(self):
        a,b=market_and_decision()
        a["universe"][0]["ticker_time_utc"]=iso(SCAN+600)
        with self.assertRaises(ValueError):
            verify(a,b)

    def test_rejects_stale_quote_at_actual_snapshot_ready_time(self):
        a,b=market_and_decision()
        a["universe"][0]["ticker_time_utc"]=iso(SCAN-400000)
        with self.assertRaises(ValueError):
            verify(a,b)

    def test_rejects_candle_closed_after_scan_start(self):
        a,b=market_and_decision()
        a["candles"][SYM]["15m"][-1][0]=SCAN//900000*900000
        with self.assertRaises(ValueError):
            verify(a,b)

    def test_rejects_signal_decision_time_prior_to_market_availability(self):
        a,b=market_and_decision()
        b["evaluated_at_utc"]=iso(SCAN+15000)
        with self.assertRaises(ValueError):
            verify(a,b)

    def test_rejects_quote_or_time_provenance_missing(self):
        a,b=market_and_decision()
        del a["research_clock_provenance"]["ticker_fetch_completed_utc"]
        with self.assertRaises((ValueError,KeyError)):
            verify(a,b)

    def test_rejects_mismatched_source_timestamp_or_changed_asset_count(self):
        for mode in ("timestamp","count"):
            a,b=market_and_decision()
            if mode=="timestamp":
                b["source_collected_utc"]=iso(SCAN)
            else:
                b["source_asset_count"]=2
            with self.assertRaises(ValueError):
                verify(a,b)

    def test_requires_no_order_authorization_for_both_market_and_decision(self):
        for side in ("market","decision"):
            a,b=market_and_decision()
            if side=="market":
                a["signals_allowed"]=True
            else:
                b["order_allowed"]=True
            with self.assertRaises(ValueError):
                verify(a,b)

    def test_legacy_uninstrumented_market_is_not_falsely_verified(self):
        a,b=market_and_decision()
        del a["research_clock_provenance"]
        with self.assertRaises(ValueError):
            verify(a,b)

    def test_allows_historical_close_at_start_but_not_after(self):
        a,b=market_and_decision()
        candle=a["candles"][SYM]["15m"][-1]
        self.assertLessEqual(candle[0]+900000,SCAN)
        verify(a,b)
        candle[0]=SCAN//900000*900000
        with self.assertRaises(ValueError):
            verify(a,b)


if __name__=="__main__":
    unittest.main()
