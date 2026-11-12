// UI smoke test: loads docs/index.html in jsdom with a fake Binance / GitHub / news network and
// walks the whole workflow: scan -> size -> risk -> confirm -> steps -> place -> journal -> alerts -> rebalance -> cloud.
// Needs jsdom (npm i jsdom). Skipped when it is not installed.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');
let JSDOM;
try { JSDOM = require('jsdom').JSDOM; } catch (e) { JSDOM = null; }
const FX = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures.json'), 'utf8'));
const DOCS = path.join(__dirname, '..', '..', 'docs');
const raw = FX.scan.btc_up.raw;
const WINDOWS = [];
test.after(() => WINDOWS.forEach((w) => { try { w.close(); } catch (e) { /* ignore */ } }));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeNetwork(github) {
  const calls = [];
  const resp = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  async function fetch(url, opts) {
    opts = opts || {};
    const u = new URL(url);
    calls.push(u.origin + u.pathname);
    if (u.hostname === 'data-api.binance.vision') {
      const p = u.pathname, q = Object.fromEntries(u.searchParams), want = q.symbols ? JSON.parse(q.symbols) : null;
      if (p === '/api/v3/exchangeInfo') return resp(200, { symbols: raw.exchangeInfo.symbols.filter((s) => !want || want.includes(s.symbol)) });
      if (p === '/api/v3/ticker/24hr') return resp(200, q.symbol ? raw.tickers.find((t) => t.symbol === q.symbol) : raw.tickers);
      if (p === '/api/v3/ticker/bookTicker') return resp(200, q.symbol ? raw.books.find((t) => t.symbol === q.symbol) : raw.books.filter((b) => !want || want.includes(b.symbol)));
      if (p === '/api/v3/ticker/price') return resp(200, want ? want.filter((s) => raw.prices[s]).map((s) => ({ symbol: s, price: raw.prices[s] })) : { symbol: q.symbol, price: raw.prices[q.symbol] });
      if (p === '/api/v3/klines') { const k = raw.klines[q.symbol]; return resp(200, k[q.interval + (q.limit === '200' ? '200' : q.limit === '120' ? '120' : '')] || k[q.interval]); }
    }
    if (u.hostname === 'www.binance.com') return resp(200, { data: { articles: [{ title: 'Binance will list NEWCOIN (NEW)', code: 'abc', releaseDate: FX.now - 3600000 }] } });
    if (u.hostname === 'api.rss2json.com') return resp(200, { status: 'ok', items: [{ title: 'Hackers drain a bridge, UP users hit', link: 'https://x.test/a', pubDate: new Date(FX.now - 7200000).toISOString().slice(0, 19).replace('T', ' ') }] });
    if (u.hostname === 'api.github.com' && github) return github(u, opts, resp);
    return resp(404, {});
  }
  return { fetch, calls };
}
function fakeGithub(isPrivate) {
  const files = {}; let n = 0; const log = [];
  const handler = (u, opts, resp) => {
    const m = /^\/repos\/([^/]+\/[^/]+)(\/contents\/(.+))?$/.exec(u.pathname);
    if (!m) return resp(404, {});
    if (!opts.headers || !/^Bearer /.test(opts.headers.Authorization)) return resp(401, {});
    if (!m[2]) return resp(200, { private: isPrivate, default_branch: 'main' });
    const file = decodeURIComponent(m[3]);
    if ((opts.method || 'GET') === 'GET') return files[file] ? resp(200, { sha: files[file].sha, content: Buffer.from(files[file].text).toString('base64') }) : resp(404, {});
    const body = JSON.parse(opts.body);
    if (files[file] && body.sha !== files[file].sha) return resp(409, {});
    if (!files[file] && body.sha) return resp(422, {});
    files[file] = { sha: 'sha' + (++n), text: Buffer.from(body.content, 'base64').toString('utf8') };
    log.push(body.message);
    return resp(200, { content: { sha: files[file].sha } });
  };
  return { handler, files, log };
}
async function boot(net, store) {
  const html = fs.readFileSync(path.join(DOCS, 'index.html'), 'utf8').replace(/<script src="[^"]+"><\/script>/g, '');
  const errors = [];
  const dom = new JSDOM(html, { url: 'https://example.github.io/dash/', runScripts: 'outside-only', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = net.fetch;
      w.addEventListener('error', (e) => errors.push(e.message));
      const ls = store || {};
      Object.defineProperty(w, 'localStorage', { value: { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: (k) => { delete ls[k]; } } });
      w.scrollTo = () => {};
      w.__ls = ls;
    } });
  const w = dom.window;
  WINDOWS.push(w);
  for (const f of ['core.js', 'i18n.js']) w.eval(fs.readFileSync(path.join(DOCS, f), 'utf8'));
  w.BN.setClock(() => FX.now);
  w.eval(fs.readFileSync(path.join(DOCS, 'app.js'), 'utf8'));
  return { w, errors, dom };
}
const text = (w) => w.document.getElementById('view').textContent;
const click = async (w, act, data) => {
  const el = w.document.createElement('button'); el.dataset.act = act;
  Object.assign(el.dataset, data || {});
  w.document.body.appendChild(el); el.click(); el.remove();
  await sleep(20);
};
const setv = (w, id, v) => { const e = w.document.getElementById(id); assert.ok(e, 'missing #' + id); if (e.type === 'checkbox') e.checked = !!v; else e.value = v; };
const toasts = (w) => Array.from(w.document.querySelectorAll('.toast')).map((x) => x.textContent);

test('whole workflow in the browser UI', { skip: !JSDOM && 'jsdom not installed' }, async () => {
  const net = makeNetwork();
  const { w, errors } = await boot(net);
  const B = w.__BND;
  // every view renders without junk
  for (const v of ['home', 'scan', 'orders', 'journal', 'analyst', 'news', 'alerts', 'rebalance', 'settings']) {
    await click(w, 'nav', { v });
    const t = text(w);
    assert.ok(t.length > 40, v + ' is empty');
    assert.ok(!/undefined|NaN|\[object/.test(t), v + ' shows junk: ' + t.match(/.{20}(undefined|NaN|\[object).{20}/));
  }
  assert.equal(w.document.documentElement.dir, 'rtl');
  await click(w, 'lang'); assert.equal(w.document.documentElement.dir, 'ltr');
  assert.match(w.document.getElementById('top').textContent, /Binance Trader Dashboard/);

  // scan
  await click(w, 'nav', { v: 'scan' });
  setv(w, 'scan-top', 40); setv(w, 'scan-min', 75);
  await click(w, 'run-scan'); await sleep(200);
  assert.ok(B.ST && w.document.getElementById('view').textContent.includes('UPUSDT'), 'UPUSDT should qualify');
  assert.ok(!text(w).includes('USDCUSDT'), 'stablecoin must not be scanned');
  assert.match(text(w), /Excluded/);

  // size -> orders
  await click(w, 'size-top'); await sleep(100);
  assert.equal(B.D.view, 'orders');
  assert.equal(B.ST.orders.orders.length, 1);
  const o = B.ST.orders.orders[0];
  assert.equal(o.symbol, 'UPUSDT'); assert.equal(o.status, 'proposed');

  // approving before a risk check must fail
  await click(w, 'risk-all'); await sleep(50);
  assert.equal(B.ST.orders.orders[0].risk.verdict, 'PASS', JSON.stringify(B.ST.orders.orders[0].risk));
  assert.equal(B.ST.orders.orders[0].status, 'risk_pass');
  setv(w, 'yes-' + o.id, 'yes SOLUSDT'); await click(w, 'approve', { id: o.id });
  assert.equal(B.ST.orders.orders[0].status, 'risk_pass', 'wrong symbol must not approve');
  assert.ok(toasts(w).some((x) => /must be you typing/.test(x)));
  setv(w, 'yes-' + o.id, 'YES upusdt'); await click(w, 'approve', { id: o.id });
  assert.equal(B.ST.orders.orders[0].status, 'approved');
  await click(w, 'steps', { id: o.id });
  assert.match(w.document.getElementById('modal').textContent, /OCO/);
  await click(w, 'modal-close');
  // tamper after the yes: the yes dies
  B.ST.orders.orders[0].quantity = B.ST.orders.orders[0].quantity * 2;
  assert.equal(w.BN.readiness(B.ST.orders.orders[0], w.BN.mergeConfig({}))[0], 'NOT READY');
  B.ST.orders.orders[0].quantity = B.ST.orders.orders[0].quantity / 2;
  // place + record fill
  await click(w, 'placed', { id: o.id });
  await click(w, 'placed-ok', { id: o.id });
  assert.equal(B.ST.orders.orders[0].status, 'placed');
  await click(w, 'record-fill', { id: o.id });
  await click(w, 'record-fill-ok', { id: o.id });
  assert.equal(B.ST.journal.open.length, 1);
  assert.ok(B.ST.alerts.alerts.length >= 2, 'stop + target alerts were created');

  // home shows the position, close it with a loss -> stats
  await click(w, 'nav', { v: 'home' });
  await click(w, 'refresh-home'); await sleep(50);
  assert.match(text(w), /UPUSDT/);
  await click(w, 'close-pos', { sym: 'UPUSDT' });
  setv(w, 'cp-price', B.ST.journal.open[0].entry * 0.98);
  await click(w, 'close-ok', { sym: 'UPUSDT' });
  assert.equal(B.ST.journal.closed.length, 1); assert.ok(B.ST.journal.closed[0].pnl < 0);
  await click(w, 'nav', { v: 'journal' }); assert.match(text(w), /UPUSDT/);

  // alerts: a price alert that is already true fires
  await click(w, 'nav', { v: 'alerts' });
  setv(w, 'al-sym', 'UP'); setv(w, 'al-dir', 'above'); setv(w, 'al-lvl', 1); await click(w, 'alert-add');
  await click(w, 'alerts-check'); await sleep(50);
  assert.ok(B.ST.alerts.alerts.some((a) => a.triggered_at), 'alert should have fired');
  assert.ok(w.document.getElementById('banner').textContent.includes('UPUSDT'));

  // analyst + news
  await click(w, 'analyze', { sym: 'UPUSDT' }); await sleep(100);
  assert.match(text(w), /BULLISH/);
  await click(w, 'nav', { v: 'news' }); setv(w, 'nw-coins', 'UP'); await click(w, 'run-news'); await sleep(100);
  assert.match(text(w), /Hackers drain/);

  // rebalance
  await click(w, 'nav', { v: 'rebalance' });
  setv(w, 'pf-asset', 'UP'); setv(w, 'pf-amt', 4); await click(w, 'pf-add');
  setv(w, 'pf-asset', 'USDT'); setv(w, 'pf-amt', 100); await click(w, 'pf-add');
  setv(w, 'tg-asset', 'UP'); setv(w, 'tg-pct', 50); await click(w, 'tg-add');
  setv(w, 'tg-asset', 'USDT'); setv(w, 'tg-pct', 50); await click(w, 'tg-add');
  await click(w, 'rb-run'); await sleep(100);
  assert.ok(B.U.rb && B.U.rb.breach, 'drift is outside the band');
  await click(w, 'rb-send');
  assert.ok(B.ST.orders.orders.some((x) => x.kind === 'rebalance'));

  // settings
  await click(w, 'nav', { v: 'settings' });
  setv(w, 'cfg-budget_usdt', 250); setv(w, 'cfg-blocked', 'luna, lunc');
  await click(w, 'cfg-save');
  assert.equal(B.ST.config.budget_usdt, 250); assert.equal(JSON.stringify(B.ST.config.blocked), '["LUNA","LUNC"]');
  assert.ok(w.__ls['bnd.state'].includes('"budget_usdt":250'), 'saved on this device');

  // safety: no order endpoints were ever called, only market data / news / (no github here)
  assert.ok(net.calls.every((c) => !/\/order|\/account|\/sapi|\/fapi/.test(c)), 'forbidden endpoint: ' + net.calls.join(' '));
  assert.equal(errors.length, 0, errors.join('; '));
});

test('cloud storage: save, second device loads, public repo refused, conflict', { skip: !JSDOM && 'jsdom not installed' }, async () => {
  const gh = fakeGithub(true);
  const net = makeNetwork(gh.handler);
  const a = await boot(net);
  await click(a.w, 'nav', { v: 'journal' });
  setv(a.w, 'jo-sym', 'SOL'); setv(a.w, 'jo-price', 100); setv(a.w, 'jo-qty', 2); setv(a.w, 'jo-stop', 95); await click(a.w, 'journal-open');
  await click(a.w, 'nav', { v: 'settings' });
  setv(a.w, 'cl-repo', 'me/private-data'); setv(a.w, 'cl-token', 'github_pat_x'); await click(a.w, 'cloud-save'); await sleep(100);
  assert.equal(a.w.__BND.CS.status, 'ok');
  assert.ok(gh.files['binance-dashboard-data.json'], 'file created in the repo');
  const saved = JSON.parse(gh.files['binance-dashboard-data.json'].text);
  assert.equal(saved.journal.open[0].symbol, 'SOLUSDT');
  assert.ok(!JSON.stringify(saved).includes('github_pat_x'), 'the token never leaves the device');
  // a change is pushed automatically
  await click(a.w, 'nav', { v: 'alerts' });
  setv(a.w, 'al-sym', 'SOL'); setv(a.w, 'al-lvl', 90); setv(a.w, 'al-dir', 'below'); await click(a.w, 'alert-add');
  await sleep(2800);
  assert.equal(JSON.parse(gh.files['binance-dashboard-data.json'].text).alerts.alerts.length, 1);

  // second device (empty local) picks the data up
  const b = await boot(net, { 'bnd.device': JSON.stringify({ lang: 'en', cloud: { repo: 'me/private-data', path: 'binance-dashboard-data.json', branch: '', token: 'github_pat_x', auto: true } }) });
  await sleep(150);
  assert.equal(b.w.__BND.ST.journal.open[0].symbol, 'SOLUSDT');
  // conflict: both change; b pushes first, a then conflicts
  await click(b.w, 'nav', { v: 'alerts' });
  setv(b.w, 'al-sym', 'ETH'); setv(b.w, 'al-lvl', 1); setv(b.w, 'al-dir', 'below'); await click(b.w, 'alert-add'); await sleep(2800);
  await click(a.w, 'nav', { v: 'alerts' });
  setv(a.w, 'al-sym', 'BTC'); setv(a.w, 'al-lvl', 1); setv(a.w, 'al-dir', 'below'); await click(a.w, 'alert-add'); await sleep(2900);
  assert.equal(a.w.__BND.CS.status, 'conflict');
  assert.match(a.w.document.getElementById('banner').textContent, /Conflict|تعارض/);
  await click(a.w, 'use-cloud');
  assert.ok(a.w.__BND.ST.alerts.alerts.some((x) => x.symbol === 'ETHUSDT'));
  assert.equal(a.w.__BND.CS.status, 'ok');
  assert.equal(a.errors.length + b.errors.length, 0, a.errors.concat(b.errors).join('; '));

  // a public repo is refused
  const gh2 = fakeGithub(false);
  const c = await boot(makeNetwork(gh2.handler));
  await click(c.w, 'nav', { v: 'settings' });
  setv(c.w, 'cl-repo', 'me/public-repo'); setv(c.w, 'cl-token', 'github_pat_y'); await click(c.w, 'cloud-save'); await sleep(100);
  assert.equal(Object.keys(gh2.files).length, 0, 'nothing is written to a public repo');
  assert.match(c.w.document.getElementById('view').textContent, /PUBLIC|عام/);
});
