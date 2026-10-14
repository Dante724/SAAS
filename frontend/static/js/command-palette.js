/* ════════════════════════════════════════════════════
   Command palette (⌘K / Ctrl+K) — build 135.
   Jump to any page, run quick actions, and live-search
   work orders / customers / products. Keyboard-driven.
════════════════════════════════════════════════════ */
const CommandPalette = {
  _open: false, _idx: 0, _rows: [], _t: null, _seq: 0,

  NAV: [
    ['dashboard', 'Home · Dashboard'],
    ['orders', 'Work orders'],
    ['clients', 'Customers · Contacts'],
    ['suppliers', 'Suppliers'],
    ['inventory', 'Inventory'],
    ['products', 'Products catalog'],
    ['sales', 'Sales'],
    ['finance', 'Finance · Money'],
    ['cash', 'Cash'],
    ['bookings', 'Calendar · Bookings'],
    ['chats', 'Inbox · Chats'],
    ['leads', 'Leads · Inquiries'],
    ['tasks', 'Tasks'],
    ['reports', 'Reports'],
    ['settings', 'Settings'],
  ],

  _actions() {
    return [
      ['New work order', () => this._nav('orders', () => OrdersPage?.newModal?.())],
      ['New customer', () => this._nav('clients', () => ClientsPage?.newModal?.())],
      ['New supplier', () => this._nav('suppliers', () => window.SuppliersPage?.edit?.())],
      ['New product', () => this._nav('products', () => window.ProductsPage?.editModal?.())],
      ['New purchase order', () => this._nav('inventory', () => InventoryPage?.newPoModal?.())],
    ];
  },

  _nav(page, fn) { this.close(); try { Router.go(page); } catch (e) {} if (fn) setTimeout(fn, 340); },

  open() {
    if (this._open) return;
    this._open = true; this._idx = 0;
    let ov = document.getElementById('cmdk');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'cmdk';
      ov.className = 'cmdk-overlay';
      ov.innerHTML = `
        <div class="cmdk-panel" role="dialog" aria-label="Command palette">
          <div class="cmdk-search">
            <span class="cmdk-ico">${typeof icon === 'function' ? icon('search', 16) : '⌕'}</span>
            <input id="cmdk-input" placeholder="Search or type a command…" autocomplete="off" spellcheck="false">
            <span class="cmdk-hint">esc</span>
          </div>
          <div class="cmdk-list" id="cmdk-list"></div>
        </div>`;
      ov.addEventListener('mousedown', (e) => { if (e.target === ov) this.close(); });
      document.body.appendChild(ov);
      ov.querySelector('#cmdk-input').addEventListener('input', (e) => this.onType(e.target.value));
      ov.querySelector('#cmdk-input').addEventListener('keydown', (e) => this.onKey(e));
      // Delegated row handlers on the list container (which is never replaced —
      // only its innerHTML is), so selecting a result always works.
      const list = ov.querySelector('#cmdk-list');
      list.addEventListener('mousedown', (e) => {
        // activate on mousedown: immune to the row being re-rendered before mouseup
        const row = e.target.closest('.cmdk-row');
        if (!row) return;
        e.preventDefault();
        this.exec(Number(row.dataset.i));
      });
      list.addEventListener('mouseover', (e) => {
        const row = e.target.closest('.cmdk-row');
        if (row) this._hover(Number(row.dataset.i));
      });
    }
    ov.style.display = 'flex';
    const inp = ov.querySelector('#cmdk-input');
    inp.value = '';
    setTimeout(() => inp.focus(), 20);
    this.onType('');
  },

  close() {
    this._open = false;
    const ov = document.getElementById('cmdk');
    if (ov) ov.style.display = 'none';
  },

  onType(q) {
    const s = (q || '').trim().toLowerCase();
    // synchronous rows first (nav + actions), then merge live search
    const rows = [];
    this._actions().forEach(([label, run]) => {
      if (!s || label.toLowerCase().includes(s)) rows.push({ group: 'Actions', label, run });
    });
    this.NAV.forEach(([page, label]) => {
      if (!s || label.toLowerCase().includes(s) || page.includes(s)) rows.push({ group: 'Go to', label, run: () => this._nav(page) });
    });
    this._rows = rows; this._idx = 0; this.render();
    if (s.length >= 2) {
      clearTimeout(this._t);
      const seq = ++this._seq;
      this._t = setTimeout(() => this._live(s, seq), 180);
    }
  },

  async _live(s, seq) {
    const live = [];
    const enc = encodeURIComponent(s);
    try {
      const d = await get(`/api/clients?search=${enc}&limit=5`);
      (d.items || []).forEach(c => live.push({ group: 'Customers', label: c.name,
        sub: c.phone || c.email || '', run: () => this._nav('clients', () => ClientsPage?.viewClient?.(c.id)) }));
    } catch (e) {}
    try {
      const d = await get(`/api/orders?search=${enc}&limit=5`);
      (d.items || []).forEach(o => live.push({ group: 'Work orders', label: o.order_number,
        sub: (o.client && o.client.name) || o.complaint || '', run: () => this._nav('orders', () => OrdersPage?.viewOrder?.(o.id)) }));
    } catch (e) {}
    try {
      const d = await get(`/api/catalog/search?q=${enc}&type=product`);
      (d.items || []).slice(0, 5).forEach(p => live.push({ group: 'Products', label: p.name,
        sub: p.category || '', run: () => this._nav('products', () => window.ProductsPage?.editModal?.(p.ref_id)) }));
    } catch (e) {}
    if (seq !== this._seq || !this._open) return;   // a newer keystroke won
    // keep synchronous rows, append live
    this._rows = this._rows.concat(live);
    this.render();
  },

  render() {
    const list = document.getElementById('cmdk-list');
    if (!list) return;
    if (!this._rows.length) {
      list.innerHTML = `<div class="cmdk-empty">No matches. Try a page name, a customer, or a work order number.</div>`;
      return;
    }
    if (this._idx >= this._rows.length) this._idx = this._rows.length - 1;
    let html = '', lastGroup = null;
    this._rows.forEach((r, i) => {
      if (r.group !== lastGroup) { html += `<div class="cmdk-group">${r.group}</div>`; lastGroup = r.group; }
      // No inline handlers: the list uses delegated listeners (see open()), so a
      // row stays clickable even if live search re-renders it mid-click.
      html += `<div class="cmdk-row ${i === this._idx ? 'on' : ''}" data-i="${i}">
        <span class="cmdk-label">${this._esc(r.label)}</span>
        ${r.sub ? `<span class="cmdk-sub">${this._esc(r.sub)}</span>` : ''}</div>`;
    });
    list.innerHTML = html;
    const on = list.querySelector('.cmdk-row.on');
    if (on) on.scrollIntoView({ block: 'nearest' });
  },

  // Move the highlight WITHOUT rebuilding the list. Re-rendering on mouseenter
  // replaced the row's DOM node between mousedown and mouseup, so the browser
  // never fired a click and results looked unselectable. Toggle classes instead.
  _hover(i) {
    this._idx = i;
    const list = document.getElementById('cmdk-list');
    if (!list) return;
    list.querySelectorAll('.cmdk-row').forEach(el => {
      el.classList.toggle('on', Number(el.dataset.i) === i);
    });
  },
  onKey(e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); this._idx = Math.min(this._idx + 1, this._rows.length - 1); this.render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); this._idx = Math.max(this._idx - 1, 0); this.render(); }
    else if (e.key === 'Enter') { e.preventDefault(); this.exec(this._idx); }
    else if (e.key === 'Escape') { e.preventDefault(); this.close(); }
  },
  exec(i) { const r = this._rows[i]; if (r && r.run) r.run(); },
  _esc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; },
};

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    CommandPalette._open ? CommandPalette.close() : CommandPalette.open();
  }
});
