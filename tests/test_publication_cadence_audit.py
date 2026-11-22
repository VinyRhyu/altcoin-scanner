"""Publication age is not an external scheduler invocation."""
from datetime import datetime,timezone,timedelta
import unittest

from research.publication_cadence_audit import audit

NOW=datetime(2026,11,22,22,30,tzinfo=timezone.utc)


def c(minutes):
    at=(NOW-timedelta(minutes=minutes)).isoformat()
    return {"commit":{"message":"data: coleta OKX "+at,
                      "committer":{"date":at}}}


def manifest(minutes=200):
    return {"status":"OK","file":"market_data.json",
            "collected_utc":(NOW-timedelta(minutes=minutes)).isoformat()}


class PublicationCadenceTests(unittest.TestCase):
    def test_commits_show_real_publications_but_not_external_heartbeat(self):
        r=audit([c(197),c(600),c(900)],manifest(198),
                checked_at_utc=NOW.isoformat())
        self.assertEqual(r["publication_age_minutes_at_check"],197.)
        self.assertEqual(r["last_publication_pipeline_lag_seconds"],60.)
        self.assertEqual(r["publication_commit_count_in_api_page"],3)
        self.assertFalse(r["external_one_minute_http_checks_observed"])
        self.assertFalse(r["external_http_checks_equal_publications"])
        self.assertFalse(r["publication_failures_causally_assigned"])
        self.assertEqual(r["trading_orders_seen"],0)

    def test_future_or_incomplete_manifest_rejected(self):
        with self.assertRaises(ValueError):
            audit([c(20)],manifest(0),checked_at_utc=NOW.isoformat())
        with self.assertRaises(ValueError):
            audit([c(20)],{"status":"FAILED"},
                  checked_at_utc=NOW.isoformat())

    def test_unrelated_commits_not_counted_as_publications(self):
        x=c(100)
        x["commit"]["message"]="docs: test"
        r=audit([c(200),x],manifest(201),checked_at_utc=NOW.isoformat())
        self.assertEqual(r["publication_commit_count_in_api_page"],1)
        self.assertIsNone(r["median_gap_between_observed_publications_minutes"])

if __name__=="__main__":
    unittest.main()
