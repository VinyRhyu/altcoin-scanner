"""Read-only chronological scheduled workflow cadence guard tests."""
import unittest

from research.scheduled_workflow_health import audit

START="2026-11-22T00:00:00Z"
END="2026-11-22T16:00:00Z"


def run(at, event="schedule", name="OKX - Coleta e publicacao de dados",
        conclusion="success"):
    return {"created_at":at,"event":event,"name":name,
            "status":"completed","conclusion":conclusion}


class ScheduleReliabilityTests(unittest.TestCase):
    def test_only_two_observed_runs_vs_64_predeclared_cron_slots(self):
        rows={"workflow_runs":[
            run("2026-11-21T23:00:00Z"),
            run("2026-11-22T03:29:38Z"),
            run("2026-11-22T11:53:29Z"),
            run("2026-11-22T13:30:00Z",event="push"),
        ]}
        r=audit(rows,START,END)
        self.assertEqual(r["expected_nominal_cron_slots_in_window"],64)
        self.assertEqual(r["actual_scheduled_workflow_runs_CREATED_in_window"],2)
        self.assertEqual(r["observed_runs_per_expected_slot_ratio"],.03125)
        self.assertEqual(r["max_gap_between_observed_runs_minutes"],503.85)
        self.assertFalse(r["job_queue_delay_directly_measured"])
        self.assertFalse(r["individual_missing_trigger_slots_established"])
        self.assertFalse(r["github_scheduler_root_cause_proven"])
        self.assertFalse(r["user_trade_data_read"])
        self.assertFalse(r["production_changed"])

    def test_future_or_older_runs_not_in_fixed_window(self):
        r=audit({"workflow_runs":[
            run("2026-11-21T23:59:00Z"),
            run("2026-11-22T16:00:00Z"),
        ]},START,END)
        self.assertEqual(r["actual_scheduled_workflow_runs_CREATED_in_window"],0)
        self.assertEqual(r["expected_nominal_cron_slots_in_window"],64)

    def test_incomplete_api_page_fails_closed(self):
        with self.assertRaises(ValueError):
            audit({"workflow_runs":[run("2026-11-22T11:00:00Z")]},
                  START,END)

    def test_window_validation_and_malformed_input(self):
        rows={"workflow_runs":[run("2026-11-21T23:00:00Z")]}
        with self.assertRaises(ValueError):
            audit(rows,END,START)
        with self.assertRaises(ValueError):
            audit(rows,"2026-11-01T00:00:00Z",END)
        with self.assertRaises(ValueError):
            audit({"workflow_runs":[{}]},START,END)


if __name__=="__main__":
    unittest.main()
