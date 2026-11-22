"""Tests use synthetic bars; they do not make profitability claims."""
import unittest

from research.futures_scenario import Scenario, simulate, summarize

D = 900000


def plan(side="LONG", method="BREAKOUT", future=None):
    entry, stop, tp = (100, 97, 103) if side == "LONG" else (100, 103, 97)
    return {"symbol": "SOL-USDT-SWAP", "side": side, "method": method,
            "signal_close_ms": D, "entry": entry, "stop": stop, "tp1": tp,
            "future_15m": future if future is not None else [
                [D, 100, 104, 99, 102, 3, 300]]}


class FuturesScenarioTests(unittest.TestCase):
    def test_first_bar_long_tp_without_stop_is_deterministic(self):
        r = simulate(plan(), Scenario())
        self.assertEqual(r["outcome"], "TP1_TOUCHED")
        self.assertTrue(r["executed"])
        self.assertGreater(r["net_r"], 0)

    def test_first_bar_short_tp_without_stop_is_deterministic(self):
        r = simulate(plan("SHORT", future=[[D, 100, 101, 96, 98, 3, 300]]), Scenario())
        self.assertEqual(r["outcome"], "TP1_TOUCHED")
        self.assertTrue(r["executed"])

    def test_first_bar_stop_when_opened_at_entry_is_deterministic(self):
        r = simulate(plan(future=[[D, 100, 101, 96, 98, 3, 300]]), Scenario())
        self.assertEqual(r["outcome"], "STOP_TOUCHED")
        self.assertLess(r["net_r"], -1)

    def test_both_stop_and_target_is_ambiguous(self):
        r = simulate(plan(future=[[D, 100, 104, 96, 101, 3, 300]]), Scenario())
        self.assertEqual(r["outcome"], "INTRABAR_AMBIGUOUS")
        self.assertFalse(r["executed"])
        self.assertIsNone(r["net_r"])
        self.assertLess(r["worst_case_net_r_scenario"], 0)

    def test_stop_may_precede_pending_trigger(self):
        r = simulate(plan(future=[[D, 99, 101, 96, 100, 3, 300]]), Scenario())
        self.assertEqual(r["outcome"], "INTRABAR_AMBIGUOUS")

    def test_skip_gap_through_exact_trigger(self):
        r = simulate(plan(future=[[D, 101, 104, 100, 103, 3, 300]]), Scenario())
        self.assertEqual(r["outcome"], "GAP_BEYOND_EXACT_ENTRY")
        self.assertFalse(r["executed"])

    def test_expire_stop_after_next_bar(self):
        r = simulate(plan(future=[[D, 99, 99.5, 98.5, 99, 3, 300],
                                  [2*D, 99, 104, 98, 103, 3, 300]]),
                     Scenario())
        self.assertEqual(r["outcome"], "NOT_TRIGGERED_NEXT_15M")

    def test_time_exit_only_when_horizon_complete(self):
        first = [D, 100, 101, 99, 100, 3, 300]
        second = [2*D, 100, 102, 98, 101, 3, 300]
        r = simulate(plan(future=[first, second]), Scenario(max_hold_bars=2))
        self.assertEqual(r["outcome"], "TIME_EXIT")
        self.assertTrue(r["executed"])
        r2 = simulate(plan(future=[first]), Scenario(max_hold_bars=2))
        self.assertEqual(r2["outcome"], "UNRESOLVED_IN_AVAILABLE_WINDOW")

    def test_candle_gap_is_not_filled(self):
        first = [D, 100, 101, 99, 100, 3, 300]
        third = [3*D, 100, 104, 99, 103, 3, 300]
        r = simulate(plan(future=[first, third]), Scenario(max_hold_bars=2))
        self.assertEqual(r["outcome"], "DATA_GAP_OR_INVALID_CANDLE")
        self.assertFalse(r["executed"])

    def test_fees_and_spread_reduce_expected_net_r(self):
        p = plan()
        frictionless = simulate(p, Scenario(fee_bps_side=0,
            slippage_bps_side=0, assumed_spread_pct=0))
        realistic = simulate(p, Scenario())
        self.assertGreater(frictionless["net_r"], realistic["net_r"])

    def test_funding_assumption_has_directional_cost(self):
        long = plan()
        short = plan("SHORT", future=[[D, 100, 101, 96, 98, 3, 300]])
        zero = Scenario(assumed_funding_per_8h=0)
        positive = Scenario(assumed_funding_per_8h=.01)
        self.assertLess(simulate(long, positive)["net_r"], simulate(long, zero)["net_r"])
        self.assertGreater(simulate(short, positive)["net_r"], simulate(short, zero)["net_r"])

    def test_guard_rejects_stop_beyond_approx_liquidation(self):
        p = plan()
        p["stop"] = 90
        r = simulate(p, Scenario(leverage=20))
        self.assertEqual(r["outcome"], "STOP_BEYOND_APPROX_LIQUIDATION")
        self.assertFalse(r["executed"])

    def test_scenario_validation(self):
        with self.assertRaises(ValueError):
            Scenario(leverage=200, maintenance_margin_fraction=.005)
        with self.assertRaises(ValueError):
            Scenario(fee_bps_side=-1)

    def test_non_numeric_or_reversed_stop_fails(self):
        p = plan()
        p["stop"] = 101
        with self.assertRaises(ValueError):
            simulate(p, Scenario())

    def test_breakout_retest_are_separated(self):
        plans = [plan(method="BREAKOUT"),
                 plan(method="RETEST", future=[[D, 101, 104, 100, 103, 3, 300]])]
        r = summarize(plans, Scenario())
        self.assertEqual(r["comparison"]["BREAKOUT"]["scenario_resolved"], 1)
        self.assertEqual(r["comparison"]["RETEST"]["scenario_resolved"], 0)
        self.assertIsNone(r["comparison"]["RETEST"]["mean_net_r_scenario"])
        self.assertFalse(r["proven_expectancy"])
        self.assertFalse(r["is_real_backtest"])


if __name__ == "__main__":
    unittest.main()
