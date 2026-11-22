"""Exact-stop reconstruction tests, including mirrored LONG/SHORT geometry."""
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research.stop_geometry_audit import audit, stop_components


def flat_bars():
    return [[i*900000,10.,10.1,9.9,10.,100.,100.]
            for i in range(40)]


class StopGeometryTests(unittest.TestCase):
    def test_mirrored_long_short_have_equal_atr_normalized_risk(self):
        bars=flat_bars()
        long=stop_components(bars,"LONG","BREAKOUT",".01",10.2)
        short=stop_components(bars,"SHORT","BREAKOUT",".01",9.8)
        self.assertAlmostEqual(long["risk_atr"],short["risk_atr"],places=8)
        self.assertEqual(long["dominant_stop_anchor"],"recent")
        self.assertEqual(short["dominant_stop_anchor"],"recent")
        self.assertEqual(long["recent_extreme_bar_offset"],"tie")
        self.assertEqual(short["recent_extreme_bar_offset"],"tie")
        self.assertGreater(long["predicted_stop"],0)
        self.assertLess(long["predicted_stop"],10.2)
        self.assertGreater(short["predicted_stop"],9.8)

    def test_recent_high_spike_can_drive_short_risk_above_3atr(self):
        bars=flat_bars()
        bars[-3][2]=12.
        result=stop_components(bars,"SHORT","BREAKOUT",".01",9.8)
        self.assertGreater(result["risk_atr"],3.)
        self.assertEqual(result["dominant_stop_anchor"],"recent")
        self.assertEqual(result["recent_extreme_bar_offset"],"-3")
        self.assertGreater(result["recent_vs_level_extra_risk_atr"],0.)
        self.assertLess(result["level_component_risk_atr"],3.)

    def test_cannot_compute_with_missing_official_tick(self):
        with self.assertRaises(ValueError):
            stop_components(flat_bars(),"SHORT","BREAKOUT",None,9.8)

    def test_audit_reconciles_exact_stops_to_archived_decision(self):
        bars=flat_bars()
        stamp=bars[-1][0]+900000+60000
        candidates=[]
        for side,entry in (("LONG",10.2),("SHORT",9.8)):
            stop=stop_components(bars,side,"BREAKOUT",".01",entry)
            candidates.append({
                "symbol":"AAA-USDT-SWAP","signal_close_ms":bars[-1][0]+900000,
                "side":side,"method":"BREAKOUT",
                "first_observed_at_ms":stamp,"entry":entry,
                "stop":stop["predicted_stop"],"reasons":[],
            })
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_fake.json"
            p.write_text(json.dumps({
                "status":"COLLECTED", "market_type":"USDT_PERPETUAL",
                "collected_utc":datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat(),
                "universe":[{"symbol":"AAA-USDT-SWAP","tick_size":".01"}],
                "candles":{"AAA-USDT-SWAP":{"15m":bars}},
            }))
            stub={"snapshots_analyzed":1,"confirmed_candidates":2,
                  "candidates":candidates}
            with patch("research.stop_geometry_audit.inspect_snapshots",
                       return_value=stub):
                report=audit([str(p)])
        self.assertEqual(report["plan_reconstruction_rows"],2)
        self.assertEqual(report["by_side"]["LONG"]["confirmed_with_valid_plan"],1)
        self.assertEqual(report["by_side"]["SHORT"]["confirmed_with_valid_plan"],1)
        self.assertFalse(report["stop_formula_changed"])
        self.assertFalse(report["risk_limit_changed"])
        self.assertFalse(report["proven_expectancy"])

    def test_mismatched_stop_fails_closed(self):
        bars=flat_bars()
        stamp=bars[-1][0]+900000+60000
        candidate={
            "symbol":"AAA-USDT-SWAP","signal_close_ms":bars[-1][0]+900000,
            "side":"SHORT","method":"BREAKOUT",
            "first_observed_at_ms":stamp,"entry":9.8,"stop":11.,
            "reasons":[],
        }
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_fake.json"
            p.write_text(json.dumps({
                "status":"COLLECTED","market_type":"USDT_PERPETUAL",
                "collected_utc":datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat(),
                "universe":[{"symbol":"AAA-USDT-SWAP","tick_size":".01"}],
                "candles":{"AAA-USDT-SWAP":{"15m":bars}},
            }))
            stub={"snapshots_analyzed":1,"confirmed_candidates":1,
                  "candidates":[candidate]}
            with patch("research.stop_geometry_audit.inspect_snapshots",
                       return_value=stub):
                with self.assertRaises(ValueError):
                    audit([str(p)])


if __name__=="__main__":
    unittest.main()
