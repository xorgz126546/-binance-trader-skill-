#!/usr/bin/env python3
"""Run the Python reference on synthetic data and record the results for the JavaScript parity test.

  python3 -I tests/js/make_fixtures.py        # writes tests/js/fixtures.json
"""
import copy
import json
import os
import sys
import types
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import _load  # noqa: E402
import _synth as S  # noqa: E402

scan = _load.load("binance-scanner", "scan")
an = _load.load("binance-analyst", "analyze")
size = _load.load("binance-budget", "size")
chk = _load.load("binance-risk", "check")
jr = _load.load("binance-journal", "journal")
al = _load.load("binance-alerts", "alerts")
rb = _load.load("binance-rebalance", "rebalance")
nw = _load.load("binance-news", "news")

FIXED = datetime.fromtimestamp(S.NOW / 1000.0, tz=timezone.utc)


class FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED if tz else FIXED.replace(tzinfo=None)


for m in (size, chk, jr, al, rb):
    m.datetime = FixedDT
for m in (size,):
    m.time = types.SimpleNamespace(time=lambda: FIXED.timestamp(), sleep=lambda s: None)
scan.now_ms = lambda: S.NOW
an.now_ms = lambda: S.NOW
scan.time = types.SimpleNamespace(time=lambda: FIXED.timestamp(), sleep=lambda s: None)


def clean(x):
    return json.loads(json.dumps(x))


def specs_raw(specs):
    api = S.fake_api(specs)
    out = {"exchangeInfo": api("/api/v3/exchangeInfo"), "tickers": api("/api/v3/ticker/24hr"),
           "books": api("/api/v3/ticker/bookTicker"), "klines": {}, "prices": {}}
    for sym in specs:
        out["klines"][sym] = {tf: api("/api/v3/klines", {"symbol": sym, "interval": tf, "limit": 100}) for tf in ("4h", "1h", "15m")}
        out["klines"][sym]["1h200"] = api("/api/v3/klines", {"symbol": sym, "interval": "1h", "limit": 200})
        out["klines"][sym]["4h120"] = api("/api/v3/klines", {"symbol": sym, "interval": "4h", "limit": 120})
        out["klines"][sym]["15m120"] = api("/api/v3/klines", {"symbol": sym, "interval": "15m", "limit": 120})
        out["prices"][sym] = api("/api/v3/ticker/price", {"symbol": sym})["price"]
    return out


fx = {"now": S.NOW}
cfg_scan = scan.load_config("/nonexistent")

# ---- scanner (both market states)
fx["scan"] = {}
for name, up in (("btc_up", True), ("btc_down", False)):
    specs = S.universe(up)
    scan.fetch_json = S.fake_api(specs)
    rep = scan.run(dict(cfg_scan), None, 40, 0)
    fx["scan"][name] = {"raw": specs_raw(specs), "expected": clean(rep)}
specs = S.universe(True)
scan.fetch_json = S.fake_api(specs)
rep = scan.run(dict(cfg_scan), ["UPUSDT", "DNUSDT"], 40, 0)
fx["scan"]["picked"] = {"symbols": ["UPUSDT", "DNUSDT"], "expected": clean(rep)}

# ---- analyst
an.fetch_json = S.fake_api(specs)
fx["analyst"] = {s: clean(an.analyse(s, S.NOW)) for s in ("UPUSDT", "DNUSDT")}

# ---- budget
up_cand = next(c for c in fx["scan"]["btc_up"]["expected"]["candidates"] if c["symbol"] == "UPUSDT")
filters = {i["symbol"]: size.parse_filters(i) for i in S.fake_api(specs)("/api/v3/exchangeInfo")["symbols"]}
fx["budget"] = []
for balance in (1000.0, 100.0, 20.0, 5.0):
    for extra in ({}, {"risk_per_trade_pct": 0.5, "max_position_pct": 40.0}, {"min_notional_bump": False}):
        cfg = size.load_config("/nonexistent"); cfg.update(extra)
        for journal in ({}, {"open": [{"symbol": "UPUSDT", "qty": 1, "entry": 100, "fee_in": 0.1}]},
                        {"open": [{"symbol": "AAAUSDT", "qty": 3, "entry": 100, "fee_in": 0.3}] * 1}):
            plan = size.build([up_cand, dict(up_cand, symbol="DNUSDT")], filters, balance, cfg, journal, 5)
            fx["budget"].append({"balance": balance, "extra": extra, "journal": journal, "cand": [up_cand, dict(up_cand, symbol="DNUSDT")],
                                 "expected": clean(plan)})
fx["filters"] = {s: clean(f) for s, f in filters.items()}

# ---- risk
cfg_risk = chk.load_config("/nonexistent")
cfg_risk["budget_usdt"] = 1000.0
good = next(b for b in fx["budget"] if b["balance"] == 1000.0 and not b["extra"] and not b["journal"])["expected"]["orders"][0]
variants = {
    "good": good,
    "risk3": dict(good, risk_pct=3.0),
    "stop_above": dict(good, stop_price=good["price"] + 1),
    "market": dict(good, type="MARKET"),
    "margin": dict(good, leverage=3),
    "stale": dict(good, created_at="2027-01-15T06:00:00+00:00"),
    "oversize": dict(good, quantity=good["quantity"] * 5, notional=good["notional"] * 5),
    "lowrr": dict(good, reward_risk=1.0),
    "blocked_base": dict(good),
    "sell_no_hold": {"id": "x-RB-1", "kind": "rebalance", "market": "SPOT", "symbol": "UPUSDT", "side": "SELL", "type": "LIMIT",
                      "price": 100.0, "quantity": 5.0, "created_at": "2027-01-15T07:59:00+00:00", "status": "proposed"},
    "rb_buy": {"id": "x-RB-2", "kind": "rebalance", "market": "SPOT", "symbol": "UPUSDT", "side": "BUY", "type": "LIMIT",
               "price": 100.0, "quantity": 5.0, "created_at": "2027-01-15T07:59:00+00:00", "status": "proposed"},
}
today = FIXED.date().isoformat()
journals = {
    "empty": {},
    "held": {"open": [{"symbol": "UPUSDT", "qty": 1, "entry": 100, "fee_in": 0.1}]},
    "full": {"open": [{"symbol": s, "qty": 1, "entry": 10, "fee_in": 0.01} for s in ("AUSDT", "BUSDT", "CUSDT")]},
    "lossy": {"open": [], "closed": [{"symbol": "ZUSDT", "pnl": -45.0, "closed_at": today + "T05:00:00+00:00"}]},
}
portfolio = {"balances": [{"asset": "USDT", "amount": 300}, {"asset": "UP", "amount": 2}]}
fx["risk"] = []
for vname, order in variants.items():
    for jname, jn in journals.items():
        for live in (False, True):
            cfg = dict(cfg_risk)
            if vname == "blocked_base":
                cfg["blocked"] = ["UP"]
            doc = {"orders": [copy.deepcopy(order)]}
            chk.fetch_json = S.fake_api(specs)
            res = chk.run(doc, cfg, copy.deepcopy(jn), portfolio, live)
            fx["risk"].append({"variant": vname, "journal_name": jname, "live": live, "cfg": cfg, "order": order, "journal": jn, "portfolio": portfolio,
                               "prices": {"UPUSDT": float(S.fake_api(specs)("/api/v3/ticker/price", {"symbol": "UPUSDT"})["price"])},
                               "expected": [{"verdict": o["risk"]["verdict"], "reasons": o["risk"]["reasons"], "status": o["status"]} for o in res]})

# ---- journal
jcfg = {"fee_pct": 0.1}
j = {"open": [], "closed": []}
jr.fee_pct = lambda: 0.1
steps = []
def step(kind, *a, **k):
    r = (jr.open_position if kind == "open" else jr.close_position)(j, *a, **k)
    steps.append({"kind": kind, "args": list(a), "kwargs": k, "result": clean(r)})
step("open", "sol", 100.0, 2.0, 95.0, 110.0)
step("open", "BTCUSDT", 50000.0, 0.01, 49000.0, 52000.0)
step("close", "SOL", 108.0)
step("close", "BTCUSDT", 49000.0, 0.004)
step("close", "BTCUSDT", 50500.0)
step("open", "ETH", 3000.0, 0.1, None, None, 0.5)
step("close", "ETH", 2900.0, None, 0.4)
fx["journal"] = {"steps": steps, "final": clean(j), "stats": clean(jr.stats(j["closed"]))}

# ---- alerts
doc = {"alerts": []}
al.add_alert(doc, "sol", "above", 120.0)
al.add_alert(doc, "BTCUSDT", "below", 49000.0, "stop", "open position")
al.add_alert(doc, "ETH", "above", 3200.0, "target")
fired = al.check(doc, False, {"SOLUSDT": 121.0, "BTCUSDT": 50000.0, "ETHUSDT": 3100.0})
fx["alerts"] = {"fired": [{"id": a["id"], "price": p, "text": t} for a, p, t in fired], "ids": [a["id"] for a in doc["alerts"]]}

# ---- rebalance
prices = {"BTCUSDT": 70000.0, "UPUSDT": 172.45, "DNUSDT": 80.0}
port = {"balances": [{"asset": "BTC", "amount": 0.01}, {"asset": "UP", "amount": 4}, {"asset": "USDT", "amount": 100}, {"asset": "DN", "amount": 0}]}
targets = {"BTC": 40, "UP": 30, "DN": 10, "USDT": 20}
rows, breach, unpriced, untargeted, total = rb.compute(port, targets, prices, 5.0, 10.0)
rflt = {s: rb.parse_filters(i) for s, i in ((i["symbol"], i) for i in S.fake_api(specs)("/api/v3/exchangeInfo")["symbols"])}
orders, skipped = rb.make_orders(rows, rflt, 10.0, 0.1)
fx["rebalance"] = {"portfolio": port, "targets": targets, "prices": prices, "rows": clean(rows), "breach": breach, "unpriced": unpriced,
                   "untargeted": untargeted, "total": total, "orders": clean(orders), "skipped": clean(skipped), "filters": clean(rflt)}

# ---- news text rules
titles = ["Hackers drain $40M from a DeFi bridge, Solana users hit", "Binance will list NEWCOIN (NEW) with seed tag", "SEC approves Bitcoin ETF options, ETH next",
          "Ethereum network upgrade scheduled; withdrawals paused for maintenance", "Token unlock of 5% for ARB this week", "Dogecoin rallies as Elon posts again",
          "Binance Will Delist ABC, XYZ and 3 other tokens", "Weather in Paris", "Bitcoin Layer-2 SOL-based bridge lists tokens"]
fx["news"] = {"titles": titles, "coins": ["BTC", "ETH", "SOL", "ARB", "DOGE"],
              "classify": [list(nw.classify(t)) for t in titles], "coins_in": [nw.coins_in(t, ["BTC", "ETH", "SOL", "ARB", "DOGE"]) for t in titles]}

with open(os.path.join(HERE, "fixtures.json"), "w") as fh:
    json.dump(fx, fh)
print("wrote fixtures: %d budget cases, %d risk cases" % (len(fx["budget"]), len(fx["risk"])))
