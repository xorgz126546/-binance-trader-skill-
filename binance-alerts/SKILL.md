---
name: binance-alerts
description: >-
  Price Alerts for Binance Spot. Rings when a coin crosses a level: a stop, a
  take-profit, an entry that may have filled, or any price. Builds the stop and
  target alerts for every open position in one command, checks public prices, and
  can send a Telegram message. Run it once, or on a schedule. Reads public prices
  only: no Binance API key, no account, no orders. Use when the user says "alert
  me", "notify me when BTC hits", "tell me if SOL drops to", "watch my stops",
  "set alerts for my positions", "ping me on Telegram", or to protect an open trade.
argument-hint: "add SYMBOL above|below LEVEL | check | watch"
---

# binance-alerts

An alert is a promise to look so the user does not have to stare at a chart.
It reports; it never acts. When a stop alert fires, the user decides what to do
in the Binance app.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add SOLUSDT below 144 --kind stop --note "SOL stop"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add BTC above 70000
python3 "${CLAUDE_SKILL_DIR}/alerts.py" from-journal         # stop + target alert per open position
python3 "${CLAUDE_SKILL_DIR}/alerts.py" from-orders          # entry + stop alert per approved order
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                 # look once
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check --telegram     # and message Telegram
python3 "${CLAUDE_SKILL_DIR}/alerts.py" watch --interval 60 --telegram   # keep looking until Ctrl-C
python3 "${CLAUDE_SKILL_DIR}/alerts.py" test-telegram
```

Alerts are stored in `binance/alerts.json`. An alert fires once and is then marked
fired, so it does not repeat; `rearm ID` arms it again, `remove ID` deletes it.
Crypto trades 24/7, so unlike stocks there is no market clock: alerts can fire any hour.

## Telegram

Optional. The user creates a bot with BotFather, then sets two **environment
variables** on their machine: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never
ask for the token in the chat and never write it into a file or a command; the
script reads it from the environment and never prints it. Check the setup with
`test-telegram`. If the variables are missing, `check --telegram` still prints
the alerts and says why nothing was sent.

## Running it on a schedule

`check` is made for a scheduled task (for example every 5 minutes): it looks once,
reports what fired, and exits. Offer to schedule it after `from-journal`. A
`watch` loop only runs while its terminal stays open, so say that if the user
picks it.

## How to talk about it

State what fired in one line: coin, price, which level, which kind. A **stop**
alert is urgent: say so first, and remind the user that the OCO or stop order they
placed in the app is what actually protects the position, not this alert.

## Rules

Not financial advice. An alert is not an order and not a guarantee: prices can
gap through a level, and a check only sees the moment it runs. Nothing here places
an order; any trade needs the user's yes at the ticket step and is placed by hand.
Reply in the language the user writes in.
