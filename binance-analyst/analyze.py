#!/usr/bin/env python3
"""binance-analyst: a deep dive on one Binance Spot USDT pair.

Read-only. Public market data only: no API key, no account access, no orders.
Standard library only (Python 3.8+).

  python3 analyze.py SOL            # SOLUSDT
  python3 analyze.py BTCUSDT --json
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASES = [b for b in (os.environ.get("BINANCE_BASE_URL"), "https://data-api.binance.vision",
                     "https://api.binance.com") if b]
UA = "binance-trader-skill/1.0"


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
                raise ApiError("Binance rate limit (HTTP %d). Wait a minute and retry." % exc.code)
            if exc.code == 400:
                raise ApiError("Binance rejected the request %s: %s" % (path, exc.read()[:200]))
            last = "HTTP %d from %s" % (exc.code, base)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = "%s: %s" % (base, exc)
    raise ApiError("Binance public API unreachable (%s). If your network blocks it, "
                   "set BINANCE_BASE_URL." % last)


def now_ms():
    return int(time.time() * 1000)


def ema(values, period):
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
    now = now_ms() if now is None else now
    out = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
            "c": float(k[4]), "v": float(k[5]), "ct": int(k[6]), "qv": float(k[7])} for k in raw]
    if out and out[-1]["ct"] > now:
        out.pop()
    return out


def pivots(candles, wing=3):
    """Swing highs and lows: a candle higher (lower) than `wing` candles on each side."""
    highs, lows = [], []
    for i in range(wing, len(candles) - wing):
        window = candles[i - wing:i + wing + 1]
        if candles[i]["h"] == max(c["h"] for c in window):
            highs.append(candles[i]["h"])
        if candles[i]["l"] == min(c["l"] for c in window):
            lows.append(candles[i]["l"])
    return highs, lows


def frame(candles):
    closes = [c["c"] for c in candles]
    if len(closes) < 55:
        return None
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    close = closes[-1]
    trend = "up" if close > e50 and e20 > e50 else "down" if close < e50 and e20 < e50 else "mixed"
    a = atr(candles)
    return {"close": close, "ema20": e20, "ema50": e50, "rsi": rsi(closes), "trend": trend,
            "atr": a, "atr_pct": (a / close * 100.0) if a else None}


def normalise(symbol):
    s = symbol.upper().strip()
    return s if s.endswith("USDT") else s + "USDT"


def analyse(symbol, now=None):
    sym = normalise(symbol)
    now = now_ms() if now is None else now
    k4 = parse_klines(fetch_json("/api/v3/klines", {"symbol": sym, "interval": "4h", "limit": 120}), now)
    k1 = parse_klines(fetch_json("/api/v3/klines", {"symbol": sym, "interval": "1h", "limit": 200}), now)
    k15 = parse_klines(fetch_json("/api/v3/klines", {"symbol": sym, "interval": "15m", "limit": 120}), now)
    tick = fetch_json("/api/v3/ticker/24hr", {"symbol": sym})
    book = fetch_json("/api/v3/ticker/bookTicker", {"symbol": sym})
    f4, f1, f15 = frame(k4), frame(k1), frame(k15)
    if not (f4 and f1 and f15):
        raise ApiError("%s has too little candle history to analyse" % sym)
    price = f15["close"]
    highs, lows = pivots(k1[-120:])
    resist = sorted({round(h, 8) for h in highs if h > price})[:3]
    support = sorted({round(l, 8) for l in lows if l < price}, reverse=True)[:3]
    bid, ask = float(book["bidPrice"]), float(book["askPrice"])
    spread = (ask - bid) / ((ask + bid) / 2) * 100.0 if bid > 0 and ask > 0 else None

    ups = [f["trend"] for f in (f4, f1, f15)].count("up")
    downs = [f["trend"] for f in (f4, f1, f15)].count("down")
    verdict = ("BULLISH" if ups == 3 else "LEANING BULLISH" if ups == 2 and downs == 0
               else "BEARISH" if downs == 3 else "LEANING BEARISH" if downs == 2 and ups == 0 else "MIXED")

    dist = max(2.0 * f15["atr"], price * 0.01)
    atr_stop = price - dist
    stop_note = None
    sup_stop = None
    if support and (price - support[0]) / price <= 0.04:
        sup_stop = support[0] * 0.998
        stop_note = "nearest 1h support %s, stop just under it" % support[0]
    vols = [c["v"] for c in k1]
    vol_trend = (sum(vols[-6:]) / 6.0) / (sum(vols[-30:-6]) / 24.0) if len(vols) >= 30 and sum(vols[-30:-6]) > 0 else None
    high24, low24 = float(tick["highPrice"]), float(tick["lowPrice"])
    return {
        "symbol": sym,
        "price": price,
        "verdict": verdict,
        "timeframes": {"4h": f4, "1h": f1, "15m": f15},
        "support": support,
        "resistance": resist,
        "levels": {"atr_stop": atr_stop, "support_stop": sup_stop, "stop_note": stop_note,
                   "target_2r": price + 2 * dist, "target_3r": price + 3 * dist, "stop_pct": dist / price * 100.0},
        "stats": {
            "change_24h_pct": float(tick["priceChangePercent"]),
            "quote_volume_24h": float(tick["quoteVolume"]),
            "high_24h": high24, "low_24h": low24,
            "pct_below_24h_high": (high24 - price) / high24 * 100.0 if high24 else None,
            "volume_trend_1h": vol_trend,
            "spread_pct": spread,
        },
    }


def g(x, n=4):
    if x is None:
        return "-"
    return ("%." + str(n) + "f") % x if x >= 10 else ("%.8f" % x).rstrip("0").rstrip(".")


def show(r):
    print("%s  %s  price %s\n" % (r["symbol"], r["verdict"], g(r["price"])))
    print("%-4s %-6s %12s %12s %12s %6s %7s" % ("tf", "trend", "close", "EMA20", "EMA50", "RSI", "ATR%"))
    for tf in ("4h", "1h", "15m"):
        f = r["timeframes"][tf]
        print("%-4s %-6s %12s %12s %12s %6.1f %7.2f" % (tf, f["trend"], g(f["close"]), g(f["ema20"]), g(f["ema50"]),
                                                      f["rsi"] or 0, f["atr_pct"] or 0))
    print("\nsupport    : %s" % (", ".join(g(x) for x in r["support"]) or "none found nearby"))
    print("resistance : %s" % (", ".join(g(x) for x in r["resistance"]) or "none found nearby"))
    lv = r["levels"]
    print("\nif long: ATR stop %s (-%.2f%%), targets 2R %s / 3R %s" % (g(lv["atr_stop"]), lv["stop_pct"], g(lv["target_2r"]), g(lv["target_3r"])))
    if lv["support_stop"]:
        print("        structure stop %s (%s)" % (g(lv["support_stop"]), lv["stop_note"]))
    s = r["stats"]
    print("\n24h %+.2f%%, volume %.1fM USDT, %.2f%% under the 24h high, spread %s%%, 1h volume trend %s" % (
        s["change_24h_pct"], s["quote_volume_24h"] / 1e6, s["pct_below_24h_high"] or 0,
        "%.3f" % s["spread_pct"] if s["spread_pct"] is not None else "-",
        "%.2fx" % s["volume_trend_1h"] if s["volume_trend_1h"] else "-"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", help="SOL or SOLUSDT")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        r = analyse(args.symbol)
    except ApiError as exc:
        sys.exit("analysis failed: %s" % exc)
    print(json.dumps(r, indent=2)) if args.json else show(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
