/* ════════════════════════════════════════════════════
   CatalogPicker — the "Service, labor, or product" field.
   Live-searches both catalogs; picking one adds a line
   with price + cost auto-filled. Also supports free-text.
════════════════════════════════════════════════════ */

const CatalogPicker = {
  lines: [],          // [{kind, ref_id, description, qty, unit_price, cost_price, available, technician_id, discount, discount_type, tax_id, tax_rate, tax_mode}]
  containerId: null,
  _debounce: null,
  _filter: 'all',     // all | service | product
  _taxes: [],
  _techs: [],

  async mount(containerId) {
    this.containerId = containerId;
    this.lines = [];
    this._filter = 'all';
    const c = document.getElementById(containerId);
    if (!c) return;
    // load taxes + technicians for per-line dropdowns
    try { this._taxes = (await get('/api/catalog/taxes')).items; } catch { this._taxes = []; }
    try { this._techs = await get('/api/staff/employees'); } catch { this._techs = []; }
    c.innerHTML = `
      <div class="cat-filter-row">
        ${[['all','All'],['service','Services'],['product','Products']].map(([v,l])=>
          `<button type="button" class="cat-filter-btn ${v==='all'?'active':''}" data-f="${v}" onclick="CatalogPicker.setFilter('${v}','${containerId}',this)">${l}</button>`).join('')}
      </div>
      <div class="cat-search-wrap">
        <div class="search-box">
          <span class="search-icon">${icon('search', 14)}</span>
          <input class="field-input search-input" id="cat-input-${containerId}"
            placeholder="Search a service or product, or type to add custom…" autocomplete="off"
            oninput="CatalogPicker.onType('${containerId}')"
            onfocus="CatalogPicker.onType('${containerId}')"
            onkeydown="CatalogPicker.onKey(event,'${containerId}')">
        </div>
        <div class="cat-dropdown" id="cat-drop-${containerId}"></div>
      </div>
      <div class="cat-lines" id="cat-lines-${containerId}"></div>`;
    this._render();
  },

  setFilter(f, cid, el) {
    this._filter = f;
    el.parentElement.querySelectorAll('.cat-filter-btn').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    const q = document.getElementById(`cat-input-${cid}`).value.trim();
    this._search(cid, q);
    document.getElementById(`cat-input-${cid}`).focus();
  },

  onType(cid) {
    clearTimeout(this._debounce);
    const q = document.getElementById(`cat-input-${cid}`).value.trim();
    this._debounce = setTimeout(() => this._search(cid, q), 200);
  },

  onKey(e, cid) {
    if (e.key === 'Enter') {
      e.preventDefault();
      const v = e.target.value.trim();
      if (v) { this.addCustom(v); e.target.value = ''; document.getElementById(`cat-drop-${cid}`).classList.remove('open'); }
    }
  },

  async _search(cid, q) {
    const drop = document.getElementById(`cat-drop-${cid}`);
    if (!drop) return;
    let items = [];
    try { items = (await get(`/api/catalog/search?q=${encodeURIComponent(q)}&type=${this._filter}`)).items; } catch {}
    let html = items.map(it => {
      const tag = it.kind === 'service'
        ? `<span class="cat-kind cat-kind-svc">service</span>`
        : it.kind === 'bundle'
        ? `<span class="cat-kind cat-kind-bundle">bundle · ${it.components || 0} items</span>`
        : `<span class="cat-kind cat-kind-prod">product${it.available != null ? ' · ' + it.available + ' in stock' : ''}</span>`;
      const low = it.kind === 'product' && it.available <= 0;
      return `<div class="cat-opt" onclick='CatalogPicker.pick(${JSON.stringify(it).replace(/'/g, "&#39;")})'>
        <div class="cat-opt-main">
          <div class="cat-opt-name">${this._esc(it.name)}</div>
          <div class="cat-opt-meta">${tag}${it.category ? ' · ' + this._esc(it.category) : ''}${low ? ' · <span style="color:var(--red)">out of stock</span>' : ''}</div>
        </div>
        <div class="cat-opt-price">${fmt.currency(it.price)}</div>
      </div>`;
    }).join('');
    const typed = document.getElementById(`cat-input-${cid}`).value.trim();
    html += `<div class="cat-opt cat-opt-custom" onclick="CatalogPicker.addCustom('${this._escAttr(typed)}')">
      <div class="cat-plus">${icon('plus', 14)}</div>
      <div class="cat-opt-main"><div class="cat-opt-name" style="color:var(--blue-dark)">${typed ? 'Add "' + this._esc(typed) + '" as custom line' : 'Add a custom line'}</div>
      <div class="cat-opt-meta">Free-text, set your own price</div></div>
    </div>`;
    drop.innerHTML = html;
    drop.classList.add('open');
  },

  _defaultTax() {
    const def = this._taxes.find(t => t.is_default);
    return def ? { tax_id: def.id, tax_rate: def.rate, tax_mode: def.mode }
               : { tax_id: '', tax_rate: 0, tax_mode: 'imposed' };
  },

  repairPricing: false,   // 144bk: work orders bill products at the repair price
  pick(it) {
    const price = (this.repairPricing && it.kind === 'product' && it.repair_price) ? it.repair_price : (it.price || 0);
    this.lines.push({
      kind: it.kind, ref_id: it.ref_id, description: it.name,
      qty: 1, unit_price: price, cost_price: it.cost_price || 0,
      available: it.available, technician_id: '',
      discount: 0, discount_type: 'pct', ...this._defaultTax(),
    });
    this._closeAndClear();
    this._render();
  },

  addCustom(text) {
    this.lines.push({
      kind: 'custom', ref_id: null, description: text || 'New line',
      qty: 1, unit_price: 0, cost_price: 0, available: null, technician_id: '',
      discount: 0, discount_type: 'pct', save_to_catalog: false, catalog_kind: 'service',
      ...this._defaultTax(),
    });
    this._closeAndClear();
    this._render();
  },

  _closeAndClear() {
    const input = document.getElementById(`cat-input-${this.containerId}`);
    if (input) input.value = '';
    document.getElementById(`cat-drop-${this.containerId}`)?.classList.remove('open');
  },

  remove(idx) { this.lines.splice(idx, 1); this._render(); },

  update(idx, field, val) {
    const numeric = ['qty','unit_price','discount'];
    this.lines[idx][field] = numeric.includes(field) ? (parseFloat(val) || 0) : val;
    if (field === 'tax_id') {
      const t = this._taxes.find(x => x.id === val);
      this.lines[idx].tax_rate = t ? t.rate : 0;
      this.lines[idx].tax_mode = t ? t.mode : 'imposed';
    }
    if (field === 'save_to_catalog') { this._render(); return; }   // toggles the kind selector
    this._renderTotals();
  },

  _lineTotal(l) {
    const base = (l.qty || 0) * (l.unit_price || 0);
    const afterDisc = l.discount_type === 'fixed'
      ? Math.max(base - (l.discount || 0), 0)
      : base * (1 - (l.discount || 0) / 100);
    if (l.tax_mode === 'included') return afterDisc;   // tax already inside
    return afterDisc * (1 + (l.tax_rate || 0) / 100);
  },

  _render() {
    const el = document.getElementById(`cat-lines-${this.containerId}`);
    if (!el) return;
    if (!this.lines.length) {
      el.innerHTML = `<div class="cat-empty">No items yet — search above to add services or products</div>`;
      return;
    }
    const kindBadge = {
      service: `<span class="cat-kind cat-kind-svc">service</span>`,
      product: `<span class="cat-kind cat-kind-prod">product</span>`,
      custom:  `<span class="cat-kind">custom</span>`,
    };
    el.innerHTML = `
      ${this.lines.map((l, i) => {
        const stockWarn = l.kind === 'product' && l.available != null && l.qty > l.available
          ? `<div class="cat-stock-warn">Only ${l.available} in stock — added without full deduction</div>` : '';
        return `<div class="cat-line-card">
          <div class="cat-line-row1">
            <div class="cat-line-name">${kindBadge[l.kind] || ''}${
              l.kind === 'custom'
                ? `<input class="field-input cat-line-name-input" value="${this._escAttr(l.description)}" placeholder="Name this service / product…" oninput="CatalogPicker.update(${i},'description',this.value)">`
                : `<span>${this._esc(l.description)}</span>`}</div>
            <button class="cat-line-del" onclick="CatalogPicker.remove(${i})" title="Remove">${icon('x', 13)}</button>
          </div>
          ${stockWarn}
          <div class="cat-line-grid">
            <label class="cat-f"><span>Qty</span><input class="field-input cat-line-input" type="number" min="0" step="1" value="${l.qty}" oninput="CatalogPicker.update(${i},'qty',this.value)"></label>
            <label class="cat-f"><span>Price</span><input class="field-input cat-line-input" type="number" min="0" value="${l.unit_price}" oninput="CatalogPicker.update(${i},'unit_price',this.value)"></label>
            <label class="cat-f"><span>Discount</span>
              <div class="cat-disc">
                <input class="field-input cat-line-input" type="number" min="0" value="${l.discount}" oninput="CatalogPicker.update(${i},'discount',this.value)">
                <select class="field-select cat-line-input" onchange="CatalogPicker.update(${i},'discount_type',this.value)">
                  <option value="pct"${l.discount_type==='pct'?' selected':''}>%</option>
                  <option value="fixed"${l.discount_type==='fixed'?' selected':''}>${fmt._sym}</option>
                </select>
              </div>
            </label>
            <label class="cat-f"><span>Tax</span>
              <select class="field-select cat-line-input" onchange="CatalogPicker.update(${i},'tax_id',this.value)">
                <option value="">No tax</option>
                ${this._taxes.map(t=>`<option value="${t.id}"${l.tax_id===t.id?' selected':''}>${t.name}${t.mode==='included'?' (incl)':''}</option>`).join('')}
              </select>
            </label>
            <label class="cat-f"><span>Technician</span>
              <select class="field-select cat-line-input" onchange="CatalogPicker.update(${i},'technician_id',this.value)">
                <option value="">—</option>
                ${this._techs.map(t=>`<option value="${t.id}"${l.technician_id===t.id?' selected':''}>${t.name}</option>`).join('')}
              </select>
            </label>
            <label class="cat-f cat-f-total"><span>Line total</span><div class="cat-line-total" id="cat-lt-${this.containerId}-${i}">${fmt.currency(this._lineTotal(l))}</div></label>
          </div>
          ${l.kind === 'custom' ? `<div style="display:flex;align-items:center;gap:12px;margin-top:9px;padding-top:9px;border-top:1px dashed var(--line)">
            <label class="cash-check" style="font-size:12px"><input type="checkbox" ${l.save_to_catalog?'checked':''} onchange="CatalogPicker.update(${i},'save_to_catalog',this.checked)"> Save to catalog</label>
            ${l.save_to_catalog
              ? `<select class="field-select cat-line-input" style="max-width:140px" onchange="CatalogPicker.update(${i},'catalog_kind',this.value)">
                   <option value="service"${l.catalog_kind!=='product'?' selected':''}>As service</option>
                   <option value="product"${l.catalog_kind==='product'?' selected':''}>As product</option>
                 </select>`
              : '<span style="font-size:11.5px;color:var(--tx-4)">One-off — this work order only</span>'}
          </div>` : ''}
        </div>`;
      }).join('')}
      <div class="cat-grand">Total: <span id="cat-grand-${this.containerId}">${fmt.currency(this.grandTotal())}</span></div>`;
  },

  _renderTotals() {
    this.lines.forEach((l, i) => {
      const cell = document.getElementById(`cat-lt-${this.containerId}-${i}`);
      if (cell) cell.textContent = fmt.currency(this._lineTotal(l));
    });
    const g = document.getElementById(`cat-grand-${this.containerId}`);
    if (g) g.textContent = fmt.currency(this.grandTotal());
  },

  grandTotal() { return this.lines.reduce((s, l) => s + this._lineTotal(l), 0); },

  payload() {
    return this.lines.map(l => ({
      line_type: l.kind === 'service' ? 'labour' : l.kind === 'product' ? 'part' : 'fee',
      kind: l.kind,
      ref_id: l.ref_id,
      description: l.description,
      qty: l.qty || 1,
      unit_price: l.unit_price || 0,
      cost_price: l.cost_price || 0,
      technician_id: l.technician_id || null,
      discount: l.discount || 0,
      discount_type: l.discount_type || 'pct',
      tax_id: l.tax_id || null,
      tax_rate: l.tax_rate || 0,
      tax_mode: l.tax_mode || 'imposed',
      save_to_catalog: !!l.save_to_catalog,
      catalog_kind: l.catalog_kind || 'service',
    }));
  },

  _esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; },
  _escAttr(s) { return (s || '').replace(/'/g, "\\'").replace(/"/g, '&quot;'); },
};

document.addEventListener('click', (e) => {
  if (!e.target.closest('.cat-search-wrap')) {
    document.querySelectorAll('.cat-dropdown.open').forEach(d => d.classList.remove('open'));
  }
});
