"""Deterministic tests for legacy vs causal RETEST classification."""
import unittest
from unittest.mock import patch

from entry_trigger import is_retest
from experimental_scan import atr14
from research.retest_atr_compare import compare, legacy_is_retest


def candle_cases():
    return (
        ("LONG",[10.02,10.16,10.,10.14,100.,300.],
         [10.03,13.,9.95,12.,100.,150.]),
        ("SHORT",[9.98,10.,9.84,9.86,100.,300.],
         [9.97,10.05,7.,8.,100.,150.]),
    )


class RetestAtrComparisonTests(unittest.TestCase):
    def test_future_retest_range_cannot_change_prior_breakout_test(self):
        for side,prior,last in candle_cases():
            with self.subTest(side=side):
                bars=[[i*900000,10.,10.1,9.9,10.,100.,100.]
                      for i in range(40)]
                bars[-2][1:7]=prior
                bars[-1][1:7]=last
                atr=atr14(bars)
                self.assertFalse(legacy_is_retest(bars,side,atr)[0])
                self.assertTrue(is_retest(bars,side,atr)[0])

    def test_comparison_detects_new_and_removed_retests_without_breakout_change(self):
        breakout={
            "symbol":"AAA-USDT-SWAP","signal_close_ms":900000,"side":"LONG",
            "method":"BREAKOUT","decision_at_snapshot":"NAO_ENTRAR",
        }
        old_retest={
            "symbol":"BBB-USDT-SWAP","signal_close_ms":900000,"side":"LONG",
            "method":"RETEST","decision_at_snapshot":"NAO_ENTRAR",
        }
        new_retest={
            "symbol":"CCC-USDT-SWAP","signal_close_ms":1800000,"side":"SHORT",
            "method":"RETEST","decision_at_snapshot":"GATILHO_CONDICIONAL",
        }
        legacy={
            "snapshots_analyzed":100,"confirmed_candidates":2,
            "candidates":[breakout,old_retest],
        }
        fixed={
            "snapshots_analyzed":100,"confirmed_candidates":2,
            "candidates":[breakout,new_retest],
        }
        with patch("research.retest_atr_compare.inspect_snapshots",
                   side_effect=[legacy,fixed]):
            r=compare(["unused"])
        self.assertEqual(r["breakout_unchanged_count"],1)
        self.assertEqual(r["retest_newly_confirmed_count"],1)
        self.assertEqual(r["retest_no_longer_confirmed_count"],1)
        self.assertEqual(r["retest_fixed"]["conditional"],1)
        self.assertEqual(r["exchange_fills_verified"],0)
        self.assertFalse(r["profitability_proven"])
        self.assertFalse(r["production_data_modified"])

    def test_comparison_fails_if_breakout_changes(self):
        old={"symbol":"AAA-USDT-SWAP","signal_close_ms":900000,
             "side":"LONG","method":"BREAKOUT",
             "decision_at_snapshot":"NAO_ENTRAR"}
        changed=dict(old,decision_at_snapshot="GATILHO_CONDICIONAL")
        legacy={"snapshots_analyzed":1,"confirmed_candidates":1,"candidates":[old]}
        fixed={"snapshots_analyzed":1,"confirmed_candidates":1,"candidates":[changed]}
        with patch("research.retest_atr_compare.inspect_snapshots",
                   side_effect=[legacy,fixed]):
            with self.assertRaises(ValueError):
                compare(["unused"])


if __name__=="__main__":
    unittest.main()
