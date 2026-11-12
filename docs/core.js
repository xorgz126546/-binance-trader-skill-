/* Binance Trader Dashboard - core logic (no DOM, no network of its own).
 * A faithful JavaScript port of the nine Python skills in this repository:
 * scanner, analyst, news, budget, risk, ticket, alerts, rebalance, journal.
 * Public market data only. No API key, no account access, no order placement.
 * Works in the browser (window.BN) and in Node (require) so it can be unit tested.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.BN = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ------------------------------------------------------------------ clock + small helpers */
  const clock = { now: function () { return Date.now(); } };
  function setClock(fn) { clock.now = fn; }
  function nowMs() { return clock.now(); }
  function isoNow() { return new Date(nowMs()).toISOString().replace(/\.\d+Z$/, 'Z'); }
  function parseTime(s) { const t = Date.parse(s); return Number.isNaN(t) ? null : t; }
  function minutesSince(s) { const t = parseTime(s); return t == null ? null : (nowMs() - t) / 60000; }
  function num(x, d) { const v = Number(x); return Number.isFinite(v) ? v : (d === undefined ? 0 : d); }
  function clone(x) { return JSON.parse(JSON.stringify(x)); }
  function sum(a) { let t = 0; for (const x of a) t += x; return t; }

  /* ------------------------------------------------------------------ exact decimals (tick / step sizes) */
  function decParse(x) {
    const s = String(x).trim();
    const m = /^(-?)(\d*)\.?(\d*)(?:e([+-]?\d+))?$/i.exec(s);
    if (!m || (m[2] === '' && m[3] === '')) throw new Error('not a number: ' + s);
    let scale = m[3].length - (m[4] ? parseInt(m[4], 10) : 0);
    let n = BigInt((m[2] + m[3]) || '0');
    if (m[1]) n = -n;
    if (scale < 0) { n = n * (10n ** BigInt(-scale)); scale = 0; }
    return { n: n, s: scale };
  }
  function decAlign(a, b) {
    const s = Math.max(a.s, b.s);
    return [a.n * (10n ** BigInt(s - a.s)), b.n * (10n ** BigInt(s - b.s)), s];
  }
  function decStr(d) {
    const neg = d.n < 0n;
    const abs = neg ? -d.n : d.n;
    const str = abs.toString().padStart(d.s + 1, '0');
    const int = str.slice(0, str.length - d.s);
    const frac = str.slice(str.length - d.s).replace(/0+$/, '');
    return (neg ? '-' : '') + int + (frac ? '.' + frac : '');
  }
  function decNum(d) { return Number(decStr(d)); }
  function floorTo(value, step) {
    const v = decParse(value), st = decParse(step);
    if (st.n <= 0n) return v;
    const t = decAlign(v, st);
    return { n: (t[0] / t[1]) * t[1], s: t[2] };            // values here are positive
  }
  function ceilTo(value, step) {
    const v = decParse(value), st = decParse(step);
    if (st.n <= 0n) return v;
    const t = decAlign(v, st);
    return { n: ((t[0] + t[1] - 1n) / t[1]) * t[1], s: t[2] };
  }
  function plain(x) { return decStr(typeof x === 'object' && x !== null && 'n' in x ? x : decParse(x)); }
  function stable(x) { return Number(Number(x).toPrecision(12)); }   // strips float noise before flooring

  /* ------------------------------------------------------------------ SHA-256 (for order fingerprints) */
  const SHA_K = [], SHA_H = [];
  (function () {
    let found = 0;
    for (let c = 2; found < 64; c++) {
      let prime = true;
      for (let d = 2; d * d <= c; d++) if (c % d === 0) { prime = false; break; }
      if (!prime) continue;
      SHA_K.push(Math.floor((Math.cbrt(c) % 1) * 4294967296));
      if (found < 8) SHA_H.push(Math.floor((Math.sqrt(c) % 1) * 4294967296));
      found++;
    }
  })();
  function sha256hex(str) {
    const msg = Array.from(new TextEncoder().encode(str));
    const bitLen = msg.length * 8;
    msg.push(0x80);
    while (msg.length % 64 !== 56) msg.push(0);
    const hi = Math.floor(bitLen / 4294967296), lo = bitLen >>> 0;
    [hi, lo].forEach(function (w) { msg.push((w >>> 24) & 255, (w >>> 16) & 255, (w >>> 8) & 255, w & 255); });
    const H = SHA_H.slice();
    const rotr = function (x, n) { return (x >>> n) | (x << (32 - n)); };
    for (let off = 0; off < msg.length; off += 64) {
      const w = new Array(64);
      for (let i = 0; i < 16; i++) w[i] = ((msg[off + 4 * i] << 24) | (msg[off + 4 * i + 1] << 16) | (msg[off + 4 * i + 2] << 8) | msg[off + 4 * i + 3]) >>> 0;
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + S1 + ch + SHA_K[i] + w[i]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
      H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
    }
    return H.map(function (x) { return x.toString(16).padStart(8, '0'); }).join('');
  }

  /* ------------------------------------------------------------------ configuration */
  const DEFAULTS = {
    // budget
    budget_usdt: 100, risk_per_trade_pct: 1, max_risk_pct_hard: 2, max_position_pct: 25, cash_buffer_pct: 10,
    max_open_positions: 3, fee_pct: 0.1, min_notional_bump: true, stop_limit_gap_pct: 0.2,
    // scanner
    min_score: 75, min_quote_volume: 5000000, max_spread_pct: 0.15, min_atr_pct: 0.15, max_atr_pct: 2.5,
    stop_atr_mult: 2, min_stop_pct: 1, btc_filter: true, blocked: [], exclude_bstocks: true, bstock_allow: [], scan_top: 30,
    // risk + ticket
    quote_assets: ['USDT'], min_reward_risk: 1.4, max_order_age_minutes: 30, max_price_drift_pct: 0.5,
    daily_loss_limit_pct: 3, allow_market_orders: false, approval_ttl_minutes: 15,
    // rebalance
    rebalance_band_pct: 5, min_trade_usdt: 10
  };
  function mergeConfig(user) {
    const cfg = clone(DEFAULTS);
    Object.keys(user || {}).forEach(function (k) { if (k[0] !== '_' && user[k] !== undefined && user[k] !== null) cfg[k] = user[k]; });
    return cfg;
  }

  /* ------------------------------------------------------------------ Binance public API client */
  class ApiError extends Error {}
  const BASES = ['https://data-api.binance.vision', 'https://api.binance.com'];
  function makeApi(fetchImpl, bases, timeoutMs) {
    bases = bases || BASES;
    timeoutMs = timeoutMs || 20000;
    async function getJson(path, params) {
      const qs = new URLSearchParams();
      Object.keys(params || {}).forEach(function (k) { qs.append(k, String(params[k])); });
      const q = qs.toString();
      let last = null;
      for (const base of bases) {
        const url = base + path + (q ? '?' + q : '');
        const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
        const timer = ctl ? setTimeout(function () { ctl.abort(); }, timeoutMs) : null;
        try {
          const res = await fetchImpl(url, ctl ? { signal: ctl.signal } : {});
          if (res.status === 418 || res.status === 429) throw new ApiError('Binance rate limit (HTTP ' + res.status + '). Wait a minute and use fewer symbols.');
          if (res.status === 400) throw new ApiError('Binance rejected the request ' + path);
          if (!res.ok) { last = 'HTTP ' + res.status + ' from ' + base; continue; }
          return await res.json();
        } catch (e) {
          if (e instanceof ApiError) throw e;
          last = base + ': ' + (e && e.message ? e.message : e);
        } finally { if (timer) clearTimeout(timer); }
      }
      throw new ApiError('Binance public API unreachable (' + last + ').');
    }
    return { getJson: getJson };
  }
  async function mapPool(items, limit, fn) {
    const out = new Array(items.length);
    let next = 0, stop = null;
    const worker = async function () {
      for (;;) {
        if (stop) return;
        const k = next++;
        if (k >= items.length) return;
        try { out[k] = await fn(items[k], k); } catch (e) { if (e && e.fatal) { stop = e; return; } out[k] = { __error: e }; }
      }
    };
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    if (stop) throw stop;
    return out;
  }

  /* ------------------------------------------------------------------ indicators */
  function ema(values, period) {
    if (period < 1 || values.length < period) return [];
    const k = 2 / (period + 1);
    let e = sum(values.slice(0, period)) / period;
    const out = [e];
    for (let i = period; i < values.length; i++) { e = values[i] * k + e * (1 - k); out.push(e); }
    return out;
  }
  function rsi(closes, period) {
    period = period || 14;
    if (closes.length <= period) return null;
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) { const d = closes[i] - closes[i - 1]; gain += Math.max(d, 0); loss += Math.max(-d, 0); }
    let ag = gain / period, al = loss / period;
    for (let i = period + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      ag = (ag * (period - 1) + Math.max(d, 0)) / period;
      al = (al * (period - 1) + Math.max(-d, 0)) / period;
    }
    if (al === 0) return ag > 0 ? 100 : 50;
    return 100 - 100 / (1 + ag / al);
  }
  function atr(candles, period) {
    period = period || 14;
    if (candles.length <= period) return null;
    const trs = [];
    for (let i = 1; i < candles.length; i++) {
      const h = candles[i].h, l = candles[i].l, pc = candles[i - 1].c;
      trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
    }
    let a = sum(trs.slice(0, period)) / period;
    for (let i = period; i < trs.length; i++) a = (a * (period - 1) + trs[i]) / period;
    return a;
  }
  function parseKlines(raw, now) {
    now = now === undefined ? nowMs() : now;
    const out = raw.map(function (k) {
      return { t: parseInt(k[0], 10), o: parseFloat(k[1]), h: parseFloat(k[2]), l: parseFloat(k[3]), c: parseFloat(k[4]),
        v: parseFloat(k[5]), ct: parseInt(k[6], 10), qv: parseFloat(k[7]) };
    });
    if (out.length && out[out.length - 1].ct > now) out.pop();       // the candle still forming
    return out;
  }
  const last = function (a) { return a[a.length - 1]; };

  /* ------------------------------------------------------------------ 1. scanner */
  const STABLES = new Set(['USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'BUSD', 'USDD', 'USD1', 'USDE', 'USDS', 'USDG', 'RLUSD', 'PYUSD',
    'BFUSD', 'AEUR', 'EURI', 'EUR', 'GBP', 'TRY', 'BRL', 'ARS', 'PLN', 'RON', 'UAH', 'ZAR', 'MXN', 'CZK', 'JPY', 'XUSD']);
  const BSTOCK_KEEP = new Set(['BNB', 'SHIB', 'TRB', 'CKB', 'BTCB']);   // real coins whose ticker happens to end in B
  function isStableBase(b) { return STABLES.has(b) || /USD$/.test(b) || /^USD[A-Z0-9]$/.test(b); }
  function isBStock(b, cfg) {
    if (BSTOCK_KEEP.has(b) || ((cfg && cfg.bstock_allow) || []).map(function (x) { return String(x).toUpperCase(); }).includes(b)) return false;
    return /^[A-Z]{2,5}B$/.test(b);
  }

  function analyse(c4, c1, c15, cfg) {
    if (Math.min(c4.length, c1.length, c15.length) < 55) return null;
    const cl4 = c4.map(function (c) { return c.c; }), cl1 = c1.map(function (c) { return c.c; }), cl15 = c15.map(function (c) { return c.c; });
    const close4 = last(cl4), close1 = last(cl1), close15 = last(cl15);
    const e20_4 = last(ema(cl4, 20)), e50_4 = last(ema(cl4, 50));
    const e20_1 = last(ema(cl1, 20)), e50_1 = last(ema(cl1, 50));
    const e20_15 = last(ema(cl15, 20)), e50_15 = last(ema(cl15, 50));
    const rsi1 = rsi(cl1), rsi15 = rsi(cl15), atr15 = atr(c15);

    let trend = 0;
    if (close4 > e50_4 && e20_4 > e50_4) trend = 25; else if (close4 > e50_4) trend = 12;

    let confirm = 0;
    if (close1 > e20_1 && e20_1 > e50_1) confirm = 12; else if (close1 > e50_1) confirm = 6;
    if (rsi1 !== null) {
      if (rsi1 >= 50 && rsi1 <= 70) confirm += 8;
      else if ((rsi1 >= 45 && rsi1 < 50) || (rsi1 > 70 && rsi1 <= 75)) confirm += 4;
    }

    const priorHigh = Math.max.apply(null, c15.slice(-21, -1).map(function (c) { return c.h; }));
    const touched = c15.slice(-3).some(function (c) { return c.l <= e20_15 * 1.003; });
    let entry, setup;
    if (close15 > priorHigh) { entry = 25; setup = 'breakout'; }
    else if (e20_15 > e50_15 && touched && e20_15 < close15 && close15 <= e20_15 * 1.01) { entry = 18; setup = 'retest'; }
    else if (close15 >= priorHigh * 0.997) { entry = 8; setup = 'near-breakout'; }
    else { entry = 0; setup = 'none'; }

    const avgVol = sum(c15.slice(-21, -1).map(function (c) { return c.v; })) / 20;
    const relVol = avgVol > 0 ? last(c15).v / avgVol : 0;
    const volume = relVol >= 2 ? 15 : relVol >= 1.5 ? 10 : relVol >= 1 ? 5 : 0;
    const heat = (rsi15 !== null && rsi15 <= 70) ? 15 : (rsi15 !== null && rsi15 <= 78) ? 7 : 0;

    const atrPct = (atr15 && close15) ? atr15 / close15 * 100 : 0;
    const dist = Math.max(cfg.stop_atr_mult * (atr15 || 0), close15 * cfg.min_stop_pct / 100);
    return {
      score: trend + confirm + entry + volume + heat,
      breakdown: { trend: trend, confirm: confirm, entry: entry, volume: volume, heat: heat },
      setup: setup, price: close15, entry: close15, stop: close15 - dist,
      take_profit: close15 + 2 * dist, take_profit_2: close15 + 3 * dist,
      stop_pct: dist / close15 * 100, atr15_pct: atrPct, rsi15: rsi15, rsi1h: rsi1, rel_volume: relVol,
      trend4h_ok: close4 > e50_4
    };
  }
  function btcState(c1) {
    if (c1.length < 55) return { ok: true, known: false };
    const closes = c1.map(function (c) { return c.c; });
    const e50 = last(ema(closes, 50));
    return { ok: last(closes) > e50, known: true, close: last(closes), ema50_1h: e50 };
  }
  function evaluate(sym, ticker, book, c4, c1, c15, cfg, btcOk) {
    const a = analyse(c4, c1, c15, cfg);
    if (a === null) return null;
    book = book || {};
    const bid = num(book.bidPrice), ask = num(book.askPrice);
    const spread = (bid > 0 && ask > 0) ? (ask - bid) / ((ask + bid) / 2) * 100 : null;
    const qv = num(ticker.quoteVolume);
    const fails = [];
    if (!a.trend4h_ok) fails.push('4h close is below EMA50 (no longs against the 4h trend)');
    if (spread === null) fails.push('no order book data');
    else if (spread > cfg.max_spread_pct) fails.push('spread ' + spread.toFixed(3) + '% is wider than ' + cfg.max_spread_pct.toFixed(2) + '%');
    if (!(cfg.min_atr_pct <= a.atr15_pct && a.atr15_pct <= cfg.max_atr_pct))
      fails.push('15m ATR ' + a.atr15_pct.toFixed(2) + '% is outside ' + cfg.min_atr_pct.toFixed(2) + '-' + cfg.max_atr_pct.toFixed(2) + '%');
    if (qv < cfg.min_quote_volume) fails.push('24h volume ' + qv.toFixed(0) + ' USDT is under ' + cfg.min_quote_volume.toFixed(0));
    if (cfg.btc_filter && !btcOk) fails.push('BTC is under its 1h EMA50 (market filter)');
    if (a.score < cfg.min_score) fails.push('score ' + a.score + ' is under ' + cfg.min_score);
    a.symbol = sym; a.qualified = fails.length === 0; a.why_not = fails; a.spread_pct = spread;
    a.quote_volume_24h = qv; a.change_24h_pct = num(ticker.priceChangePercent);
    return a;
  }
  /** Rank the USDT pairs from the 24h tickers (cheap) before any per-symbol request. */
  function pickFromTickers(tickers, cfg, symbols) {
    const blocked = new Set((cfg.blocked || []).map(function (b) { return String(b).toUpperCase(); }));
    const skipped = { stable: 0, bstock: 0, blocked: 0 };
    let rows = [];
    tickers.forEach(function (t) {
      const s = t.symbol || '';
      if (!s.endsWith('USDT') || num(t.lastPrice) <= 0) return;
      const base = s.slice(0, -4);
      if (!base) return;
      if (isStableBase(base)) { skipped.stable++; return; }
      if (blocked.has(base)) { skipped.blocked++; return; }
      if (cfg.exclude_bstocks && isBStock(base, cfg)) { skipped.bstock++; return; }
      rows.push(t);
    });
    if (symbols && symbols.length) {
      const want = symbols.map(function (x) { return x.toUpperCase(); });
      rows = rows.filter(function (t) { return want.includes(t.symbol); });
    } else {
      rows = rows.filter(function (t) { return num(t.quoteVolume) >= cfg.min_quote_volume; });
    }
    rows.sort(function (a, b) { return num(b.quoteVolume) - num(a.quoteVolume); });
    return { rows: rows, skipped: skipped };
  }
  async function runScan(api, cfg, opts) {
    opts = opts || {};
    const symbols = opts.symbols || null;
    const top = opts.top || cfg.scan_top || 30;
    const conc = opts.concurrency || 6;
    const progress = opts.onProgress || function () {};
    const now = nowMs();
    progress({ stage: 'tickers' });
    const tickers = await api.getJson('/api/v3/ticker/24hr');
    const picked = pickFromTickers(tickers, cfg, symbols);
    let pool = picked.rows.slice(0, symbols ? picked.rows.length : Math.min(top * 2, 100));
    progress({ stage: 'rules', n: pool.length });
    const info = pool.length ? await api.getJson('/api/v3/exchangeInfo', { symbols: JSON.stringify(pool.map(function (t) { return t.symbol; })) }) : { symbols: [] };
    const tradable = new Set((info.symbols || []).filter(function (s) {
      return s.status === 'TRADING' && s.quoteAsset === 'USDT' && s.isSpotTradingAllowed !== false;
    }).map(function (s) { return s.symbol; }));
    const universe = pool.filter(function (t) { return tradable.has(t.symbol); }).slice(0, symbols ? undefined : top);
    const books = {};
    if (universe.length) {
      const rows = await api.getJson('/api/v3/ticker/bookTicker', { symbols: JSON.stringify(universe.map(function (t) { return t.symbol; })) });
      rows.forEach(function (b) { books[b.symbol] = b; });
    }
    progress({ stage: 'btc' });
    const btc = btcState(parseKlines(await api.getJson('/api/v3/klines', { symbol: 'BTCUSDT', interval: '1h', limit: 100 }), now));
    const btcOk = opts.ignoreBtc ? true : btc.ok;
    let done = 0;
    const out = await mapPool(universe, conc, async function (t) {
      const sym = t.symbol;
      let frames;
      try {
        frames = await Promise.all(['4h', '1h', '15m'].map(function (tf) {
          return api.getJson('/api/v3/klines', { symbol: sym, interval: tf, limit: 100 }).then(function (r) { return parseKlines(r, now); });
        }));
      } catch (e) {
        if (/rate limit/.test(String(e.message))) { e.fatal = true; throw e; }
        done++; progress({ stage: 'symbols', done: done, total: universe.length });
        return { error: { symbol: sym, error: String(e.message) } };
      }
      done++; progress({ stage: 'symbols', done: done, total: universe.length });
      const item = evaluate(sym, t, books[sym], frames[0], frames[1], frames[2], cfg, btcOk);
      return item === null ? { error: { symbol: sym, error: 'not enough candle history' } } : { item: item };
    });
    const results = [], errors = [];
    out.forEach(function (o) {
      if (!o) return;
      if (o.__error) errors.push({ symbol: '?', error: String(o.__error.message) });
      else if (o.error) errors.push(o.error);
      else results.push(o.item);
    });
    results.sort(function (a, b) { return b.score - a.score; });
    return {
      generated_at: isoNow(),
      market: { btc: btc, btc_filter_applied: !!(cfg.btc_filter && !opts.ignoreBtc) },
      params: { min_score: cfg.min_score, min_quote_volume: cfg.min_quote_volume, max_spread_pct: cfg.max_spread_pct,
        min_atr_pct: cfg.min_atr_pct, max_atr_pct: cfg.max_atr_pct, stop_atr_mult: cfg.stop_atr_mult, min_stop_pct: cfg.min_stop_pct, btc_filter: cfg.btc_filter },
      scanned: universe.length, excluded: picked.skipped,
      candidates: results.filter(function (r) { return r.qualified; }),
      watch: results.filter(function (r) { return !r.qualified; }).slice(0, 10),
      errors: errors
    };
  }

  /* ------------------------------------------------------------------ 2. analyst */
  function pivots(candles, wing) {
    wing = wing || 3;
    const highs = [], lows = [];
    for (let i = wing; i < candles.length - wing; i++) {
      const win = candles.slice(i - wing, i + wing + 1);
      if (candles[i].h === Math.max.apply(null, win.map(function (c) { return c.h; }))) highs.push(candles[i].h);
      if (candles[i].l === Math.min.apply(null, win.map(function (c) { return c.l; }))) lows.push(candles[i].l);
    }
    return { highs: highs, lows: lows };
  }
  function frame(candles) {
    const closes = candles.map(function (c) { return c.c; });
    if (closes.length < 55) return null;
    const e20 = last(ema(closes, 20)), e50 = last(ema(closes, 50)), close = last(closes);
    const trend = (close > e50 && e20 > e50) ? 'up' : (close < e50 && e20 < e50) ? 'down' : 'mixed';
    const a = atr(candles);
    return { close: close, ema20: e20, ema50: e50, rsi: rsi(closes), trend: trend, atr: a, atr_pct: a ? a / close * 100 : null };
  }
  function normalise(symbol) { const s = String(symbol).toUpperCase().trim(); return s.endsWith('USDT') ? s : s + 'USDT'; }
  async function analyseSymbol(api, symbol, now) {
    const sym = normalise(symbol);
    now = now === undefined ? nowMs() : now;
    const get = function (tf, limit) { return api.getJson('/api/v3/klines', { symbol: sym, interval: tf, limit: limit }).then(function (r) { return parseKlines(r, now); }); };
    const all = await Promise.all([get('4h', 120), get('1h', 200), get('15m', 120),
      api.getJson('/api/v3/ticker/24hr', { symbol: sym }), api.getJson('/api/v3/ticker/bookTicker', { symbol: sym })]);
    const k4 = all[0], k1 = all[1], k15 = all[2], tick = all[3], book = all[4];
    const f4 = frame(k4), f1 = frame(k1), f15 = frame(k15);
    if (!(f4 && f1 && f15)) throw new ApiError(sym + ' has too little candle history to analyse');
    const price = f15.close;
    const pv = pivots(k1.slice(-120));
    const r8 = function (x) { return Number(x.toFixed(8)); };
    const uniq = function (a) { return Array.from(new Set(a)); };
    const resist = uniq(pv.highs.filter(function (h) { return h > price; }).map(r8)).sort(function (a, b) { return a - b; }).slice(0, 3);
    const support = uniq(pv.lows.filter(function (l) { return l < price; }).map(r8)).sort(function (a, b) { return b - a; }).slice(0, 3);
    const bid = num(book.bidPrice), ask = num(book.askPrice);
    const spread = (bid > 0 && ask > 0) ? (ask - bid) / ((ask + bid) / 2) * 100 : null;
    const trends = [f4.trend, f1.trend, f15.trend];
    const ups = trends.filter(function (t) { return t === 'up'; }).length;
    const downs = trends.filter(function (t) { return t === 'down'; }).length;
    const verdict = ups === 3 ? 'BULLISH' : (ups === 2 && downs === 0) ? 'LEANING BULLISH' : downs === 3 ? 'BEARISH' : (downs === 2 && ups === 0) ? 'LEANING BEARISH' : 'MIXED';
    const dist = Math.max(2 * f15.atr, price * 0.01);
    let supStop = null, stopNote = null;
    if (support.length && (price - support[0]) / price <= 0.04) { supStop = support[0] * 0.998; stopNote = 'nearest 1h support ' + support[0] + ', stop just under it'; }
    const vols = k1.map(function (c) { return c.v; });
    const older = vols.length >= 30 ? sum(vols.slice(-30, -6)) : 0;
    const volTrend = (vols.length >= 30 && older > 0) ? (sum(vols.slice(-6)) / 6) / (older / 24) : null;
    const high24 = num(tick.highPrice), low24 = num(tick.lowPrice);
    return {
      symbol: sym, price: price, verdict: verdict, timeframes: { '4h': f4, '1h': f1, '15m': f15 },
      support: support, resistance: resist,
      levels: { atr_stop: price - dist, support_stop: supStop, stop_note: stopNote, target_2r: price + 2 * dist, target_3r: price + 3 * dist, stop_pct: dist / price * 100 },
      stats: { change_24h_pct: num(tick.priceChangePercent), quote_volume_24h: num(tick.quoteVolume), high_24h: high24, low_24h: low24,
        pct_below_24h_high: high24 ? (high24 - price) / high24 * 100 : null, volume_trend_1h: volTrend, spread_pct: spread },
      series: k1.slice(-72).map(function (c) { return c.c; })
    };
  }

  /* ------------------------------------------------------------------ 3. budget (position sizing) */
  function parseFilters(s) {
    const f = {};
    (s.filters || []).forEach(function (x) { f[x.filterType] = x; });
    const n = f.NOTIONAL || f.MIN_NOTIONAL || {};
    return { tick: (f.PRICE_FILTER || {}).tickSize || '0.00000001', step: (f.LOT_SIZE || {}).stepSize || '0.00000001',
      min_qty: (f.LOT_SIZE || {}).minQty || '0', max_qty: (f.LOT_SIZE || {}).maxQty || '9000000000',
      min_notional: n.minNotional || '5', status: s.status };
  }
  async function getFilters(api, symbols) {
    if (!symbols.length) return {};
    const info = await api.getJson('/api/v3/exchangeInfo', { symbols: JSON.stringify(symbols) });
    const out = {};
    (info.symbols || []).forEach(function (s) { out[s.symbol] = parseFilters(s); });
    return out;
  }
  function planOrder(c, flt, balance, deployable, cfg) {
    if (!flt) return [null, 'no exchange rules found for ' + c.symbol];
    if (flt.status !== 'TRADING') return [null, c.symbol + ' is not trading (' + flt.status + ')'];
    const entry = decNum(floorTo(c.entry, flt.tick)), stop = decNum(floorTo(c.stop, flt.tick)), tp = decNum(floorTo(c.take_profit, flt.tick));
    if (entry <= 0 || stop <= 0 || stop >= entry) return [null, 'stop is not below entry'];
    const fee = cfg.fee_pct / 100;
    const perUnitLoss = (entry - stop) + entry * fee * 2;
    const riskTarget = balance * cfg.risk_per_trade_pct / 100;
    const maxNotional = Math.min(balance * cfg.max_position_pct / 100, deployable);
    if (maxNotional <= 0) return [null, 'no cash left under the cash buffer'];
    let qty = riskTarget / perUnitLoss;
    const notes = [];
    if (qty * entry > maxNotional) { qty = maxNotional / entry; notes.push('capped by position size or cash buffer'); }
    qty = decNum(floorTo(stable(qty), flt.step));
    const minNotional = Number(flt.min_notional), minQty = Number(flt.min_qty);
    if (qty < minQty || qty * entry < minNotional) {
      if (!cfg.min_notional_bump) return [null, 'below Binance minimum order (' + plain(flt.min_notional) + ' USDT)'];
      const need = decNum(ceilTo(stable(Math.max(minNotional * 1.02 / entry, minQty)), flt.step));
      if (need * entry > maxNotional)
        return [null, 'Binance minimum order (' + plain(flt.min_notional) + ' USDT) is above what the position cap (' + cfg.max_position_pct +
          '% of the budget) and the cash buffer allow (' + maxNotional.toFixed(2) + ' USDT): on a small budget, raise max_position_pct'];
      const riskIfBumped = need * perUnitLoss / balance * 100;
      if (riskIfBumped > cfg.max_risk_pct_hard)
        return [null, 'stop is too wide for this balance: the minimum order would risk ' + riskIfBumped.toFixed(2) + '% (limit ' + cfg.max_risk_pct_hard + '%)'];
      qty = need; notes.push("rounded up to Binance's minimum order value");
    }
    if (qty > Number(flt.max_qty)) return [null, 'above Binance max quantity'];
    const notional = qty * entry, risk = qty * perUnitLoss, fees = notional * fee * 2;
    const gain = qty * (tp - entry) - fees;
    const stopLimit = decNum(floorTo(stop * (1 - cfg.stop_limit_gap_pct / 100), flt.tick));
    const order = {
      id: c.symbol + '-' + Math.floor(nowMs() / 1000), kind: 'trade', market: 'SPOT', symbol: c.symbol, side: 'BUY', type: 'LIMIT', time_in_force: 'GTC',
      price: entry, quantity: qty, price_str: plain(floorTo(c.entry, flt.tick)), quantity_str: plain(floorTo(stable(qty), flt.step)),
      notional: notional, stop_price: stop, stop_limit_price: stopLimit, take_profit: tp,
      take_profit_2: decNum(floorTo(c.take_profit_2 !== undefined ? c.take_profit_2 : c.take_profit, flt.tick)),
      risk_usdt: risk, risk_pct: risk / balance * 100, reward_risk: risk > 0 ? gain / risk : 0, fees_est: fees,
      min_notional: minNotional, score: c.score === undefined ? null : c.score, setup: c.setup === undefined ? null : c.setup,
      created_at: isoNow(), status: 'proposed', notes: notes
    };
    return [order, null];
  }
  function openExposure(journal) {
    const pos = (journal && journal.open) || [];
    return { exposure: sum(pos.map(function (p) { return Number(p.qty) * Number(p.entry); })), n: pos.length, held: new Set(pos.map(function (p) { return p.symbol; })) };
  }
  function buildPlan(cands, filters, balance, cfg, journal, top) {
    top = top || 5;
    const ex = openExposure(journal);
    let deployable = balance * (1 - cfg.cash_buffer_pct / 100) - ex.exposure;
    const slots = Math.max(cfg.max_open_positions - ex.n, 0);
    const orders = [], skipped = [];
    cands.slice(0, top).forEach(function (c) {
      if (ex.held.has(c.symbol)) { skipped.push({ symbol: c.symbol, reason: 'already an open position' }); return; }
      if (orders.length >= slots) { skipped.push({ symbol: c.symbol, reason: 'max_open_positions (' + cfg.max_open_positions + ') reached' }); return; }
      const r = planOrder(c, filters[c.symbol], balance, deployable, cfg);
      if (r[0] === null) { skipped.push({ symbol: c.symbol, reason: r[1] }); return; }
      deployable -= r[0].notional; orders.push(r[0]);
    });
    return { generated_at: isoNow(), balance: balance, cash_after: balance - ex.exposure - sum(orders.map(function (o) { return o.notional; })), orders: orders, skipped: skipped };
  }

  /* ------------------------------------------------------------------ 4. risk gate */
  const FORBIDDEN_KEYS = ['leverage', 'margin', 'isolated', 'borrow', 'futures', 'reduce_only', 'is_isolated'];
  const FINAL = new Set(['placed', 'cancelled', 'closed']);
  function fingerprint(o) {
    const keys = ['kind', 'symbol', 'side', 'type', 'price', 'quantity', 'stop_price', 'take_profit'];
    const blob = JSON.stringify(keys.map(function (k) { return o[k] === undefined ? null : o[k]; }));
    return sha256hex(blob).slice(0, 16);
  }
  function baseAsset(symbol, quotes) {
    for (const q of quotes) if (symbol.endsWith(q)) return symbol.slice(0, -q.length);
    return symbol;
  }
  function realizedToday(journal) {
    const today = new Date(nowMs()).toISOString().slice(0, 10);
    return sum(((journal && journal.closed) || []).filter(function (t) { return String(t.closed_at || '').startsWith(today); }).map(function (t) { return num(t.pnl); }));
  }
  function checkOrder(o, cfg, ctx, live) {
    const out = [];
    const add = function (name, ok, detail) { out.push({ name: name, ok: !!ok, detail: detail || '' }); };
    const balance = Number(cfg.budget_usdt);
    const kind = o.kind || 'trade';
    const side = String(o.side || '').toUpperCase();
    let price, qty, sym, wellFormed;
    try {
      price = Number(o.price); qty = Number(o.quantity); sym = String(o.symbol).toUpperCase();
      if (o.price === undefined || o.quantity === undefined || o.symbol === undefined || !Number.isFinite(price) || !Number.isFinite(qty)) throw new Error('bad');
      wellFormed = price > 0 && qty > 0 && (side === 'BUY' || side === 'SELL');
    } catch (e) {
      add('format', false, 'symbol, side, price and quantity are required and must be positive numbers');
      return out;
    }
    add('format', wellFormed, wellFormed ? '' : 'side must be BUY or SELL, price and quantity above zero');
    if (!wellFormed) return out;
    const notional = price * qty;
    const keysLower = Object.keys(o).map(function (k) { return k.toLowerCase(); });
    const bad = FORBIDDEN_KEYS.filter(function (k) { return keysLower.includes(k); }).sort();
    const spot = String(o.market || 'SPOT').toUpperCase() === 'SPOT' && bad.length === 0;
    add('spot-only', spot, spot ? '' : 'Spot only: no margin, futures or leverage (' + (bad.length ? bad.join(', ') : o.market) + ')');
    const quotes = cfg.quote_assets;
    add('quote', quotes.some(function (q) { return sym.endsWith(q); }), 'pair must be quoted in ' + quotes.join('/'));
    const base = baseAsset(sym, quotes);
    add('not-blocked', !(cfg.blocked || []).map(function (b) { return String(b).toUpperCase(); }).includes(base), base + ' is on the blocked list');
    const typ = String(o.type || '').toUpperCase();
    add('order-type', typ === 'LIMIT' || (cfg.allow_market_orders && typ === 'MARKET'), 'only LIMIT orders are allowed (set allow_market_orders to change)');
    const age = minutesSince(o.created_at);
    add('fresh', age !== null && age <= Number(cfg.max_order_age_minutes),
      'proposal is ' + (age === null ? '?' : age.toFixed(0) + ' min') + ' old (limit ' + cfg.max_order_age_minutes + ' min): re-run the scanner and sizing');

    if (kind === 'trade' && side === 'BUY') {
      const stop = Number(o.stop_price || 0), tp = Number(o.take_profit || 0);
      add('stop', stop > 0 && stop < price, 'a BUY needs a stop above zero and under the price');
      add('target', tp > price, 'take profit must be above the entry price');
      const rr = Number(o.reward_risk || 0);
      add('reward-risk', rr >= Number(cfg.min_reward_risk), 'reward/risk ' + rr.toFixed(2) + ' is under ' + Number(cfg.min_reward_risk).toFixed(2));
      const risk = Number(o.risk_pct || 0);
      add('risk', risk > 0 && risk <= Number(cfg.max_risk_pct_hard), 'risk ' + risk.toFixed(2) + '% is over the ' + Number(cfg.max_risk_pct_hard).toFixed(2) + '% limit');
      const cap = balance * Number(cfg.max_position_pct) / 100;
      add('position-size', notional <= cap * 1.001, notional.toFixed(2) + ' USDT is over the ' + Number(cfg.max_position_pct).toFixed(0) + '% cap (' + cap.toFixed(2) + ' USDT)');
      add('min-order', notional >= Number(o.min_notional || 0), notional.toFixed(2) + " USDT is under Binance's minimum order");
      const room = balance * (1 - Number(cfg.cash_buffer_pct) / 100) - ctx.exposure;
      add('cash-buffer', notional <= room + 0.01, 'only ' + Math.max(room, 0).toFixed(2) + ' USDT is available above the ' + Number(cfg.cash_buffer_pct).toFixed(0) + '% cash buffer');
      add('not-held', !ctx.symbols.has(sym), sym + ' is already an open position');
      const count = ctx.symbols.size;
      add('max-positions', ctx.symbols.has(sym) || count < parseInt(cfg.max_open_positions, 10),
        count + ' positions are open or approved (limit ' + parseInt(cfg.max_open_positions, 10) + ')');
      const loss = realizedToday(ctx.journal), limit = balance * Number(cfg.daily_loss_limit_pct) / 100;
      add('daily-loss', loss > -limit, "today's realized loss " + (-loss).toFixed(2) + ' USDT hit the daily limit ' + limit.toFixed(2) + ' USDT: no new buys today');
      if (live !== null && live !== undefined) {
        const drift = (live - price) / price * 100;
        add('live-price', live > stop && Math.abs(drift) <= Number(cfg.max_price_drift_pct),
          'market ' + live + ' is ' + (drift >= 0 ? '+' : '') + drift.toFixed(2) + '% from the entry (limit ' + Number(cfg.max_price_drift_pct).toFixed(2) + '%, stop ' + stop + '): the setup has moved, re-scan');
      }
    } else if (side === 'SELL') {
      const held = ctx.holdings[base] || 0;
      add('no-short', qty <= held * 1.0000001, 'selling ' + qty + ' ' + base + ' but only ' + held + ' held (Spot cannot short)');
    } else {
      add('cash', ctx.buy_spent + notional <= ctx.buy_budget + 0.01,
        'buys need ' + (ctx.buy_spent + notional).toFixed(2) + ' USDT but only ' + ctx.buy_budget.toFixed(2) + ' USDT is available (cash plus the sells in this batch)');
    }
    if (out.every(function (c) { return c.ok; })) {
      if (kind === 'trade' && side === 'BUY') { ctx.exposure += notional; ctx.symbols.add(sym); }
      else if (side === 'SELL') ctx.holdings[base] = (ctx.holdings[base] || 0) - qty;
      else ctx.buy_spent += notional;
    }
    return out;
  }
  function makeContext(cfg, journal, portfolio, orders) {
    const openPos = (journal && journal.open) || [];
    const holdings = {};
    ((portfolio && portfolio.balances) || []).forEach(function (b) {
      const a = String(b.asset).toUpperCase();
      holdings[a] = (holdings[a] || 0) + num(b.amount);
    });
    openPos.forEach(function (p) {
      const b = baseAsset(p.symbol, cfg.quote_assets);
      if (!(b in holdings)) holdings[b] = Number(p.qty);
    });
    const usdt = holdings.USDT || 0;
    const sells = sum(orders.filter(function (o) { return String(o.side || '').toUpperCase() === 'SELL' && o.kind === 'rebalance' && !FINAL.has(o.status); })
      .map(function (o) { return Number(o.price) * Number(o.quantity); }));
    return {
      exposure: sum(openPos.map(function (p) { return Number(p.qty) * Number(p.entry); })),
      symbols: new Set(openPos.map(function (p) { return p.symbol; })),
      holdings: holdings, journal: journal, buy_spent: 0, buy_budget: usdt + sells
    };
  }
  /** Check every open proposal. `prices` (symbol -> price) is optional and turns on the live-price check. */
  function runRisk(doc, cfg, journal, portfolio, prices, livePricesWanted) {
    const orders = doc.orders || [];
    const ctx = makeContext(cfg, journal, portfolio, orders);
    const verdicts = [];
    orders.forEach(function (o) {
      if (FINAL.has(o.status)) return;
      const wantsLive = !!livePricesWanted && (o.kind || 'trade') === 'trade';
      const priceNow = wantsLive && prices && prices[o.symbol] !== undefined ? prices[o.symbol] : null;
      const checks = checkOrder(o, cfg, ctx, priceNow);
      if (wantsLive && priceNow === null) checks.push({ name: 'live-price', ok: false, detail: 'could not read the current price' });
      const ok = checks.every(function (c) { return c.ok; });
      o.risk = { verdict: ok ? 'PASS' : 'BLOCK', checked_at: isoNow(), fingerprint: fingerprint(o),
        reasons: checks.filter(function (c) { return !c.ok; }).map(function (c) { return c.detail; }), live: !!livePricesWanted, checks: checks };
      o.status = ok ? 'risk_pass' : 'blocked';
      verdicts.push(o);
    });
    return verdicts;
  }

  /* ------------------------------------------------------------------ 5. ticket (confirmation + manual steps) */
  function readiness(o, cfg) {
    if (FINAL.has(o.status)) return ['DONE', ['already ' + o.status]];
    const why = [];
    const r = o.risk, fp = fingerprint(o);
    if (!r) why.push('not risk-checked yet: run the risk check');
    else if (r.verdict !== 'PASS') why.push('BLOCKED by the risk check: ' + (r.reasons || []).join('; '));
    else if (r.fingerprint !== fp) why.push('the order changed after the risk check: run the risk check again');
    else {
      const age = minutesSince(r.checked_at);
      if (age === null || age > Number(cfg.max_order_age_minutes)) why.push('the risk check is ' + (age === null ? '?' : age.toFixed(0) + ' min') + ' old: run it again with live price');
    }
    const ap = o.approval;
    if (!ap) why.push('no yes recorded for this order');
    else if (ap.fingerprint !== fp) why.push('the order changed after your yes: it needs a new yes');
    else {
      const age = minutesSince(ap.at);
      if (age === null || age > Number(cfg.approval_ttl_minutes)) why.push('your yes is ' + (age === null ? '?' : age.toFixed(0) + ' min') + ' old (valid ' + cfg.approval_ttl_minutes + ' min): confirm again');
    }
    return [why.length ? 'NOT READY' : 'READY', why];
  }
  function escRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  /** Records the user's typed confirmation. Throws when it is not exactly "yes SYMBOL" (or the id). */
  function approve(o, confirm, cfg) {
    const r = o.risk;
    if (!r || r.verdict !== 'PASS' || r.fingerprint !== fingerprint(o)) throw new Error('cannot record a yes: this order has no current PASS from the risk check');
    const want = new RegExp('^\\s*yes\\s+(' + escRe(o.symbol) + '|' + escRe(o.id) + ')\\s*$', 'i');
    if (!want.test(String(confirm))) throw new Error('the confirmation must be you typing "yes ' + o.symbol + '" for this exact order');
    o.approval = { at: isoNow(), fingerprint: fingerprint(o), confirm: String(confirm).trim() };
    o.status = 'approved';
    return o;
  }
  function fmtField(o, key) { return o[key + '_str'] || String(o[key]); }
  function renderSteps(o) {
    const sym = o.symbol, base = sym.endsWith('USDT') ? sym.slice(0, -4) : sym, side = String(o.side).toUpperCase();
    const price = fmtField(o, 'price'), qty = fmtField(o, 'quantity'), total = Number(o.price) * Number(o.quantity);
    const lines = [side + ' ' + sym + ' on Binance Spot (limit order). Place it by hand in the Binance app:', '',
      '1. Open Spot, pair ' + base + '/USDT, choose Limit, tab ' + (side === 'BUY' ? 'Buy' : 'Sell') + '.',
      '2. Price: ' + price + '    Amount: ' + qty + ' ' + base + '    (total about ' + total.toFixed(2) + ' USDT)',
      '3. Confirm the order. It rests on the book until it fills (GTC). If it has not filled when the',
      '   setup no longer looks right, cancel it yourself.'];
    if (side === 'BUY' && (o.kind || 'trade') === 'trade') {
      lines.push('4. Once it has FILLED (not before), protect it: in the order type menu pick OCO and Sell:',
        '     Price (take profit, limit): ' + fmtField(o, 'take_profit'),
        '     Stop (trigger):             ' + fmtField(o, 'stop_price'),
        '     Limit (after the trigger):  ' + o.stop_limit_price,
        '     Amount: ' + qty + ' ' + base,
        '   If the trigger fires on a fast move the fill can be worse than the stop price.',
        '5. Record the real fill price and amount in the journal.', '',
        'If stopped out, the planned loss is about ' + num(o.risk_usdt).toFixed(2) + ' USDT (' + num(o.risk_pct).toFixed(2) + '% of the budget), fees included.');
      if (o.take_profit_2) lines.push('Optional second target (3R): ' + o.take_profit_2 + '. Moving the stop up as price rises is your call.');
    } else lines.push('4. Record it when it has filled, with the real price and amount.');
    return lines.join('\n');
  }
  function markPlaced(o, price, qty) { o.status = 'placed'; o.placed = { at: isoNow(), price: price, qty: qty }; return o; }
  function cancelOrder(o) { o.status = 'cancelled'; return o; }

  /* ------------------------------------------------------------------ 6. journal */
  function normSym(symbol) { const s = String(symbol).toUpperCase().trim(); return s.endsWith('USDT') ? s : s + 'USDT'; }
  function openPosition(j, symbol, price, qty, stop, target, fee, feePct) {
    const sym = normSym(symbol);
    if (j.open.some(function (p) { return p.symbol === sym; })) throw new Error(sym + ' is already open: close it first');
    if (!(price > 0) || !(qty > 0)) throw new Error('price and quantity must be above zero');
    if (stop != null && stop >= price) throw new Error('the stop must be under the entry price for a long');
    const feeUsdt = fee != null ? fee : price * qty * (feePct === undefined ? 0.1 : feePct) / 100;
    const pos = { symbol: sym, entry: price, qty: qty, stop: stop == null ? null : stop, target: target == null ? null : target, fee_in: feeUsdt, opened_at: isoNow() };
    j.open.push(pos);
    return pos;
  }
  function closePosition(j, symbol, price, qty, fee, feePct) {
    const sym = normSym(symbol);
    const pos = j.open.find(function (p) { return p.symbol === sym; });
    if (!pos) throw new Error(sym + ' is not an open position');
    const q = qty != null ? Number(qty) : Number(pos.qty);
    if (!(q > 0) || q > Number(pos.qty) * 1.0000001) throw new Error('quantity must be between 0 and ' + pos.qty);
    const frac = q / Number(pos.qty);
    const feeIn = Number(pos.fee_in) * frac;
    const feeOut = fee != null ? fee : price * q * (feePct === undefined ? 0.1 : feePct) / 100;
    const gross = (price - Number(pos.entry)) * q;
    const pnl = gross - feeIn - feeOut;
    const cost = Number(pos.entry) * q;
    let risk = null;
    if (pos.stop) risk = (Number(pos.entry) - Number(pos.stop)) * q + feeIn * 2;
    const trade = { symbol: sym, entry: pos.entry, exit: price, qty: q, gross: gross, fees: feeIn + feeOut, pnl: pnl,
      pnl_pct: cost ? pnl / cost * 100 : 0, r_multiple: risk ? pnl / risk : null, opened_at: pos.opened_at, closed_at: isoNow() };
    j.closed.push(trade);
    if (frac >= 0.9999999) j.open.splice(j.open.indexOf(pos), 1);
    else { pos.qty = Number(pos.qty) - q; pos.fee_in = Number(pos.fee_in) - feeIn; }
    return trade;
  }
  function journalStats(closed) {
    const n = closed.length;
    if (n === 0) return { trades: 0 };
    const wins = closed.filter(function (t) { return t.pnl > 0; }), losses = closed.filter(function (t) { return t.pnl <= 0; });
    const grossWin = sum(wins.map(function (t) { return t.pnl; })), grossLoss = -sum(losses.map(function (t) { return t.pnl; }));
    let equity = 0, peak = 0, dd = 0;
    closed.slice().sort(function (a, b) { return a.closed_at < b.closed_at ? -1 : a.closed_at > b.closed_at ? 1 : 0; }).forEach(function (t) {
      equity += t.pnl; peak = Math.max(peak, equity); dd = Math.max(dd, peak - equity);
    });
    const rs = closed.filter(function (t) { return t.r_multiple !== null && t.r_multiple !== undefined; }).map(function (t) { return t.r_multiple; });
    return {
      trades: n, win_rate_pct: wins.length / n * 100, total_pnl: sum(closed.map(function (t) { return t.pnl; })),
      total_fees: sum(closed.map(function (t) { return t.fees; })), avg_win: wins.length ? grossWin / wins.length : 0,
      avg_loss: losses.length ? -grossLoss / losses.length : 0, profit_factor: grossLoss > 0 ? grossWin / grossLoss : null,
      expectancy: sum(closed.map(function (t) { return t.pnl; })) / n, avg_r: rs.length ? sum(rs) / rs.length : null, max_drawdown: dd
    };
  }

  /* ------------------------------------------------------------------ 7. alerts (checked while the page is open) */
  function nextAlertId(doc) {
    const nums = doc.alerts.map(function (a) { return /^A\d+$/.test(String(a.id)) ? parseInt(String(a.id).slice(1), 10) : 0; });
    return 'A' + (Math.max.apply(null, [0].concat(nums)) + 1);
  }
  function addAlert(doc, symbol, direction, level, kind, note) {
    if (direction !== 'above' && direction !== 'below') throw new Error('direction must be above or below');
    if (!(Number(level) > 0)) throw new Error('level must be above zero');
    const a = { id: nextAlertId(doc), symbol: normSym(symbol), direction: direction, level: Number(level), kind: kind || 'price',
      note: note || '', created_at: isoNow(), triggered_at: null, notified: false };
    doc.alerts.push(a);
    return a;
  }
  function alertHit(a, price) { return a.direction === 'above' ? price >= a.level : price <= a.level; }
  function alertMessage(a, price) {
    const verb = a.direction === 'above' ? 'rose to' : 'fell to';
    const label = { stop: 'STOP level', target: 'TARGET level', entry: 'ENTRY level' }[a.kind] || 'price alert';
    return a.symbol + ' ' + verb + ' ' + price + ' (alert ' + a.direction + ' ' + a.level + ') - ' + label + (a.note ? ' - ' + a.note : '');
  }
  function checkAlerts(doc, prices) {
    const fired = [];
    doc.alerts.filter(function (a) { return !a.triggered_at; }).forEach(function (a) {
      const p = prices[a.symbol];
      if (p === undefined || !alertHit(a, p)) return;
      a.triggered_at = isoNow(); a.price_at_trigger = p;
      fired.push({ alert: a, price: p, text: alertMessage(a, p) });
    });
    return fired;
  }
  function alertsFromJournal(doc, journal) {
    const have = new Set(doc.alerts.filter(function (a) { return !a.triggered_at; }).map(function (a) { return a.symbol + '|' + a.kind + '|' + a.level; }));
    let made = 0;
    ((journal && journal.open) || []).forEach(function (pos) {
      [['stop', 'below', 'stop'], ['target', 'above', 'target']].forEach(function (k) {
        const lvl = pos[k[2]];
        if (lvl && !have.has(pos.symbol + '|' + k[0] + '|' + Number(lvl))) { addAlert(doc, pos.symbol, k[1], lvl, k[0], 'open position'); made++; }
      });
    });
    return made;
  }
  function alertsFromOrders(doc, orders) {
    let made = 0;
    (orders || []).forEach(function (o) {
      if ((o.status === 'approved' || o.status === 'placed') && o.side === 'BUY' && (o.kind || 'trade') === 'trade') {
        addAlert(doc, o.symbol, 'below', o.price, 'entry', 'limit buy may have filled');
        addAlert(doc, o.symbol, 'below', o.stop_price, 'stop', 'stop level');
        made += 2;
      }
    });
    return made;
  }
  async function getPrices(api, symbols) {
    const list = Array.from(new Set(symbols)).sort();
    if (!list.length) return {};
    const rows = await api.getJson('/api/v3/ticker/price', { symbols: JSON.stringify(list) });
    const out = {};
    rows.forEach(function (r) { out[r.symbol] = Number(r.price); });
    return out;
  }

  /* ------------------------------------------------------------------ 8. rebalance */
  function computeRebalance(portfolio, targets, prices) {
    const held = {};
    (portfolio.balances || []).forEach(function (b) { const a = String(b.asset).toUpperCase(); held[a] = (held[a] || 0) + num(b.amount); });
    const tg = {};
    Object.keys(targets || {}).forEach(function (k) { if (k[0] !== '_') tg[k.toUpperCase()] = Number(targets[k]); });
    const tsum = sum(Object.values(tg));
    if (Math.abs(tsum - 100) > 0.5) throw new Error('targets add up to ' + tsum.toFixed(2) + '%, they must add up to 100');
    const unpriced = [], untargeted = [], values = {};
    Array.from(new Set(Object.keys(held).concat(Object.keys(tg)))).sort().forEach(function (asset) {
      const amt = held[asset] || 0;
      if (asset === 'USDT') values[asset] = amt;
      else if (prices[asset + 'USDT'] !== undefined) values[asset] = amt * prices[asset + 'USDT'];
      else if (asset in tg) unpriced.push(asset);
      else if (amt > 0) untargeted.push(asset);
    });
    Object.keys(values).forEach(function (a) { if (!(a in tg)) { untargeted.push(a); delete values[a]; } });
    const total = sum(Object.values(values));
    const rows = [];
    Object.keys(tg).sort().forEach(function (asset) {
      if (unpriced.includes(asset)) return;
      const v = values[asset] || 0, w = total > 0 ? v / total * 100 : 0;
      rows.push({ asset: asset, amount: held[asset] || 0, value: v, weight: w, target: tg[asset], drift: w - tg[asset],
        trade_usdt: tg[asset] / 100 * total - v, price: prices[asset + 'USDT'] });
    });
    return { rows: rows, unpriced: unpriced, untargeted: Array.from(new Set(untargeted)).sort(), total: total };
  }
  function rebalanceBreach(rows, band) { return rows.some(function (r) { return Math.abs(r.drift) > band; }); }
  function makeRebalanceOrders(rows, filters, minTrade, feePct) {
    feePct = feePct === undefined ? 0.1 : feePct;
    const orders = [], skipped = [];
    const ts = Math.floor(nowMs() / 1000);
    rows.slice().sort(function (a, b) { return a.trade_usdt - b.trade_usdt; }).forEach(function (r) {
      if (r.asset === 'USDT') return;
      if (Math.abs(r.trade_usdt) < minTrade) { skipped.push({ asset: r.asset, reason: 'trade of ' + Math.abs(r.trade_usdt).toFixed(2) + ' USDT is under min_trade_usdt' }); return; }
      const sym = r.asset + 'USDT', flt = filters[sym];
      if (!flt || flt.status !== 'TRADING') { skipped.push({ asset: r.asset, reason: sym + ' is not tradable on Spot right now' }); return; }
      const side = r.trade_usdt > 0 ? 'BUY' : 'SELL';
      const priceD = floorTo(r.price, flt.tick);
      let qty = decNum(floorTo(stable(Math.abs(r.trade_usdt) / r.price), flt.step));
      if (side === 'SELL') qty = Math.min(qty, decNum(floorTo(stable(r.amount), flt.step)));
      const price = decNum(priceD);
      if (qty < Number(flt.min_qty) || qty * price < Number(flt.min_notional)) { skipped.push({ asset: r.asset, reason: "below Binance's minimum order (" + flt.min_notional + ' USDT)' }); return; }
      orders.push({ id: sym + '-RB-' + ts, kind: 'rebalance', market: 'SPOT', symbol: sym, side: side, type: 'LIMIT', time_in_force: 'GTC',
        price: price, quantity: qty, price_str: decStr(priceD), quantity_str: decStr(floorTo(stable(qty), flt.step)), notional: qty * price,
        fees_est: qty * price * feePct / 100,
        reason: r.asset + ' is ' + (r.drift >= 0 ? '+' : '') + r.drift.toFixed(1) + ' points off its ' + r.target.toFixed(0) + '% target',
        created_at: isoNow(), status: 'proposed', notes: [] });
    });
    return { orders: orders, skipped: skipped };
  }

  /* ------------------------------------------------------------------ 9. news */
  const NAMES = { BTC: ['bitcoin'], ETH: ['ethereum', 'ether'], SOL: ['solana'], BNB: ['bnb chain', 'binance coin'], XRP: ['ripple'], ADA: ['cardano'],
    DOGE: ['dogecoin'], AVAX: ['avalanche'], LINK: ['chainlink'], DOT: ['polkadot'], TRX: ['tron'], TON: ['toncoin'], MATIC: ['polygon'], POL: ['polygon'],
    LTC: ['litecoin'], SHIB: ['shiba inu'], UNI: ['uniswap'], NEAR: ['near protocol'], ATOM: ['cosmos'], ARB: ['arbitrum'], OP: ['optimism'],
    SUI: ['sui network'], APT: ['aptos'], PEPE: ['pepe coin'] };
  const TAGS = [
    ['security', true, /\b(hack(ed|s)?|exploit(ed|s)?|drain(ed)?|stolen|vulnerabilit\w+|breach|rug ?pull)\b/],
    ['delisting', true, /\b(delist\w*|removal of|monitoring tag|seed tag)\b/],
    ['halt', true, /\b(suspend\w*|halt\w*|maintenance|network (upgrade|issue)|withdrawals? paused)\b/],
    ['listing', false, /\b(will list|new listing|lists?|listing|launchpool|launchpad)\b/],
    ['regulation', false, /\b(sec|cftc|lawsuit|sues?|ban(s|ned)?|regulat\w+|etf|court|approval|approves?)\b/],
    ['upgrade', false, /\b(upgrade|hard fork|mainnet|halving|testnet)\b/],
    ['unlock', false, /\b(token unlock|unlocks?|vesting)\b/]
  ];
  const ANNOUNCE_LINK = 'https://www.binance.com/en/support/announcement/';
  const NEWS_SOURCES = {
    announce: [['Binance listing', 'https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=48'],
      ['Binance delisting', 'https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20&catalogId=161']],
    // RSS feeds do not allow browsers to read them directly, so they go through the public rss2json.com converter.
    rss: [['CoinDesk', 'https://www.coindesk.com/arc/outboundfeeds/rss'], ['Cointelegraph', 'https://cointelegraph.com/rss'], ['Decrypt', 'https://decrypt.co/feed']]
  };
  function classify(title) {
    const tags = []; let high = false; const low = title.toLowerCase();
    TAGS.forEach(function (t) { if (t[2].test(low)) { tags.push(t[0]); high = high || t[1]; } });
    return { tags: tags, high: high };
  }
  function coinsIn(title, coins) {
    const hit = [], low = title.toLowerCase();
    coins.forEach(function (c) {
      c = c.toUpperCase();
      if (new RegExp('(^|[^A-Za-z0-9])' + escRe(c) + '(?![A-Za-z0-9])').test(title)) hit.push(c);
      else if ((NAMES[c] || []).some(function (n) { return new RegExp('\\b' + escRe(n) + '\\b').test(low); })) hit.push(c);
    });
    return hit;
  }
  function parseAnnouncements(data, source) {
    const found = [];
    (function walk(x) {
      if (Array.isArray(x)) x.forEach(walk);
      else if (x && typeof x === 'object') {
        if ('title' in x && ('code' in x || 'releaseDate' in x)) found.push(x);
        Object.keys(x).forEach(function (k) { walk(x[k]); });
      }
    })(data);
    return found.map(function (a) {
      return { title: String(a.title).trim(), link: ANNOUNCE_LINK + String(a.code === undefined ? '' : a.code),
        when: a.releaseDate ? new Date(Number(a.releaseDate)).getTime() : null, source: source };
    });
  }
  function parseRss2json(data, source) {
    if (!data || data.status !== 'ok' || !Array.isArray(data.items)) throw new Error('feed could not be converted');
    return data.items.filter(function (i) { return i.title; }).map(function (i) {
      const raw = String(i.pubDate || '').trim();
      const when = raw ? parseTime(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(raw) ? raw.replace(' ', 'T') + 'Z' : raw) : null;
      return { title: String(i.title).trim(), link: String(i.link || ''), when: when, source: source };
    });
  }
  async function collectNews(fetchJson, coins, hours, market, sources) {
    sources = sources || NEWS_SOURCES;
    const since = nowMs() - hours * 3600000;
    let items = []; const problems = [];
    await Promise.all(sources.announce.map(async function (s) {
      try { items = items.concat(parseAnnouncements(await fetchJson(s[1]), s[0])); } catch (e) { problems.push(s[0] + ': ' + (e && e.message) + ' (unofficial endpoint)'); }
    }));
    await Promise.all(sources.rss.map(async function (s) {
      try { items = items.concat(parseRss2json(await fetchJson('https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(s[1])), s[0])); }
      catch (e) { problems.push(s[0] + ': ' + (e && e.message)); }
    }));
    const out = [], seen = new Set();
    items.forEach(function (it) {
      if (it.when === null || it.when < since) return;
      const key = it.title.toLowerCase().replace(/\W+/g, ' ').trim();
      if (seen.has(key)) return;
      seen.add(key);
      const c = classify(it.title);
      const matched = coins && coins.length ? coinsIn(it.title, coins) : [];
      if (!market && coins && coins.length && !matched.length) return;
      out.push(Object.assign({}, it, { tags: c.tags, high: c.high, coins: matched, age_hours: (nowMs() - it.when) / 3600000 }));
    });
    out.sort(function (a, b) { return (a.high === b.high ? 0 : a.high ? -1 : 1) || a.age_hours - b.age_hours; });
    return { items: out, problems: problems, total_sources: sources.announce.length + sources.rss.length };
  }

  /* ------------------------------------------------------------------ cloud storage (private GitHub repo) */
  function b64encode(str) {
    const bytes = new TextEncoder().encode(str);
    let bin = '';
    bytes.forEach(function (b) { bin += String.fromCharCode(b); });
    return btoa(bin);
  }
  function b64decode(b64) {
    const bin = atob(String(b64).replace(/\s/g, ''));
    return new TextDecoder().decode(Uint8Array.from(bin, function (c) { return c.charCodeAt(0); }));
  }
  function emptyState() {
    return { v: 1, config: {}, journal: { open: [], closed: [] }, alerts: { alerts: [] }, orders: { orders: [], skipped: [] },
      portfolio: { balances: [] }, targets: {}, updated_at: null };
  }
  function normaliseState(s) {
    const e = emptyState();
    s = s && typeof s === 'object' ? s : {};
    return {
      v: 1, config: s.config && typeof s.config === 'object' ? s.config : e.config,
      journal: { open: (s.journal && s.journal.open) || [], closed: (s.journal && s.journal.closed) || [] },
      alerts: { alerts: (s.alerts && s.alerts.alerts) || [] },
      orders: { orders: (s.orders && s.orders.orders) || [], skipped: (s.orders && s.orders.skipped) || [] },
      portfolio: { balances: (s.portfolio && s.portfolio.balances) || [] },
      targets: s.targets && typeof s.targets === 'object' ? s.targets : {}, updated_at: s.updated_at || null
    };
  }
  /** Stores the whole state as one JSON file in a (private) GitHub repository through the contents API. */
  function GithubStore(opts) {
    const f = opts.fetchImpl, repo = opts.repo, path = opts.path || 'binance-dashboard-data.json', branch = opts.branch || '', token = opts.token;
    let sha = null;
    const url = function (p) { return 'https://api.github.com/repos/' + repo + p; };
    const headers = function (extra) { return Object.assign({ Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' }, extra || {}); };
    const fail = function (res, what) {
      const e = new Error(what + ' (HTTP ' + res.status + ')');
      e.status = res.status; return e;
    };
    return {
      async check() {
        const res = await f(url(''), { headers: headers() });
        if (res.status === 401) throw new Error('The token was rejected (HTTP 401): check it and its expiry date.');
        if (res.status === 404) throw new Error('Repository not found, or the token has no access to it (HTTP 404).');
        if (!res.ok) throw fail(res, 'GitHub error');
        const j = await res.json();
        return { private: j.private === true, default_branch: j.default_branch, permissions: j.permissions || null };
      },
      async load() {
        const res = await f(url('/contents/' + encodeURIComponent(path) + (branch ? '?ref=' + encodeURIComponent(branch) : '')), { headers: headers() });
        if (res.status === 404) { sha = null; return null; }
        if (!res.ok) throw fail(res, 'Could not read the cloud copy');
        const j = await res.json();
        sha = j.sha;
        return normaliseState(JSON.parse(b64decode(j.content)));
      },
      async save(state) {
        const body = { message: 'dashboard data ' + isoNow(), content: b64encode(JSON.stringify(state, null, 1)) };
        if (sha) body.sha = sha;
        if (branch) body.branch = branch;
        const res = await f(url('/contents/' + encodeURIComponent(path)), { method: 'PUT', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
        if (res.status === 409 || res.status === 422) { const e = new Error('The cloud copy changed on another device'); e.conflict = true; throw e; }
        if (res.status === 403 || res.status === 401) throw new Error('GitHub refused the write (HTTP ' + res.status + '): the token needs "Contents: read and write" on this repository.');
        if (!res.ok) throw fail(res, 'Could not save to the cloud');
        sha = (await res.json()).content.sha;
        return true;
      },
      forgetSha() { sha = null; }
    };
  }

  return {
    DEFAULTS: DEFAULTS, mergeConfig: mergeConfig, setClock: setClock, nowMs: nowMs, isoNow: isoNow, minutesSince: minutesSince, clone: clone,
    ApiError: ApiError, makeApi: makeApi, mapPool: mapPool, sha256hex: sha256hex,
    decParse: decParse, decStr: decStr, decNum: decNum, floorTo: floorTo, ceilTo: ceilTo, plain: plain,
    ema: ema, rsi: rsi, atr: atr, parseKlines: parseKlines,
    isStableBase: isStableBase, isBStock: isBStock, analyse: analyse, btcState: btcState, evaluate: evaluate, pickFromTickers: pickFromTickers, runScan: runScan,
    pivots: pivots, frame: frame, normalise: normalise, analyseSymbol: analyseSymbol,
    parseFilters: parseFilters, getFilters: getFilters, planOrder: planOrder, buildPlan: buildPlan,
    fingerprint: fingerprint, checkOrder: checkOrder, makeContext: makeContext, runRisk: runRisk, realizedToday: realizedToday, FINAL: FINAL,
    readiness: readiness, approve: approve, renderSteps: renderSteps, markPlaced: markPlaced, cancelOrder: cancelOrder,
    openPosition: openPosition, closePosition: closePosition, journalStats: journalStats,
    addAlert: addAlert, checkAlerts: checkAlerts, alertsFromJournal: alertsFromJournal, alertsFromOrders: alertsFromOrders, getPrices: getPrices, alertHit: alertHit,
    computeRebalance: computeRebalance, rebalanceBreach: rebalanceBreach, makeRebalanceOrders: makeRebalanceOrders,
    classify: classify, coinsIn: coinsIn, parseAnnouncements: parseAnnouncements, parseRss2json: parseRss2json, collectNews: collectNews, NEWS_SOURCES: NEWS_SOURCES,
    emptyState: emptyState, normaliseState: normaliseState, GithubStore: GithubStore, b64encode: b64encode, b64decode: b64decode
  };
});
