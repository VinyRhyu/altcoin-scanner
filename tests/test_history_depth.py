"""Coverage metrics are derived from validated continuous segments only."""
import unittest

from research.history_depth import coverage


def fixture():
    return {"source_snapshot_count": 2, "source_integrity_passed": False,
            "segments": [
                {"symbol": "AAA-USDT-SWAP", "timeframe": "15m",
                 "start_open_ms": 0, "end_open_ms": 900000*95, "candles": 96},
                {"symbol": "AAA-USDT-SWAP", "timeframe": "15m",
                 "start_open_ms": 900000*100, "end_open_ms": 900000*199, "candles": 100},
                {"symbol": "BBB-USDT-SWAP", "timeframe": "1h",
                 "start_open_ms": 0, "end_open_ms": 3600000*23, "candles": 24},
            ]}


class DepthTests(unittest.TestCase):
    def test_distinguishes_calendar_length_from_aggregate_hours(self):
        report = coverage(fixture())
        tf = report["timeframes"]["15m"]
        self.assertEqual(tf["segments"], 2)
        self.assertEqual(tf["symbols"], 1)
        self.assertEqual(tf["longest_segment_hours"], 25)
        self.assertEqual(tf["total_segment_hours"], 49)
        self.assertFalse(report["profitability_backtest_ready"])

    def test_counts_by_symbol_and_timeframe(self):
        report = coverage(fixture())
        self.assertEqual(report["timeframes"]["1h"]["total_candles"], 24)
        self.assertEqual(report["timeframes"]["2h"]["segments"], 0)
        self.assertEqual(report["symbol_depth"][0]["longest_contiguous_hours"]["15m"], 25)
        self.assertEqual(report["symbol_depth"][1]["longest_contiguous_hours"]["1h"], 24)

    def test_rejects_false_continuity(self):
        raw = fixture()
        raw["segments"][0]["end_open_ms"] += 900000
        with self.assertRaises(ValueError):
            coverage(raw)


if __name__ == "__main__":
    unittest.main()
