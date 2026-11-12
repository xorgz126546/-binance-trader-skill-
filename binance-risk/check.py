#!/usr/bin/env python3
"""binance-risk: check every proposed order against your limits. PASS or BLOCK, with the reason.

Reads binance/orders.json, binance/config.json, binance/journal.json (open positions, today's
losses) and binance/portfolio.json (holdings, for rebalance orders). Writes the verdict into
orders.json together with a fingerprint of the exact order. With --live it also reads the current
public price. No API key, no account access, no orders. Standard library only (Python 3.8+).

  python3 check.py                # check and record
  python3 check.py --live         # also compare to the current market price
  python3 check.py --no-write     # look without recording
  python3 check.py --strict       # exit 1 if anything is blocked
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
BASES = [b for b in (os.environ.get("BINANCE_BASE_URL"), "https://data-api.binance.vision",
                     "https://api.binance.com") if b]
UA = "binance-trader-skill/1.0"

DEFAULTS = {
    "budget_usdt": 100.0,
    "max_risk_pct_hard": 2.0,
    "max_position_pct": 25.0,
    "cash_buffer_pct": 10.0,
    "max_open_positions": 3,
    "quote_assets": ["USDT"],
    "blocked": [],
    "min_reward_risk": 1.4,
    "max_order_age_minutes": 30,
    "max_price_drift_pct": 0.5,
    "daily_loss_limit_pct": 3.0,
    "allow_market_orders": False,
}
FORBIDDEN_KEYS = {"leverage", "margin", "isolated", "borrow", "futures", "reduce_only", "is_isolated"}
FINAL_STATUSES = {"placed", "cancelled", "closed"}


def fingerprint(order):
    """A hash of the fields that define the order. Any change to them invalidates a verdict or a yes."""
    keys = ("kind", "symbol", "side", "type", "price", "quantity", "stop_price", "take_profit")
    blob = json.dumps([order.get(k) for k in keys], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
                raise ApiError("Binance rate limit (HTTP %d)" % exc.code)
            if exc.code == 400:
                raise ApiError("Binance rejected the request %s" % path)
            last = "HTTP %d from %s" % (exc.code, base)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = "%s: %s" % (base, exc)
    raise ApiError("Binance public API unreachable (%s)" % last)


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


def now_utc():
    return datetime.now(timezone.utc)


def parse_time(s):
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def base_asset(symbol, quotes):
    for q in quotes:
        if symbol.endswith(q):
            return symbol[:-len(q)]
    return symbol


def realized_today(journal):
    today = now_utc().date().isoformat()
    return sum(float(t.get("pnl", 0)) for t in (journal or {}).get("closed", [])
               if str(t.get("closed_at", "")).startswith(today))


# ------------------------------------------------------------------ the checks

def check_order(o, cfg, ctx, live=None):
    """Return a list of {"name", "ok", "detail"}. ctx is mutated when the order passes."""
    out = []

    def add(name, ok, detail=""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    balance = float(cfg["budget_usdt"])
    kind = o.get("kind", "trade")
    side = str(o.get("side", "")).upper()
    try:
        price, qty = float(o["price"]), float(o["quantity"])
        sym = str(o["symbol"]).upper()
        well_formed = price > 0 and qty > 0 and side in ("BUY", "SELL")
    except (KeyError, TypeError, ValueError):
        add("format", False, "symbol, side, price and quantity are required and must be positive numbers")
        return out
    add("format", well_formed, "" if well_formed else "side must be BUY or SELL, price and quantity above zero")
    if not well_formed:
        return out
    notional = price * qty

    bad = sorted(FORBIDDEN_KEYS & {str(k).lower() for k in o})
    spot = str(o.get("market", "SPOT")).upper() == "SPOT" and not bad
    add("spot-only", spot, "" if spot else "Spot only: no margin, futures or leverage (%s)" % ", ".join(bad or [str(o.get("market"))]))

    quotes = cfg["quote_assets"]
    add("quote", any(sym.endswith(q) for q in quotes), "pair must be quoted in %s" % "/".join(quotes))
    base = base_asset(sym, quotes)
    add("not-blocked", base not in {b.upper() for b in cfg["blocked"]}, "%s is on the blocked list" % base)

    typ = str(o.get("type", "")).upper()
    add("order-type", typ == "LIMIT" or (cfg["allow_market_orders"] and typ == "MARKET"),
        "only LIMIT orders are allowed (set allow_market_orders to change)")

    created = parse_time(o.get("created_at"))
    age = (now_utc() - created).total_seconds() / 60.0 if created else None
    add("fresh", age is not None and age <= float(cfg["max_order_age_minutes"]),
        "proposal is %s old (limit %s min): re-run the scanner and sizing" % (
            "?" if age is None else "%.0f min" % age, cfg["max_order_age_minutes"]))

    if kind == "trade" and side == "BUY":
        stop, tp = float(o.get("stop_price") or 0), float(o.get("take_profit") or 0)
        add("stop", 0 < stop < price, "a BUY needs a stop above zero and under the price")
        add("target", tp > price, "take profit must be above the entry price")
        rr = float(o.get("reward_risk") or 0)
        add("reward-risk", rr >= float(cfg["min_reward_risk"]), "reward/risk %.2f is under %.2f" % (rr, float(cfg["min_reward_risk"])))
        risk = float(o.get("risk_pct") or 0)
        add("risk", 0 < risk <= float(cfg["max_risk_pct_hard"]), "risk %.2f%% is over the %.2f%% limit" % (risk, float(cfg["max_risk_pct_hard"])))
        cap = balance * float(cfg["max_position_pct"]) / 100.0
        add("position-size", notional <= cap * 1.001, "%.2f USDT is over the %.0f%% cap (%.2f USDT)" % (notional, float(cfg["max_position_pct"]), cap))
        add("min-order", notional >= float(o.get("min_notional") or 0), "%.2f USDT is under Binance's minimum order" % notional)
        room = balance * (1 - float(cfg["cash_buffer_pct"]) / 100.0) - ctx["exposure"]
        add("cash-buffer", notional <= room + 0.01, "only %.2f USDT is available above the %.0f%% cash buffer" % (max(room, 0), float(cfg["cash_buffer_pct"])))
        add("not-held", sym not in ctx["symbols"], "%s is already an open position" % sym)
        count = len(ctx["symbols"])
        add("max-positions", sym in ctx["symbols"] or count < int(cfg["max_open_positions"]),
            "%d positions are open or approved (limit %d)" % (count, int(cfg["max_open_positions"])))
        loss = realized_today(ctx["journal"])
        limit = balance * float(cfg["daily_loss_limit_pct"]) / 100.0
        add("daily-loss", loss > -limit, "today's realized loss %.2f USDT hit the daily limit %.2f USDT: no new buys today" % (-loss, limit))
        if live is not None:
            drift = (live - price) / price * 100.0
            add("live-price", live > stop and abs(drift) <= float(cfg["max_price_drift_pct"]),
                "market %.8g is %+.2f%% from the entry (limit %.2f%%, stop %.8g): the setup has moved, re-scan" % (
                    live, drift, float(cfg["max_price_drift_pct"]), stop))
    elif side == "SELL":
        held = ctx["holdings"].get(base, 0.0)
        add("no-short", qty <= held * 1.0000001, "selling %.8g %s but only %.8g held (Spot cannot short)" % (qty, base, held))
    else:  # rebalance BUY
        add("cash", ctx["buy_spent"] + notional <= ctx["buy_budget"] + 0.01,
            "buys need %.2f USDT but only %.2f USDT is available (cash plus the sells in this batch)" % (ctx["buy_spent"] + notional, ctx["buy_budget"]))

    if all(c["ok"] for c in out):
        if kind == "trade" and side == "BUY":
            ctx["exposure"] += notional
            ctx["symbols"].add(sym)
        elif side == "SELL":
            ctx["holdings"][base] = ctx["holdings"].get(base, 0.0) - qty
        else:
            ctx["buy_spent"] += notional
    return out


def make_context(cfg, journal, portfolio, orders):
    open_pos = (journal or {}).get("open", [])
    holdings = {}
    for b in (portfolio or {}).get("balances", []):
        holdings[str(b["asset"]).upper()] = holdings.get(str(b["asset"]).upper(), 0.0) + float(b.get("amount", 0))
    for p in open_pos:
        base = base_asset(p["symbol"], cfg["quote_assets"])
        if base not in holdings:
            holdings[base] = float(p["qty"])
    usdt = holdings.get("USDT", 0.0)
    sells = sum(float(o["price"]) * float(o["quantity"]) for o in orders
                if str(o.get("side", "")).upper() == "SELL" and o.get("kind") == "rebalance" and o.get("status") not in FINAL_STATUSES)
    return {
        "exposure": sum(float(p["qty"]) * float(p["entry"]) for p in open_pos),
        "symbols": {p["symbol"] for p in open_pos},
        "holdings": holdings,
        "journal": journal,
        "buy_spent": 0.0,
        "buy_budget": usdt + sells,
    }


def run(doc, cfg, journal, portfolio, live=False, now_fn=None):
    orders = doc.get("orders", [])
    ctx = make_context(cfg, journal, portfolio, orders)
    verdicts = []
    for o in orders:
        if o.get("status") in FINAL_STATUSES:
            continue
        price_now = None
        if live and o.get("kind", "trade") == "trade":
            try:
                price_now = float(fetch_json("/api/v3/ticker/price", {"symbol": o["symbol"]})["price"])
            except (ApiError, KeyError, ValueError) as exc:
                price_now = None
                o["_live_error"] = str(exc)
        checks = check_order(o, cfg, ctx, price_now)
        if live and o.get("kind", "trade") == "trade" and price_now is None:
            checks.append({"name": "live-price", "ok": False, "detail": "could not read the current price: %s" % o.get("_live_error", "")})
        o.pop("_live_error", None)
        ok = all(c["ok"] for c in checks)
        reasons = [c["detail"] for c in checks if not c["ok"]]
        o["risk"] = {"verdict": "PASS" if ok else "BLOCK", "checked_at": now_utc().isoformat(timespec="seconds"),
                     "fingerprint": fingerprint(o), "reasons": reasons, "live": bool(live)}
        o["status"] = "risk_pass" if ok else "blocked"
        verdicts.append(o)
    return verdicts


def show(verdicts):
    if not verdicts:
        print("no orders to check")
        return
    for o in verdicts:
        r = o["risk"]
        print("%-5s %-4s %-10s %12s x %-12s %s" % (r["verdict"], o.get("side"), o.get("symbol"), o.get("price_str", o.get("price")),
                                                  o.get("quantity_str", o.get("quantity")), o.get("kind", "trade")))
        for reason in r["reasons"]:
            print("      - %s" % reason)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders", default=os.path.join(DATA_DIR, "orders.json"))
    ap.add_argument("--config", default=os.path.join(DATA_DIR, "config.json"))
    ap.add_argument("--live", action="store_true", help="also compare each entry to the current public price")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any order is blocked")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    doc = load_json(args.orders)
    if not doc:
        sys.exit("no %s: run binance-budget with --write first" % args.orders)
    cfg = load_config(args.config)
    journal = load_json(os.path.join(DATA_DIR, "journal.json"), {})
    portfolio = load_json(os.path.join(DATA_DIR, "portfolio.json"), {})
    verdicts = run(doc, cfg, journal, portfolio, args.live)
    if args.json:
        print(json.dumps([{"id": o.get("id"), "symbol": o.get("symbol"), **o["risk"]} for o in verdicts], indent=2))
    else:
        show(verdicts)
    if not args.no_write:
        save_json(args.orders, doc)
    blocked = [o for o in verdicts if o["risk"]["verdict"] == "BLOCK"]
    return 1 if (args.strict and blocked) else 0


if __name__ == "__main__":
    sys.exit(main())
