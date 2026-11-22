"""Synthetic tests for isolated, assumption-heavy retrospective pilot."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.futures_scenario import Scenario
from research.retrospective_price_pilot import load_history, pilot

DURATIONS = {"15m": 900000, "1h": 3600000, "2h": 7200000}


def history_fixture():
    return {
        tf: [[i*duration, 100., 101., 99., 100., 10., 1000.]
             for i in range(n)]
        for tf, (duration, n) in {
            "15m": (900000, 420), "1h": (3600000, 115),
            "2h": (7200000, 62)}.items()
    }


def write_history(root, hist):
    for tf, series in hist.items():
        rows = [
            {"source": "okx_history_candles",
             "point_in_time_eligible": False,
             "symbol": "SOL-USDT-SWAP", "timeframe": tf,
             "open_ms": r[0], "open": r[1], "high": r[2],
             "low": r[3], "close": r[4],
             "volume_contracts": r[5], "quote_volume": r[6]}
            for r in series
        ]
        (root / f"sol_{tf}_30d.jsonl").write_text(
            "".join(json.dumps(row)+"\n" for row in rows))


class PricePilotTests(unittest.TestCase):
    def test_loading_offline_history_preserves_contract_volume(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            fixture = history_fixture()
            write_history(root, fixture)
            loaded = load_history(root)
            self.assertEqual(loaded["15m"], fixture["15m"])
            self.assertEqual(loaded["2h"][-1][0], 61*7200000)

    def test_rejects_history_with_gaps(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            hist = history_fixture()
            hist["15m"][10][0] += 900000
            write_history(root, hist)
            with self.assertRaises(ValueError):
                load_history(root)

    def test_rejects_historical_data_mislabelled_as_point_in_time(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, history_fixture())
            file = root / "sol_1h_30d.jsonl"
            rows = [json.loads(x) for x in file.read_text().splitlines()]
            rows[0]["point_in_time_eligible"] = True
            file.write_text("".join(json.dumps(x)+"\n" for x in rows))
            with self.assertRaises(ValueError):
                load_history(root)

    def test_other_swap_symbol_uses_its_own_file_prefix_and_provenance(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            write_history(root, history_fixture())
            for tf in ("15m", "1h", "2h"):
                path = root / f"sol_{tf}_30d.jsonl"
                rows = [json.loads(x) for x in path.read_text().splitlines()]
                for item in rows:
                    item["symbol"] = "BTC-USDT-SWAP"
                (root / f"btc_{tf}_30d.jsonl").write_text(
                    "".join(json.dumps(x)+"\n" for x in rows))
            loaded = load_history(root, symbol="BTC-USDT-SWAP")
            self.assertEqual(loaded, history_fixture())
            with self.assertRaises(ValueError):
                load_history(root, symbol="INVALID")

    def test_cost_stress_same_signals_and_no_real_fills(self):
        sc = Scenario(assumed_spread_pct=.025)
        stress = Scenario(assumed_spread_pct=.08, slippage_bps_side=12.5)
        result = pilot(history_fixture(), ".01", sc,
                       symbol="ETH-USDT-SWAP", stress_scenario=stress)
        self.assertEqual(result["symbol"], "ETH-USDT-SWAP")
        self.assertEqual(result["execution_scenario"]["trades"],
                         result["cost_stress_scenario"]["trades"])
        self.assertFalse(result["proven_expectancy"])

    def test_no_signal_is_not_counted_as_a_trade(self):
        report = pilot(history_fixture(), ".01", Scenario())
        self.assertGreater(report["evaluated_15m_closes"], 0)
        self.assertEqual(report["execution_scenario"]["comparison"]["BREAKOUT"]["candidates"], 0)
        self.assertEqual(report["execution_scenario"]["comparison"]["RETEST"]["candidates"], 0)
        self.assertFalse(report["is_real_backtest"])
        self.assertFalse(report["proven_expectancy"])

    def test_60_day_historical_files_are_supported(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            fixture = history_fixture()
            write_history(root, fixture)
            for tf in ("15m", "1h", "2h"):
                (root / f"sol_{tf}_30d.jsonl").rename(
                    root / f"sol_{tf}_60d.jsonl")
            loaded = load_history(root, days=60)
            self.assertEqual(loaded, fixture)
            with self.assertRaises(FileNotFoundError):
                load_history(root, days=30)

    def test_invalid_historical_window_is_rejected(self):
        for value in (0, -1, 366, 60.0, "60"):
            with self.subTest(days=value):
                with self.assertRaises(ValueError):
                    load_history(".", days=value)

    def test_requires_an_explicit_nonzero_tick_proxy(self):
        with self.assertRaises(ValueError):
            pilot(history_fixture(), "0", Scenario())


if __name__ == "__main__":
    unittest.main()
