"""Shadow-comparison tests: hypothetical filter removal cannot bypass safety."""
import unittest
from unittest.mock import patch

from research.filter_shadow_1m import shadow_compare

OPEN=900000


def candidate(index=1, reason=None):
    allowed=reason is None
    return {
        "symbol":"SOL-USDT-SWAP","method":"BREAKOUT","side":"LONG",
        "signal_close_ms":index*OPEN,
        "first_observed_at_ms":index*OPEN+25000,
        "decision_at_snapshot":"GATILHO_CONDICIONAL" if allowed else "NAO_ENTRAR",
        "entry":100.,"stop":98.,"tp1":102.,"spread_pct_observed":.02,
        "reasons":(["Aguardar preco cruzar gatilho e validar spread/volume novamente"]
                   if allowed else [reason] if isinstance(reason,str) else reason),
    }


def archived(candidates):
    return {"snapshots_analyzed":100,"confirmed_candidates":len(candidates),
            "candidates":candidates}


def candle(ts):
    return {
        "source":"okx_history_candles","point_in_time_eligible":False,
        "symbol":"SOL-USDT-SWAP","timeframe":"1m",
        "open_ms":ts,"open":99.,"high":100.5,"low":98.5,"close":100.,
    }


class FilterShadowTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def fetch(symbol,tf,start,end,max_pages):
            self.calls.append((symbol,tf,start,end,max_pages))
            return {"backtest_ready":False,"candles_retrieved":1,
                    "pages_requested":1},[candle(start)]
        self.fetch=fetch

    def test_sole_pivot_block_added_but_hard_stop_not(self):
        items=[
            candidate(1),
            candidate(2,"pivot estrutural entre entrada e TP1"),
            candidate(3,"stop distante > 3 ATR"),
        ]
        with patch("research.filter_shadow_1m.inspect_snapshots",
                   return_value=archived(items)):
            report=shadow_compare(["unused"],fetch_history=self.fetch)
        self.assertEqual(report["baseline_checked"],1)
        self.assertEqual(report["counterfactual_checked"],1)
        self.assertEqual(len(self.calls),2)
        self.assertEqual(report["shadow_groups"]["pivot_before_tp1"]["candidates"],1)
        self.assertEqual(report["shadow_groups"]["stop_above_3atr"]["candidates"]
                         if "stop_above_3atr" in report["shadow_groups"] else 0,0)
        self.assertEqual(report["actual_exchange_fills_verified"],0)
        self.assertFalse(report["proven_expectancy"])
        self.assertFalse(report["safety_guards_modified"])
        self.assertTrue(any(x["counterfactual_only"] for x in report["details"]))

    def test_overlapping_strategy_blocks_are_not_removed(self):
        both=candidate(1,["pivot estrutural entre entrada e TP1",
                          "1h em tendencia contraria sem virada estrutural"])
        with patch("research.filter_shadow_1m.inspect_snapshots",
                   return_value=archived([both])):
            r=shadow_compare(["unused"],fetch_history=self.fetch)
        self.assertEqual(r["baseline_checked"],0)
        self.assertEqual(r["counterfactual_checked"],0)
        self.assertEqual(self.calls,[])

    def test_budgets_fail_before_network(self):
        items=[candidate(1),candidate(2,"pivot estrutural entre entrada e TP1")]
        with patch("research.filter_shadow_1m.inspect_snapshots",
                   return_value=archived(items)):
            with self.assertRaises(ValueError):
                shadow_compare(["unused"],max_candidates=1,fetch_history=self.fetch)
        self.assertEqual(self.calls,[])

    def test_missing_provenance_from_api_fails_closed(self):
        with patch("research.filter_shadow_1m.inspect_snapshots",
                   return_value=archived([candidate(1)])):
            with self.assertRaises(ValueError):
                shadow_compare(["unused"],fetch_history=lambda *a,**kw:(
                    {"backtest_ready":True,"candles_retrieved":0},[]))

    def test_every_hypothetical_is_separately_identified(self):
        items=[candidate(1),candidate(2,"R:R liquido inferior a 1.6 na entrada exata")]
        with patch("research.filter_shadow_1m.inspect_snapshots",
                   return_value=archived(items)):
            r=shadow_compare(["unused"],fetch_history=self.fetch)
        self.assertEqual(r["shadow_groups"]["rr_below_1_6"]["candidates"],1)
        self.assertEqual(r["details"][0]["original_scan_decision"],"GATILHO_CONDICIONAL")
        self.assertEqual(r["details"][1]["original_scan_decision"],"NAO_ENTRAR")
        self.assertTrue(r["details"][1]["counterfactual_only"])
        self.assertTrue(all(not x["exchange_fill_verified"] for x in r["details"]))


if __name__=="__main__":
    unittest.main()
