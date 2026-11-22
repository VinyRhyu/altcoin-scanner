"""End-to-end candidate selection and API budget tests with zero network calls."""
import unittest
from unittest.mock import patch

from research.minute_archive_pilot import run

M=60000


def candidate(stamp=920000):
    return {"symbol":"SOL-USDT-SWAP","method":"BREAKOUT","side":"LONG",
            "signal_close_ms":900000, "first_observed_at_ms":stamp,
            "decision_at_snapshot":"GATILHO_CONDICIONAL",
            "entry":100., "stop":98., "tp1":102.,
            "spread_pct_observed":.025}


def row(ts, o=99, hi=100.5, lo=98.5, cl=100):
    return {"open_ms":ts, "open":o, "high":hi, "low":lo, "close":cl,
            "symbol":"SOL-USDT-SWAP", "timeframe":"1m",
            "source":"okx_history_candles","point_in_time_eligible":False}


class ArchivePilotTests(unittest.TestCase):
    def test_uses_actual_observation_and_never_confirms_exchange_fill(self):
        report_stub={"snapshots_analyzed":100,"confirmed_candidates":1,
                     "candidates":[candidate()],
                     "by_method":{"BREAKOUT":{"conditionally_eligible_at_snapshot":1},
                                  "RETEST":{"conditionally_eligible_at_snapshot":0}}}
        recorded=[]
        def fake_fetch(sym,tf,start,end,max_pages):
            recorded.append((sym,tf,start,end,max_pages))
            bars=[row(960000),row(1020000,100,103,99,102)]
            return {"backtest_ready":False,"candles_retrieved":len(bars),
                    "pages_requested":1},bars
        with patch("research.minute_archive_pilot.inspect_snapshots",return_value=report_stub):
            report, windows=run(["dummy"],fetch_history=fake_fetch)
        self.assertEqual(report["source_snapshot_count"],100)
        self.assertEqual(report["by_method"]["BREAKOUT"]["modelled_exits_not_fills"],1)
        self.assertEqual(report["exchange_fills_verified"],0)
        self.assertFalse(report["proven_expectancy"])
        self.assertEqual(recorded[0][2],960000)
        self.assertTrue(windows[0]["data_retrieved_after_the_fact"])
        self.assertFalse(report["details"][0]["exchange_fill_verified"])

    def test_fails_closed_when_candidate_budget_exceeded(self):
        base=candidate()
        reported={"snapshots_analyzed":1, "confirmed_candidates":2,
                  "candidates":[base,dict(base,method="RETEST")],
                  "by_method":{}}
        with patch("research.minute_archive_pilot.inspect_snapshots",return_value=reported):
            with self.assertRaises(ValueError):
                run(["dummy"],max_candidates=1,fetch_history=lambda *a,**k:None)

    def test_invalid_history_provenance_aborts(self):
        stub={"snapshots_analyzed":1,"confirmed_candidates":1,
              "candidates":[candidate()],"by_method":{}}
        with patch("research.minute_archive_pilot.inspect_snapshots",return_value=stub):
            with self.assertRaises(ValueError):
                run(["dummy"],fetch_history=lambda *args,**kwargs:(
                    {"backtest_ready":True,"candles_retrieved":0},[]))


if __name__=="__main__":
    unittest.main()
