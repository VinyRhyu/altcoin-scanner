"""Archived snapshot signal decisions: genuine observation, never assumed fills."""
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.point_in_time_candidates import inspect_snapshots
from test_entry_trigger import prepare_breakout, ticker
from test_experimental_scan import source


def fixture(delay_ms=60000):
    obj = source()
    history, price = prepare_breakout("LONG")
    obj["status"] = "COLLECTED"
    obj["candles"]["AAA-USDT-SWAP"] = history
    obj["universe"][0].update(ticker(price))
    close = history["15m"][-1][0] + 900000
    timestamp = close+delay_ms
    obj["collected_utc"] = datetime.fromtimestamp(
        timestamp / 1000, timezone.utc).isoformat()
    return obj


class ArchivedSignalsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, obj):
        p = self.root / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def test_identifies_real_scan_confirmations_as_candidates_only(self):
        report = inspect_snapshots([self.write("market_data_a.json", fixture())])
        self.assertGreaterEqual(report["confirmed_candidates"], 1)
        self.assertFalse(report["eligible_for_futures_pnl_backtest"])
        breakout = [x for x in report["candidates"] if x["method"] == "BREAKOUT"]
        self.assertTrue(breakout)
        self.assertTrue(all(not x["order_allowed"] and not x["execution_verified"]
                            for x in breakout))
        self.assertTrue(all(x["next_bar_already_in_progress_at_snapshot"]
                            for x in breakout))

    def test_deduplicates_same_closed_candle_by_first_observation(self):
        self.write("market_data_b.json", fixture(120000))
        self.write("market_data_a.json", fixture(60000))
        r = inspect_snapshots([self.root])
        first = min(x["first_observed_at_ms"] for x in r["candidates"])
        self.assertEqual(first, int(
            datetime.fromisoformat(fixture()["collected_utc"]).timestamp()*1000))
        self.assertEqual(r["snapshots_analyzed"], 2)
        self.assertEqual(r["confirmed_candidates"], 1)

    def test_expired_signal_is_not_reinterpreted_as_live(self):
        obj = fixture(16*60000)
        r = inspect_snapshots([self.write("market_data_expired.json", obj)])
        self.assertEqual(r["confirmed_candidates"], 0)
        self.assertEqual(r["issues"]["stale_or_future_15m"], 1)

    def test_bad_snapshot_has_no_signals(self):
        obj = fixture()
        obj["status"] = "ERROR"
        r = inspect_snapshots([self.write("market_data_bad.json", obj)])
        self.assertEqual(r["confirmed_candidates"], 0)
        self.assertEqual(r["issues"]["invalid_snapshot"], 1)


if __name__ == "__main__":
    unittest.main()
