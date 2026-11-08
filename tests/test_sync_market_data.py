import unittest
from datetime import datetime, timedelta, timezone
from entry_trigger import analyze_method
from experimental_scan import features
from sync_market_data import check_market_freshness
from trade_decision import analyze
from test_entry_trigger import prepare_breakout, fresh_after_close, ticker
from test_experimental_scan import source


def make_market(last_close: str, quote_age_seconds=40, age_seconds=40):
    last = datetime.fromisoformat(last_close.replace("Z", "+00:00"))
    now = last + timedelta(minutes=1)
    start_ms = int((last-timedelta(minutes=15)).timestamp()*1000)
    return {
        "market_type": "USDT_PERPETUAL",
        "collected_utc": (now-timedelta(seconds=age_seconds)).isoformat(),
        "universe": [{
            "symbol": "AAA-USDT-SWAP", "price": 1.0,
            "ticker_time_utc": (now-timedelta(seconds=quote_age_seconds)).isoformat(),
            "tick_size": "0.0001", "spread_pct": .02,
        }],
        "candles": {"AAA-USDT-SWAP": {
            "15m": [[start_ms, 1., 1.1, .9, 1., 10., 10.]],
        }},
    }, now


class SyncAndLifecycleTests(unittest.TestCase):
    def test_same_candle_recent_quote_is_current(self):
        m, now = make_market("2026-11-08T02:45:00+00:00")
        s = check_market_freshness(m, now)
        self.assertEqual(s["source_sync_status"], "CANDLE_ATUAL")
        self.assertTrue(s["current_for_new_entries"])

    def test_closed_candle_not_yet_published_blocks_old_signal(self):
        m, _ = make_market("2026-11-08T02:30:00+00:00")
        now = datetime(2026,11,8,2,46,tzinfo=timezone.utc)
        # Precisamente o caso ENA: candle 02:45 finalizou, fonte so tem 02:30.
        s = check_market_freshness(m, now)
        self.assertEqual(s["source_sync_status"], "AGUARDANDO_NOVO_CANDLE_PUBLICADO")
        self.assertFalse(s["current_for_new_entries"])
        self.assertEqual(s["expected_last_closed_15m_utc"],
                         "2026-11-08T02:45:00+00:00")

    def test_current_candle_but_quote_old_is_not_actionable(self):
        m, now = make_market("2026-11-08T02:45:00+00:00",
                             quote_age_seconds=700)
        s = check_market_freshness(m, now)
        self.assertEqual(s["source_sync_status"], "COTACAO_DESATUALIZADA")
        self.assertFalse(s["current_for_new_entries"])

    def test_missing_quote_timestamp_is_not_assumed_current(self):
        m, now = make_market("2026-11-08T02:45:00+00:00")
        del m["universe"][0]["ticker_time_utc"]
        s = check_market_freshness(m, now)
        self.assertEqual(s["source_sync_status"], "COTACAO_DESATUALIZADA")

    def test_no_enter_on_old_candle_even_when_snapshot_timestamp_looks_new(self):
        data = source()
        now = datetime(2026, 11, 8, 2, 46, tzinfo=timezone.utc)
        data["collected_utc"] = now.isoformat()
        data["universe"][0].update(ticker(1.04))
        result = analyze(data, now_utc=now)
        self.assertFalse(result["current_for_new_entries"])
        self.assertTrue(result["snapshot_stale"])
        self.assertEqual(result["status"], "FONTE_DESATUALIZADA")
        self.assertFalse(result["confirmed_entry_triggers"])

    def test_already_crossed_is_not_still_available(self):
        h, _ = prepare_breakout()
        info = features(h)
        p = analyze_method(h, info, ticker(1.053),
                           "LONG", "BREAKOUT", fresh_after_close(h), False)
        self.assertEqual(p["status"], "CONFIRMADO")
        self.assertEqual(p["entry_trigger_price"], 1.0521)
        self.assertEqual(p["entry_state"], "PRECO_JA_ULTRAPASSOU_GATILHO")
        self.assertFalse(p["entry_eligible_at_snapshot"])
        self.assertEqual(p["decision"], "NAO_ENTRAR")

    def test_expired_is_not_recommended_even_if_price_is_before_trigger(self):
        h, price = prepare_breakout()
        p = analyze_method(h, features(h), ticker(price),
                           "LONG", "BREAKOUT",
                           fresh_after_close(h, minute=16), False)
        self.assertEqual(p["entry_state"], "EXPIRADO")
        self.assertFalse(p["entry_eligible_at_snapshot"])

    def test_invalidated_can_not_be_called_a_new_entry(self):
        h, _ = prepare_breakout()
        p = analyze_method(h, features(h), ticker(.5),
                           "LONG", "BREAKOUT", fresh_after_close(h), False)
        self.assertEqual(p["entry_state"], "INVALIDADO")
        self.assertFalse(p["entry_eligible_at_snapshot"])

    def test_no_promise_of_order_fill_when_quote_crossed(self):
        h, _ = prepare_breakout("SHORT")
        p = analyze_method(h, features(h), ticker(1.028),
                           "SHORT", "BREAKOUT", fresh_after_close(h), False)
        self.assertTrue(p["trigger_crossed_in_snapshot"])
        self.assertFalse(p["order_allowed"])
        self.assertFalse(p["entry_eligible_at_snapshot"])


if __name__ == "__main__":
    unittest.main()
