"""Research opt-in receipt clock: exact payload, no future quote, provenance."""
from datetime import datetime, timezone
from unittest.mock import patch
import unittest

import collector

NOW=1800000000000
BAR=900000


def public_row(name="SOL-USDT-SWAP",timestamp=NOW+150):
    return {
        "instId":name,"last":"100","open24h":"95",
        "volCcy24h":"50000","bidPx":"99.99","askPx":"100.01",
        "ts":str(timestamp),"high24h":"105","low24h":"90",
    }


def provider(*args,**kwargs):
    if args[0]=="public/instruments":
        return [{"instId":"SOL-USDT-SWAP","state":"live","tickSz":"0.01"}]
    if args[0]=="market/tickers":
        return [public_row()]
    raise AssertionError("unknown provider request")


def aligned(now,tf):
    duration=collector.INTERVALS[tf]*1000
    close=now//duration*duration
    return [[close-duration*(96-i),100.,101.,99.,100.,100.,10000.]
            for i in range(96)]


class ReceiptClockTests(unittest.TestCase):
    def test_default_old_filter_remains_strict_and_unchanged(self):
        with patch.object(collector,"okx",side_effect=provider):
            self.assertEqual(collector.tickers({"SOL"},NOW),[])

    def test_same_payload_newer_than_prefetch_but_not_receipt_is_accepted(self):
        times=iter([NOW+100,NOW+300])
        trace={}
        with patch.object(collector,"okx",side_effect=provider):
            out=collector.tickers({"SOL"},NOW,research_clock_mode="receipt",
                    provenance=trace,clock_ms=lambda:next(times))
        self.assertEqual([x["symbol"] for x in out],["SOL-USDT-SWAP"])
        self.assertEqual(trace["legacy_eligible_tickers"],0)
        self.assertEqual(trace["receipt_eligible_tickers"],1)
        self.assertEqual(trace["shadow_added_symbols"],["SOL-USDT-SWAP"])
        self.assertEqual(trace["shadow_removed_symbols"],[])
        self.assertEqual(trace["maximum_quote_age_seconds"],300)
        self.assertTrue(trace["no_order_execution"])
        self.assertLessEqual(trace["ticker_fetch_started_utc"],
                             trace["ticker_fetch_completed_utc"])

    def test_quote_after_receipt_or_stale_after_5min_still_rejected(self):
        for timestamp in (NOW+400,NOW-300001):
            def mock_api(path,**kwargs):
                return [{"instId":"SOL-USDT-SWAP","state":"live"}] if path=="public/instruments" else [
                    public_row(timestamp=timestamp)]
            times=iter([NOW+100,NOW+300])
            with patch.object(collector,"okx",side_effect=mock_api):
                out=collector.tickers({"SOL"},NOW,research_clock_mode="receipt",
                    clock_ms=lambda:next(times))
            self.assertEqual(out,[])

    def test_old_but_nonexpired_ticks_are_not_created_from_future(self):
        def mock_api(path,**kwargs):
            return [{"instId":"SOL-USDT-SWAP","state":"live"}] if path=="public/instruments" else [
                public_row(timestamp=NOW-299800)]
        times=iter([NOW+100,NOW+300])
        with patch.object(collector,"okx",side_effect=mock_api):
            out=collector.tickers({"SOL"},NOW,research_clock_mode="receipt",
                clock_ms=lambda:next(times))
        self.assertEqual(out,[])  # older than 300s AT RESPONSE, not at scan start

    def test_clock_regression_or_negative_interval_fails_closed(self):
        for stamps in ([NOW-1,NOW+1],[NOW+200,NOW+100]):
            with patch.object(collector,"okx",side_effect=provider):
                it=iter(stamps)
                with self.assertRaises(ValueError):
                    collector.tickers({"SOL"},NOW,research_clock_mode="receipt",
                        clock_ms=lambda:next(it))
        with patch.object(collector,"okx",side_effect=provider):
            with self.assertRaises(ValueError):
                collector.tickers({"SOL"},NOW,research_clock_mode="unverified")

    def test_collect_research_marks_data_ready_after_quote_and_closed_candles(self):
        base=NOW//BAR*BAR+90000
        allowed={"SOL"} | {"ASSET"+str(i) for i in range(14)}
        quotes=[{"symbol":s,"price":100.,"change_24h_pct":5.,
                 "turnover_24h_usdt_est":5000000.,"range_24h_pct":2.,
                 "spread_pct":.02,"ticker_time_utc":collector.utc(base+200),
                 "tick_size":"0.01"} for s in sorted(allowed)]
        def fake_tickers(eligible,now_ms,**kwargs):
            self.assertEqual(now_ms,base)
            self.assertEqual(kwargs["research_clock_mode"],"receipt")
            kwargs["provenance"].update({
                "ticker_fetch_started_utc":collector.utc(base+100),
                "ticker_fetch_completed_utc":collector.utc(base+400),
                "ticker_clock_mode":"receipt_research_only",
                "legacy_eligible_tickers":0,
                "receipt_eligible_tickers":15,
                "shadow_added_symbols":sorted(allowed),
                "shadow_removed_symbols":[],
            })
            return quotes
        with (
            patch.object(collector,"eligible_bases",
                         return_value=(allowed,"test_curated")),
            patch.object(collector,"tickers",side_effect=fake_tickers),
            patch.object(collector,"candles",side_effect=lambda sym,tf,now:aligned(base,tf)),
            patch.object(collector,"validated_liquidity",return_value=(5000000.,"priority")),
            patch.object(collector,"intraday_anomaly",return_value=(2.,2.)),
            patch.object(collector,"prescreen",side_effect=lambda vals:vals),
            patch.object(collector,"choose_universe",side_effect=lambda vals:[{**x,"group":"liquidity_backfill"} for x in vals]),
            patch.object(collector.time,"sleep",return_value=None),
        ):
            r=collector.collect(base,research_clock_mode="receipt",
                                clock_ms=lambda:base+30000)
        self.assertEqual(r["status"],"COLLECTED")
        self.assertFalse(r["signals_allowed"])
        self.assertEqual(len(r["universe"]),15)
        self.assertEqual(r["collected_utc"],collector.utc(base+30000))
        p=r["research_clock_provenance"]
        self.assertEqual(p["decision_data_ready_utc"],r["collected_utc"])
        self.assertTrue(p["collected_at_means_data_ready"])
        self.assertLess(p["ticker_fetch_completed_utc"],p["decision_data_ready_utc"])
        for data in r["candles"].values():
            for tf,bars in data.items():
                duration=collector.INTERVALS[tf]*1000
                self.assertLessEqual(bars[-1][0]+duration,base)
                self.assertLessEqual(bars[-1][0]+duration,base+30000)

    def test_scan_started_before_candle_close_cannot_promote_future_candle(self):
        base=NOW//BAR*BAR+90000
        allowed={"SOL"} | {"ASSET"+str(i) for i in range(14)}
        quotes=[{"symbol":s,"price":100.,"change_24h_pct":5.,
                 "turnover_24h_usdt_est":5000000.,
                 "range_24h_pct":2.,"spread_pct":.02} for s in allowed]
        def fake_tickers(_a,_now,**kwargs):
            kwargs["provenance"]["ticker_fetch_completed_utc"]=collector.utc(base+1000)
            return quotes
        def future_bars(sym,tf,now):
            rows=aligned(base,tf)
            if tf=="15m":
                rows[-1][0]=base//BAR*BAR
            return rows
        with (
            patch.object(collector,"eligible_bases",return_value=(allowed,"test")),
            patch.object(collector,"tickers",side_effect=fake_tickers),
            patch.object(collector,"candles",side_effect=future_bars),
            patch.object(collector,"validated_liquidity",return_value=(5000000.,"priority")),
            patch.object(collector,"intraday_anomaly",return_value=(2.,2.)),
            patch.object(collector,"prescreen",side_effect=lambda vals:vals),
            patch.object(collector,"choose_universe",side_effect=lambda vals:[{**x,"group":"liquidity_backfill"} for x in vals]),
            patch.object(collector.time,"sleep",return_value=None),
        ):
            with self.assertRaises(ValueError):
                collector.collect(base,research_clock_mode="receipt",
                                  clock_ms=lambda:base+10000)


if __name__=="__main__":
    unittest.main()
