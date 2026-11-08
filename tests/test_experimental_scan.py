import unittest

from experimental_scan import analyze, classify, features


def rows(step_ms, down=False):
    data = []
    for i in range(40):
        price = 1.08 - i*0.001 if down else 1.0 + i*0.001
        data.append([1700000000000 + i*step_ms, price, price+0.002,
                     price-0.002, price, 10.0, 100.0])
    return data


def source(symbols=("AAA-USDT-SWAP",), down=False):
    return {
        "market_type": "USDT_PERPETUAL",
        "collected_utc": "2026-11-08T00:00:00+00:00",
        "universe": [{"symbol": s} for s in symbols],
        "candles": {s: {
            "2h": rows(7200000, down),
            "1h": rows(3600000, down),
            "15m": rows(900000, down),
        } for s in symbols},
    }


class ExperimentalScannerTests(unittest.TestCase):
    def test_retains_all_symbols_even_when_one_is_missing_data(self):
        data = source(("AAA-USDT-SWAP", "BBB-USDT-SWAP"))
        del data["candles"]["BBB-USDT-SWAP"]
        result = analyze(data)
        self.assertEqual(result["source_asset_count"], 2)
        self.assertEqual(result["retained_asset_count"], 2)
        self.assertEqual(result["analyzed_asset_count"], 1)
        self.assertEqual(result["unavailable_asset_count"], 1)

    def test_both_directions_always_present_regardless_of_trend(self):
        for down in (False, True):
            data = source(down=down)
            result = analyze(data)
            asset = result["assets"][0]
            self.assertEqual(asset["status"], "analisado")
            self.assertEqual({s["side"] for s in asset["setups"]},
                             {"LONG", "SHORT"})
            self.assertTrue(all(s["trade_signal"] is False
                                for s in asset["setups"]))

    def test_bearish_candle_above_resistance_not_bullish_breakout(self):
        data = source()
        candles = data["candles"]["AAA-USDT-SWAP"]["15m"]
        candles[-1][1] = 1.060
        candles[-1][2] = 1.065
        candles[-1][3] = 1.045
        candles[-1][4] = 1.050
        candles[-1][6] = 500.0
        info = features(data["candles"]["AAA-USDT-SWAP"])
        long = classify(info, "LONG")
        self.assertNotEqual(long["stage"], "rompimento_em_observacao")

    def test_ignores_stale_5m_warning(self):
        data = source()
        fake = {"source_collected_utc": "2020-01-01T00:00:00+00:00",
                "warnings": [{"symbol": "AAA-USDT-SWAP"}]}
        result = analyze(data, fake)
        self.assertFalse(result["early_warning_integrated"])
        self.assertIsNone(result["assets"][0]["alerta_5m_original"])


if __name__ == "__main__":
    unittest.main()
