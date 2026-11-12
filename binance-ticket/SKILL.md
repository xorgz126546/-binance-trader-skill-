---
name: binance-ticket
description: >-
  Order Ticket for Binance Spot. Shows only the orders that passed /binance-risk,
  asks the user for an explicit yes for that exact order, records the yes, and
  prints a step-by-step ticket for placing it BY HAND in the Binance app: limit
  price, amount, and the OCO stop and take-profit to place after the fill. It never
  connects to the user's account and never sends an order; there is no API key in
  this pack. Use when the user says "place the order", "give me the ticket", "I
  want to buy", "go ahead", "yes", "execute", "how do I enter this", or after
  /binance-risk passes.
argument-hint: "show | approve SYMBOL | render SYMBOL | placed SYMBOL"
---

# binance-ticket

This is the only skill that leads to an order, and it does not place one. It
produces instructions; the user places the order in the Binance app with their
own hands. That is on purpose: no API key, no withdrawal risk, no bot with
access to the account.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                                  # READY / NOT READY per order
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve SOLUSDT --confirm "yes SOLUSDT"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" render SOLUSDT                        # the step-by-step ticket
python3 "${CLAUDE_SKILL_DIR}/ticket.py" placed SOLUSDT --price 148.2 --qty 0.19
python3 "${CLAUDE_SKILL_DIR}/ticket.py" cancel SOLUSDT
```

## The flow, every time

1. `/binance-risk --live` must have returned PASS for the order a few minutes ago.
2. Run `show`. Only a PASS order can become READY. If it says NOT READY, read the
   reasons to the user and stop; never work around them.
3. Show the user the order in plain words: "Buy 0.19 SOL at 148.20 (about 28 USDT),
   stop 144.00, take profit 156.60, planned loss about 1 USDT". Then ask: **do you
   want this exact order? Type "yes SOLUSDT".**
4. Only when the user has typed that, in their own words, run `approve` with their
   exact words in `--confirm`. Never type the yes for them, never reuse an earlier
   yes for a different order, and never treat "ok", "maybe" or silence as a yes.
5. Run `render` and give the user the ticket. They place it in the app.
6. When they say it is placed or filled, run `placed`, then log the fill with
   `/binance-journal`.

A yes is tied to the exact order (symbol, side, price, quantity, stop, target). If
anything changes, the yes is void and a new one is needed. A yes also expires
after `approval_ttl_minutes` (default 15), and so does the risk check.

## Rules

Not financial advice. The user may lose money; they decide, and the final click is
theirs. No yes, no ticket. Never ask the user for an API key or a secret key, and
never suggest pasting one into the chat; none is needed. A ticket for an order
that did not pass `/binance-risk` must never be shown. Reply in the language the
user writes in.
