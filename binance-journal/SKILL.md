---
name: binance-journal
description: >-
  Trade Journal for Binance Spot. The ledger of what the user really did: record
  a fill when a position opens, record the exit when it closes, with real prices,
  quantities and fees. Computes profit and loss net of fees, R-multiple against
  the planned stop, win rate, profit factor, expectancy and drawdown, and feeds
  open positions and today's losses back to /binance-budget and /binance-risk.
  Nothing is read from an account; the user tells Claude the fills. Use when the
  user says "I bought", "it filled", "I sold", "I got stopped out", "log this
  trade", "how am I doing", "my stats", "my P&L", "win rate".
argument-hint: "open SYMBOL --price P --qty Q | close SYMBOL --price P | stats"
---

# binance-journal

The journal is what turns a pile of trades into a method. It is also the memory
of the pack: the other skills read it to know what is already open and whether
today's loss limit was hit. Without it they are blind to real positions.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/journal.py" open SOLUSDT --price 148.2 --qty 0.19 --stop 144 --target 156.6
python3 "${CLAUDE_SKILL_DIR}/journal.py" close SOLUSDT --price 155.9            # all of it
python3 "${CLAUDE_SKILL_DIR}/journal.py" close SOLUSDT --price 152 --qty 0.1    # part of it
python3 "${CLAUDE_SKILL_DIR}/journal.py" list
python3 "${CLAUDE_SKILL_DIR}/journal.py" stats
python3 "${CLAUDE_SKILL_DIR}/journal.py" today
```

## Recording honestly

- Use the **real** average fill price and amount from the Binance order history,
  not the planned ones. If the user does not know them, ask; do not guess.
- Fees default to `fee_pct` per side from `binance/config.json` (0.1%). If the user
  paid fees in BNB or has a different rate, pass the real number with `--fee`
  (in USDT).
- Record a stop-out like any other exit, at the real fill price. A loss recorded
  is the journal doing its job; never leave a losing trade out.
- One position per coin. To add to a position, close and reopen at the new average.

## What the stats say

| stat | meaning |
| --- | --- |
| win rate / profit factor | how often, and gross wins divided by gross losses (above 1 is profitable) |
| expectancy | average USDT per trade after fees |
| avg R | average result in multiples of the planned risk (needs a stop recorded) |
| max drawdown | the deepest fall of the running P&L |

Under about 30 closed trades the stats mean little. Say so; do not declare the
method good or bad from five trades. If fees are eating the profit (`total_fees`
close to `total_pnl`), say that too.

## Files

`binance/journal.json`: `open` positions and `closed` trades. It is read by
`/binance-budget` (position limit, cash), `/binance-risk` (daily loss limit) and
`/binance-alerts` (`from-journal`). After `open`, offer to run
`/binance-alerts from-journal` so the stop and target are watched.

## Rules

Not financial advice. The journal records and measures; it does not decide, and
it cannot place an order. Any new trade still needs the user's yes at the ticket
step. Reply in the language the user writes in.
