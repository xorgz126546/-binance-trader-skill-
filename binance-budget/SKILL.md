---
name: binance-budget
description: >-
  Budget Manager for Binance Spot. Turns scanner candidates into sized limit-order
  proposals: risk a fixed percent of the budget per trade (fees included), cap any
  one coin, keep a USDT cash buffer, respect the open-position limit, and round
  price and quantity to Binance's tick size, step size and minimum order value so
  the order is accepted. Works for small accounts. Writes proposals only; it
  places nothing. Use when the user says "how much should I buy", "size this
  trade", "position size", "my budget is 50 USDT", "risk 1%", "turn these into
  orders", or after /binance-scanner.
argument-hint: "[--balance 100] [--top 3]"
---

# binance-budget

The Budget Manager answers one question: how much of this coin, at what price,
so that if the stop is hit the loss is the amount the user chose. It writes
**proposals**. A proposal is not an order until it has passed `/binance-risk`
and the user has said yes at `/binance-ticket`.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                    # print the plan
python3 "${CLAUDE_SKILL_DIR}/size.py" --write            # writes binance/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --balance 50 --top 3
```

## Setup

Copy `config.example.json` to `binance/config.json` in the project and set
`budget_usdt` (the money this pack manages, not necessarily the whole account).
If there is no config the defaults apply: 100 USDT, 1% risk per trade, 25% cap
per coin, 10% cash buffer, 3 open positions, 0.1% fee per side.

## The sizing rule

```
loss per unit = (entry - stop) + entry x fee x 2
quantity      = (budget x risk%) / loss per unit
```

then capped by `max_position_pct` of the budget and by the cash above the
buffer, then rounded **down** to Binance's step size. Entry, stop and target are
rounded down to the tick size. The proposal is a **LIMIT BUY** (GTC); market
orders are not proposed.

Small accounts: if the result is under Binance's minimum order value (often 5
USDT) and `min_notional_bump` is on, the quantity is raised to the minimum, but
only if the real risk stays under `max_risk_pct_hard` (default 2%). Otherwise the
coin is skipped with the reason ("stop is too wide for this balance"). Say that
reason to the user; it is information, not a failure.

## What it writes

`binance/orders.json`: for each proposal the symbol, limit price, quantity, USDT
amount, stop trigger, OCO stop-limit price, take profit, risk in USDT and percent,
reward-to-risk after fees, and the minimum order. Also `skipped` (with reasons)
and the cash left. It reads `binance/journal.json` so positions already open count
against the position limit and the cash buffer.

## Next

Always `/binance-risk` next. Never go straight to the ticket.

## Rules

Not financial advice. Sizing limits a loss only if the stop works: on Spot a stop
can fill worse than its price in a fast market or a gap. The user decides; nothing
is placed until they say yes at the ticket step, and they place the order by hand
in the Binance app. Reply in the language the user writes in.
