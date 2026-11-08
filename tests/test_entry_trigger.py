import unittest
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from entry_trigger import (
    analyze_both, analyze_method, is_breakout, is_retest,
    tick_decimal, tick_round,
)
from experimental_scan import features
from test_experimental_scan import source


def fresh_after_close(history, minute=1):
    return datetime.fromtimestamp(
        (int(history["15m"][-1][0])+900000+minute*60000)/1000, timezone.utc)


def ticker(price, tick="0.0001", spread=.02):
    return {"price": price, "tick_size": tick, "spread_pct": spread}


def prepare_breakout(side="LONG"):
    d = source(down=(side == "SHORT"))
    h = d["candles"]["AAA-USDT-SWAP"]
    m = h["15m"]
    if side == "LONG":
        m[-1][1:7] = [1.039, 1.052, 1.038, 1.050, 100., 800.]
        price = 1.048
    else:
        m[-1][1:7] = [1.041, 1.042, 1.029, 1.032, 100., 800.]
        price = 1.035
    return h, price


def prepare_retest():
    d = source()
    h = d["candles"]["AAA-USDT-SWAP"]
    h["15m"][-2][1:7] = [1.038, 1.052, 1.037, 1.050, 100., 800.]
    h["15m"][-1][1:7] = [1.042, 1.047, 1.038, 1.045, 100., 120.]
    return h


class EntryTriggersTests(unittest.TestCase):
    def test_decimal_tick_rounding(self):
        self.assertEqual(tick_round(Decimal("0.0000048189"),
                                    Decimal("0.00000001"), "up"),
                         0.00000482)
        self.assertEqual(tick_round(Decimal("0.0000048189"),
                                    Decimal("0.00000001"), "down"),
                         0.00000481)
        self.assertEqual(tick_round(Decimal("47.141"),
                                    Decimal("0.01"), "up"), 47.15)
        self.assertIsNone(tick_decimal(""))

    def test_long_breakout_exact_tick(self):
        h, price = prepare_breakout()
        plan = analyze_method(h, features(h), ticker(price), "LONG",
                              "BREAKOUT", fresh_after_close(h), False)
        self.assertEqual(plan["status"], "CONFIRMADO")
        self.assertEqual(plan["entry_trigger_price"], 1.0521)
        self.assertLess(plan["stop_loss"], plan["entry_trigger_price"])
        self.assertGreater(plan["tp1_projected"], plan["entry_trigger_price"])
        self.assertFalse(plan["order_allowed"])

    def test_short_breakout_exact_tick(self):
        h, price = prepare_breakout("SHORT")
        plan = analyze_method(h, features(h), ticker(price), "SHORT",
                              "BREAKOUT", fresh_after_close(h), False)
        self.assertEqual(plan["status"], "CONFIRMADO")
        self.assertEqual(plan["entry_trigger_price"], 1.0289)
        self.assertGreater(plan["stop_loss"], plan["entry_trigger_price"])
        self.assertLess(plan["tp1_projected"], plan["entry_trigger_price"])

    def test_independent_retest_trigger(self):
        h = prepare_retest()
        info = features(h)
        method = analyze_both(h, info, ticker(1.044), "LONG",
                              fresh_after_close(h), False)
        breakout, retest = method
        self.assertEqual(breakout["method"], "BREAKOUT")
        self.assertEqual(retest["method"], "RETEST")
        self.assertEqual(retest["status"], "CONFIRMADO")
        self.assertEqual(retest["entry_trigger_price"], 1.0471)

    def test_missing_tick_never_fabricates_price(self):
        h, price = prepare_breakout()
        plan = analyze_method(h, features(h), ticker(price, tick=None),
                              "LONG", "BREAKOUT", fresh_after_close(h), False)
        self.assertEqual(plan["decision"], "NAO_ENTRAR")
        self.assertIsNone(plan["entry_trigger_price"])
        self.assertIn("tickSz", plan["reasons"][0])

    def test_expired_signal_not_actionable(self):
        h, price = prepare_breakout()
        plan = analyze_method(h, features(h), ticker(price),
                              "LONG", "BREAKOUT",
                              fresh_after_close(h, minute=16), False)
        self.assertEqual(plan["decision"], "NAO_ENTRAR")
        self.assertTrue(any("expirado" in v for v in plan["reasons"]))

    def test_high_price_slippage_can_reject_signal(self):
        h, _ = prepare_breakout()
        plan = analyze_method(h, features(h), ticker(2.0), "LONG",
                              "BREAKOUT", fresh_after_close(h), False)
        self.assertEqual(plan["decision"], "NAO_ENTRAR")
        self.assertTrue(any("atravessou" in x for x in plan["reasons"]))

    def test_no_synthetic_entry_without_candle_confirmation(self):
        d = source()
        h = d["candles"]["AAA-USDT-SWAP"]
        methods = analyze_both(h, features(h), ticker(1.039), "LONG",
                               fresh_after_close(h), False)
        self.assertTrue(all(m["status"] == "AGUARDANDO_CONFIRMACAO"
                            for m in methods))
        self.assertTrue(all(m["entry_trigger_price"] is None for m in methods))

    def test_tick_is_not_assumed_from_price_decimals(self):
        h, price = prepare_breakout()
        for unavailable in (None, "0", "nan", "invalid"):
            plan = analyze_method(h, features(h), ticker(price, unavailable),
                                  "LONG", "BREAKOUT",
                                  fresh_after_close(h), False)
            self.assertIsNone(plan["entry_trigger_price"])
            self.assertNotEqual(plan["decision"], "GATILHO_CONDICIONAL")

    def test_sample_source_outputs_both_directions_and_methods(self):
        from trade_decision import analyze
        d = source()
        d["universe"][0].update(ticker(1.04))
        out = analyze(d, now_utc=datetime(
            2026,11,8,tzinfo=timezone.utc))
        self.assertEqual(len(out["ranked_setups"]), 2)
        self.assertEqual(sum(len(x["entry_methods"])
                             for x in out["ranked_setups"]), 4)
        self.assertEqual(len(out["confirmed_entry_triggers"]), 0)
        self.assertFalse(out["order_allowed"])


if __name__ == "__main__":
    unittest.main()
