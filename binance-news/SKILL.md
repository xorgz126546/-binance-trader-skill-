---
name: binance-news
description: >-
  News Monitor for Binance Spot. Watches crypto headlines (CoinDesk, Cointelegraph,
  Decrypt) and Binance's own listing and delisting announcements for the coins the
  user holds or watches, tags each one (security incident, delisting, halt or
  maintenance, listing, regulation, upgrade, token unlock) and puts the urgent
  ones first. Headlines and links only; public feeds, no API key, no orders. Use
  when the user says "any news on SOL", "what's happening with my coins", "is
  anything being delisted", "why is it dropping", "check the news", "new listings",
  or on a schedule for the open positions.
argument-hint: "[--coins BTC,ETH,SOL] [--hours 24] [--market]"
---

# binance-news

News explains moves after the fact and warns of some before. Two kinds matter
most for a Spot holder: a **delisting or monitoring-tag notice** (liquidity can
vanish) and a **security incident** (a hack or exploit). Those are flagged `!!`
and shown first.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py" --coins BTC,ETH,SOL --hours 24
python3 "${CLAUDE_SKILL_DIR}/news.py"                      # coins from the open positions in binance/journal.json
python3 "${CLAUDE_SKILL_DIR}/news.py" --market --hours 6   # the whole market, newest first
python3 "${CLAUDE_SKILL_DIR}/news.py" --json
```

## What it does

- Reads three public RSS feeds and two Binance announcement lists (new listings,
  delistings). A headline is kept when it names a coin you asked about, by ticker
  as written in capitals or by its common name (SOL, Solana).
- Drops duplicates and anything older than `--hours`.
- Tags: `security`, `delisting`, `halt` (suspensions, maintenance), `listing`,
  `regulation`, `upgrade`, `unlock`. `security`, `delisting` and `halt` are urgent.
- The tags are keyword matches on the headline. They are a prompt to look, not a
  reading of the story.

## How to talk about it

- Lead with anything urgent on a coin the user holds. Give the headline, the age
  and the link; do not retell an article from a headline you have not opened. If
  the user wants the detail, open the link with a web fetch.
- If the user has an open position and a delisting or security headline names it,
  say plainly that this deserves attention now, and that the decision is theirs.
- No headlines is a fine answer: "nothing in the last 24 hours on BTC, ETH, SOL".
- Binance's announcement endpoint is unofficial and can change. When a source
  cannot be read, the script says which one; tell the user and carry on with the rest.
  Never invent a headline.

## Rules

Not financial advice. Headlines move prices unpredictably and often arrive after
the move. This skill only reads; it cannot place an order, and any trade still needs
the user's yes at the ticket step. Reply in the language the user writes in.
