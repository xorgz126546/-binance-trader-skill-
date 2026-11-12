#!/usr/bin/env python3
"""binance-ticket: the last step before YOU place an order by hand in the Binance app.

It shows each order that passed binance-risk, records your yes for that exact order, and then
prints the step-by-step ticket (limit price, amount, and the OCO sell to place after the fill).
It never connects to your account and never sends an order: there is no API key in this pack.
Standard library only (Python 3.8+).

  python3 ticket.py show
  python3 ticket.py approve SOLUSDT --confirm "yes SOLUSDT"
  python3 ticket.py render SOLUSDT
  python3 ticket.py placed SOLUSDT --price 148.2 --qty 0.19     # after you placed it by hand
  python3 ticket.py cancel SOLUSDT
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
DEFAULTS = {"max_order_age_minutes": 30, "approval_ttl_minutes": 15}
FINAL_STATUSES = {"placed", "cancelled", "closed"}


def fingerprint(order):
    """Same hash binance-risk records. Any change to the order invalidates a verdict or a yes."""
    keys = ("kind", "symbol", "side", "type", "price", "quantity", "stop_price", "take_profit")
    blob = json.dumps([order.get(k) for k in keys], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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


def minutes_since(s):
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now_utc() - dt).total_seconds() / 60.0


def readiness(o, cfg):
    """('READY' | 'NOT READY', [reasons])."""
    if o.get("status") in FINAL_STATUSES:
        return "DONE", ["already %s" % o["status"]]
    why = []
    r = o.get("risk")
    fp = fingerprint(o)
    if not r:
        why.append("not risk-checked yet: run binance-risk")
    elif r.get("verdict") != "PASS":
        why.append("BLOCKED by binance-risk: " + "; ".join(r.get("reasons", [])))
    elif r.get("fingerprint") != fp:
        why.append("the order changed after the risk check: run binance-risk again")
    else:
        age = minutes_since(r.get("checked_at"))
        if age is None or age > float(cfg["max_order_age_minutes"]):
            why.append("the risk check is %s old: run binance-risk --live again" % ("?" if age is None else "%.0f min" % age))
    ap = o.get("approval")
    if not ap:
        why.append("no yes recorded for this order")
    elif ap.get("fingerprint") != fp:
        why.append("the order changed after your yes: it needs a new yes")
    else:
        age = minutes_since(ap.get("at"))
        if age is None or age > float(cfg["approval_ttl_minutes"]):
            why.append("your yes is %s old (valid %s min): confirm again" % ("?" if age is None else "%.0f min" % age, cfg["approval_ttl_minutes"]))
    return ("READY" if not why else "NOT READY"), why


def find(doc, key):
    key = key.upper()
    hits = [o for o in doc.get("orders", []) if o.get("id", "").upper() == key or o.get("symbol", "").upper() == key]
    live = [o for o in hits if o.get("status") not in FINAL_STATUSES]
    pool = live or hits
    if not pool:
        sys.exit("no order matches %r in the orders file" % key)
    if len(pool) > 1:
        sys.exit("%r matches %d orders; use the id: %s" % (key, len(pool), ", ".join(o["id"] for o in pool)))
    return pool[0]


def fmt(o, key):
    return o.get(key + "_str") or str(o.get(key))


def render(o):
    sym = o["symbol"]
    base = sym[:-4] if sym.endswith("USDT") else sym
    side = o["side"].upper()
    price, qty = fmt(o, "price"), fmt(o, "quantity")
    total = float(o["price"]) * float(o["quantity"])
    lines = ["%s %s on Binance Spot (limit order). Place it by hand in the Binance app:" % (side, sym), ""]
    lines += ["1. Open Spot, pair %s/USDT, choose Limit, tab %s." % (base, "Buy" if side == "BUY" else "Sell"),
              "2. Price: %s    Amount: %s %s    (total about %.2f USDT)" % (price, qty, base, total),
              "3. Confirm the order. It rests on the book until it fills (GTC). If it has not filled when the",
              "   setup no longer looks right, cancel it yourself."]
    if side == "BUY" and o.get("kind", "trade") == "trade":
        stop, stop_limit, tp = fmt(o, "stop_price"), o.get("stop_limit_price"), fmt(o, "take_profit")
        lines += ["4. Once it has FILLED (not before), protect it: in the order type menu pick OCO and Sell:",
                  "     Price (take profit, limit): %s" % tp,
                  "     Stop (trigger):             %s" % stop,
                  "     Limit (after the trigger):  %s" % stop_limit,
                  "     Amount: %s %s" % (qty, base),
                  "   If the trigger fires on a fast move the fill can be worse than the stop price.",
                  "5. Tell me the real fill price and amount so I can log it in the journal.", "",
                  "If stopped out, the planned loss is about %.2f USDT (%.2f%% of the budget), fees included." % (
                      float(o.get("risk_usdt", 0)), float(o.get("risk_pct", 0)))]
        if o.get("take_profit_2"):
            lines.append("Optional second target (3R): %s. Moving the stop up as price rises is your call." % o["take_profit_2"])
    else:
        lines.append("4. Tell me when it has filled, with the real price and amount.")
    return "\n".join(lines)


def cmd_show(doc, cfg, as_json):
    rows = []
    for o in doc.get("orders", []):
        state, why = readiness(o, cfg)
        rows.append({"id": o.get("id"), "symbol": o.get("symbol"), "side": o.get("side"), "state": state, "reasons": why})
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("no orders in the orders file")
    for o, r in zip(doc.get("orders", []), rows):
        print("%-9s %-4s %-10s %12s x %-12s" % (r["state"], r["side"], r["symbol"], fmt(o, "price"), fmt(o, "quantity")))
        for w in r["reasons"]:
            print("            - %s" % w)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders", default=os.path.join(DATA_DIR, "orders.json"))
    ap.add_argument("--config", default=os.path.join(DATA_DIR, "config.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve")
    p.add_argument("order")
    p.add_argument("--confirm", required=True, help='the user\'s own words, exactly: "yes SOLUSDT"')
    p = sub.add_parser("render")
    p.add_argument("order")
    p = sub.add_parser("cancel")
    p.add_argument("order")
    p = sub.add_parser("placed")
    p.add_argument("order")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--qty", type=float, required=True)
    args = ap.parse_args(argv)

    doc = load_json(args.orders)
    if not doc:
        sys.exit("no %s: run binance-budget with --write first" % args.orders)
    cfg = load_config(args.config)

    if args.cmd == "show":
        cmd_show(doc, cfg, args.json)
        return 0
    o = find(doc, args.order)
    if args.cmd == "approve":
        r = o.get("risk")
        if not r or r.get("verdict") != "PASS" or r.get("fingerprint") != fingerprint(o):
            sys.exit("cannot record a yes: this order has no current PASS from binance-risk")
        want = re.compile(r"^\s*yes\s+(%s|%s)\s*$" % (re.escape(o["symbol"]), re.escape(o["id"])), re.I)
        if not want.match(args.confirm):
            sys.exit('the confirmation must be the user typing "yes %s" for this exact order' % o["symbol"])
        o["approval"] = {"at": now_utc().isoformat(timespec="seconds"), "fingerprint": fingerprint(o), "confirm": args.confirm.strip()}
        o["status"] = "approved"
        save_json(args.orders, doc)
        print("yes recorded for %s (valid %s min). Now: render %s" % (o["symbol"], cfg["approval_ttl_minutes"], o["symbol"]))
    elif args.cmd == "render":
        state, why = readiness(o, cfg)
        if state != "READY":
            print("NOT READY: %s" % o["symbol"])
            for w in why:
                print("  - %s" % w)
            return 1
        print(render(o))
    elif args.cmd == "cancel":
        o["status"] = "cancelled"
        save_json(args.orders, doc)
        print("cancelled %s" % o["symbol"])
    elif args.cmd == "placed":
        o["status"] = "placed"
        o["placed"] = {"at": now_utc().isoformat(timespec="seconds"), "price": args.price, "qty": args.qty}
        save_json(args.orders, doc)
        if o["side"].upper() == "BUY" and o.get("kind", "trade") == "trade":
            print("logged as placed. Once it fills, record it:\n  python3 binance-journal/journal.py open %s --price %s --qty %s --stop %s --target %s" % (
                o["symbol"], args.price, args.qty, o.get("stop_price"), o.get("take_profit")))
        else:
            print("logged as placed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
