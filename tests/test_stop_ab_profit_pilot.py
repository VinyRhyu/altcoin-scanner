"""Conservative A/B stop-profit analysis: offline deterministic regression."""
import unittest
from unittest.mock import patch

from research.stop_ab_profit_pilot import (
    _profit_stats, inspect_ab, run,
)

MS = 900000
START = 960000
ALLOW = "GATILHO_CONDICIONAL"


def plan(n=3, side="LONG", method="BREAKOUT", decision=ALLOW,
         idx=1, observed_ms=None):
    close_ms=idx*MS
    return {
        "symbol":"SOL-USDT-SWAP","method":method,"side":side,
        "signal_close_ms":close_ms,
        "first_observed_at_ms":observed_ms or close_ms+20000,
        "entry":100.,
        "stop":96. if n==3 and side=="LONG" else (
               98. if side=="LONG" else (104. if n==3 else 102.)),
        "tp1":108.8 if n==3 and side=="LONG" else (
               104.4 if side=="LONG" else (91.2 if n==3 else 95.6)),
        "decision_at_snapshot":decision,
        "reasons":(["Aguardar preco cruzar gatilho e validar spread/volume novamente"]
                   if decision==ALLOW else ["R:R liquido inferior a 1.6 na entrada exata"]),
        "spread_pct_observed":.02,
        "tick_size_observed":"0.01",
    }


def source(bars, decisions=None):
    rows=[plan(n=bars,side=side,idx=i,decision=decision)
          for i,side,decision in (decisions or [(1,"LONG",ALLOW)])]
    return {"research_stop_bars":bars,"snapshots_analyzed":100,
            "confirmed_candidates":len(rows),"candidates":rows}


def candle(ts,high=100.5,low=98.7,close=100,open_=99):
    return {"symbol":"SOL-USDT-SWAP","timeframe":"1m",
            "source":"okx_history_candles","point_in_time_eligible":False,
            "open_ms":ts,"open":open_,"high":high,"low":low,"close":close}


class StopABProfitTests(unittest.TestCase):
    def test_accepted_same_3bar_candidate_matches_and_2bar_is_not_wider(self):
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[source(3),source(2)]):
            r=inspect_ab(["dummy"])
        self.assertEqual(r["transition_counts"]["both"],1)
        self.assertEqual(len(r["eligible"]),1)
        self.assertEqual(r["a"][r["eligible"][0]]["stop"],96.)
        self.assertEqual(r["b"][r["eligible"][0]]["stop"],98.)

    def test_paired_both_scenarios_share_single_minute_fetch_and_costs(self):
        calls=[]
        def fake_fetch(sym,tf,start,end,max_pages):
            calls.append((sym,tf,start,end,max_pages))
            rows=[candle(start),
                  candle(start+60000,105,99,104,100),
                  candle(start+120000,109,102,108.8,104)]
            return {"backtest_ready":False,"candles_retrieved":len(rows)},rows
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[source(3),source(2)]):
            r=run(["dummy"],fetch_history=fake_fetch)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][2],START)
        self.assertEqual(r["comparison"]["3"]["all"]["modelled_exits_not_fills"],1)
        self.assertEqual(r["comparison"]["2"]["all"]["modelled_exits_not_fills"],1)
        self.assertEqual(r["comparison"]["matched_candidate_subset"]["both_modelled"],1)
        self.assertEqual(r["comparison"]["3"]["all"]["outcomes"]["MODELLED_TP1"],1)
        self.assertEqual(r["comparison"]["2"]["all"]["outcomes"]["MODELLED_TP1"],1)
        self.assertEqual(r["verified_exchange_fills"],0)
        self.assertFalse(r["actual_profit_proven"])
        self.assertFalse(r["new_stop_strategy_approved"])

    def test_2bar_may_legitimately_lose_eligibility_on_net_rr(self):
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[source(3),source(2,decisions=[
                       (1,"LONG","NAO_ENTRAR")])]):
            pre=inspect_ab(["dummy"])
        self.assertEqual(pre["transition_counts"]["only_3bar"],1)

    def test_2bar_can_add_candidate_but_not_bypass_3atr_guard(self):
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[
                       source(3,decisions=[(1,"LONG","NAO_ENTRAR")]),
                       source(2)
                   ]):
            pre=inspect_ab(["dummy"])
        self.assertEqual(pre["transition_counts"]["only_2bar"],1)
        self.assertEqual(len(pre["eligible"]),1)

    def test_budget_is_enforced_before_external_requests(self):
        states=[(1,"LONG",ALLOW),(2,"SHORT",ALLOW)]
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[source(3,states),source(2,states)]):
            with self.assertRaises(ValueError):
                run(["dummy"],max_pairs=1,fetch_history=lambda *a,**k:None)

    def test_wrong_historical_provenance_fails_closed(self):
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[source(3),source(2)]):
            with self.assertRaises(ValueError):
                run(["dummy"],fetch_history=lambda *a,**k:(
                    {"backtest_ready":True,"candles_retrieved":0},[]))

    def test_changed_confirmed_signal_universe_must_fail(self):
        with patch("research.stop_ab_profit_pilot.inspect_snapshots",
                   side_effect=[source(3),source(2,decisions=[(2,"LONG",ALLOW)])]):
            with self.assertRaises(ValueError):
                inspect_ab(["dummy"])

    def test_summary_does_not_assume_no_fill_is_zero_return(self):
        x=_profit_stats([
            {"modelled_execution":False,"net_r_scenario":None,
             "outcome":"NOT_TRIGGERED_DURING_ACTIVE_WINDOW"},
        ])
        self.assertEqual(x["modelled_exits_not_fills"],0)
        self.assertIsNone(x["mean_net_r_modelled_only"])
        self.assertIsNone(x["profit_factor_modelled_only"])
        self.assertFalse(x["usd_amount_is_account_return"])

    def test_missing_full_minute_and_ambiguous_entry_not_profit(self):
        for decision in ("NO_COMPLETE_MINUTE_BEFORE_EXPIRY",
                         "ENTRY_MINUTE_AMBIGUOUS"):
            x=_profit_stats([{"modelled_execution":False,
                              "net_r_scenario":None,"outcome":decision}])
            self.assertEqual(x["modelled_exits_not_fills"],0)
            self.assertEqual(x["censored_or_not_triggered"],1)


if __name__=="__main__":
    unittest.main()
