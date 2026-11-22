"""Guard tests for bulk-collection timestamp versus nominal schedule."""
from datetime import datetime,timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.scan_schedule_audit import audit, _bucket

CLOSE=288000000
BAR=900000


def snap(lag,mode="normal",diverge=False):
    series={}
    universe=[]
    for i,symbol in enumerate(("AAA-USDT-SWAP","BBB-USDT-SWAP","CCC-USDT-SWAP")):
        universe.append({"symbol":symbol})
        open_ms=CLOSE-BAR-(BAR if diverge and i==2 else 0)
        series[symbol]={"15m":[[open_ms,100.,101.,99.,100.,10.,1000.]]}
    return {
        "status":"COLLECTED" if mode=="normal" else "FAILED",
        "market_type":"USDT_PERPETUAL",
        "collected_utc":datetime.fromtimestamp(
            (CLOSE+lag*1000)/1000,timezone.utc).isoformat(),
        "universe":universe,"candles":series,
    }


class ScanScheduleAuditTests(unittest.TestCase):
    def test_nominal_and_delayed_collection_quantiles_are_calculated(self):
        with TemporaryDirectory() as td:
            for i,lag in enumerate((60,240,650)):
                Path(td,f"market_data_{i}.json").write_text(json.dumps(
                    snap(lag,diverge=(i==2))))
            r=audit([td])
        self.assertEqual(r["evaluated_archived_collections"],3)
        self.assertEqual(r["median_collected_lag_seconds"],240.)
        self.assertEqual(r["p90_collected_lag_seconds"],650.)
        self.assertEqual(r["median_excess_vs_nominal_schedule_seconds"],180.)
        self.assertEqual(r["collections_at_or_before_nominal_60s"],1)
        self.assertEqual(r["collections_more_than_3m_after_close"],2)
        self.assertEqual(r["collections_more_than_10m_after_close"],1)
        self.assertEqual(r["collections_with_divergent_15m_close"],1)
        self.assertFalse(r["root_cause_of_delay_verified"])
        self.assertFalse(r["delivery_latency_measured"])
        self.assertFalse(r["production_changed"])

    def test_mixed_or_invalid_snapshots_are_not_invented(self):
        with TemporaryDirectory() as td:
            Path(td,"market_data_bad.json").write_text(json.dumps(snap(1000)))
            Path(td,"market_data_failed.json").write_text(json.dumps(
                snap(200,mode="failed")))
            r=audit([td])
        self.assertEqual(r["evaluated_archived_collections"],0)
        self.assertIsNone(r["median_collected_lag_seconds"])
        self.assertEqual(r["excluded_snapshot_counts"],{
            "invalid_status_or_market":1,
            "stale_or_future_dominant_15m":1,
        })

    def test_group_boundaries_and_invalid_lag(self):
        self.assertEqual(_bucket(179.999),"under_3m")
        self.assertEqual(_bucket(180),"3_to_under_6m")
        self.assertEqual(_bucket(360),"6_to_under_10m")
        self.assertEqual(_bucket(600),"10_to_under_15m")
        with self.assertRaises(ValueError):
            _bucket(900)


if __name__=="__main__":
    unittest.main()
