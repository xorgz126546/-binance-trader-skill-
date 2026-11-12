#!/usr/bin/env python3
"""binance-journal: the trade ledger. Open positions, closed trades, fees, profit and loss, stats.

Everything is typed in by you (or by Claude after you tell it your fills): no API key, no account
access, no network. binance-budget and binance-risk read this file for open positions and for
today's losses. Standard library only (Python 3.8+).

  python3 journal.py open SOLUSDT --price 148.2 --qty 0.19 --stop 144 --target 156.6
  python3 journal.py close SOLUSDT --price 155.9
  python3 journal.py list
  python3 journal.py stats
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
DEFAULT_FEE_PCT = 0.1


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


def jpath():
    return os.path.join(DATA_DIR, "journal.json")


def load_journal():
    j = load_json(jpath(), {}) or {}
    j.setdefault("open", [])
    j.setdefault("closed", [])
    return j


def fee_pct():
    cfg = load_json(os.path.join(DATA_DIR, "config.json"), {}) or {}
    return float(cfg.get("fee_pct", DEFAULT_FEE_PCT))


# ------------------------------------------------------------------ actions

def open_position(j, symbol, price, qty, stop=None, target=None, fee=None):
    sym = norm(symbol)
    if any(p["symbol"] == sym for p in j["open"]):
        raise ValueError("%s is already open: close it first, or journal an add as a new average by closing and reopening" % sym)
    if price <= 0 or qty <= 0:
        raise ValueError("price and quantity must be above zero")
    if stop is not None and stop >= price:
        raise ValueError("the stop must be under the entry price for a long")
    fee_usdt = fee if fee is not None else price * qty * fee_pct() / 100.0
    pos = {"symbol": sym, "entry": price, "qty": qty, "stop": stop, "target": target,
           "fee_in": fee_usdt, "opened_at": now_iso()}
    j["open"].append(pos)
    return pos


def close_position(j, symbol, price, qty=None, fee=None):
    sym = norm(symbol)
    pos = next((p for p in j["open"] if p["symbol"] == sym), None)
    if pos is None:
        raise ValueError("%s is not an open position" % sym)
    q = float(qty) if qty is not None else float(pos["qty"])
    if q <= 0 or q > float(pos["qty"]) * 1.0000001:
        raise ValueError("quantity must be between 0 and %s" % pos["qty"])
    frac = q / float(pos["qty"])
    fee_in = float(pos["fee_in"]) * frac
    fee_out = fee if fee is not None else price * q * fee_pct() / 100.0
    gross = (price - float(pos["entry"])) * q
    pnl = gross - fee_in - fee_out
    cost = float(pos["entry"]) * q
    risk = None
    if pos.get("stop"):
        risk = (float(pos["entry"]) - float(pos["stop"])) * q + fee_in * 2
    trade = {"symbol": sym, "entry": pos["entry"], "exit": price, "qty": q, "gross": gross,
             "fees": fee_in + fee_out, "pnl": pnl, "pnl_pct": pnl / cost * 100.0 if cost else 0.0,
             "r_multiple": (pnl / risk) if risk else None,
             "opened_at": pos["opened_at"], "closed_at": now_iso()}
    j["closed"].append(trade)
    if frac >= 0.9999999:
        j["open"].remove(pos)
    else:
        pos["qty"] = float(pos["qty"]) - q
        pos["fee_in"] = float(pos["fee_in"]) - fee_in
    return trade


def stats(closed):
    n = len(closed)
    if n == 0:
        return {"trades": 0}
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    equity = peak = dd = 0.0
    for t in sorted(closed, key=lambda x: x["closed_at"]):
        equity += t["pnl"]
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    rs = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]
    return {
        "trades": n,
        "win_rate_pct": len(wins) / n * 100.0,
        "total_pnl": sum(t["pnl"] for t in closed),
        "total_fees": sum(t["fees"] for t in closed),
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": sum(t["pnl"] for t in closed) / n,
        "avg_r": sum(rs) / len(rs) if rs else None,
        "max_drawdown": dd,
    }


def show_stats(s):
    if not s["trades"]:
        print("no closed trades yet")
        return
    pf = "n/a (no losses)" if s["profit_factor"] is None else "%.2f" % s["profit_factor"]
    print("trades %d   win rate %.0f%%   total P&L %+.2f USDT   fees %.2f USDT" % (s["trades"], s["win_rate_pct"], s["total_pnl"], s["total_fees"]))
    print("avg win %+.2f   avg loss %+.2f   profit factor %s   expectancy %+.2f USDT/trade" % (s["avg_win"], s["avg_loss"], pf, s["expectancy"]))
    print("avg R %s   max drawdown %.2f USDT" % ("%.2f" % s["avg_r"] if s["avg_r"] is not None else "n/a", s["max_drawdown"]))
    if s["trades"] < 30:
        print("only %d trades: too few to say whether the method works." % s["trades"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open")
    p.add_argument("symbol")
    p.add_argument("--price", type=float, required=True, help="real average fill price")
    p.add_argument("--qty", type=float, required=True, help="real filled amount of the coin")
    p.add_argument("--stop", type=float)
    p.add_argument("--target", type=float)
    p.add_argument("--fee", type=float, help="real entry fee in USDT (default: fee_pct from the config)")
    p = sub.add_parser("close")
    p.add_argument("symbol")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--qty", type=float, help="partial close (default: all)")
    p.add_argument("--fee", type=float, help="real exit fee in USDT")
    sub.add_parser("list")
    sub.add_parser("stats")
    p = sub.add_parser("today")
    args = ap.parse_args(argv)

    j = load_journal()
    try:
        if args.cmd == "open":
            pos = open_position(j, args.symbol, args.price, args.qty, args.stop, args.target, args.fee)
            save_json(jpath(), j)
            print("opened %s: %g @ %g (%.2f USDT)" % (pos["symbol"], pos["qty"], pos["entry"], pos["qty"] * pos["entry"]))
        elif args.cmd == "close":
            t = close_position(j, args.symbol, args.price, args.qty, args.fee)
            save_json(jpath(), j)
            print("closed %s: %+.2f USDT (%+.2f%%)%s" % (t["symbol"], t["pnl"], t["pnl_pct"],
                                                      "   %.2fR" % t["r_multiple"] if t["r_multiple"] is not None else ""))
        elif args.cmd == "list":
            print("open positions:")
            for p in j["open"] or []:
                print("  %-10s %g @ %g  stop %s  target %s" % (p["symbol"], p["qty"], p["entry"], p.get("stop"), p.get("target")))
            if not j["open"]:
                print("  none")
            print("last closed trades:")
            for t in j["closed"][-10:]:
                print("  %-10s %+8.2f USDT  %+6.2f%%  %s" % (t["symbol"], t["pnl"], t["pnl_pct"], t["closed_at"][:16]))
            if not j["closed"]:
                print("  none")
        elif args.cmd == "stats":
            show_stats(stats(j["closed"]))
        elif args.cmd == "today":
            today = now_iso()[:10]
            day = [t for t in j["closed"] if t["closed_at"].startswith(today)]
            print("today (UTC): %d closed trades, P&L %+.2f USDT" % (len(day), sum(t["pnl"] for t in day)))
    except ValueError as exc:
        sys.exit("journal: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
