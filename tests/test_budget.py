import unittest
from decimal import Decimal

import _synth as S
from _load import load

size = load("binance-budget", "size")

FLT = size.parse_filters(S.symbol_info("XUSDT", "X", tick="0.01000000", step="0.00100000", min_notional="5.00000000"))


def cand(entry=100.0, stop=95.0, tp=110.0, symbol="XUSDT", score=80):
    return {"symbol": symbol, "entry": entry, "stop": stop, "take_profit": tp, "take_profit_2": tp * 1.05, "score": score, "setup": "breakout"}


def cfg(**kw):
    c = size.load_config("/nonexistent")
    c.update(kw)
    return c


class Rounding(unittest.TestCase):
    def test_floor_and_ceil_to_step(self):
        self.assertEqual(size.floor_to(1.2349, "0.01"), Decimal("1.23"))
        self.assertEqual(size.ceil_to(1.2301, "0.01"), Decimal("1.24"))
        self.assertEqual(size.floor_to(0.30000000000000004, "0.1"), Decimal("0.3"))

    def test_filters_are_parsed_from_the_notional_filter_or_the_old_one(self):
        info = S.symbol_info("XUSDT", "X")
        self.assertEqual(size.parse_filters(info)["min_notional"], "5.00000000")
        info["filters"][2] = {"filterType": "MIN_NOTIONAL", "minNotional": "10.0", "applyToMarket": True, "avgPriceMins": 5}
        self.assertEqual(size.parse_filters(info)["min_notional"], "10.0")


class Sizing(unittest.TestCase):
    def test_risk_is_about_one_percent_and_never_above(self):
        o, why = size.plan_order(cand(100, 95, 110), FLT, 100.0, 90.0, cfg())
        self.assertIsNone(why)
        self.assertLessEqual(o["risk_pct"], 1.0 + 1e-9)
        self.assertGreater(o["risk_pct"], 0.95)
        self.assertEqual(o["quantity_str"], "0.192")                  # 1.00 / (5 + 0.2) = 0.1923, floored to the step
        self.assertEqual(o["side"], "BUY")
        self.assertEqual(o["type"], "LIMIT")

    def test_fees_are_inside_the_risk(self):
        o, _ = size.plan_order(cand(100, 95, 110), FLT, 100.0, 90.0, cfg())
        self.assertAlmostEqual(o["risk_usdt"], o["quantity"] * (5 + 100 * 0.001 * 2), places=6)

    def test_position_cap_limits_a_tight_stop(self):
        o, _ = size.plan_order(cand(100, 99, 102), FLT, 100.0, 90.0, cfg())
        self.assertLessEqual(o["notional"], 25.0 + 1e-9)
        self.assertIn("capped", " ".join(o["notes"]))
        self.assertLess(o["risk_pct"], 1.0)

    def test_cash_left_limits_the_size(self):
        o, _ = size.plan_order(cand(100, 95, 110), FLT, 100.0, 10.0, cfg())
        self.assertLessEqual(o["notional"], 10.0 + 1e-9)

    def test_no_cash_means_no_order(self):
        o, why = size.plan_order(cand(), FLT, 100.0, 0.0, cfg())
        self.assertIsNone(o)
        self.assertIn("cash", why)

    def test_prices_are_rounded_down_to_the_tick(self):
        o, _ = size.plan_order(cand(100.129, 95.555, 110.999), FLT, 100.0, 90.0, cfg())
        self.assertEqual((o["price_str"], o["stop_price"], o["take_profit"]), ("100.12", 95.55, 110.99))

    def test_oco_stop_limit_sits_under_the_trigger(self):
        o, _ = size.plan_order(cand(), FLT, 100.0, 90.0, cfg())
        self.assertLess(o["stop_limit_price"], o["stop_price"])
        self.assertGreater(o["stop_limit_price"], o["stop_price"] * 0.99)

    def test_stop_must_be_under_entry(self):
        o, why = size.plan_order(cand(100, 101), FLT, 100.0, 90.0, cfg())
        self.assertIsNone(o)
        self.assertIn("stop", why)

    def test_untradable_symbol_is_skipped(self):
        halted = dict(FLT, status="BREAK")
        self.assertIn("not trading", size.plan_order(cand(), halted, 100.0, 90.0, cfg())[1])
        self.assertIn("no exchange rules", size.plan_order(cand(), None, 100.0, 90.0, cfg())[1])


class SmallAccounts(unittest.TestCase):
    def test_minimum_order_is_reached_when_the_real_risk_stays_acceptable(self):
        o, why = size.plan_order(cand(100, 90, 120), FLT, 40.0, 36.0, cfg(max_position_pct=25))
        self.assertIsNone(why)
        self.assertGreaterEqual(o["notional"], 5.0)
        self.assertIn("minimum order", " ".join(o["notes"]))
        self.assertLessEqual(o["risk_pct"], 2.0)

    def test_bump_off_skips_instead(self):
        o, why = size.plan_order(cand(100, 90, 120), FLT, 40.0, 36.0, cfg(min_notional_bump=False))
        self.assertIsNone(o)
        self.assertIn("minimum order", why)

    def test_a_stop_too_wide_for_the_balance_is_skipped_with_the_reason(self):
        o, why = size.plan_order(cand(100, 70, 160), FLT, 40.0, 36.0, cfg())
        self.assertIsNone(o)
        self.assertIn("too wide", why)

    def test_tiny_budget_says_to_raise_the_position_cap(self):
        o, why = size.plan_order(cand(100, 95, 110), FLT, 10.0, 9.0, cfg())
        self.assertIsNone(o)
        self.assertIn("max_position_pct", why)
        o, why = size.plan_order(cand(100, 98, 106), FLT, 10.0, 9.0, cfg(max_position_pct=60))
        self.assertIsNone(why)
        self.assertGreaterEqual(o["notional"], 5.0)
        self.assertLessEqual(o["risk_pct"], 2.0)
        # a 5% stop would risk 2.65% of a 10 USDT budget at the minimum order: skipped, with the number
        o, why = size.plan_order(cand(100, 95, 110), FLT, 10.0, 9.0, cfg(max_position_pct=60))
        self.assertIn("too wide", why)


class Batch(unittest.TestCase):
    def test_position_limit_and_open_positions(self):
        cands = [cand(symbol="AUSDT"), cand(symbol="BUSDT"), cand(symbol="CUSDT")]
        flt = {s: FLT for s in ("AUSDT", "BUSDT", "CUSDT")}
        journal = {"open": [{"symbol": "ZUSDT", "qty": 0.1, "entry": 100.0}, {"symbol": "BUSDT", "qty": 0.1, "entry": 100.0}]}
        plan = size.build(cands, flt, 100.0, cfg(max_open_positions=3), journal)
        self.assertEqual([o["symbol"] for o in plan["orders"]], ["AUSDT"])
        reasons = {s["symbol"]: s["reason"] for s in plan["skipped"]}
        self.assertIn("already an open position", reasons["BUSDT"])
        self.assertIn("max_open_positions", reasons["CUSDT"])

    def test_open_positions_count_against_the_cash_buffer(self):
        journal = {"open": [{"symbol": "ZUSDT", "qty": 0.8, "entry": 100.0}]}   # 80 USDT already deployed
        plan = size.build([cand(symbol="AUSDT")], {"AUSDT": FLT}, 100.0, cfg(), journal)
        # 100 * 0.9 - 80 = 10 USDT may be deployed
        self.assertLessEqual(plan["orders"][0]["notional"], 10.0 + 1e-9)

    def test_total_never_dips_into_the_buffer(self):
        cands = [cand(100, 99, 102, symbol=s) for s in ("AUSDT", "BUSDT", "CUSDT")]
        flt = {s: FLT for s in ("AUSDT", "BUSDT", "CUSDT")}
        plan = size.build(cands, flt, 100.0, cfg(max_position_pct=50, max_open_positions=5), {})
        self.assertLessEqual(sum(o["notional"] for o in plan["orders"]), 90.0 + 1e-9)
        self.assertGreaterEqual(plan["cash_after"], 10.0 - 1e-9)


if __name__ == "__main__":
    unittest.main()
