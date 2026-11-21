"""Regression coverage for entry-candle outcomes in the exploratory replay."""
import unittest

from replay_entry_triggers import forward_outcome


def bar(open_, high, low, close):
    return [0, open_, high, low, close, 1.0, 1.0]


class ReplayOutcomeRegressionTests(unittest.TestCase):
    def test_long_tp1_in_entry_bar_without_stop_is_deterministic(self):
        result = forward_outcome([bar(100, 105, 99.5, 104)], "LONG", 101, 99, 104)
        self.assertEqual(result, {"outcome": "TP1_TOUCHED", "bars_checked": 1})

    def test_short_tp1_in_entry_bar_without_stop_is_deterministic(self):
        result = forward_outcome([bar(100, 100.5, 95, 96)], "SHORT", 99, 101, 96)
        self.assertEqual(result["outcome"], "TP1_TOUCHED")

    def test_stop_in_entry_bar_before_fill_is_ambiguous(self):
        result = forward_outcome([bar(100, 102, 98.5, 100)], "LONG", 101, 99, 104)
        self.assertEqual(result["outcome"], "INTRABAR_AMBIGUOUS")

    def test_stop_from_open_at_trigger_is_deterministic(self):
        result = forward_outcome([bar(101, 102, 98.5, 100)], "LONG", 101, 99, 104)
        self.assertEqual(result["outcome"], "STOP_TOUCHED")

    def test_both_levels_in_same_bar_remains_ambiguous(self):
        result = forward_outcome([bar(100, 105, 98, 102)], "LONG", 101, 99, 104)
        self.assertEqual(result["outcome"], "INTRABAR_AMBIGUOUS")

    def test_no_next_bar_trigger(self):
        result = forward_outcome([bar(100, 100.5, 99.5, 100)], "LONG", 101, 99, 104)
        self.assertEqual(result["outcome"], "NOT_TRIGGERED_NEXT_15M")

    def test_gap_beyond_trigger(self):
        result = forward_outcome([bar(102, 104, 101, 102.5)], "LONG", 101, 99, 104)
        self.assertEqual(result["outcome"], "GAP_BEYOND_EXACT_ENTRY")

    def test_stop_in_second_bar(self):
        future = [bar(100, 102, 99.5, 101.5), bar(101.5, 102, 98, 99)]
        result = forward_outcome(future, "LONG", 101, 99, 104)
        self.assertEqual(result, {"outcome": "STOP_TOUCHED", "bars_checked": 2})

    def test_no_future_data(self):
        self.assertEqual(
            forward_outcome([], "LONG", 101, 99, 104)["outcome"],
            "INSUFFICIENT_FUTURE_DATA")

    def test_unresolved_without_exit(self):
        result = forward_outcome([bar(100, 102, 99.5, 101)], "LONG", 101, 99, 104)
        self.assertEqual(result["outcome"], "UNRESOLVED_IN_AVAILABLE_WINDOW")


if __name__ == "__main__":
    unittest.main()
