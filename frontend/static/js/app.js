/* ════════════════════════════════════════════════════
   RepairOS — App controller, routing, page loader
════════════════════════════════════════════════════ */

const App = {
  me: null,
  // Build 137: global location scope. '' = all locations. Persisted per device.
  locationId: (typeof localStorage !== 'undefined' && localStorage.getItem('ros_loc')) || '',

  async mountLocationSwitcher() {
    const box = document.getElementById('loc-switcher');
    if (!box) return;
    let locs = [];
    try { locs = (await get('/api/staff/locations')).items.filter(l => l.active !== false); } catch { return; }
    // Build 144dg — clear a stale persisted location that no longer exists BEFORE the
    // single-branch early return; otherwise the list keeps filtering by a dead location
    // id and every order/estimate silently disappears.
    if (this.locationId && !locs.some(l => l.id === this.locationId)) {
      this.locationId = '';
      try { localStorage.setItem('ros_loc', ''); } catch (e) {}
    }
    if (locs.length < 2) { box.innerHTML = ''; return; }   // single branch: no switcher needed
    box.innerHTML = `
      <span class="loc-switcher-ico">${typeof icon === 'function' ? icon('box', 14) : ''}</span>
      <select id="loc-switcher-sel" onchange="App.setLocation(this.value)">
        <option value="" ${this.locationId === '' ? 'selected' : ''}>All locations</option>
        ${locs.map(l => `<option value="${l.id}" ${this.locationId === l.id ? 'selected' : ''}>${(l.name || '').replace(/</g, '&lt;')}</option>`).join('')}
      </select>`;
  },

  setLocation(id) {
    this.locationId = id || '';
    try { localStorage.setItem('ros_loc', this.locationId); } catch (e) {}
    if (typeof Router !== 'undefined' && Router.current) Router.go(Router.current);
  },
  locParam(prefix = '?') { return this.locationId ? `${prefix}location_id=${encodeURIComponent(this.locationId)}` : ''; },

  /* ── Role permissions in the UI (deny-wins, default-allow) ──
     can(code) is true unless the employee's role explicitly sets it to False —
     the same rule the API uses. Owners and full-access roles are unaffected, so
     nothing changes until someone actually unchecks a box in a role. */
  perms: {},
  isOwner: true,
  can(code) {
    if (!code) return true;
    if (this.isOwner) return true;
    return this.perms[code] !== false;
  },
  // sidebar page (data-page) -> the permission that grants access to that area
  NAV_PERM: {
    clients: 'can_view_client_info',
    finance: 'can_view_cashflow',
    cash: 'can_view_cashflow',
    settings: 'can_manage_settings',
  },
  // Hide anything the role denies: sidebar links, plus any element that carries
  // data-perm="code". Safe to call repeatedly and on any subtree.
  applyPermissions(root) {
    try {
      Object.entries(this.NAV_PERM).forEach(([page, code]) => {
        document.querySelectorAll(`.nav-link[data-page="${page}"]`).forEach(el => {
          el.style.display = this.can(code) ? '' : 'none';
        });
      });
      const scope = root && root.querySelectorAll ? root : document;
      scope.querySelectorAll('[data-perm]').forEach(el => {
        el.style.display = this.can(el.getAttribute('data-perm')) ? '' : 'none';
      });
    } catch (e) { /* never let permission painting break a render */ }
  },

  // Build 144L: re-pull the signed-in user's permissions after a role is edited,
  // so denied controls disappear in the current session (not only after a reload).
  // applyPermissions re-scans the whole document (nav + any on-screen data-perm
  // control) live; every later page render already re-applies on setContent.
  async refreshPerms() {
    try {
      const me = await get('/api/auth/me');
      this.me = me;
      this.perms = me.permissions || {};
      this.isOwner = me.is_owner === true;
    } catch (e) { return; }
    this.applyPermissions();
  },

  async boot() {
    // Build 144cb: reset-password / verify-email deep links take over before login.
    if (typeof AuthFlows !== 'undefined' && AuthFlows.intercept()) return;
    if (Auth.token) {
      try {
        const me = await get('/api/auth/me');
        this.me = me;
        this.perms = me.permissions || {};
        this.isOwner = me.is_owner === true;
        if (me.currency) fmt.setBase(me.currency);   // Build 125: format money in the org currency
        this.showApp();
        Router.go('dashboard');
        // Build 144ci: pull the user's saved language so it follows them across devices
        if (typeof I18n !== 'undefined') {
          get('/api/prefs/lang').then(r => {
            const l = r && r.value && r.value.lang;
            if (l && I18n.LANGS[l] && l !== I18n.lang) I18n.setLang(l);
          }).catch(() => {});
        }
      } catch { this.showAuth(); }
    } else {
      this.showAuth();
    }
  },

  showAuth() {
    document.getElementById('auth-view').style.display = 'flex';
    document.getElementById('app-view').style.display  = 'none';
    if (typeof I18n !== 'undefined') { try { I18n.boot(); } catch (e) {} }
  },

  showApp() {
    document.getElementById('auth-view').style.display = 'none';
    document.getElementById('app-view').style.display  = 'block';
    // Populate user pod
    const n = this.me?.name || this.me?.email || 'User';
    document.getElementById('user-name-pod').textContent = n;
    document.getElementById('user-role-pod').textContent = this.me?.role || 'staff';
    document.getElementById('user-avatar-pod').innerHTML = avatarHtml(n, 32);
    // v2 — mirror the avatar into the top bar (Stitch parity)
    try { const tap = document.getElementById('topbar-avatar-pod'); if (tap) tap.innerHTML = avatarHtml(n, 30); } catch (e) {}
    // mount the global time-clock widget once the shell is visible
    if (typeof TimeClock !== 'undefined') { try { TimeClock.mount(); } catch (e) {} }
    // Build 137: global location switcher in the topbar
    try { this.mountLocationSwitcher(); } catch (e) {}
    // hide sidebar areas this role can't access
    this.applyPermissions();
    // Build 144ci/cj: translate the shell. The language switch lives in the
    // per-user profile menu (App.toggleProfileMenu), not the topbar.
    if (typeof I18n !== 'undefined') { try { I18n.boot(); } catch (e) {} }
    if (typeof LayoutEditor !== 'undefined') { try { LayoutEditor.boot(); } catch (e) {} }  // Build 144cn
    // Build 144cd: force 2FA enrollment when the org mandates it and the user hasn't set it up
    if (typeof MfaGate !== 'undefined') { try { MfaGate.enforce(); } catch (e) {} }
  },

  logout() {
    Auth.clear(); this.me = null; this.showAuth();
    const w = document.getElementById('tc-widget'); if (w) w.remove();
    Toast.info('Signed out');
  },

  /* Build 144cj — per-user profile menu (opened from the sidebar user pod).
     Shows the person's own payroll and a language switch that changes the UI
     language for THEM only (persisted per-user), plus sign out. */
  toggleProfileMenu(ev) {
    if (ev) ev.stopPropagation();
    const existing = document.getElementById('profile-menu');
    if (existing) { existing.remove(); document.removeEventListener('click', this._pmClose, true); return; }
    const me = this.me || {};
    const langRow = (typeof I18n !== 'undefined')
      ? `<div class="pm-row"><span>${typeof I18n!=='undefined'?I18n.t('Language'):'Language'}</span>${I18n.switcherHtml()}</div>` : '';
    const el = document.createElement('div');
    el.id = 'profile-menu';
    el.className = 'profile-menu glass';
    el.innerHTML = `
      <div class="pm-head">
        ${avatarHtml(me.name || me.email || 'User', 34)}
        <div><div class="pm-name">${me.name || 'User'}</div><div class="pm-role">${me.role || 'staff'}</div></div>
      </div>
      <button class="pm-item" onclick="App.openMyPayroll()">${icon('wallet',15)} ${I18n.t('My payroll')}</button>
      ${langRow}
      <button class="pm-item" onclick="App.logout()">${icon('logout',15)} ${I18n.t('Sign out')}</button>`;
    document.body.appendChild(el);
    const pod = document.querySelector('.sidebar-footer');
    if (pod) { const r = pod.getBoundingClientRect(); el.style.left = (r.left + 6) + 'px'; el.style.bottom = (window.innerHeight - r.top + 8) + 'px'; }
    if (typeof I18n !== 'undefined') I18n.apply(el);
    // close on any outside click (next tick so this same click doesn't close it)
    this._pmClose = (e) => { if (!el.contains(e.target)) { el.remove(); document.removeEventListener('click', this._pmClose, true); } };
    setTimeout(() => document.addEventListener('click', this._pmClose, true), 0);
  },

  openMyPayroll() {
    const m = document.getElementById('profile-menu'); if (m) m.remove();
    if (!this.me || !this.me.id) return Toast.error('No profile loaded');
    openPrintPdf(`/api/print/payroll-statement/${this.me.id}`, 'payroll.pdf');
  },
};

/* ── AUTH HANDLER ── */
async function handleLogin() {
  const email = document.getElementById('a-email').value.trim();
  const pass  = document.getElementById('a-pass').value;
  if (!email || !pass) return Toast.error('Enter email and password');
  const btn = document.getElementById('a-btn');
  btn.textContent = 'Signing in…'; btn.disabled = true;
  try {
    const d = await apiLogin(email, pass);
    Auth.token = d.access_token;
    App.me = d;
    // Guarded root cutover: the redesign lives at "/". When we sign in from the
    // legacy login gateway (/login), hand off to the React app at the root.
    if (location.pathname !== '/') { location.replace('/'); return; }
    App.showApp();
    Router.go('dashboard');
    Toast.success(`Welcome back, ${d.name || email}!`);
  } catch (e) {
    Toast.error(e.message);
  } finally {
    btn.textContent = 'Sign in →'; btn.disabled = false;
  }
}

/* ── MY COMPANY tab strip (Orderry-style: one sidebar entry, horizontal tabs) ── */
const COMPANY_TABS = [
  ['dashboard',  'Dashboard'],
  ['timesheets', 'Employee hours'],
  ['services',   'Services'],
  ['products',   'Products'],
  ['bundles',    'Bundles'],
];

/* ── ROUTER ── */
const Router = {
  current: null,
  pages: {},

  register(name, fn) { this.pages[name] = fn; },

  go(name) {
    this.current = name;
    // leaving Settings: restore the real content/topbar functions
    if (name !== 'settings' && typeof SettingsPage !== 'undefined' && SettingsPage._shimActive) {
      SettingsPage.restore();
    }
    // leaving Inventory: restore the real setContent (tab shim)
    if (name !== 'inventory' && typeof InventoryPage !== 'undefined' && InventoryPage._shimActive) {
      InventoryPage._restoreShim();
    }
    // nav highlight — the five My-company tabs all light up the single sidebar entry
    const hl = (COMPANY_TABS.some(([k]) => k === name) || name === 'bins') ? 'mycompany' : name;
    document.querySelectorAll('.nav-link').forEach(el => {
      el.classList.toggle('active', el.dataset.page === hl);
    });
    // topbar title
    const titles = {
      dashboard:'My company', leads:'Inquiries & leads', chats:'Chats', bookings:'Bookings', orders:'Work orders', clients:'Contacts & devices',
      products:'My company', bundles:'My company',
      inventory:'Inventory', conversions:'Conversions', suppliers:'Suppliers', inbox:'Inbox', sales:'Sales', bins:'Bins & serialized stock', stocktake:'Stocktake', transfers:'Branch transfers', returns:'Returns & refunds', finance:'Finance', tasks:'Tasks', staff:'Staff',
      reports:'Reports & analytics',
      timesheets:'My company',
      services:'My company',
      assets:'Asset warehouse',
      cash:'Cash management',
      statuses:'Statuses & triggers',
      forms:'Forms editor',
      notifications:'Notifications',
      settings:'Settings',
    };
    document.getElementById('page-title').textContent = titles[name] || name;
    // clear topbar actions + any hoisted page toolbar (Build 144bw)
    document.getElementById('topbar-actions').innerHTML = '';
    App._pageToolbarHTML = '';
    // render page
    const fn = this.pages[name];
    if (fn) fn();
    else document.getElementById('content').innerHTML = `<div class="empty"><div class="empty-blob"></div><div class="empty-title">Page not found</div></div>`;

    // My-company tab strip is (re)injected by setContent once the page renders,
    // so it always reflects the *current* tab. Nothing to do here — but clear any
    // strip left over from the previous page in the (rare) case the next page does
    // not call setContent, so we never show a stale tab strip.
    if (!COMPANY_TABS.some(([k]) => k === name)) {
      document.querySelectorAll('#content .company-tabs').forEach(el => el.remove());
    }
  },
};

/* ── MY-COMPANY TAB STRIP ──
   Injected from setContent (runs synchronously right after content is replaced)
   so the strip is always present in the fresh markup and always highlights the
   active tab. This replaces an older approach that could short-circuit on the
   previous tab's leftover strip and then be wiped by setContent — which made the
   whole tab strip (and the way back to the other My-company tabs) disappear. */
window.injectCompanyTabs = function injectCompanyTabs() {
  const name = Router.current;
  const c = document.getElementById('content');
  if (!c) return;
  const inner = c.querySelector('.page-enter') || c.firstElementChild || c;
  if (!inner) return;
  // drop any existing strip so we render exactly one, matching the active tab
  inner.querySelectorAll('.company-tabs').forEach(el => el.remove());
  if (!COMPANY_TABS.some(([k]) => k === name)) return;
  const strip = document.createElement('div');
  strip.className = 'rep-tabs company-tabs';
  strip.style.marginBottom = '16px';
  strip.innerHTML = COMPANY_TABS.map(([k, l]) =>
    `<button class="rep-tab ${name === k ? 'active' : ''}" onclick="Router.go('${k}')">${l}</button>`).join('');
  inner.insertBefore(strip, inner.firstChild);
};

/* ── CONTENT HELPERS ── */
window.setContent = function setContent(html) {
  const c = document.getElementById('content');
  if (typeof BulkBar !== 'undefined') BulkBar.hide();   // Build 144cw: never let a bulk bar linger across pages
  c.innerHTML = `<div class="page-enter">${html}</div>`;
  window.injectCompanyTabs();
  App.applyPermissions(c);          // hide controls this role is denied
  window._renderPageToolbar();      // Build 144bw: hoist actions into a content toolbar
  if (typeof I18n !== 'undefined') I18n.apply(c);   // Build 144ci: translate rendered content
  if (typeof LayoutEditor !== 'undefined') { try { LayoutEditor.apply(); } catch (e) {} }  // Build 144cn
};

/* Build 144cz — touch fallback for double-click-to-open rows.
   Orderry-style list rows open on dblclick (mouse). Touch devices have no
   double-click, so on a coarse pointer a single tap on such a row re-dispatches
   as the row's own ondblclick — one global listener, no per-row changes, and it
   stays inert on desktop (fine pointer). Taps on inner controls are ignored. */
if (typeof window !== 'undefined' && typeof document !== 'undefined'
    && window.matchMedia && window.matchMedia('(pointer:coarse)').matches) {
  document.addEventListener('click', (e) => {
    const row = e.target.closest && e.target.closest('tr[ondblclick]');
    if (!row) return;
    if (e.target.closest('button,a,input,select,textarea,label,[onclick]')) return;
    row.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true }));
  });
}

/* Build 144bw — "Work-orders layout" app-wide.
   The top-right keeps only view switches (Table/Board/Scheduler). Every other
   page action — the primary "＋ create", secondary buttons and Filter — is
   hoisted into a left-aligned toolbar row at the top of the page content, so
   every tab reads like the Work Orders page. */
window.setTopbarActions = function setTopbarActions(html) {
  const bar = document.getElementById('topbar-actions');
  bar.innerHTML = html;
  App.applyPermissions(bar);
  const keep = [], move = [];
  [...bar.children].forEach(el => {
    // a "view switch" stays in the corner; everything else drops into the toolbar
    if (el.classList.contains('view-toggle') || el.classList.contains('vt-btn') ||
        el.querySelector?.('.vt-btn')) keep.push(el);
    else move.push(el);
  });
  App._pageToolbarHTML = move.map(el => el.outerHTML).join('');
  bar.innerHTML = '';
  keep.forEach(el => bar.appendChild(el));
  App.applyPermissions(bar);
  window._renderPageToolbar();
};

window._renderPageToolbar = function _renderPageToolbar() {
  const c = document.getElementById('content');
  if (!c) return;
  const inner = c.querySelector('.page-enter') || c;
  if (!inner) return;
  const html = App._pageToolbarHTML || '';
  let bar = inner.querySelector(':scope > .app-toolbar');
  if (!html) { if (bar) bar.remove(); return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.className = 'app-toolbar';
    const strip = inner.querySelector(':scope > .company-tabs');
    if (strip) strip.after(bar); else inner.insertBefore(bar, inner.firstChild);
  }
  bar.innerHTML = html;
  App.applyPermissions(bar);
  if (typeof LayoutEditor !== 'undefined') { try { LayoutEditor.apply(); } catch (e) {} }  // Build 144cn
};
function setPageSub(text) {
  let sub = document.getElementById('page-sub');
  if (!sub) {
    sub = document.createElement('div');
    sub.id = 'page-sub'; sub.className = 'page-sub';
    document.getElementById('page-title').after(sub);
  }
  sub.textContent = text;
}

/* Close the work-order Actions menu on any outside click */
document.addEventListener('click', (e) => {
  if (!e.target.closest('.wo-actions-wrap')) {
    document.querySelectorAll('.wo-actions-menu').forEach(m => m.style.display = 'none');
  }
});
