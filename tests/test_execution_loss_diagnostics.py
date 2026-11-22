"""Stage 1+2: offline regression for real-snapshot 1m loss diagnostics."""
import unittest
from unittest.mock import patch

from research.execution_loss_diagnostics import (
    _group, _risk_interiors, diagnose,
)
from research.minute_execution import evaluate

M=60000
S=900000
SIGNAL=S
OBS=S+20000
START=S+M


def candidate(observed=OBS, side="LONG", method="BREAKOUT"):
    return {
        "symbol":"SOL-USDT-SWAP","side":side,"method":method,
        "signal_close_ms":SIGNAL,
        "first_observed_at_ms":observed,
        "decision_at_snapshot":"GATILHO_CONDICIONAL",
        "entry":100.,"stop":98. if side=="LONG" else 102.,
        "tp1":104. if side=="LONG" else 96.,
        "spread_pct_observed":.02,"tick_size_observed":".01",
        "reasons":["Aguardar preco cruzar gatilho e validar spread/volume novamente"],
    }


def bar(ts,o,hi,lo,c):
    return {"source":"okx_history_candles",
        "point_in_time_eligible":False,"symbol":"SOL-USDT-SWAP",
        "timeframe":"1m","open_ms":ts,
        "open":o,"high":hi,"low":lo,"close":c}


def source(c):
    return {"snapshots_analyzed":1,"confirmed_candidates":1,
            "candidates":[c],"by_method":{}}


def run_one(c, rows):
    calls=[]
    def fetch(*args,**kw):
        calls.append((args,kw))
        return {"backtest_ready":False,"candles_retrieved":len(rows)},rows
    with patch("research.minute_archive_pilot.inspect_snapshots",
               return_value=source(c)),patch(
             "research.execution_loss_diagnostics.inspect_snapshots",
             return_value=source(c)),patch(
             "research.execution_loss_diagnostics.analyze_signal_quality",
             return_value={"conditional_sample":1}):
        r=diagnose(["synthetic"],fetch_history=fetch)
    return r,calls


class ExecutionLossDiagnosticsTests(unittest.TestCase):
    def test_stop_has_worse_net_r_under_adverse_costs(self):
        rows=[bar(START,99.,100.5,99.,100.),
              bar(START+M,100.,100.2,97.9,98.)]
        report,calls=run_one(candidate(),rows)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0][:3],("SOL-USDT-SWAP","1m",START))
        self.assertEqual(report["overall"]["outcomes"],{"MODELLED_STOP":1})
        self.assertLess(report["details"][0]["stress"]["net_r_scenario"],
                        report["details"][0]["base"]["net_r_scenario"])
        self.assertEqual(report["details"][0]["snapshot_lag_s"],20)
        self.assertEqual(report["details"][0]["next_full_minute_wait_s"],40)
        self.assertEqual(report["details"][0]["minutes_after_entry_to_exit"],1.)
        self.assertEqual(report["real_exchange_fills_verified"],0)
        self.assertFalse(report["net_profitability_proven"])

    def test_entry_and_stop_in_same_minute_remain_ambiguous(self):
        c=candidate()
        rows=[bar(START,99.,100.5,97.9,98.8)]
        report,_=run_one(c,rows)
        self.assertEqual(report["details"][0]["issue_category"],"intraminute_order_unknown")
        self.assertEqual(report["details"][0]["base"]["outcome"],"ENTRY_MINUTE_AMBIGUOUS")
        self.assertEqual(report["overall"]["modelled_exits_not_fills"],0)
        self.assertIsNone(report["overall"]["mean_net_r_modelled_only"])
        self.assertIsNone(report["details"][0]["excursions"])
        self.assertIsNone(report["details"][0]["stress"]["net_r_scenario"])

    def test_untriggered_has_no_fabricated_loss_or_profit(self):
        rows=[bar(START+i*M,98.,99.,97.,98.) for i in range(14)]
        report,_=run_one(candidate(),rows)
        self.assertEqual(report["overall"]["outcomes"],
                         {"NOT_TRIGGERED_DURING_ACTIVE_WINDOW":1})
        self.assertEqual(report["overall"]["modelled_exits_not_fills"],0)
        self.assertEqual(report["overall"]["non_modelled_or_censored"],1)

    def test_near_expiry_snapshot_is_censored_before_api(self):
        c=candidate(observed=SIGNAL+S-10000)
        report,calls=run_one(c,[])
        self.assertEqual(calls,[])
        self.assertEqual(report["details"][0]["issue_category"],
                         "late_snapshot_no_full_minute")
        self.assertEqual(report["details"][0]["minutes_of_arming_left_at_first_full_minute"],0)

    def test_partial_scan_minute_cannot_trigger(self):
        c=candidate()
        # An early partial minute must be excluded from eligibility.
        rows=[bar(SIGNAL,99.,101.,99.,100.)]+[
            bar(START+i*M,99.,99.5,98.6,99.) for i in range(14)]
        report,_=run_one(c,rows)
        self.assertEqual(report["details"][0]["base"]["outcome"],
                         "NOT_TRIGGERED_DURING_ACTIVE_WINDOW")

    def test_interior_excursions_exclude_entry_and_exit_minute(self):
        c=candidate()
        rows=[
            bar(START,99.,100.5,99.,100.),
            bar(START+M,100.,102.,99.,101.),
            bar(START+2*M,101.,103.,100.5,102.),
            bar(START+3*M,102.,104.5,101.,104.),
        ]
        out=evaluate(c,rows)
        self.assertEqual(out["outcome"],"MODELLED_TP1")
        diag=_risk_interiors(c,{"minute_rows":rows},out)
        self.assertEqual(diag["interior_full_minutes"],2)
        self.assertEqual(diag["mfe_r_lower_bound_interior_only"],1.5)
        self.assertEqual(diag["mae_r_lower_bound_interior_only"],.5)

    def test_time_exit_near_target_is_descriptive_not_target_hit(self):
        c=candidate()
        rows=[bar(START,99.,100.5,99.,100.)]
        for i in range(1,240):
            close=103.1 if i==239 else 101.
            rows.append(bar(START+i*M,close,close+.1,close-.1,close))
        out=evaluate(c,rows)
        self.assertEqual(out["outcome"],"MODELLED_TIME_EXIT")
        diag=_risk_interiors(c,{"minute_rows":rows},out)
        self.assertGreater(diag["target_fraction_remaining_at_time_exit"],0)
        self.assertLess(diag["target_fraction_remaining_at_time_exit"],.3)

    def test_empty_group_has_no_inferred_positive_expectancy(self):
        g=_group([])
        self.assertEqual(g["modelled_exits_not_fills"],0)
        self.assertIsNone(g["mean_net_r_modelled_only"])
        self.assertIsNone(g["profit_factor_modelled_only"])

    def test_unverified_history_fail_closed(self):
        c=candidate()
        def bad_fetch(*args,**kwargs):
            return {"backtest_ready":True,"candles_retrieved":0},[]
        with patch("research.minute_archive_pilot.inspect_snapshots",
                   return_value=source(c)),patch(
                "research.execution_loss_diagnostics.inspect_snapshots",
                return_value=source(c)):
            with self.assertRaises(ValueError):
                diagnose(["synthetic"],fetch_history=bad_fetch)

    def test_candidate_limit_before_any_download(self):
        c=candidate()
        with patch("research.minute_archive_pilot.inspect_snapshots",
                   return_value=source(c)),patch(
                "research.execution_loss_diagnostics.inspect_snapshots",
                return_value=source(c)):
            with self.assertRaises(ValueError):
                diagnose(["synthetic"],max_candidates=0)


if __name__=="__main__":
    unittest.main()
