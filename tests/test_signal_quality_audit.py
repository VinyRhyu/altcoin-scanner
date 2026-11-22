"""Causal, read-only feature/outcome and later-only delay stress regressions."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research.futures_scenario import Scenario
from research.minute_execution import evaluate
from research.signal_quality_audit import (
    _lag_label, analyze, extract_features,
    extract_first_snapshot_features, summarize_group,
)

M=60000
SIGNAL=288000000
OBS=SIGNAL+20000
START=SIGNAL+M
SYMBOL="SOL-USDT-SWAP"


def candidate(side="LONG",stamp=OBS):
    return {
        "symbol":SYMBOL,"side":side,"method":"BREAKOUT",
        "signal_close_ms":SIGNAL,"first_observed_at_ms":stamp,
        "decision_at_snapshot":"GATILHO_CONDICIONAL",
        "entry":100.,"stop":98. if side=="LONG" else 102.,
        "tp1":104. if side=="LONG" else 96.,
        "spread_pct_observed":.02,"tick_size_observed":".01",
        "reasons":[],
    }


def history():
    def bars(indices,duration):
        return [[i*duration,100.,101.,99.,100.,100.,1000.]
                for i in indices]
    return {
        "15m":bars(range(280,320),900000),
        "1h":bars(range(40,80),3600000),
        "2h":bars(range(40),7200000),
    }


def snapshot(stamp=OBS,series=None,price=99.):
    iso=datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat()
    return {
        "status":"COLLECTED","market_type":"USDT_PERPETUAL",
        "collected_utc":iso,
        "universe":[{"symbol":SYMBOL,"price":price,
                     "tick_size":".01","spread_pct":.02}],
        "candles":{SYMBOL:series if series is not None else history()},
    }


def minute(ts,o,hi,lo,cl):
    return {"source":"okx_history_candles",
            "point_in_time_eligible":False,
            "symbol":SYMBOL,"timeframe":"1m",
            "open_ms":ts,"open":o,"high":hi,"low":lo,"close":cl}


def windows(rows,side="LONG"):
    return [{
        "symbol":SYMBOL,"signal_close_ms":SIGNAL,
        "side":side,"method":"BREAKOUT",
        "minute_rows":rows,
    }]


def diagnoses(c,rows):
    out=evaluate(c,rows,Scenario())
    return [{
        "symbol":c["symbol"],"side":c["side"],"method":c["method"],
        "signal_close_ms":SIGNAL,
        "first_observed_at_ms":c["first_observed_at_ms"],
        "base":out,
    }]


class SignalQualityTests(unittest.TestCase):
    def test_features_from_latest_closed_bar_and_exact_snapshot(self):
        c=candidate()
        hist=history()
        r=extract_features(hist,snapshot()["universe"][0],c,OBS)
        self.assertEqual(r["feature_observed_at_ms"],OBS)
        self.assertFalse(r["feature_uses_future_minute_candles"])
        self.assertEqual(r["numeric"]["relative_volume_15m"],1.)
        self.assertEqual(r["numeric"]["candle_range_atr"],1.)
        self.assertEqual(r["numeric"]["risk_atr"],1.)
        self.assertEqual(r["numeric"]["snapshot_remaining_trigger_atr"],.5)
        self.assertEqual(r["numeric"]["snapshot_directional_move_since_close_atr"],-.5)

    def test_reject_future_hourly_candle_even_if_15m_closed(self):
        hist=history()
        hist["1h"][-1][0]=SIGNAL
        with self.assertRaises(ValueError):
            extract_features(hist,snapshot()["universe"][0],candidate(),OBS)

    def test_first_observed_archive_wins_not_later_rewritten_prices(self):
        c=candidate()
        later=copy.deepcopy(snapshot(stamp=OBS+M,price=98.))
        with TemporaryDirectory() as td:
            p1=Path(td)/"market_data_a.json"
            p2=Path(td)/"market_data_b.json"
            p1.write_text(json.dumps(snapshot(price=99.)))
            p2.write_text(json.dumps(later))
            found=extract_first_snapshot_features([td],[c])
        self.assertEqual(found[tuple(c[k] for k in (
            "symbol","signal_close_ms","side","method"))]["numeric"][
            "snapshot_remaining_trigger_atr"],.5)

    def test_delay_stress_never_shifts_entry_earlier(self):
        c=candidate()
        rows=[minute(START,99.,100.5,99.,100.),
              minute(START+M,100.,100.2,97.9,98.)]
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_a.json"
            p.write_text(json.dumps(snapshot()))
            report=analyze([td],{"candidates":[c]},windows(rows),diagnoses(c,rows))
        self.assertEqual(report["conditional_sample"],1)
        self.assertEqual(report["by_outcome"]["MODELLED_STOP"]["n"],1)
        self.assertEqual(report["rows"][0]["lag_seconds"],20.)
        self.assertEqual(report["rows"][0]["arming_minutes_left"],14)
        delay=report["rows"][0]["hypothetical_extra_delay"]
        self.assertEqual(delay["0"]["outcome"],"MODELLED_STOP")
        self.assertEqual(delay["1"]["outcome"],"ENTRY_MINUTE_AMBIGUOUS")
        self.assertFalse(delay["1"]["modelled_execution"])
        self.assertIsNone(delay["1"]["net_r_scenario"])
        self.assertTrue(report["features_extracted_from_exact_earliest_snapshot"])
        self.assertFalse(report["profitability_or_causality_proven"])
        self.assertEqual(report["real_exchange_fills_verified"],0)
        self.assertFalse(report["production_changed"])

    def test_zero_delay_uses_actual_scan_timestamp_when_full_minute_is_expired(self):
        # Later full-minute boundary is not the original observation time.
        stamp=SIGNAL+900000-10000
        c=candidate(stamp=stamp)
        base=diagnoses(c,[])
        self.assertEqual(base[0]["base"]["outcome"],
                         "NO_COMPLETE_MINUTE_BEFORE_EXPIRY")
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_a.json"
            p.write_text(json.dumps(snapshot(stamp=stamp)))
            r=analyze([td],{"candidates":[c]},windows([]),base)
        d=r["rows"][0]["hypothetical_extra_delay"]
        self.assertEqual(d["0"]["outcome"],"NO_COMPLETE_MINUTE_BEFORE_EXPIRY")
        self.assertEqual(d["1"]["outcome"],"INVALID_OR_EXPIRED_OBSERVATION")
        self.assertIsNone(d["0"]["net_r_scenario"])
        self.assertEqual(r["real_exchange_fills_verified"],0)

    def test_future_minute_prices_never_change_pre_signal_features(self):
        c=candidate()
        base=[minute(START,99.,100.5,99.,100.),
              minute(START+M,100.,100.2,97.9,98.)]
        modified=[minute(START,99.,100.5,99.,100.),
                  minute(START+M,100.,104.5,99.9,104.)]
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_a.json"
            p.write_text(json.dumps(snapshot()))
            old=analyze([td],{"candidates":[c]},windows(base),
                        diagnoses(c,base))
            new=analyze([td],{"candidates":[c]},windows(modified),
                        diagnoses(c,modified))
        self.assertEqual(old["rows"][0]["features"],new["rows"][0]["features"])
        self.assertNotEqual(old["rows"][0]["outcome"]["outcome"],
                            new["rows"][0]["outcome"]["outcome"])

    def test_missing_candidate_or_bad_snapshot_timestamp_fails_closed(self):
        c=candidate()
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_a.json"
            p.write_text(json.dumps(snapshot(stamp=OBS+M)))
            with self.assertRaises(ValueError):
                extract_first_snapshot_features([td],[c])
        with self.assertRaises(ValueError):
            extract_features(history(),snapshot()["universe"][0],c,OBS+M)

    def test_latency_buckets_have_explicit_upper_bound(self):
        self.assertEqual(_lag_label(0),"under_3m")
        self.assertEqual(_lag_label(180),"3_to_under_6m")
        self.assertEqual(_lag_label(360),"6_to_under_10m")
        self.assertEqual(_lag_label(899.999),"10_to_under_15m")
        with self.assertRaises(ValueError):
            _lag_label(900)

    def test_small_or_empty_groups_have_no_fabricated_expectancy(self):
        r=summarize_group([])
        self.assertEqual(r["n"],0)
        self.assertIsNone(r["features"]["relative_volume_15m"]["median"])
        self.assertIsNone(r["median_net_r_resolved_only"])

    def test_blocked_without_tick_is_counted_but_not_fabricated(self):
        blocked=dict(candidate(),method="RETEST",entry=None,stop=None,
                     decision_at_snapshot="NAO_ENTRAR")
        c=candidate()
        rows=[minute(START,99.,100.5,99.,100.)]
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_a.json"
            p.write_text(json.dumps(snapshot()))
            r=analyze([td],{"candidates":[c,blocked]},
                      windows(rows),diagnoses(c,rows))
        self.assertEqual(r["confirmed_technical_sample"],2)
        self.assertEqual(r["confirmed_with_computable_feature_plan"],1)
        self.assertEqual(r["excluded_confirmations_without_plan_or_spread"],1)

    def test_baseline_result_must_match_zero_extra_latency(self):
        c=candidate()
        rows=[minute(START,99.,100.5,99.,100.)]
        wrong=diagnoses(c,rows)
        wrong[0]["base"]["outcome"]="MODELLED_TP1"
        with TemporaryDirectory() as td:
            p=Path(td)/"market_data_a.json"
            p.write_text(json.dumps(snapshot()))
            with self.assertRaises(ValueError):
                analyze([td],{"candidates":[c]},windows(rows),wrong)


if __name__=="__main__":
    unittest.main()
