"""Offline safety tests: monotonic stage timing and stale quotes without orders."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import collector
from research.pipeline_latency_probe import (
    BAR_MS, EVENTS, mark_event, read_events, summarize,
)

BASE=1700000000000


def iso(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()


def market(start_ms):
    bar_close=start_ms//BAR_MS*BAR_MS
    return {
        "status":"COLLECTED","market_type":"USDT_PERPETUAL",
        "collected_utc":iso(start_ms),
        "universe":[{"symbol":"SOL-USDT-SWAP",
                     "ticker_time_utc":iso(start_ms-180000)}],
        "candles":{
            "SOL-USDT-SWAP":{
                "15m":[[bar_close-BAR_MS,100,101,99,100,10,1000]]
            }
        },
    }


def append_all(path):
    times={}
    for i,event in enumerate(EVENTS):
        stamp=BASE+i*20000
        times[event]=(stamp,10000000000000+i*20000000000)
        mark_event(path,event,wall_ms=stamp,monotonic_ns=times[event][1])
    return times


class TimingProbeTests(unittest.TestCase):
    def test_full_timing_and_quote_age_with_explicit_no_order_flags(self):
        with TemporaryDirectory() as td:
            log=Path(td,"trace.jsonl")
            times=append_all(log)
            data=Path(td,"market.json")
            initial=times["collector_started"][0]+5000
            data.write_text(json.dumps(market(initial)))
            decisions=Path(td,"decisions.json")
            decisions.write_text(json.dumps({"order_allowed":False}))
            with patch.dict(os.environ,{"GITHUB_EVENT_NAME":"workflow_dispatch",
                                       "GITHUB_RUN_ID":"12345"}):
                r=summarize(log,data,decisions)
        self.assertEqual(r["status"],"RESEARCH_ONLY_TIMED_OKX_DRY_RUN")
        self.assertEqual(r["event_name"],"workflow_dispatch")
        self.assertEqual(r["captured_ticker_count"],1)
        self.assertEqual(r["durations"]["ticker_discovery_seconds"],20.)
        self.assertEqual(r["durations"]["collector_wall_seconds"],180.)
        self.assertEqual(r["durations"]["decision_wall_seconds"],20.)
        self.assertGreater(r["quote_age_at_decision_start_seconds"]["median"],180)
        self.assertTrue(r["snapshot_capture_initiated_not_completed"])
        self.assertFalse(r["cron_queue_latency_verified"])
        self.assertFalse(r["publication_completed"])
        self.assertFalse(r["alert_delivery_verified"])
        self.assertFalse(r["order_execution_verified"])
        self.assertFalse(r["personal_trades_recorded"])
        self.assertFalse(r["production_changed"])

    def test_fail_closed_on_unknown_or_duplicate_event(self):
        with TemporaryDirectory() as td:
            p=Path(td,"trace.jsonl")
            with self.assertRaises(ValueError):
                mark_event(p,"trade_executed")
            append_all(p)
            mark_event(p,"collector_started")
            with self.assertRaises(ValueError):
                read_events(p)

    def test_fail_closed_on_backwards_monotonic_clock(self):
        with TemporaryDirectory() as td:
            p=Path(td,"trace.jsonl")
            mark_event(p,"runner_started",wall_ms=BASE,
                       monotonic_ns=100000000000)
            mark_event(p,"collector_started",wall_ms=BASE+3000,
                       monotonic_ns=99999990000)
            with self.assertRaises(ValueError):
                read_events(p)

    def test_missing_checkpoint_is_error_not_zero_duration(self):
        with TemporaryDirectory() as td:
            p=Path(td,"trace.jsonl")
            mark_event(p,"runner_started",wall_ms=BASE,
                       monotonic_ns=100000000000)
            with self.assertRaises(ValueError):
                read_events(p)

    def test_future_quote_fails_provenance(self):
        with TemporaryDirectory() as td:
            log=Path(td,"trace.jsonl")
            times=append_all(log)
            initial=times["collector_started"][0]
            src=market(initial)
            src["universe"][0]["ticker_time_utc"]=iso(
                times["decision_started"][0]+20000)
            data=Path(td,"market.json")
            data.write_text(json.dumps(src))
            decisions=Path(td,"decisions.json")
            decisions.write_text(json.dumps({"order_allowed":False}))
            with self.assertRaises(ValueError):
                summarize(log,data,decisions)

    def test_reject_decisions_that_allow_orders(self):
        with TemporaryDirectory() as td:
            log=Path(td,"trace.jsonl")
            times=append_all(log)
            data=Path(td,"market.json")
            data.write_text(json.dumps(market(times["collector_started"][0])))
            decisions=Path(td,"decisions.json")
            decisions.write_text(json.dumps({"order_allowed":True}))
            with self.assertRaises(ValueError):
                summarize(log,data,decisions)

    def test_collector_probe_is_disabled_without_opt_in(self):
        with TemporaryDirectory() as td:
            p=Path(td,"events.jsonl")
            with patch.dict(os.environ,{"RESEARCH_TIMING_LOG":""}):
                collector._research_timing("allowlist_started")
            self.assertFalse(p.exists())
            with patch.dict(os.environ,{"RESEARCH_TIMING_LOG":str(p)}):
                collector._research_timing("allowlist_started")
            self.assertEqual(read_lines(p)[0]["event"],"allowlist_started")

    def test_external_coverage_is_not_evidence_of_trading(self):
        # Generic coverage guard, no user's personal trade record in repo.
        self.assertNotIn("ORCA",collector.CRYPTO_BASES)
        self.assertNotIn("ORCA",collector.BLOCKED_BASES)


def read_lines(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


if __name__=="__main__":
    unittest.main()
