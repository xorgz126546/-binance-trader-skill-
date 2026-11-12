#!/usr/bin/env python3
"""binance-alerts: price alerts for Binance Spot pairs, with optional Telegram messages.

Reads public prices. Alerts live in binance/alerts.json. Telegram is optional and uses two
environment variables (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID): the token never goes in the chat
or in a file. No Binance API key, no account access, no orders. Standard library only (Python 3.8+).

  python3 alerts.py add SOLUSDT below 144 --kind stop --note "stop on the SOL trade"
  python3 alerts.py from-journal            # a stop and a target alert for every open position
  python3 alerts.py check --telegram
  python3 alerts.py watch --interval 60 --telegram
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

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
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
                raise ApiError("Binance rejected the request (is every symbol a real Spot pair?)")
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


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(symbol):
    s = symbol.upper().strip()
    return s if s.endswith("USDT") else s + "USDT"


# ------------------------------------------------------------------ telegram

def post_form(url, data, timeout=15):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def send_telegram(text):
    """Send one message. Returns (ok, detail). The token is only read from the environment."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False, "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are not set in the environment"
    try:
        post_form("https://api.telegram.org/bot%s/sendMessage" % token, {"chat_id": chat, "text": text})
        return True, "sent"
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc).replace(token, "***")


# ------------------------------------------------------------------ alerts

def path_alerts():
    return os.path.join(DATA_DIR, "alerts.json")


def load_alerts():
    return load_json(path_alerts(), {"alerts": []}) or {"alerts": []}


def next_id(doc):
    nums = [int(a["id"][1:]) for a in doc["alerts"] if str(a.get("id", "")).startswith("A") and a["id"][1:].isdigit()]
    return "A%d" % (max(nums, default=0) + 1)


def add_alert(doc, symbol, direction, level, kind="price", note=""):
    if direction not in ("above", "below"):
        raise ValueError("direction must be above or below")
    a = {"id": next_id(doc), "symbol": norm(symbol), "direction": direction, "level": float(level),
         "kind": kind, "note": note, "created_at": now_iso(), "triggered_at": None, "notified": False}
    doc["alerts"].append(a)
    return a


def hit(alert, price):
    return price >= alert["level"] if alert["direction"] == "above" else price <= alert["level"]


def get_prices(symbols):
    if not symbols:
        return {}
    rows = fetch_json("/api/v3/ticker/price", {"symbols": json.dumps(sorted(symbols), separators=(",", ":"))})
    return {r["symbol"]: float(r["price"]) for r in rows}


def message(a, price):
    verb = "rose to" if a["direction"] == "above" else "fell to"
    label = {"stop": "STOP level", "target": "TARGET level", "entry": "ENTRY level"}.get(a["kind"], "price alert")
    text = "%s %s %g (alert %s %g) - %s" % (a["symbol"], verb, price, a["direction"], a["level"], label)
    return text + (" - " + a["note"] if a.get("note") else "")


def check(doc, telegram=False, prices=None):
    """Evaluate active alerts. Returns the list of alerts triggered in this run."""
    active = [a for a in doc["alerts"] if not a.get("triggered_at")]
    prices = prices if prices is not None else get_prices({a["symbol"] for a in active})
    fired = []
    for a in active:
        p = prices.get(a["symbol"])
        if p is None or not hit(a, p):
            continue
        a["triggered_at"], a["price_at_trigger"] = now_iso(), p
        text = message(a, p)
        if telegram:
            ok, detail = send_telegram(text)
            a["notified"] = ok
            a["notify_detail"] = detail
        fired.append((a, p, text))
    return fired


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("symbol")
    p.add_argument("direction", choices=["above", "below"])
    p.add_argument("level", type=float)
    p.add_argument("--kind", default="price", choices=["price", "stop", "target", "entry"])
    p.add_argument("--note", default="")
    sub.add_parser("list")
    p = sub.add_parser("remove")
    p.add_argument("id")
    p = sub.add_parser("rearm")
    p.add_argument("id")
    sub.add_parser("from-journal")
    sub.add_parser("from-orders")
    for name in ("check", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--telegram", action="store_true", help="also send a Telegram message")
        if name == "watch":
            p.add_argument("--interval", type=int, default=60, help="seconds between checks (default 60, min 15)")
            p.add_argument("--cycles", type=int, default=0, help="stop after N checks (default: until Ctrl-C)")
    p = sub.add_parser("test-telegram")
    args = ap.parse_args(argv)

    doc = load_alerts()
    if args.cmd == "add":
        a = add_alert(doc, args.symbol, args.direction, args.level, args.kind, args.note)
        save_json(path_alerts(), doc)
        print("added %s: %s %s %g" % (a["id"], a["symbol"], a["direction"], a["level"]))
    elif args.cmd == "list":
        if not doc["alerts"]:
            print("no alerts")
        for a in doc["alerts"]:
            state = "fired %s at %g" % (a["triggered_at"], a.get("price_at_trigger", 0)) if a.get("triggered_at") else "active"
            print("%-4s %-10s %-5s %-12g %-7s %s %s" % (a["id"], a["symbol"], a["direction"], a["level"], a["kind"], state, a.get("note", "")))
    elif args.cmd == "remove":
        n = len(doc["alerts"])
        doc["alerts"] = [a for a in doc["alerts"] if a["id"] != args.id]
        save_json(path_alerts(), doc)
        print("removed" if len(doc["alerts"]) < n else "no such alert")
    elif args.cmd == "rearm":
        for a in doc["alerts"]:
            if a["id"] == args.id:
                a["triggered_at"], a["notified"] = None, False
                a.pop("price_at_trigger", None)
        save_json(path_alerts(), doc)
        print("rearmed %s" % args.id)
    elif args.cmd == "from-journal":
        journal = load_json(os.path.join(DATA_DIR, "journal.json"), {}) or {}
        have = {(a["symbol"], a["kind"], a["level"]) for a in doc["alerts"] if not a.get("triggered_at")}
        made = 0
        for pos in journal.get("open", []):
            for kind, direction, key in (("stop", "below", "stop"), ("target", "above", "target")):
                lvl = pos.get(key)
                if lvl and (pos["symbol"], kind, float(lvl)) not in have:
                    add_alert(doc, pos["symbol"], direction, lvl, kind, "open position")
                    made += 1
        save_json(path_alerts(), doc)
        print("created %d alerts from %d open positions" % (made, len(journal.get("open", []))))
    elif args.cmd == "from-orders":
        orders = (load_json(os.path.join(DATA_DIR, "orders.json"), {}) or {}).get("orders", [])
        made = 0
        for o in orders:
            if o.get("status") in ("approved", "placed") and o.get("side") == "BUY" and o.get("kind", "trade") == "trade":
                add_alert(doc, o["symbol"], "below", o["price"], "entry", "limit buy may have filled")
                add_alert(doc, o["symbol"], "below", o["stop_price"], "stop", "stop level")
                made += 2
        save_json(path_alerts(), doc)
        print("created %d alerts" % made)
    elif args.cmd == "test-telegram":
        ok, detail = send_telegram("binance-alerts test message")
        print("telegram: %s (%s)" % ("OK" if ok else "FAILED", detail))
        return 0 if ok else 1
    elif args.cmd in ("check", "watch"):
        loops = 1 if args.cmd == "check" else args.cycles
        interval = 0 if args.cmd == "check" else max(args.interval, 15)
        n = 0
        try:
            while True:
                doc = load_alerts()
                try:
                    fired = check(doc, args.telegram)
                except ApiError as exc:
                    print("check failed: %s" % exc)
                    fired = []
                if fired:
                    save_json(path_alerts(), doc)
                for a, p, text in fired:
                    note = "" if not args.telegram else ("  [telegram: %s]" % a.get("notify_detail"))
                    print("ALERT %s: %s%s" % (a["id"], text, note))
                if not fired and args.cmd == "check":
                    print("no alert triggered (%d active)" % len([a for a in doc["alerts"] if not a.get("triggered_at")]))
                n += 1
                if loops and n >= loops:
                    break
                time.sleep(interval)
        except KeyboardInterrupt:
            print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
