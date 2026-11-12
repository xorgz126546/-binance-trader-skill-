---
name: binance-scanner
description: >-
  Opportunity Scanner for Binance Spot. Ranks USDT pairs on a 4h trend, 1h
  confirmation and 15m entry (breakout or EMA retest), scores each 0-100, applies
  a BTC market filter, a spread filter and a volatility filter, and proposes an
  entry, a stop and targets from ATR. Reads public Binance market data only: no
  API key, no account, no orders. Use when the user says "scan Binance", "find me
  a trade", "any setups", "what's moving", "opportunities", "scan the market", or
  as the first step before /binance-budget.
argument-hint: "[--symbols BTCUSDT,SOLUSDT] [--min-score 70]"
---

# binance-scanner

The Scanner finds candidates. It never trades and never touches an account.
Everything it prints is a proposal for the next skills to size and check.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py"                        # top 40 USDT pairs by 24h volume
python3 "${CLAUDE_SKILL_DIR}/scan.py" --write                # also writes binance/candidates.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" --symbols BTCUSDT,SOLUSDT --min-score 60
python3 "${CLAUDE_SKILL_DIR}/scan.py" --ignore-btc           # skip the BTC market filter
```

## What it does

1. Takes Spot pairs quoted in USDT with status TRADING. Stablecoins, fiat bases
   `blocked` coins and Binance's tokenized US stocks/ETFs (names like GOOGLB, QQQB; set `exclude_bstocks` to false to scan them) are left out, as are stablecoins such as USDC, FDUSD and RLUSD. Pairs under `min_quote_volume` (24h) are left out.
2. Reads 4h, 1h and 15m candles. The candle still forming is dropped, so every
   number comes from closed candles.
3. Scores each pair out of 100:

| part | points | what earns it |
| --- | --- | --- |
| trend (4h) | 25 | close above EMA50 and EMA20 above EMA50 (12 for close above EMA50 only) |
| confirm (1h) | 20 | close above EMA20 above EMA50 (12, or 6 above EMA50), RSI 50-70 (+8) |
| entry (15m) | 25 | close breaks the previous 20-candle high (25), holds an EMA20 retest (18), or sits within 0.3% below the high (8) |
| volume (15m) | 15 | last candle volume vs the previous 20: 2x (15), 1.5x (10), 1x (5) |
| heat (15m) | 15 | RSI at most 70 (15), at most 78 (7) |

4. Gates that disqualify no matter the score: 4h close under EMA50, spread wider
   than `max_spread_pct`, 15m ATR outside `min_atr_pct`..`max_atr_pct`, 24h volume
   too low, BTC under its 1h EMA50 (`btc_filter`).
5. For a pair that qualifies it proposes: entry = last close, stop = entry minus
   2 x ATR(15m) (never closer than `min_stop_pct`), targets at 2R and 3R.

## Reading the output

- `candidates` are the pairs that passed. Hand them to `/binance-budget`.
- When nothing passes, say **NO TRADE** plainly. Then show the watch list: each
  near miss with its score parts (`T C E V H`) and the reason it failed. Do not
  lower `--min-score` just to produce a trade; if the user wants to see more,
  offer `--min-score 60` as a look, and say that a lower score is a weaker setup.
- Say when the BTC filter is what blocked everything.

## Files

| file | what |
| --- | --- |
| `binance/config.json` | optional limits: `min_score`, `min_quote_volume`, `max_spread_pct`, `stop_atr_mult`, `blocked`, ... (`binance-budget/config.example.json` lists all) |
| `binance/candidates.json` | written by `--write`, read by `/binance-budget` |

## Rules

Not financial advice. The score is a transparent heuristic, not a forecast, and
it has not been backtested. Crypto moves fast and can lose money quickly. This
skill cannot place an order; the user must say yes at the ticket step, and
places the order by hand in the Binance app. Reply in the language the user writes in.

If Binance cannot be reached, say so and stop. Do not invent prices or fill in
candles from memory.
