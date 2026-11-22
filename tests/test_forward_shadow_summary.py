"""Research-forward shadow must not mistake missing scans for zero trades."""
import copy
import unittest
from research.forward_shadow_summary import aggregate


def sample(t="2026-11-22T21:01:00Z",added=1):
    return {
        "status":"SAME_PAYLOAD_RECEIPT_CLOCK_SHADOW_ONLY",
        "scan_started_utc":t,
        "ticker_fetch_completed_utc":"2026-11-22T21:01:00.500Z" if t.endswith("21:01:00Z")
            else "2026-11-22T21:02:00.500Z",
        "data_ready_utc":"2026-11-22T21:01:30Z" if t.endswith("21:01:00Z")
            else "2026-11-22T21:02:30Z",
        "any_orders_submitted":False,
        "profitability_verified":False,
        "master_or_market_data_changed":False,
        "baseline_shadow_is_subset_not_independent_full_legacy_collection":True,
        "pre_fetch_eligible_tickers":47-added,
        "receipt_eligible_tickers":47,
        "recovered_tickers_at_first_filter":added,
        "validated_selected_only_due_to_receipt":added,
        "recovered_tickers_not_selected_or_validated":0,
        "old_full_candle_validated_conditional_triggers":0,
        "receipt_full_candle_validated_conditional_triggers":added,
        "new_conditional_triggers_due_to_recovered_tickers":added,
    }


class ForwardShadowSummaryTests(unittest.TestCase):
    def test_paired_scan_counts_and_missing_preserved(self):
        r=aggregate([sample(),{"status":"UNAVAILABLE"},sample("2026-11-22T21:02:00Z",0)])
        self.assertEqual(r["planned_scan_slots"],3)
        self.assertEqual(r["completed_scans"],2)
        self.assertEqual(r["failed_or_censored_slots"],1)
        self.assertEqual(r["first_gate_recovered_tickers_nonunique"],1)
        self.assertEqual(r["new_conditional_triggers_nonunique"],1)
        self.assertFalse(r["ready_for_production_approval"])
        self.assertFalse(r["data_available_for_net_ev_or_real_fills"])
        self.assertTrue(r["no_order_submission"])

    def test_duplicate_scan_never_doubles_count(self):
        with self.assertRaises(ValueError):
            aggregate([sample(),sample()])

    def test_misleading_baseline_gain_rejected(self):
        bad=sample()
        bad["old_full_candle_validated_conditional_triggers"]=5
        with self.assertRaises(ValueError):
            aggregate([bad])

    def test_fabricated_fill_or_pnl_guard_rejected(self):
        bad=sample()
        bad["profitability_verified"]=True
        with self.assertRaises(ValueError):
            aggregate([bad])

    def test_all_failed_is_not_zero_market_data(self):
        r=aggregate([{"status":"UNAVAILABLE"}])
        self.assertEqual(r["completed_scans"],0)
        self.assertEqual(r["failed_or_censored_slots"],1)
        self.assertEqual(r["receipt_ticker_eligibility_total_nonunique"],0)
        self.assertFalse(r["ready_for_production_approval"])

if __name__=="__main__":
    unittest.main()
