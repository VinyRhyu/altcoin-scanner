"""Compare independent read-only gate accounting to the unchanged collector."""
from unittest.mock import patch
import unittest

import collector
from research.okx_ticker_eligibility_funnel import inspect,live_read,REASONS

NOW=1800000000000


def row(symbol,**edits):
    base={
        "instId":symbol,"last":"100","open24h":"95",
        "volCcy24h":"50000","bidPx":"99.99","askPx":"100.01",
        "ts":str(NOW-10000),"high24h":"105","low24h":"90",
    }
    base.update(edits)
    return base


class TickerFunnelTests(unittest.TestCase):
    def test_exclusive_reasons_match_original_filter_and_dont_change_thresholds(self):
        symbols=(
            "SOL-USDT-SWAP","XRP-USDT-SWAP","ADA-USDT-SWAP",
            "DOGE-USDT-SWAP","BNB-USDT-SWAP","LINK-USDT-SWAP",
            "AVAX-USDT-SWAP","SUI-USDT-SWAP","TRX-USDT-SWAP",
            "BTC-USDT-SWAP",
        )
        live_instruments=[{"instId":sym,"state":"live"} for sym in symbols
                          if not sym.startswith("SUI")]
        raw=[
            row(symbols[0]),
            row(symbols[1],askPx="101.00"),
            row(symbols[2],ts=str(NOW-400000)),
            row(symbols[3],volCcy24h="1"),
            row(symbols[4],high24h="99"),
            row(symbols[5],bidPx="-1"),
            row(symbols[6],last="-1"),
            row(symbols[7]),
            row(symbols[8]),
            row(symbols[9]),
            row("SOLUSDT"),
        ]
        allowed=set(("SOL","XRP","ADA","DOGE","BNB","LINK","AVAX",
                     "SUI","BTC"))
        audit=inspect(live_instruments,raw,allowed,NOW)
        reasons=audit["first_rejection_reason_counts"]
        self.assertEqual(audit["source_total_public_tickers"],11)
        self.assertEqual(sum(reasons.values()),11)
        self.assertEqual(reasons["eligible_ticker"],1)
        self.assertEqual(audit["ticker_eligible_symbols_public_only"],
                         ["SOL-USDT-SWAP"])
        for category in ("spread_over_0_25_pct",
                         "stale_or_future_exchange_ticker",
                         "volume_below_minimum","invalid_daily_range",
                         "invalid_bid_ask","invalid_price_open_or_turnover",
                         "not_live_in_instruments",
                         "outside_curated_or_live_top100",
                         "blocked_base","non_usdt_perpetual"):
            self.assertEqual(reasons[category],1,category)
        with patch.object(collector,"okx",side_effect=[live_instruments,raw]):
            current=collector.tickers(allowed,NOW)
        self.assertEqual({x["symbol"] for x in current},
                         set(audit["ticker_eligible_symbols_public_only"]))
        self.assertFalse(audit["filter_thresholds_changed"])
        self.assertFalse(audit["order_placement_enabled"])
        self.assertFalse(audit["full_candle_validation_performed"])

    def test_failure_closed_on_duplicate_instruments_or_tickers(self):
        with self.assertRaises(ValueError):
            inspect([{"instId":"SOL-USDT-SWAP","state":"live"}]*2,[],set(),NOW)
        with self.assertRaises(ValueError):
            inspect([], [row("SOL-USDT-SWAP")]*2,set(),NOW)

    def test_stale_and_future_tickers_not_accepted(self):
        inst=[{"instId":"SOL-USDT-SWAP","state":"live"}]
        for stamp in (NOW+1,NOW-300001):
            x=inspect(inst,[row("SOL-USDT-SWAP",ts=str(stamp))],{"SOL"},NOW)
            self.assertEqual(x["first_rejection_reason_counts"][
                "stale_or_future_exchange_ticker"],1)
            self.assertEqual(x["ticker_eligible_count"],0)

    def test_public_fetch_shares_original_allowlist_but_no_orders(self):
        inst=[{"instId":"SOL-USDT-SWAP","state":"live"}]
        calls=[]
        def fake_fetch(path,**kw):
            calls.append((path,kw))
            return inst if path=="public/instruments" else [row("SOL-USDT-SWAP")]
        with patch.object(collector,"eligible_bases",
                          return_value=({"SOL"},"curated_test_source")):
            a=live_read(ticker_fetch=fake_fetch,clock_ms=lambda:NOW)
        self.assertEqual(len(calls),2)
        self.assertEqual(a["ticker_eligible_count"],1)
        self.assertEqual(a["allowlist_source"],"curated_test_source")
        self.assertFalse(a["personal_trades_recorded"])
        self.assertFalse(a["production_changed"])
        self.assertEqual(len(REASONS),12)


if __name__=="__main__":
    unittest.main()
