#!/usr/bin/env python3
"""binance-budget: size scanner candidates to your balance, your risk per trade and Binance's filters.

Reads binance/candidates.json (from binance-scanner) and binance/config.json. Reads public
exchange rules (tick size, step size, minimum order value). Writes proposals to binance/orders.json.
No API key, no account access, no orders. Standard library only (Python 3.8+).

  python3 size.py                      # print the plan
  python3 size.py --write              # write binance/orders.json
  python3 size.py --balance 100 --top 3
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
BASES = [b for b in (os.environ.get("BINANCE_BASE_URL"), "https://data-api.binance.vision",
                     "https://api.binance.com") if b]
UA = "binance-trader-skill/1.0"

DEFAULTS = {
    "budget_usdt": 100.0,         # the trading capital this pack manages
    "risk_per_trade_pct": 1.0,    # loss if the stop is hit, fees included, as percent of budget
    "max_risk_pct_hard": 2.0,     # never exceed this, even to reach Binance's minimum order
    "max_position_pct": 25.0,     # one coin may not be more than this percent of the budget
    "cash_buffer_pct": 10.0,      # always keep this percent in USDT
    "max_open_positions": 3,
    "fee_pct": 0.1,               # per side. 0.1 standard, 0.075 with BNB fee discount
    "min_notional_bump": True,    # on small accounts, round up to Binance's minimum order value
    "stop_limit_gap_pct": 0.2,    # OCO stop-limit sits this far under the stop trigger
}


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


# ------------------------------------------------------------------ Binance filters

def D(x):
    return Decimal(str(x))


def floor_to(value, step):
    step = D(step)
    if step <= 0:
        return D(value)
    return (D(value) / step).to_integral_value(rounding=ROUND_FLOOR) * step


def ceil_to(value, step):
    step = D(step)
    if step <= 0:
        return D(value)
    return (D(value) / step).to_integral_value(rounding=ROUND_CEILING) * step


def plain(d):
    """Decimal -> string without exponent or trailing zeros."""
    s = format(d.normalize(), "f")
    return s


def parse_filters(sym_info):
    f = {x["filterType"]: x for x in sym_info.get("filters", [])}
    notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
    return {
        "tick": f.get("PRICE_FILTER", {}).get("tickSize", "0.00000001"),
        "step": f.get("LOT_SIZE", {}).get("stepSize", "0.00000001"),
        "min_qty": f.get("LOT_SIZE", {}).get("minQty", "0"),
        "max_qty": f.get("LOT_SIZE", {}).get("maxQty", "9000000000"),
        "min_notional": notional.get("minNotional", "5"),
        "status": sym_info.get("status"),
    }


def get_filters(symbols):
    if not symbols:
        return {}
    info = fetch_json("/api/v3/exchangeInfo", {"symbols": json.dumps(list(symbols), separators=(",", ":"))})
    return {s["symbol"]: parse_filters(s) for s in info.get("symbols", [])}


# ------------------------------------------------------------------ sizing

def plan_order(c, flt, balance, deployable, cfg):
    """Return (order_dict, None) or (None, reason). All money in USDT."""
    if flt is None:
        return None, "no exchange rules found for %s" % c["symbol"]
    if flt.get("status") != "TRADING":
        return None, "%s is not trading (%s)" % (c["symbol"], flt.get("status"))
    entry = floor_to(c["entry"], flt["tick"])
    stop = floor_to(c["stop"], flt["tick"])
    tp = floor_to(c["take_profit"], flt["tick"])
    if entry <= 0 or stop <= 0 or stop >= entry:
        return None, "stop is not below entry"
    fee = D(cfg["fee_pct"]) / 100
    per_unit_loss = (entry - stop) + entry * fee * 2     # price move to the stop plus fees both ways
    risk_target = D(balance) * D(cfg["risk_per_trade_pct"]) / 100
    max_notional = min(D(balance) * D(cfg["max_position_pct"]) / 100, D(deployable))
    if max_notional <= 0:
        return None, "no cash left under the cash buffer"
    qty = risk_target / per_unit_loss
    notes = []
    if qty * entry > max_notional:
        qty = max_notional / entry
        notes.append("capped by position size or cash buffer")
    qty = floor_to(qty, flt["step"])
    min_notional, min_qty = D(flt["min_notional"]), D(flt["min_qty"])
    if qty < min_qty or qty * entry < min_notional:
        if not cfg["min_notional_bump"]:
            return None, "below Binance minimum order (%s USDT)" % plain(min_notional)
        need = ceil_to(max(min_notional * D("1.02") / entry, min_qty), flt["step"])
        if need * entry > max_notional:
            return None, ("Binance minimum order (%s USDT) is above what the position cap (%s%% of the budget) and the cash buffer "
                          "allow (%.2f USDT): on a small budget, raise max_position_pct" % (
                              plain(min_notional), cfg["max_position_pct"], max_notional))
        risk_if_bumped = need * per_unit_loss / D(balance) * 100
        if risk_if_bumped > D(cfg["max_risk_pct_hard"]):
            return None, "stop is too wide for this balance: the minimum order would risk %.2f%% (limit %s%%)" % (
                risk_if_bumped, cfg["max_risk_pct_hard"])
        qty = need
        notes.append("rounded up to Binance's minimum order value")
    if qty > D(flt["max_qty"]):
        return None, "above Binance max quantity"
    notional = qty * entry
    risk = qty * per_unit_loss
    fees = notional * fee * 2
    gain = qty * (tp - entry) - fees
    gap = D(cfg["stop_limit_gap_pct"]) / 100
    stop_limit = floor_to(stop * (1 - gap), flt["tick"])
    order = {
        "id": "%s-%d" % (c["symbol"], int(time.time())),
        "kind": "trade",
        "market": "SPOT",
        "symbol": c["symbol"],
        "side": "BUY",
        "type": "LIMIT",
        "time_in_force": "GTC",
        "price": float(entry),
        "quantity": float(qty),
        "price_str": plain(entry),
        "quantity_str": plain(qty),
        "notional": float(notional),
        "stop_price": float(stop),
        "stop_limit_price": float(stop_limit),
        "take_profit": float(tp),
        "take_profit_2": float(floor_to(c.get("take_profit_2", c["take_profit"]), flt["tick"])),
        "risk_usdt": float(risk),
        "risk_pct": float(risk / D(balance) * 100),
        "reward_risk": float(gain / risk) if risk > 0 else 0.0,
        "fees_est": float(fees),
        "min_notional": float(min_notional),
        "score": c.get("score"),
        "setup": c.get("setup"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "proposed",
        "notes": notes,
    }
    return order, None


def open_exposure(journal):
    pos = (journal or {}).get("open", [])
    return sum(float(p["qty"]) * float(p["entry"]) for p in pos), len(pos), {p["symbol"] for p in pos}


def build(cands, filters, balance, cfg, journal, top=5):
    exposure, n_open, held = open_exposure(journal)
    deployable = balance * (1 - cfg["cash_buffer_pct"] / 100.0) - exposure
    slots = max(cfg["max_open_positions"] - n_open, 0)
    orders, skipped = [], []
    for c in cands[:top]:
        if c["symbol"] in held:
            skipped.append({"symbol": c["symbol"], "reason": "already an open position"})
            continue
        if len(orders) >= slots:
            skipped.append({"symbol": c["symbol"], "reason": "max_open_positions (%d) reached" % cfg["max_open_positions"]})
            continue
        order, why = plan_order(c, filters.get(c["symbol"]), balance, deployable, cfg)
        if order is None:
            skipped.append({"symbol": c["symbol"], "reason": why})
            continue
        deployable -= order["notional"]
        orders.append(order)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "balance": balance,
        "cash_after": balance - exposure - sum(o["notional"] for o in orders),
        "orders": orders,
        "skipped": skipped,
    }


def show(plan):
    print("balance %.2f USDT\n" % plan["balance"])
    if plan["orders"]:
        print("%-10s %-10s %12s %12s %8s %12s %12s %6s %5s" % ("symbol", "side", "limit price", "quantity", "USDT", "stop", "take profit", "risk%", "R:R"))
    for o in plan["orders"]:
        print("%-10s %-10s %12s %12s %8.2f %12s %12s %6.2f %5.2f" % (
            o["symbol"], o["side"], o["price_str"], o["quantity_str"], o["notional"], o["stop_price"],
            o["take_profit"], o["risk_pct"], o["reward_risk"]))
        for n in o["notes"]:
            print("    note: %s" % n)
    for s in plan["skipped"]:
        print("skipped %-10s %s" % (s["symbol"], s["reason"]))
    if not plan["orders"]:
        print("no orders proposed")
    print("\ncash left after these: %.2f USDT" % plan["cash_after"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--balance", type=float, help="override config budget_usdt")
    ap.add_argument("--top", type=int, default=5, help="size at most this many candidates (default 5)")
    ap.add_argument("--symbols", help="only these symbols from the candidates file")
    ap.add_argument("--include-watch", action="store_true", help="also size near misses from the watch list")
    ap.add_argument("--candidates", default=os.path.join(DATA_DIR, "candidates.json"))
    ap.add_argument("--config", default=os.path.join(DATA_DIR, "config.json"))
    ap.add_argument("--write", action="store_true", help="write %s" % os.path.join(DATA_DIR, "orders.json"))
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    balance = args.balance if args.balance is not None else float(cfg["budget_usdt"])
    data = load_json(args.candidates)
    if not data:
        sys.exit("no %s: run binance-scanner with --write first" % args.candidates)
    cands = list(data.get("candidates", []))
    if args.include_watch:
        cands += data.get("watch", [])
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        cands = [c for c in cands if c["symbol"] in want]
    if not cands:
        print("NO TRADE: the candidates file has nothing to size.")
        return 0
    journal = load_json(os.path.join(DATA_DIR, "journal.json"), {})
    try:
        filters = get_filters([c["symbol"] for c in cands[:args.top]])
    except ApiError as exc:
        sys.exit("could not read Binance exchange rules: %s" % exc)
    plan = build(cands, filters, balance, cfg, journal, args.top)
    show(plan)
    if args.write:
        save_json(os.path.join(DATA_DIR, "orders.json"), plan)
        print("wrote %s" % os.path.join(DATA_DIR, "orders.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
