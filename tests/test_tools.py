"""alerts, rebalance, journal, news and analyst."""
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone

import _synth as S
from _load import load

alerts = load("binance-alerts", "alerts")
rebalance = load("binance-rebalance", "rebalance")
journal = load("binance-journal", "journal")
news = load("binance-news", "news")
analyze = load("binance-analyst", "analyze")


def run_main(mod, tmp, *argv):
    mod.DATA_DIR = tmp
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = mod.main(list(argv))
    return code, out.getvalue()


class Alerts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        alerts.DATA_DIR = self.dir.name
        self.env = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in self.env.items() if v is not None])

    def test_direction_logic(self):
        a = {"direction": "below", "level": 100.0}
        self.assertTrue(alerts.hit(a, 99.9))
        self.assertTrue(alerts.hit(a, 100.0))
        self.assertFalse(alerts.hit(a, 100.1))
        self.assertTrue(alerts.hit({"direction": "above", "level": 5.0}, 5.0))

    def test_fires_once_then_stays_quiet(self):
        doc = {"alerts": []}
        alerts.add_alert(doc, "sol", "below", 144, "stop")
        alerts.add_alert(doc, "BTCUSDT", "above", 70000)
        self.assertEqual(doc["alerts"][0]["symbol"], "SOLUSDT")
        fired = alerts.check(doc, prices={"SOLUSDT": 143.0, "BTCUSDT": 69000.0})
        self.assertEqual([a["id"] for a, _, _ in fired], ["A1"])
        self.assertIn("STOP", fired[0][2])
        self.assertEqual(alerts.check(doc, prices={"SOLUSDT": 143.0, "BTCUSDT": 69000.0}), [])

    def test_telegram_without_setup_says_why_and_does_not_crash(self):
        doc = {"alerts": []}
        alerts.add_alert(doc, "SOL", "below", 144)
        fired = alerts.check(doc, telegram=True, prices={"SOLUSDT": 100.0})
        self.assertFalse(fired[0][0]["notified"])
        self.assertIn("not set", fired[0][0]["notify_detail"])

    def test_telegram_token_comes_from_the_environment_and_is_never_printed(self):
        sent = []
        alerts.post_form = lambda url, data, timeout=15: sent.append((url, data)) or 200
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "123:SECRET", "42"
        try:
            alerts.save_json(alerts.path_alerts(), {"alerts": [{"id": "A1", "symbol": "SOLUSDT", "direction": "below", "level": 144.0,
                                                               "kind": "stop", "note": "", "created_at": "x", "triggered_at": None, "notified": False}]})
            alerts.fetch_json = lambda path, params=None, timeout=20: [{"symbol": "SOLUSDT", "price": "140"}]
            code, out = run_main(alerts, self.dir.name, "check", "--telegram")
        finally:
            os.environ.pop("TELEGRAM_BOT_TOKEN"), os.environ.pop("TELEGRAM_CHAT_ID")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["chat_id"], "42")
        self.assertNotIn("SECRET", out)
        with open(alerts.path_alerts()) as fh:
            self.assertNotIn("SECRET", fh.read())

    def test_a_failed_telegram_send_hides_the_token(self):
        def boom(url, data, timeout=15):
            raise urllib.error.URLError("cannot reach https://api.telegram.org/bot123:SECRET/sendMessage")
        alerts.post_form = boom
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "123:SECRET", "42"
        try:
            ok, detail = alerts.send_telegram("hi")
        finally:
            os.environ.pop("TELEGRAM_BOT_TOKEN"), os.environ.pop("TELEGRAM_CHAT_ID")
        self.assertFalse(ok)
        self.assertNotIn("SECRET", detail)

    def test_from_journal_builds_stop_and_target_once(self):
        alerts.save_json(os.path.join(self.dir.name, "journal.json"),
                         {"open": [{"symbol": "SOLUSDT", "qty": 1, "entry": 150.0, "stop": 144.0, "target": 160.0}], "closed": []})
        run_main(alerts, self.dir.name, "from-journal")
        run_main(alerts, self.dir.name, "from-journal")
        doc = alerts.load_alerts()
        self.assertEqual(sorted((a["kind"], a["direction"], a["level"]) for a in doc["alerts"]),
                         [("stop", "below", 144.0), ("target", "above", 160.0)])

    def test_check_survives_an_api_failure(self):
        def down(path, params=None, timeout=20):
            raise alerts.ApiError("unreachable")
        alerts.fetch_json = down
        run_main(alerts, self.dir.name, "add", "SOL", "below", "100")
        code, out = run_main(alerts, self.dir.name, "check")
        self.assertEqual(code, 0)
        self.assertIn("check failed", out)


class Rebalance(unittest.TestCase):
    PORT = {"balances": [{"asset": "BTC", "amount": 0.002}, {"asset": "ETH", "amount": 0.1}, {"asset": "USDT", "amount": 20}]}
    PRICES = {"BTCUSDT": 70000.0, "ETHUSDT": 3000.0}          # 140 + 300 + 20 = 460 USDT

    def test_drift_table(self):
        rows, breach, unpriced, untargeted, total = rebalance.compute(self.PORT, {"BTC": 50, "ETH": 30, "USDT": 20}, self.PRICES, 5, 10)
        self.assertAlmostEqual(total, 460.0)
        by = {r["asset"]: r for r in rows}
        self.assertAlmostEqual(by["ETH"]["weight"], 300 / 460 * 100)
        self.assertAlmostEqual(by["BTC"]["trade_usdt"], 230 - 140)
        self.assertTrue(breach)

    def test_inside_the_band_nothing_to_do(self):
        rows, breach, *_ = rebalance.compute(self.PORT, {"BTC": 30, "ETH": 65, "USDT": 5}, self.PRICES, 5, 10)
        self.assertFalse(breach)

    def test_targets_must_add_up(self):
        with self.assertRaises(ValueError):
            rebalance.compute(self.PORT, {"BTC": 50, "ETH": 30}, self.PRICES, 5, 10)

    def test_untargeted_and_unpriced_assets_are_left_alone(self):
        port = {"balances": self.PORT["balances"] + [{"asset": "DOGE", "amount": 100}, {"asset": "FOO", "amount": 1}]}
        rows, _, unpriced, untargeted, total = rebalance.compute(port, {"BTC": 50, "ETH": 30, "USDT": 20}, dict(self.PRICES, DOGEUSDT=0.1), 5, 10)
        self.assertEqual(sorted(untargeted), ["DOGE", "FOO"])
        self.assertAlmostEqual(total, 460.0)

    def test_orders_sell_first_and_are_rounded_and_small_trades_skipped(self):
        rows, *_ = rebalance.compute(self.PORT, {"BTC": 50, "ETH": 30, "USDT": 20}, self.PRICES, 5, 10)
        flt = {"BTCUSDT": rebalance.parse_filters(S.symbol_info("BTCUSDT", "BTC", tick="0.01", step="0.00001")),
               "ETHUSDT": rebalance.parse_filters(S.symbol_info("ETHUSDT", "ETH", tick="0.01", step="0.0001"))}
        orders, skipped = rebalance.make_orders(rows, flt, 10)
        self.assertEqual([o["side"] for o in orders], ["SELL", "BUY"])
        self.assertEqual(orders[0]["symbol"], "ETHUSDT")
        self.assertLessEqual(orders[0]["quantity"], 0.1)                 # never sells more than held
        self.assertEqual(orders[0]["kind"], "rebalance")
        orders, skipped = rebalance.make_orders(rows, flt, 1000)
        self.assertEqual(orders, [])
        self.assertEqual(len(skipped), 2)


class Journal(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        journal.DATA_DIR = self.dir.name

    def test_pnl_is_net_of_fees_and_r_multiple_uses_the_stop(self):
        j = journal.load_journal()
        journal.open_position(j, "sol", 100.0, 1.0, stop=95.0, target=110.0)
        t = journal.close_position(j, "SOLUSDT", 110.0)
        self.assertAlmostEqual(t["gross"], 10.0)
        self.assertAlmostEqual(t["fees"], 0.1 + 0.11)
        self.assertAlmostEqual(t["pnl"], 10.0 - 0.21)
        self.assertAlmostEqual(t["r_multiple"], (10.0 - 0.21) / (5.0 + 0.2))
        self.assertEqual(j["open"], [])

    def test_partial_close_keeps_the_rest_open(self):
        j = journal.load_journal()
        journal.open_position(j, "SOLUSDT", 100.0, 2.0)
        journal.close_position(j, "SOLUSDT", 105.0, qty=0.5)
        self.assertAlmostEqual(j["open"][0]["qty"], 1.5)
        self.assertAlmostEqual(j["closed"][0]["qty"], 0.5)

    def test_real_fees_override_the_default(self):
        j = journal.load_journal()
        journal.open_position(j, "SOLUSDT", 100.0, 1.0, fee=0.05)
        t = journal.close_position(j, "SOLUSDT", 100.0, fee=0.05)
        self.assertAlmostEqual(t["pnl"], -0.10)

    def test_bad_input_is_refused(self):
        j = journal.load_journal()
        journal.open_position(j, "SOLUSDT", 100.0, 1.0)
        for fn in (lambda: journal.open_position(j, "SOLUSDT", 100.0, 1.0),
                   lambda: journal.open_position(j, "ETHUSDT", 100.0, 1.0, stop=101.0),
                   lambda: journal.open_position(j, "ETHUSDT", 0, 1.0),
                   lambda: journal.close_position(j, "BTCUSDT", 1.0),
                   lambda: journal.close_position(j, "SOLUSDT", 1.0, qty=5.0)):
            with self.assertRaises(ValueError):
                fn()

    def test_stats(self):
        closed = [{"pnl": 10.0, "fees": 0.2, "closed_at": "2026-01-01T00:00", "r_multiple": 2.0},
                  {"pnl": -5.0, "fees": 0.2, "closed_at": "2026-01-02T00:00", "r_multiple": -1.0},
                  {"pnl": -3.0, "fees": 0.2, "closed_at": "2026-01-03T00:00", "r_multiple": None},
                  {"pnl": 4.0, "fees": 0.2, "closed_at": "2026-01-04T00:00", "r_multiple": 1.0}]
        s = journal.stats(closed)
        self.assertEqual(s["trades"], 4)
        self.assertAlmostEqual(s["win_rate_pct"], 50.0)
        self.assertAlmostEqual(s["total_pnl"], 6.0)
        self.assertAlmostEqual(s["profit_factor"], 14.0 / 8.0)
        self.assertAlmostEqual(s["avg_r"], (2.0 - 1.0 + 1.0) / 3)
        self.assertAlmostEqual(s["max_drawdown"], 8.0)
        self.assertEqual(journal.stats([]), {"trades": 0})

    def test_cli_round_trip(self):
        run_main(journal, self.dir.name, "open", "SOL", "--price", "100", "--qty", "1", "--stop", "95", "--target", "110")
        _, out = run_main(journal, self.dir.name, "list")
        self.assertIn("SOLUSDT", out)
        _, out = run_main(journal, self.dir.name, "close", "SOL", "--price", "90")
        self.assertIn("-", out)
        _, out = run_main(journal, self.dir.name, "stats")
        self.assertIn("too few", out)
        _, out = run_main(journal, self.dir.name, "today")
        self.assertIn("1 closed", out)


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Solana network outage halts block production</title><link>https://example.com/a</link><pubDate>Thu, 12 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Bitcoin ETF sees record inflows</title><link>https://example.com/b</link><pubDate>Thu, 12 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Old story about Ethereum</title><link>https://example.com/c</link><pubDate>Mon, 02 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>Console makers report earnings</title><link>https://example.com/d</link><pubDate>Thu, 12 Nov 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>SOL unlock of 10M tokens next week</title>
<link href="https://example.com/e"/><updated>2026-11-12T10:30:00Z</updated></entry></feed>"""
ANN = json.dumps({"data": {"catalogs": [{"articles": [
    {"title": "Binance Will Delist ABC, XYZ on 2026-11-20", "code": "c0de1", "releaseDate": 1_794_480_000_000}]}]}}).encode()


class News(unittest.TestCase):
    NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

    def test_rss_and_atom_parse(self):
        items = news.parse_feed(RSS, "T")
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["when"], datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc))
        atom = news.parse_feed(ATOM, "A")
        self.assertEqual((atom[0]["link"], atom[0]["when"].hour), ("https://example.com/e", 10))

    def test_xml_with_entities_is_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'
        with self.assertRaises(ValueError):
            news.parse_feed(bomb, "T")

    def test_coin_matching(self):
        self.assertEqual(news.coins_in("Solana network outage", ["SOL", "BTC"]), ["SOL"])
        self.assertEqual(news.coins_in("SOL unlock next week", ["SOL"]), ["SOL"])
        self.assertEqual(news.coins_in("Console makers report earnings", ["SOL"]), [])
        self.assertEqual(news.coins_in("Bitcoin ETF inflows", ["BTC"]), ["BTC"])
        self.assertEqual(news.coins_in("sol is a word", ["SOL"]), [])           # lowercase ticker alone is not a match

    def test_tags_and_urgency(self):
        self.assertEqual(news.classify("Exchange hacked, $50M drained")[1], True)
        self.assertEqual(news.classify("Binance Will Delist ABC")[0], ["delisting"])
        self.assertEqual(news.classify("Binance Will List NEW on Launchpool")[1], False)
        self.assertEqual(news.classify("Bitcoin ETF approval expected")[0], ["regulation"])

    def test_collect_filters_by_coin_window_and_puts_urgent_first(self):
        def fetch(url, timeout=20):
            if "announcement" in url or "bapi" in url:
                return ANN
            if "coindesk" in url:
                return RSS
            if "cointelegraph" in url:
                return ATOM
            raise urllib.error.URLError("blocked")
        items, problems = news.collect(["SOL", "BTC", "ABC"], 24, now=self.NOW, fetch=fetch)
        titles = [i["title"] for i in items]
        self.assertIn("Solana network outage halts block production", titles)
        self.assertIn("SOL unlock of 10M tokens next week", titles)
        self.assertIn("Binance Will Delist ABC, XYZ on 2026-11-20", titles)
        self.assertNotIn("Old story about Ethereum", titles)
        self.assertNotIn("Console makers report earnings", titles)
        self.assertIn("Bitcoin ETF sees record inflows", titles)
        self.assertTrue(items[0]["high"])
        self.assertEqual({i["title"] for i in items if i["high"]},
                         {"Solana network outage halts block production", "Binance Will Delist ABC, XYZ on 2026-11-20"})
        self.assertTrue(any("Decrypt" in p for p in problems))

    def test_market_mode_keeps_everything_in_the_window(self):
        items, _ = news.collect([], 24, market=True, now=self.NOW, fetch=lambda url, timeout=20: RSS if "coindesk" in url else (_ for _ in ()).throw(OSError("x")))
        self.assertEqual(len(items), 3)

    def test_duplicates_are_dropped(self):
        both = lambda url, timeout=20: RSS
        items, _ = news.collect([], 24, market=True, now=self.NOW, feeds=[("A", "u1"), ("B", "u2")], announce=[], fetch=both)
        self.assertEqual(len(items), 3)

    def test_announcement_parser_is_defensive(self):
        out = news.parse_announcements(ANN, "Binance delisting")
        self.assertEqual(out[0]["link"], news.ANNOUNCE_LINK + "c0de1")
        self.assertEqual(news.parse_announcements(b'{"data": {}}', "x"), [])


class Analyst(unittest.TestCase):
    def test_pivots(self):
        mk = lambda h, l: {"h": h, "l": l}
        candles = [mk(10, 9), mk(11, 10), mk(12, 11), mk(15, 12), mk(12, 11), mk(11, 10), mk(10, 6), mk(11, 8), mk(12, 9), mk(13, 10)]
        highs, lows = analyze.pivots(candles, wing=3)
        self.assertEqual(highs, [15])
        self.assertEqual(lows, [6])

    def test_normalise(self):
        self.assertEqual(analyze.normalise("sol"), "SOLUSDT")
        self.assertEqual(analyze.normalise("BTCUSDT"), "BTCUSDT")

    def test_uptrend_is_bullish_and_downtrend_bearish(self):
        analyze.now_ms = lambda: S.NOW
        analyze.fetch_json = S.fake_api(S.universe())
        up = analyze.analyse("UP")
        self.assertEqual(up["verdict"], "BULLISH")
        self.assertLess(up["levels"]["atr_stop"], up["price"])
        self.assertGreater(up["levels"]["target_3r"], up["levels"]["target_2r"])
        dn = analyze.analyse("DN")
        self.assertEqual(dn["verdict"], "BEARISH")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            analyze.show(up)
        self.assertIn("BULLISH", out.getvalue())


if __name__ == "__main__":
    unittest.main()
