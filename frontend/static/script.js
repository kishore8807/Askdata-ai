'use strict';
/* AskData Ai — AI Database Work Assistant (Stitch UI). Vanilla JS; every control calls a real backend endpoint.
   Security notes: the session is an HttpOnly cookie (JS cannot read it); the CSRF token is echoed in a header;
   the role shown here is informational only — the backend re-checks everything on every request. */

// ───────────────────────────── helpers ─────────────────────────────
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ESC[c]);
const fmtTime = iso => { try { return new Date(iso).toLocaleString(); } catch { return ''; } };
const ago = iso => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`; return `${Math.floor(s / 86400)}d ago`;
};
const csrf = () => { const m = document.cookie.match(/(?:^|; )csrf_token=([^;]*)/); return m ? decodeURIComponent(m[1]) : ''; };
const icon = (n, cls = '') => `<span class="material-symbols-outlined ${cls}">${n}</span>`;
const spinner = '<span class="spinner"></span>';

function newStudio() {
  return { nl: '', gen: null, sql: '', editing: false, result: null, ticket: null, error: null, busy: false, running: false, attach: null, attachBusy: false, tab: 0 };
}

const state = {
  user: null, perms: {}, status: null, view: 'studio', expiresAt: null,
  notif: { items: [], unread: 0, open: false }, pending: 0,
  studio: newStudio(),
  approvals: { tab: 'pending', selected: null, pending: [], all: [], rejecting: false },
  audit: { action: '', offset: 0, limit: 50 },
  emp: { search: '' },
};

/* Wipes everything one user could see, so the next sign-in on this browser tab starts clean. */
function resetClientState() {
  state.studio = newStudio();
  state.approvals = { tab: 'pending', selected: null, pending: [], all: [], rejecting: false };
  state.notif = { items: [], unread: 0, open: false };
  state.audit = { action: '', offset: 0, limit: 50 };
  state.emp = { search: '' };
  state.perms = {}; state.status = null; state.pending = 0; state.view = 'studio';
  const m = $('#modal-root'); if (m) m.innerHTML = '';
  history.replaceState(null, '', location.pathname);
}

async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (o.method !== 'GET') o.headers['X-CSRF-Token'] = csrf();
  if (opts.json !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.json); }
  if (opts.form) o.body = opts.form;
  let res;
  try { res = await fetch(path, o); }
  catch { return { ok: false, status: 0, data: { success: false, message: 'Cannot reach the server. Please try again later.' } }; }
  let data = {};
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (res.status === 401 && state.user && !opts.quiet) sessionEnded(data.message);
  return { ok: res.ok, status: res.status, data };
}

function toast(msg, type = 'info') {
  const tone = {
    info: 'bg-inverse-surface text-inverse-on-surface', success: 'bg-primary text-on-primary',
    error: 'bg-error text-on-error', warn: 'bg-secondary-container text-on-secondary-container'
  }[type];
  const ic = { info: 'info', success: 'check_circle', error: 'error', warn: 'warning' }[type];
  const el = document.createElement('div');
  el.className = `toast ${tone}`;
  el.innerHTML = `${icon(ic, 'text-[18px]')}<div class="flex-1">${esc(msg)}</div>`;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), type === 'error' ? 7000 : 4500);
}

function confirmModal({ title, body, confirmText = 'Confirm', tone = 'primary', cancelText = 'Cancel' }) {
  return new Promise(resolve => {
    const root = $('#modal-root');
    root.innerHTML = `<div class="modal-backdrop"><div class="bg-surface-container-lowest rounded-2xl shadow-2xl max-w-lg w-full p-6 fade-in" role="dialog" aria-modal="true">
      <div class="flex items-start gap-3"><div class="w-10 h-10 rounded-xl ${tone === 'danger' ? 'bg-error-container text-on-error-container' : 'bg-primary-fixed text-primary'} flex items-center justify-center">${icon(tone === 'danger' ? 'warning' : 'help', 'fill-1')}</div>
      <div class="flex-1"><h3 class="font-headline-sm text-headline-sm">${esc(title)}</h3><div class="mt-2 text-body-md text-on-surface-variant">${body}</div></div></div>
      <div class="mt-6 flex justify-end gap-2"><button id="m-cancel" class="px-4 py-2 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface text-label-md font-label-md">${esc(cancelText)}</button>
      <button id="m-ok" class="px-4 py-2 rounded-lg ${tone === 'danger' ? 'bg-error' : 'bg-primary hover:bg-secondary'} text-on-primary text-label-md font-label-md">${esc(confirmText)}</button></div></div></div>`;
    const done = v => { root.innerHTML = ''; resolve(v); };
    $('#m-cancel').onclick = () => done(false); $('#m-ok').onclick = () => done(true);
    $('.modal-backdrop').addEventListener('click', e => { if (e.target.classList.contains('modal-backdrop')) done(false); });
    $('#m-ok').focus();
  });
}

// ───────────────────────────── SQL display ─────────────────────────────
const KW = new Set(('SELECT FROM WHERE JOIN INNER LEFT RIGHT FULL OUTER CROSS ON GROUP BY HAVING ORDER LIMIT OFFSET INSERT INTO VALUES UPDATE SET DELETE ' +
  'CREATE TABLE ALTER ADD COLUMN DROP TRUNCATE BEGIN COMMIT ROLLBACK AND OR NOT NULL IS IN AS DISTINCT UNION ALL WITH ASC DESC CASE WHEN THEN ELSE END ' +
  'LIKE ILIKE BETWEEN EXISTS RETURNING PRIMARY KEY FOREIGN REFERENCES DEFAULT INDEX VIEW CASCADE IF TRUE FALSE').split(' '));
function highlightLine(line) {
  return line.replace(/(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g,
    (m, cm, str, num, word, other) => {
      if (cm) return `<span class="tk-cm">${esc(cm)}</span>`;
      if (str) return `<span class="tk-str">${esc(str)}</span>`;
      if (num) return `<span class="tk-num">${esc(num)}</span>`;
      if (word) return KW.has(word.toUpperCase()) ? `<span class="tk-kw">${esc(word)}</span>` : esc(word);
      return esc(other);
    });
}
function prettySql(sql) {
  if (!sql) return '';
  if (sql.includes('\n')) return sql.trim();
  // only break on clause keywords outside string literals
  return sql.split(/('(?:[^']|'')*')/).map((seg, i) => i % 2 ? seg :
    seg.replace(/\s+(FROM|WHERE|SET|VALUES|GROUP BY|HAVING|ORDER BY|LIMIT|(?:LEFT |RIGHT |INNER |FULL |CROSS )?JOIN|UNION|RETURNING)\b/g, '\n$1')
      .replace(/;\s*(?=\S)/g, ';\n')).join('').trim();
}
function sqlLines(lines, from = 0) {
  return `<div class="sql-block"><pre>${lines.map((l, i) =>
    `<div class="sql-line"><span class="sql-ln">${String(from + i + 1).padStart(2, '0')}</span><span class="sql-tx">${highlightLine(l) || ' '}</span></div>`).join('')}</pre></div>`;
}
/* Long scripts (e.g. one UPDATE per row of an attached file) are collapsed after `max` lines. */
function sqlBlock(sql, { max = 40 } = {}) {
  const lines = (sql || '').split('\n');
  if (lines.length <= max + 2) return sqlLines(lines);
  return `${sqlLines(lines.slice(0, max))}<details class="mt-1"><summary class="text-primary font-label-md text-label-md hover:underline py-1">Show the other ${lines.length - max} lines</summary>${sqlLines(lines.slice(max), max)}</details>`;
}

// ───────────────────────────── shell ─────────────────────────────
const NAV = () => {
  const role = state.user.role;
  return [
    {
      group: 'Core AI Studio', items: [
        { id: 'studio', icon: 'terminal', label: 'AI Query Studio' },
        { id: 'schema', icon: 'account_tree', label: 'Schema & Explorer' },
        { id: 'history', icon: 'history', label: 'Query History' }]
    },
    {
      group: 'Governance & Operations', items: [
        role === 'DBA' ? { id: 'approvals', icon: 'gavel', label: 'Pending Approvals', badge: 'pending' }
          : role === 'DEVELOPER' ? { id: 'approvals', icon: 'gavel', label: 'My Change Requests', badge: 'pending' } : null,
        role === 'DBA' ? { id: 'audit', icon: 'verified_user', label: 'Audit Logs' } : null].filter(Boolean)
    },
    {
      group: 'Team & Data Sync', items: [
        { id: 'employees', icon: 'group', label: 'Employee Directory' },
        { id: 'settings', icon: 'settings', label: 'Settings' }].filter(Boolean)
    },
  ].filter(g => g.items.length);
};

function navHtml() {
  return NAV().map(g => `<div class="space-y-1.5"><div class="px-3"><span class="font-label-sm text-label-sm uppercase tracking-wider text-tertiary font-semibold">${esc(g.group)}</span></div>
    <nav class="space-y-0.5">${g.items.map(it => {
    const active = state.view === it.id;
    const cls = active ? 'bg-surface-container-lowest text-primary shadow-sm font-semibold border-l-2 border-primary'
      : 'text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface';
    const badge = it.badge === 'pending' && state.pending > 0 ? `<span class="px-1.5 py-0.5 rounded-full bg-secondary-container/30 text-on-secondary-container font-label-sm text-label-sm font-semibold">${state.pending}</span>` : '';
    return `<a href="#${it.id}" data-nav="${it.id}" class="flex items-center justify-between px-3 py-2 rounded-lg font-body-md text-body-md transition-all ${cls}">
        <div class="flex items-center gap-3">${icon(it.icon, 'text-[20px]')}<span>${esc(it.label)}</span></div>${badge}</a>`;
  }).join('')}</nav></div>`).join('');
}

const LOGO = `<div class="w-9 h-9 rounded-xl bg-primary flex items-center justify-center shadow-sm text-on-primary">${icon('database', 'text-[22px]')}</div>`;

function renderShell() {
  const u = state.user;
  $('#app').innerHTML = `
  <aside class="fixed left-0 top-0 h-full w-72 bg-surface-container-low border-r border-outline-variant/30 z-50 flex flex-col justify-between shadow-[0_1px_8px_rgba(0,0,0,0.03)] max-lg:hidden" id="sidebar">
    <div class="flex flex-col h-full">
      <div class="h-16 px-5 flex items-center gap-3 border-b border-outline-variant/20">${LOGO}
        <div class="flex flex-col"><span class="font-headline-md text-headline-sm font-semibold tracking-tight text-on-surface leading-none">AskData Ai</span>
        <span class="font-label-sm text-label-sm text-tertiary tracking-wider uppercase mt-0.5">AI Database Assistant</span></div></div>
      <div id="nav" class="flex-1 overflow-y-auto px-3 py-4 space-y-6">${navHtml()}</div>
      <div class="p-3 border-t border-outline-variant/20 bg-surface-container">
        <div class="flex items-center justify-between p-2 rounded-lg bg-surface-container-lowest border border-outline-variant/30">
          <div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-primary animate-pulse"></span><span class="font-code-md text-code-md text-on-surface font-medium">session</span></div>
          <span id="session-timer" class="font-label-sm text-label-sm text-tertiary">--:--</span></div></div></div>
  </aside>
  <div class="lg:pl-72">
    <header class="fixed top-0 left-0 lg:left-72 right-0 h-16 bg-surface/90 backdrop-blur-md border-b border-outline-variant/20 z-40 flex items-center justify-between px-4 lg:px-6">
      <div class="flex items-center gap-3 overflow-hidden">
        <div id="pill-db" class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-container border border-outline-variant/30"></div>
      </div>
      <div class="flex items-center gap-3.5">
        <div class="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-surface-container-high border border-outline-variant/30 font-label-sm text-label-sm uppercase tracking-wider font-semibold text-on-surface-variant">${icon('shield_person', 'text-[14px]')}<span>${esc(u.role_label)}</span></div>
        <div class="relative"><button id="bell" class="relative p-2 rounded-full hover:bg-surface-container-high transition-colors" aria-label="Notifications">${icon('notifications', 'text-[22px] text-on-surface-variant')}<span id="bell-dot" class="absolute top-1 right-1 min-w-[16px] h-4 px-1 rounded-full bg-primary text-on-primary text-[10px] font-semibold flex items-center justify-center hidden">0</span></button>
          <div id="bell-panel" class="hidden absolute right-0 mt-2 w-96 max-w-[90vw] bg-surface-container-lowest rounded-xl shadow-2xl border border-outline-variant/30 overflow-hidden fade-in"></div></div>
        <div class="flex items-center gap-2.5 pl-3 border-l border-outline-variant/30">
          <div class="text-right hidden sm:block"><div class="font-label-md text-label-md text-on-surface leading-tight">${esc(u.username)}</div><div class="font-label-sm text-label-sm text-tertiary">${esc(u.email)}</div></div>
          <div class="w-9 h-9 rounded-full bg-primary text-on-primary flex items-center justify-center font-semibold uppercase">${esc(u.username.slice(0, 2))}</div>
          <button id="logout" class="p-2 rounded-full hover:bg-surface-container-high text-on-surface-variant" title="Sign out" aria-label="Sign out">${icon('logout', 'text-[20px]')}</button></div>
      </div>
    </header>
    <main id="view" class="pt-16 min-h-screen"></main>
  </div>`;
  $('#nav').addEventListener('click', e => { const a = e.target.closest('[data-nav]'); if (a) { e.preventDefault(); go(a.dataset.nav); } });
  $('#logout').onclick = logout;
  $('#bell').onclick = e => { e.stopPropagation(); state.notif.open = !state.notif.open; renderBell(); };
  document.addEventListener('click', e => { if (state.notif.open && !e.target.closest('#bell-panel')) { state.notif.open = false; renderBell(); } });
  renderPills(); renderBell(); tickSession();
}

function renderPills() {
  const s = state.status; if (!$('#pill-db')) return;
  const dbOk = s?.database?.online, ai = s?.ai;
  $('#pill-db').innerHTML = `${icon('database', 'text-[18px] text-primary')}<span class="font-code-md text-code-md font-medium truncate max-w-[200px]">PostgreSQL${s?.database?.name ? ' · ' + esc(s.database.name) : ''}</span>
    <span class="inline-flex items-center gap-1 font-label-sm text-label-sm ${dbOk ? 'text-tertiary' : 'text-error'} border-l border-outline-variant/40 pl-2"><span class="w-1.5 h-1.5 rounded-full ${dbOk ? 'bg-emerald-600' : 'bg-error'}"></span>${s ? (dbOk ? 'Online' : 'Offline') : 'Checking'}</span>`;
}

function renderBell() {
  const n = state.notif, dot = $('#bell-dot'), panel = $('#bell-panel'); if (!dot) return;
  dot.textContent = n.unread > 99 ? '99+' : n.unread; dot.classList.toggle('hidden', n.unread === 0);
  panel.classList.toggle('hidden', !n.open);
  if (!n.open) return;
  panel.innerHTML = `<div class="px-4 py-3 flex items-center justify-between border-b border-outline-variant/20"><span class="font-title-md text-title-md">Notifications</span>
      <button id="mark-read" class="text-label-md font-label-md text-primary hover:underline ${n.unread ? '' : 'hidden'}">Mark all read</button></div>
    <div class="max-h-96 overflow-y-auto">${n.items.length ? n.items.map(x => `
      <button data-notif="${x.id}" data-appr="${x.approval_id || ''}" class="w-full text-left px-4 py-3 border-b border-outline-variant/15 hover:bg-surface-container-low ${x.is_read ? '' : 'bg-primary-fixed/30'}">
        <div class="flex items-start gap-2.5">${icon(/rejected|failed/i.test(x.title) ? 'cancel' : /approved/i.test(x.title) ? 'check_circle' : 'notifications_active', 'text-[18px] mt-0.5 text-primary')}
        <div class="flex-1"><div class="font-label-md text-label-md font-semibold">${esc(x.title)}</div><div class="text-body-sm text-on-surface-variant mt-0.5">${esc(x.message)}</div>
        <div class="text-label-sm text-tertiary mt-1">${ago(x.created_at)}</div></div></div></button>`).join('')
      : '<div class="px-4 py-8 text-center text-body-sm text-tertiary">No notifications yet.</div>'}</div>`;
  const mr = $('#mark-read'); if (mr) mr.onclick = async e => { e.stopPropagation(); await api('/api/notifications/read', { method: 'POST', json: {} }); await refreshNotifs(); };
  $$('[data-notif]', panel).forEach(b => b.onclick = async () => {
    await api('/api/notifications/read', { method: 'POST', json: { ids: [+b.dataset.notif] } });
    state.notif.open = false; await refreshNotifs();
    if (['DBA', 'DEVELOPER'].includes(state.user.role)) go('approvals');
  });
}

async function refreshNotifs() {
  if (!state.user) return;
  const r = await api('/api/notifications', { quiet: true });
  if (r.ok) { state.notif.items = r.data.notifications; state.notif.unread = r.data.unread; renderBell(); }
  await refreshPending();
}
async function refreshPending() {
  if (!state.user) return;
  const role = state.user.role; let n = state.pending;
  if (role === 'DBA') { const r = await api('/api/approvals/pending', { quiet: true }); if (r.ok) n = r.data.count; }
  else if (role === 'DEVELOPER') { const r = await api('/api/approvals/mine?status=PENDING', { quiet: true }); if (r.ok) n = r.data.requests.length; }
  if (n !== state.pending) { state.pending = n; if ($('#nav')) $('#nav').innerHTML = navHtml(); }
}
async function refreshStatus() { const r = await api('/api/status', { quiet: true }); if (r.ok) { state.status = r.data; renderPills(); } }

let timerHandle = null;
function tickSession() {
  clearInterval(timerHandle);
  const upd = () => {
    if (!state.expiresAt) return;
    const left = Math.floor((state.expiresAt - Date.now()) / 1000);
    if (left <= 0) { clearInterval(timerHandle); sessionEnded('Your session has expired. Please sign in again.'); return; }
    const el = $('#session-timer'); if (el) el.textContent = `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}`;
  };
  upd(); timerHandle = setInterval(upd, 1000);
}

function go(view) {
  state.view = view; if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  if ($('#nav')) $('#nav').innerHTML = navHtml();
  const v = VIEWS[view] || VIEWS.studio; v();
  window.scrollTo(0, 0);
}

// ───────────────────────────── auth ─────────────────────────────
async function renderLogin(message = '') {
  clearInterval(timerHandle);
  const h = await api('/api/health', { quiet: true });
  const up = h.ok && h.data.database;
  $('#app').innerHTML = `
  <header class="fixed top-0 w-full z-50 bg-surface/85 backdrop-blur-xl shadow-[0_1px_8px_rgba(0,0,0,0.03)]"><div class="h-16 max-w-7xl mx-auto px-6 lg:px-12 flex items-center justify-between">
    <div class="flex items-center gap-space-md">${LOGO}<div class="flex items-baseline gap-space-xs"><span class="font-headline-sm text-headline-sm text-on-surface">AskData Ai</span><span class="font-body-editorial text-body-editorial italic text-on-surface-variant">Studio</span></div></div>
    <div class="flex items-center gap-1.5 bg-surface-container-low px-3 py-1 rounded-full shadow-sm"><span class="w-2 h-2 rounded-full ${up ? 'bg-emerald-600' : 'bg-error'}"></span><span class="font-label-sm text-label-sm text-on-surface-variant">${up ? 'Database online' : 'Database unreachable'}</span></div></div></header>
  <main class="w-full pt-16 bg-surface min-h-screen flex flex-col justify-center"><div class="relative w-full py-8 md:py-14 px-4 sm:px-6 lg:px-8 flex flex-col items-center justify-center overflow-hidden">
    <div class="absolute -top-32 -left-20 w-96 h-96 rounded-full bg-primary-fixed/40 blur-3xl pointer-events-none"></div>
    <div class="absolute -bottom-24 -right-16 w-80 h-80 rounded-full bg-secondary-container/20 blur-3xl pointer-events-none"></div>
    <div class="relative w-full max-w-xl"><div class="w-full bg-surface-container-lowest rounded-2xl shadow-[0_4px_28px_-4px_rgba(43,42,39,0.06),0_1px_4px_0_rgba(43,42,39,0.03)] p-6 sm:p-10">
      <div class="flex flex-col items-center text-center"><div class="w-12 h-12 rounded-xl bg-primary-fixed flex items-center justify-center text-primary mb-4 shadow-sm">${icon('database', 'text-[28px]')}</div>
        <span class="font-label-sm text-label-sm uppercase tracking-widest text-primary font-semibold mb-1">Database Team Portal</span>
        <h1 class="font-headline-lg text-headline-lg text-on-surface mb-2 tracking-tight">Sign in to Database Studio</h1>
        <p class="font-body-md text-body-md text-on-surface-variant max-w-sm">Ask questions in plain English. AI drafts the SQL — you review it, the system validates it, and PostgreSQL runs it.</p></div>
      <div id="login-msg" class="${message ? '' : 'hidden'} mt-6 px-3 py-2.5 rounded-lg bg-error-container/50 text-on-error-container text-body-sm flex gap-2">${icon('error', 'text-[18px]')}<span>${esc(message)}</span></div>
      <form id="login-form" class="mt-8 flex flex-col gap-5" autocomplete="on">
        <div><label class="block font-label-md text-label-md text-on-surface font-medium mb-1.5" for="username">Username or email</label><div class="relative">
          <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-tertiary">${icon('person', 'text-[18px]')}</div>
          <input class="w-full pl-9 pr-3 py-2.5 bg-surface-container-low rounded-lg font-body-md text-body-md text-on-surface placeholder:text-outline focus:outline-none focus:ring-2 focus:ring-primary/25 transition-all" id="username" name="username" autocomplete="username" placeholder="Enter your username or email" required maxlength="255"></div></div>
        <div><label class="block font-label-md text-label-md text-on-surface font-medium mb-1.5" for="password">Password</label><div class="relative">
          <div class="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none text-tertiary">${icon('lock', 'text-[18px]')}</div>
          <input class="w-full pl-9 pr-10 py-2.5 bg-surface-container-low rounded-lg font-body-md text-body-md text-on-surface placeholder:text-outline focus:outline-none focus:ring-2 focus:ring-primary/25 transition-all" id="password" name="password" type="password" autocomplete="current-password" placeholder="Enter your password" required maxlength="256">
          <button class="absolute inset-y-0 right-0 pr-3 flex items-center text-tertiary hover:text-on-surface" id="toggle-pw" type="button" aria-label="Show password">${icon('visibility', 'text-[18px]')}</button></div></div>
        <button id="login-btn" class="w-full mt-2 py-3 px-4 rounded-lg bg-primary hover:bg-secondary text-on-primary font-title-md text-title-md shadow-sm hover:shadow-md flex items-center justify-center gap-2 transition-all" type="submit"><span>Sign In</span>${icon('arrow_forward', 'text-[18px]')}</button>
      </form>
      <div class="mt-8 pt-5 -mx-6 -mb-6 sm:-mx-10 sm:-mb-10 p-4 sm:px-10 rounded-b-2xl bg-surface-container-low/50 flex items-center gap-2 text-tertiary font-label-sm text-label-sm">${icon('security', 'text-[16px] text-primary')}Role-based access · DBA-approved changes · Every action audited</div>
    </div></div></div></main>`;
  $('#toggle-pw').onclick = () => { const p = $('#password'); p.type = p.type === 'password' ? 'text' : 'password'; };
  $('#login-form').onsubmit = async e => {
    e.preventDefault();
    const btn = $('#login-btn'); btn.disabled = true; btn.innerHTML = `${spinner}<span>Signing in…</span>`;
    const r = await api('/api/auth/login', { method: 'POST', json: { username: $('#username').value.trim(), password: $('#password').value }, quiet: true });
    if (r.ok) { $('#password').value = ''; await boot(); return; }
    btn.disabled = false; btn.innerHTML = `<span>Sign In</span>${icon('arrow_forward', 'text-[18px]')}`;
    const m = $('#login-msg'); m.classList.remove('hidden'); m.querySelector('span:last-child').textContent = r.data.message || 'Sign in failed.';
  };
}

function sessionEnded(msg) {
  if (!state.user) return;
  state.user = null; clearInterval(timerHandle); clearInterval(pollHandle); clearInterval(statusHandle);
  resetClientState();
  renderLogin(msg || 'Your session has ended. Please sign in again.');
}
async function logout() {
  await api('/api/auth/logout', { method: 'POST', json: {}, quiet: true });
  state.user = null; clearInterval(timerHandle); clearInterval(pollHandle); clearInterval(statusHandle);
  resetClientState();
  renderLogin();
}

let pollHandle = null, statusHandle = null;
async function boot() {
  const me = await api('/api/auth/me', { quiet: true });
  if (!me.ok) { renderLogin(); return; }
  state.user = me.data.user; state.perms = me.data.permissions || {};
  state.expiresAt = new Date(me.data.session_expires_at).getTime();
  state.status = null; state.pending = 0;
  renderShell();
  const wanted = location.hash.slice(1);
  state.view = VIEWS[wanted] && NAV().some(g => g.items.some(i => i.id === wanted)) ? wanted : 'studio';
  go(state.view);
  refreshStatus(); refreshNotifs();
  clearInterval(pollHandle);
  pollHandle = setInterval(() => { refreshNotifs(); if (state.view === 'approvals') loadApprovals(false); }, 20000);
  clearInterval(statusHandle); statusHandle = setInterval(refreshStatus, 30000);
}

// ───────────────────────────── shared UI bits ─────────────────────────────
const card = (inner, cls = '') => `<section class="bg-surface-container-lowest rounded-xl shadow-[0_1px_8px_rgba(43,42,39,0.05)] border border-outline-variant/20 ${cls}">${inner}</section>`;
const pageHead = (kicker, title, sub, right = '') => `<div class="flex flex-wrap items-end justify-between gap-4 mb-6"><div>
  ${kicker ? `<div class="font-label-sm text-label-sm uppercase tracking-wider text-primary font-semibold mb-1">${esc(kicker)}</div>` : ''}
  <h1 class="font-headline-lg text-headline-lg text-on-surface">${esc(title)}</h1>${sub ? `<p class="text-on-surface-variant mt-1 max-w-2xl">${sub}</p>` : ''}</div>${right}</div>`;
const chip = (text, tone = 'neutral') => {
  const t = {
    neutral: 'bg-surface-container-high text-on-surface-variant', primary: 'bg-primary-fixed text-on-primary-fixed-variant', warn: 'bg-secondary-container/30 text-on-secondary-container',
    error: 'bg-error-container text-on-error-container', ok: 'bg-emerald-100 text-emerald-900'
  }[tone];
  return `<span class="inline-flex items-center px-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${t}">${esc(text)}</span>`;
};
const statusTone = s => ({ PENDING: 'warn', APPROVED: 'primary', EXECUTED: 'ok', REJECTED: 'error', FAILED: 'error', SUCCESS: 'ok', DENIED: 'error', INVALID: 'error', LOCKED: 'error' }[s] || 'neutral');
const EMPTY = (ic, t, s = '') => `<div class="py-14 text-center text-tertiary"><div class="mx-auto w-12 h-12 rounded-full bg-surface-container flex items-center justify-center mb-3">${icon(ic, 'text-[24px]')}</div><div class="font-title-md text-title-md text-on-surface-variant">${esc(t)}</div>${s ? `<div class="text-body-sm mt-1">${esc(s)}</div>` : ''}</div>`;
const container = html => { const v = $('#view'); if (!v) return; v.innerHTML = `<div class="p-4 lg:p-8 max-w-[1400px] mx-auto fade-in">${html}</div>`; };
function dataTable(columns, rows) {
  return `<div class="scroll-x max-h-[480px]"><table class="data-table"><thead><tr>${columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
  <tbody>${rows.map(r => `<tr>${r.map(v => `<td class="${typeof v === 'number' ? 'text-right tabular-nums' : ''}">${v === null ? '<span class="text-tertiary">NULL</span>' : esc(typeof v === 'object' ? JSON.stringify(v) : v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

// ───────────────────────────── AI Query Studio ─────────────────────────────
const EXAMPLES = ['Show all employees in the Development department', 'How many employees are in each department?',
  'Show each project with its department name', 'List the 5 highest paid employees', 'Change employee EMP001 salary to 60000'];
const FILE_EXAMPLES = ['Update the employees in the file', 'Remove the employees listed in the file',
  'Add the employees in the file', 'Show the employees from the file'];

function studioStatus(g) {
  const s = state.studio;
  if (!g) return null;
  if (g.needs_clarification) return { tone: 'warn', label: 'Clarification needed', ic: 'help' };
  if (g.manual && !s.sql.trim()) return { tone: 'warn', label: 'Type SQL to validate it', ic: 'edit' };
  if (g.permission_denied) return { tone: 'error', label: 'Permission denied', ic: 'block' };
  if (!g.valid) return { tone: 'error', label: 'Validation failed', ic: 'gpp_bad' };
  if (g.requires_dba_approval) return { tone: 'warn', label: 'DBA Approval Required', ic: 'gavel' };
  if (g.dangerous) return { tone: 'warn', label: 'Dangerous — confirmation required', ic: 'warning' };
  return { tone: 'ok', label: 'Ready — review before executing', ic: 'task_alt' };
}

function pipelineHtml() {
  const s = state.studio, g = s.gen;
  const approval = g && g.valid && g.requires_dba_approval;
  const steps = [
    ['Natural Language', 'chat', s.nl.trim() || s.attach ? 'pipe-done' : 'pipe-active'],
    ['AI Generates SQL', 'auto_awesome', g && !g.needs_clarification ? 'pipe-done' : s.busy ? 'pipe-active' : 'pipe-idle'],
    ['Review & Validate', 'rule', g && !g.needs_clarification ? (s.result || s.ticket ? 'pipe-done' : 'pipe-active') : 'pipe-idle'],
    ['DBA Gate', 'gavel', !g ? 'pipe-idle' : approval ? (s.ticket ? 'pipe-done' : 'pipe-active') : 'pipe-skip'],
    ['PostgreSQL Commit', 'database', s.result ? 'pipe-done' : 'pipe-idle'],
  ];
  return `<div class="flex flex-wrap items-center gap-2 text-label-sm font-label-sm">${steps.map(([l, ic, c], i) =>
    `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full ${c}">${icon(ic, 'text-[14px]')}${l}</span>${i < steps.length - 1 ? icon('chevron_right', 'text-[16px] text-outline') : ''}`).join('')}</div>`;
}

function attachHtml() {
  const s = state.studio, a = s.attach;
  if (s.attachBusy) return `<div class="flex items-center gap-2 text-body-sm text-on-surface-variant">${spinner}<span>Reading file…</span></div>`;
  if (!a) return `<button id="attach-btn" type="button" class="px-3 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface text-label-md font-label-md flex items-center gap-1.5">${icon('attach_file', 'text-[18px]')}Attach file</button>
    <span class="text-label-sm text-tertiary">.xlsx · .xls · .csv — e.g. a list of employees to update or remove</span>`;
  return `<div class="flex items-center gap-3 p-3 rounded-lg bg-primary-fixed/40 border border-primary/20 w-full">
    <div class="w-9 h-9 rounded-lg bg-surface-container-lowest text-primary flex items-center justify-center shrink-0">${icon('description', 'text-[20px]')}</div>
    <div class="min-w-0 flex-1"><div class="font-label-md text-label-md font-semibold truncate" title="${esc(a.filename)}">${esc(a.filename)}</div>
      <div class="text-label-sm text-on-surface-variant truncate">${a.row_count} rows · columns: ${a.columns.map(c => `<code class="font-code-md">${esc(c)}</code>`).join(', ')}</div></div>
    <button id="attach-preview" type="button" class="px-2.5 py-1 rounded-lg hover:bg-surface-container-lowest text-primary text-label-md font-label-md">Preview</button>
    <button id="attach-remove" type="button" aria-label="Remove attached file" title="Remove file" class="p-1.5 rounded-full hover:bg-surface-container-lowest text-on-surface-variant">${icon('close', 'text-[18px]')}</button></div>`;
}
const chipsHtml = () => (state.studio.attach ? FILE_EXAMPLES : EXAMPLES).map(e =>
  `<button type="button" data-ex="${esc(e)}" class="px-2.5 py-1 rounded-full bg-surface-container hover:bg-surface-container-high text-on-surface-variant text-label-sm font-label-sm text-left">${esc(e)}</button>`).join('');

// ---- Voice input (browser Web Speech API) ----
let micRec = null;
function stopMic() { if (micRec) { try { micRec.stop(); } catch (_) { } } }
function bindMic() {
  const btn = $('#mic-btn'); if (!btn) return;
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    btn.disabled = true; btn.title = 'Voice input is not supported in this browser (try Chrome, Edge or Safari)';
    return;
  }
  const s = state.studio, nl = $('#nl');
  const setUi = on => {
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.classList.toggle('mic-on', on);
    btn.title = on ? 'Stop dictation' : 'Dictate with microphone';
    btn.firstElementChild.textContent = on ? 'stop' : 'mic';
  };
  btn.onclick = () => {
    if (micRec) { stopMic(); return; }
    const rec = new SR();
    rec.lang = navigator.language || 'en-US';
    rec.interimResults = true; rec.continuous = true;
    const base = nl.value, sep = base && !/\s$/.test(base) ? ' ' : '';
    let finalText = '';
    const apply = interim => {
      s.nl = (base + sep + finalText + interim).slice(0, 1000);
      nl.value = s.nl;
      const pl = $('#pipeline'); if (pl) pl.innerHTML = pipelineHtml();
    };
    rec.onresult = e => {
      let interim = '';
      for (let k = e.resultIndex; k < e.results.length; k++) {
        const r = e.results[k];
        if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript;
      }
      apply(interim);
    };
    rec.onerror = e => {
      const m = {
        'not-allowed': 'Microphone access was blocked. Allow it in your browser settings.',
        'service-not-allowed': 'Microphone access was blocked. Allow it in your browser settings.',
        'no-speech': 'No speech detected. Try again.', 'audio-capture': 'No microphone found.',
        'network': 'Speech recognition needs a network connection.'
      }[e.error];
      if (m) toast(m, 'error');
    };
    rec.onend = () => { micRec = null; if (btn.isConnected) { setUi(false); apply(''); nl.focus(); } };
    try { rec.start(); micRec = rec; setUi(true); } catch (_) { micRec = null; setUi(false); }
  };
}

function bindAttach() {
  const s = state.studio, ab = $('#attach-btn');
  if (ab) ab.onclick = () => $('#attach-file').click();
  const rm = $('#attach-remove'); if (rm) rm.onclick = removeAttachment;
  const pv = $('#attach-preview'); if (pv) pv.onclick = previewAttachment;
  $$('[data-ex]').forEach(b => b.onclick = () => { s.nl = b.dataset.ex; $('#nl').value = s.nl; $('#nl').focus(); const pl = $('#pipeline'); if (pl) pl.innerHTML = pipelineHtml(); });
}
function renderAttach() {
  const a = $('#attach-area'); if (!a) return;
  a.innerHTML = attachHtml(); $('#example-chips').innerHTML = chipsHtml();
  $('#nl').placeholder = state.studio.attach ? 'e.g. Update the salary of the employees in the file' : 'Ask your database question…';
  const pl = $('#pipeline'); if (pl) pl.innerHTML = pipelineHtml(); bindAttach();
}

async function attachFile(file) {
  const s = state.studio; if (!file) return;
  if (file.size > 5 * 1024 * 1024) { toast('The file is too large (maximum 5 MB).', 'error'); return; }
  s.attachBusy = true; renderAttach();
  const fd = new FormData(); fd.append('file', file);
  const r = await api('/api/attachments', { method: 'POST', form: fd });
  s.attachBusy = false;
  if (r.ok) { s.attach = r.data; toast(`${r.data.row_count} rows attached. Now tell me what to do with them.`, 'success'); }
  else toast(r.data.message || 'The file could not be attached.', 'error');
  renderAttach();
}
async function removeAttachment() {
  const s = state.studio, id = s.attach?.attachment_id; s.attach = null; renderAttach();
  if (id) await api(`/api/attachments/${encodeURIComponent(id)}`, { method: 'DELETE' });
  toast('File removed.', 'info');
}
function infoModal(title, body) {
  const root = $('#modal-root');
  root.innerHTML = `<div class="modal-backdrop"><div class="bg-surface-container-lowest rounded-2xl shadow-2xl max-w-5xl w-full p-6 fade-in" role="dialog" aria-modal="true">
    <div class="flex items-center justify-between mb-4"><h3 class="font-headline-sm text-headline-sm">${esc(title)}</h3><button id="im-close" class="p-1.5 rounded-full hover:bg-surface-container" aria-label="Close">${icon('close')}</button></div>${body}</div></div>`;
  const close = () => { root.innerHTML = ''; };
  $('#im-close').onclick = close; $('.modal-backdrop').addEventListener('click', e => { if (e.target.classList.contains('modal-backdrop')) close(); });
}
async function previewAttachment() {
  const id = state.studio.attach?.attachment_id; if (!id) return;
  const r = await api(`/api/attachments/${encodeURIComponent(id)}`);
  if (!r.ok) { toast(r.data.message || 'Could not load the file.', 'error'); if (r.status === 404) { state.studio.attach = null; renderAttach(); } return; }
  infoModal(`${r.data.filename} — ${r.data.row_count} rows`, dataTable(r.data.columns, r.data.rows.map(row => r.data.columns.map(c => row[c]))));
}

function studioRender() {
  const s = state.studio;
  container(`
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-6">
      <div class="xl:col-span-5 space-y-6">
        ${card(`<div class="p-5" id="nl-card"><div class="flex items-center gap-2 mb-3">${icon('psychology', 'text-primary')}<h2 class="font-headline-sm text-headline-sm">Natural Language Intent</h2></div>
          <div class="relative"><textarea id="nl" rows="5" maxlength="1000" class="w-full bg-surface-container-low rounded-lg p-3 pr-14 font-body-editorial text-[17px] leading-7 text-on-surface placeholder:text-outline focus:outline-none focus:ring-2 focus:ring-primary/25 resize-y" placeholder="${s.attach ? 'e.g. Update the salary of the employees in the file' : 'Ask your database question…'}">${esc(s.nl)}</textarea><button id="mic-btn" type="button" aria-pressed="false" aria-label="Dictate with microphone" title="Dictate with microphone" class="mic-btn absolute right-2 bottom-2 w-10 h-10 rounded-full flex items-center justify-center bg-surface-container hover:bg-surface-container-high text-on-surface-variant transition-colors">${icon('mic', 'text-[22px]')}</button></div>
          <div id="attach-area" class="mt-3 flex flex-wrap items-center gap-3">${attachHtml()}</div>
          <input id="attach-file" type="file" accept=".xlsx,.xls,.csv" class="sr-only" tabindex="-1"/>
          <div id="example-chips" class="mt-3 flex flex-wrap gap-1.5">${chipsHtml()}</div>
          <div class="mt-4 flex flex-wrap items-center justify-between gap-3"><div class="flex items-center gap-3"><span class="text-label-sm text-tertiary">Ctrl + Enter to generate</span>
            <button id="manual-btn" type="button" class="text-primary font-label-md text-label-md hover:underline">Write SQL manually</button></div>
          <button id="gen-btn" class="px-5 py-2.5 rounded-lg bg-primary hover:bg-secondary text-on-primary font-title-md text-title-md shadow-sm flex items-center gap-2 transition-all">${icon('auto_awesome', 'text-[18px]')}<span>Generate SQL</span></button></div></div>`)}
        ${s.gen && s.gen.tables_considered ? card(`<div class="p-5"><div class="flex items-center gap-2 mb-2">${icon('account_tree', 'text-primary')}<h2 class="font-headline-sm text-headline-sm">Schema Context</h2></div>
          <p class="text-body-sm text-on-surface-variant mb-2">Only these tables were sent to the model:</p>
          <div class="flex flex-wrap gap-1.5">${s.gen.tables_considered.map(t => `<span class="px-2 py-1 rounded bg-surface-container font-code-md text-code-md">${esc(t)}</span>`).join('')}</div></div>`) : ''}
      </div>
      <div class="xl:col-span-7 space-y-6" id="studio-right"></div>
    </div>`);
  $('#nl').oninput = e => { s.nl = e.target.value; const pl = $('#pipeline'); if (pl) pl.innerHTML = pipelineHtml(); };
  $('#nl').onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') generate(); };
  bindMic();
  $('#attach-file').onchange = e => { const f = e.target.files[0]; e.target.value = ''; attachFile(f); };
  const nlCard = $('#nl-card');
  nlCard.addEventListener('dragover', e => { e.preventDefault(); nlCard.classList.add('ring-2', 'ring-primary/30'); });
  nlCard.addEventListener('dragleave', () => nlCard.classList.remove('ring-2', 'ring-primary/30'));
  nlCard.addEventListener('drop', e => { e.preventDefault(); nlCard.classList.remove('ring-2', 'ring-primary/30'); if (e.dataTransfer.files[0]) attachFile(e.dataTransfer.files[0]); });
  $('#gen-btn').onclick = generate;
  $('#manual-btn').onclick = startManual;
  bindAttach();
  studioRight();
}

function resultHtml() {
  const s = state.studio, r = s.result; if (!r) return '';
  const list = r.results || [];
  const rowSets = list.filter(x => x.has_rows);
  const tab = Math.min(s.tab, Math.max(0, rowSets.length - 1)), cur = rowSets[tab];
  const summary = list.length > 1 || !rowSets.length ? `<details class="mt-3 text-body-sm"><summary class="text-primary font-label-md text-label-md hover:underline">Per-query summary (${list.length})</summary><div class="mt-2">${dataTable(['#', 'Command', 'Result', 'SQL'], list.slice(0, 100).map(x => [x.index, x.command || '', x.has_rows ? `${x.row_count} rows` : `${x.row_count} affected`, x.sql.slice(0, 140)]))}${list.length > 100 ? `<p class="text-label-sm text-tertiary mt-1">Showing the first 100 of ${list.length}.</p>` : ''}</div></details>` : '';
  const downloads = rowSets.length ? `<div class="flex items-center gap-1.5">
      <button id="dl-csv" class="px-3 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface text-label-md font-label-md flex items-center gap-1.5">${icon('download', 'text-[16px]')}CSV</button>
      <button id="dl-xlsx" class="px-3 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface text-label-md font-label-md flex items-center gap-1.5">${icon('download', 'text-[16px]')}Excel${rowSets.length > 1 ? ' (all ' + rowSets.length + ' results)' : ''}</button></div>` : '';
  return card(`<div class="p-5"><div class="flex flex-wrap items-center justify-between gap-2 mb-3"><div class="flex items-center gap-2">${icon('table_view', 'text-primary')}<h2 class="font-headline-sm text-headline-sm">Result</h2></div>
      <div class="flex flex-wrap gap-2 items-center">${chip(r.message, 'ok')}${r.truncated ? chip('Truncated — row limit reached', 'warn') : ''}${downloads}</div></div>
    ${rowSets.length > 1 ? `<div class="flex gap-1 mb-3 p-1 bg-surface-container rounded-lg overflow-x-auto">${rowSets.map((x, i) => `<button data-rtab="${i}" class="px-3 py-1 rounded-md text-label-md font-label-md whitespace-nowrap ${i === tab ? 'bg-surface-container-lowest shadow-sm text-primary font-semibold' : 'text-on-surface-variant'}">Query ${x.index} · ${x.row_count} rows</button>`).join('')}</div>` : ''}
    ${cur ? `${rowSets.length > 1 ? `<div class="mb-2">${sqlBlock(prettySql(cur.sql), { max: 6 })}</div>` : ''}${dataTable(cur.columns, cur.rows)}` :
      `<div class="px-4 py-3 rounded-lg bg-emerald-50 text-emerald-900 flex gap-2">${icon('check_circle', 'text-[18px]')}<span>${esc(r.message)}</span></div>`}
    ${summary}
    <details class="mt-3 text-body-sm text-tertiary"><summary class="hover:text-on-surface-variant cursor-pointer">Executed SQL (as validated)</summary><div class="mt-2">${sqlBlock(prettySql(r.executed_sql))}</div></details></div>`);
}

function studioRight() {
  const s = state.studio, g = s.gen, box = $('#studio-right'); if (!box) return;
  const st = studioStatus(g);
  const toneCls = { ok: 'bg-emerald-100 text-emerald-900', warn: 'bg-secondary-container/30 text-on-secondary-container', error: 'bg-error-container text-on-error-container' };
  let sqlCard;
  if (!g) {
    sqlCard = card(`<div class="p-5"><div class="flex items-center gap-2 mb-3">${icon('code', 'text-primary')}<h2 class="font-headline-sm text-headline-sm">Generated SQL</h2></div>${EMPTY('terminal', 'No SQL yet', 'Ask a question (optionally with an attached file) and press Generate SQL — or write SQL yourself.')}</div>`);
  } else if (g.needs_clarification) {
    sqlCard = card(`<div class="p-5"><div class="flex items-center gap-2 mb-3">${icon('help', 'text-primary')}<h2 class="font-headline-sm text-headline-sm">The model needs more detail</h2></div>
      <div class="px-4 py-3 rounded-lg bg-secondary-container/20 text-on-secondary-container">${esc(g.message)}</div><p class="text-body-sm text-tertiary mt-3">Refine your request on the left and generate again.</p></div>`);
  } else {
    const n = g.statement_count || 1;
    const canRun = g.valid && !s.running && !s.ticket && s.sql.trim();
    sqlCard = card(`<div class="p-5">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-3"><div class="flex items-center gap-2">${icon('code', 'text-primary')}<h2 class="font-headline-sm text-headline-sm">${g.manual ? 'Your SQL' : 'Generated SQL'}</h2>
        <span class="px-2 py-0.5 rounded-full bg-surface-container-high font-code-md text-code-md text-on-surface-variant">PostgreSQL</span>${n > 1 ? chip(n + ' queries', 'primary') : ''}</div>
        <div class="flex items-center gap-1">
          <button id="copy-sql" class="px-2.5 py-1 rounded-lg hover:bg-surface-container text-on-surface-variant text-label-md font-label-md flex items-center gap-1">${icon('content_copy', 'text-[16px]')}Copy</button>
          <button id="edit-sql" class="px-2.5 py-1 rounded-lg hover:bg-surface-container text-on-surface-variant text-label-md font-label-md flex items-center gap-1">${icon(s.editing ? 'visibility' : 'edit', 'text-[16px]')}${s.editing ? 'Preview' : 'Edit'}</button></div></div>
      ${g.batch ? `<div class="mb-3 px-4 py-3 rounded-lg bg-primary-fixed/40 text-on-primary-fixed-variant text-body-sm"><div class="font-semibold flex items-center gap-1.5">${icon('table_rows', 'text-[18px]')}Runs once for each of the ${g.batch.rows} rows in “${esc(g.batch.filename)}”</div>
          <div class="mt-1">The AI wrote this template; values come from the file (${g.batch.columns.map(c => `<code class="font-code-md">${esc(c)}</code>`).join(', ')}). All rows run in one all-or-nothing transaction.</div><div class="mt-2">${sqlBlock(g.batch.template, { max: 6 })}</div></div>` :
        n > 1 ? `<div class="mb-3 px-4 py-2.5 rounded-lg bg-surface-container-low text-on-surface-variant text-body-sm flex gap-2">${icon('layers', 'text-[18px]')}<span>These ${n} queries run together as <b>one all-or-nothing transaction</b>. If any query fails, none are saved.</span></div>` : ''}
      ${g.manual && s.editing ? `<p class="text-label-sm text-tertiary mb-2">Separate several queries with a semicolon (;).</p>` : ''}
      ${s.editing ? `<textarea id="sql-edit" class="sql-edit" spellcheck="false" placeholder="SELECT * FROM employees;">${esc(s.sql)}</textarea>` : sqlBlock(s.sql, { max: 14 })}
      <div class="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div class="p-3 rounded-lg bg-surface-container-low"><div class="text-label-sm uppercase tracking-wider text-tertiary">Operation</div><div class="font-title-md text-title-md mt-0.5">${esc(g.operation_type || '—')}${n > 1 ? ` <span class="text-body-sm text-tertiary">· ${n} queries</span>` : ''}</div></div>
        <div class="p-3 rounded-lg bg-surface-container-low"><div class="text-label-sm uppercase tracking-wider text-tertiary">Target table</div><div class="font-code-md text-code-md mt-1 truncate" title="${esc(g.target_table || '')}">${esc(g.target_table || '—')}</div></div>
        <div class="p-3 rounded-lg ${toneCls[st.tone]}"><div class="text-label-sm uppercase tracking-wider opacity-80">Status</div><div class="font-label-md text-label-md font-semibold mt-1 flex items-center gap-1">${icon(st.ic, 'text-[16px]')}<span>${esc(st.label)}</span></div></div></div>
      ${!g.valid && g.validation_errors?.length ? `<div class="mt-4 px-4 py-3 rounded-lg bg-error-container/60 text-on-error-container text-body-sm"><div class="font-semibold flex items-center gap-1.5 mb-1">${icon('gpp_bad', 'text-[18px]')}The generated SQL failed validation.</div><ul class="list-disc pl-5 space-y-0.5">${g.validation_errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
      ${g.valid && g.danger_reasons?.length ? `<div class="mt-4 px-4 py-3 rounded-lg bg-secondary-container/25 text-on-secondary-container text-body-sm"><div class="font-semibold flex items-center gap-1.5 mb-1">${icon('warning', 'text-[18px]')}Dangerous operation</div><ul class="list-disc pl-5 space-y-0.5">${g.danger_reasons.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div>` : ''}
      ${g.valid && g.requires_dba_approval && !s.ticket ? `<div class="mt-4 px-4 py-3 rounded-lg bg-primary-fixed/50 text-on-primary-fixed-variant text-body-sm flex gap-2">${icon('gavel', 'text-[18px] mt-0.5')}<div><b>DBA approval required.</b> This will not touch PostgreSQL. Proceeding sends ${n > 1 ? 'all ' + n + ' queries' : 'it'} to a DBA, who can approve or reject. Only an approved request is executed.</div></div>` : ''}
      <div class="mt-5 flex flex-wrap items-center justify-end gap-2">
        <button id="clear-sql" class="px-4 py-2 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface text-label-md font-label-md">Clear</button>
        <button id="run-btn" ${canRun ? '' : 'disabled'} class="px-5 py-2.5 rounded-lg bg-primary hover:bg-secondary text-on-primary font-title-md text-title-md shadow-sm flex items-center gap-2 transition-all">${s.running ? spinner : icon(g.requires_dba_approval ? 'send' : 'play_arrow', 'text-[18px] fill-1')}<span>Proceed &amp; Execute${n > 1 ? ` (${n} queries)` : ''}</span></button></div></div>`);
  }
  const ticket = s.ticket ? card(`<div class="p-5 flex gap-3"><div class="w-10 h-10 rounded-xl bg-secondary-container/30 text-on-secondary-container flex items-center justify-center shrink-0">${icon('hourglass_top', 'fill-1')}</div>
    <div class="flex-1"><h3 class="font-headline-sm text-headline-sm">DBA Approval Required</h3><p class="text-on-surface-variant mt-1">${esc(s.ticket.message)}</p>
    <div class="mt-3 flex flex-wrap gap-2 items-center">${chip('Ticket #' + s.ticket.approval_id, 'primary')}${chip(s.ticket.status, statusTone(s.ticket.status))}${chip(s.ticket.operation_type)}
    <a href="#approvals" data-nav="approvals" class="text-primary font-label-md text-label-md hover:underline">Track in My Change Requests →</a></div></div></div>`) : '';
  const err = s.error ? card(`<div class="p-5 flex gap-3"><div class="w-10 h-10 rounded-xl bg-error-container text-on-error-container flex items-center justify-center shrink-0">${icon('error', 'fill-1')}</div>
    <div><h3 class="font-headline-sm text-headline-sm">${esc(s.error.message)}</h3>${s.error.errors?.length ? `<ul class="list-disc pl-5 mt-1 text-on-surface-variant">${s.error.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}</div></div>`) : '';
  box.innerHTML = sqlCard + ticket + err + resultHtml();
  const pl = $('#pipeline'); if (pl) pl.innerHTML = pipelineHtml();

  if (g && !g.needs_clarification) {
    $('#copy-sql').onclick = () => { navigator.clipboard?.writeText(s.sql); toast('SQL copied.', 'success'); };
    $('#edit-sql').onclick = () => { s.editing = !s.editing; studioRight(); };
    $('#clear-sql').onclick = () => { Object.assign(s, { gen: null, sql: '', result: null, ticket: null, error: null, editing: false }); studioRender(); };
    $('#run-btn').onclick = () => executeSql(false);
    const ed = $('#sql-edit');
    if (ed) { let t; ed.oninput = e => { s.sql = e.target.value; clearTimeout(t); t = setTimeout(revalidate, 500); }; if (g.manual && !s.sql) ed.focus(); }
  }
  $$('[data-nav]', box).forEach(a => a.onclick = e => { e.preventDefault(); go(a.dataset.nav); });
  $$('[data-rtab]', box).forEach(b => b.onclick = () => { s.tab = +b.dataset.rtab; studioRight(); });
  const c = $('#dl-csv'); if (c) c.onclick = () => downloadResult('csv');
  const x = $('#dl-xlsx'); if (x) x.onclick = () => downloadResult('xlsx');
}

function startManual() {
  const s = state.studio;
  Object.assign(s, {
    gen: { manual: true, valid: false, validation_errors: [], operation_type: '—', needs_clarification: false, tables_considered: null, statement_count: 0 },
    sql: '', editing: true, result: null, ticket: null, error: null
  });
  studioRender();
}

async function generate() {
  stopMic();
  const s = state.studio; const q = s.nl.trim();
  if (q.length < 3) { toast('Please describe what you need (at least 3 characters).', 'warn'); return; }
  const btn = $('#gen-btn'); if (btn) { btn.disabled = true; btn.innerHTML = `${spinner}<span>Generating…</span>`; } s.busy = true;
  Object.assign(s, { result: null, ticket: null, error: null });
  const r = await api('/api/query/generate', { method: 'POST', json: { question: q, attachment_id: s.attach?.attachment_id || null } });
  s.busy = false;
  if (!state.user || state.studio !== s) return;          // signed out (or a different user) while the model was thinking
  if (!r.ok) {
    s.gen = null; s.error = { message: r.data.message || 'Could not generate SQL.' };
    if (r.status === 404 && s.attach) { s.attach = null; }
  } else { s.gen = r.data; s.sql = r.data.sql ? prettySql(r.data.sql) : ''; s.editing = false; s.tab = 0; }
  if (state.view === 'studio') studioRender();           // never repaint the Studio over another page
}

async function revalidate() {
  const s = state.studio; if (!s.gen) return;
  if (!s.sql.trim()) { Object.assign(s.gen, { valid: false, validation_errors: [], statement_count: 0, operation_type: '—', target_table: null, requires_dba_approval: false, danger_reasons: [], dangerous: false, permission_denied: false }); studioRight(); return; }
  const r = await api('/api/query/validate', { method: 'POST', json: { sql: s.sql } });
  if (!r.ok) return;
  const d = r.data;
  Object.assign(s.gen, {
    valid: d.valid, validation_errors: d.errors, operation_type: d.operation_type, target_table: d.target_table, statement_count: d.statement_count,
    requires_dba_approval: d.requires_dba_approval, dangerous: d.dangerous, danger_reasons: d.danger_reasons, permission_denied: !!d.permission_denied, needs_clarification: false, batch: null
  });
  s.result = null; s.ticket = null; s.error = null;
  const keep = $('#sql-edit'); const pos = keep?.selectionStart;
  studioRight();
  const again = $('#sql-edit'); if (again) { again.focus(); try { again.setSelectionRange(pos, pos); } catch { /* ignore */ } }
}

async function executeSql(confirmDangerous) {
  const s = state.studio; s.running = true; s.error = null; studioRight();
  const note = s.gen?.batch ? `\n[Attached file: ${s.gen.batch.filename}, ${s.gen.batch.rows} rows]` : '';
  const question = ((s.nl.trim() + note).trim() || null)?.slice(0, 1000) ?? null;
  const r = await api('/api/query/execute', { method: 'POST', json: { sql: s.sql, question, confirm_dangerous: confirmDangerous } });
  s.running = false; const d = r.data;
  if (!state.user || state.studio !== s) return;
  if (r.status === 409 && d.requires_dangerous_confirmation) {
    const ok = await confirmModal({
      title: 'Confirm dangerous operation', tone: 'danger', confirmText: 'Yes, execute it',
      body: `<p>${esc(d.message)}</p><ul class="list-disc pl-5 mt-2">${(d.danger_reasons || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul><p class="mt-2">This cannot be undone.</p>`
    });
    if (ok) return executeSql(true);
    studioRight(); return;
  }
  if (r.status === 202 && d.approval_required) { s.ticket = d; s.result = null; toast('Sent to the DBA for approval.', 'success'); refreshNotifs(); }
  else if (r.ok && d.success) { s.result = d; s.ticket = null; s.tab = 0; toast(d.message, 'success'); refreshStatus(); }
  else { s.error = { message: d.message || 'The request failed.', errors: d.errors }; s.result = null; }
  studioRight();
}

async function downloadResult(fmt) {
  const s = state.studio, rowSets = (s.result?.results || []).filter(x => x.has_rows);
  if (!rowSets.length) { toast('There are no rows to download.', 'warn'); return; }
  const picked = fmt === 'csv' ? [rowSets[Math.min(s.tab, rowSets.length - 1)]] : rowSets.slice(0, 20);
  if (fmt === 'xlsx' && rowSets.length > 20) toast('Excel download includes the first 20 results.', 'warn');
  let resp;
  try {
    resp = await fetch('/api/export', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
      body: JSON.stringify({ format: fmt, sheets: picked.map(x => ({ name: `Query ${x.index}`, columns: x.columns, rows: x.rows })) })
    });
  } catch { toast('Cannot reach the server. Please try again later.', 'error'); return; }
  if (!resp.ok) {
    let data = {}; try { data = await resp.json(); } catch { /* ignore */ }
    if (resp.status === 401) { sessionEnded(data.message); return; }
    toast(data.message || 'The download failed.', 'error'); return;
  }
  const blob = await resp.blob();
  const name = (resp.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || `query_results.${fmt}`;
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
  toast(`Downloaded ${name}`, 'success');
}

// ───────────────────────────── Schema & Explorer ─────────────────────────────
async function viewSchema() {
  container(pageHead('Core AI Studio', 'Schema & Explorer', 'Live structure of the PostgreSQL database, limited to what your role may see.') + EMPTY('hourglass_top', 'Loading schema…'));
  const r = await api('/api/schema'); if (!r.ok) { container(EMPTY('error', r.data.message || 'Could not load the schema.')); return; }
  container(pageHead('Core AI Studio', 'Schema & Explorer', 'Live structure of the PostgreSQL database, limited to what your role may see.') +
    `<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">${r.data.tables.map(t => card(`<div class="p-5">
      <div class="flex items-center justify-between"><div class="flex items-center gap-2">${icon(t.is_view ? 'visibility' : 'table', 'text-primary')}<h2 class="font-code-md text-[15px] font-medium">${esc(t.table)}</h2></div>
        <button data-ask="${esc(t.table)}" class="text-primary font-label-md text-label-md hover:underline">Ask about this table →</button></div>
      <div class="mt-3 scroll-x"><table class="data-table"><thead><tr><th>Column</th><th>Type</th><th>Null</th><th>Key</th></tr></thead><tbody>${t.columns.map(c => {
      const fk = t.foreign_keys.find(f => f.column === c.name);
      return `<tr><td class="font-code-md">${esc(c.name)}</td><td class="text-on-surface-variant">${esc(c.type)}</td><td>${c.nullable ? 'yes' : 'no'}</td>
          <td>${t.primary_key.includes(c.name) ? chip('PK', 'primary') : ''}${fk ? chip('FK → ' + fk.references) : ''}</td></tr>`;
    }).join('')}</tbody></table></div></div>`)).join('')}</div>`);
  $$('[data-ask]').forEach(b => b.onclick = () => { state.studio.nl = `Show all rows from ${b.dataset.ask}`; go('studio'); });
}

// ───────────────────────────── Query History ─────────────────────────────
async function viewHistory() {
  container(pageHead('Core AI Studio', 'Query History', 'Your recent generated and executed SQL.') + EMPTY('hourglass_top', 'Loading…'));
  const r = await api('/api/query/history'); if (!r.ok) { container(EMPTY('error', r.data.message || 'Could not load history.')); return; }
  const rows = r.data.history;
  container(pageHead('Core AI Studio', 'Query History', 'Your recent generated and executed SQL.') +
    (rows.length ? `<div class="space-y-3">${rows.map((h, i) => card(`<div class="p-4"><div class="flex flex-wrap items-center justify-between gap-2"><div class="flex items-center gap-2">${chip(h.action.replace('SQL_', '').replace('_', ' '), 'neutral')}${chip(h.operation_type || '—')}${chip(h.status, statusTone(h.status))}</div><span class="text-label-sm text-tertiary">${fmtTime(h.created_at)}</span></div>
      ${h.question ? `<p class="mt-2 font-body-editorial text-[16px] italic text-on-surface-variant">“${esc(h.question)}”</p>` : ''}
      <div class="mt-2">${sqlBlock(prettySql(h.sql_query))}</div>
      ${(h.sql_query || '').includes('{{') ? '<div class="mt-2 text-right text-label-sm text-tertiary">File template — attach the file again in the AI Query Studio to reuse it.</div>' : `<div class="mt-2 text-right"><button data-reuse="${i}" class="text-primary font-label-md text-label-md hover:underline">Open in Query Studio →</button></div>`}</div>`)).join('')}</div>` : card(EMPTY('history', 'No history yet', 'Generate your first query in the AI Query Studio.'))));
  $$('[data-reuse]').forEach(b => b.onclick = async () => {
    const h = rows[+b.dataset.reuse]; const s = state.studio;
    Object.assign(s, {
      nl: h.question || '', sql: prettySql(h.sql_query), result: null, ticket: null, error: null, editing: true,
      gen: { valid: true, operation_type: h.operation_type, target_table: h.target_table, tables_considered: null }
    });
    go('studio'); await revalidate();
  });
}

// ───────────────────────────── Approvals ─────────────────────────────
async function loadApprovals(render = true) {
  const a = state.approvals;
  if (state.user.role === 'DBA') {
    const [p, all] = await Promise.all([api('/api/approvals/pending', { quiet: true }), api('/api/approvals/mine', { quiet: true })]);
    if (p.ok) { a.pending = p.data.requests; state.pending = p.data.count; }
    if (all.ok) a.all = all.data.requests;
    if (!a.pending.find(x => x.id === a.selected)) a.selected = a.pending[0]?.id ?? null;
  } else {
    const all = await api('/api/approvals/mine', { quiet: true }); if (all.ok) a.all = all.data.requests;
  }
  if ($('#nav')) $('#nav').innerHTML = navHtml();
  if (render && state.view === 'approvals') approvalsRender();
  else if (state.view === 'approvals') approvalsRender();
}
function viewApprovals() { container(pageHead('Governance & Operations', state.user.role === 'DBA' ? 'Pending Database Changes' : 'My Change Requests', '') + EMPTY('hourglass_top', 'Loading…')); loadApprovals(); }

const opIcon = o => ({ UPDATE: 'edit', DELETE: 'delete', DROP: 'delete_forever', TRUNCATE: 'delete_sweep', ALTER: 'build', TRANSACTION: 'swap_horiz', INSERT: 'add', CREATE: 'add_box' }[o] || 'database');

function approvalsRender() {
  const a = state.approvals;
  if (state.user.role !== 'DBA') { return devRequestsRender(); }
  const counts = s => a.all.filter(x => x.status === s).length;
  const sel = a.pending.find(x => x.id === a.selected);
  const history = a.all.filter(x => x.status !== 'PENDING');
  container(`${pageHead('Elevated gatekeeper mode', 'Pending Database Changes', 'Developer requests for UPDATE, DELETE and other sensitive operations. <b>Nothing executes until you approve.</b>')}
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      ${[['Pending sign-offs', a.pending.length, 'pending_actions', 'text-on-secondary-container'], ['Executed', counts('EXECUTED'), 'check_circle', 'text-emerald-700'], ['Rejected', counts('REJECTED'), 'cancel', 'text-error'], ['Failed', counts('FAILED'), 'report', 'text-error']]
      .map(([l, n, ic, c]) => card(`<div class="p-4 flex items-start justify-between"><div><div class="text-label-sm uppercase tracking-wider text-tertiary">${l}</div><div class="font-headline-lg text-headline-lg mt-1">${n}</div></div>${icon(ic, 'text-[26px] ' + c)}</div>`)).join('')}</div>
    <div class="flex gap-1 mb-4 p-1 bg-surface-container rounded-lg w-fit">${['pending', 'history'].map(t => `<button data-tab="${t}" class="px-4 py-1.5 rounded-md text-label-md font-label-md ${a.tab === t ? 'bg-surface-container-lowest shadow-sm text-primary font-semibold' : 'text-on-surface-variant'}">${t === 'pending' ? 'Sign-off buffer (' + a.pending.length + ')' : 'History (' + history.length + ')'}</button>`).join('')}</div>
    ${a.tab === 'history' ? card(history.length ? `<div class="divide-y divide-outline-variant/20">${history.map(h => `<div class="p-4"><div class="flex flex-wrap items-center justify-between gap-2"><div class="flex items-center gap-2">${chip(h.status, statusTone(h.status))}${chip(h.operation_type)}<span class="text-body-sm text-on-surface-variant">by <b>${esc(h.requester_name)}</b> · ${fmtTime(h.created_at)}</span></div>
        <span class="text-label-sm text-tertiary">${h.reviewed_by_name ? 'Reviewed by ' + esc(h.reviewed_by_name) : ''}</span></div>
        ${h.natural_language_request ? `<p class="mt-2 font-body-editorial italic text-on-surface-variant">“${esc(h.natural_language_request)}”</p>` : ''}
        <div class="mt-2">${sqlBlock(prettySql(h.generated_sql))}</div>
        ${h.rejection_reason ? `<p class="mt-2 text-body-sm text-error">Rejection reason: ${esc(h.rejection_reason)}</p>` : ''}${h.execution_message ? `<p class="mt-2 text-body-sm text-on-surface-variant">${esc(h.execution_message)}</p>` : ''}</div>`).join('')}</div>` : EMPTY('history', 'No reviewed requests yet')) :
      (a.pending.length ? `<div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
      <div class="lg:col-span-4 space-y-3">${a.pending.map(p => `<button data-sel="${p.id}" class="w-full text-left p-4 rounded-xl border ${p.id === a.selected ? 'bg-surface-container-lowest border-primary shadow-sm' : 'bg-surface-container-low border-outline-variant/20 hover:bg-surface-container'}">
          <div class="flex items-center justify-between"><span class="font-code-md text-primary">#${p.id}</span><span class="text-label-sm text-tertiary">${ago(p.created_at)}</span></div>
          <div class="mt-1 font-title-md text-title-md flex items-center gap-1.5">${icon(opIcon(p.operation_type), 'text-[18px]')}${esc(p.operation_type)} ${p.target_table ? '· ' + esc(p.target_table) : ''}</div>
          <div class="mt-1 text-body-sm text-on-surface-variant truncate">${esc(p.natural_language_request || p.generated_sql)}</div>
          <div class="mt-2 flex items-center gap-2 text-label-sm text-tertiary">${icon('person', 'text-[14px]')}${esc(p.requester_name)} · ${esc(p.requester_role)}${p.dangerous ? chip('Dangerous', 'error') : ''}</div></button>`).join('')}</div>
      <div class="lg:col-span-8" id="ticket"></div></div>` : card(EMPTY('verified', 'Nothing awaiting review', 'New developer requests will appear here and in your notifications.')))}`);
  $$('[data-tab]').forEach(b => b.onclick = () => { a.tab = b.dataset.tab; approvalsRender(); });
  $$('[data-sel]').forEach(b => b.onclick = () => { a.selected = +b.dataset.sel; a.rejecting = false; approvalsRender(); });
  if (sel && a.tab === 'pending') ticketRender(sel);
}

function ticketRender(p) {
  const a = state.approvals;
  $('#ticket').innerHTML = card(`<div class="p-6">
    <div class="flex flex-wrap items-start justify-between gap-3"><div><div class="font-label-sm text-label-sm uppercase tracking-wider text-primary font-semibold">Database change request</div>
      <h2 class="font-headline-md text-headline-md mt-1">Ticket #${p.id}</h2></div>${chip('Awaiting DBA', 'warn')}${p.statement_count > 1 ? chip(p.statement_count + ' queries', 'primary') : ''}</div>
    <dl class="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3">${[['Requested by', p.requester_name], ['Role', p.requester_role], ['Operation', p.operation_type], ['Target table', p.target_table || '—']]
      .map(([k, v]) => `<div class="p-3 rounded-lg bg-surface-container-low"><dt class="text-label-sm uppercase tracking-wider text-tertiary">${k}</dt><dd class="font-title-md text-title-md mt-0.5 break-words">${esc(v)}</dd></div>`).join('')}</dl>
    <div class="mt-1 text-label-sm text-tertiary">Submitted ${fmtTime(p.created_at)}</div>
    ${p.natural_language_request ? `<div class="mt-5"><div class="text-label-sm uppercase tracking-wider text-tertiary mb-1">Natural-language request</div><blockquote class="p-4 rounded-lg bg-surface-container-low font-body-editorial text-[17px] italic leading-7">“${esc(p.natural_language_request)}”</blockquote></div>` : ''}
    <div class="mt-5"><div class="text-label-sm uppercase tracking-wider text-tertiary mb-1">Proposed SQL</div>${sqlBlock(prettySql(p.generated_sql))}</div>
    ${p.dangerous ? `<div class="mt-4 px-4 py-3 rounded-lg bg-error-container/60 text-on-error-container text-body-sm"><div class="font-semibold flex items-center gap-1.5 mb-1">${icon('warning', 'text-[18px]')}Dangerous operation</div><ul class="list-disc pl-5">${p.danger_reasons.map(x => `<li>${esc(x)}</li>`).join('')}</ul>
      <label class="mt-3 flex items-start gap-2 cursor-pointer"><input id="danger-ok" type="checkbox" class="mt-1 accent-[#99462a]"/><span>I have reviewed this SQL and explicitly authorise this dangerous operation.</span></label></div>` : ''}
    ${a.rejecting ? `<div class="mt-4"><label class="text-label-md font-label-md" for="rej-reason">Rejection reason (shown to the requester)</label>
      <textarea id="rej-reason" rows="3" maxlength="500" class="mt-1 w-full bg-surface-container-low rounded-lg p-3 focus:outline-none focus:ring-2 focus:ring-primary/25" placeholder="Explain why this change is rejected…"></textarea></div>` : ''}
    <div class="mt-6 flex flex-wrap justify-end gap-2">
      ${a.rejecting ? `<button id="rej-cancel" class="px-4 py-2 rounded-lg bg-surface-container hover:bg-surface-container-high text-label-md font-label-md">Back</button>
        <button id="rej-confirm" class="px-4 py-2.5 rounded-lg bg-error text-on-error font-title-md text-title-md flex items-center gap-2">${icon('close', 'text-[18px]')}Confirm rejection</button>` :
      `<button id="rej-btn" class="px-4 py-2.5 rounded-lg bg-error text-on-error font-title-md text-title-md flex items-center gap-2">${icon('close', 'text-[18px]')}Reject</button>
        <button id="app-btn" class="px-5 py-2.5 rounded-lg bg-primary hover:bg-secondary text-on-primary font-title-md text-title-md shadow-sm flex items-center gap-2">${icon('check_circle', 'text-[18px] fill-1')}Approve &amp; Execute</button>`}
    </div></div>`);
  const busy = (btn, on) => { btn.disabled = on; };
  if (a.rejecting) {
    $('#rej-cancel').onclick = () => { a.rejecting = false; ticketRender(p); };
    $('#rej-confirm').onclick = async e => {
      const reason = $('#rej-reason').value.trim(); if (reason.length < 3) { toast('Please provide a rejection reason.', 'warn'); return; }
      busy(e.currentTarget, true);
      const r = await api(`/api/approvals/${p.id}/reject`, { method: 'POST', json: { reason } });
      a.rejecting = false; toast(r.ok ? `Request #${p.id} rejected.` : (r.data.message || 'Could not reject.'), r.ok ? 'success' : 'error');
      await loadApprovals(); refreshNotifs();
    };
  } else {
    $('#rej-btn').onclick = () => { a.rejecting = true; ticketRender(p); };
    $('#app-btn').onclick = async e => {
      const ok = $('#danger-ok'); if (p.dangerous && !ok.checked) { toast('Tick the confirmation box to approve a dangerous operation.', 'warn'); return; }
      busy(e.currentTarget, true); e.currentTarget.innerHTML = `${spinner}<span>Executing…</span>`;
      const r = await api(`/api/approvals/${p.id}/approve`, { method: 'POST', json: { confirm_dangerous: !!(ok && ok.checked) } });
      if (r.ok && r.data.success) toast(r.data.message || 'Approved and executed.', 'success');
      else toast(r.data.message || 'Execution failed.', 'error');
      await loadApprovals(); refreshNotifs();
    };
  }
}

function devRequestsRender() {
  const all = state.approvals.all;
  container(`${pageHead('Governance & Operations', 'My Change Requests', 'Sensitive operations you submitted for DBA approval. A DBA must approve before anything runs.')}
    ${all.length ? `<div class="space-y-3">${all.map(h => card(`<div class="p-4"><div class="flex flex-wrap items-center justify-between gap-2"><div class="flex items-center gap-2">${chip(h.status, statusTone(h.status))}${chip(h.operation_type)}<span class="font-code-md text-primary">#${h.id}</span></div><span class="text-label-sm text-tertiary">${fmtTime(h.created_at)}</span></div>
      ${h.natural_language_request ? `<p class="mt-2 font-body-editorial italic text-on-surface-variant">“${esc(h.natural_language_request)}”</p>` : ''}
      <div class="mt-2">${sqlBlock(prettySql(h.generated_sql))}</div>
      ${h.status === 'PENDING' ? `<p class="mt-2 text-body-sm text-on-secondary-container flex items-center gap-1.5">${icon('hourglass_top', 'text-[16px]')}Waiting for DBA review.</p>` : ''}
      ${h.reviewed_by_name ? `<p class="mt-2 text-body-sm text-on-surface-variant">Reviewed by ${esc(h.reviewed_by_name)} · ${fmtTime(h.reviewed_at)}</p>` : ''}
      ${h.rejection_reason ? `<p class="mt-1 text-body-sm text-error">Rejection reason: ${esc(h.rejection_reason)}</p>` : ''}
      ${h.execution_message ? `<p class="mt-1 text-body-sm text-on-surface-variant">${esc(h.execution_message)}</p>` : ''}</div>`)).join('')}</div>` : card(EMPTY('inbox', 'No change requests yet', 'UPDATE and DELETE queries you run are sent here for DBA approval.'))}`);
}

// ───────────────────────────── Audit logs ─────────────────────────────
const AUDIT_ACTIONS = ['LOGIN', 'LOGIN_FAILED', 'LOGOUT', 'SQL_GENERATED', 'SQL_EXECUTION_REQUESTED', 'SQL_EXECUTED', 'SQL_REJECTED', 'PERMISSION_DENIED',
  'APPROVAL_REQUESTED', 'APPROVAL_APPROVED', 'APPROVAL_REJECTED', 'FILE_ATTACHED', 'FILE_REMOVED', 'RESULT_EXPORTED', 'DATABASE_ERROR'];
async function viewAudit() {
  const f = state.audit; const head = pageHead('Governance & Operations', 'Audit Logs', 'Immutable record of logins, SQL, approvals and imports. Passwords and secrets are never logged.');
  container(head + EMPTY('hourglass_top', 'Loading…'));
  const r = await api(`/api/audit-logs?limit=${f.limit}&offset=${f.offset}${f.action ? '&action=' + encodeURIComponent(f.action) : ''}`);
  if (!r.ok) { container(head + EMPTY('error', r.data.message || 'Could not load audit logs.')); return; }
  const { logs, total } = r.data;
  container(head + card(`<div class="p-4 flex flex-wrap items-center justify-between gap-3 border-b border-outline-variant/20">
      <select id="audit-filter" class="bg-surface-container-low rounded-lg px-3 py-2 text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/25"><option value="">All actions</option>${AUDIT_ACTIONS.map(a => `<option ${a === f.action ? 'selected' : ''}>${a}</option>`).join('')}</select>
      <div class="flex items-center gap-2 text-body-sm text-on-surface-variant">${total ? `${f.offset + 1}–${Math.min(f.offset + f.limit, total)} of ${total}` : '0 entries'}
        <button id="pg-prev" ${f.offset === 0 ? 'disabled' : ''} class="p-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high">${icon('chevron_left', 'text-[18px]')}</button>
        <button id="pg-next" ${f.offset + f.limit >= total ? 'disabled' : ''} class="p-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high">${icon('chevron_right', 'text-[18px]')}</button></div></div>
    ${logs.length ? `<div class="scroll-x max-h-[640px]"><table class="data-table"><thead><tr><th>Time</th><th>User</th><th>Role</th><th>Action</th><th>Operation</th><th>Table</th><th>Status</th><th>IP</th><th>SQL</th></tr></thead><tbody>
      ${logs.map(l => `<tr><td class="whitespace-nowrap">${fmtTime(l.created_at)}</td><td>${esc(l.username || '—')}</td><td>${esc(l.role || '—')}</td><td class="font-code-md">${esc(l.action)}</td><td>${esc(l.operation_type || '')}</td><td>${esc(l.target_table || '')}</td><td>${chip(l.status, statusTone(l.status))}</td><td class="font-code-md">${esc(l.ip_address || '')}</td>
        <td class="max-w-[360px]">${l.sql_query ? `<details><summary class="text-primary text-label-md font-label-md">View SQL</summary><div class="mt-1">${sqlBlock(prettySql(l.sql_query))}</div></details>` : ''}</td></tr>`).join('')}</tbody></table></div>` : EMPTY('verified_user', 'No matching entries')}`));
  $('#audit-filter').onchange = e => { f.action = e.target.value; f.offset = 0; viewAudit(); };
  $('#pg-prev').onclick = () => { f.offset = Math.max(0, f.offset - f.limit); viewAudit(); };
  $('#pg-next').onclick = () => { f.offset += f.limit; viewAudit(); };
}

// ───────────────────────────── Employee Directory ─────────────────────────────
async function viewEmployees() {
  const head = pageHead('PostgreSQL · public.employees', 'Employee Directory', 'Company employee master data. Anything stored here can be queried in the AI Query Studio — or changed in bulk by attaching a file there.');
  const load = async () => {
    const r = await api('/api/employees?search=' + encodeURIComponent(state.emp.search));
    const body = $('#emp-body'); if (!body) return;
    if (!r.ok) { body.innerHTML = EMPTY('error', r.data.message || 'Could not load employees.'); return; }
    $('#emp-total').textContent = `${r.data.employees.length} shown · ${r.data.total} total`;
    body.innerHTML = r.data.employees.length ? `<div class="scroll-x max-h-[620px]"><table class="data-table"><thead><tr><th>Code</th><th>Name</th><th>Email</th><th>Department</th><th>Designation</th><th>Salary</th><th>Joined</th><th>Status</th></tr></thead><tbody>
      ${r.data.employees.map(e => `<tr><td class="font-code-md text-primary">${esc(e.employee_code)}</td><td class="font-medium">${esc(e.name)}</td><td>${esc(e.email)}</td><td>${esc(e.department)}</td><td>${esc(e.designation)}</td><td class="tabular-nums">${Number(e.salary).toLocaleString()}</td><td>${esc(e.joining_date)}</td><td>${chip(e.status, e.status === 'Active' ? 'ok' : 'neutral')}</td></tr>`).join('')}</tbody></table></div>` : EMPTY('group', 'No employees found');
  };
  container(head + card(`<div class="p-4 flex items-center justify-between gap-3 border-b border-outline-variant/20"><div class="relative flex-1 max-w-sm">${icon('search', 'absolute left-3 top-2.5 text-[18px] text-tertiary')}
    <input id="emp-search" value="${esc(state.emp.search)}" maxlength="100" placeholder="Search name, code, email, department…" class="w-full pl-9 pr-3 py-2 bg-surface-container-low rounded-lg text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/25"/></div><span id="emp-total" class="text-body-sm text-tertiary"></span></div><div id="emp-body">${EMPTY('hourglass_top', 'Loading…')}</div>`));
  let t; $('#emp-search').oninput = e => { state.emp.search = e.target.value; clearTimeout(t); t = setTimeout(load, 300); };
  load();
}

// ───────────────────────────── Settings ─────────────────────────────
async function viewSettings() {
  const head = pageHead('Team & Data Sync', 'Settings', 'Your account, session and the role permission matrix enforced by the backend.');
  container(head + EMPTY('hourglass_top', 'Loading…'));
  const [p, st] = await Promise.all([api('/api/auth/permissions'), api('/api/status')]);
  if (st.ok) { state.status = st.data; renderPills(); }
  const u = state.user, m = p.ok ? p.data.matrix : {};
  const ops = Object.keys(m.DBA || {});
  const cell = d => d === 'ALLOW' ? chip('Allowed', 'ok') : d === 'APPROVAL' ? chip('DBA approval', 'warn') : chip('Denied', 'error');
  const ai = state.status?.ai, db = state.status?.database;
  container(head + `<div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
    ${card(`<div class="p-5"><h2 class="font-headline-sm text-headline-sm mb-3">Account</h2><dl class="space-y-2 text-body-md">
      <div class="flex justify-between"><dt class="text-tertiary">Username</dt><dd>${esc(u.username)}</dd></div><div class="flex justify-between"><dt class="text-tertiary">Email</dt><dd>${esc(u.email)}</dd></div>
      <div class="flex justify-between"><dt class="text-tertiary">Role</dt><dd>${chip(u.role_label, 'primary')}</dd></div>
      <div class="flex justify-between"><dt class="text-tertiary">Session ends</dt><dd>${fmtTime(state.expiresAt)}</dd></div></dl>
      <button id="set-logout" class="mt-4 w-full px-4 py-2 rounded-lg bg-surface-container hover:bg-surface-container-high text-label-md font-label-md flex items-center justify-center gap-1.5">${icon('logout', 'text-[18px]')}Sign out</button></div>`)}
    ${card(`<div class="p-5"><h2 class="font-headline-sm text-headline-sm mb-3">PostgreSQL</h2><dl class="space-y-2"><div class="flex justify-between"><dt class="text-tertiary">Status</dt><dd>${chip(db?.online ? 'Online' : 'Offline', db?.online ? 'ok' : 'error')}</dd></div><div class="flex justify-between"><dt class="text-tertiary">Database</dt><dd class="font-code-md">${esc(db?.name || '—')}</dd></div></dl></div>`)}
    ${card(`<div class="p-5"><h2 class="font-headline-sm text-headline-sm mb-3">Local AI (Ollama)</h2><dl class="space-y-2"><div class="flex justify-between"><dt class="text-tertiary">Service</dt><dd>${chip(ai?.online ? 'Running' : 'Not running', ai?.online ? 'ok' : 'error')}</dd></div>
      <div class="flex justify-between"><dt class="text-tertiary">Model</dt><dd class="font-code-md">${esc(ai?.model || '—')}</dd></div><div class="flex justify-between"><dt class="text-tertiary">Installed</dt><dd>${chip(ai?.model_installed ? 'Yes' : 'No', ai?.model_installed ? 'ok' : 'warn')}</dd></div></dl>
      ${ai && !ai.online ? '<p class="mt-3 text-body-sm text-error">AI service is unavailable. Please start Ollama.</p>' : ''}</div>`)}</div>
    ${card(`<div class="p-5 border-b border-outline-variant/20"><h2 class="font-headline-sm text-headline-sm">Role permission matrix</h2><p class="text-body-sm text-on-surface-variant mt-1">Enforced by the backend on every request. “DBA approval” means the statement is queued and only runs after a DBA approves it.</p></div>
      <div class="scroll-x"><table class="data-table"><thead><tr><th>Operation</th>${Object.keys(m).map(r => `<th>${r === u.role ? '● ' : ''}${r}</th>`).join('')}</tr></thead>
      <tbody>${ops.map(o => `<tr><td class="font-code-md">${o}</td>${Object.keys(m).map(r => `<td>${cell(m[r][o])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`)}`);
  $('#set-logout').onclick = logout;
}

const VIEWS = { studio: studioRender, schema: viewSchema, history: viewHistory, approvals: viewApprovals, audit: viewAudit, employees: viewEmployees, settings: viewSettings };
window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (state.user && v !== state.view && VIEWS[v]) go(v); });
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
