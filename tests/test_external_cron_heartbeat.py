"""Safety tests for the user's actual external one-minute scheduler."""
import unittest
from research.external_cron_heartbeat import audit


def entry(eid,planned,delta=0,status=1,http=200):
    return {
        "jobLogId":eid,"datePlanned":planned,"date":planned+delta,
        "jitter":delta*1000,"duration":250,"status":status,"httpStatus":http,
        "url":"https://private.example/secret-token-do-not-log",
        "headers":"Authorization: Bearer fake-secret","body":"fake-secret",
        "identifier":"PRIVATE_DO_NOT_COPY",
    }


class ExternalCronHeartbeatTests(unittest.TestCase):
    def test_one_minute_external_requests_without_secret_or_url_leak(self):
        payload={"history":[entry(1,1700000040,2),
                            entry(2,1700000100,5),
                            entry(3,1700000160,1,status=5,http=503)],
                 "predictions":[1700000220]}
        git={"workflow_runs":[
            {"name":"OKX - Coleta e publicacao de dados","event":"schedule",
             "created_at":"2026-11-22T19:02:01Z"},
            {"name":"OKX - Coleta e publicacao de dados","event":"workflow_dispatch",
             "created_at":"2026-11-08T22:18:06Z"},
            {"name":"Research audit tests","event":"push",
             "created_at":"2026-11-22T18:02:01Z"},
        ],"total_count":344}
        r=audit(payload,git)
        self.assertEqual(r["cronjob_observed"]["recorded_requests"],3)
        self.assertEqual(r["cronjob_observed"]["actual_calls_with_status_ok"],2)
        self.assertEqual(r["cronjob_observed"]["adjacent_recorded_plans_one_minute_apart"],2)
        self.assertEqual(r["github_observed"]["scanner_event_counts_in_page"],
                         {"schedule":1,"workflow_dispatch":1})
        self.assertTrue(r["github_observed"]["github_scheduled_count_is_not_external_success_rate"])
        self.assertFalse(r["http_success_proves_market_publication"])
        self.assertFalse(r["github_workflow_trigger_proven_from_external_history"])
        self.assertFalse(r["root_cause_of_missing_scan_proven"])
        self.assertEqual(r["orders_executed"],0)
        from json import dumps
        encoded=dumps(r)
        for secret in ("fake-secret","PRIVATE_DO_NOT_COPY","private.example"):
            self.assertNotIn(secret,encoded)

    def test_absent_private_history_unknown_not_zero(self):
        r=audit(None,{"workflow_runs":[],"total_count":0})
        self.assertFalse(r["cronjob_history_available"])
        self.assertIsNone(r["cronjob_observed"])
        self.assertEqual(r["github_observed"]["scanner_event_counts_in_page"],{})
        self.assertFalse(r["external_requests_equal_full_market_scans"])

    def test_duplicate_ids_or_invalid_timestamp_fail_closed(self):
        same=entry(1,1700000040)
        with self.assertRaises(ValueError):
            audit({"history":[same,same]})
        with self.assertRaises(ValueError):
            audit({"history":[entry(1,1700000040,-80)]})
        with self.assertRaises(ValueError):
            audit({"history":[entry(1,1700000040,status=22)]})

    def test_not_all_scheduled_minutes_are_proven_missing_in_partial_history(self):
        r=audit({"history":[entry(1,1700000040),
                            entry(2,1700000040+600)]})
        self.assertEqual(r["cronjob_observed"][
            "adjacent_recorded_plans_over_one_minute_apart"],1)
        self.assertTrue(r["cronjob_observed"][
            "unrecorded_minute_slots_must_not_be_labelled_failed"])

    def test_unexpected_cadence_cannot_be_mislabeled_as_60_seconds(self):
        with self.assertRaises(ValueError):
            audit({"history":[]},expected_interval_seconds=900)

if __name__=="__main__":
    unittest.main()
