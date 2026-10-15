/* ════════════════════════════════════════════════════
   RepairOS — Core utilities & API client
════════════════════════════════════════════════════ */

const API = window.location.origin;

/* ── THEME (light / dark matte) ── */
const Theme = {
  get current() { return localStorage.getItem('ros_theme') || 'light'; },
  apply(t) {
    const theme = t || this.current;
    document.documentElement.setAttribute('data-theme', theme === 'dark' ? 'dark' : 'light');
    localStorage.setItem('ros_theme', theme);
  },
  toggle() { this.apply(this.current === 'dark' ? 'light' : 'dark'); return this.current; },
};
// apply immediately so there's no flash of the wrong theme
Theme.apply();

/* ── TOKEN STORE ── */
const Auth = {
  get token() { return localStorage.getItem('ros_tok') || ''; },
  set token(v) { localStorage.setItem('ros_tok', v); },
  clear() { localStorage.removeItem('ros_tok'); },
};

/* Session ended (expired, revoked, or the account was suspended). The legacy admin shell defined
   App.logout; the technician PWA — the only page still on this file — never did, so a 401 there
   threw "App is not defined" and stranded the user on an error instead of the sign-in screen. */
function signedOut() {
  Auth.clear();
  if (typeof App !== 'undefined' && App.logout) { App.logout(); return; }
  location.reload();   // with no token the page boots to its sign-in form
}

/* ── HTTP CLIENT ── */
async function req(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${Auth.token}`, ...(opts.headers || {}) };
  const r = await fetch(API + path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (r.status === 401) { signedOut(); return null; }
  const json = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(errMessage(json, r.status));
  return json;
}
// Build 144dd — FastAPI errors: 422 `detail` is an array of {loc,msg}, plain
// errors a string. Flatten to a readable message so toasts stop showing
// "[object Object]". One guard here covers every get/post/patch/del caller.
function errMessage(json, status) {
  const d = json && json.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) {
    return d.map(e => {
      const field = Array.isArray(e.loc) ? e.loc.filter(x => x !== 'body').join('.') : '';
      return (field ? field + ': ' : '') + (e.msg || 'invalid');
    }).join('; ') || `HTTP ${status}`;
  }
  if (d && typeof d === 'object') return d.msg || JSON.stringify(d);
  return `HTTP ${status}`;
}
async function get(path) { return req(path, { method: 'GET' }); }
async function post(path, body) { return req(path, { method: 'POST', body }); }

/* ── Build 144am: open an authenticated PDF/binary endpoint in a new tab.
   A raw window.open() sends no Authorization header, so /api/printing/* 401s and
   the tab comes up blank. This fetches with the bearer token, then opens the blob. */
async function openPrintPdf(path, filename) {
  try {
    const res = await fetch(API + path, { headers: { Authorization: `Bearer ${Auth.token}` } });
    if (!res.ok) throw new Error(`Could not generate the document (HTTP ${res.status})`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const w = window.open(url, '_blank');
    if (!w) {  // popup blocked → fall back to a download
      const a = document.createElement('a');
      a.href = url; a.download = filename || 'document.pdf';
      document.body.appendChild(a); a.click(); a.remove();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (e) { (typeof Toast !== 'undefined' ? Toast.error : alert)(e.message); }
}
async function put(path, body) { return req(path, { method: 'PUT', body }); }
async function patch(path, body) { return req(path, { method: 'PATCH', body }); }
async function del(path) { return req(path, { method: 'DELETE' }); }

/* auth-form login */
async function apiLogin(username, password) {
  const fd = new FormData();
  fd.append('username', username); fd.append('password', password);
  const r = await fetch(API + '/api/auth/token', { method: 'POST', body: fd });
  const json = await r.json();
  if (!r.ok) throw new Error(json.detail || 'Login failed');
  return json;
}

/* ── TOAST ── */
const Toast = {
  show(msg, type = 'info', customIcon = null) {
    const stack = document.getElementById('toast-stack');
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    const icons = { success:icon('check',15), error:icon('alert',15), info:icon('message',15) };
    t.innerHTML = `<span class="toast-icon">${customIcon || icons[type] || icons.info}</span><span>${msg}</span>`;
    stack.appendChild(t);
    setTimeout(() => t.remove(), 3800);
  },
  success(m) { this.show(m, 'success'); },
  error(m)   { this.show(m, 'error'); },
  info(m)    { this.show(m, 'info'); },
};

/* ── MODAL (stack-aware) ── */
const Modal = {
  _stack: [],
  // open(html, cls)            → replaces the current top-level modal (default)
  // open(html, cls, true)      → stacks a new modal on top of the existing one
  open(html, cls = '', stack = false) {
    if (!stack) this.closeAll();
    const wrap = document.createElement('div');
    wrap.className = 'overlay';
    wrap.innerHTML = `<div class="modal glass-heavy ${cls}">${html}</div>`;
    wrap.addEventListener('click', e => { if (e.target === wrap) this.close(); });
    document.body.appendChild(wrap);
    this._stack.push(wrap);
    // hide any data-perm control this role is denied (App loads after core.js)
    if (typeof App !== 'undefined' && App.applyPermissions) App.applyPermissions(wrap);
    return wrap;
  },
  // close the top-most modal only
  close() {
    const top = this._stack.pop();
    top?.remove();
  },
  closeAll() {
    this._stack.forEach(w => w.remove());
    this._stack = [];
  },
};

/* ── STYLED CONFIRM / PROMPT (Build 144eb) ──
   Replace native confirm()/prompt() with on-brand modals. Each call resolves via
   a closure (no shared global), stacks over existing modals, and a backdrop click
   or Cancel resolves the "no" value so a promise never hangs. */
Modal.confirm = function (message, opts = {}) {
  return new Promise(resolve => {
    const title = opts.title || 'Please confirm';
    const okLabel = opts.confirmLabel || (opts.danger ? 'Delete' : 'Confirm');
    const okClass = opts.danger ? 'btn-danger' : 'btn-primary';
    const wrap = Modal.open(`
      <div class="modal-header">
        <span class="modal-title">${title}</span>
        <button class="modal-close" data-act="cancel">×</button>
      </div>
      <div class="modal-body" style="padding:22px 24px;font-size:14px;font-weight:600;color:var(--tx-2);line-height:1.55">${message}</div>
      <div class="modal-footer">
        <button class="btn btn-glass" data-act="cancel">Cancel</button>
        <button class="btn ${okClass}" data-act="ok">${okLabel}</button>
      </div>`, 'modal-narrow', true);
    const settle = v => { Modal.close(); resolve(v); };
    wrap.querySelectorAll('[data-act="cancel"]').forEach(b => b.onclick = () => settle(false));
    wrap.querySelector('[data-act="ok"]').onclick = () => settle(true);
    wrap.addEventListener('click', e => { if (e.target === wrap) resolve(false); });
  });
};
Modal.prompt = function (message, opts = {}) {
  return new Promise(resolve => {
    const wrap = Modal.open(`
      <div class="modal-header">
        <span class="modal-title">${opts.title || message}</span>
        <button class="modal-close" data-act="cancel">×</button>
      </div>
      <div class="modal-body" style="padding:18px 24px">
        ${opts.title ? `<div style="font-size:13px;color:var(--tx-2);margin-bottom:8px">${message}</div>` : ''}
        <input class="field-input" data-role="pv" placeholder="${opts.placeholder || ''}" style="width:100%">
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" data-act="cancel">Cancel</button>
        <button class="btn btn-primary" data-act="ok">${opts.confirmLabel || 'OK'}</button>
      </div>`, 'modal-narrow', true);
    const input = wrap.querySelector('[data-role="pv"]');
    input.value = opts.default != null ? opts.default : '';
    setTimeout(() => { input.focus(); input.select(); }, 30);
    const cancel = () => { Modal.close(); resolve(null); };
    const ok = () => { Modal.close(); resolve(input.value); };
    wrap.querySelectorAll('[data-act="cancel"]').forEach(b => b.onclick = cancel);
    wrap.querySelector('[data-act="ok"]').onclick = ok;
    input.addEventListener('keydown', e => { if (e.key === 'Enter') ok(); else if (e.key === 'Escape') cancel(); });
    wrap.addEventListener('click', e => { if (e.target === wrap) resolve(null); });
  });
};
// Back-compat shim (kept so any old caller still works).
function confirm2(msg, opts) { return Modal.confirm(msg, opts); }

/* ── FORMAT ── */
const fmt = {
  // Build 125: the org's base currency symbol, set from /api/auth/me at boot and refreshed
  // when General settings are saved. Persisted so it survives a reload before boot finishes.
  _sym: (typeof localStorage !== 'undefined' && localStorage.getItem('ros_cur_sym')) || '₹',
  // Build 144ci: active locale for number/date grouping — set by I18n.setLang.
  _locale: (typeof localStorage !== 'undefined' && localStorage.getItem('ros_lang') === 'es') ? 'es-ES' : 'en-US',
  setBase(sym) {
    this._sym = sym || '₹';
    try { localStorage.setItem('ros_cur_sym', this._sym); } catch (e) {}
  },
  currency(n) { return this._sym + (n || 0).toLocaleString(this._locale, { maximumFractionDigits: 0 }); },
  // Build 122: format an amount in a display currency. Pass the base amount plus the
  // document's { symbol, rate } (rate = display units per 1 base). Falls back to base symbol.
  money(n, cur) {
    const v = (n || 0) * (cur && cur.rate ? cur.rate : 1);
    const sym = (cur && cur.symbol) || this._sym;
    return sym + v.toLocaleString(this._locale, { maximumFractionDigits: 2 });
  },
  date(s)     { return s ? new Date(s).toLocaleDateString(this._locale, { day:'2-digit', month:'short', year:'numeric' }) : '—'; },
  datetime(s) { return s ? new Date(s).toLocaleString(this._locale, { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' }) : '—'; },
  reltime(s)  {
    if (!s) return '—';
    const d = Math.round((Date.now() - new Date(s)) / 60000);
    if (d < 1) return 'just now';
    if (d < 60) return `${d}m ago`;
    if (d < 1440) return `${Math.round(d/60)}h ago`;
    return `${Math.round(d/1440)}d ago`;
  },
  initials(name) { return (name || '?').split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2); },
};

/* ── BADGE / PRIORITY HTML ── */
const statusMap = {
  new:'b-new', diagnosed:'b-diagnosed', waiting_parts:'b-waiting',
  in_repair:'b-in_repair', ready:'b-ready', done:'b-done', delivered:'b-delivered', dropped:'b-dropped',
  paid:'b-paid', draft:'b-draft', partial:'b-partial', overdue:'b-overdue',
  todo:'b-todo', in_progress:'b-in_progress',
};
// Estimate statuses → label + accent colour. Build 144dh: a distinct cool/violet
// register so an estimate status never shares a colour with a work-order status
// (WO uses blue/amber/red/purple/teal/green) — paired with the frosted chip shape.
const estStatusMap = {
  est_draft:    ['Draft',         '#6366f1'],   // indigo
  est_progress: ['In progress',   '#8b5cf6'],   // violet
  est_sent:     ['Sent',          '#0ea5e9'],   // sky
  est_pending:  ['Pending',       '#d97706'],   // deep amber (distinct from WO amber)
  est_accepted: ['Accepted',      '#0d9488'],   // deep teal
  est_converted:['Order created', '#7c3aed'],   // deep violet
  est_canceled: ['Canceled',      '#94a3b8'],   // slate
};
function badge(s, opts) {
  opts = opts || {};
  // Estimate statuses (or any caller passing {est:true}): transparent outline chip
  // with coloured border + text — never looks like a committed work order.
  if (opts.est || (s && estStatusMap[s])) {
    const em = estStatusMap[s];
    const color = opts.color || (em && em[1]) || '#6b7fb0';
    const label = (em && em[0]) || (s || '—').replace(/_/g,' ');
    return `<span class="badge badge-est" style="--est:${color}">${label}</span>`;
  }
  // Work-order statuses: solid coloured fill.
  const cls = statusMap[s];
  if (!cls && opts.color) {
    // custom (org-defined) WO status with a hex colour → solid inline fill
    return `<span class="badge badge-solid" style="background:${opts.color};color:#fff">${(s || '—').replace(/_/g,' ')}</span>`;
  }
  return `<span class="badge badge-solid ${cls || 'b-draft'}">${(s || '—').replace(/_/g,' ')}</span>`;
}
function priBadge(p) {
  const conf = [[0,'Normal'],[1,'Urgent'],[2,'VIP']];
  const [, label] = conf[p] || conf[0];
  return `<span class="pri pri-${p}">${label}</span>`;
}
function avatarHtml(name, size = 32) {
  const colors = ['linear-gradient(135deg,#4f8ef7,#9b7bf4)','linear-gradient(135deg,#3dca7a,#2cd4c0)','linear-gradient(135deg,#f5a623,#f2645a)','linear-gradient(135deg,#f06daa,#9b7bf4)'];
  const idx = (name || '?').charCodeAt(0) % colors.length;
  return `<div class="avatar" style="width:${size}px;height:${size}px;font-size:${size*0.38}px;background:${colors[idx]}">${fmt.initials(name)}</div>`;
}

/* ── SKELETON ROWS ── */
function skeletonRows(cols, n = 5) {
  return Array(n).fill(0).map(() =>
    `<tr>${Array(cols).fill(0).map((_, i) =>
      `<td><div class="skeleton" style="height:14px;width:${70 - i * 8}%"></div></td>`
    ).join('')}</tr>`
  ).join('');
}

/* ── TABLE EMPTY ── */
function emptyRow(cols, iconName, title, sub = '') {
  return `<tr><td colspan="${cols}">
    <div class="empty">
      <div class="empty-blob">${icon(iconName, 30)}</div>
      <div class="empty-title">${title}</div>
      ${sub ? `<div class="empty-sub">${sub}</div>` : ''}
    </div></td></tr>`;
}

// ── Custom (org-defined) entity fields — render + collect ──
// Used by the client form, asset form and work-order create modal so shop-defined
// fields (Settings → Forms & types → Client/Asset fields) appear on those forms.
// Build 144ax — one shared list thumbnail: small square, hover to enlarge. Reused by
// products, inventory stock and assets lists. Falls back to a muted placeholder.
window.thumbImg = function (url, opts) {
  opts = opts || {};
  if (!url) return opts.hideEmpty ? '' : `<span class="thumb-empty">${icon('image', 13)}</span>`;
  return `<span class="asset-thumb"><img src="${url}" alt="">` +
         `<span class="asset-thumb-pop"><img src="${url}" alt=""></span></span>`;
};

window.CustomFields = {
  async fetch(entity) {
    try { return (await get(`/api/forms/entity-fields?entity=${entity}`)).items || []; }
    catch (e) { return []; }
  },
  _esc(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;'); },
  // render(fields, values, prefix, opts) — opts.editing=true disables locked fields
  // (Orderry "Disable editing after creating") once they already hold a value.
  render(fields, values, prefix, opts) {
    values = values || {};
    const editing = !!(opts && opts.editing);
    if (!fields || !fields.length) return '';
    const rows = fields.map(f => {
      const id = `${prefix}-${f.field_key}`;
      const v = values[f.field_key];
      if (f.field_type === 'header')
        return `<div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:12px 0 6px">${f.field_label}</div>`;
      const locked = editing && f.lock_after_create && !(v == null || v === '');
      const dis = locked ? ' disabled' : '';
      let input;
      if (f.field_type === 'textarea' || f.field_type === 'address') input = `<textarea class="field-textarea" id="${id}"${dis} placeholder="${this._esc(f.placeholder || (f.field_type === 'address' ? 'Street, city, region, postcode' : ''))}">${this._esc(v)}</textarea>`;
      else if (f.field_type === 'number') input = `<input class="field-input" type="number" id="${id}"${dis} value="${this._esc(v)}" placeholder="${this._esc(f.placeholder)}">`;
      else if (f.field_type === 'date') input = `<input class="field-input" type="date" id="${id}"${dis} value="${this._esc(v)}">`;
      else if (f.field_type === 'datetime') input = `<input class="field-input" type="datetime-local" id="${id}"${dis} value="${this._esc(v)}">`;
      else if (f.field_type === 'bool') return `<div class="field"><label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--tx-2);cursor:pointer"><input type="checkbox" id="${id}"${dis} ${v ? 'checked' : ''} style="width:auto;accent-color:var(--blue)"> ${f.field_label}</label></div>`;
      else if (f.field_type === 'select') input = `<select class="field-select" id="${id}"${dis}><option value=""${v ? '' : ' selected'}>—</option>${(f.options || []).map(o => `<option ${v === o ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
      else input = `<input class="field-input" id="${id}"${dis} value="${this._esc(v)}" placeholder="${this._esc(f.placeholder)}">`;
      const tags = `${f.required ? ' *' : ''}${locked ? ' <span style="font-size:9.5px;font-weight:800;color:var(--tx-4)">· locked</span>' : ''}`;
      return `<div class="field"><label class="field-label">${f.field_label}${tags}</label>${input}</div>`;
    });
    return `<div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--g-border)"><div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em;margin:0 0 10px">Custom fields</div>${rows.join('')}</div>`;
  },
  collect(fields, prefix) {
    const out = {};
    (fields || []).forEach(f => {
      if (f.field_type === 'header') return;
      const el = document.getElementById(`${prefix}-${f.field_key}`);
      if (!el) return;
      out[f.field_key] = f.field_type === 'bool' ? el.checked
        : f.field_type === 'number' ? (el.value === '' ? null : parseFloat(el.value))
        : (el.value || null);
    });
    return out;
  },
  // returns the label of the first empty required (non-bool) field, or null if all good
  validate(fields, prefix) {
    for (const f of (fields || [])) {
      if (f.field_type === 'header' || f.field_type === 'bool' || !f.required) continue;
      const el = document.getElementById(`${prefix}-${f.field_key}`);
      if (el && el.disabled) continue;               // locked & pre-set — skip
      const val = el ? (el.value || '').trim() : '';
      if (!val) return f.field_label;
    }
    return null;
  },
};
