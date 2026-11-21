"""Deterministic, synthetic tests for offline history reconstruction."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.market_history_audit import audit, main

DUR = {"15m": 900_000, "1h": 3_600_000, "2h": 7_200_000}


def candle(t, close=100.):
    return [t, 100., max(101., close), min(99., close), close, 10., 1000.]


def snapshot():
    return {"status": "COLLECTED", "market_type": "USDT_PERPETUAL",
            "collected_utc": "1970-01-01T04:00:00+00:00",
            "universe": [{"symbol": "SOL-USDT-SWAP"}],
            "candles": {"SOL-USDT-SWAP": {
                tf: [candle(0), candle(ms)] for tf, ms in DUR.items()}}}


class HistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, name, data):
        p = self.root / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_valid_closed_snapshot(self):
        report, candles = audit([self.save("market_data1.json", snapshot())])
        self.assertTrue(report["integrity_passed"])
        self.assertEqual(len(candles), 6)
        self.assertTrue(all(c["first_seen_utc"] for c in candles))
        self.assertFalse(report["profitability_backtest_ready"])

    def test_overlapping_snapshots_dedup(self):
        a = self.save("market_data1.json", snapshot())
        b = self.save("market_data2.json", snapshot())
        report, candles = audit([a, b])
        self.assertEqual(report["duplicate_observations"], 6)
        self.assertEqual(len(candles), 6)
        self.assertTrue(report["integrity_passed"])

    def test_conflict_is_excluded(self):
        a = self.save("market_data1.json", snapshot())
        changed = snapshot()
        changed["candles"]["SOL-USDT-SWAP"]["15m"][0] = candle(0, 100.5)
        b = self.save("market_data2.json", changed)
        report, candles = audit([a, b])
        self.assertEqual(report["conflicting_candles"], 1)
        self.assertEqual(len(candles), 5)
        self.assertFalse(report["integrity_passed"])

    def test_detects_gap(self):
        s = snapshot()
        s["candles"]["SOL-USDT-SWAP"]["15m"][1] = candle(1_800_000)
        report, _ = audit([self.save("market_data1.json", s)])
        self.assertEqual(report["missing_candles_between_observations"], 1)
        self.assertFalse(report["integrity_passed"])

    def test_rejects_unclosed_candle(self):
        s = snapshot()
        s["candles"]["SOL-USDT-SWAP"]["2h"][1] = candle(14_400_000)
        report, _ = audit([self.save("market_data1.json", s)])
        self.assertEqual(report["issues"]["invalid_candle"], 1)

    def test_rejects_invalid_ohlc(self):
        s = snapshot()
        s["candles"]["SOL-USDT-SWAP"]["15m"][0][2] = 0.1
        report, _ = audit([self.save("market_data1.json", s)])
        self.assertEqual(report["issues"]["invalid_candle"], 1)

    def test_rejects_negative_volume(self):
        s = snapshot()
        s["candles"]["SOL-USDT-SWAP"]["15m"][0][6] = -1.
        report, _ = audit([self.save("market_data1.json", s)])
        self.assertEqual(report["issues"]["invalid_candle"], 1)

    def test_missing_timeframe_is_reported(self):
        s = snapshot()
        del s["candles"]["SOL-USDT-SWAP"]["1h"]
        report, _ = audit([self.save("market_data1.json", s)])
        self.assertEqual(report["issues"]["missing_series"], 1)

    def test_directory_cli_and_jsonl(self):
        self.save("market_data1.json", snapshot())
        report_path = self.root / "audit.json"
        bars = self.root / "candles.jsonl"
        result = main([str(self.root), "--report", str(report_path),
                       "--normalized", str(bars), "--strict"])
        self.assertEqual(result, 0)
        self.assertEqual(len(bars.read_text().splitlines()), 6)

    def test_strict_exit_failure(self):
        s = snapshot()
        s["candles"]["SOL-USDT-SWAP"]["15m"][1] = candle(1_800_000)
        self.save("market_data1.json", s)
        self.assertEqual(main([str(self.root), "--report",
                               str(self.root / "audit.json"), "--strict"]), 2)

    def test_empty_directory_fails(self):
        with self.assertRaises(ValueError):
            audit([self.root])


if __name__ == "__main__":
    unittest.main()
