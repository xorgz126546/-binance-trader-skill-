"""Synthetic Binance-shaped data for the offline tests.

Nothing here is real market data. The shapes (kline arrays of strings, exchangeInfo filters, 24hr
and bookTicker rows) follow Binance's public Spot API so the parsing code is exercised for real.
"""
import json
import math

NOW = 1_800_000_000_000          # a fixed "now" in milliseconds
MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def closes_up(n, start=100.0, drift=0.002, wiggle=0.004):
    return [start * (1 + drift) ** i * (1 + wiggle * math.sin(i * 0.9)) for i in range(n)]


def closes_down(n, start=100.0, drift=-0.002, wiggle=0.004):
    return [start * (1 + drift) ** i * (1 + wiggle * math.sin(i * 0.9)) for i in range(n)]


def klines(closes, tf, vols=None, forming=True, now=NOW, wick=0.002):
    """Binance kline rows (lists of strings and ints). One extra, still-forming candle is appended."""
    step = MS[tf]
    n = len(closes)
    last_close = (now // step) * step - 1
    rows = []
    for i, c in enumerate(closes):
        ct = last_close - (n - 1 - i) * step
        o = closes[i - 1] if i else c
        h, l = max(o, c) * (1 + wick), min(o, c) * (1 - wick)
        v = vols[i] if vols else 100.0
        rows.append([ct - step + 1, "%.8f" % o, "%.8f" % h, "%.8f" % l, "%.8f" % c, "%.8f" % v, ct,
                     "%.8f" % (v * c), 100, "0", "0", "0"])
    if forming:
        ot = last_close + 1
        c = closes[-1]
        rows.append([ot, "%.8f" % c, "%.8f" % c, "%.8f" % c, "%.8f" % c, "1.0", ot + step - 1, "%.8f" % c, 1, "0", "0", "0"])
    return rows


def breakout_15m(n=100, start=100.0):
    """Rising 15m series whose last closed candle breaks the previous high on 3x volume."""
    c = closes_up(n, start)
    c[-1] = max(c[:-1]) * 1.02
    vols = [100.0] * n
    vols[-1] = 300.0
    return c, vols


def symbol_info(symbol, base, status="TRADING", tick="0.01000000", step="0.00100000", min_notional="5.00000000"):
    return {"symbol": symbol, "status": status, "baseAsset": base, "quoteAsset": "USDT", "isSpotTradingAllowed": True,
            "permissions": ["SPOT"],
            "filters": [{"filterType": "PRICE_FILTER", "minPrice": tick, "maxPrice": "1000000.00", "tickSize": tick},
                        {"filterType": "LOT_SIZE", "minQty": step, "maxQty": "9000000.00", "stepSize": step},
                        {"filterType": "NOTIONAL", "minNotional": min_notional, "applyMinToMarket": True,
                         "maxNotional": "9000000.00", "applyMaxToMarket": False, "avgPriceMins": 5}]}


def universe(btc_up=True):
    """name -> spec used by fake_api."""
    up15, v15 = breakout_15m()
    return {
        "UPUSDT": {"base": "UP", "c4": closes_up(100), "c1": closes_up(100), "c15": up15, "v15": v15,
                   "qv": 80e6, "bid": "172.40", "ask": "172.45"},
        "DNUSDT": {"base": "DN", "c4": closes_down(100), "c1": closes_down(100), "c15": closes_down(100),
                   "qv": 60e6, "bid": "80.00", "ask": "80.05"},
        "BTCUSDT": {"base": "BTC", "c4": closes_up(100, 60000), "c1": (closes_up(100, 60000) if btc_up else closes_down(100, 60000)),
                    "c15": closes_up(100, 60000), "qv": 900e6, "bid": "70000.00", "ask": "70000.10"},
        "THINUSDT": {"base": "THIN", "c4": closes_up(100), "c1": closes_up(100), "c15": up15, "v15": v15,
                     "qv": 100e3, "bid": "172.40", "ask": "172.45"},
        "USDCUSDT": {"base": "USDC", "c4": closes_up(100, 1.0, 0.0), "c1": closes_up(100, 1.0, 0.0), "c15": closes_up(100, 1.0, 0.0),
                     "qv": 500e6, "bid": "1.0000", "ask": "1.0001"},
        "HALTUSDT": {"base": "HALT", "status": "BREAK", "c4": closes_up(100), "c1": closes_up(100), "c15": up15, "v15": v15,
                     "qv": 70e6, "bid": "172.40", "ask": "172.45"},
    }


def fake_api(specs):
    """A stand-in for fetch_json that answers like Binance's public endpoints."""
    def api(path, params=None, timeout=20):
        params = params or {}
        if path == "/api/v3/exchangeInfo":
            rows = [symbol_info(s, d["base"], d.get("status", "TRADING")) for s, d in specs.items()]
            if "symbols" in params:
                want = json.loads(params["symbols"])
                rows = [r for r in rows if r["symbol"] in want]
            return {"symbols": rows}
        if path == "/api/v3/ticker/24hr":
            rows = []
            for s, d in specs.items():
                last = d["c15"][-1]
                rows.append({"symbol": s, "lastPrice": "%.8f" % last, "quoteVolume": "%.2f" % d["qv"],
                             "priceChangePercent": "2.5", "highPrice": "%.8f" % (last * 1.03), "lowPrice": "%.8f" % (last * 0.95)})
            if "symbol" in params:
                return next(r for r in rows if r["symbol"] == params["symbol"])
            return rows
        if path == "/api/v3/ticker/bookTicker":
            rows = [{"symbol": s, "bidPrice": d["bid"], "askPrice": d["ask"], "bidQty": "1", "askQty": "1"} for s, d in specs.items()]
            if "symbol" in params:
                return next(r for r in rows if r["symbol"] == params["symbol"])
            return rows
        if path == "/api/v3/klines":
            d = specs[params["symbol"]]
            tf = params["interval"]
            key = {"4h": "c4", "1h": "c1", "15m": "c15"}[tf]
            vols = d.get("v15") if tf == "15m" else None
            return klines(d[key], tf, vols)
        if path == "/api/v3/ticker/price":
            if "symbols" in params:
                want = json.loads(params["symbols"])
                return [{"symbol": s, "price": "%.8f" % specs[s]["c15"][-1]} for s in want if s in specs]
            return {"symbol": params["symbol"], "price": "%.8f" % specs[params["symbol"]]["c15"][-1]}
        raise AssertionError("unexpected path " + path)
    return api
