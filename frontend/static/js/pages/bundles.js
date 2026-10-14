/* My Company > Bundles — kits that explode into components on the order. */
const BundlesSettingsPage = {
  _items: [], _parts: [], _services: [], _comps: [], _bcats: [], _catFilter: '',

  async load() {
    this._catFilter = '';
    setTopbarActions('');
    setContent(`
      <div class="pd">
        <aside class="pd-tree glass" id="bd-tree"></aside>
        <div class="pd-main">
          <div class="pd-toolbar">
            <div class="search-box">
              <span class="search-icon">${icon('search', 14)}</span>
              <input class="field-input search-input" id="bd-search" placeholder="Search bundles…" oninput="BundlesSettingsPage.reload()">
            </div>
            <button class="btn btn-primary" onclick="BundlesSettingsPage.editModal()">${icon('plus', 14)} Bundle</button>
          </div>
          <div id="bd-list"></div>
        </div>
      </div>`);
    await this.loadCats();
    CategoryTree.mount({
      el: 'bd-tree', entity: 'bundle', activeId: this._catFilter,
      onSelect: (id) => { this._catFilter = id; this.reload(); },
      onChange: async () => { await this.loadCats(); this.reload(); },
    });
    await this.reload();
  },

  async loadCats() {
    try {
      const d = await get('/api/catalog/categories?entity_type=bundle');
      this._bcats = [];
      const flat = (nodes, dep) => nodes.forEach(n => { this._bcats.push({ id: n.id, name: `${'— '.repeat(dep)}${n.name}` }); if (n.children?.length) flat(n.children, dep + 1); });
      flat(d.tree || [], 0);
    } catch (e) { this._bcats = []; }
  },

  async reload() {
    const el = document.getElementById('bd-list');
    if (!el) return;
    const s = (document.getElementById('bd-search')?.value || '').toLowerCase();
    try {
      const data = await get('/api/bundles' + (this._catFilter ? '?category_id=' + this._catFilter : ''));
      this._items = data.items;
      const items = s ? data.items.filter(b => b.name.toLowerCase().includes(s)) : data.items;
      el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Bundle</th><th>Components</th><th>Pricing</th><th>Sells at</th><th style="width:140px"></th></tr></thead>
        <tbody>${items.map(b => `<tr>
          <td class="tbl-strong">${b.name}</td>
          <td style="color:var(--tx-3)">${b.components.map(c => `${c.qty}x ${c.name}`).join(', ')}</td>
          <td>${b.price_mode === 'fixed' ? 'Fixed kit price' : 'Sum of components'}</td>
          <td style="font-weight:700">${fmt.currency(b.effective_price)}</td>
          <td style="text-align:right">
            <button class="btn btn-glass btn-sm" onclick="BundlesSettingsPage.editModal('${b.id}')">Edit</button>
            <button class="btn btn-glass btn-sm" onclick="BundlesSettingsPage.remove('${b.id}')">Delete</button></td>
        </tr>`).join('') || `<tr><td colspan="5" style="text-align:center;color:var(--tx-4);padding:22px">No bundles here yet</td></tr>`}
        </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  addCategoryInline() {
    CategoryManager.addPanel({
      entity: 'bundle', stack: true,
      onDone: async (cat) => {
        await this.loadCats();
        const sel = document.getElementById('bd-cat');
        if (sel && cat) { const o = document.createElement('option'); o.value = cat.id; o.textContent = cat.name; o.selected = true; sel.appendChild(o); }
      },
    });
  },

  async editModal(id) {
    try {
      [this._parts, this._services] = await Promise.all([
        get('/api/inventory/parts').then(r => r.items),
        get('/api/catalog/search?type=service').then(r => r.items).catch(() => [])]);
    } catch (e) { return Toast.error(e.message); }
    if (!this._bcats.length) await this.loadCats();
    const b = id ? this._items.find(x => x.id === id) : null;
    this._comps = b ? b.components.map(c => ({ ref_type: c.ref_type, ref_id: c.ref_id, name: c.name, qty: c.qty })) : [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${b ? 'Edit bundle' : 'New bundle'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Bundle name</label>
          <input class="field-input" id="bd-name" value="${b ? b.name : ''}" placeholder="Screen replacement kit"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Add service</label>
            <select class="field-select" id="bd-svc" onchange="BundlesSettingsPage.addComp('service')">
              <option value="">— pick —</option>${this._services.map(s => `<option value="${s.ref_id}">${s.name}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Add product</label>
            <select class="field-select" id="bd-prod" onchange="BundlesSettingsPage.addComp('product')">
              <option value="">— pick —</option>${this._parts.map(p => `<option value="${p.id}">${p.name}</option>`).join('')}</select></div>
        </div>
        <div id="bd-comps"></div>
        <div class="field" style="margin-top:10px"><label class="field-label">Category</label>
          <div style="display:flex;gap:6px">
            <select class="field-select" id="bd-cat" style="flex:1"><option value="">No category</option>${this._bcats.map(c => `<option value="${c.id}" ${b && b.category_id === c.id ? 'selected' : ''}>${c.name}</option>`).join('')}</select>
            <button type="button" class="btn btn-glass btn-sm" onclick="BundlesSettingsPage.addCategoryInline()">${icon('plus', 14)} Category</button>
          </div></div>
        <div class="field-row" style="margin-top:10px">
          <div class="field"><label class="field-label">Pricing</label>
            <select class="field-select" id="bd-mode">
              <option value="sum" ${!b || b.price_mode === 'sum' ? 'selected' : ''}>Sum of components</option>
              <option value="fixed" ${b && b.price_mode === 'fixed' ? 'selected' : ''}>Fixed kit price</option></select></div>
          <div class="field"><label class="field-label">Fixed price (if fixed)</label>
            <input class="field-input" id="bd-price" type="number" value="${b ? b.price : 0}"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="BundlesSettingsPage.submit(${b ? `'${b.id}'` : 'null'})">${b ? 'Save' : 'Create bundle'}</button>
      </div>`);
    this.renderComps();
  },

  addComp(type) {
    const sel = document.getElementById(type === 'service' ? 'bd-svc' : 'bd-prod');
    if (!sel.value) return;
    const src = type === 'service' ? this._services : this._parts;
    const item = src.find(x => (type === 'service' ? x.ref_id : x.id) === sel.value);
    this._comps.push({ ref_type: type, ref_id: sel.value, name: item.name, qty: 1 });
    sel.value = '';
    this.renderComps();
  },

  renderComps() {
    const el = document.getElementById('bd-comps');
    if (!el) return;
    el.innerHTML = this._comps.length ? `<table class="tbl">
      <thead><tr><th>Component</th><th style="width:90px">Qty</th><th style="width:36px"></th></tr></thead>
      <tbody>${this._comps.map((c, i) => `<tr>
        <td>${c.name}<span style="color:var(--tx-4);font-size:11px"> · ${c.ref_type}</span></td>
        <td><input class="field-input" style="padding:4px 8px" type="number" value="${c.qty}" onchange="BundlesSettingsPage._comps[${i}].qty=parseFloat(this.value)||1"></td>
        <td><button class="btn btn-glass btn-sm" onclick="BundlesSettingsPage._comps.splice(${i},1);BundlesSettingsPage.renderComps()">×</button></td>
      </tr>`).join('')}</tbody></table>` :
      `<div style="text-align:center;color:var(--tx-4);font-size:12.5px;padding:12px 0">Add the services and products in this kit</div>`;
  },

  async submit(id) {
    const name = document.getElementById('bd-name').value.trim();
    if (!name) return Toast.error('Name is required');
    if (!this._comps.length) return Toast.error('Add at least one component');
    const body = { name, price_mode: document.getElementById('bd-mode').value,
      price: parseFloat(document.getElementById('bd-price').value) || 0,
      category_id: document.getElementById('bd-cat')?.value || null,
      components: this._comps.map(c => ({ ref_type: c.ref_type, ref_id: c.ref_id, qty: c.qty })) };
    try {
      if (id) await put(`/api/bundles/${id}`, body); else await post('/api/bundles', body);
      Modal.close(); Toast.success('Bundle saved'); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async remove(id) {
    if (!(await confirm2('Delete this bundle? Existing order lines stay.'))) return;
    try { await del(`/api/bundles/${id}`); await this.reload(); } catch (e) { Toast.error(e.message); }
  },
};

/* Promoted to a top-level My Company route. */
Router.register('bundles', () => BundlesSettingsPage.load());
