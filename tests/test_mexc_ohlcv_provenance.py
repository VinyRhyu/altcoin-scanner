"""MEXC public OHLCV must preserve venue and candle chronology."""
import json
from io import BytesIO
import unittest

from research.mexc_ohlcv_provenance import (
    retrieve,validate,ENDPOINT,DURATIONS,
)

START=1800000000 // 900 * 900
END=START+3600


def payload(interval="Min15",indexes=(0,1,2,3),*,gap=False):
    step=DURATIONS[interval]
    times=[START+i*step for i in indexes]
    if gap:
        times.remove(START+step)
    n=len(times)
    return {
        "success":True,"code":0,
        "data":{
            "time":times,
            "open":[100]*n,"high":[103]*n,
            "low":[99]*n,"close":[102]*n,
            "vol":[1000]*n,"amount":[100000]*n,
        },
    }


class MexcOHLCVTests(unittest.TestCase):
    def test_valid_closed_mexc_only_bars_no_fills_or_trade_inference(self):
        r=validate("ORCA_USDT","Min15",START,END,payload(),
                   observed_at_s=END+10)
        self.assertEqual(r["completed_bars"],4)
        self.assertEqual(r["missing_contiguous_candles_between_first_last"],0)
        self.assertEqual(r["instrument"],"ORCA_USDT")
        self.assertTrue(r["all_bars_from_mexc_only"])
        self.assertFalse(r["point_in_time_tradable_bid_ask_verified"])
        self.assertFalse(r["funding_fees_liquidation_verified"])
        self.assertEqual(r["signals_or_fills_estimated"],0)
        self.assertFalse(r["profitable_edge_proven"])

    def test_last_unfinished_bar_excluded_no_future_candle(self):
        data=payload()
        # Query end is a closed boundary, but a provider may return an extra
        # candle whose timestamp is still in-range and not yet closed at
        # actual observation: a complete historical query cannot include it.
        # For this case assert hard rejection of a pre-query-end observation.
        with self.assertRaises(ValueError):
            validate("ORCA_USDT","Min15",START,END,data,
                     observed_at_s=START+3*900+100)
        good=validate("ORCA_USDT","Min15",START,END,data,
                      observed_at_s=END)
        self.assertTrue(good["all_bars_closed_at_observation"])

    def test_detect_missing_candle_without_inventing_replacement(self):
        r=validate("ORCA_USDT","Min15",START,END,payload(gap=True),
                   observed_at_s=END)
        self.assertEqual(r["completed_bars"],3)
        self.assertEqual(r["missing_contiguous_candles_between_first_last"],1)

    def test_fail_closed_on_ohlc_high_low_mismatch_or_duplicates(self):
        data=payload()
        data["data"]["high"][1]=101
        with self.assertRaises(ValueError):
            validate("ORCA_USDT","Min15",START,END,data,observed_at_s=END)
        data=payload(indexes=(0,1,1))
        with self.assertRaises(ValueError):
            validate("ORCA_USDT","Min15",START,END,data,observed_at_s=END)

    def test_reject_wrong_venue_invalid_price_and_incomplete_arrays(self):
        data=payload()
        with self.assertRaises(ValueError):
            validate("ORCA-USDT-SWAP","Min15",START,END,data,observed_at_s=END)
        data["data"]["open"][0]=-1
        with self.assertRaises(ValueError):
            validate("ORCA_USDT","Min15",START,END,data,observed_at_s=END)
        data=payload()
        data["data"]["vol"].pop()
        with self.assertRaises(ValueError):
            validate("ORCA_USDT","Min15",START,END,data,observed_at_s=END)

    def test_public_query_uses_seconds_and_no_private_apis(self):
        seen=[]
        def fake(req,timeout):
            seen.append((req.full_url,req.get_method(),timeout,dict(req.header_items())))
            return BytesIO(json.dumps(payload()).encode())
        _=retrieve("ORCA_USDT","Min15",START,END,opener=fake)
        self.assertEqual(len(seen),1)
        self.assertTrue(seen[0][0].startswith(ENDPOINT+"ORCA_USDT?"))
        self.assertIn("start="+str(START),seen[0][0])
        self.assertIn("end="+str(END),seen[0][0])
        self.assertIn("interval=Min15",seen[0][0])
        self.assertEqual(seen[0][1],"GET")
        self.assertFalse(any("authoriz" in k.lower() for k in seen[0][3]))
        with self.assertRaises(ValueError):
            retrieve("BTC_USDT","Min15",START,END,opener=fake)
        with self.assertRaises(ValueError):
            retrieve("ORCA_USDT","Min15",START,START+86401,opener=fake)

if __name__=="__main__":
    unittest.main()
