/* Sales — Build 144an: Quick POS. Category rail + searchable/scannable product & service
   grid + live cart. Products are findable by name, SKU, barcode and serial number.
   One warehouse per sale; exact FIFO cost, so estimated profit is real. */
const SalesPage = {
  _parts: [], _services: [], _accounts: [], _cats: [], _tplKey: null,
  _cart: [], _client: null, _cat: 'all', _q: '', _view: 'grid',
  _discPct: 0, _tender: '', _cashSel: null, _qtyPrefix: '', _held: [],

  async load() {
    try {
      [this._parts, this._services, this._accounts] = await Promise.all([
        get('/api/inventory/parts?limit=500').then(r => r.items || []),
        get('/api/catalog/search?type=service').then(r => r.items || []).catch(() => []),
        get('/api/cash/accounts').then(r => r.items || []).catch(() => []),
      ]);
    } catch (e) { return Toast.error(e.message); }
    try { this._tplKey = (await get('/api/settings/industry-template')).current || null; } catch { this._tplKey = null; }
    await this._loadCats();
    this._tender = this._accounts[0]?.id || '';
    this._topbar();
    setContent(`<div class="pos">
      <aside class="pos-rail" id="pos-rail"></aside>
      <main class="pos-center">
        <div class="pos-tools">
          <div class="pos-search"><span style="display:flex;color:var(--tx-3)">${icon('search', 15)}</span>
            <input id="pos-q" placeholder="Search or scan — name, SKU, barcode, serial…" autocomplete="off"
              oninput="SalesPage.onSearch(this.value)" onkeydown="if(event.key==='Enter')SalesPage.scan(this.value)"></div>
          <div class="pos-scan" onclick="SalesPage.focusSearch()">${icon('box', 15)} Scan</div>
          <div class="pos-seg">
            <b id="pos-vg" class="on" onclick="SalesPage.setView('grid')">Grid</b>
            <b id="pos-vk" onclick="SalesPage.setView('keypad')">Keypad</b>
          </div>
        </div>
        <div class="pos-grid" id="pos-grid"></div>
      </main>
      <aside class="pos-cart" id="pos-cart"></aside>
    </div>`);
    this.renderRail();
    this.renderGrid();
    this.renderCart();
  },

  _topbar() {
    setTopbarActions(
      `${this._held.length ? `<button class="btn btn-glass" onclick="SalesPage.resumeHeld()">${icon('hourglass', 14)} Held (${this._held.length})</button>` : ''}` +
      `<button class="btn btn-glass" onclick="SalesPage.openRecent()">${icon('list', 14)} Recent sales</button>`);
  },

  /* ── categories (managed tree, template-scoped) ── */
  async _loadCats() {
    try {
      const d = await get(`/api/catalog/categories?entity_type=product${this._tplKey ? '&template_key=' + encodeURIComponent(this._tplKey) : ''}`);
      const flat = []; const walk = (n) => n.forEach(x => { flat.push({ id: x.id, name: x.name, count: x.count }); if (x.children?.length) walk(x.children); });
      walk(d.tree || []); this._cats = flat;
    } catch { this._cats = []; }
  },
  renderRail() {
    const el = document.getElementById('pos-rail'); if (!el) return;
    const prod = this._parts.length, svc = this._services.length;
    const chips = [
      { key: 'all', label: 'All items', icon: 'grid', count: prod + svc },
      { key: 'products', label: 'Products', icon: 'box', count: prod },
      { key: 'services', label: 'Services', icon: 'bolt', count: svc },
      ...this._cats.map(c => ({ key: 'catid:' + c.id, label: c.name, icon: 'box', count: c.count })),
    ];
    el.innerHTML = `<div style="font-size:10px;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:var(--tx-3);padding:4px 8px 6px">Categories</div>` +
      chips.map(c => `<div class="pos-cat ${this._cat === c.key ? 'on' : ''}" onclick="SalesPage.setCat('${c.key.replace(/'/g, "\\'")}')">${icon(c.icon, 16)}<span>${c.label}</span><span class="c">${c.count}</span></div>`).join('') +
      `<div class="pos-cat" style="margin-top:auto;color:var(--tx-3)" onclick="SalesPage.manageCats()">${icon('plus', 15)}<span>Manage categories</span></div>`;
  },
  setCat(k) { this._cat = k; this.renderRail(); this.renderGrid(); },
  manageCats() {
    if (typeof CategoryManager === 'undefined') return;
    CategoryManager.open({ template_key: this._tplKey, onChange: async () => { await this._loadCats(); this.renderRail(); this.renderGrid(); } });
  },

  /* ── grid / search / scan ── */
  _items() {
    const q = this._q.toLowerCase().trim();
    const catId = this._cat.startsWith('catid:') ? this._cat.slice(6) : null;
    const wantProd = this._cat === 'all' || this._cat === 'products' || !!catId;
    const wantSvc = this._cat === 'all' || this._cat === 'services';
    let out = [];
    if (wantProd) {
      out = out.concat(this._parts.filter(p =>
        (!catId || p.category_id === catId) &&
        (!q || [p.name, p.sku, p.barcode, p.code].some(v => (v || '').toLowerCase().includes(q)))
      ).map(p => ({ type: 'product', ref_id: p.id, name: p.name, price: p.sell_price || 0,
        cost: p.avg_cost || p.cost_price || 0, stock: p.qty_available ?? 0, low: p.low_stock,
        sku: p.sku, sub: p.sku ? 'SKU ' + p.sku : (p.category || 'product') })));
    }
    if (wantSvc) {
      out = out.concat(this._services.filter(s => !q || (s.name || '').toLowerCase().includes(q))
        .map(s => ({ type: 'service', ref_id: s.ref_id, name: s.name, price: s.price || 0, cost: 0, sub: 'service' })));
    }
    return out;
  },
  renderGrid() {
    const el = document.getElementById('pos-grid'); if (!el) return;
    if (this._view === 'keypad') { this._renderKeypad(el); return; }
    el.style.display = 'grid';
    const items = this._items();
    if (!items.length) { el.style.display = 'block'; el.innerHTML = `<div class="pos-empty">No items match. Try a different search, or scan a barcode / serial.</div>`; return; }
    el.innerHTML = items.map((it, i) => {
      const isSvc = it.type === 'service';
      const tint = isSvc ? 'rgba(155,123,244,.14)' : 'rgba(79,142,247,.12)';
      const pill = isSvc ? '<span class="pos-pill svc">Service</span>'
        : (it.stock <= 0 ? '<span class="pos-pill out">Out</span>' : it.low ? '<span class="pos-pill low">Low</span>' : '');
      const st = isSvc ? '&nbsp;' : `${it.stock} in stock`;
      return `<div class="pos-tile" onclick="SalesPage.pick(${i})">${pill}
        <div class="pos-thumb" style="background:${tint};color:${isSvc ? 'var(--purple)' : 'var(--blue)'}">${icon(isSvc ? 'bolt' : 'box', 22)}</div>
        <div class="nm">${it.name}</div><div class="pr">${fmt.currency(it.price)}</div><div class="st">${st}</div></div>`;
    }).join('');
    this._gridItems = items;
  },
  _renderKeypad(el) {
    el.style.display = 'block';
    el.innerHTML = `<div class="pos-qtynote" style="margin-bottom:8px">Quantity for the next item: <b style="color:var(--tx-1);font-size:15px">${this._qtyPrefix || '1'}</b></div>
      <div class="pos-keypad">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => `<div class="pos-key" onclick="SalesPage.keypad('${n}')">${n}</div>`).join('')}
        <div class="pos-key" onclick="SalesPage.keypad('C')">C</div>
        <div class="pos-key" onclick="SalesPage.keypad('0')">0</div>
        <div class="pos-key" onclick="SalesPage.setView('grid')">${icon('box', 18)}</div></div>
      <div class="pos-qtynote" style="margin-top:10px">Type a quantity, then switch to Grid and tap an item.</div>`;
  },
  keypad(k) {
    if (k === 'C') this._qtyPrefix = '';
    else this._qtyPrefix = (this._qtyPrefix + k).slice(0, 4).replace(/^0+/, '') || '';
    this.renderGrid();
  },
  setView(v) { this._view = v; document.getElementById('pos-vg')?.classList.toggle('on', v === 'grid'); document.getElementById('pos-vk')?.classList.toggle('on', v === 'keypad'); this.renderGrid(); },
  focusSearch() { document.getElementById('pos-q')?.focus(); },
  onSearch(v) { this._q = v; this.renderGrid(); },

  async scan(code) {
    code = (code || '').trim(); if (!code) return;
    // exact resolve by SKU / barcode / code / serial
    try {
      const r = await get(`/api/inventory/scan?code=${encodeURIComponent(code)}${(typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : ''}`);
      const p = r.product;
      this.add({ type: 'product', ref_id: p.id, name: p.name, price: p.sell_price || 0, cost: p.avg_cost || p.cost_price || 0 });
      Toast.success(`Added ${p.name}${r.matched_on === 'serial' ? ` · serial ${r.serial}` : ''}`);
      const q = document.getElementById('pos-q'); if (q) { q.value = ''; this._q = ''; this.renderGrid(); }
    } catch (e) { /* not an exact code — leave the text as a filter */ }
  },

  pick(i) {
    const it = (this._gridItems || [])[i]; if (!it) return;
    const qty = parseInt(this._qtyPrefix, 10) || 1;
    this.add(it, qty);
    this._qtyPrefix = '';
  },
  add(it, qty = 1) {
    const ex = this._cart.find(l => l.ref_id === it.ref_id && l.type === it.type);
    if (ex) ex.qty += qty;
    else this._cart.push({ type: it.type, ref_id: it.ref_id, name: it.name, price: it.price, cost: it.cost || 0, qty });
    this.renderCart();
  },
  step(i, d) { const l = this._cart[i]; if (!l) return; l.qty = Math.max(1, l.qty + d); this.renderCart(); },
  removeLine(i) { this._cart.splice(i, 1); this.renderCart(); },
  clearCart() { this._cart = []; this._discPct = 0; this._cashSel = null; this.renderCart(); },

  /* ── cart ── */
  _subtotal() { return this._cart.reduce((a, l) => a + l.price * l.qty, 0); },
  _cost() { return this._cart.reduce((a, l) => a + (l.cost || 0) * l.qty, 0); },
  _total() { return Math.round(this._subtotal() * (1 - (this._discPct || 0) / 100) * 100) / 100; },

  renderCart() {
    const el = document.getElementById('pos-cart'); if (!el) return;
    const sub = this._subtotal(), total = this._total(), disc = sub - total, profit = total - this._cost();
    const c = this._client;
    const cashOpts = this._cart.length ? [total, Math.ceil(total / 100) * 100 + 0, Math.ceil(total / 500) * 500] : [];
    el.innerHTML = `
      <div class="pos-carth">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div style="font-size:15px;font-weight:900">Current sale</div>
          <div style="font-size:10.5px;color:var(--tx-3);font-weight:800">${this._cart.length} item${this._cart.length === 1 ? '' : 's'}</div>
        </div>
        <div class="pos-client" onclick="SalesPage.pickClient()">
          <div style="width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,#a7c4ff,#c9b6ff);display:grid;place-items:center;color:#2b2166;font-weight:800;font-size:12px">${c ? (c.name || '?').slice(0, 1).toUpperCase() : icon('user', 15)}</div>
          <div><div class="who">${c ? c.name : 'Walk-in'}</div><div class="sub">${c ? 'Customer attached' : 'No customer attached'}</div></div>
          <div class="act">${c ? icon('refresh', 13) + ' Change' : '＋ Add client'}</div>
        </div>
      </div>
      <div class="pos-lines">${this._cart.length ? this._cart.map((l, i) => `
        <div class="pos-line">
          <div class="li"><b>${l.name}</b><span>${l.type} · ${fmt.currency(l.price)}</span></div>
          <div class="pos-step"><button onclick="SalesPage.step(${i},-1)">−</button><span>${l.qty}</span><button onclick="SalesPage.step(${i},1)">+</button></div>
          <div class="amt">${fmt.currency(l.price * l.qty)}</div>
          <div class="del" onclick="SalesPage.removeLine(${i})">${icon('x', 14)}</div>
        </div>`).join('') : `<div class="pos-empty">${icon('receipt', 26)}<div style="margin-top:8px">Tap items to start a sale.<br>Scan a barcode or serial to add instantly.</div></div>`}</div>
      ${this._cart.length ? `<div class="pos-quick">
        <div class="pos-qbtn" onclick="SalesPage.setDiscount()">${icon('receipt', 13)} Discount${this._discPct ? ' · ' + this._discPct + '%' : ''}</div>
        <div class="pos-qbtn" onclick="SalesPage.hold()">${icon('hourglass', 13)} Hold</div>
        <div class="pos-qbtn" onclick="SalesPage.clearCart()">${icon('trash', 13)} Clear</div>
      </div>` : ''}
      <div class="pos-tot">
        <div class="pos-trow"><span>Subtotal</span><span>${fmt.currency(sub)}</span></div>
        ${disc > 0 ? `<div class="pos-trow"><span>Discount (${this._discPct}%)</span><span style="color:var(--green)">− ${fmt.currency(disc)}</span></div>` : ''}
        <div class="pos-trow big"><span>Total</span><span>${fmt.currency(total)}</span></div>
        <div class="pos-trow" style="margin-top:2px"><span style="color:var(--tx-4)">Est. profit</span><span style="color:${profit >= 0 ? 'var(--green)' : 'var(--red)'};font-weight:800">${fmt.currency(profit)}</span></div>
      </div>
      <div class="pos-tender">
        <select class="field-select" id="pos-acct" onchange="SalesPage._tender=this.value">
          <option value="">Take payment into…</option>
          ${this._accounts.map(a => `<option value="${a.id}" ${this._tender === a.id ? 'selected' : ''}>${a.name}</option>`).join('')}
        </select>
      </div>
      ${cashOpts.length ? `<div class="pos-cash">${[...new Set(cashOpts)].map(v => `<b class="${this._cashSel === v ? 'on' : ''}" onclick="SalesPage.pickCash(${v})">${fmt.currency(v)}</b>`).join('')}</div>` : ''}
      ${this._cashSel && this._cashSel > total ? `<div class="pos-trow" style="padding:0 15px 8px"><span>Change due</span><span style="font-weight:800">${fmt.currency(this._cashSel - total)}</span></div>` : ''}
      <div class="pos-pay"><button class="pos-charge" ${this._cart.length ? '' : 'disabled'} onclick="SalesPage.pay()">Take payment<span>${fmt.currency(total)} · complete sale</span></button></div>`;
  },
  pickCash(v) { this._cashSel = (this._cashSel === v ? null : v); this.renderCart(); },
  async setDiscount() {
    const v = await Modal.prompt('Order discount %', {default: this._discPct || ''});
    if (v === null) return;
    this._discPct = Math.min(100, Math.max(0, parseFloat(v) || 0));
    this.renderCart();
  },

  hold() {
    if (!this._cart.length) return;
    this._held.push({ cart: this._cart, client: this._client, disc: this._discPct });
    this._cart = []; this._client = null; this._discPct = 0; this._cashSel = null;
    this._topbar(); this.renderCart();
    Toast.success('Sale held');
  },
  resumeHeld() {
    if (!this._held.length) return;
    const h = this._held.pop();
    this._cart = h.cart; this._client = h.client; this._discPct = h.disc || 0;
    this._topbar(); this.renderCart();
  },

  /* ── client attach ── */
  pickClient() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Attach client</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="search-box" style="margin-bottom:10px"><span class="search-icon">${icon('search', 14)}</span>
          <input class="field-input search-input" id="pc-q" placeholder="Search name or phone…" oninput="SalesPage._pcSearch(this.value)"></div>
        <div id="pc-list" style="max-height:320px;overflow:auto"></div>
      </div>
      <div class="modal-footer">
        ${this._client ? `<button class="btn btn-glass" onclick="SalesPage._setClient(null)">Detach</button>` : ''}
        <button class="btn btn-primary" onclick="SalesPage.addClient()">＋ New client</button>
      </div>`);
    this._pcSearch('');
  },
  async _pcSearch(q) {
    const el = document.getElementById('pc-list'); if (!el) return;
    let items = [];
    try { items = (await get(`/api/clients?limit=12${q ? '&search=' + encodeURIComponent(q) : ''}`)).items || []; } catch {}
    el.innerHTML = items.length ? items.map(c => `
      <div class="pos-client" style="margin:0 0 6px" onclick="SalesPage._setClient('${c.id}','${(c.name || '').replace(/'/g, "\\'")}')">
        <div style="width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#a7c4ff,#c9b6ff);display:grid;place-items:center;color:#2b2166;font-weight:800;font-size:12px">${(c.name || '?').slice(0, 1).toUpperCase()}</div>
        <div><div class="who">${c.name}</div><div class="sub">${c.phone || 'no phone'} · ${c.order_count || 0} orders</div></div>
      </div>`).join('') : `<div style="color:var(--tx-4);font-size:12.5px;padding:14px;text-align:center">No matches — create a new client.</div>`;
  },
  _setClient(id, name) { this._client = id ? { id, name } : null; Modal.close(); this.renderCart(); },
  addClient() {
    if (typeof ClientsPage === 'undefined' || !ClientsPage._clientForm) return Toast.error('Client form unavailable');
    ClientsPage._clientForm(null, { stack: true, onCreated: (cli) => { this._client = { id: cli.id, name: cli.name }; Modal.close(); this.renderCart(); } });
  },

  /* ── complete sale ── */
  async pay() {
    if (!this._cart.length) return Toast.error('Add at least one item');
    try {
      const r = await post('/api/sales', {
        client_id: this._client?.id || null,
        account_id: this._tender || null,
        lines: this._cart.map(l => ({ kind: l.type, ref_id: l.ref_id, description: l.name, qty: l.qty, unit_price: l.price, discount: this._discPct || 0 })),
      });
      Toast.success(`${r.sale_number} completed — profit ${fmt.currency(r.profit)}`);
      this._cart = []; this._client = null; this._discPct = 0; this._cashSel = null;
      this.renderCart();
      // refresh stock counts so tiles stay accurate
      try { this._parts = (await get('/api/inventory/parts?limit=500')).items || this._parts; this.renderGrid(); this.renderRail(); } catch {}
    } catch (e) { Toast.error(e.message); }
  },

  /* ── recent sales (kept, now in a modal) ── */
  async openRecent() {
    let data;
    try { data = await get(`/api/sales${(typeof App !== 'undefined' && App.locParam) ? App.locParam('?') : ''}`); }
    catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Recent sales</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Sale</th><th>Total</th><th>Cost</th><th>Profit</th><th>Paid</th><th>When</th></tr></thead>
          <tbody>${(data.items || []).map(s => `<tr>
            <td class="tbl-strong">${s.sale_number}</td>
            <td>${fmt.currency(s.total)}</td><td>${fmt.currency(s.total_cost)}</td>
            <td style="color:${s.profit >= 0 ? 'var(--green)' : 'var(--red)'};font-weight:700">${fmt.currency(s.profit)}</td>
            <td>${s.paid ? '<span style="color:var(--green);font-weight:600">paid</span>' : '<span style="color:var(--tx-4)">unpaid</span>'}</td>
            <td style="color:var(--tx-3)">${new Date(s.created_at).toLocaleString()}</td>
          </tr>`).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:24px">No sales yet</td></tr>`}
          </tbody></table></div>
      </div>`, 'modal-wide');
  },
};

Router.register('sales', () => SalesPage.load());
