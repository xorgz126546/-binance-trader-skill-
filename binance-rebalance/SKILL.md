---
name: binance-rebalance
description: >-
  Rebalancer for Binance Spot. Brings a holdings mix back to the user's target
  weights (for example 50% BTC, 30% ETH, 20% USDT) when any coin drifts past a
  band. Reads the holdings the user types in and public prices, prints the drift
  table, and proposes limit orders rounded to Binance's tick size, step size and
  minimum order value, sells first. Does nothing inside the band, because
  rebalancing there only pays fees. Proposals go through /binance-risk and
  /binance-ticket; nothing is placed. Use when the user says "rebalance", "my
  portfolio drifted", "back to 50/30/20", "am I balanced", "portfolio weights".
argument-hint: "[--band 5] [--min-trade 10]"
---

# binance-rebalance

For long-term holdings, not for the short trades of `/binance-scanner`. No API
key: the user gives Claude their balances (from the Binance app: Wallet, Spot)
and the targets, and Claude puts them in two small files.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```
binance/portfolio.json   {"balances": [{"asset": "BTC", "amount": 0.0012}, {"asset": "ETH", "amount": 0.05}, {"asset": "USDT", "amount": 40}]}
binance/targets.json     {"BTC": 50, "ETH": 30, "USDT": 20}
```

```bash
python3 "${CLAUDE_SKILL_DIR}/rebalance.py"                 # drift table + proposed trades
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --band 3 --min-trade 15
python3 "${CLAUDE_SKILL_DIR}/rebalance.py" --write         # writes binance/orders.json
```

## How it decides

- Each asset is priced against USDT. The weight is its value over the total.
  Targets must add up to 100.
- If no asset is more than `rebalance_band_pct` (default 5 points) off its
  target, it says so and stops. Say that to the user as good news.
- If one is, every asset is moved to its target. Trades under `min_trade_usdt`
  (default 10) are skipped, and so is anything under Binance's minimum order.
  Sells come first so the USDT is there for the buys.
- Coins held but missing from the targets are left alone and listed. USDT is the
  cash residual; it is never traded.
- Orders are LIMIT at the last price rounded to the tick. The user may adjust the
  price against the order book when they place it.

## Next

`--write` replaces `binance/orders.json` (the script warns if that drops earlier
proposals). Then `/binance-risk`, then `/binance-ticket`: rebalance sells are
checked against the holdings so nothing is sold that is not held, and buys
against the USDT available plus the sells in the same batch.

## Rules

Not financial advice. Rebalancing sells winners and buys laggards on a schedule;
it can cost more in fees than it earns, and it triggers taxes in many countries.
The user decides; nothing is placed until they say yes at the ticket step, and
they place the orders by hand in the Binance app. Reply in the language the user
writes in.
