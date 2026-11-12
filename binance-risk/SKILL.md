---
name: binance-risk
description: >-
  Risk Manager for Binance Spot. The gate between a proposal and the ticket:
  checks every proposed order against the user's limits and prints PASS or BLOCK
  with the reason. Blocks margin, futures and leverage, pairs outside the allowed
  quote asset, blocked coins, market orders, missing stops, weak reward-to-risk,
  too much risk, oversize positions, a broken cash buffer, too many open
  positions, a hit daily loss limit, selling more than is held, stale proposals,
  and (with --live) a price that has moved away from the entry. Use when the user
  says "check my orders", "is this safe", "risk check", "does this break my
  rules", "can I place this", or before /binance-ticket, always.
argument-hint: "[--live]"
---

# binance-risk

The Risk Manager is the gate. Every order written by `/binance-budget` or
`/binance-rebalance` passes through it. A BLOCK is final for that order: do not
offer it to the user for a yes, and do not render a ticket for it. Fix the cause
(re-scan, re-size) or drop it.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"             # checks binance/orders.json and records the verdicts
python3 "${CLAUDE_SKILL_DIR}/check.py" --live      # also compares each entry to the current public price
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write  # look without recording
```

Run it with `--live` right before a ticket: a proposal that was fine ten minutes
ago may not be now.

## What it reads and writes

| file | what |
| --- | --- |
| `binance/orders.json` | the proposals; the verdict and a fingerprint of each exact order are written back into it |
| `binance/config.json` | the limits (`binance-budget/config.example.json` lists them all) |
| `binance/journal.json` | open positions (cash buffer, position count) and today's realized losses |
| `binance/portfolio.json` | holdings, used for sells and rebalance orders |

## The checks

Orders are checked in file order, as if each order that passed had filled, so a
batch that is fine order by order but breaks a rule together is caught.

| check | blocks when |
| --- | --- |
| format | symbol, side, price or quantity missing, zero or negative |
| spot-only | the order carries margin, futures, borrow or leverage fields |
| quote / not-blocked | the pair is not quoted in `quote_assets`, or the coin is on `blocked` |
| order-type | not a LIMIT order (unless `allow_market_orders`) |
| fresh | the proposal is older than `max_order_age_minutes` |
| stop / target | a BUY with no stop under the price, or a target under the entry |
| reward-risk | reward/risk after fees is under `min_reward_risk` |
| risk | the loss at the stop is over `max_risk_pct_hard` of the budget |
| position-size / min-order | over `max_position_pct`, or under Binance's minimum order |
| cash-buffer | the buy would dip into the cash buffer |
| not-held / max-positions | the coin is already open, or the position limit is reached |
| daily-loss | today's realized losses reached `daily_loss_limit_pct`: no new buys today |
| no-short | a sell of more than is held (Spot cannot short) |
| live-price | with `--live`: the market moved more than `max_price_drift_pct` from the entry, or is already under the stop |

## How to talk about it

Show PASS and BLOCK per order with the reasons in plain words. A BLOCK is not an
error to work around: if the user wants to override a limit, they change
`binance/config.json` themselves, and you say that is their call. Never edit the
limits or the verdicts to get an order through.

## Rules

Not financial advice. PASS means the order fits the user's own rules, not that it
will make money. Nothing here places an order; the user says yes at the ticket
step and places the order by hand in the Binance app. Reply in the language the
user writes in.
