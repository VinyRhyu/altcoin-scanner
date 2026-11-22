"""Safety and accounting tests for loss decomposition of historical 15m scenarios."""
import unittest

from research.multiasset_price_validation import (
    PERIOD_START, SPLIT, PERIOD_END, SYMBOLS,
)
from research.retrospective_loss_attribution import (
    analyze_rows, diagnose,
)


def trade(symbol="BTC-USDT-SWAP",side="LONG",outcome="TIME_EXIT",
          ret=-.3,stress=-.5,period="temporal_evaluation"):
    return {
        "symbol":symbol,"method":"BREAKOUT","side":side,
        "period":period,
        "signal_close_ms":SPLIT+60*900000 if period=="temporal_evaluation"
            else PERIOD_START+60*900000,
        "entry_proxy":100.,"stop_proxy":98. if side=="LONG" else 102.,
        "tp1_proxy":104. if side=="LONG" else 96.,
        "exchange_fill_verified":False,
        "base":{"outcome":outcome,"executed":True,
                "net_r":ret,"raw_exit_price":101. if side=="LONG" else 99.,
                "bars_to_exit":16},
        "stress":{"outcome":outcome,"executed":True,"net_r":stress,
                  "raw_exit_price":101. if side=="LONG" else 99.,
                  "bars_to_exit":16},
    }


def report(symbol, trades):
    evaluation=[t for t in trades if t["period"]=="temporal_evaluation"]
    exploration=[t for t in trades if t["period"]=="exploration"]
    return {
        "symbol":symbol,"start_ms":PERIOD_START,"split_ms":SPLIT,
        "end_ms":PERIOD_END,
        "net_edge_proven":False,"historical_fills_verified":0,
        "trades_scenarios":trades,
        "periods":{
            "exploration":{"base":{"overall":{
                "modelled_exits_not_fills":sum(t["base"]["executed"] for t in exploration)}}},
            "temporal_evaluation":{"base":{"overall":{
                "modelled_exits_not_fills":sum(t["base"]["executed"] for t in evaluation)}}},
        },
    }


class AttributionTests(unittest.TestCase):
    def test_loss_sum_breaks_out_time_stop_and_target(self):
        first=trade(ret=-.3,stress=-.5)
        stopped=trade(outcome="STOP_TOUCHED",ret=-1.2,stress=-1.4)
        target=trade(outcome="TP1_TOUCHED",ret=2.,stress=1.8)
        r=analyze_rows([first,stopped,target])
        self.assertEqual(r["modelled_exits"],3)
        self.assertEqual(r["overall"]["by_exit"]["TIME_EXIT"]["resolved"],1)
        self.assertEqual(r["overall"]["by_exit"]["STOP_TOUCHED"]["resolved"],1)
        self.assertEqual(r["overall"]["by_exit"]["TP1_TOUCHED"]["resolved"],1)
        self.assertAlmostEqual(r["overall"]["modelled_net_r"]["sum"],.5,places=8)
        self.assertEqual(r["actual_verified_exchange_fills"],0)

    def test_nontrigger_and_ambiguous_do_not_contribute_return(self):
        r=trade()
        r["base"]={"outcome":"NOT_TRIGGERED_NEXT_15M","executed":False,"net_r":None}
        r["stress"]={"outcome":"NOT_TRIGGERED_NEXT_15M","executed":False,"net_r":None}
        out=analyze_rows([r])
        self.assertEqual(out["modelled_exits"],0)
        self.assertEqual(out["nonexecutions_or_ambiguous"],1)
        self.assertIsNone(out["overall"]["modelled_net_r"]["mean"])

    def test_remaining_tp_fraction_for_time_exit_is_descriptive(self):
        r=analyze_rows([trade()])
        self.assertEqual(r["overall"]["by_exit"]["TIME_EXIT"]
                         ["remaining_tp_fraction_at_close"]["mean"],.75)
        self.assertEqual(r["overall"]["by_exit"]["TIME_EXIT"]
                         ["mean_exit_price_move_r_before_costs"],.5)

    def test_cost_flip_is_detected_as_stress_only(self):
        r=analyze_rows([trade(ret=.02,stress=-.1)])
        self.assertEqual(r["overall"]["positive_becoming_negative_under_stress"],1)
        self.assertGreater(r["overall"]["extra_cost_penalty_r"]["sum"],0)

    def test_adverse_cost_improving_pnl_fails(self):
        with self.assertRaises(ValueError):
            analyze_rows([trade(ret=-.2,stress=0.)])

    def test_actual_fill_claim_fails(self):
        r=trade()
        r["exchange_fill_verified"]=True
        with self.assertRaises(ValueError):
            analyze_rows([r])

    def test_no_real_exchange_fill_when_aggregating_predeclared_symbols(self):
        sources=[report(symbol,[trade(symbol=symbol)] if i==0 else [])
                 for i,symbol in enumerate(SYMBOLS)]
        r=diagnose(sources)
        self.assertEqual(r["exploratory_and_temporal_evaluation"]
                         ["temporal_evaluation"]["modelled_exits"],1)
        self.assertFalse(r["causal_loss_attribution_proven"])
        self.assertEqual(r["actual_verified_exchange_fills"],0)
        self.assertFalse(r["profitable_edge_proven"])

    def test_missing_asset_or_changed_period_fails_closed(self):
        sources=[report(s,[]) for s in SYMBOLS]
        with self.assertRaises(ValueError):
            diagnose(sources[:2])
        sources[2]["split_ms"]+=900000
        with self.assertRaises(ValueError):
            diagnose(sources)

    def test_boundary_crossing_label_mismatch_fails(self):
        sources=[report(s,[]) for s in SYMBOLS]
        t=trade(symbol=SYMBOLS[0],period="exploration")
        t["signal_close_ms"]=SPLIT+900000
        sources[0]=report(SYMBOLS[0],[t])
        with self.assertRaises(ValueError):
            diagnose(sources)

    def test_accounting_mismatch_fails(self):
        sources=[report(s,[]) for s in SYMBOLS]
        sources[0]["periods"]["temporal_evaluation"]["base"]["overall"][
            "modelled_exits_not_fills"]=5
        with self.assertRaises(ValueError):
            diagnose(sources)


if __name__=="__main__":
    unittest.main()
