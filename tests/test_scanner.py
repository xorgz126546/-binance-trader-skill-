import unittest

import _synth as S
from _load import load

scan = load("binance-scanner", "scan")


class Indicators(unittest.TestCase):
    def test_ema_of_a_constant_is_the_constant(self):
        self.assertTrue(all(abs(x - 5.0) < 1e-12 for x in scan.ema([5.0] * 30, 10)))

    def test_ema_needs_enough_data(self):
        self.assertEqual(scan.ema([1, 2, 3], 10), [])

    def test_rsi_extremes(self):
        self.assertEqual(scan.rsi([float(i) for i in range(1, 40)]), 100.0)
        self.assertAlmostEqual(scan.rsi([float(i) for i in range(40, 1, -1)]), 0.0, places=6)

    def test_atr_of_fixed_range_candles(self):
        candles = [{"h": 11.0, "l": 9.0, "c": 10.0} for _ in range(30)]
        self.assertAlmostEqual(scan.atr(candles), 2.0)

    def test_parse_klines_drops_the_forming_candle(self):
        raw = S.klines(S.closes_up(60), "15m")
        self.assertEqual(len(raw), 61)
        self.assertEqual(len(scan.parse_klines(raw, S.NOW)), 60)
        self.assertEqual(len(scan.parse_klines(S.klines(S.closes_up(60), "15m", forming=False), S.NOW)), 60)


class Scoring(unittest.TestCase):
    def setUp(self):
        self.cfg = scan.load_config("/nonexistent")

    def frames(self, c4, c1, c15, v15=None):
        return (scan.parse_klines(S.klines(c4, "4h"), S.NOW), scan.parse_klines(S.klines(c1, "1h"), S.NOW),
                scan.parse_klines(S.klines(c15, "15m", v15), S.NOW))

    def test_breakout_in_an_uptrend_scores_the_full_parts(self):
        c15, v15 = S.breakout_15m()
        a = scan.analyse(*self.frames(S.closes_up(100), S.closes_up(100), c15, v15), self.cfg)
        b = a["breakdown"]
        self.assertEqual((b["trend"], b["entry"], b["volume"]), (25, 25, 15))
        self.assertEqual(a["setup"], "breakout")
        self.assertEqual(a["score"], sum(b.values()))

    def test_levels_are_ordered_and_targets_are_2r_and_3r(self):
        c15, v15 = S.breakout_15m()
        a = scan.analyse(*self.frames(S.closes_up(100), S.closes_up(100), c15, v15), self.cfg)
        self.assertLess(a["stop"], a["entry"])
        self.assertLess(a["entry"], a["take_profit"])
        risk = a["entry"] - a["stop"]
        self.assertAlmostEqual(a["take_profit"] - a["entry"], 2 * risk, places=6)
        self.assertAlmostEqual(a["take_profit_2"] - a["entry"], 3 * risk, places=6)
        self.assertGreaterEqual(a["stop_pct"], self.cfg["min_stop_pct"] - 1e-9)

    def test_downtrend_scores_no_trend_points(self):
        a = scan.analyse(*self.frames(S.closes_down(100), S.closes_down(100), S.closes_down(100)), self.cfg)
        self.assertEqual(a["breakdown"]["trend"], 0)
        self.assertFalse(a["trend4h_ok"])

    def test_too_little_history_returns_none(self):
        short = scan.parse_klines(S.klines(S.closes_up(30), "4h"), S.NOW)
        self.assertIsNone(scan.analyse(short, short, short, self.cfg))

    def test_evaluate_gates(self):
        c15, v15 = S.breakout_15m()
        f = self.frames(S.closes_up(100), S.closes_up(100), c15, v15)
        tick = {"quoteVolume": "80000000", "priceChangePercent": "1"}
        tight = {"bidPrice": "124.17", "askPrice": "124.18"}
        wide = {"bidPrice": "120.00", "askPrice": "124.18"}
        ok = scan.evaluate("X", tick, tight, *f, self.cfg, True)
        self.assertTrue(ok["qualified"], ok["why_not"])
        self.assertTrue(any("spread" in w for w in scan.evaluate("X", tick, wide, *f, self.cfg, True)["why_not"]))
        self.assertTrue(any("BTC" in w for w in scan.evaluate("X", tick, tight, *f, self.cfg, False)["why_not"]))
        self.assertTrue(any("volume" in w for w in scan.evaluate("X", dict(tick, quoteVolume="1000"), tight, *f, self.cfg, True)["why_not"]))
        self.assertTrue(any("no order book" in w for w in scan.evaluate("X", tick, {}, *f, self.cfg, True)["why_not"]))


class FullScan(unittest.TestCase):
    def run_scan(self, btc_up=True, **kw):
        scan.now_ms = lambda: S.NOW
        scan.fetch_json = S.fake_api(S.universe(btc_up))
        return scan.run(scan.load_config("/nonexistent"), pause=0, **kw)

    def test_universe_skips_stablecoins_halted_and_thin_pairs(self):
        rep = self.run_scan()
        seen = {r["symbol"] for r in rep["candidates"] + rep["watch"]}
        self.assertEqual(seen, {"UPUSDT", "DNUSDT", "BTCUSDT"})

    def test_finds_the_strong_pair_and_rejects_the_downtrend(self):
        rep = self.run_scan()
        self.assertEqual([c["symbol"] for c in rep["candidates"]], ["UPUSDT"])
        dn = next(r for r in rep["watch"] if r["symbol"] == "DNUSDT")
        self.assertTrue(any("4h" in w for w in dn["why_not"]))

    def test_btc_filter_blocks_everything_and_says_why(self):
        rep = self.run_scan(btc_up=False)
        self.assertEqual(rep["candidates"], [])
        up = next(r for r in rep["watch"] if r["symbol"] == "UPUSDT")
        self.assertTrue(any("BTC" in w for w in up["why_not"]))

    def test_ignore_btc_lets_it_through(self):
        rep = self.run_scan(btc_up=False, ignore_btc=True)
        self.assertEqual([c["symbol"] for c in rep["candidates"]], ["UPUSDT"])

    def test_symbols_option_scans_only_those(self):
        rep = self.run_scan(symbols=["DNUSDT"])
        self.assertEqual(rep["scanned"], 1)

    def test_a_failing_symbol_is_reported_not_fatal(self):
        base = S.fake_api(S.universe())

        def flaky(path, params=None, timeout=20):
            if path == "/api/v3/klines" and params["symbol"] == "DNUSDT":
                raise scan.ApiError("boom")
            return base(path, params, timeout)
        scan.now_ms = lambda: S.NOW
        scan.fetch_json = flaky
        rep = scan.run(scan.load_config("/nonexistent"), pause=0)
        self.assertEqual([e["symbol"] for e in rep["errors"]], ["DNUSDT"])
        self.assertEqual(len(rep["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
