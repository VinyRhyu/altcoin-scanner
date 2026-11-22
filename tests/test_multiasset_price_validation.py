"""Deterministic multiasset profit validation tests; exchange network never used."""
import unittest
from unittest.mock import patch

from research.futures_scenario import Scenario
from research.multiasset_price_validation import (
    BASE, STRESS, PERIOD_START, SPLIT, PERIOD_END, SYMBOLS, BAR_MS, DAY_MS,
    _groups, _stress_for_side, aggregate, analyze_asset,
    conservative_exit,
)


def plan(signal_close, side="LONG", symbol="BTC-USDT-SWAP"):
    first=[signal_close,99.,100.5,98.5,100.,10.,10000.]
    second=[signal_close+BAR_MS,100.,104.5,99.,104.,10.,10000.]
    remaining=[
        [signal_close+i*BAR_MS,104.,105.,103.,104.,10.,10000.]
        for i in range(2,16)
    ]
    return {
        "symbol":symbol,"method":"BREAKOUT","side":side,
        "signal_close_ms":signal_close,"entry":100.,"stop":98.,"tp1":104.,
        "future_15m":[first,second]+remaining,
    }


def fake_history():
    return {"15m":[[PERIOD_START,100,101,99,100,10,1000]],
            "1h":[[PERIOD_START,100,101,99,100,10,1000]],
            "2h":[[PERIOD_START,100,101,99,100,10,1000]]}


def fake_pilot(plan_list):
    return {
        "evaluated_15m_closes":100,
        "detection_summary":{"BREAKOUT":{"confirmed":len(plan_list)},"RETEST":{}},
        "research_only_plans_with_future_bars":plan_list,
    }


class MultiassetTests(unittest.TestCase):
    def test_first_bar_entry_and_target_is_censored_even_without_stop(self):
        p=plan(SPLIT+4*BAR_MS)
        p["future_15m"][0][2]=105.
        r=conservative_exit(p,BASE)
        self.assertFalse(r["executed"])
        self.assertIsNone(r["net_r"])
        self.assertEqual(r["outcome"],"ENTRY_EXIT_SAME_FIRST_15M_AMBIGUOUS")

    def test_target_in_second_bar_is_resolved_only_model(self):
        r=conservative_exit(plan(SPLIT+4*BAR_MS),BASE)
        self.assertTrue(r["executed"])
        self.assertEqual(r["outcome"],"TP1_TOUCHED")
        self.assertGreater(r["net_r"],0)

    def test_gap_after_entry_worsens_stop_fill(self):
        p=plan(SPLIT+4*BAR_MS)
        p["future_15m"][1][1:5]=[96.,97.,95.,96.]
        result=conservative_exit(p,BASE)
        self.assertEqual(result["outcome"],"STOP_TOUCHED")
        self.assertTrue(result.get("adverse_stop_gap_modelled"))
        self.assertEqual(result["raw_exit_price"],96.)
        self.assertLess(result["net_r"],-1.)

    def test_ambiguous_and_untriggered_are_not_0r_wins(self):
        r=_groups([
            {"executed":False,"net_r":None,"outcome":"INTRABAR_AMBIGUOUS",
             "signal_close_ms":SPLIT},
            {"executed":False,"net_r":None,"outcome":"NOT_TRIGGERED_NEXT_15M",
             "signal_close_ms":SPLIT+BAR_MS},
        ])
        self.assertEqual(r["modelled_exits_not_fills"],0)
        self.assertIsNone(r["mean_net_r_modelled_only"])
        self.assertEqual(r["unresolved_or_unfilled"],2)
        self.assertIsNone(r["mean_net_r_cluster_bootstrap_95pct"])

    def test_stress_funding_is_adverse_to_both_sides(self):
        self.assertGreater(_stress_for_side("LONG").assumed_funding_per_8h,0)
        self.assertLess(_stress_for_side("SHORT").assumed_funding_per_8h,0)
        self.assertGreater(STRESS.slippage_bps_side,BASE.slippage_bps_side)

    def test_holdout_boundary_does_not_use_future_prices_in_train(self):
        before=SPLIT-3*BAR_MS
        after=SPLIT+4*BAR_MS
        with patch("research.multiasset_price_validation._validate_full_history"):
            with patch("research.multiasset_price_validation.pilot",
                       return_value=fake_pilot([plan(before),plan(after)])):
                result=analyze_asset(fake_history(),"BTC-USDT-SWAP")
        self.assertEqual(result["candidates_excluded_at_time_boundaries"],
                         {"exploration_would_cross_boundary":1})
        self.assertEqual(result["periods"]["exploration"]["base"]
                         ["overall"]["signals_eligible_and_evaluated"],0)
        self.assertEqual(result["periods"]["temporal_evaluation"]["base"]
                         ["overall"]["modelled_exits_not_fills"],1)
        self.assertFalse(result["net_edge_proven"])
        self.assertEqual(result["historical_fills_verified"],0)

    def test_60d_end_censors_exits_past_historical_window(self):
        too_late=PERIOD_END-5*BAR_MS
        with patch("research.multiasset_price_validation._validate_full_history"):
            with patch("research.multiasset_price_validation.pilot",
                       return_value=fake_pilot([plan(too_late)])):
                r=analyze_asset(fake_history(),"BTC-USDT-SWAP")
        self.assertEqual(r["candidates_excluded_at_time_boundaries"],
                         {"temporal_evaluation_would_cross_boundary":1})
        self.assertEqual(r["periods"]["temporal_evaluation"]["base"]
                         ["overall"]["signals_eligible_and_evaluated"],0)

    def test_missing_period_history_must_fail_closed(self):
        with self.assertRaises(ValueError):
            analyze_asset(fake_history(),"BTC-USDT-SWAP")

    def test_aggregate_requires_all_three_approved_symbols(self):
        with self.assertRaises(ValueError):
            aggregate([])

    def test_costs_reduce_scenario_net_on_same_price_path(self):
        p=plan(SPLIT+4*BAR_MS)
        base=conservative_exit(p,BASE)
        stress=conservative_exit(p,_stress_for_side("LONG"))
        self.assertEqual(base["outcome"],stress["outcome"])
        self.assertLess(stress["net_r"],base["net_r"])

    def test_day_cluster_bootstrap_reproducible_and_empty_with_1_day(self):
        rows=[]
        for i in range(30):
            rows.append({"executed":True,"net_r": 1. if i%3 else -1.,
                         "signal_close_ms":SPLIT+i*DAY_MS,
                         "outcome":"TP1_TOUCHED" if i%3 else "STOP_TOUCHED"})
        a=_groups(rows)
        b=_groups(rows)
        self.assertEqual(a["mean_net_r_cluster_bootstrap_95pct"],
                         b["mean_net_r_cluster_bootstrap_95pct"])
        self.assertEqual(len(a["mean_net_r_cluster_bootstrap_95pct"]),2)
        for r in rows: r["signal_close_ms"]=SPLIT
        self.assertIsNone(_groups(rows)["mean_net_r_cluster_bootstrap_95pct"])


if __name__=="__main__":
    unittest.main()
