#!/usr/bin/env python3
"""binance-scanner: rank Binance Spot USDT pairs on a 4h trend / 1h confirmation / 15m entry.

Read-only. Public market data only: no API key, no account access, no orders.
Standard library only (Python 3.8+).

  python3 scan.py                      # scan the top 40 USDT pairs by 24h volume
  python3 scan.py --write              # also write binance/candidates.json
  python3 scan.py --symbols BTCUSDT,SOLUSDT --min-score 60
  python3 scan.py --json
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
BASES = [b for b in (os.environ.get("BINANCE_BASE_URL"), "https://data-api.binance.vision",
                     "https://api.binance.com") if b]
UA = "binance-trader-skill/1.0"

DEFAULTS = {
    "min_score": 75,            # 0-100, a setup needs at least this to qualify
    "min_quote_volume": 5000000,  # 24h USDT volume floor
    "max_spread_pct": 0.15,     # best bid/ask spread ceiling, in percent
    "min_atr_pct": 0.15,        # 15m ATR as percent of price, floor and ceiling
    "max_atr_pct": 2.5,
    "stop_atr_mult": 2.0,       # stop = entry - mult * ATR(15m)
    "min_stop_pct": 1.0,        # but never closer than this percent
    "btc_filter": True,         # no new setups while BTC is under its 1h EMA50
    "blocked": [],              # base assets to never scan, e.g. ["LUNC"]
    "exclude_bstocks": True,    # Binance's tokenized US stocks/ETFs (GOOGLB, QQQB...) are not crypto trades
    "bstock_allow": [],         # extra real coins whose ticker happens to end in B
}
# stablecoins and fiat bases are not trades
STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDD", "USD1", "USDE", "USDS", "USDG", "RLUSD", "PYUSD",
           "BFUSD", "AEUR", "EURI", "EUR", "GBP", "TRY", "BRL", "ARS", "PLN", "RON", "UAH", "ZAR", "MXN", "CZK", "JPY", "XUSD"}
# real coins whose ticker ends in B (everything else shaped like TICKERB is a tokenized stock)
BSTOCK_KEEP = {"BNB", "SHIB", "TRB", "CKB", "BTCB"}


def is_stable_base(base):
    return base in STABLES or bool(re.search(r"USD$", base)) or bool(re.match(r"^USD[A-Z0-9]$", base))


def is_bstock(base, cfg):
    if base in BSTOCK_KEEP or base in {x.upper() for x in cfg.get("bstock_allow", [])}:
        return False
    return bool(re.match(r"^[A-Z]{2,5}B$", base))


class ApiError(Exception):
    pass


def fetch_json(path, params=None, timeout=20):
    """GET a public Binance market-data endpoint. Read-only, no key, no signing."""
    qs = urllib.parse.urlencode(params or {})
    last = None
    for base in BASES:
        url = base + path + ("?" + qs if qs else "")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (418, 429):
                raise ApiError("Binance rate limit (HTTP %d). Wait a minute and use fewer symbols." % exc.code)
            if exc.code == 400:
                raise ApiError("Binance rejected the request %s: %s" % (path, exc.read()[:200]))
            last = "HTTP %d from %s" % (exc.code, base)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = "%s: %s" % (base, exc)
    raise ApiError("Binance public API unreachable (%s). If your network blocks it, "
                   "set BINANCE_BASE_URL." % last)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except ValueError as exc:
        sys.exit("%s is not valid JSON: %s" % (path, exc))


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_config(path):
    cfg = dict(DEFAULTS)
    user = load_json(path, {}) or {}
    cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    return cfg


def now_ms():
    return int(time.time() * 1000)


# ------------------------------------------------------------------ indicators

def ema(values, period):
    """EMA series aligned to values[period-1:]. Empty when there is not enough data."""
    if period < 1 or len(values) < period:
        return []
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    out = [e]
    for v in values[period:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gain = loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gain += max(d, 0.0)
        loss += max(-d, 0.0)
    ag, al = gain / period, loss / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def atr(candles, period=14):
    if len(candles) <= period:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def parse_klines(raw, now=None):
    """Binance kline arrays -> dicts. The still-forming last candle is dropped."""
    now = now_ms() if now is None else now
    out = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
            "c": float(k[4]), "v": float(k[5]), "ct": int(k[6]), "qv": float(k[7])} for k in raw]
    if out and out[-1]["ct"] > now:
        out.pop()
    return out


# ------------------------------------------------------------------ scoring

def analyse(c4, c1, c15, cfg):
    """Score one symbol 0-100. Returns None when there is not enough history.

    trend 25   4h: close above EMA50 and EMA20 above EMA50 (12 for close above EMA50 only)
    confirm 20 1h: close above EMA20 above EMA50 (12, or 6 above EMA50), RSI 50-70 (+8) or 45-50 / 70-75 (+4)
    entry 25   15m: close breaks the prior 20-candle high (25), or holds a retest of EMA20 (18),
               or sits within 0.3 percent below the high (8)
    volume 15  last 15m volume vs the previous 20: 2x (15), 1.5x (10), 1x (5)
    heat 15    15m RSI at most 70 (15), at most 78 (7), else 0
    """
    if min(len(c4), len(c1), len(c15)) < 55:
        return None
    cl4, cl1, cl15 = [c["c"] for c in c4], [c["c"] for c in c1], [c["c"] for c in c15]
    close4, close1, close15 = cl4[-1], cl1[-1], cl15[-1]
    e20_4, e50_4 = ema(cl4, 20)[-1], ema(cl4, 50)[-1]
    e20_1, e50_1 = ema(cl1, 20)[-1], ema(cl1, 50)[-1]
    e20_15, e50_15 = ema(cl15, 20)[-1], ema(cl15, 50)[-1]
    rsi1, rsi15 = rsi(cl1), rsi(cl15)
    atr15 = atr(c15)

    if close4 > e50_4 and e20_4 > e50_4:
        trend = 25
    elif close4 > e50_4:
        trend = 12
    else:
        trend = 0

    confirm = 0
    if close1 > e20_1 > e50_1:
        confirm = 12
    elif close1 > e50_1:
        confirm = 6
    if rsi1 is not None:
        if 50 <= rsi1 <= 70:
            confirm += 8
        elif 45 <= rsi1 < 50 or 70 < rsi1 <= 75:
            confirm += 4

    prior_high = max(c["h"] for c in c15[-21:-1])
    touched = any(c["l"] <= e20_15 * 1.003 for c in c15[-3:])
    if close15 > prior_high:
        entry, setup = 25, "breakout"
    elif e20_15 > e50_15 and touched and e20_15 < close15 <= e20_15 * 1.01:
        entry, setup = 18, "retest"
    elif close15 >= prior_high * 0.997:
        entry, setup = 8, "near-breakout"
    else:
        entry, setup = 0, "none"

    avg_vol = sum(c["v"] for c in c15[-21:-1]) / 20.0
    rel_vol = (c15[-1]["v"] / avg_vol) if avg_vol > 0 else 0.0
    volume = 15 if rel_vol >= 2.0 else 10 if rel_vol >= 1.5 else 5 if rel_vol >= 1.0 else 0

    heat = 15 if (rsi15 is not None and rsi15 <= 70) else 7 if (rsi15 is not None and rsi15 <= 78) else 0

    atr_pct = (atr15 / close15 * 100.0) if (atr15 and close15) else 0.0
    dist = max(cfg["stop_atr_mult"] * (atr15 or 0.0), close15 * cfg["min_stop_pct"] / 100.0)
    return {
        "score": trend + confirm + entry + volume + heat,
        "breakdown": {"trend": trend, "confirm": confirm, "entry": entry, "volume": volume, "heat": heat},
        "setup": setup,
        "price": close15,
        "entry": close15,
        "stop": close15 - dist,
        "take_profit": close15 + 2.0 * dist,       # 2R
        "take_profit_2": close15 + 3.0 * dist,     # 3R
        "stop_pct": dist / close15 * 100.0,
        "atr15_pct": atr_pct,
        "rsi15": rsi15,
        "rsi1h": rsi1,
        "rel_volume": rel_vol,
        "trend4h_ok": close4 > e50_4,
    }


def btc_state(c1):
    """BTC is fine when its last closed 1h candle is above its 1h EMA50."""
    if len(c1) < 55:
        return {"ok": True, "known": False}
    closes = [c["c"] for c in c1]
    e50 = ema(closes, 50)[-1]
    return {"ok": closes[-1] > e50, "known": True, "close": closes[-1], "ema50_1h": e50}


def evaluate(sym, ticker, book, c4, c1, c15, cfg, btc_ok):
    a = analyse(c4, c1, c15, cfg)
    if a is None:
        return None
    book = book or {}
    bid, ask = float(book.get("bidPrice") or 0), float(book.get("askPrice") or 0)
    spread = ((ask - bid) / ((ask + bid) / 2) * 100.0) if bid > 0 and ask > 0 else None
    qv = float(ticker.get("quoteVolume") or 0)
    fails = []
    if not a["trend4h_ok"]:
        fails.append("4h close is below EMA50 (no longs against the 4h trend)")
    if spread is None:
        fails.append("no order book data")
    elif spread > cfg["max_spread_pct"]:
        fails.append("spread %.3f%% is wider than %.2f%%" % (spread, cfg["max_spread_pct"]))
    if not (cfg["min_atr_pct"] <= a["atr15_pct"] <= cfg["max_atr_pct"]):
        fails.append("15m ATR %.2f%% is outside %.2f-%.2f%%" % (a["atr15_pct"], cfg["min_atr_pct"], cfg["max_atr_pct"]))
    if qv < cfg["min_quote_volume"]:
        fails.append("24h volume %.0f USDT is under %.0f" % (qv, cfg["min_quote_volume"]))
    if cfg["btc_filter"] and not btc_ok:
        fails.append("BTC is under its 1h EMA50 (market filter)")
    if a["score"] < cfg["min_score"]:
        fails.append("score %d is under %d" % (a["score"], cfg["min_score"]))
    a.update({
        "symbol": sym,
        "qualified": not fails,
        "why_not": fails,
        "spread_pct": spread,
        "quote_volume_24h": qv,
        "change_24h_pct": float(ticker.get("priceChangePercent") or 0),
    })
    return a


# ------------------------------------------------------------------ scan

def pick_universe(info, tickers, cfg, symbols=None, top=40):
    ok = {}
    blocked = {b.upper() for b in cfg["blocked"]}
    for s in info.get("symbols", []):
        if s.get("status") != "TRADING" or s.get("quoteAsset") != "USDT":
            continue
        if s.get("isSpotTradingAllowed") is False:
            continue
        base = s.get("baseAsset", "")
        if is_stable_base(base) or base in blocked or (cfg.get("exclude_bstocks", True) and is_bstock(base, cfg)):
            continue
        ok[s["symbol"]] = s
    rows = [t for t in tickers if t.get("symbol") in ok and float(t.get("lastPrice") or 0) > 0]
    if symbols:
        wanted = [x.upper() for x in symbols]
        rows = [t for t in rows if t["symbol"] in wanted]
    else:
        rows = [t for t in rows if float(t.get("quoteVolume") or 0) >= cfg["min_quote_volume"]]
    rows.sort(key=lambda t: float(t.get("quoteVolume") or 0), reverse=True)
    return rows[:top] if not symbols else rows


def run(cfg, symbols=None, top=40, pause=0.05, ignore_btc=False, log=print, workers=6):
    info = fetch_json("/api/v3/exchangeInfo")
    tickers = fetch_json("/api/v3/ticker/24hr")
    books = {b["symbol"]: b for b in fetch_json("/api/v3/ticker/bookTicker")}
    universe = pick_universe(info, tickers, cfg, symbols, top)
    now = now_ms()
    btc = btc_state(parse_klines(fetch_json("/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1h", "limit": 100}), now))
    btc_ok = True if ignore_btc else btc["ok"]

    def work(t):
        sym = t["symbol"]
        try:
            frames = [parse_klines(fetch_json("/api/v3/klines", {"symbol": sym, "interval": tf, "limit": 100}), now)
                      for tf in ("4h", "1h", "15m")]
        except ApiError as exc:
            if "rate limit" in str(exc):
                raise
            return {"error": {"symbol": sym, "error": str(exc)}}
        item = evaluate(sym, t, books.get(sym, {}), frames[0], frames[1], frames[2], cfg, btc_ok)
        time.sleep(pause)
        return {"error": {"symbol": sym, "error": "not enough candle history"}} if item is None else {"item": item}

    results, errors = [], []
    # several requests at once: a slow network made the one-at-a-time version take minutes
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for out in pool.map(work, universe):
            if "error" in out:
                errors.append(out["error"])
            else:
                results.append(out["item"])
    results.sort(key=lambda r: r["score"], reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": {"btc": btc, "btc_filter_applied": cfg["btc_filter"] and not ignore_btc},
        "params": {k: cfg[k] for k in DEFAULTS if k not in ("blocked", "bstock_allow")},
        "scanned": len(universe),
        "candidates": [r for r in results if r["qualified"]],
        "watch": [r for r in results if not r["qualified"]][:10],
        "errors": errors,
    }


def fmt(x):
    if x is None:
        return "-"
    return ("%.6f" % x).rstrip("0").rstrip(".") if x < 10 else "%.2f" % x


def show(report):
    btc = report["market"]["btc"]
    if btc.get("known"):
        print("BTC 1h: %s (close %s vs EMA50 %s)" % ("OK" if btc["ok"] else "BELOW EMA50", fmt(btc["close"]), fmt(btc["ema50_1h"])))
    print("scanned %d pairs, %d qualified (min score %s)\n" % (
        report["scanned"], len(report["candidates"]), report["params"]["min_score"]))
    head = "%-12s %5s  %-13s %-26s %6s %5s %5s"
    print(head % ("symbol", "score", "setup", "entry / stop / tp(2R)", "relvol", "rsi15", "stop%"))
    for r in report["candidates"]:
        print(head % (r["symbol"], r["score"], r["setup"], "%s / %s / %s" % (fmt(r["entry"]), fmt(r["stop"]), fmt(r["take_profit"])),
                      "%.1fx" % r["rel_volume"], "%.0f" % (r["rsi15"] or 0), "%.2f" % r["stop_pct"]))
    if not report["candidates"]:
        print("NO TRADE: nothing passed. Closest setups below (the reason each failed is shown).")
    if report["watch"]:
        print("\nwatch list (did not qualify):")
        for r in report["watch"][:8]:
            b = r["breakdown"]
            print("  %-12s %3d  T%d C%d E%d V%d H%d  %s" % (r["symbol"], r["score"], b["trend"], b["confirm"], b["entry"],
                                                       b["volume"], b["heat"], "; ".join(r["why_not"][:2])))
    for e in report["errors"][:5]:
        print("  skipped %s: %s" % (e["symbol"], e["error"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", help="comma separated, e.g. BTCUSDT,SOLUSDT (default: top by 24h volume)")
    ap.add_argument("--top", type=int, default=40, help="how many pairs to scan (default 40)")
    ap.add_argument("--min-score", type=int, help="override config min_score")
    ap.add_argument("--min-volume", type=float, help="override config min_quote_volume (USDT)")
    ap.add_argument("--ignore-btc", action="store_true", help="skip the BTC market filter")
    ap.add_argument("--pause", type=float, default=0.05, help="seconds each worker waits after a symbol")
    ap.add_argument("--workers", type=int, default=6, help="parallel requests (default 6)")
    ap.add_argument("--include-bstocks", action="store_true", help="also scan Binance's tokenized stocks (excluded by default)")
    ap.add_argument("--config", default=os.path.join(DATA_DIR, "config.json"))
    ap.add_argument("--write", action="store_true", help="write %s" % os.path.join(DATA_DIR, "candidates.json"))
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if args.min_score is not None:
        cfg["min_score"] = args.min_score
    if args.min_volume is not None:
        cfg["min_quote_volume"] = args.min_volume
    if args.include_bstocks:
        cfg["exclude_bstocks"] = False
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    try:
        report = run(cfg, symbols, args.top, args.pause, args.ignore_btc, workers=args.workers)
    except ApiError as exc:
        sys.exit("scan failed: %s" % exc)
    if args.write:
        save_json(os.path.join(DATA_DIR, "candidates.json"), report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        show(report)
        if args.write:
            print("\nwrote %s" % os.path.join(DATA_DIR, "candidates.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
