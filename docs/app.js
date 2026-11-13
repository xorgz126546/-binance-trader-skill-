/* Binance Trader Dashboard - interface. Logic lives in core.js. Manual execution only: nothing here places an order. */
(function () {
  'use strict';
  const BN = window.BN, I18N = window.I18N;
  const api = BN.makeApi(function (u, o) { return fetch(u, o); });
  const getJ = function (u) { return fetch(u).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }); };

  /* ---------------------------------------------------------------- persistence */
  const LS = {
    get: function (k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } }
  };
  const D = Object.assign({ lang: 'ar', view: 'home', auto_alerts: true, dirty: false, lastRemote: null,
    cloud: { repo: '', path: 'binance-dashboard-data.json', branch: '', token: '', auto: true } }, LS.get('bnd.device') || {});
  D.cloud = Object.assign({ repo: '', path: 'binance-dashboard-data.json', branch: '', token: '', auto: true }, D.cloud || {});
  let ST = BN.normaliseState(LS.get('bnd.state'));
  let SCAN = LS.get('bnd.scan');
  const U = { prices: {}, btc: null, analysis: null, news: null, rb: null, scanning: false, more: false, modal: null, remoteCopy: null, ui: {} };
  const CS = { status: 'off', msg: '', ready: false };
  const saveDevice = function () { LS.set('bnd.device', D); };
  const saveLocal = function () { LS.set('bnd.state', ST); };

  /* ---------------------------------------------------------------- helpers */
  const $ = function (id) { return document.getElementById(id); };
  const esc = function (s) { return String(s === undefined || s === null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  const t = function (k) { const d = I18N[D.lang] || I18N.en; return d[k] !== undefined ? d[k] : (I18N.en[k] !== undefined ? I18N.en[k] : k); };
  const cfg = function () { return BN.mergeConfig(ST.config); };
  const f2 = function (x) { return Number(x).toFixed(2); };
  const sgn = function (x, d) { const v = Number(x); return (v >= 0 ? '+' : '') + v.toFixed(d === undefined ? 2 : d); };
  const cls = function (x) { return Number(x) > 0 ? 'pos' : Number(x) < 0 ? 'neg' : ''; };
  function fp(x) { if (x === null || x === undefined || !Number.isFinite(Number(x))) return '-'; x = Number(x); return x >= 10 ? x.toFixed(2) : String(Number(x.toFixed(8))); }
  const L = function (s) { return '<span class="num">' + esc(s) + '</span>'; };
  const badge = function (txt, kind) { return '<span class="badge ' + (kind || '') + '">' + esc(txt) + '</span>'; };
  const val = function (id) { const e = $(id); return e ? e.value.trim() : ''; };
  const numval = function (id) { const v = val(id); return v === '' ? null : Number(v); };
  const checked = function (id) { const e = $(id); return !!(e && e.checked); };
  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || ''); el.textContent = msg;
    $('toasts').appendChild(el);
    setTimeout(function () { el.remove(); }, kind === 'bad' ? 7000 : 3500);
  }
  function netMsg(e) { const m = String(e && e.message || e); return /unreachable|Failed to fetch|NetworkError|Load failed/i.test(m) ? t('t_net') : m; }
  function field(id, label, value, opts) {
    opts = opts || {};
    return '<label class="f"><span>' + esc(label) + '</span><input id="' + id + '" type="' + (opts.type || 'text') + '" value="' + esc(value === undefined || value === null ? '' : value) + '"' +
      (opts.step ? ' step="' + opts.step + '"' : '') + (opts.ph ? ' placeholder="' + esc(opts.ph) + '"' : '') + (opts.mode ? ' inputmode="' + opts.mode + '"' : '') +
      (opts.type === 'password' ? ' autocomplete="off"' : ' autocomplete="off" autocapitalize="off" spellcheck="false"') + '></label>';
  }
  function sparkline(values, lines) {
    if (!values || values.length < 2) return '';
    lines = (lines || []).filter(function (l) { return Number.isFinite(l.y); });
    const all = values.concat(lines.map(function (l) { return l.y; }));
    const min = Math.min.apply(null, all), max = Math.max.apply(null, all), w = 300, h = 90, pad = 4;
    const sx = function (i) { return pad + i * (w - 2 * pad) / (values.length - 1); };
    const sy = function (v) { return max === min ? h / 2 : h - pad - (v - min) * (h - 2 * pad) / (max - min); };
    const pts = values.map(function (v, i) { return sx(i).toFixed(1) + ',' + sy(v).toFixed(1); }).join(' ');
    const ls = lines.map(function (l) { return '<line x1="0" x2="' + w + '" y1="' + sy(l.y).toFixed(1) + '" y2="' + sy(l.y).toFixed(1) + '" stroke="' + l.c + '" stroke-dasharray="4 3" stroke-width="1"/>'; }).join('');
    return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" role="img">' + ls + '<polyline points="' + pts + '" fill="none" stroke="#f0b90b" stroke-width="1.6"/></svg>';
  }

  /* ---------------------------------------------------------------- state changes + cloud */
  function commit() {
    ST.updated_at = BN.isoNow(); D.dirty = true; saveLocal(); saveDevice();
    scheduleCloud(); render();
  }
  const cloudOn = function () { return !!(D.cloud.repo && D.cloud.token); };
  let store = null, storeKey = '', timer = null, remoteCopy = null;
  function getStore() {
    if (!cloudOn()) return null;
    const key = [D.cloud.repo, D.cloud.path, D.cloud.branch, D.cloud.token].join('|');
    if (!store || key !== storeKey) {
      store = BN.GithubStore({ fetchImpl: function (u, o) { return fetch(u, o); }, repo: D.cloud.repo.trim(), path: (D.cloud.path || 'binance-dashboard-data.json').trim(), branch: (D.cloud.branch || '').trim(), token: D.cloud.token.trim() });
      storeKey = key; CS.ready = false;
    }
    return store;
  }
  function isEmptyState(s) {
    return !s.journal.open.length && !s.journal.closed.length && !s.alerts.alerts.length && !s.orders.orders.length && !s.portfolio.balances.length && !Object.keys(s.targets).length && !Object.keys(s.config).length;
  }
  function scheduleCloud() {
    if (!cloudOn() || !D.cloud.auto) return;
    clearTimeout(timer); timer = setTimeout(pushNow, 2500);
  }
  async function cloudSync() {
    const st = getStore();
    if (!st) { CS.status = 'off'; renderTop(); return; }
    CS.status = 'syncing'; renderTop();
    try {
      const remote = await st.load();
      if (!remote) { await st.save(ST); }
      else if (isEmptyState(ST) || (!D.dirty && remote.updated_at !== ST.updated_at)) { ST = remote; saveLocal(); }
      else if (remote.updated_at === ST.updated_at) { /* already in sync */ }
      else if (D.dirty && (remote.updated_at === D.lastRemote)) { await st.save(ST); }
      else if (D.dirty) { CS.status = 'conflict'; remoteCopy = remote; renderTop(); renderBanner(); return; }
      D.dirty = false; D.lastRemote = ST.updated_at; saveDevice();
      CS.status = 'ok'; CS.ready = true; remoteCopy = null; render();
    } catch (e) {
      CS.status = e.conflict ? 'conflict' : 'error'; CS.msg = netMsg(e); renderTop(); renderBanner();
    }
  }
  async function pushNow() {
    const st = getStore();
    if (!st) return;
    if (!CS.ready) return cloudSync();
    CS.status = 'saving'; renderTop();
    try {
      await st.save(ST);
      D.dirty = false; D.lastRemote = ST.updated_at; saveDevice(); CS.status = 'ok';
    } catch (e) {
      if (e.conflict) { CS.status = 'conflict'; try { st.forgetSha(); remoteCopy = await st.load(); } catch (e2) { /* keep going */ } }
      else { CS.status = 'error'; CS.msg = netMsg(e); }
    }
    renderTop(); renderBanner();
  }

  /* ---------------------------------------------------------------- shell */
  const NAV = [['home', '🏠'], ['scan', '🔎'], ['orders', '📋'], ['journal', '📒'], ['analyst', '📈'], ['news', '📰'], ['alerts', '🔔'], ['rebalance', '⚖️'], ['settings', '⚙️']];
  const TABS = ['home', 'scan', 'orders', 'journal'];
  function renderTop() {
    const label = { off: 'cloud_off', ok: 'cloud_ok', saving: 'cloud_saving', syncing: 'cloud_syncing', error: 'cloud_error', conflict: 'cloud_conflict' }[CS.status];
    const tone = CS.status === 'ok' ? 'ok' : CS.status === 'error' || CS.status === 'conflict' ? 'bad' : '';
    $('top').innerHTML = '<h1>' + esc(t('app')) + '</h1><div class="tools">' + badge(t(label), tone) +
      '<button class="sm" data-act="lang">' + (D.lang === 'ar' ? 'EN' : 'عربي') + '</button></div>';
  }
  function renderNav() {
    $('side').innerHTML = '<div class="brand">◆ Binance Dashboard</div>' + NAV.map(function (n) {
      return '<button data-act="nav" data-v="' + n[0] + '" class="' + (D.view === n[0] ? 'on' : '') + '"><span>' + n[1] + '</span>' + esc(t(n[0])) + '</button>';
    }).join('');
    const inMore = TABS.indexOf(D.view) < 0;
    $('tabs').innerHTML = TABS.map(function (v) {
      const n = NAV.find(function (x) { return x[0] === v; });
      return '<button data-act="nav" data-v="' + v + '" class="' + (D.view === v ? 'on' : '') + '"><span class="ic">' + n[1] + '</span>' + esc(t(v)) + '</button>';
    }).join('') + '<button data-act="more" class="' + (inMore ? 'on' : '') + '"><span class="ic">☰</span>' + esc(t('more')) + '</button>';
  }
  function renderBanner() {
    let h = '';
    if (CS.status === 'conflict') {
      h += '<div class="alertbar">' + esc(t('c_conflict')) + '<div class="row" style="margin-top:8px"><button class="sm" data-act="use-cloud">' + esc(t('c_use_cloud')) + '</button><button class="sm dng" data-act="keep-local">' + esc(t('c_keep_local')) + '</button></div></div>';
    } else if (CS.status === 'error') h += '<div class="alertbar warnbar">☁️ ' + esc(CS.msg) + '</div>';
    (U.fired || []).forEach(function (f, i) { h += '<div class="alertbar">🔔 <b class="ltr">' + esc(f.text) + '</b><button class="sm" data-act="dismiss" data-i="' + i + '">✕</button></div>'; });
    $('banner').innerHTML = h;
  }
  function render() {
    document.documentElement.lang = D.lang; document.documentElement.dir = D.lang === 'ar' ? 'rtl' : 'ltr';
    renderTop(); renderNav(); renderBanner();
    const v = VIEWS[D.view] || VIEWS.home;
    $('view').innerHTML = '<div class="safe">' + esc(t('safe')) + '</div>' + v() + '<p class="mut small">' + esc(t('disc')) + '</p>';
    renderModal();
  }
  function renderModal() {
    const m = $('modal');
    if (!U.modal) { m.hidden = true; m.innerHTML = ''; return; }
    m.hidden = false; m.innerHTML = '<div class="sheet">' + U.modal() + '</div>';
  }
  const openModal = function (fn) { U.modal = fn; renderModal(); };
  const closeModal = function () { U.modal = null; renderModal(); };

  /* ---------------------------------------------------------------- views */
  const VIEWS = {};

  VIEWS.home = function () {
    const c = cfg(), j = ST.journal, today = BN.realizedToday(j);
    const pend = ST.orders.orders.filter(function (o) { return !BN.FINAL.has(o.status); });
    let h = '<div class="grid g4" style="margin-bottom:12px">' +
      '<div class="stat"><div class="k">' + t('budget') + '</div><div class="v num">' + f2(c.budget_usdt) + ' USDT</div></div>' +
      '<div class="stat"><div class="k">' + t('today_pnl') + '</div><div class="v num ' + cls(today) + '">' + sgn(today) + '</div></div>' +
      '<div class="stat"><div class="k">' + t('open_pos') + '</div><div class="v num">' + j.open.length + ' / ' + c.max_open_positions + '</div></div>' +
      '<div class="stat"><div class="k">' + t('active_alerts') + '</div><div class="v num">' + ST.alerts.alerts.filter(function (a) { return !a.triggered_at; }).length + '</div></div></div>';
    h += '<div class="card"><div class="row sp"><h2>' + t('btc_filter') + '</h2><button class="sm" data-act="refresh-home">' + t('refresh') + '</button></div>' +
      (U.btc ? (U.btc.ok ? badge(t('btc_ok'), 'ok') : badge(t('btc_bad'), 'bad')) + ' <span class="mut small num">' + fp(U.btc.close) + ' / EMA50 ' + fp(U.btc.ema50_1h) + '</span>' : '<span class="mut">' + t('unknown') + '</span>') + '</div>';
    h += '<div class="card"><h2>' + t('open_pos') + '</h2>';
    if (!j.open.length) h += '<div class="empty">' + t('no_positions') + '</div>';
    j.open.forEach(function (p) {
      const px = U.prices[p.symbol], pnl = px ? (px - p.entry) * p.qty : null, pct = px ? (px / p.entry - 1) * 100 : null;
      h += '<div class="item"><div class="row sp"><b class="ltr">' + esc(p.symbol) + '</b><span class="num ' + cls(pnl) + '">' + (px ? sgn(pnl) + ' USDT (' + sgn(pct) + '%)' : '-') + '</span></div>' +
        '<div class="kv"><div><div class="k">' + t('entry') + '</div><div class="v">' + fp(p.entry) + '</div></div><div><div class="k">' + t('price') + '</div><div class="v">' + fp(px) + '</div></div><div><div class="k">' + t('qty') + '</div><div class="v">' + fp(p.qty) + '</div></div>' +
        '<div><div class="k">' + t('stop') + '</div><div class="v">' + fp(p.stop) + '</div></div><div><div class="k">' + t('target') + '</div><div class="v">' + fp(p.target) + '</div></div></div>' +
        '<div class="row"><button class="sm" data-act="close-pos" data-sym="' + esc(p.symbol) + '">' + t('close') + '</button><button class="sm ghost" data-act="analyze" data-sym="' + esc(p.symbol) + '">' + t('analyze') + '</button></div></div>';
    });
    h += '</div>';
    if (pend.length) h += '<div class="card"><div class="row sp"><h2>' + t('orders') + '</h2><button class="sm" data-act="nav" data-v="orders">→</button></div>' + pend.map(function (o) {
      return '<div class="row sp item"><b class="ltr">' + esc(o.side + ' ' + o.symbol) + '</b>' + orderBadge(o) + '</div>'; }).join('') + '</div>';
    h += '<div class="card"><h2>' + t('how_title') + '</h2><ol class="steps">' + [1, 2, 3, 4, 5, 6].map(function (i) { return '<li>' + esc(t('how' + i).replace(/^\d\)\s*/, '')) + '</li>'; }).join('') + '</ol></div>';
    return h;
  };

  // Arabic labels and numbers must not share one LTR run (the % and signs jump around): wrap each number on its own.
  const nm = function (x) { return '<span class="num">' + esc(x) + '</span>'; };

  function candCard(r, watch) {
    const b = r.breakdown;
    return '<div class="item"><div class="row sp"><div class="row"><b class="ltr">' + esc(r.symbol) + '</b>' + badge(r.score, r.qualified ? 'ok' : 'warn') + badge(r.setup, 'info') + '</div>' +
      '<span class="small ' + cls(r.change_24h_pct) + '">' + t('change24') + ' ' + nm(sgn(r.change_24h_pct, 1) + '%') + '</span></div>' +
      '<div class="kv"><div><div class="k">' + t('entry') + '</div><div class="v">' + fp(r.entry) + '</div></div><div><div class="k">' + t('stop') + '</div><div class="v">' + fp(r.stop) + '</div></div><div><div class="k">' + t('target') + ' 2R</div><div class="v">' + fp(r.take_profit) + '</div></div></div>' +
      '<div class="mut small">' + nm('T' + b.trend + ' C' + b.confirm + ' E' + b.entry + ' V' + b.volume + ' H' + b.heat) + ' · ' + t('relvol') + ' ' + nm(r.rel_volume.toFixed(1) + 'x') + ' · ' + nm('RSI15 ' + (r.rsi15 === null ? '-' : r.rsi15.toFixed(0))) + ' · ' + t('stop_pct') + ' ' + nm(r.stop_pct.toFixed(2) + '%') + ' · ' + t('spread') + ' ' + nm((r.spread_pct === null ? '-' : r.spread_pct.toFixed(3)) + '%') + '</div>' +
      (watch ? '<div class="small warn ltr" style="margin-top:4px">' + esc(r.why_not.slice(0, 2).join('; ')) + '</div>' : '') +
      '<div class="row" style="margin-top:8px"><button class="sm ' + (watch ? '' : 'pri') + '" data-act="size" data-sym="' + esc(r.symbol) + '">' + t(watch ? 'size_anyway' : 'size_it') + '</button><button class="sm ghost" data-act="analyze" data-sym="' + esc(r.symbol) + '">' + t('analyze') + '</button></div></div>';
  }
  VIEWS.scan = function () {
    const c = cfg(), ui = U.ui.scan || {};
    let h = '<div class="card"><div class="grid g2">' + field('scan-top', t('scan_top'), ui.top || c.scan_top, { type: 'number', mode: 'numeric' }) + field('scan-min', t('scan_min'), ui.min === undefined ? c.min_score : ui.min, { type: 'number', mode: 'numeric' }) + '</div>' +
      '<div style="margin-top:8px">' + field('scan-syms', t('scan_syms'), ui.syms || '') + '</div>' +
      '<label class="row" style="margin:10px 0"><input type="checkbox" id="scan-ibtc"' + (ui.ibtc ? ' checked' : '') + '> <span>' + t('scan_ignore_btc') + '</span></label>' +
      '<div class="row"><button class="pri" data-act="run-scan"' + (U.scanning ? ' disabled' : '') + '>' + t(U.scanning ? 'scan_running' : 'scan_run') + '</button></div>' +
      '<div id="prog-wrap" style="margin-top:10px"' + (U.scanning ? '' : ' hidden') + '><div class="bar"><i id="prog" style="width:' + (U.prog || 0) + '%"></i></div><div class="small mut" id="prog-t"></div></div></div>';
    if (!SCAN) return h;
    const b = SCAN.market.btc;
    h += '<div class="card"><div class="row sp"><h2>' + t('last_scan') + '</h2><span class="mut small num">' + esc(SCAN.generated_at) + '</span></div>' +
      (b.known ? (b.ok ? badge(t('btc_ok'), 'ok') : badge(t('btc_bad'), 'bad')) : '') +
      '<div class="small mut" style="margin-top:6px">' + t('scanned') + ' ' + SCAN.scanned + ' ' + t('pairs') + ' · ' + t('qualified') + ' ' + SCAN.candidates.length + ' · min ' + SCAN.params.min_score +
      (SCAN.excluded ? ' · ' + t('excluded') + ': ' + SCAN.excluded.stable + ' ' + t('stables') + ', ' + SCAN.excluded.bstock + ' ' + t('bstocks') : '') + '</div>';
    if (SCAN.candidates.length) h += '<div class="row" style="margin:8px 0"><button class="sm pri" data-act="size-top">' + t('size_top') + '</button></div>' + SCAN.candidates.map(function (r) { return candCard(r, false); }).join('');
    else h += '<div class="alertbar warnbar" style="margin-top:8px">' + t('scan_none') + '</div>';
    h += '</div>';
    if (SCAN.watch.length) h += '<div class="card"><h2>' + t('watch') + '</h2>' + SCAN.watch.slice(0, 8).map(function (r) { return candCard(r, true); }).join('') + '</div>';
    SCAN.errors.slice(0, 5).forEach(function (e) { h += '<div class="small mut ltr">skipped ' + esc(e.symbol) + ': ' + esc(e.error) + '</div>'; });
    return h;
  };

  VIEWS.analyst = function () {
    const a = U.analysis;
    let h = '<div class="card"><div class="row" style="align-items:flex-end"><div style="flex:1">' + field('an-sym', t('analyst_sym'), (U.ui.an || ''), { ph: 'SOL' }) + '</div><button class="pri" data-act="run-analyst">' + t('analyst_go') + '</button></div></div>';
    if (!a) return h;
    const bear = /BEARISH|MIXED/.test(a.verdict);
    h += '<div class="card"><div class="row sp"><h2 class="ltr">' + esc(a.symbol) + '</h2>' + badge(a.verdict, /^BULL|LEANING BULL/.test(a.verdict) ? 'ok' : bear ? 'bad' : '') + '</div>' +
      '<div class="num" style="font-size:22px;font-weight:700">' + fp(a.price) + '</div>' +
      sparkline(a.series, a.support.map(function (y) { return { y: y, c: '#2ebd85' }; }).concat(a.resistance.map(function (y) { return { y: y, c: '#f6465d' }; }))) +
      '<div class="scroll"><table><thead><tr><th>TF</th><th></th><th>Close</th><th>EMA20</th><th>EMA50</th><th>RSI</th><th>ATR%</th></tr></thead><tbody>' +
      ['4h', '1h', '15m'].map(function (tf) { const f = a.timeframes[tf]; return '<tr><td>' + tf + '</td><td>' + badge(f.trend, f.trend === 'up' ? 'ok' : f.trend === 'down' ? 'bad' : '') + '</td><td class="num">' + fp(f.close) + '</td><td class="num">' + fp(f.ema20) + '</td><td class="num">' + fp(f.ema50) + '</td><td class="num">' + (f.rsi === null ? '-' : f.rsi.toFixed(1)) + '</td><td class="num">' + (f.atr_pct === null ? '-' : f.atr_pct.toFixed(2)) + '</td></tr>'; }).join('') + '</tbody></table></div>' +
      '<div class="kv"><div><div class="k">' + t('support') + '</div><div class="v">' + (a.support.map(fp).join(' · ') || '-') + '</div></div><div><div class="k">' + t('resistance') + '</div><div class="v">' + (a.resistance.map(fp).join(' · ') || '-') + '</div></div>' +
      '<div><div class="k">' + t('vol_trend') + '</div><div class="v">' + (a.stats.volume_trend_1h ? a.stats.volume_trend_1h.toFixed(2) + 'x' : '-') + '</div></div></div>' +
      '<h3>' + t('if_long') + '</h3><div class="kv"><div><div class="k">' + t('atr_stop') + '</div><div class="v">' + fp(a.levels.atr_stop) + ' (-' + a.levels.stop_pct.toFixed(2) + '%)</div></div><div><div class="k">2R</div><div class="v">' + fp(a.levels.target_2r) + '</div></div><div><div class="k">3R</div><div class="v">' + fp(a.levels.target_3r) + '</div></div></div>' +
      (a.levels.support_stop ? '<div class="small mut ltr">' + t('struct_stop') + ' ' + fp(a.levels.support_stop) + ' — ' + esc(a.levels.stop_note) + '</div>' : '') +
      '<div class="small mut" style="margin-top:6px">24h ' + nm(sgn(a.stats.change_24h_pct) + '%') + ' · vol ' + nm((a.stats.quote_volume_24h / 1e6).toFixed(1) + 'M') + ' · ' + t('spread') + ' ' + nm((a.stats.spread_pct === null ? '-' : a.stats.spread_pct.toFixed(3)) + '%') + '</div>' +
      (bear ? '<div class="alertbar warnbar" style="margin-top:8px">' + t('bearish_warn') + '</div>' : '') +
      '<div class="row" style="margin-top:10px"><button class="sm" data-act="size-analysis">' + t('size_this') + '</button></div></div>';
    return h;
  };

  VIEWS.news = function () {
    const n = U.news, ui = U.ui.news || {};
    let h = '<div class="card">' + field('nw-coins', t('news_coins'), ui.coins || '') + '<div class="grid g2" style="margin-top:8px">' + field('nw-h', t('news_hours'), ui.hours || 24, { type: 'number' }) +
      '<label class="row" style="align-self:end;margin-bottom:8px"><input type="checkbox" id="nw-mkt"' + (ui.market ? ' checked' : '') + '> <span>' + t('news_market') + '</span></label></div>' +
      '<div class="row"><button class="pri" data-act="run-news">' + t('news_go') + '</button></div><p class="mut small">' + t('news_note') + '</p></div>';
    if (!n) return h;
    h += '<div class="card">';
    if (!n.items.length) h += '<div class="empty">' + (n.problems.length >= n.total_sources ? t('news_noconn') : n.problems.length ? t('news_partial') : t('news_none')) + '</div>';
    n.items.slice(0, 40).forEach(function (i) {
      h += '<div class="item"><div class="row">' + (i.high ? badge('!! ' + t('high'), 'bad') : '') + '<span class="mut small num">' + i.age_hours.toFixed(1) + 'h · ' + esc(i.source) + '</span>' + i.coins.map(function (c) { return badge(c, 'info'); }).join('') + i.tags.map(function (g) { return badge(g, ''); }).join('') + '</div>' +
        '<div style="margin-top:2px">' + (i.link ? '<a href="' + esc(i.link) + '" target="_blank" rel="noopener noreferrer">' + esc(i.title) + '</a>' : esc(i.title)) + '</div></div>';
    });
    n.problems.forEach(function (p) { h += '<div class="small mut ltr">⚠ ' + esc(p) + '</div>'; });
    return h + '</div>';
  };

  function orderBadge(o) {
    const r = BN.readiness(o, cfg()), st = r[0];
    if (st === 'READY') return badge(t('READY'), 'ok');
    const map = { proposed: '', blocked: 'bad', risk_pass: 'info', approved: 'info', placed: 'ok', cancelled: '', closed: '' };
    return badge(t('st_' + o.status) + (st === 'NOT READY' ? '' : ''), map[o.status] || '');
  }
  function orderCard(o) {
    const c = cfg(), r = BN.readiness(o, c), done = BN.FINAL.has(o.status);
    const trade = (o.kind || 'trade') === 'trade' && o.side === 'BUY';
    let h = '<div class="item"><div class="row sp"><div class="row"><b class="ltr">' + esc(o.side + ' ' + o.symbol) + '</b>' + (o.kind === 'rebalance' ? badge('rebalance', 'info') : '') + '</div>' + orderBadge(o) + '</div>' +
      '<div class="kv"><div><div class="k">' + t('price') + '</div><div class="v">' + esc(o.price_str || o.price) + '</div></div><div><div class="k">' + t('qty') + '</div><div class="v">' + esc(o.quantity_str || o.quantity) + '</div></div><div><div class="k">' + t('notional') + '</div><div class="v">' + f2(o.notional || o.price * o.quantity) + '</div></div>' +
      (trade ? '<div><div class="k">' + t('stop') + '</div><div class="v">' + esc(o.stop_price) + '</div></div><div><div class="k">' + t('target') + '</div><div class="v">' + esc(o.take_profit) + '</div></div><div><div class="k">' + t('risk') + ' / ' + t('rr') + '</div><div class="v">' + f2(o.risk_pct) + '% / ' + f2(o.reward_risk) + '</div></div>' : '') + '</div>';
    if (o.reason) h += '<div class="small mut ltr">' + esc(o.reason) + '</div>';
    (o.notes || []).forEach(function (n) { h += '<div class="small warn ltr">• ' + esc(n) + '</div>'; });
    if (o.risk && o.risk.verdict === 'BLOCK') o.risk.reasons.forEach(function (x) { h += '<div class="small neg ltr">✗ ' + esc(x) + '</div>'; });
    if (!done) {
      if (o.risk && o.risk.verdict === 'PASS' && r[0] !== 'READY' && o.status !== 'approved') {
        h += '<div class="row" style="margin-top:8px">' + '<input style="flex:1;min-width:140px" id="yes-' + esc(o.id) + '" placeholder="' + esc(t('ph_confirm') + o.symbol) + '" autocomplete="off" autocapitalize="off" spellcheck="false" dir="ltr">' +
          '<button class="sm pri" data-act="approve" data-id="' + esc(o.id) + '">' + t('approve') + '</button></div><div class="small mut">' + t('confirm_label') + ' <b class="ltr">yes ' + esc(o.symbol) + '</b></div>';
      }
      if (r[0] === 'READY') h += '<div class="row" style="margin-top:8px"><button class="sm pri" data-act="steps" data-id="' + esc(o.id) + '">' + t('show_steps') + '</button><button class="sm" data-act="placed" data-id="' + esc(o.id) + '">' + t('mark_placed') + '</button></div>';
      else if (o.status === 'approved') r[1].forEach(function (x) { h += '<div class="small warn ltr">• ' + esc(x) + '</div>'; });
      h += '<div class="row" style="margin-top:6px"><button class="sm dng ghost" data-act="cancel-order" data-id="' + esc(o.id) + '">' + t('cancel') + '</button></div>';
    } else if (o.status === 'placed' && trade) {
      h += '<div class="row" style="margin-top:8px"><button class="sm" data-act="record-fill" data-id="' + esc(o.id) + '">' + t('record_fill') + '</button></div>';
    }
    return h + '</div>';
  }
  VIEWS.orders = function () {
    const c = cfg(), orders = ST.orders.orders;
    let h = '<div class="card"><div class="row" style="align-items:flex-end"><div style="flex:1">' + field('ord-bal', t('orders_balance'), c.budget_usdt, { type: 'number', step: 'any', mode: 'decimal' }) + '</div>' +
      '<button class="pri" data-act="size-top"' + (SCAN && SCAN.candidates.length ? '' : ' disabled') + '>' + t('orders_plan') + '</button></div>' +
      '<div class="row" style="margin-top:8px"><button data-act="risk-all"' + (orders.some(function (o) { return !BN.FINAL.has(o.status); }) ? '' : ' disabled') + '>' + t('risk_all') + '</button>' +
      '<button class="ghost" data-act="clear-done"' + (orders.some(function (o) { return BN.FINAL.has(o.status); }) ? '' : ' disabled') + '>' + t('clear_done') + '</button></div></div>';
    h += '<div class="card">' + (orders.length ? orders.map(orderCard).join('') : '<div class="empty">' + t('no_orders') + '</div>');
    (ST.orders.skipped || []).forEach(function (s) { h += '<div class="small mut ltr" style="margin-top:6px">' + t('skipped') + ' ' + esc(s.symbol || s.asset) + ': ' + esc(s.reason) + '</div>'; });
    return h + '</div>';
  };

  VIEWS.journal = function () {
    const j = ST.journal, s = BN.journalStats(j.closed);
    let h = '<div class="card"><h2>' + t('j_open') + '</h2><div class="grid g2">' + field('jo-sym', t('j_symbol'), '', { ph: 'SOL' }) + field('jo-price', t('j_price'), '', { type: 'number', step: 'any', mode: 'decimal' }) +
      field('jo-qty', t('j_qty'), '', { type: 'number', step: 'any', mode: 'decimal' }) + field('jo-fee', t('j_fee'), '', { type: 'number', step: 'any', mode: 'decimal' }) +
      field('jo-stop', t('j_stop'), '', { type: 'number', step: 'any', mode: 'decimal' }) + field('jo-target', t('j_target'), '', { type: 'number', step: 'any', mode: 'decimal' }) + '</div>' +
      '<div class="row" style="margin-top:10px"><button class="pri" data-act="journal-open">' + t('add') + '</button></div></div>';
    h += '<div class="card"><h2>' + t('open_pos') + '</h2>' + (j.open.length ? j.open.map(function (p) {
      return '<div class="row sp item"><div><b class="ltr">' + esc(p.symbol) + '</b> <span class="mut small">' + nm(fp(p.qty) + ' @ ' + fp(p.entry)) + ' · ' + t('stop') + ' ' + nm(fp(p.stop)) + ' · ' + t('target') + ' ' + nm(fp(p.target)) + '</span></div><button class="sm" data-act="close-pos" data-sym="' + esc(p.symbol) + '">' + t('close') + '</button></div>'; }).join('') : '<div class="empty">' + t('no_positions') + '</div>') + '</div>';
    h += '<div class="card"><h2>' + t('j_stats') + '</h2>';
    if (!s.trades) h += '<div class="empty">' + t('j_none') + '</div>';
    else {
      h += '<div class="grid g4"><div class="stat"><div class="k">' + t('st_trades') + '</div><div class="v num">' + s.trades + '</div></div><div class="stat"><div class="k">' + t('st_win') + '</div><div class="v num">' + s.win_rate_pct.toFixed(0) + '%</div></div>' +
        '<div class="stat"><div class="k">' + t('st_total') + '</div><div class="v num ' + cls(s.total_pnl) + '">' + sgn(s.total_pnl) + '</div></div><div class="stat"><div class="k">' + t('st_pf') + '</div><div class="v num">' + (s.profit_factor === null ? 'n/a' : s.profit_factor.toFixed(2)) + '</div></div>' +
        '<div class="stat"><div class="k">' + t('st_exp') + '</div><div class="v num ' + cls(s.expectancy) + '">' + sgn(s.expectancy) + '</div></div><div class="stat"><div class="k">' + t('st_avgr') + '</div><div class="v num">' + (s.avg_r === null ? 'n/a' : s.avg_r.toFixed(2)) + '</div></div>' +
        '<div class="stat"><div class="k">' + t('st_dd') + '</div><div class="v num">' + f2(s.max_drawdown) + '</div></div><div class="stat"><div class="k">' + t('st_fees') + '</div><div class="v num">' + f2(s.total_fees) + '</div></div></div>';
      let eq = 0; const curve = [0].concat(j.closed.slice().sort(function (a, b) { return a.closed_at < b.closed_at ? -1 : 1; }).map(function (x) { eq += x.pnl; return eq; }));
      h += sparkline(curve, [{ y: 0, c: '#8b97ab' }]);
      if (s.trades < 30) h += '<div class="small warn">' + t('j_few') + '</div>';
    }
    h += '</div><div class="card"><h2>' + t('j_closed') + '</h2><div class="scroll"><table><thead><tr><th>' + t('th_symbol') + '</th><th>' + t('th_pnl') + '</th><th>%</th><th>R</th><th>' + t('closed_at') + '</th></tr></thead><tbody>' +
      j.closed.slice(-20).reverse().map(function (x) { return '<tr><td class="ltr">' + esc(x.symbol) + '</td><td class="num ' + cls(x.pnl) + '">' + sgn(x.pnl) + '</td><td class="num ' + cls(x.pnl_pct) + '">' + sgn(x.pnl_pct) + '</td><td class="num">' + (x.r_multiple === null || x.r_multiple === undefined ? '-' : x.r_multiple.toFixed(2)) + '</td><td class="num">' + esc(String(x.closed_at).slice(0, 16).replace('T', ' ')) + '</td></tr>'; }).join('') + '</tbody></table></div></div>';
    return h;
  };

  VIEWS.alerts = function () {
    const al = ST.alerts.alerts, notif = typeof Notification !== 'undefined' ? Notification.permission : 'unsupported';
    let h = '<div class="card"><h2>' + t('al_add') + '</h2><div class="grid g2">' + field('al-sym', t('j_symbol'), '', { ph: 'SOL' }) +
      '<label class="f"><span>&nbsp;</span><select id="al-dir"><option value="above">' + t('al_dir_above') + '</option><option value="below">' + t('al_dir_below') + '</option></select></label>' +
      field('al-lvl', t('al_level'), '', { type: 'number', step: 'any', mode: 'decimal' }) +
      '<label class="f"><span>' + t('al_kind') + '</span><select id="al-kind"><option value="price">price</option><option value="stop">stop</option><option value="target">target</option><option value="entry">entry</option></select></label></div>' +
      '<div style="margin-top:8px">' + field('al-note', t('al_note'), '') + '</div><div class="row" style="margin-top:10px"><button class="pri" data-act="alert-add">' + t('add') + '</button>' +
      '<button class="sm" data-act="alerts-j">' + t('al_from_j') + '</button><button class="sm" data-act="alerts-o">' + t('al_from_o') + '</button><button class="sm" data-act="alerts-check">' + t('al_check') + '</button></div>' +
      '<div class="row" style="margin-top:10px"><label class="row"><input type="checkbox" id="al-auto"' + (D.auto_alerts ? ' checked' : '') + ' data-act="auto-alerts"> <span class="small">' + t('al_auto') + '</span></label>' +
      (notif === 'default' ? '<button class="sm" data-act="notif">' + t('al_notify') + '</button>' : '') + '</div><p class="mut small">' + t('al_note2') + '</p></div>';
    h += '<div class="card">' + (al.length ? al.map(function (a) {
      return '<div class="row sp item"><div><b class="ltr">' + esc(a.symbol) + '</b> <span>' + t(a.direction === 'above' ? 'al_dir_above' : 'al_dir_below') + ' ' + nm(fp(a.level)) + '</span> ' + badge(a.kind, 'info') + (a.triggered_at ? badge(t('al_fired') + ' @ ' + fp(a.price_at_trigger), 'bad') : badge(t('al_active'), 'ok')) +
        (a.note ? '<div class="small mut ltr">' + esc(a.note) + '</div>' : '') + '</div><div class="row">' + (a.triggered_at ? '<button class="sm" data-act="alert-rearm" data-id="' + esc(a.id) + '">' + t('al_rearm') + '</button>' : '') + '<button class="sm dng ghost" data-act="alert-del" data-id="' + esc(a.id) + '">✕</button></div></div>'; }).join('') : '<div class="empty">' + t('al_none') + '</div>') + '</div>';
    return h;
  };

  VIEWS.rebalance = function () {
    const c = cfg(), pf = ST.portfolio.balances, tg = ST.targets, tsum = Object.keys(tg).reduce(function (a, k) { return a + Number(tg[k] || 0); }, 0);
    let h = '<div class="card"><h2>' + t('rb_portfolio') + '</h2>' + pf.map(function (b, i) {
      return '<div class="row item"><b class="ltr" style="min-width:64px">' + esc(b.asset) + '</b><input class="num" style="flex:1" id="pf-' + i + '" value="' + esc(b.amount) + '" inputmode="decimal"><button class="sm dng ghost" data-act="pf-del" data-i="' + i + '">✕</button></div>'; }).join('') +
      '<div class="row" style="margin-top:8px"><div style="flex:1">' + field('pf-asset', t('rb_asset'), '', { ph: 'BTC' }) + '</div><div style="flex:1">' + field('pf-amt', t('rb_amount'), '', { type: 'number', step: 'any', mode: 'decimal' }) + '</div><button class="sm" data-act="pf-add" style="align-self:flex-end">' + t('add') + '</button></div></div>';
    h += '<div class="card"><h2>' + t('rb_targets') + ' <span class="small ' + (Math.abs(tsum - 100) > 0.5 ? 'neg' : 'pos') + '">(' + t('rb_sum') + ' ' + nm(tsum.toFixed(1)) + ')</span></h2>' + Object.keys(tg).map(function (k) {
      return '<div class="row item"><b class="ltr" style="min-width:64px">' + esc(k) + '</b><input class="num" style="flex:1" id="tg-' + esc(k) + '" value="' + esc(tg[k]) + '" inputmode="decimal"><button class="sm dng ghost" data-act="tg-del" data-k="' + esc(k) + '">✕</button></div>'; }).join('') +
      '<div class="row" style="margin-top:8px"><div style="flex:1">' + field('tg-asset', t('rb_asset'), '', { ph: 'ETH' }) + '</div><div style="flex:1">' + field('tg-pct', '%', '', { type: 'number', step: 'any', mode: 'decimal' }) + '</div><button class="sm" data-act="tg-add" style="align-self:flex-end">' + t('add') + '</button></div>' +
      '<div class="row" style="margin-top:10px"><button class="sm" data-act="rb-save">' + t('save') + '</button><button class="pri" data-act="rb-run">' + t('rb_compute') + '</button><span class="mut small">' + t('rb_band') + ' ' + c.rebalance_band_pct + ' · ' + t('rb_min') + ' ' + c.min_trade_usdt + '</span></div></div>';
    const r = U.rb;
    if (r) {
      h += '<div class="card"><div class="row sp"><h2>' + t('rb_total') + '</h2><b class="num">' + f2(r.calc.total) + ' USDT</b></div><div class="scroll"><table><thead><tr><th>' + t('rb_asset') + '</th><th>' + t('rb_weight') + '</th><th>' + t('rb_target') + '</th><th>' + t('rb_drift') + '</th><th>USDT</th></tr></thead><tbody>' +
        r.calc.rows.map(function (x) { return '<tr><td class="ltr">' + esc(x.asset) + '</td><td class="num">' + x.weight.toFixed(1) + '%</td><td class="num">' + x.target.toFixed(0) + '%</td><td class="num ' + (Math.abs(x.drift) > c.rebalance_band_pct ? 'neg' : '') + '">' + sgn(x.drift, 1) + '</td><td class="num ' + cls(x.trade_usdt) + '">' + sgn(x.trade_usdt) + '</td></tr>'; }).join('') + '</tbody></table></div>';
      r.calc.unpriced.forEach(function (a) { h += '<div class="small mut">' + t('rb_unpriced') + ': ' + esc(a) + '</div>'; });
      if (r.calc.untargeted.length) h += '<div class="small mut">' + t('rb_untargeted') + ': ' + esc(r.calc.untargeted.join(', ')) + '</div>';
      if (!r.breach) h += '<div class="alertbar info" style="margin-top:8px">' + t('rb_inside') + '</div>';
      else {
        h += (r.orders.orders.length ? r.orders.orders.map(function (o) { return '<div class="item"><b class="ltr">' + esc(o.side + ' ' + o.symbol) + '</b> <span class="num">' + esc(o.quantity_str + ' @ ' + o.price_str) + ' (~' + f2(o.notional) + ')</span><div class="small mut ltr">' + esc(o.reason) + '</div></div>'; }).join('') + '<div class="row" style="margin-top:8px"><button class="pri" data-act="rb-send">' + t('rb_send') + '</button></div>' : '');
        r.orders.skipped.forEach(function (s) { h += '<div class="small mut ltr">skip ' + esc(s.asset) + ': ' + esc(s.reason) + '</div>'; });
      }
      h += '</div>';
    }
    return h;
  };

  const CFG_GROUPS = [['budget_usdt', 'risk_per_trade_pct', 'max_risk_pct_hard', 'max_position_pct', 'cash_buffer_pct', 'max_open_positions', 'fee_pct', 'stop_limit_gap_pct', 'daily_loss_limit_pct', 'min_reward_risk', 'max_order_age_minutes', 'approval_ttl_minutes', 'max_price_drift_pct'],
    ['min_score', 'scan_top', 'min_quote_volume', 'max_spread_pct', 'min_atr_pct', 'max_atr_pct', 'stop_atr_mult', 'min_stop_pct'], ['rebalance_band_pct', 'min_trade_usdt']];
  const CFG_BOOL = ['btc_filter', 'exclude_bstocks', 'min_notional_bump', 'allow_market_orders'];
  VIEWS.settings = function () {
    const c = cfg(), cl = D.cloud;
    const lab = function (k) { const d = I18N.cfgdoc[k]; return d ? d[D.lang === 'ar' ? 0 : 1] : k; };
    let h = '<div class="card"><h2>' + t('language') + '</h2><div class="row"><button class="sm ' + (D.lang === 'ar' ? 'pri' : '') + '" data-act="setlang" data-l="ar">العربية</button><button class="sm ' + (D.lang === 'en' ? 'pri' : '') + '" data-act="setlang" data-l="en">English</button></div></div>';
    h += '<div class="card"><h2>' + t('s_cloud') + '</h2><p class="mut small">' + t('c_help') + '</p>' + field('cl-repo', t('c_repo'), cl.repo, { ph: 'xorgz126546/binance-dashboard-data' }) +
      '<div class="grid g2" style="margin-top:8px">' + field('cl-path', t('c_path'), cl.path) + field('cl-branch', t('c_branch'), cl.branch) + '</div><div style="margin-top:8px">' + field('cl-token', t('c_token'), cl.token, { type: 'password' }) + '</div>' +
      '<label class="row" style="margin:10px 0"><input type="checkbox" id="cl-auto"' + (cl.auto ? ' checked' : '') + '> <span>' + t('c_auto') + '</span></label>' +
      '<div class="row"><button class="pri" data-act="cloud-save">' + t('c_test') + ' + ' + t('save') + '</button><button data-act="cloud-sync"' + (cloudOn() ? '' : ' disabled') + '>' + t('c_sync') + '</button><button class="dng ghost" data-act="cloud-off"' + (cloudOn() ? '' : ' disabled') + '>' + t('c_off') + '</button></div>' +
      (U.cloudWarn ? '<div class="alertbar" style="margin-top:8px">' + t('c_public_warn') + '</div>' : '') + (cloudOn() && CS.status === 'ok' && !U.cloudWarn ? '<div class="small pos" style="margin-top:6px">' + t('c_connected') + '</div>' : '') + '</div>';
    h += '<div class="card"><h2>' + t('s_cfg') + '</h2>' + CFG_GROUPS.map(function (g) {
      return '<div class="grid g2" style="margin-bottom:10px">' + g.map(function (k) { return field('cfg-' + k, lab(k), c[k], { type: 'number', step: 'any', mode: 'decimal' }); }).join('') + '</div>'; }).join('') +
      '<div class="grid g2">' + CFG_BOOL.map(function (k) { return '<label class="row"><input type="checkbox" id="cfg-' + k + '"' + (c[k] ? ' checked' : '') + '> <span class="small">' + esc(lab(k)) + '</span></label>'; }).join('') + '</div>' +
      '<div style="margin-top:10px">' + field('cfg-blocked', t('s_blocked'), (c.blocked || []).join(',')) + '</div>' +
      '<div class="row" style="margin-top:10px"><button class="pri" data-act="cfg-save">' + t('save') + '</button><button class="ghost" data-act="cfg-reset">' + t('s_reset') + '</button></div></div>';
    h += '<div class="card"><h2>' + t('s_data') + '</h2><div class="row"><button data-act="export">' + t('d_export') + '</button></div>' +
      (U.exportText ? '<textarea id="exp-box" readonly>' + esc(U.exportText) + '</textarea>' : '') +
      '<h3>' + t('d_import') + '</h3><textarea id="imp-box" placeholder="{ ... }"></textarea><div class="row" style="margin-top:8px"><button data-act="import">' + t('d_import_go') + '</button></div></div>';
    return h;
  };

  /* ---------------------------------------------------------------- actions */
  const ACT = {};
  ACT.nav = function (el) { D.view = el.dataset.v; U.more = false; closeModal(); saveDevice(); render(); window.scrollTo(0, 0); };
  ACT.more = function () {
    openModal(function () {
      return '<h2>' + t('more') + '</h2><div class="morelist">' + ['analyst', 'news', 'alerts', 'rebalance', 'settings'].map(function (v) {
        const n = NAV.find(function (x) { return x[0] === v; });
        return '<button data-act="nav" data-v="' + v + '"><span>' + n[1] + '</span>' + esc(t(v)) + '</button>'; }).join('') + '</div><div class="row" style="margin-top:12px"><button class="ghost" data-act="modal-close">' + t('back') + '</button></div>';
    });
  };
  ACT['modal-close'] = closeModal;
  ACT.lang = function () { D.lang = D.lang === 'ar' ? 'en' : 'ar'; saveDevice(); render(); };
  ACT.setlang = function (el) { D.lang = el.dataset.l; saveDevice(); render(); };
  ACT.dismiss = function (el) { (U.fired || []).splice(Number(el.dataset.i), 1); renderBanner(); };
  ACT['use-cloud'] = function () {
    if (!remoteCopy) return;
    ST = remoteCopy; remoteCopy = null; D.dirty = false; D.lastRemote = ST.updated_at; saveLocal(); saveDevice(); CS.status = 'ok'; CS.ready = true; render();
  };
  ACT['keep-local'] = async function () {
    const st = getStore(); if (!st) return;
    try { st.forgetSha(); const r = await st.load(); ST.updated_at = BN.isoNow(); await st.save(ST); D.dirty = false; D.lastRemote = ST.updated_at; saveLocal(); saveDevice(); CS.status = 'ok'; CS.ready = true; remoteCopy = null; render(); }
    catch (e) { toast(netMsg(e), 'bad'); }
  };

  async function refreshPrices(symbols) {
    if (!symbols.length) return;
    try { Object.assign(U.prices, await BN.getPrices(api, symbols)); } catch (e) { toast(netMsg(e), 'bad'); }
  }
  async function refreshBtc() {
    try { U.btc = BN.btcState(BN.parseKlines(await api.getJson('/api/v3/klines', { symbol: 'BTCUSDT', interval: '1h', limit: 100 }))); } catch (e) { /* shown as unknown */ }
  }
  ACT['refresh-home'] = async function () {
    await Promise.all([refreshPrices(ST.journal.open.map(function (p) { return p.symbol; })), refreshBtc()]);
    render();
  };

  ACT['run-scan'] = async function () {
    if (U.scanning) return;
    const c = cfg();
    U.ui.scan = { top: val('scan-top'), min: val('scan-min'), syms: val('scan-syms'), ibtc: checked('scan-ibtc') };
    const syms = U.ui.scan.syms ? U.ui.scan.syms.split(/[ ,;]+/).filter(Boolean).map(BN.normalise) : null;
    const run = Object.assign({}, c, { min_score: Number(U.ui.scan.min) || c.min_score });
    U.scanning = true; U.prog = 0; render();
    try {
      const rep = await BN.runScan(api, run, { symbols: syms, top: Number(U.ui.scan.top) || c.scan_top, ignoreBtc: U.ui.scan.ibtc, onProgress: function (p) {
        if (p.stage === 'symbols') { U.prog = Math.round(p.done / p.total * 100); const b = $('prog'); if (b) b.style.width = U.prog + '%'; const x = $('prog-t'); if (x) x.textContent = p.done + ' / ' + p.total; }
        else { const x = $('prog-t'); if (x) x.textContent = p.stage; }
      } });
      SCAN = rep; LS.set('bnd.scan', rep);
      if (rep.market.btc.known) U.btc = rep.market.btc;
    } catch (e) { toast(netMsg(e), 'bad'); }
    U.scanning = false; render();
  };

  async function sizeSymbols(cands, goOrders) {
    try {
      const c = cfg(), bal = numval('ord-bal') || Number(c.budget_usdt);
      const filters = await BN.getFilters(api, cands.slice(0, 5).map(function (x) { return x.symbol; }));
      const plan = BN.buildPlan(cands, filters, bal, c, ST.journal, 5);
      const syms = new Set(plan.orders.map(function (o) { return o.symbol; }));
      ST.orders.orders = ST.orders.orders.filter(function (o) { return !((o.kind || 'trade') === 'trade' && syms.has(o.symbol) && !BN.FINAL.has(o.status)); }).concat(plan.orders);
      ST.orders.skipped = plan.skipped;
      if (goOrders !== false) D.view = 'orders';
      commit();
      if (!plan.orders.length) toast(plan.skipped.map(function (s) { return s.symbol + ': ' + s.reason; }).join('\n') || t('t_err'), 'bad');
    } catch (e) { toast(netMsg(e), 'bad'); }
  }
  ACT.size = function (el) {
    const all = SCAN ? SCAN.candidates.concat(SCAN.watch) : [];
    const c = all.find(function (x) { return x.symbol === el.dataset.sym; });
    if (c) sizeSymbols([c]);
  };
  ACT['size-top'] = function () { if (SCAN && SCAN.candidates.length) sizeSymbols(SCAN.candidates.slice(0, 3)); };
  ACT['size-analysis'] = function () {
    const a = U.analysis; if (!a) return;
    sizeSymbols([{ symbol: a.symbol, entry: a.price, stop: a.levels.atr_stop, take_profit: a.levels.target_2r, take_profit_2: a.levels.target_3r, score: null, setup: 'manual' }]);
  };

  ACT.analyze = function (el) { U.ui.an = el.dataset.sym.replace(/USDT$/, ''); D.view = 'analyst'; render(); ACT['run-analyst'](); };
  ACT['run-analyst'] = async function () {
    const s = ($('an-sym') ? val('an-sym') : U.ui.an) || '';
    if (!s) return;
    U.ui.an = s;
    try { U.analysis = await BN.analyseSymbol(api, s); } catch (e) { toast(netMsg(e), 'bad'); }
    render();
  };

  ACT['run-news'] = async function () {
    U.ui.news = { coins: val('nw-coins'), hours: val('nw-h'), market: checked('nw-mkt') };
    let coins = U.ui.news.coins.split(/[ ,;]+/).filter(Boolean).map(function (x) { return x.toUpperCase(); });
    if (!coins.length) coins = Array.from(new Set(ST.journal.open.map(function (p) { return p.symbol.replace(/USDT$/, ''); })));
    const market = U.ui.news.market || !coins.length;
    U.news = null; toast(t('t_loaded') + '…');
    try { U.news = await BN.collectNews(getJ, coins, Number(U.ui.news.hours) || 24, market); } catch (e) { toast(netMsg(e), 'bad'); }
    render();
  };

  const orderById = function (id) { return ST.orders.orders.find(function (o) { return o.id === id; }); };
  ACT['risk-all'] = async function () {
    const c = cfg();
    const syms = ST.orders.orders.filter(function (o) { return !BN.FINAL.has(o.status) && (o.kind || 'trade') === 'trade'; }).map(function (o) { return o.symbol; });
    let prices = {};
    try { prices = await BN.getPrices(api, syms); } catch (e) { toast(netMsg(e), 'bad'); }
    BN.runRisk(ST.orders, c, ST.journal, ST.portfolio, prices, true);
    commit();
  };
  ACT.approve = function (el) {
    const o = orderById(el.dataset.id); if (!o) return;
    try { BN.approve(o, val('yes-' + o.id), cfg()); toast(t('t_ok'), 'ok'); commit(); } catch (e) { toast(e.message, 'bad'); }
  };
  ACT.steps = function (el) {
    const o = orderById(el.dataset.id); if (!o) return;
    const r = BN.readiness(o, cfg());
    openModal(function () {
      return '<h2 class="ltr">' + esc(o.side + ' ' + o.symbol) + '</h2>' + (r[0] === 'READY' ? '<pre>' + esc(BN.renderSteps(o)) + '</pre>' : r[1].map(function (x) { return '<div class="small neg ltr">• ' + esc(x) + '</div>'; }).join('')) +
        '<div class="row" style="margin-top:10px"><button class="ghost" data-act="modal-close">' + t('back') + '</button></div>';
    });
  };
  ACT.placed = function (el) {
    const o = orderById(el.dataset.id); if (!o) return;
    openModal(function () {
      return '<h2 class="ltr">' + esc(o.side + ' ' + o.symbol) + '</h2><div class="grid g2">' + field('pl-price', t('fill_price'), o.price, { type: 'number', step: 'any', mode: 'decimal' }) + field('pl-qty', t('fill_qty'), o.quantity, { type: 'number', step: 'any', mode: 'decimal' }) + '</div>' +
        '<div class="row" style="margin-top:10px"><button class="pri" data-act="placed-ok" data-id="' + esc(o.id) + '">' + t('save') + '</button><button class="ghost" data-act="modal-close">' + t('cancel') + '</button></div>';
    });
  };
  ACT['placed-ok'] = function (el) {
    const o = orderById(el.dataset.id); if (!o) return;
    BN.markPlaced(o, numval('pl-price') || o.price, numval('pl-qty') || o.quantity);
    closeModal(); commit();
    if (o.side === 'BUY' && (o.kind || 'trade') === 'trade') toast(t('record_fill'), 'ok');
  };
  ACT['record-fill'] = function (el) {
    const o = orderById(el.dataset.id); if (!o) return;
    const p = o.placed || {};
    openModal(function () {
      return '<h2 class="ltr">' + esc(o.symbol) + '</h2><div class="grid g2">' + field('rf-price', t('fill_price'), p.price || o.price, { type: 'number', step: 'any', mode: 'decimal' }) + field('rf-qty', t('fill_qty'), p.qty || o.quantity, { type: 'number', step: 'any', mode: 'decimal' }) +
        field('rf-fee', t('j_fee'), '', { type: 'number', step: 'any', mode: 'decimal' }) + '</div><label class="row" style="margin:10px 0"><input type="checkbox" id="rf-al" checked> <span class="small">' + t('al_from_j') + '</span></label>' +
        '<div class="row"><button class="pri" data-act="record-fill-ok" data-id="' + esc(o.id) + '">' + t('save') + '</button><button class="ghost" data-act="modal-close">' + t('cancel') + '</button></div>';
    });
  };
  ACT['record-fill-ok'] = function (el) {
    const o = orderById(el.dataset.id); if (!o) return;
    try {
      BN.openPosition(ST.journal, o.symbol, numval('rf-price'), numval('rf-qty'), o.stop_price, o.take_profit, numval('rf-fee'), cfg().fee_pct);
      if (checked('rf-al')) BN.alertsFromJournal(ST.alerts, ST.journal);
      o.status = 'closed';
      closeModal(); commit(); toast(t('t_ok'), 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  };
  ACT['cancel-order'] = function (el) { const o = orderById(el.dataset.id); if (o) { BN.cancelOrder(o); commit(); } };
  ACT['clear-done'] = function () { ST.orders.orders = ST.orders.orders.filter(function (o) { return !BN.FINAL.has(o.status); }); commit(); };

  ACT['journal-open'] = function () {
    try {
      const p = BN.openPosition(ST.journal, val('jo-sym'), numval('jo-price'), numval('jo-qty'), numval('jo-stop'), numval('jo-target'), numval('jo-fee'), cfg().fee_pct);
      commit(); toast(p.symbol + ' ' + t('t_ok'), 'ok');
    } catch (e) { toast(e.message, 'bad'); }
  };
  ACT['close-pos'] = async function (el) {
    const sym = el.dataset.sym;
    if (!U.prices[sym]) await refreshPrices([sym]);
    openModal(function () {
      return '<h2 class="ltr">' + t('close') + ' ' + esc(sym) + '</h2><div class="grid g2">' + field('cp-price', t('j_price'), U.prices[sym] || '', { type: 'number', step: 'any', mode: 'decimal' }) + field('cp-qty', t('j_qty') + (D.lang === 'ar' ? ' (فارغ = الكل)' : ' (empty = all)'), '', { type: 'number', step: 'any', mode: 'decimal' }) +
        field('cp-fee', t('j_fee'), '', { type: 'number', step: 'any', mode: 'decimal' }) + '</div><div class="row" style="margin-top:10px"><button class="pri" data-act="close-ok" data-sym="' + esc(sym) + '">' + t('close') + '</button><button class="ghost" data-act="modal-close">' + t('cancel') + '</button></div>';
    });
  };
  ACT['close-ok'] = function (el) {
    try {
      const tr = BN.closePosition(ST.journal, el.dataset.sym, numval('cp-price'), numval('cp-qty'), numval('cp-fee'), cfg().fee_pct);
      closeModal(); commit(); toast(tr.symbol + ' ' + sgn(tr.pnl) + ' USDT', tr.pnl >= 0 ? 'ok' : 'bad');
      const c = cfg(), lim = c.budget_usdt * c.daily_loss_limit_pct / 100;
      if (BN.realizedToday(ST.journal) <= -lim) toast('Daily loss limit reached: no new buys today.', 'bad');
    } catch (e) { toast(e.message, 'bad'); }
  };

  ACT['alert-add'] = function () {
    try { BN.addAlert(ST.alerts, val('al-sym'), val('al-dir'), numval('al-lvl'), val('al-kind'), val('al-note')); commit(); } catch (e) { toast(e.message, 'bad'); }
  };
  ACT['alert-del'] = function (el) { ST.alerts.alerts = ST.alerts.alerts.filter(function (a) { return a.id !== el.dataset.id; }); commit(); };
  ACT['alert-rearm'] = function (el) { ST.alerts.alerts.forEach(function (a) { if (a.id === el.dataset.id) { a.triggered_at = null; a.notified = false; delete a.price_at_trigger; } }); commit(); };
  ACT['alerts-j'] = function () { const n = BN.alertsFromJournal(ST.alerts, ST.journal); commit(); toast(n + ' ' + t('t_ok'), 'ok'); };
  ACT['alerts-o'] = function () { const n = BN.alertsFromOrders(ST.alerts, ST.orders.orders); commit(); toast(n + ' ' + t('t_ok'), 'ok'); };
  ACT['alerts-check'] = function () { checkAlertsNow(true); };
  ACT['auto-alerts'] = function (el) { D.auto_alerts = el.checked; saveDevice(); };
  ACT.notif = async function () { try { await Notification.requestPermission(); } catch (e) { /* ignore */ } render(); };
  async function checkAlertsNow(manual) {
    const active = ST.alerts.alerts.filter(function (a) { return !a.triggered_at; });
    if (!active.length) { if (manual) toast(t('al_none')); return; }
    try {
      const prices = await BN.getPrices(api, active.map(function (a) { return a.symbol; }));
      Object.assign(U.prices, prices);
      const fired = BN.checkAlerts(ST.alerts, prices);
      if (fired.length) {
        U.fired = (U.fired || []).concat(fired);
        fired.forEach(function (f) {
          try { if (typeof Notification !== 'undefined' && Notification.permission === 'granted') new Notification(t('fired_toast'), { body: f.text }); } catch (e) { /* ignore */ }
          try { if (navigator.vibrate) navigator.vibrate([200, 100, 200]); } catch (e) { /* ignore */ }
        });
        commit();
      } else { if (manual) toast(active.length + ' ' + t('al_active')); }
    } catch (e) { if (manual) toast(netMsg(e), 'bad'); }
  }

  function readRebalanceInputs() {
    ST.portfolio.balances.forEach(function (b, i) { const v = $('pf-' + i); if (v) b.amount = Number(v.value) || 0; });
    Object.keys(ST.targets).forEach(function (k) { const v = $('tg-' + k); if (v) ST.targets[k] = Number(v.value) || 0; });
  }
  ACT['pf-add'] = function () {
    readRebalanceInputs();
    const raw = val('pf-asset').toUpperCase(), asset = raw === 'USDT' ? 'USDT' : raw.replace(/USDT$/, '');
    if (!asset) return;
    const ex = ST.portfolio.balances.find(function (b) { return b.asset === asset; });
    if (ex) ex.amount = Number(val('pf-amt')) || 0; else ST.portfolio.balances.push({ asset: asset, amount: Number(val('pf-amt')) || 0 });
    commit();
  };
  ACT['pf-del'] = function (el) { readRebalanceInputs(); ST.portfolio.balances.splice(Number(el.dataset.i), 1); commit(); };
  ACT['tg-add'] = function () {
    readRebalanceInputs();
    const raw = val('tg-asset').toUpperCase(), asset = raw === 'USDT' ? 'USDT' : raw.replace(/USDT$/, '');
    if (!asset) return;
    ST.targets[asset] = Number(val('tg-pct')) || 0; commit();
  };
  ACT['tg-del'] = function (el) { readRebalanceInputs(); delete ST.targets[el.dataset.k]; commit(); };
  ACT['rb-save'] = function () { readRebalanceInputs(); commit(); toast(t('s_saved'), 'ok'); };
  ACT['rb-run'] = async function () {
    readRebalanceInputs(); saveLocal();
    try {
      const c = cfg(), assets = new Set(ST.portfolio.balances.map(function (b) { return b.asset; }).concat(Object.keys(ST.targets)));
      const pairs = Array.from(assets).filter(function (a) { return a !== 'USDT'; }).map(function (a) { return a + 'USDT'; });
      let prices = {};
      try { prices = pairs.length ? await BN.getPrices(api, pairs) : {}; } catch (e) { if (/rejected/.test(e.message)) throw new Error('Does every asset have a USDT pair?'); throw e; }
      const calc = BN.computeRebalance(ST.portfolio, ST.targets, prices);
      const breach = BN.rebalanceBreach(calc.rows, c.rebalance_band_pct);
      let orders = { orders: [], skipped: [] };
      if (breach) {
        const tp = calc.rows.filter(function (r) { return r.asset !== 'USDT' && Math.abs(r.trade_usdt) >= c.min_trade_usdt; }).map(function (r) { return r.asset + 'USDT'; });
        const filters = tp.length ? await BN.getFilters(api, tp) : {};
        orders = BN.makeRebalanceOrders(calc.rows, filters, c.min_trade_usdt, c.fee_pct);
      }
      U.rb = { calc: calc, breach: breach, orders: orders };
    } catch (e) { toast(netMsg(e), 'bad'); U.rb = null; }
    render();
  };
  ACT['rb-send'] = function () {
    if (!U.rb) return;
    ST.orders.orders = ST.orders.orders.filter(function (o) { return !(o.kind === 'rebalance' && !BN.FINAL.has(o.status)); }).concat(U.rb.orders.orders);
    ST.orders.skipped = U.rb.orders.skipped; D.view = 'orders'; commit();
  };

  ACT['cfg-save'] = function () {
    const out = {};
    CFG_GROUPS.forEach(function (g) { g.forEach(function (k) { const v = numval('cfg-' + k); if (v !== null && Number.isFinite(v)) out[k] = v; }); });
    CFG_BOOL.forEach(function (k) { out[k] = checked('cfg-' + k); });
    out.blocked = val('cfg-blocked').split(/[ ,;]+/).filter(Boolean).map(function (x) { return x.toUpperCase(); });
    const diff = {};
    Object.keys(out).forEach(function (k) { if (JSON.stringify(out[k]) !== JSON.stringify(BN.DEFAULTS[k])) diff[k] = out[k]; });
    ST.config = diff; commit(); toast(t('s_saved'), 'ok');
    if (out.max_risk_pct_hard > 2 || out.risk_per_trade_pct > out.max_risk_pct_hard) toast(t('cfg_note_risk'), 'bad');
  };
  ACT['cfg-reset'] = function () { ST.config = {}; commit(); };
  ACT['cloud-save'] = async function () {
    D.cloud = { repo: val('cl-repo'), path: val('cl-path') || 'binance-dashboard-data.json', branch: val('cl-branch'), token: val('cl-token'), auto: checked('cl-auto') };
    saveDevice(); store = null; U.cloudWarn = false;
    if (!/^[\w.-]+\/[\w.-]+$/.test(D.cloud.repo) || !D.cloud.token) { toast('owner/name + token', 'bad'); render(); return; }
    try {
      const info = await getStore().check();
      if (!info.private) { U.cloudWarn = true; toast(t('c_public_warn'), 'bad'); render(); return; }
      await cloudSync(); toast(t('c_connected'), 'ok');
    } catch (e) { toast(netMsg(e), 'bad'); }
    render();
  };
  ACT['cloud-sync'] = function () { cloudSync(); };
  ACT['cloud-off'] = function () { D.cloud.token = ''; store = null; CS.status = 'off'; CS.ready = false; saveDevice(); render(); };
  ACT.export = function () { U.exportText = JSON.stringify(ST, null, 2); render(); };
  ACT.import = function () {
    try {
      const j = JSON.parse(val('imp-box'));
      if (!j || typeof j !== 'object' || (!j.journal && !j.config && !j.orders && !j.alerts)) throw new Error(t('d_bad'));
      ST = BN.normaliseState(j); commit(); toast(t('d_imported'), 'ok');
    } catch (e) { toast(t('d_bad') + ': ' + e.message, 'bad'); }
  };

  /* ---------------------------------------------------------------- events + timers */
  document.addEventListener('click', function (e) {
    const el = e.target.closest('[data-act]');
    if (!el || el.tagName === 'INPUT') return;
    const fn = ACT[el.dataset.act];
    if (fn) { e.preventDefault(); fn(el, e); }
  });
  document.addEventListener('change', function (e) {
    const el = e.target;
    if (el && el.dataset && el.dataset.act && el.tagName === 'INPUT') { const fn = ACT[el.dataset.act]; if (fn) fn(el, e); }
  });
  $('modal').addEventListener('click', function (e) { if (e.target === $('modal')) closeModal(); });
  setInterval(function () {
    if (document.visibilityState === 'hidden') return;
    if (D.auto_alerts && ST.alerts.alerts.some(function (a) { return !a.triggered_at; })) checkAlertsNow(false);
    else if (D.view === 'home' && ST.journal.open.length) refreshPrices(ST.journal.open.map(function (p) { return p.symbol; })).then(function () { if (D.view === 'home' && !U.modal) render(); });
  }, 30000);

  render();
  if (D.view === 'home' && ST.journal.open.length) ACT['refresh-home']();
  if (cloudOn()) cloudSync();
  window.__BND = { ACT: ACT, get ST() { return ST; }, U: U, D: D, CS: CS, render: render, VIEWS: VIEWS };
})();
