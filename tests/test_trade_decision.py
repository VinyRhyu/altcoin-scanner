import unittest
from datetime import datetime, timezone, timedelta
from trade_decision import analyze, candle_quality, levels, setup
from test_experimental_scan import source


class TradeDecisionTests(unittest.TestCase):
    def test_both_sides_retained_even_when_no_setup(self):
        data = source(("AAA-USDT-SWAP", "BBB-USDT-SWAP"))
        out = analyze(data, now_utc=datetime(2026,11,8,tzinfo=timezone.utc))
        self.assertEqual(out["source_asset_count"], 2)
        self.assertEqual(out["retained_asset_count"], 2)
        self.assertEqual(len(out["ranked_setups"]), 4)
        self.assertEqual({p["side"] for p in out["ranked_setups"]}, {"LONG", "SHORT"})
        self.assertFalse(out["order_allowed"])

    def test_missing_history_keeps_asset_without_false_entry(self):
        data = source(("AAA-USDT-SWAP", "BBB-USDT-SWAP"))
        del data["candles"]["BBB-USDT-SWAP"]
        out = analyze(data, now_utc=datetime(2026,11,8,tzinfo=timezone.utc))
        self.assertEqual(out["retained_asset_count"], 2)
        self.assertEqual(out["analyzed_asset_count"], 1)
        self.assertEqual(out["assets"][1]["status"], "DADOS_INSUFICIENTES")

    def test_bearish_candle_above_resistance_not_bullish_confirmation(self):
        self.assertFalse(candle_quality([1, 1.15, 1.17, 1.08, 1.10, 0, 200], "LONG"))

    def test_breakout_is_reported_independently_of_retest(self):
        data = source()
        c = data["candles"]["AAA-USDT-SWAP"]["15m"]
        c[-1][1:7] = [1.038, 1.060, 1.037, 1.056, 100, 800]
        out = analyze(data, now_utc=datetime(2026,11,8,tzinfo=timezone.utc))
        p = next(x for x in out["ranked_setups"] if x["side"] == "LONG")
        self.assertEqual(p["stage"], "ROMPIMENTO_CONFIRMADO")
        self.assertEqual(p["legacy_stage"], "AGUARDAR_RETESTE")
        self.assertNotEqual(p["decision"], "ENTRADA_CONDICIONAL")

    def test_retest_detected_with_closed_candles(self):
        data = source()
        c = data["candles"]["AAA-USDT-SWAP"]["15m"]
        c[-2][1:7] = [1.038, 1.052, 1.037, 1.050, 100, 800]
        c[-1][1:7] = [1.043, 1.052, 1.038, 1.044, 100, 120]
        from experimental_scan import features
        info = features(data["candles"]["AAA-USDT-SWAP"])
        stage, level, volume = setup(data["candles"]["AAA-USDT-SWAP"], info, "LONG")
        self.assertEqual(stage, "RETESTE_CONFIRMADO")
        self.assertGreater(level, 0)
        self.assertGreater(volume, .65)

    def test_reaction_and_rejection_are_monitored_independently(self):
        # 40 candles laterais. Usar um unico candle fechado como gatilho.
        from trade_decision import setup
        base = [[i*900000, 10., 10.2, 9.8, 10., 100., 100.]
                for i in range(40)]
        hist = {"15m": base}
        hist["15m"][-1] = [39*900000, 9.81, 9.92, 9.80, 9.90, 100., 200.]
        stage_long, _, _ = setup(hist, {"atr_15m": .2}, "LONG")
        self.assertEqual(stage_long, "REACAO_CONFIRMADA")
        hist["15m"][-1] = [39*900000, 10.18, 10.20, 10.05, 10.08, 100., 200.]
        stage_short, _, _ = setup(hist, {"atr_15m": .2}, "SHORT")
        self.assertEqual(stage_short, "REJEICAO_CONFIRMADA")

    def test_stale_data_blocks_confirmed_entry(self):
        data = source()
        now = datetime(2026,11,8,tzinfo=timezone.utc) + timedelta(days=2)
        out = analyze(data, now_utc=now)
        self.assertTrue(out["snapshot_stale"])
        self.assertTrue(all(p["decision"] != "ENTRADA_CONDICIONAL"
                            for p in out["ranked_setups"]))

    def test_micro_prices_preserve_significant_digits(self):
        data = source()
        from experimental_scan import features
        hist = data["candles"]["AAA-USDT-SWAP"]
        for tf in hist:
            for r in hist[tf]:
                for j in (1,2,3,4):
                    r[j] *= .0000048
        f = features(hist)
        p = levels(hist, f, "LONG", f["resistance_15m"], .02)
        self.assertGreater(p["rr_worst_entry"], 0)
        self.assertGreater(p["stop"], 0)
        self.assertGreater(p["tp1_2_2R_projection"], 0)
        self.assertGreater(p["tp1_2_2R_projection"], p["rr_worst_entry"])

    def test_half_atr_watch_pre_breakout(self):
        from trade_decision import watch_fields
        w = watch_fields("LONG", "PREPARANDO", 10.0, 9.95, .2,
                         "AGUARDAR", False, None)
        self.assertAlmostEqual(w["confirmation_close_price"], 10.024)
        self.assertTrue(w["near_trigger_0_5_atr"])
        self.assertEqual(w["alert_type"], "PROXIMO_ROMPIMENTO")
        self.assertFalse(w["signal_is_actionable_now"])

    def test_watch_does_not_fire_when_trigger_already_passed_or_stale(self):
        from trade_decision import watch_fields
        already = watch_fields("LONG", "PREPARANDO", 10., 10.30, .2,
                               "AGUARDAR", False, None)
        stale = watch_fields("SHORT", "PREPARANDO", 10., 10.04, .2,
                             "AGUARDAR", True, None)
        weak = watch_fields("LONG", "ROMPIMENTO_FRACO", 10., 10.01, .2,
                            "NAO_ENTRAR", False, None)
        self.assertFalse(already["near_trigger_0_5_atr"])
        self.assertFalse(stale["near_trigger_0_5_atr"])
        self.assertFalse(weak["near_trigger_0_5_atr"])

    def test_near_retest_is_not_an_entry(self):
        from trade_decision import watch_fields
        w = watch_fields("LONG", "AGUARDAR_RETESTE", 10.0, 10.08, .2,
                         "AGUARDAR", False, None)
        self.assertTrue(w["near_trigger_0_5_atr"])
        self.assertEqual(w["alert_type"], "PROXIMO_RETESTE")
        self.assertFalse(w["signal_is_actionable_now"])
        self.assertIn("NAO entrar", w["alert_message"])

    def test_all_directions_have_radar_fields(self):
        data = source(("AAA-USDT-SWAP", "BBB-USDT-SWAP"))
        out = analyze(data, now_utc=datetime(2026,11,8,tzinfo=timezone.utc))
        self.assertEqual(len(out["ranked_setups"]), 4)
        for p in out["ranked_setups"]:
            self.assertIn("monitor_price", p["watch"])
            self.assertIn("confirmation_close_price", p["watch"])
            self.assertIn("invalid_if_15m_closes_beyond", p["watch"])
        self.assertEqual(out["retained_asset_count"], 2)
        self.assertEqual(out["proximity_alert_count"],
                         len(out["proximity_alerts"]))


if __name__ == "__main__":
    unittest.main()
