"""Regression tests for continuous OHLCV segments and observed gap context."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.continuous_history import analyze, main, observed_gap_context


def candle(open_ms):
    return [open_ms, 100., 101., 99., 100., 5., 500.]


def at(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def main_snapshot():
    return {
        "status": "COLLECTED", "market_type": "USDT_PERPETUAL",
        "collected_utc": at(18_000_000),
        "universe": [{"symbol": "SOL-USDT-SWAP"}],
        "candles": {"SOL-USDT-SWAP": {
            "15m": [candle(x) for x in (0, 900_000, 11_700_000, 12_600_000)],
            "1h": [candle(x) for x in (0, 3_600_000, 7_200_000, 10_800_000)],
            "2h": [candle(x) for x in (0, 7_200_000)],
        }},
    }


def intermediate(present, collected_ms=8_100_000):
    if not present:
        return {"status": "COLLECTED", "market_type": "USDT_PERPETUAL",
                "collected_utc": at(collected_ms), "universe": [], "candles": {}}
    return {
        "status": "COLLECTED", "market_type": "USDT_PERPETUAL",
        "collected_utc": at(collected_ms),
        "universe": [{"symbol": "SOL-USDT-SWAP"}],
        "candles": {"SOL-USDT-SWAP": {
            "15m": [candle(0), candle(900_000)],
            "1h": [candle(0), candle(3_600_000)],
            "2h": [candle(0)],
        }},
    }


class ContinuousHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, obj):
        path = self.root / name
        path.write_text(json.dumps(obj), encoding="utf-8")
        return path

    def main_only(self, min_bars=2):
        return analyze([self.write("market_data_main.json", main_snapshot())],
                       min_bars=min_bars)

    def test_segments_split_at_missing_candles(self):
        report, rows = self.main_only()
        self.assertEqual(report["segments_total"], 4)
        self.assertEqual(report["gaps_total"], 1)
        self.assertEqual(report["missing_candles_total"], 11)
        self.assertEqual(report["source_distinct_candles"], 10)
        self.assertEqual(len(rows), 10)
        self.assertFalse(report["backtest_ready"])

    def test_never_interpolates_15m_prices(self):
        report, rows = self.main_only()
        times = [row["open_ms"] for row in rows if row["timeframe"] == "15m"]
        self.assertEqual(times, [0, 900_000, 11_700_000, 12_600_000])
        fifteen = [x for x in report["segments"] if x["timeframe"] == "15m"]
        self.assertEqual([x["candles"] for x in fifteen], [2, 2])
        self.assertEqual(len(set(x["segment_id"] for x in fifteen)), 2)

    def test_min_bars_is_applied_per_contiguous_segment(self):
        report, rows = self.main_only(min_bars=3)
        self.assertEqual(report["eligible_segments"], 1)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["timeframe"] == "1h" for row in rows))

    def test_no_snapshot_observed_inside_gap(self):
        report, _ = self.main_only()
        self.assertEqual(report["gaps"][0]["classification"],
                         "no_snapshot_during_missing_interval")

    def test_symbol_absent_in_observed_gap(self):
        self.write("market_data_main.json", main_snapshot())
        self.write("market_data_absent.json", intermediate(False))
        report, _ = analyze([self.root], 2)
        self.assertEqual(report["gaps"][0]["classification"],
                         "symbol_absent_from_snapshots")
        self.assertEqual(report["gaps"][0]["snapshots_without_symbol"], 1)

    def test_symbol_present_but_old_candles_only(self):
        self.write("market_data_main.json", main_snapshot())
        self.write("market_data_present.json", intermediate(True))
        report, _ = analyze([self.root], 2)
        self.assertEqual(report["gaps"][0]["classification"],
                         "symbol_present_in_snapshots")
        self.assertEqual(report["gaps"][0]["snapshots_with_symbol"], 1)
        self.assertEqual(report["gaps_total"], 1)

    def test_mixed_universe_membership(self):
        self.write("market_data_main.json", main_snapshot())
        self.write("market_data_present.json", intermediate(True))
        self.write("market_data_absent.json", intermediate(False, 9_000_000))
        report, _ = analyze([self.root], 2)
        self.assertEqual(report["gaps"][0]["classification"],
                         "mixed_universe_presence")

    def test_excludes_observation_before_first_missing_bar_closed(self):
        context = observed_gap_context("SOL-USDT-SWAP", 1_800_000, 11_700_000,
                                       900_000, [(2_000_000, {"SOL-USDT-SWAP"})])
        self.assertEqual(context["snapshots_with_symbol"], 0)
        self.assertEqual(context["classification"],
                         "no_snapshot_during_missing_interval")

    def test_default_minimum_does_not_claim_short_series_ready(self):
        report, rows = self.main_only(min_bars=100)
        self.assertEqual(report["eligible_segments"], 0)
        self.assertEqual(rows, [])

    def test_cli_exports_clean_segments(self):
        self.write("market_data_main.json", main_snapshot())
        out = self.root / "out"
        result = main([str(self.root), "--report", str(out / "summary.json"),
                       "--segments-jsonl", str(out / "segments.jsonl"),
                       "--candles-jsonl", str(out / "candles.jsonl"),
                       "--min-bars", "2"])
        self.assertEqual(result, 0)
        summary = json.loads((out / "summary.json").read_text())
        self.assertEqual(summary["segments_total"], 4)
        self.assertEqual(len((out / "segments.jsonl").read_text().splitlines()), 4)
        self.assertEqual(len((out / "candles.jsonl").read_text().splitlines()), 10)

    def test_rejects_invalid_threshold(self):
        with self.assertRaises(ValueError):
            analyze([], min_bars=0)


if __name__ == "__main__":
    unittest.main()
