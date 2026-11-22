"""Matched historical/source timing guards for receipt-clock signal shadow."""
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

import collector
from research.receipt_clock_shadow import compare

BAR=900000
BEG=1800000000000//BAR*BAR+90000
FETCH_START=BEG+100
FETCH_END=BEG+500
READY=BEG+30000
SYMS=["SOL-USDT-SWAP"]+[f"ALT{i}-USDT-SWAP" for i in range(14)]


def sample():
    p={
        "scan_started_utc":collector.utc(BEG),
        "ticker_fetch_started_utc":collector.utc(FETCH_START),
        "ticker_fetch_completed_utc":collector.utc(FETCH_END),
        "decision_data_ready_utc":collector.utc(READY),
        "ticker_clock_mode":"receipt_research_only",
        "legacy_eligible_tickers":14,
        "receipt_eligible_tickers":15,
        "shadow_added_symbols":[SYMS[0]],
        "shadow_removed_symbols":[],
    }
    return {
        "status":"COLLECTED","signals_allowed":False,
        "venue":"okx_swap",
        "market_type":"USDT_PERPETUAL",
        "collected_utc":collector.utc(READY),
        "research_clock_provenance":p,
        "universe":[{"symbol":s,"ticker_time_utc":collector.utc(BEG+200),
                     "price":100,"spread_pct":.02} for s in SYMS],
        "candles":{
            s:{tf:[[BEG//(duration*1000)*(duration*1000)-duration*1000,
                    100,101,99,100,10,1000]]
               for tf,duration in collector.INTERVALS.items()
               if tf in ("15m","1h","2h")}
            for s in SYMS
        },
    }


def fake_analyze(market,now_utc):
    symbols={t["symbol"] for t in market["universe"]}
    return {
        "order_allowed":False,
        "current_for_new_entries":True,
        "confirmed_entry_triggers":[
            {"symbol":s,"side":"LONG","method":"BREAKOUT"}
            for s in ("SOL-USDT-SWAP","ALT0-USDT-SWAP") if s in symbols],
        "analyzed_asset_count":len(symbols),
    }


class ReceiptShadowTests(unittest.TestCase):
    def test_same_candles_and_clock_only_adds_recovered_ticker_setup(self):
        data=sample()
        with patch("research.receipt_clock_shadow.analyze",
                   side_effect=fake_analyze):
            r=compare(data,at_utc=datetime.fromtimestamp(
                (READY+1000)/1000,timezone.utc))
        self.assertEqual(r["recovered_tickers_at_first_filter"],1)
        self.assertEqual(r["validated_selected_only_due_to_receipt"],1)
        self.assertEqual(r["old_full_candle_validated_conditional_triggers"],1)
        self.assertEqual(r["receipt_full_candle_validated_conditional_triggers"],2)
        self.assertEqual(r["new_conditional_triggers_due_to_recovered_tickers"],1)
        self.assertEqual(r["new_conditional_trigger_symbols"],["SOL-USDT-SWAP"])
        self.assertFalse(r["profitability_verified"])
        self.assertFalse(r["any_orders_submitted"])

    def test_quote_newer_than_fetch_receipt_rejected(self):
        data=sample()
        data["universe"][0]["ticker_time_utc"]=collector.utc(FETCH_END+1)
        with self.assertRaises(ValueError):
            compare(data)

    def test_quote_stale_at_data_ready_rejected(self):
        data=sample()
        data["universe"][0]["ticker_time_utc"]=collector.utc(READY-300001)
        with self.assertRaises(ValueError):
            compare(data)

    def test_future_15m_close_rejected_even_if_data_available_by_decision(self):
        data=sample()
        last=data["candles"][SYMS[0]]["15m"][-1]
        last[0]=BEG//BAR*BAR
        with self.assertRaises(ValueError):
            compare(data)

    def test_data_ready_must_postdate_ticker_fetch(self):
        data=sample()
        data["research_clock_provenance"]["decision_data_ready_utc"]=collector.utc(BEG+300)
        data["collected_utc"]=collector.utc(BEG+300)
        with self.assertRaises(ValueError):
            compare(data)

    def test_master_mode_cannot_be_inferred_from_missing_provenance(self):
        data=sample()
        data.pop("research_clock_provenance")
        with self.assertRaises(ValueError):
            compare(data)

    def test_evaluation_before_ready_must_be_rejected(self):
        with self.assertRaises(ValueError):
            compare(sample(),at_utc=datetime.fromtimestamp(
                (READY-1)/1000,timezone.utc))

    def test_any_order_permission_is_prohibited(self):
        def bad(market,now_utc):
            x=fake_analyze(market,now_utc)
            x["order_allowed"]=True
            return x
        with patch("research.receipt_clock_shadow.analyze",side_effect=bad):
            with self.assertRaises(ValueError):
                compare(sample())


if __name__=="__main__":
    unittest.main()
