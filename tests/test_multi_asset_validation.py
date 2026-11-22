"""Unit tests for three-asset, fixed-window, price-only validation."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import unittest

from research.multi_asset_validation import (
    BASE_SLIPPAGE_BPS, DAY_MS, SYMBOLS,
    _pair_costs, _stats, _utc_ms, multi_asset_report,
)


def tr(symbol,when,net=1.,outcome="TP1_TOUCHED"):
    ok=net is not None
    return {
        "symbol":symbol,"method":"BREAKOUT","side":"LONG",
        "signal_close_ms":when,"outcome":outcome,
        "executed":ok, "net_r":net,
    }


class MultiAssetValidationTests(unittest.TestCase):
    def test_grouped_stats_never_treat_unfilled_as_zero_trades(self):
        rows=[tr("SOL-USDT-SWAP",900000,None,"NOT_TRIGGERED_NEXT_15M")]
        result=_stats(rows)
        self.assertEqual(result["conditional_plans"],1)
        self.assertEqual(result["modelled_resolutions_not_fills"],0)
        self.assertIsNone(result["net_r_scenario_mean_resolved"])
        self.assertTrue(result["resolved_sample_below_100"])
        self.assertEqual(result["exchange_fills_verified"],0)

    def test_rejects_cost_stress_that_increases_net_r(self):
        a=[tr("ETH-USDT-SWAP",900000,1)]
        b=[tr("ETH-USDT-SWAP",900000,1.1)]
        with self.assertRaises(ValueError):
            _pair_costs(a,b)

    def test_rejects_stress_that_changes_selection_or_exit_order(self):
        a=[tr("ETH-USDT-SWAP",900000,1)]
        b=[tr("ETH-USDT-SWAP",900000,None,"NOT_TRIGGERED_NEXT_15M")]
        with self.assertRaises(ValueError):
            _pair_costs(a,b)

    def test_utc_window_boundaries_and_timezone_enforced(self):
        with self.assertRaises(ValueError):
            _utc_ms("2026-08-22T00:00:00")
        with self.assertRaises(ValueError):
            _utc_ms("2026-08-21T21:00:00-03:00")
        with self.assertRaises(ValueError):
            multi_asset_report(".","2026-08-22T00:00:00Z",
                               "2026-11-19T00:00:00Z")

    def test_three_instruments_and_3_period_blocks_pre_registered(self):
        start=_utc_ms("2026-08-22T00:00:00Z")
        end=start+90*DAY_MS
        duration={"15m":900000,"1h":3600000,"2h":7200000}
        hist={
            tf:[[start+i*d,100.,101.,99.,100.,10.,1000.]
                for i in range((end-start)//d)]
            for tf,d in duration.items()
        }
        loaded=[]
        def fake_load(directory,symbol,days):
            loaded.append((symbol,days))
            return hist
        def fake_pilot(history,tick,base,symbol,stress_scenario):
            self.assertEqual(base.slippage_bps_side,BASE_SLIPPAGE_BPS)
            self.assertGreater(stress_scenario.slippage_bps_side,
                               base.slippage_bps_side)
            # One signal per period in each currency, consistently matched.
            cases=[tr(symbol,start+10*DAY_MS,1.1),
                   tr(symbol,start+40*DAY_MS,-1.05),
                   tr(symbol,start+70*DAY_MS,None,"NOT_TRIGGERED_NEXT_15M")]
            stressed=[dict(t,net_r=(t["net_r"]-.2 if t["net_r"] is not None
                                   else None)) for t in cases]
            return {
                "closed_15m_bars":len(hist["15m"]),
                "evaluated_15m_closes":8000,
                "detection_summary":{"BREAKOUT":{"confirmed":20},
                                     "RETEST":{"confirmed":2}},
                "execution_scenario":{"trades":cases},
                "cost_stress_scenario":{"trades":stressed},
            }
        with patch("research.multi_asset_validation.load_history",
                   side_effect=fake_load),patch(
                   "research.multi_asset_validation.pilot",
                   side_effect=fake_pilot):
            r=multi_asset_report(".","2026-08-22T00:00:00Z",
                                  "2026-11-20T00:00:00Z")
        self.assertEqual(set(loaded), {(s,90) for s in SYMBOLS})
        self.assertEqual(len(r["by_30day_block"]),3)
        self.assertEqual(r["aggregated"]["baseline"]["modelled_resolutions_not_fills"],6)
        self.assertEqual(r["aggregated"]["baseline"]["conditional_plans"],9)
        self.assertEqual(r["by_30day_block"]["block_3_days_61_to_90"]
                         ["baseline"]["modelled_resolutions_not_fills"],0)
        self.assertFalse(r["proven_positive_expectancy"])
        self.assertFalse(r["portfolio_pnl_verified"])
        self.assertFalse(r["minimum_100_resolutions_per_asset_reached"])
        self.assertFalse(r["production_changes"])
        self.assertEqual(r["historical_real_fills_verified"],0)


if __name__=="__main__":
    unittest.main()
