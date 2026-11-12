# The Binance Spot skill pack

Nine Claude skills for Binance Spot (USDT pairs): scan the market on three
timeframes, deep-dive a coin, watch the news and listings, size every trade to
your risk, check it against your rules, and hand you a ticket to place **by hand**.
Nothing in this pack can place an order, and nothing asks for your API key.

Free, MIT. The tools are plain Python (standard library only) that read Binance's
public market data and public RSS feeds.

```
scanner -> (analyst) -> budget -> risk -> ticket -> [you place it in the Binance app] -> journal
news / alerts                                    (watch, on a schedule)
rebalance -> risk -> ticket                      (maintain long-term holdings)
```

## Why no API key

The pack only needs public prices, so it works with no key at all. Execution is
manual on purpose: no bot with access to your account, nothing to leak, nothing
that can withdraw. The last step (`/binance-ticket`) tells you exactly what to
type into the Binance app, and records your yes for that exact order first.

## Install

Copy the folders into Claude Code's skills directory:

```bash
cp -r binance-trader-skill/binance-* ~/.claude/skills/
```

Project-local instead of global: copy them into your repo's `.claude/skills/`.
As a plugin, in Claude Code:

```
/plugin marketplace add xorgz126546/binance-trader-skill
/plugin install binance-trader-skill@binance-trader-skill
```

Without Claude Code, paste any single `SKILL.md` at the top of a chat and it runs
as a mode (you lose the Python tools, but the method works). Python 3.8 or newer;
nothing to pip install.

Then create `binance/config.json` in your project (start from
`binance-budget/config.example.json`) and set at least `budget_usdt`.

## The dashboard (phone and computer)

`docs/` is a web app with all nine skills in one place: scanner, analyst, news, sizing,
risk gate, confirmation ticket, alerts, rebalance and journal. Arabic and English, works
on a phone. It reads Binance's **public** market data straight from your browser, so there
is no server, no API key and no account access, and it still **never places an order**:
you confirm by typing `yes SYMBOL`, then place the order yourself in the Binance app.
The logic is a port of the Python tools and is tested against them (`tests/js/`).

**Open it**

1. On GitHub: this repository, Settings, Pages, Source "Deploy from a branch", Branch
   `main`, folder `/docs`. After a minute the page is at
   `https://<your-user>.github.io/<repo-name>/`.
2. Or locally: open `docs/index.html` in a browser (no install, no build).
3. On a phone, open the page link and use "Add to Home Screen".

**Cloud storage (optional)**. By default your settings, journal, alerts and orders stay in
the browser of that device. To keep them in the cloud and share them between phone and
computer:

1. Create an empty **private** repository, for example `binance-dashboard-data`.
2. GitHub, Settings, Developer settings, Fine-grained tokens: a token for **that repository
   only**, with "Contents: Read and write".
3. In the dashboard, Settings, Cloud storage: paste `owner/binance-dashboard-data` and the
   token, then "Test connection". The dashboard refuses a public repository.

The token is stored only on the device you typed it on and is never part of the cloud copy.
The data file holds your budget, trades and alerts, so keep that repository private. If two
devices change the data at once, the dashboard shows the conflict and lets you choose which
copy wins. Alerts are only checked while the page is open (no Telegram in the browser, so no
bot token ever lives in a browser).

Network note: news RSS feeds do not allow browsers to read them directly, so they go through
the public rss2json.com converter; Binance's own announcement list is read directly. If your
network blocks Binance, the dashboard says so instead of showing "no news".

## The nine

| command | job | what it does |
| --- | --- | --- |
| `/binance-scanner` | Scanner | Ranks USDT pairs on 4h trend, 1h confirmation, 15m entry; score 0-100; BTC, spread and volatility filters; entry, stop and targets from ATR. Says NO TRADE when nothing passes and shows why. |
| `/binance-analyst` | Coin Analyst | Deep dive on one pair: trend per timeframe, EMA, RSI, ATR, swing support and resistance, 24h range, a verdict. |
| `/binance-news` | News Monitor | Crypto headlines and Binance listing / delisting notices for your coins, tagged, urgent ones first. |
| `/binance-budget` | Budget Manager | Sizes each trade so a stop-out loses a fixed percent of your budget (fees included), rounds to Binance's tick, step and minimum order. Works for small accounts. |
| `/binance-risk` | Risk Manager | PASS or BLOCK per order: spot only, stop required, reward-to-risk, position cap, cash buffer, open positions, daily loss limit, stale price. |
| `/binance-ticket` | Order Ticket | Records your yes for the exact order, then prints the manual steps: limit order, then the OCO stop and take profit after the fill. Never sends anything. |
| `/binance-alerts` | Price Alerts | Stop, target and price alerts, optional Telegram message. Run once or on a schedule. |
| `/binance-rebalance` | Rebalancer | Brings your holdings back to target weights when they drift past a band. |
| `/binance-journal` | Trade Journal | Your real fills, P&L net of fees, R-multiples, win rate, profit factor, drawdown. Feeds the daily loss limit and the position limit. |

## A run, end to end

1. **`/binance-scanner`** "any setups?" Scans the top 40 USDT pairs by volume.
   BTC must be above its 1h EMA50. It lists the pairs that passed, or says NO TRADE
   and shows the closest misses with the reason each failed.
2. **`/binance-budget`** with a 100 USDT budget, 1% risk: each candidate becomes a
   limit-buy proposal. The quantity is the one where hitting the stop costs about
   1 USDT, capped at 25% of the budget, keeping a 10% cash buffer.
3. **`/binance-risk --live`** checks each proposal against your limits and the
   current price. A BLOCK stops there, with the reason.
4. **`/binance-ticket`** shows the order in plain words. You type `yes SOLUSDT`.
   It then prints the steps for the Binance app: Limit buy, price, amount; and once
   filled, an OCO sell with the take profit, the stop trigger and the stop limit.
5. You place it. Tell Claude the real fill; **`/binance-journal`** records it and
   **`/binance-alerts from-journal`** watches the stop and target.
6. On a schedule: `/binance-news` for your open positions, `/binance-alerts check`.

## The tools

```bash
python3 binance-scanner/scan.py --write                       # -> binance/candidates.json
python3 binance-analyst/analyze.py SOL
python3 binance-news/news.py --coins BTC,ETH,SOL --hours 24
python3 binance-budget/size.py --write                        # -> binance/orders.json
python3 binance-risk/check.py --live                          # PASS / BLOCK per order
python3 binance-ticket/ticket.py show
python3 binance-alerts/alerts.py from-journal
python3 binance-rebalance/rebalance.py --band 5
python3 binance-journal/journal.py stats
```

Everything lands in a `binance/` folder in your project (override with the
`BINANCE_SKILL_DIR` environment variable). If your network blocks Binance's API,
set `BINANCE_BASE_URL` to a reachable endpoint with the same paths.

```bash
python3 -m unittest discover -s tests -v
```

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and cannot see the future. Crypto is
volatile and you can lose all of what you put in. Decide for yourself.

**No order goes through until you say yes**, and even then you place it yourself.
Only `/binance-ticket` leads to an order, only after `/binance-risk` passes that
exact order and you type `yes SYMBOL` for it. Spot only: no margin, futures,
leverage or borrowing. Limit orders only unless you turn market orders on.

**The score is a heuristic, not a proven edge.** The scanner's rules (EMA trend,
breakout, volume, RSI) are common and transparent, and they have not been
backtested here. A high score is a better-looking setup, not a prediction. Keep
the journal and judge the method on your own numbers, over at least 30 trades.

**Tested offline only.** The unit tests run on synthetic data shaped like Binance's
responses (klines, exchangeInfo, tickers, RSS). They check the arithmetic and the
safety rules. They do not prove that the live endpoints behave identically on your
network: run `scan.py --symbols BTCUSDT` once and check the numbers against the
Binance chart before you rely on it. Binance's announcement endpoint (listings,
delistings) is unofficial and may change; the News skill says so when it fails.

**Fees and stops.** Sizing assumes 0.1% fee per side (set `fee_pct`; 0.075 with the
BNB discount). A stop on Spot can fill worse than its price in a fast market.

**Taxes and local rules** depend on where you live. Nothing here is tax advice.

## Tests

```bash
python3 -I -m unittest discover -s tests            # the Python tools
python3 -I tests/js/make_fixtures.py                   # run the Python reference on synthetic data
node --test tests/js/parity.test.js                    # the dashboard logic must match it
npm i jsdom && node --test tests/js/ui.test.js         # optional: click through the whole UI
```

## Files

```
binance-scanner/    SKILL.md, scan.py
binance-analyst/    SKILL.md, analyze.py
binance-news/       SKILL.md, news.py
binance-budget/     SKILL.md, size.py, config.example.json
binance-risk/       SKILL.md, check.py
binance-ticket/     SKILL.md, ticket.py
binance-alerts/     SKILL.md, alerts.py
binance-rebalance/  SKILL.md, rebalance.py
binance-journal/    SKILL.md, journal.py
docs/               the dashboard: index.html, core.js (logic), app.js (UI), i18n.js, style.css
tests/              unittest suite + synthetic fixtures; tests/js has the dashboard parity and UI tests
```

MIT. Inspired by the structure of Jake Schincariol's stock-market Trader skill
(github.com/Jakeschincariol/trader-skill); no code is copied from it.
