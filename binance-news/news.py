#!/usr/bin/env python3
"""binance-news: headlines for the coins you hold or watch, plus Binance's own listing notices.

Reads public RSS feeds and Binance's public announcement list. Headlines and links only.
No API key, no account access, no orders. Standard library only (Python 3.8+).

  python3 news.py --coins BTC,ETH,SOL --hours 24
  python3 news.py                      # coins from your open positions in binance/journal.json
  python3 news.py --market --hours 6   # everything, not filtered by coin
  python3 news.py --json
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DATA_DIR = os.environ.get("BINANCE_SKILL_DIR", "binance")
UA = "binance-trader-skill/1.0"
MAX_BYTES = 3 * 1024 * 1024

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
]
# Binance's own announcement lists (unofficial endpoint: it can change, and the tool says so if it fails)
ANNOUNCE = [
    ("Binance listing", "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=48"),
    ("Binance delisting", "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=161"),
]
ANNOUNCE_LINK = "https://www.binance.com/en/support/announcement/"

NAMES = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"], "BNB": ["bnb chain", "binance coin"],
    "XRP": ["ripple"], "ADA": ["cardano"], "DOGE": ["dogecoin"], "AVAX": ["avalanche"], "LINK": ["chainlink"],
    "DOT": ["polkadot"], "TRX": ["tron"], "TON": ["toncoin"], "MATIC": ["polygon"], "POL": ["polygon"],
    "LTC": ["litecoin"], "SHIB": ["shiba inu"], "UNI": ["uniswap"], "NEAR": ["near protocol"], "ATOM": ["cosmos"],
    "ARB": ["arbitrum"], "OP": ["optimism"], "SUI": ["sui network"], "APT": ["aptos"], "PEPE": ["pepe coin"],
}
TAGS = [
    ("security", True, r"\b(hack(ed|s)?|exploit(ed|s)?|drain(ed)?|stolen|vulnerabilit\w+|breach|rug ?pull)\b"),
    ("delisting", True, r"\b(delist\w*|removal of|monitoring tag|seed tag)\b"),
    ("halt", True, r"\b(suspend\w*|halt\w*|maintenance|network (upgrade|issue)|withdrawals? paused)\b"),
    ("listing", False, r"\b(will list|new listing|lists?|listing|launchpool|launchpad)\b"),
    ("regulation", False, r"\b(sec|cftc|lawsuit|sues?|ban(s|ned)?|regulat\w+|etf|court|approval|approves?)\b"),
    ("upgrade", False, r"\b(upgrade|hard fork|mainnet|halving|testnet)\b"),
    ("unlock", False, r"\b(token unlock|unlocks?|vesting)\b"),
]


def fetch_bytes(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(MAX_BYTES)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return default


def parse_when(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(raw, source):
    """RSS 2.0 or Atom -> [{title, link, when, source}]. Refuses XML that declares entities."""
    if b"<!entity" in raw.lower():
        raise ValueError("feed declares XML entities: refused")
    root = ET.fromstring(raw)
    out = []
    for node in root.iter():
        if local(node.tag) not in ("item", "entry"):
            continue
        f = {local(c.tag): c for c in node}
        title = (f["title"].text or "").strip() if "title" in f else ""
        link = ""
        if "link" in f:
            link = (f["link"].text or f["link"].get("href") or "").strip()
        stamp = next((f[k] for k in ("pubDate", "published", "updated") if k in f), None)
        when = parse_when(stamp.text if stamp is not None else None)
        if title:
            out.append({"title": title, "link": link, "when": when, "source": source})
    return out


def parse_announcements(raw, source):
    """Binance's announcement JSON -> items. Defensive: finds article lists wherever they sit."""
    data = json.loads(raw.decode("utf-8"))
    found = []

    def walk(x):
        if isinstance(x, dict):
            if "title" in x and ("code" in x or "releaseDate" in x):
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    out = []
    for a in found:
        ms = a.get("releaseDate")
        when = datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc) if ms else None
        out.append({"title": str(a["title"]).strip(), "link": ANNOUNCE_LINK + str(a.get("code", "")), "when": when, "source": source})
    return out


def classify(title):
    tags, high = [], False
    low = title.lower()
    for name, is_high, rx in TAGS:
        if re.search(rx, low):
            tags.append(name)
            high = high or is_high
    return tags, high


def coins_in(title, coins):
    hit = []
    low = title.lower()
    for c in coins:
        c = c.upper()
        if re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(c), title):   # ticker, uppercase as written
            hit.append(c)
        elif any(re.search(r"\b%s\b" % re.escape(n), low) for n in NAMES.get(c, [])):
            hit.append(c)
    return hit


def collect(coins, hours, market=False, now=None, feeds=FEEDS, announce=ANNOUNCE, fetch=None):
    fetch = fetch or fetch_bytes
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    items, problems = [], []
    for source, url in feeds:
        try:
            items += parse_feed(fetch(url), source)
        except (urllib.error.URLError, OSError, ET.ParseError, ValueError) as exc:
            problems.append("%s: %s" % (source, exc))
    for source, url in announce:
        try:
            items += parse_announcements(fetch(url), source)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            problems.append("%s: %s (unofficial endpoint)" % (source, exc))
    out, seen = [], set()
    for it in items:
        if it["when"] is None or it["when"] < since:
            continue
        key = re.sub(r"\W+", " ", it["title"].lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        tags, high = classify(it["title"])
        matched = coins_in(it["title"], coins) if coins else []
        if not market and coins and not matched:
            continue
        it.update({"tags": tags, "high": high, "coins": matched,
                   "age_hours": (now - it["when"]).total_seconds() / 3600.0})
        out.append(it)
    out.sort(key=lambda i: (not i["high"], i["age_hours"]))
    return out, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coins", help="comma separated base assets, e.g. BTC,ETH,SOL")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--market", action="store_true", help="show everything in the window, not only your coins")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.coins:
        coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    else:
        journal = load_json(os.path.join(DATA_DIR, "journal.json"), {}) or {}
        coins = sorted({p["symbol"][:-4] for p in journal.get("open", []) if p["symbol"].endswith("USDT")})
    if not coins and not args.market:
        sys.exit("no coins: pass --coins BTC,ETH or --market, or open a position in the journal first")
    items, problems = collect(coins, args.hours, args.market)
    if args.json:
        print(json.dumps({"coins": coins, "items": [dict(i, when=i["when"].isoformat()) for i in items], "problems": problems}, indent=2))
        return 0
    print("%s, last %g hours: %d headlines" % (", ".join(coins) or "whole market", args.hours, len(items)))
    for i in items[:40]:
        flag = "!!" if i["high"] else "  "
        print("%s %4.1fh  %-17s %-9s %s" % (flag, i["age_hours"], i["source"], ",".join(i["coins"]) or "-", i["title"]))
        print("            %s%s" % (i["link"], ("   [" + ",".join(i["tags"]) + "]") if i["tags"] else ""))
    if not items:
        if len(problems) >= len(FEEDS) + len(ANNOUNCE):
            print("NO SOURCE COULD BE READ: this is not 'no news', it is 'no connection'.")
        else:
            print("nothing found for these coins in this window%s." % (" (some sources failed, see below)" if problems else ""))
    for p in problems:
        print("could not read %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
