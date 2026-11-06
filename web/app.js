/* eBay Opportunity Board — static SPA over Supabase REST (anon read, token-gated write). */
'use strict';

const CFG = window.EB_CONFIG || {};
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, d = 0) => n == null ? '—' : Number(n).toLocaleString('en-US',
  { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (n) => n == null ? '—' : '$' + Number(n).toFixed(2);
const ago = (iso) => {
  if (!iso) return 'never';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return 'just now';
  if (s < 5400) return Math.round(s / 60) + 'm ago';
  if (s < 172800) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
};

const state = {
  keywords: [],
  kw: null,
  items: [],
  token: localStorage.getItem('eb_token') || '',
};

/* ────────────────────────────── transport ────────────────────────────── */
async function api(path, opts = {}) {
  if (!CFG.url || !CFG.anon) throw new Error('config.js missing SUPABASE_URL / ANON_KEY');
  const res = await fetch(CFG.url.replace(/\/$/, '') + '/rest/v1/' + path, {
    ...opts,
    headers: {
      apikey: CFG.anon,
      Authorization: 'Bearer ' + CFG.anon,
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  });
  const text = await res.text();
  if (!res.ok) {
    let msg = text;
    try { const j = JSON.parse(text); msg = j.message || j.hint || text; } catch { /* keep */ }
    throw new Error(msg);
  }
  return text ? JSON.parse(text) : null;
}
const rpc = (fn, body) => api('rpc/' + fn, { method: 'POST', body: JSON.stringify(body) });

function toast(msg, kind = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast' + (kind === 'err' ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('hidden'), 2600);
}

/* ────────────────────────────── routing ────────────────────────────── */
function show(view) {
  for (const v of ['board', 'detail', 'add']) $('#view-' + v).classList.toggle('hidden', v !== view);
  document.querySelectorAll('.tab').forEach((t) =>
    t.classList.toggle('active', t.dataset.view === view && view !== 'detail'));
  if (view === 'board') { history.replaceState(null, '', '#'); loadBoard(); }
  if (view === 'add') { renderManage(); }
}

/* ────────────────────────────── board ────────────────────────────── */
async function loadBoard() {
  try {
    const rows = await api('eb_keyword_board?select=*');
    state.keywords = rows || [];
    renderBoard();
    const newest = state.keywords.map((k) => k.last_run_at_latest).filter(Boolean).sort().pop();
    $('#sync').textContent = newest ? 'synced ' + ago(newest) : 'no laps yet';
    $('#foot-note').textContent = `${state.keywords.length} keyword(s) · active market only · ` +
      `units from sold badges (floor)`;
  } catch (e) {
    $('#sync').textContent = 'sync failed';
    toast('Could not load board: ' + e.message, 'err');
  }
}

function sortRows(rows) {
  const key = $('#board-sort').value;
  const dir = key === 'keyword' || key === 'median_price' ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (key === 'keyword') return String(a.keyword).localeCompare(String(b.keyword));
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;          // nulls last, always
    if (bv == null) return -1;
    return (av - bv) * dir;
  });
}

function renderBoard() {
  const q = $('#kw-filter').value.trim().toLowerCase();
  const rows = sortRows(state.keywords.filter((k) => !q || k.keyword.includes(q)));
  const max = Math.max(1, ...state.keywords.map((k) => k.total_units || 0));
  const anyDeltas = state.keywords.some((k) => k.units_per_day != null);

  $('#board-hint').textContent = anyDeltas
    ? 'Ranked by units/day where measurable; keywords under 10 badged listings stay unranked.'
    : 'No keyword has two laps yet — showing lifetime badge units. units/day appears after the second crawl.';

  $('#board-empty').classList.toggle('hidden', state.keywords.length > 0);
  $('#board').innerHTML = rows.map((k) => {
    const gated = k.gate !== 'ranked';
    const ud = k.units_per_day != null ? fmt(k.units_per_day, 1) : '—';
    const att = k.attrition != null ? Math.round(k.attrition * 100) + '%' : '—';
    return `
    <article class="kcard" data-kw="${esc(k.keyword)}">
      <div class="khead">
        <span class="kname">${esc(k.keyword)}</span>
        <span class="chip ${gated ? 'unmeasured' : 'ranked'}">${gated ? 'unmeasured' : 'ranked'}</span>
      </div>
      <div class="kstats">
        <div class="kstat"><b>${ud}</b><span>units/day</span></div>
        <div class="kstat"><b>${fmt(k.total_units)}</b><span>lifetime units</span></div>
        <div class="kstat"><b>${fmt(k.badged)}/${fmt(k.listings)}</b><span>badged / listed</span></div>
        <div class="kstat"><b>${money(k.median_price)}</b><span>median price</span></div>
        <div class="kstat"><b>${att}</b><span>attrition</span></div>
        <div class="kstat"><b>${k.status}</b><span>${k.passes_done ? k.passes_done + ' laps' : 'first lap pending'}</span></div>
      </div>
      <div class="bar-mini"><i style="width:${Math.round((k.total_units || 0) / max * 100)}%"></i></div>
    </article>`;
  }).join('');

  document.querySelectorAll('.kcard').forEach((el) =>
    el.addEventListener('click', () => openKeyword(el.dataset.kw)));
}

/* ────────────────────────────── detail ────────────────────────────── */
async function openKeyword(keyword) {
  state.kw = state.keywords.find((k) => k.keyword === keyword) || { keyword };
  $('#d-kw').textContent = keyword;
  $('#view-board').classList.add('hidden');
  $('#view-detail').classList.remove('hidden');
  document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
  renderStats();
  $('#items').innerHTML = '<p class="muted small">loading listings…</p>';
  try {
    const rows = await api('eb_listings?select=eb_id,title,url,price_usd,prev_price,units_badge,' +
      'prev_units,watchers,image_url,free_shipping,location,last_seen,starred,quantity_text,' +
      'eb_listing_keywords!inner(keyword)&eb_listing_keywords.keyword=eq.' +
      encodeURIComponent(keyword) + '&limit=600');
    state.items = rows || [];
    renderItems();
  } catch (e) {
    $('#items').innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
  }
  window.scrollTo({ top: 0 });
}

function renderStats() {
  const k = state.kw;
  const gated = k.gate !== 'ranked';
  const cells = [
    [k.units_per_day != null ? fmt(k.units_per_day, 1) : '—', 'units / day'],
    [fmt(k.total_units), 'lifetime units'],
    [`${fmt(k.badged)}/${fmt(k.listings)}`, 'badged / listed'],
    [money(k.median_price), 'median price'],
    [k.attrition != null ? Math.round(k.attrition * 100) + '%' : '—', 'attrition'],
    [k.last_run_at_latest ? ago(k.last_run_at_latest) : '—', 'last lap'],
  ];
  $('#d-stats').innerHTML = cells.map(([v, l]) =>
    `<div class="stat"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join('') +
    `<div class="stat"><b class="chip ${gated ? 'unmeasured' : 'ranked'}">${gated ? 'unmeasured' : 'ranked'}</b>
      <span>volume gate</span></div>`;
}

function renderItems() {
  const q = $('#item-filter').value.trim().toLowerCase();
  const key = $('#item-sort').value;
  let rows = state.items.filter((r) => !q || (r.title || '').toLowerCase().includes(q));
  rows = [...rows].sort((a, b) => {
    if (key === 'price_asc') return (a.price_usd ?? 1e9) - (b.price_usd ?? 1e9);
    if (key === 'price_desc') return (b.price_usd ?? -1) - (a.price_usd ?? -1);
    if (key === 'last_seen') return new Date(b.last_seen) - new Date(a.last_seen);
    return (b[key] ?? -1) - (a[key] ?? -1);
  });

  $('#items-empty').classList.toggle('hidden', state.items.length > 0);
  $('#items').innerHTML = rows.map((r) => {
    const drop = r.prev_price && r.price_usd && r.price_usd < r.prev_price;
    const gained = r.prev_units != null && r.units_badge != null && r.units_badge > r.prev_units;
    return `
    <article class="item">
      <button class="star" data-id="${esc(r.eb_id)}" title="Watch">${r.starred ? '★' : '☆'}</button>
      <a href="${esc(r.url)}" target="_blank" rel="noopener">
        ${r.image_url ? `<img loading="lazy" src="${esc(r.image_url)}" alt=""
             onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'thumb-fallback',textContent:'no image'}))" />`
          : `<div class="thumb-fallback">no image</div>`}
      </a>
      <div class="ibody">
        <a class="ititle" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>
        <div class="iprice">${money(r.price_usd)}${drop ? `<s>${money(r.prev_price)}</s>` : ''}</div>
        <div class="imeta">
          ${r.units_badge != null ? `<span class="tag sold">${fmt(r.units_badge)}+ sold</span>` : ''}
          ${gained ? `<span class="tag delta">+${fmt(r.units_badge - r.prev_units)}</span>` : ''}
          ${r.watchers != null ? `<span class="tag watch">${fmt(r.watchers)} watching</span>` : ''}
          ${r.free_shipping ? '<span class="tag">free ship</span>' : ''}
          ${r.location ? `<span class="tag">${esc(String(r.location).replace('Located in ', ''))}</span>` : ''}
        </div>
      </div>
    </article>`;
  }).join('');

  document.querySelectorAll('.star').forEach((b) => b.addEventListener('click', async (ev) => {
    ev.preventDefault();
    if (!state.token) return toast('Add a write token first (Add keyword tab)', 'err');
    const on = b.textContent.trim() === '☆';
    try {
      await rpc('eb_set_star', { p_token: state.token, p_eb_id: b.dataset.id, p_on: on });
      const it = state.items.find((x) => x.eb_id === b.dataset.id);
      if (it) it.starred = on;
      b.textContent = on ? '★' : '☆';
    } catch (e) { toast(e.message, 'err'); }
  }));
}

/* ────────────────────────────── manage / add ────────────────────────────── */
function renderManage() {
  const rows = sortRows(state.keywords);
  $('#manage').innerHTML = rows.length ? rows.map((k) => `
    <div class="mrow">
      <span>${esc(k.keyword)} <span class="muted small">· ${esc(k.status)}</span></span>
      <span class="acts">
        <button data-act="toggle" data-kw="${esc(k.keyword)}"
                data-status="${k.status === 'paused' ? 'queued' : 'paused'}">
          ${k.status === 'paused' ? 'Resume' : 'Pause'}</button>
        <button data-act="delete" data-kw="${esc(k.keyword)}">Remove</button>
      </span>
    </div>`).join('') : '<p class="muted small">No keywords yet.</p>';

  document.querySelectorAll('#manage button').forEach((b) => b.addEventListener('click', async () => {
    if (!state.token) return toast('Add a write token first', 'err');
    try {
      if (b.dataset.act === 'toggle')
        await rpc('eb_set_status', { p_token: state.token, p_keyword: b.dataset.kw, p_status: b.dataset.status });
      else if (confirm(`Remove "${b.dataset.kw}"? Listings stay, the queue entry goes.`))
        await rpc('eb_delete_keyword', { p_token: state.token, p_keyword: b.dataset.kw });
      await loadBoard();
      renderManage();
      toast('Updated');
    } catch (e) { toast(e.message, 'err'); }
  }));
}

async function submitKeyword(ev) {
  ev.preventDefault();
  const f = ev.target;
  const msg = $('#add-msg');
  const keyword = f.keyword.value.trim().toLowerCase();
  const token = f.token.value.trim() || state.token;
  if (!keyword) return;
  if (!token) { msg.className = 'msg err'; msg.textContent = 'Write token required.'; return; }

  try {
    await rpc('eb_add_keyword', {
      p_token: token,
      p_keyword: keyword,
      p_depth: Number(f.depth.value) || 10,
      p_interval_hours: Number(f.interval.value) || 48,
    });
    state.token = token;
    localStorage.setItem('eb_token', token);
    $('#token-row').classList.add('hidden');
    f.keyword.value = '';
    msg.className = 'msg ok';
    msg.textContent = `“${keyword}” queued — it appears on the board now and is crawled on the next lap.`;
    await loadBoard();
    renderManage();
  } catch (e) {
    msg.className = 'msg err';
    msg.textContent = /bad token/i.test(e.message) ? 'That write token is not valid.' : e.message;
  }
}

/* ────────────────────────────── init ────────────────────────────── */
function init() {
  document.querySelectorAll('.tab').forEach((t) =>
    t.addEventListener('click', () => show(t.dataset.view)));
  $('#refresh').addEventListener('click', () => {
    if (state.kw && !$('#view-detail').classList.contains('hidden')) openKeyword(state.kw.keyword);
    else loadBoard();
  });
  $('#back').addEventListener('click', () => show('board'));
  $('#board-sort').addEventListener('change', renderBoard);
  $('#kw-filter').addEventListener('input', renderBoard);
  $('#item-sort').addEventListener('change', renderItems);
  $('#item-filter').addEventListener('input', renderItems);
  $('#add-form').addEventListener('submit', submitKeyword);
  if (state.token) $('#token-row').classList.add('hidden');
  loadBoard();
}
document.addEventListener('DOMContentLoaded', init);
