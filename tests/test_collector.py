import unittest
from collector import parse_closed_candles, choose_universe

class TestCollector(unittest.TestCase):
    def test_closed_and_sorted(self):
        rows = [[str(i * 900000), '2', '3', '1', '2.5', '10', '25'] for i in range(61)]
        result = parse_closed_candles(list(reversed(rows)), 900, 61 * 900000)
        self.assertEqual(len(result), 61)
        self.assertEqual(result[0]['open_ms'], 0)

    def test_reject_gap(self):
        rows = [[str(i * 900000), '2', '3', '1', '2.5', '10', '25'] for i in range(61) if i != 30]
        with self.assertRaises(ValueError):
            parse_closed_candles(rows, 900, 61 * 900000)

    def test_exclude_btc(self):
        t = [{'symbol': 'BTCUSDT', 'turnover24h': '20000000', 'price24hPcnt': '.05', 'lastPrice': '100', 'highPrice24h': '110', 'lowPrice24h': '90'},
             {'symbol': 'SOLUSDT', 'turnover24h': '20000000', 'price24hPcnt': '.05', 'lastPrice': '100', 'highPrice24h': '110', 'lowPrice24h': '90'}]
        self.assertEqual([x['symbol'] for x in choose_universe(t)], ['SOLUSDT'])

if __name__ == '__main__':
    unittest.main()
