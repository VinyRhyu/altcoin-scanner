import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from collector import parse_candles, choose_universe, intraday_anomaly, CRYPTO_BASES
from analyzer import atr, pivots, latest_bos, find_targets, evaluate


class ScannerTests(unittest.TestCase):
    def test_excludes_noncrypto(self):
        for name in ('SOXL','SAMSUNG','SKHYNIX','XAG','XAUT','BTC','ETH'):
            self.assertNotIn(name, CRYPTO_BASES)

    def test_candles_reject_gaps(self):
        raw=[[i*900_000,'1','1.2','0.9','1.1','10','10','15','1'] for i in range(61)]
        raw[20][0]=str(21*900_000)
        with self.assertRaisesRegex(ValueError, 'Missing'):
            parse_candles(raw,'okx_swap','15m',61*900_000)

    def test_candles_remove_unconfirmed(self):
        raw=[[str(i*900_000),'1','1.2','0.9','1.1','10','10','15','1'] for i in range(61)]
        raw.append([str(61*900_000),'1','1.2','0.9','1.1','10','10','15','0'])
        out=parse_candles(raw,'okx_swap','15m',61*900_000)
        self.assertEqual(len(out),61)

    def test_anomaly(self):
        rows=[[i*900_000,1,1.02,0.99,1,1,10] for i in range(60)]
        rows[-1][6]=70
        v,r=intraday_anomaly(rows)
        self.assertEqual(v,7.0)

    def test_discovery_quota_and_no_duplicates(self):
        items=[dict(symbol=str(i),change_24h_pct=(-1)**i*(i+1),turnover_24h_usdt_est=1e8,
                    rel_volume_15m=2 if i%3==0 else 1,rel_range_15m=1) for i in range(30)]
        chosen=choose_universe(items,20)
        self.assertEqual(len(chosen),20)
        self.assertEqual(len({x['symbol'] for x in chosen}),20)
        self.assertTrue({'gainers24h','losers24h','intraday_anomaly'}.issubset({x['group'] for x in chosen}))

    def test_rr_checks_nearest_barrier(self):
        # If no confirmed pivot levels, do not invent TP targets.
        flat=[[i*3600_000,10,10,10,10,1,100] for i in range(80)]
        self.assertIsNone(find_targets(flat,flat,10,'LONG',0.1,0.01))

    def test_stale_signal_fail_closed(self):
        rows={tf:[[i*ms,10,11,9,10,100,1000] for i in range(70)] for tf,ms in [('2h',7200_000),('1h',3600_000),('15m',900_000)]}
        item={'symbol':'SOL-USDT-SWAP','base':'SOL','price':10}
        result=evaluate(item,rows,0)
        self.assertEqual(result['status'],'REJECTED')


if __name__=='__main__':
    unittest.main()



class DetectorScenarioTests(unittest.TestCase):
    @staticmethod
    def synthetic_early_pullback():
        """Artificial LONG breakout/retest with real structural obstacles, never market data."""
        import math
        from analyzer import DUR
        now_ms=1793628400000
        rows={}
        for tf,dur in DUR.items():
            start=(now_ms//dur-99)*dur
            if tf=='2h':
                r=[]
                for i in range(99):
                    c=102+0.5*math.sin(i*.46)
                    hi=c+.2+(7 if i==22 else 8 if i==53 else 0)
                    r.append([start+i*dur,c-.1,hi,c-.2,c,100,20000])
            elif tf=='1h':
                r=[]
                for i in range(99):
                    c=103+0.45*math.sin(i*.6)
                    r.append([start+i*dur,c-.1,c+.2,c-.2,c,100,20000])
                for i,c in {91:103.7,92:104.0,93:104.5,94:103.9,95:104.1,96:104.7,97:105.7,98:105.5}.items():
                    r[i]=[r[i][0],c-.4,105.0 if i==93 else c+.1,c-.5,c,100,20000]
                r[97][6]=65000
            else:
                r=[]
                for i in range(99):
                    c=104.8+.05*math.sin(i*.6)
                    r.append([start+i*dur,c-.03,c+.09,c-.08,c,10,15000])
                for i,(o,h,l,c,v) in {95:(105.0,105.2,104.92,105.1,15000),96:(105.1,105.3,104.98,105.14,16000),97:(105.14,105.4,105.0,105.25,15000),98:(105.25,105.8,105.1,105.5,65000)}.items():
                    r[i]=[r[i][0],o,h,l,c,10,v]
            rows[tf]=r
        return rows,now_ms

    def test_synthetic_valid_early_pullback_is_paper_only(self):
        rows,now_ms=self.synthetic_early_pullback()
        x=evaluate({'symbol':'SOL-USDT-SWAP','base':'SOL','price':105.5},rows,now_ms,report_stale=True)
        self.assertEqual(x['status'],'PAPER_CANDIDATE')
        self.assertEqual(x['direction'],'LONG')
        self.assertGreaterEqual(x['rr_worst_to_tp2'],1.6)
        self.assertGreaterEqual(x['score'],3)
        self.assertTrue(x['entry_min']<=105.5<=x['entry_max'])

    def test_synthetic_late_price_is_rejected(self):
        rows,now_ms=self.synthetic_early_pullback()
        x=evaluate({'symbol':'SOL-USDT-SWAP','base':'SOL','price':107.5},rows,now_ms,report_stale=True)
        self.assertEqual(x['status'],'REJECTED')

    def test_tokenized_stock_rejected_even_with_candles(self):
        rows,now_ms=self.synthetic_early_pullback()
        x=evaluate({'symbol':'SAMSUNG-USDT-SWAP','price':105.5},rows,now_ms,report_stale=True)
        self.assertEqual(x['status'],'REJECTED')
        self.assertIn('Not an eligible cryptocurrency',x['reasons'][0])
