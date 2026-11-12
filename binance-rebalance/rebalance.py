#!/usr/bin/env python3
"""binance-rebalance: bring your Spot holdings back to your target weights when they drift.

You tell it what you hold (binance/portfolio.json) and what you want (binance/targets.json).
It reads public prices and Binance's order rules and proposes limit orders to binance/orders.json.
No API key, no account access, no orders. Standard library only (Python 3.8+).

  portfolio.json  {"balances": [{"asset": "BTC", "amount": 0.0012}, {"asset": "USDT", "amount": 40}]}
  targets.json    {"BTC": 50, "ETH": 30, "USDT": 20}          (percent, must add up to 100)

  python3 rebalance.py                 # the drift table and the trades that fix it
  python3 rebalance.py --band 3 --write
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
BASES = [b for b in (os.environ.get("BINANCE_BASE_URL"), "https://data-api.binance.vision",
                     "https://api.binance.com") if b]
UA = "binance-trader-skill/1.0"
DEFAULTS = {"rebalance_band_pct": 5.0, "min_trade_usdt": 10.0}


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
                raise ApiError("Binance rejected the request (does every asset have a USDT pair?)")
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


def D(x):
    return Decimal(str(x))


def floor_to(value, step):
    step = D(step)
    return D(value) if step <= 0 else (D(value) / step).to_integral_value(rounding=ROUND_FLOOR) * step


def plain(d):
    return format(d.normalize(), "f")


def parse_filters(s):
    f = {x["filterType"]: x for x in s.get("filters", [])}
    n = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
    return {"tick": f.get("PRICE_FILTER", {}).get("tickSize", "0.00000001"),
            "step": f.get("LOT_SIZE", {}).get("stepSize", "0.00000001"),
            "min_qty": f.get("LOT_SIZE", {}).get("minQty", "0"),
            "min_notional": n.get("minNotional", "5"), "status": s.get("status")}


def compute(portfolio, targets, prices, band, min_trade):
    """Return (rows, breach, unpriced, untargeted). Pure arithmetic; no network."""
    held = {}
    for b in portfolio.get("balances", []):
        held[str(b["asset"]).upper()] = held.get(str(b["asset"]).upper(), 0.0) + float(b.get("amount", 0))
    tg = {k.upper(): float(v) for k, v in targets.items() if not k.startswith("_")}
    if abs(sum(tg.values()) - 100.0) > 0.5:
        raise ValueError("targets add up to %.2f%%, they must add up to 100" % sum(tg.values()))
    unpriced, untargeted, values = [], [], {}
    for asset in sorted(set(held) | set(tg)):
        amt = held.get(asset, 0.0)
        if asset == "USDT":
            values[asset] = amt
        elif asset + "USDT" in prices:
            values[asset] = amt * prices[asset + "USDT"]
        elif asset in tg:
            unpriced.append(asset)
        elif amt > 0:
            untargeted.append(asset)
    for a in list(values):
        if a not in tg:
            untargeted.append(a)
            del values[a]
    total = sum(values.values())
    rows = []
    for asset in sorted(tg):
        if asset in unpriced:
            continue
        v = values.get(asset, 0.0)
        w = v / total * 100.0 if total > 0 else 0.0
        rows.append({"asset": asset, "amount": held.get(asset, 0.0), "value": v, "weight": w, "target": tg[asset],
                     "drift": w - tg[asset], "trade_usdt": tg[asset] / 100.0 * total - v,
                     "price": prices.get(asset + "USDT")})
    breach = any(abs(r["drift"]) > band for r in rows)
    return rows, breach, unpriced, sorted(set(untargeted)), total


def make_orders(rows, filters, min_trade, fee_pct=0.1):
    orders, skipped = [], []
    now = datetime.now(timezone.utc)
    for r in sorted(rows, key=lambda r: r["trade_usdt"]):          # sells first (most negative)
        if r["asset"] == "USDT":
            continue
        if abs(r["trade_usdt"]) < min_trade:
            skipped.append({"asset": r["asset"], "reason": "trade of %.2f USDT is under min_trade_usdt" % abs(r["trade_usdt"])})
            continue
        sym = r["asset"] + "USDT"
        flt = filters.get(sym)
        if not flt or flt["status"] != "TRADING":
            skipped.append({"asset": r["asset"], "reason": "%s is not tradable on Spot right now" % sym})
            continue
        side = "BUY" if r["trade_usdt"] > 0 else "SELL"
        price = floor_to(r["price"], flt["tick"])
        qty = floor_to(abs(r["trade_usdt"]) / r["price"], flt["step"])
        if side == "SELL":
            qty = min(qty, floor_to(r["amount"], flt["step"]))
        if qty < D(flt["min_qty"]) or qty * price < D(flt["min_notional"]):
            skipped.append({"asset": r["asset"], "reason": "below Binance's minimum order (%s USDT)" % flt["min_notional"]})
            continue
        orders.append({
            "id": "%s-RB-%d" % (sym, int(now.timestamp())), "kind": "rebalance", "market": "SPOT", "symbol": sym,
            "side": side, "type": "LIMIT", "time_in_force": "GTC",
            "price": float(price), "quantity": float(qty), "price_str": plain(price), "quantity_str": plain(qty),
            "notional": float(qty * price), "fees_est": float(qty * price * D(fee_pct) / 100),
            "reason": "%s is %+.1f points off its %.0f%% target" % (r["asset"], r["drift"], r["target"]),
            "created_at": now.isoformat(timespec="seconds"), "status": "proposed", "notes": []})
    return orders, skipped


def show(rows, breach, band, unpriced, untargeted, total, orders, skipped):
    print("portfolio value %.2f USDT, band +/-%.1f points\n" % (total, band))
    print("%-7s %12s %12s %8s %8s %8s" % ("asset", "amount", "value", "weight", "target", "drift"))
    for r in rows:
        flag = "  <-- outside the band" if abs(r["drift"]) > band else ""
        print("%-7s %12.6g %12.2f %7.1f%% %7.1f%% %+7.1f%s" % (r["asset"], r["amount"], r["value"], r["weight"], r["target"], r["drift"], flag))
    for a in unpriced:
        print("no USDT price for %s: left out" % a)
    if untargeted:
        print("held but not in targets (left alone): %s" % ", ".join(untargeted))
    print()
    if not breach:
        print("Within the band: nothing to do. Rebalancing inside the band only pays fees.")
        return
    for o in orders:
        print("%-4s %-10s %s @ %s  (~%.2f USDT)  %s" % (o["side"], o["symbol"], o["quantity_str"], o["price_str"], o["notional"], o["reason"]))
    for s in skipped:
        print("skip %-6s %s" % (s["asset"], s["reason"]))
    if not orders:
        print("outside the band, but no trade is large enough to place")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--band", type=float, help="drift in percentage points that triggers a rebalance (default 5)")
    ap.add_argument("--min-trade", type=float, help="smallest trade in USDT (default 10)")
    ap.add_argument("--portfolio", default=os.path.join(DATA_DIR, "portfolio.json"))
    ap.add_argument("--targets", default=os.path.join(DATA_DIR, "targets.json"))
    ap.add_argument("--config", default=os.path.join(DATA_DIR, "config.json"))
    ap.add_argument("--write", action="store_true", help="write the trades to %s" % os.path.join(DATA_DIR, "orders.json"))
    args = ap.parse_args(argv)
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (load_json(args.config, {}) or {}).items() if not k.startswith("_")})
    band = args.band if args.band is not None else float(cfg["rebalance_band_pct"])
    min_trade = args.min_trade if args.min_trade is not None else float(cfg["min_trade_usdt"])
    portfolio, targets = load_json(args.portfolio), load_json(args.targets)
    if not portfolio or not targets:
        sys.exit("need %s and %s (see the format at the top of this script)" % (args.portfolio, args.targets))
    assets = {str(b["asset"]).upper() for b in portfolio.get("balances", [])} | {k.upper() for k in targets if not k.startswith("_")}
    pairs = sorted(a + "USDT" for a in assets if a != "USDT")
    try:
        prices = {r["symbol"]: float(r["price"]) for r in fetch_json("/api/v3/ticker/price", {"symbols": json.dumps(pairs, separators=(",", ":"))})} if pairs else {}
    except ApiError as exc:
        sys.exit("could not read prices: %s" % exc)
    try:
        rows, breach, unpriced, untargeted, total = compute(portfolio, targets, prices, band, min_trade)
    except ValueError as exc:
        sys.exit(str(exc))
    orders, skipped = [], []
    if breach:
        trade_pairs = sorted(r["asset"] + "USDT" for r in rows if r["asset"] != "USDT" and abs(r["trade_usdt"]) >= min_trade)
        try:
            info = fetch_json("/api/v3/exchangeInfo", {"symbols": json.dumps(trade_pairs, separators=(",", ":"))}) if trade_pairs else {"symbols": []}
        except ApiError as exc:
            sys.exit("could not read Binance exchange rules: %s" % exc)
        filters = {s["symbol"]: parse_filters(s) for s in info.get("symbols", [])}
        orders, skipped = make_orders(rows, filters, min_trade, float(cfg.get("fee_pct", 0.1)))
    show(rows, breach, band, unpriced, untargeted, total, orders, skipped)
    if args.write and orders:
        path = os.path.join(DATA_DIR, "orders.json")
        old = (load_json(path, {}) or {}).get("orders", [])
        pending = [o for o in old if o.get("status") not in ("placed", "cancelled", "closed")]
        if pending:
            print("\nnote: replacing %d earlier proposals in %s" % (len(pending), path))
        save_json(path, {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "balance": total,
                         "orders": orders, "skipped": skipped})
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
