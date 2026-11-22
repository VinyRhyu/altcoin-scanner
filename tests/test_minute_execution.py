"""Unit regression for post-snapshot minute-bar classification and safety."""
import unittest

from research.futures_scenario import Scenario
from research.minute_execution import (
    MINUTE_MS, evaluate, first_full_minute, summarize,
)

CLOSE = 900_000
OBSERVED = 920_000
START = 960_000


def candidate(side="LONG", method="BREAKOUT", spread=.02):
    return {"symbol": "SOL-USDT-SWAP", "side": side, "method": method,
            "signal_close_ms": CLOSE, "first_observed_at_ms": OBSERVED,
            "decision_at_snapshot": "GATILHO_CONDICIONAL",
            "entry": 100., "stop": 98. if side=="LONG" else 102.,
            "tp1": 102. if side=="LONG" else 98.,
            "spread_pct_observed": spread}


def bar(ts, o=99., h=100.5, l=98.5, c=100.):
    return {"open_ms": ts, "open": o, "high": h, "low": l,
            "close": c, "symbol": "SOL-USDT-SWAP", "timeframe": "1m",
            "source": "okx_history_candles",
            "point_in_time_eligible": False}


class MinuteExecutionTests(unittest.TestCase):
    def test_ceil_skips_partially_observed_minute(self):
        self.assertEqual(first_full_minute(OBSERVED), START)
        self.assertEqual(first_full_minute(START), START)

    def test_long_target_on_second_minute_is_only_modelled(self):
        rows = [bar(START), bar(START+MINUTE_MS, 100, 102.5, 99, 102)]
        result = evaluate(candidate(), rows)
        self.assertEqual(result["outcome"], "MODELLED_TP1")
        self.assertTrue(result["modelled_execution"])
        self.assertFalse(result["exchange_fill_verified"])
        self.assertGreater(result["net_r_scenario"], 0)
        self.assertEqual(result["applied_snapshot_spread_pct"], .02)

    def test_short_target_on_second_minute(self):
        rows = [bar(START, 101,101.2,99.5,100),
                bar(START+MINUTE_MS,100,100.5,97.5,98.5)]
        result = evaluate(candidate("SHORT"),rows)
        self.assertEqual(result["outcome"], "MODELLED_TP1")
        self.assertFalse(result["exchange_fill_verified"])

    def test_partial_minute_before_snapshot_never_counts(self):
        rows=[bar(CLOSE,100,105,97,103)] + [
            bar(START+i*MINUTE_MS,99,99.5,98.5,99)
            for i in range(14)]
        result = evaluate(candidate(), rows)
        self.assertEqual(result["outcome"], "NOT_TRIGGERED_DURING_ACTIVE_WINDOW")

    def test_entry_minute_touch_target_is_ambiguous(self):
        result = evaluate(candidate(), [
            bar(START,99,102.5,99,102)])
        self.assertEqual(result["outcome"], "ENTRY_MINUTE_AMBIGUOUS")
        self.assertFalse(result["modelled_execution"])

    def test_entry_minute_touch_stop_is_ambiguous(self):
        result = evaluate(candidate(), [
            bar(START,99,100.5,97,99)])
        self.assertEqual(result["outcome"], "ENTRY_MINUTE_AMBIGUOUS")
        self.assertLess(result["worst_case_net_r_scenario"], 0)

    def test_both_barriers_after_entry_are_ambiguous(self):
        result = evaluate(candidate(), [
            bar(START),bar(START+MINUTE_MS,100,103,97,100)])
        self.assertEqual(result["outcome"], "EXIT_MINUTE_AMBIGUOUS")
        self.assertFalse(result["modelled_execution"])

    def test_gap_past_entry_does_not_assume_fill_at_entry(self):
        result = evaluate(candidate(), [
            bar(START,101,102,100.5,101)])
        self.assertEqual(result["outcome"], "GAP_BEYOND_EXACT_TRIGGER")

    def test_better_than_stop_is_not_assumed_on_gap_down(self):
        result = evaluate(candidate(), [
            bar(START),bar(START+MINUTE_MS,97,100,96,98)])
        self.assertEqual(result["outcome"], "MODELLED_STOP")
        self.assertEqual(result["raw_exit_price"], 97.)
        self.assertLess(result["net_r_scenario"], -1)

    def test_minute_data_gap_fails_closed(self):
        result = evaluate(candidate(), [
            bar(START), bar(START+2*MINUTE_MS,100,102.5,99,102)])
        self.assertEqual(result["outcome"], "MINUTE_DATA_GAP")
        self.assertFalse(result["modelled_execution"])

    def test_missing_minute_source_provenance_fails_closed(self):
        row = bar(START)
        row["point_in_time_eligible"] = True
        result = evaluate(candidate(), [row])
        self.assertEqual(result["outcome"], "INVALID_OR_UNVERIFIED_MINUTE_DATA")

    def test_missing_spread_does_not_create_pnl(self):
        result = evaluate(candidate(spread=None), [bar(START)])
        self.assertEqual(result["outcome"], "MISSING_SNAPSHOT_SPREAD")

    def test_expired_snapshot_never_arms(self):
        p=candidate()
        p["first_observed_at_ms"]=CLOSE+900_000
        result = evaluate(p,[bar(START)])
        self.assertEqual(result["outcome"], "INVALID_OR_EXPIRED_OBSERVATION")

    def test_no_whole_minute_before_expiry(self):
        p=candidate()
        p["first_observed_at_ms"]=CLOSE+900_000-20_000
        result = evaluate(p,[bar(START)])
        self.assertEqual(result["outcome"], "NO_COMPLETE_MINUTE_BEFORE_EXPIRY")

    def test_stop_beyond_approx_liquidation_rejected(self):
        p=candidate()
        p["stop"]=90
        result = evaluate(p,[bar(START)], Scenario(leverage=20))
        self.assertEqual(result["outcome"], "STOP_BEYOND_APPROX_LIQUIDATION")

    def test_end_of_partial_history_not_counted_as_no_trigger(self):
        result = evaluate(candidate(),[bar(START,99,99.5,98.5,99)])
        self.assertEqual(result["outcome"], "INSUFFICIENT_MINUTE_DATA_FOR_TRIGGER")

    def test_after_entry_missing_future_is_censored(self):
        result = evaluate(candidate(),[bar(START)])
        self.assertEqual(result["outcome"], "INSUFFICIENT_MINUTE_DATA_FOR_EXIT")

    def test_blocked_snapshot_is_not_retroactively_eligible(self):
        p=candidate()
        p["decision_at_snapshot"]="NAO_ENTRAR"
        self.assertEqual(evaluate(p,[bar(START)])["outcome"],
                         "BLOCKED_IN_ARCHIVED_SCAN")

    def test_comparison_separates_breakout_and_retest_and_never_verifies_fill(self):
        plans=[candidate("LONG","BREAKOUT"),candidate("SHORT","RETEST")]
        results=[evaluate(plans[0],[bar(START),bar(START+MINUTE_MS,100,102.5,99,102)]),
                 evaluate(plans[1],[bar(START,101,101.2,99.5,100),
                                   bar(START+MINUTE_MS,100,100.5,97.5,98.5)])]
        r=summarize(plans,results)
        self.assertEqual(r["by_method"]["BREAKOUT"]["modelled_exits_not_fills"],1)
        self.assertEqual(r["by_method"]["RETEST"]["modelled_exits_not_fills"],1)
        self.assertEqual(r["exchange_fills_verified"],0)
        self.assertFalse(r["trading_allowed"])
        self.assertFalse(r["proven_expectancy"])


if __name__ == "__main__":
    unittest.main()
