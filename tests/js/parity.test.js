// Parity test: the JavaScript port must give the same answers as the Python reference.
// Run:  python3 -I tests/js/make_fixtures.py && node --test tests/js/
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
const BN = require('../../docs/core.js');
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures.json'), 'utf8'));
BN.setClock(() => FX.now);

const close = (a, b, msg, tol) => {
  if (a === null || b === null || a === undefined || b === undefined) return assert.equal(a, b, msg);
  const t = tol || 1e-9 * Math.max(1, Math.abs(b));
  assert.ok(Math.abs(a - b) <= t, `${msg}: js=${a} py=${b}`);
};

function fakeApi(raw) {
  return {
    async getJson(p, params) {
      params = params || {};
      const want = params.symbols ? JSON.parse(params.symbols) : null;
      if (p === '/api/v3/exchangeInfo') return { symbols: raw.exchangeInfo.symbols.filter((s) => !want || want.includes(s.symbol)) };
      if (p === '/api/v3/ticker/24hr') {
        if (params.symbol) return raw.tickers.find((t) => t.symbol === params.symbol);
        return raw.tickers;
      }
      if (p === '/api/v3/ticker/bookTicker') {
        if (params.symbol) return raw.books.find((t) => t.symbol === params.symbol);
        return raw.books.filter((b) => !want || want.includes(b.symbol));
      }
      if (p === '/api/v3/klines') {
        const k = raw.klines[params.symbol];
        const key = params.interval + (params.limit === 200 ? '200' : params.limit === 120 ? '120' : '');
        return k[key] || k[params.interval];
      }
      throw new Error('unexpected ' + p);
    }
  };
}

test('indicators and SHA-256', () => {
  const crypto = require('crypto');
  for (const s of ['', 'abc', 'x'.repeat(130)]) assert.equal(BN.sha256hex(s), crypto.createHash('sha256').update(s).digest('hex'));
  const closes = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 3) * 5 + i * 0.1);
  assert.equal(BN.ema(closes, 20).length, 41);
  assert.equal(BN.rsi(closes.slice(0, 10)), null);
});

for (const name of ['btc_up', 'btc_down']) {
  test('scanner matches Python: ' + name, async () => {
    const { raw, expected } = FX.scan[name];
    BN.setClock(() => FX.now);
    const cfg = BN.mergeConfig({});
    const rep = await BN.runScan(fakeApi(raw), cfg, { top: 40, concurrency: 3 });
    const key = (r) => r.map((x) => x.symbol).sort();
    assert.deepEqual(key(rep.candidates), key(expected.candidates));
    assert.deepEqual(key(rep.watch), key(expected.watch));
    assert.equal(rep.scanned, expected.scanned);
    assert.equal(rep.market.btc.ok, expected.market.btc.ok);
    for (const e of [...expected.candidates, ...expected.watch]) {
      const j = [...rep.candidates, ...rep.watch].find((x) => x.symbol === e.symbol);
      assert.equal(j.score, e.score, e.symbol + ' score');
      assert.deepEqual(j.breakdown, e.breakdown, e.symbol + ' breakdown');
      assert.equal(j.setup, e.setup);
      assert.equal(j.qualified, e.qualified);
      assert.deepEqual(j.why_not, e.why_not, e.symbol + ' reasons');
      for (const f of ['entry', 'stop', 'take_profit', 'take_profit_2', 'stop_pct', 'atr15_pct', 'rsi15', 'rsi1h', 'rel_volume', 'spread_pct', 'quote_volume_24h'])
        close(j[f], e[f], e.symbol + '.' + f);
    }
  });
}

test('scanner with explicit symbols', async () => {
  const { raw } = FX.scan.btc_up, exp = FX.scan.picked.expected;
  const rep = await BN.runScan(fakeApi(raw), BN.mergeConfig({}), { symbols: FX.scan.picked.symbols });
  assert.equal(rep.scanned, exp.scanned);
  assert.deepEqual([...rep.candidates, ...rep.watch].map((x) => x.symbol).sort(), [...exp.candidates, ...exp.watch].map((x) => x.symbol).sort());
});

test('stablecoins and bStocks are left out of the default scan', () => {
  const t = (s, v) => ({ symbol: s, lastPrice: '10', quoteVolume: String(v) });
  const cfg = BN.mergeConfig({});
  const r = BN.pickFromTickers([t('RLUSDUSDT', 9e9), t('GOOGLBUSDT', 9e9), t('QQQBUSDT', 9e9), t('BNBUSDT', 9e8), t('SHIBUSDT', 9e8), t('TRBUSDT', 9e8),
    t('SOLUSDT', 9e8), t('USDCUSDT', 9e9), t('FDUSDUSDT', 9e9), t('BTCETH', 1e9), t('XYZUSDT', 1e3)], cfg);
  assert.deepEqual(r.rows.map((x) => x.symbol), ['BNBUSDT', 'SHIBUSDT', 'TRBUSDT', 'SOLUSDT']);
  assert.equal(r.skipped.bstock, 2);
  const withB = BN.pickFromTickers([t('GOOGLBUSDT', 9e9)], BN.mergeConfig({ exclude_bstocks: false }));
  assert.equal(withB.rows.length, 1);
});

test('analyst matches Python', async () => {
  const raw = FX.scan.btc_up.raw;
  for (const sym of Object.keys(FX.analyst)) {
    const e = FX.analyst[sym], j = await BN.analyseSymbol(fakeApi(raw), sym, FX.now);
    assert.equal(j.verdict, e.verdict, sym);
    for (const tf of ['4h', '1h', '15m']) {
      assert.equal(j.timeframes[tf].trend, e.timeframes[tf].trend);
      for (const f of ['close', 'ema20', 'ema50', 'rsi', 'atr_pct']) close(j.timeframes[tf][f], e.timeframes[tf][f], sym + tf + f);
    }
    assert.deepEqual(j.support, e.support); assert.deepEqual(j.resistance, e.resistance);
    for (const f of ['atr_stop', 'target_2r', 'target_3r', 'stop_pct']) close(j.levels[f], e.levels[f], f);
    assert.equal(j.levels.stop_note, e.levels.stop_note);
    close(j.levels.support_stop, e.levels.support_stop, 'support_stop');
    for (const f of ['change_24h_pct', 'quote_volume_24h', 'pct_below_24h_high', 'volume_trend_1h', 'spread_pct']) close(j.stats[f], e.stats[f], f);
  }
});

test('budget sizing matches Python in all 36 cases', () => {
  for (const c of FX.budget) {
    const cfg = BN.mergeConfig(c.extra);
    const filters = {};
    for (const s of Object.keys(FX.filters)) filters[s] = FX.filters[s];
    const plan = BN.buildPlan(c.cand, filters, c.balance, cfg, c.journal, 5);
    const label = JSON.stringify([c.balance, c.extra, c.journal]);
    assert.equal(plan.orders.length, c.expected.orders.length, label);
    const norm = (a) => a.map((x) => ({ symbol: x.symbol, reason: x.reason.replace(/(\d)\.0(%| )/g, '$1$2') }));   // Python prints 25.0, JS 25
    assert.deepEqual(norm(plan.skipped), norm(c.expected.skipped), label);
    plan.orders.forEach((o, i) => {
      const e = c.expected.orders[i];
      for (const f of ['id', 'symbol', 'side', 'type', 'price_str', 'quantity_str', 'status', 'setup']) assert.equal(o[f], e[f], label + f);
      for (const f of ['price', 'quantity', 'notional', 'stop_price', 'stop_limit_price', 'take_profit', 'take_profit_2', 'risk_usdt', 'risk_pct', 'reward_risk', 'fees_est', 'min_notional'])
        close(o[f], e[f], label + ' ' + f, 1e-9 * Math.max(1, Math.abs(e[f])));
      assert.deepEqual(o.notes, e.notes, label);
    });
    close(plan.cash_after, c.expected.cash_after, label + ' cash_after', 1e-6);
  }
});

test('risk verdicts match Python in all cases', () => {
  for (const c of FX.risk) {
    const doc = { orders: [JSON.parse(JSON.stringify(c.order))] };
    const cfg = BN.mergeConfig(c.cfg);
    const res = BN.runRisk(doc, cfg, JSON.parse(JSON.stringify(c.journal)), c.portfolio, c.prices, c.live);
    const label = c.variant + '/' + c.journal_name + '/live=' + c.live;
    assert.equal(res.length, c.expected.length, label);
    for (let i = 0; i < res.length; i++) {
      assert.equal(res[i].risk.verdict, c.expected[i].verdict, label + ' ' + JSON.stringify(res[i].risk.reasons) + ' vs ' + JSON.stringify(c.expected[i].reasons));
      assert.equal(res[i].risk.reasons.length, c.expected[i].reasons.length, label);
      assert.equal(res[i].status, c.expected[i].status, label);
    }
  }
});

test('journal matches Python', () => {
  const j = { open: [], closed: [] };
  const FEE = 0.1;
  FX.journal.steps.forEach((s) => {
    const k = s.kwargs || {};
    let r;
    if (s.kind === 'open') r = BN.openPosition(j, s.args[0], s.args[1], s.args[2], s.args[3] === undefined ? null : s.args[3], s.args[4] === undefined ? null : s.args[4], k.fee === undefined && s.args[5] !== undefined ? s.args[5] : (k.fee === undefined ? null : k.fee), FEE);
    else r = BN.closePosition(j, s.args[0], s.args[1], s.args[2] === undefined ? null : s.args[2], s.args[3] !== undefined ? s.args[3] : (k.fee === undefined ? null : k.fee), FEE);
    for (const f of Object.keys(s.result)) {
      if (f.endsWith('_at')) continue;
      if (typeof s.result[f] === 'number') close(r[f], s.result[f], s.kind + ' ' + f, 1e-9 * Math.max(1, Math.abs(s.result[f])));
      else assert.equal(r[f], s.result[f], f);
    }
  });
  assert.equal(j.open.length, FX.journal.final.open.length);
  assert.equal(j.closed.length, FX.journal.final.closed.length);
  const st = BN.journalStats(j.closed), e = FX.journal.stats;
  for (const f of Object.keys(e)) close(st[f], e[f], 'stats.' + f, 1e-7);
});

test('alerts match Python', () => {
  const doc = { alerts: [] };
  BN.addAlert(doc, 'sol', 'above', 120);
  BN.addAlert(doc, 'BTCUSDT', 'below', 49000, 'stop', 'open position');
  BN.addAlert(doc, 'ETH', 'above', 3200, 'target');
  const fired = BN.checkAlerts(doc, { SOLUSDT: 121, BTCUSDT: 50000, ETHUSDT: 3100 });
  assert.deepEqual(fired.map((f) => f.alert.id), FX.alerts.fired.map((f) => f.id));
  assert.deepEqual(fired.map((f) => f.text), FX.alerts.fired.map((f) => f.text));
  assert.deepEqual(BN.checkAlerts(doc, { SOLUSDT: 121 }), []);      // fires once
});

test('rebalance matches Python', () => {
  const r = FX.rebalance;
  const c = BN.computeRebalance(r.portfolio, r.targets, r.prices);
  assert.equal(BN.rebalanceBreach(c.rows, 5), r.breach);
  assert.deepEqual(c.unpriced, r.unpriced); assert.deepEqual(c.untargeted, r.untargeted);
  close(c.total, r.total, 'total');
  c.rows.forEach((row, i) => { for (const f of ['value', 'weight', 'drift', 'trade_usdt']) close(row[f], r.rows[i][f], row.asset + f, 1e-7); assert.equal(row.asset, r.rows[i].asset); });
  const o = BN.makeRebalanceOrders(c.rows, r.filters, 10, 0.1);
  assert.equal(o.orders.length, r.orders.length);
  o.orders.forEach((x, i) => { for (const f of ['symbol', 'side', 'price_str', 'quantity_str', 'reason']) assert.equal(x[f], r.orders[i][f], f); });
  assert.deepEqual(o.skipped, r.skipped);
  assert.throws(() => BN.computeRebalance(r.portfolio, { BTC: 50, USDT: 20 }, r.prices), /add up to/);
});

test('news tags and coin matching match Python', () => {
  FX.news.titles.forEach((t, i) => {
    const c = BN.classify(t);
    assert.deepEqual(c.tags, FX.news.classify[i][0], t);
    assert.equal(c.high, FX.news.classify[i][1], t);
    assert.deepEqual(BN.coinsIn(t, FX.news.coins), FX.news.coins_in[i], t);
  });
});

test('safety: the dashboard code cannot place orders or sign requests', () => {
  const docs = path.join(__dirname, '..', '..', 'docs');
  const forbidden = [/\/api\/v3\/order/i, /\/sapi\//i, /\/fapi\//i, /\/dapi\//i, /signature/i, /hmac/i, /X-MBX-APIKEY/i, /newOrder/i, /createOrder/i, /api[_-]?secret/i, /\/withdraw/i];
  for (const f of ['core.js', 'app.js', 'index.html']) {
    const src = fs.readFileSync(path.join(docs, f), 'utf8');
    for (const rx of forbidden) assert.ok(!rx.test(src), f + ' matches ' + rx);
  }
  // the only writes to the network are the GitHub contents API for the user's own storage
  const core = fs.readFileSync(path.join(docs, 'core.js'), 'utf8');
  const methods = core.match(/method:\s*'(POST|PUT|DELETE|PATCH)'/g) || [];
  assert.deepEqual(methods, ["method: 'PUT'"]);
  assert.ok(/api\.github\.com\/repos\//.test(core));
  assert.ok(!/fetch\(/.test(fs.readFileSync(path.join(docs, 'core.js'), 'utf8').replace(/fetchImpl\(/g, '')), 'core.js only uses the injected fetch');
});

test('every UI string used by the app exists in both languages', () => {
  const I18N = require('../../docs/i18n.js');
  const app = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'app.js'), 'utf8');
  const keys = new Set();
  for (const m of app.matchAll(/\bt\('([A-Za-z0-9_ ]+)'\)/g)) keys.add(m[1]);
  for (const m of app.matchAll(/\bt\([^()']*\?\s*'([A-Za-z0-9_]+)'\s*:\s*'([A-Za-z0-9_]+)'\)/g)) { keys.add(m[1]); keys.add(m[2]); }
  for (const m of app.matchAll(/t\('(st_|how)'\s*\+/g)) keys.add(m[1]);
  ['st_proposed', 'st_blocked', 'st_risk_pass', 'st_approved', 'st_placed', 'st_cancelled', 'st_closed', 'how1', 'how2', 'how3', 'how4', 'how5', 'how6', 'READY', 'NOT READY', 'DONE',
    'cloud_off', 'cloud_ok', 'cloud_saving', 'cloud_syncing', 'cloud_error', 'cloud_conflict', 'home', 'scan', 'orders', 'journal', 'analyst', 'news', 'alerts', 'rebalance', 'settings', 'more'].forEach((k) => keys.add(k));
  for (const k of keys) {
    if (k === 'st_' || k === 'how') continue;
    assert.ok(k in I18N.ar, 'missing Arabic: ' + k);
    assert.ok(k in I18N.en, 'missing English: ' + k);
  }
  assert.deepEqual(Object.keys(I18N.ar).sort(), Object.keys(I18N.en).sort());
});
