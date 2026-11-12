---
name: binance-analyst
description: >-
  Coin Analyst for Binance Spot. A deep dive on one USDT pair: trend on 4h, 1h
  and 15m (EMA20/EMA50, RSI, ATR), nearby support and resistance from 1h swing
  points, 24h range and volume, spread, and an ATR stop with 2R and 3R targets,
  summed into one verdict (BULLISH to BEARISH). Reads public Binance market data
  only: no API key, no orders. Use when the user says "analyze SOL", "what about
  this coin", "is BTC bullish", "where is support", "where should my stop be",
  "check this pair", or to double-check a /binance-scanner candidate.
argument-hint: "SOL | BTCUSDT"
---

# binance-analyst

The Analyst looks at one pair closely. Use it on a candidate from the Scanner
before sizing, or on any coin the user is curious about.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/analyze.py" SOL
python3 "${CLAUDE_SKILL_DIR}/analyze.py" BTCUSDT --json
```

## What it prints

| part | meaning |
| --- | --- |
| verdict | BULLISH when 4h, 1h and 15m all trend up (close above EMA50 and EMA20 above EMA50); LEANING BULLISH with two up and none down; BEARISH / LEANING BEARISH the mirror; otherwise MIXED |
| timeframes | trend, close, EMA20, EMA50, RSI(14), ATR as percent of price |
| support / resistance | the nearest three 1h swing lows under and swing highs over the price |
| if long | ATR stop (2 x ATR(15m), at least 1%), 2R and 3R targets; a structure stop just under the nearest support when it is within 4% |
| stats | 24h change, 24h volume, distance under the 24h high, spread, recent 1h volume trend |

## How to talk about it

- Lead with the verdict and the one or two facts that drive it (for example
  "4h up, 1h up, 15m mixed, price 3% under resistance at ...").
- Give the stop and targets as levels with the percent distance. A stop under
  structure is usually better than a stop from ATR alone when both are close.
- Mention thin volume, a wide spread or a price right under resistance as reasons
  for caution. A BEARISH or MIXED verdict is an answer: say it, do not soften it.
- If the user wants a trade from it, continue with `/binance-budget`.

## Rules

Not financial advice. Indicators describe the past; they do not predict the next
candle. This skill reads public data only and cannot place an order; the user
says yes at the ticket step and places any order by hand. If Binance cannot be
reached, say so; never fill in prices from memory. Reply in the language the user writes in.
