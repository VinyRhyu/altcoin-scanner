"""Synthetic audit tests; filter removals cannot authorize real trades."""
import unittest

from research.filter_ablation import (
    FILTERS, analyze_report, classify_candidate, classify_reason,
)


def candidate(method="BREAKOUT",side="LONG",reason=None,index=1,decision=None):
    if reason is None:
        reasons=["Aguardar preco cruzar gatilho e validar spread/volume novamente"]
        decision=decision or "GATILHO_CONDICIONAL"
    else:
        reasons=reason if isinstance(reason,list) else [reason]
        decision=decision or "NAO_ENTRAR"
    return {"symbol":"SOL-USDT-SWAP","side":side,"method":method,
            "signal_close_ms":index*900000,
            "first_observed_at_ms":index*900000+20000,
            "reasons":reasons,
            "decision_at_snapshot":decision,
            "entry":100., "stop":97., "tp1":105.,
            "spread_pct_observed":.02}


def report(*candidates):
    return {"candidates":list(candidates),
            "confirmed_candidates":len(candidates),"snapshots_analyzed":5}


class FilterAblationTests(unittest.TestCase):
    def test_maps_every_exact_production_filter_reason(self):
        for key,(message,category) in FILTERS.items():
            self.assertEqual(classify_reason(message), key)
            self.assertIn(category,("strategy","risk_guard","data_guard","temporal_guard"))

    def test_single_strategy_blocker_adds_one_hypothetical_candidate(self):
        raw=analyze_report(report(candidate(),candidate(
            reason="pivot estrutural entre entrada e TP1",index=2)))
        self.assertEqual(raw["baseline"]["confirmed"],2)
        self.assertEqual(raw["baseline"]["conditional_baseline"],1)
        self.assertEqual(raw["filters"]["pivot_before_tp1"]["sole_blocker"],1)
        self.assertEqual(raw["hypothetical_single_strategy_filter_removal_candidates"]
                         ["pivot_before_tp1"],1)
        self.assertFalse(raw["profitability_proven"])
        self.assertFalse(raw["production_gates_modified"])

    def test_multiple_strategy_reasons_do_not_inflate_single_ablation(self):
        raw=analyze_report(report(candidate(reason=[
            "pivot estrutural entre entrada e TP1",
            "2h contraria: setup exige analise adicional",
        ])))
        self.assertEqual(raw["multi_blocker_candidates"],1)
        self.assertEqual(raw["filters"]["pivot_before_tp1"]["mentions"],1)
        self.assertEqual(raw["filters"]["pivot_before_tp1"]["sole_blocker"],0)
        self.assertEqual(raw["filters"]["opposed_2h"]["mentions"],1)
        self.assertEqual(raw["filters"]["opposed_2h"]["sole_blocker"],0)

    def test_safety_gate_never_counts_as_safe_filter_removal(self):
        raw=analyze_report(report(candidate(
            reason="cotacao da coleta ja atravessou o preco de gatilho")))
        self.assertEqual(raw["filters"]["price_crossed_trigger"]["mentions"],1)
        self.assertEqual(raw["filters"]["price_crossed_trigger"]["sole_blocker"],1)
        self.assertEqual(raw["filters"]["price_crossed_trigger"]
                         ["would_pass_other_recorded_gates"],0)
        self.assertEqual(sum(raw["hypothetical_single_strategy_filter_removal_candidates"]
                             .values()),0)

    def test_unknown_reason_fails_closed_from_removal(self):
        raw=analyze_report(report(candidate(reason="new unexplained filter")))
        self.assertEqual(raw["filters"]["unknown_reason"]["sole_blocker"],1)
        self.assertEqual(sum(raw["hypothetical_single_strategy_filter_removal_candidates"]
                             .values()),0)

    def test_deduplicates_duplicate_reasons(self):
        c=candidate(reason=["spread acima do limite","spread acima do limite"])
        raw=analyze_report(report(c))
        self.assertEqual(raw["filters"]["spread_above_limit"]["mentions"],1)
        self.assertEqual(raw["filters"]["spread_above_limit"]["sole_blocker"],1)

    def test_method_and_side_are_split(self):
        raw=analyze_report(report(
            candidate(side="LONG"), candidate(side="SHORT",method="RETEST",index=2,
            reason="1h em tendencia contraria sem virada estrutural")))
        self.assertEqual(raw["by_method"]["BREAKOUT"]["confirmed"],1)
        self.assertEqual(raw["by_method"]["RETEST"]["confirmed"],1)
        self.assertEqual(raw["by_side"]["SHORT"]["blocked_baseline"],1)
        self.assertEqual(raw["filters"]["opposed_1h"]["by_method"]["RETEST"],1)
        self.assertEqual(raw["filters"]["opposed_1h"]["by_side"]["SHORT"],1)

    def test_missing_blocked_reason_is_rejected(self):
        with self.assertRaises(ValueError):
            classify_candidate(candidate(reason=[],decision="NAO_ENTRAR"))

    def test_allowed_candidate_must_not_be_reported_with_violated_gate(self):
        with self.assertRaises(ValueError):
            classify_candidate(candidate(reason="spread acima do limite",
                                         decision="GATILHO_CONDICIONAL"))

    def test_refuse_missing_candidates_or_count_mismatch(self):
        with self.assertRaises(ValueError):
            analyze_report({"candidates":[],"confirmed_candidates":3,
                            "snapshots_analyzed":10})

    def test_counterfactuals_remain_unverified_and_not_trade_orders(self):
        raw=analyze_report(report(candidate(
            reason="gatilho distante > 0.9 ATR",index=2)))
        c=raw["counterfactual_exclusive_candidates"]["trigger_far_above_0_9atr"][0]
        self.assertTrue(c["counterfactual_only"])
        self.assertFalse(c["exchange_fill_verified"])
        self.assertEqual(c["entry"],100.)
        self.assertEqual(raw["per_candidate"][0]["outcome_studied"],False)


if __name__=="__main__":
    unittest.main()
