"""Offline OKX history retrieval tests; never contact the exchange."""
import unittest

from research.okx_history_backfill import parse_row, retrieve, utc_ms

D = 900000


def candle(ts, close="100", confirm="1"):
    return [str(ts), "100", "102", "98", close, "5", "5", "500", confirm]


class OkxHistoryTests(unittest.TestCase):
    def test_parse_closed_swap_quote_volume(self):
        raw = candle(0)
        raw[5], raw[6], raw[7] = "50", "0.25", "500"
        bar = parse_row(raw, D)
        self.assertEqual(bar["open_ms"], 0)
        self.assertEqual(bar["volume_contracts"], 50)
        self.assertEqual(bar["base_volume"], .25)
        self.assertEqual(bar["quote_volume"], 500)

    def test_ignore_unfinished_bars(self):
        self.assertIsNone(parse_row(candle(0, confirm="0"), D))

    def test_reject_malformed_ohlc(self):
        with self.assertRaises(ValueError):
            parse_row(candle(0, close="110"), D)

    def test_refuse_future_end_time(self):
        with self.assertRaises(ValueError):
            retrieve("SOL-USDT-SWAP", "15m", 0, 3*D, clock=lambda: 2*D)

    def test_bounded_reverse_pagination_and_provenance(self):
        requested = []
        def api(symbol, tf, cursor):
            requested.append(cursor)
            if cursor == 3*D:
                return [candle(2*D), candle(D)]
            if cursor == D:
                return [candle(0)]
            return []
        report, bars = retrieve("SOL-USDT-SWAP", "15m", 0, 3*D, 3,
                                fetch=api, clock=lambda: 9*D)
        self.assertEqual(requested, [3*D, D, 0])
        self.assertEqual([x["open_ms"] for x in bars], [0, D, 2*D])
        self.assertEqual(report["candles_retrieved"], 3)
        self.assertEqual(report["stopped_because"], "exchange_end_of_history")
        self.assertTrue(all(x["point_in_time_eligible"] is False for x in bars))
        self.assertFalse(report["backtest_ready"])

    def test_respects_request_start_and_max_pages(self):
        report, bars = retrieve("SOL-USDT-SWAP", "15m", D, 4*D, max_pages=1,
            fetch=lambda s, tf, after: [candle(3*D), candle(2*D), candle(D), candle(0)],
            clock=lambda: 9*D)
        self.assertEqual([x["open_ms"] for x in bars], [D, 2*D, 3*D])
        self.assertEqual(report["pages_requested"], 1)
        self.assertEqual(report["stopped_because"], "start_reached")

    def test_reject_invalid_pagination(self):
        with self.assertRaises(ValueError):
            retrieve("SOL-USDT-SWAP", "15m", 0, 3*D,
                fetch=lambda s, tf, after: [candle(3*D)],
                clock=lambda: 9*D)

    def test_reject_duplicate_conflict(self):
        with self.assertRaises(ValueError):
            retrieve("SOL-USDT-SWAP", "15m", 0, 3*D,
                fetch=lambda s, tf, after: [candle(D), candle(D, close="101")],
                clock=lambda: 9*D)

    def test_reject_invalid_instrument(self):
        with self.assertRaises(ValueError):
            retrieve("BTC-USDT", "15m", 0, D, clock=lambda: 9*D)

    def test_requires_timezone(self):
        with self.assertRaises(ValueError):
            utc_ms("2026-11-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
