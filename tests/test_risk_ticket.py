import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import _synth as S
from _load import load

size = load("binance-budget", "size")
check = load("binance-risk", "check")
ticket = load("binance-ticket", "ticket")

FLT = size.parse_filters(S.symbol_info("XUSDT", "X"))


def good_order(symbol="XUSDT"):
    c = {"symbol": symbol, "entry": 100.0, "stop": 95.0, "take_profit": 112.0, "take_profit_2": 118.0, "score": 80, "setup": "breakout"}
    o, why = size.plan_order(c, FLT, 100.0, 90.0, size.load_config("/nonexistent"))
    assert why is None, why
    return o


def cfg(**kw):
    c = check.load_config("/nonexistent")
    c.update(kw)
    return c


def verdict(order, config=None, journal=None, portfolio=None, live=None):
    doc = {"orders": [order]}
    out = check.run(doc, config or cfg(), journal or {}, portfolio or {}, live=False)
    return out[0]["risk"]


class RiskChecks(unittest.TestCase):
    def test_a_sized_order_passes(self):
        r = verdict(good_order())
        self.assertEqual((r["verdict"], r["reasons"]), ("PASS", []))

    def blocked_for(self, order, text, **kw):
        r = verdict(order, **kw)
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertTrue(any(text in x for x in r["reasons"]), r["reasons"])

    def test_missing_or_wrong_stop(self):
        o = good_order()
        o["stop_price"] = 0
        self.blocked_for(o, "stop")
        o = good_order()
        o["stop_price"] = o["price"] + 1
        self.blocked_for(o, "stop")

    def test_leverage_and_margin_are_refused(self):
        o = good_order()
        o["leverage"] = 3
        self.blocked_for(o, "Spot only")
        o = good_order()
        o["market"] = "FUTURES"
        self.blocked_for(o, "Spot only")

    def test_wrong_quote_blocked_coin_and_market_orders(self):
        o = good_order("XBUSD")
        self.blocked_for(o, "quoted in")
        self.blocked_for(good_order(), "blocked list", config=cfg(blocked=["x"]))
        o = good_order()
        o["type"] = "MARKET"
        self.blocked_for(o, "LIMIT")
        self.assertEqual(verdict(o, config=cfg(allow_market_orders=True))["verdict"], "PASS")

    def test_stale_proposals_are_refused(self):
        o = good_order()
        o["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(timespec="seconds")
        self.blocked_for(o, "old")

    def test_weak_reward_risk_and_too_much_risk(self):
        o = good_order()
        o["reward_risk"] = 0.9
        self.blocked_for(o, "reward/risk")
        o = good_order()
        o["risk_pct"] = 2.5
        self.blocked_for(o, "risk 2.50%")

    def test_oversize_position(self):
        o = good_order()
        o["quantity"] *= 3
        self.blocked_for(o, "cap")

    def test_cash_buffer_counts_open_positions(self):
        journal = {"open": [{"symbol": "ZUSDT", "qty": 0.8, "entry": 100.0}]}
        o = good_order()
        self.blocked_for(o, "cash buffer", journal=journal)

    def test_already_open_and_position_limit(self):
        o = good_order()
        self.blocked_for(o, "already an open", journal={"open": [{"symbol": "XUSDT", "qty": 0.01, "entry": 100.0}]})
        three = {"open": [{"symbol": s, "qty": 0.01, "entry": 100.0} for s in ("AUSDT", "BUSDT", "CUSDT")]}
        self.blocked_for(good_order(), "positions are open", journal=three)

    def test_daily_loss_limit_stops_new_buys(self):
        today = datetime.now(timezone.utc).isoformat(timespec="seconds")
        journal = {"closed": [{"symbol": "ZUSDT", "pnl": -3.5, "closed_at": today}]}
        self.blocked_for(good_order(), "daily limit", journal=journal)
        old = {"closed": [{"symbol": "ZUSDT", "pnl": -30.0, "closed_at": "2020-01-01T00:00:00+00:00"}]}
        self.assertEqual(verdict(good_order(), journal=old)["verdict"], "PASS")

    def test_cannot_sell_what_is_not_held(self):
        o = good_order()
        o.update({"side": "SELL", "quantity": 1.0})
        self.blocked_for(o, "cannot short")
        ok = verdict(o, portfolio={"balances": [{"asset": "X", "amount": 2.0}]})
        self.assertEqual(ok["verdict"], "PASS")

    def test_batch_is_checked_as_if_earlier_orders_filled(self):
        a, b = good_order("AUSDT"), good_order("BUSDT")
        a["quantity"] = b["quantity"] = 0.2         # 20 USDT each
        doc = {"orders": [a, b, good_order("CUSDT")]}
        for o in doc["orders"][2:]:
            o["quantity"] = 0.3                    # 30 USDT: 70 deployed, inside 90, but over the 25% cap
        out = check.run(doc, cfg(), {}, {}, live=False)
        self.assertEqual([o["risk"]["verdict"] for o in out], ["PASS", "PASS", "BLOCK"])

    def test_live_price_drift_and_stop(self):
        o = good_order()
        orig = check.fetch_json
        try:
            check.fetch_json = lambda path, params=None, timeout=20: {"symbol": "XUSDT", "price": "101.5"}
            r = check.run({"orders": [dict(o)]}, cfg(), {}, {}, live=True)[0]["risk"]
            self.assertTrue(any("moved" in x for x in r["reasons"]))
            check.fetch_json = lambda path, params=None, timeout=20: {"symbol": "XUSDT", "price": "100.1"}
            self.assertEqual(check.run({"orders": [dict(o)]}, cfg(), {}, {}, live=True)[0]["risk"]["verdict"], "PASS")
            check.fetch_json = lambda path, params=None, timeout=20: {"symbol": "XUSDT", "price": "94.0"}
            self.assertEqual(check.run({"orders": [dict(o)]}, cfg(), {}, {}, live=True)[0]["risk"]["verdict"], "BLOCK")
        finally:
            check.fetch_json = orig

    def test_live_failure_blocks(self):
        def boom(path, params=None, timeout=20):
            raise check.ApiError("down")
        orig = check.fetch_json
        try:
            check.fetch_json = boom
            r = check.run({"orders": [good_order()]}, cfg(), {}, {}, live=True)[0]["risk"]
            self.assertEqual(r["verdict"], "BLOCK")
        finally:
            check.fetch_json = orig

    def test_malformed_order(self):
        self.assertEqual(verdict({"symbol": "XUSDT"})["verdict"], "BLOCK")
        o = good_order()
        o["quantity"] = -1
        self.assertEqual(verdict(o)["verdict"], "BLOCK")

    def test_fingerprints_match_across_the_two_skills_and_change_with_the_order(self):
        o = good_order()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        p = dict(o, price=o["price"] + 0.01)
        self.assertNotEqual(check.fingerprint(o), check.fingerprint(p))
        q = dict(o, status="approved", notes=["x"])         # fields outside the fingerprint do not matter
        self.assertEqual(check.fingerprint(o), check.fingerprint(q))


class TicketFlow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "orders.json")
        self.cfgpath = os.path.join(self.dir.name, "config.json")
        self.order = good_order()
        self.write({"orders": [self.order]})

    def write(self, doc):
        with open(self.path, "w") as fh:
            json.dump(doc, fh)

    def read(self):
        with open(self.path) as fh:
            return json.load(fh)

    def run_risk(self):
        doc = self.read()
        check.run(doc, cfg(), {}, {}, live=False)
        self.write(doc)

    def tk(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ticket.main(["--orders", self.path, "--config", self.cfgpath] + list(argv))
        return code, out.getvalue()

    def test_not_ready_until_risk_passes_and_the_user_says_yes(self):
        _, out = self.tk("show")
        self.assertIn("NOT READY", out)
        self.assertIn("not risk-checked", out)
        self.run_risk()
        _, out = self.tk("show")
        self.assertIn("no yes recorded", out)
        code, out = self.tk("render", "XUSDT")
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", out)

    def test_cannot_approve_without_a_pass(self):
        with self.assertRaises(SystemExit) as cm:
            self.tk("approve", "XUSDT", "--confirm", "yes XUSDT")
        self.assertIn("PASS", str(cm.exception))

    def test_the_confirmation_must_name_the_order(self):
        self.run_risk()
        for bad in ("yes", "ok", "yes BTCUSDT", "yes please", "no XUSDT"):
            with self.assertRaises(SystemExit, msg=bad):
                self.tk("approve", "XUSDT", "--confirm", bad)
        self.assertNotIn("approval", self.read()["orders"][0])

    def test_yes_then_ticket(self):
        self.run_risk()
        self.tk("approve", "XUSDT", "--confirm", "Yes xusdt")
        code, out = self.tk("render", "XUSDT")
        self.assertEqual(code, 0)
        for text in ("Limit", "OCO", self.order["price_str"], self.order["quantity_str"], "FILLED", "Stop (trigger)"):
            self.assertIn(text, out)
        self.assertNotIn("API", out)

    def test_changing_the_order_voids_both_the_verdict_and_the_yes(self):
        self.run_risk()
        self.tk("approve", "XUSDT", "--confirm", "yes XUSDT")
        doc = self.read()
        doc["orders"][0]["quantity"] = doc["orders"][0]["quantity"] * 1.1
        self.write(doc)
        _, out = self.tk("show")
        self.assertIn("changed after the risk check", out)
        self.assertIn("changed after your yes", out)

    def test_an_old_yes_expires(self):
        self.run_risk()
        self.tk("approve", "XUSDT", "--confirm", "yes XUSDT")
        doc = self.read()
        doc["orders"][0]["approval"]["at"] = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat(timespec="seconds")
        self.write(doc)
        _, out = self.tk("show")
        self.assertIn("your yes is", out)

    def test_an_old_risk_check_expires(self):
        self.run_risk()
        self.tk("approve", "XUSDT", "--confirm", "yes XUSDT")
        doc = self.read()
        doc["orders"][0]["risk"]["checked_at"] = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat(timespec="seconds")
        self.write(doc)
        _, out = self.tk("show")
        self.assertIn("risk check is", out)

    def test_a_blocked_order_never_becomes_ready(self):
        doc = self.read()
        doc["orders"][0]["stop_price"] = 0
        self.write(doc)
        self.run_risk()
        with self.assertRaises(SystemExit):
            self.tk("approve", "XUSDT", "--confirm", "yes XUSDT")
        _, out = self.tk("show")
        self.assertIn("BLOCKED by binance-risk", out)

    def test_placed_prints_the_journal_command_and_cancel_closes_it(self):
        self.run_risk()
        self.tk("approve", "XUSDT", "--confirm", "yes XUSDT")
        _, out = self.tk("placed", "XUSDT", "--price", "100.1", "--qty", "0.19")
        self.assertIn("journal.py open XUSDT --price 100.1 --qty 0.19", out)
        self.assertEqual(self.read()["orders"][0]["status"], "placed")
        _, out = self.tk("show")
        self.assertIn("DONE", out)

    def test_two_orders_for_one_symbol_need_the_id(self):
        doc = self.read()
        second = copy.deepcopy(doc["orders"][0])
        second["id"] = "XUSDT-2"
        doc["orders"].append(second)
        self.write(doc)
        with self.assertRaises(SystemExit) as cm:
            self.tk("render", "XUSDT")
        self.assertIn("use the id", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
