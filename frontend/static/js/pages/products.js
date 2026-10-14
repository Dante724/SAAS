/* ════════════════════════════════════════════════════
   My Company > Products — the product catalog master.
   List + category filter; create/edit card with a side panel for
   identifiers (+ barcode generator), UoM, stock rules, serialization
   (irreversible), variants, plus Excel import/export.
════════════════════════════════════════════════════ */
const ProductsPage = {
  cats: [], _tree: [], _rows: [], _selected: new Set(), _catFilter: '',

  async load() {
    this._selected = new Set();
    this._catFilter = '';
    this._bindMenuClose();
    this._renderTopbar(0);
    setContent(`
      <div class="pd">
        <aside class="pd-tree glass" id="pr-tree"></aside>
        <div class="pd-main">
          <div class="pd-toolbar">
            <button class="btn btn-primary" onclick="ProductsPage.editModal()">${icon('plus', 14)} Product</button>
            <button class="ic-btn" title="Columns & views" onclick="ProductsPage.columnsMenu()">${icon('bolt', 16)}</button>
            <div class="search-box" style="flex:1">
              <span class="search-icon">${icon('search', 14)}</span>
              <input class="field-input search-input" id="pr-search" placeholder="Search name, SKU, barcode…" oninput="ProductsPage.reload()">
            </div>
            <span id="pr-actions" class="pd-actions-inline" style="margin-left:auto"></span>
          </div>
          <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl" id="pr-list-table">
            <thead><tr>
              <th style="width:36px"><input type="checkbox" id="pr-all" onclick="ProductsPage.toggleAll(this.checked)"></th>
              <th>Product</th><th>Category</th><th>SKU / barcode</th><th>Sell</th><th>Margin</th><th>Stock</th><th>Type</th><th></th>
            </tr></thead>
            <tbody id="pr-tbody">${skeletonRows(8)}</tbody>
          </table></div></div>
        </div>
      </div>`);
    await this.loadCats();
    CategoryTree.mount({
      el: 'pr-tree', entity: 'product', activeId: this._catFilter,
      onSelect: (id) => { this._catFilter = id; this.reload(); },
      onChange: async () => { await this.loadCats(); this.reload(); },
    });
    this._renderTopbar(this._selected.size);   // paint Actions into the inline slot
    await this.reload();
  },

  // Build 144ap — page vs selection actions, both under one Actions menu (Work-orders pattern).
  _renderTopbar(count) {
    const wrap = (id, btn, items) =>
      `<div class="pd-menuwrap"><button class="btn ${count ? 'btn-primary' : 'btn-glass'}" onclick="ProductsPage.toggleMenu('${id}',event)">${btn} ${icon('chevron-down', 12)}</button>` +
      `<div class="pd-menu pd-menu-right" id="pd-menu-${id}" style="display:none">${items}</div></div>`;
    // Build 144db — Actions lives inline on the search row, beside "+ Product",
    // and stays there as you select (turns into "Actions · N" with bulk features).
    const slot = document.getElementById('pr-actions');
    const paint = (html) => { if (slot) slot.innerHTML = html; };
    if (count > 0) {
      paint(
        `<span class="pd-selpill">${count} selected</span>` +
        wrap('sel', `${icon('bolt', 14)} Actions · ${count}`,
          `<div class="pd-mi" onclick="ProductsPage.printLabels()">${icon('file', 14)} Print labels</div>` +
          `<div class="pd-mi" onclick="ProductsPage.bulkCode('code128')">${icon('box', 14)} Generate barcode</div>` +
          `<div class="pd-mi" onclick="ProductsPage.bulkCode('qr')">${icon('grid', 14)} Generate QR code</div>` +
          `<div class="pd-div"></div>` +
          `<div class="pd-mi" onclick="ProductsPage.moveCategory()">${icon('box', 14)} Move to category</div>` +
          `<div class="pd-mi" onclick="ProductsPage.bulkPrice()">${icon('coin', 14)} Bulk price update</div>` +
          `<div class="pd-mi" onclick="ProductsPage.doExport()">${icon('file', 14)} Export</div>` +
          `<div class="pd-div"></div>` +
          `<div class="pd-mi danger" onclick="ProductsPage.bulkDelete()">${icon('trash', 14)} Delete / write-off</div>`));
    } else {
      paint(
        wrap('page', `${icon('bolt', 14)} Actions`,
        `<div class="pd-mi" onclick="ProductsPage.manageCategories()">${icon('box', 14)} Manage categories</div>` +
        `<div class="pd-mi" onclick="Router.go('bins')">${icon('grid', 14)} Bins &amp; serials</div>` +
        `<div class="pd-div"></div>` +
        `<div class="pd-mi" onclick="ProductsPage.importModal()">${icon('paperclip', 14)} Import products</div>` +
        `<div class="pd-mi" onclick="ProductsPage.doExport()">${icon('file', 14)} Export products</div>`));
    }
  },
  toggleMenu(id, e) { if (e) e.stopPropagation(); const m = document.getElementById('pd-menu-' + id); if (m) m.style.display = m.style.display === 'none' ? 'block' : 'none'; },
  _bindMenuClose() {
    if (this._menuBound) return; this._menuBound = true;
    document.addEventListener('click', (e) => { if (!e.target.closest('.pd-menuwrap')) document.querySelectorAll('.pd-menu').forEach(m => m.style.display = 'none'); });
  },

  async loadCats() {
    try {
      const data = await get('/api/catalog/categories?entity_type=product');
      this._tree = data.tree || [];
      this.cats = [];
      const flat = (nodes, d) => nodes.forEach(n => { this.cats.push({ id: n.id, name: `${'— '.repeat(d)}${n.name}` }); if (n.children?.length) flat(n.children, d + 1); });
      flat(this._tree, 0);
      const sel = document.getElementById('p-cat');
      if (sel) sel.innerHTML = `<option value="">No category</option>` + this.cats.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    } catch (e) { /* categories optional */ }
  },

  // Build 144ax — the category rail is now the shared collapsible CategoryTree
  // (mounted in load). setCat kept for programmatic filter changes.
  setCat(id) { this._catFilter = id; if (typeof CategoryTree !== 'undefined') CategoryTree.select(id); else this.reload(); },

  catName(id) { const c = this.cats.find(x => x.id === id); return c ? c.name.replace(/— /g, '') : null; },

  // Build 144ao — open the full category manager, refresh filters/rows after changes.
  manageCategories() {
    CategoryManager.open({ onChange: async () => { await this.loadCats(); await this.reload(); } });
  },
  // Inline "＋" beside the product form's category select — open the add panel (stacked)
  // and select the new category once created.
  addCategoryInline() {
    CategoryManager.addPanel({
      stack: true,
      onDone: async (cat) => {
        await this.loadCats();
        const sel = document.getElementById('p-cat');
        if (sel && cat) {
          const opt = document.createElement('option');
          opt.value = cat.id; opt.textContent = cat.name; opt.selected = true;
          sel.appendChild(opt);
        }
      },
    });
  },

  async reload() {
    const s = document.getElementById('pr-search')?.value || '';
    const tbody = document.getElementById('pr-tbody');
    if (!tbody) return;
    try {
      const qs = [s ? 'search=' + encodeURIComponent(s) : '', this._catFilter ? 'category_id=' + this._catFilter : ''].filter(Boolean).join('&');
      const data = await get('/api/catalog/products' + (qs ? '?' + qs : ''));
      this._rows = data.items;
      if (!data.items.length) { tbody.innerHTML = emptyRow(9, 'box', 'No products here', 'Add one, or pick another category'); this._syncSel(); return; }
      const open = (id) => `onclick="ProductsPage.editModal('${id}')"`;
      tbody.innerHTML = data.items.map(p => {
        const m = p.margin_pct == null ? '—' : p.margin_pct + '%';
        const stockColor = p.low_stock ? 'var(--amber)' : 'var(--tx-1)';
        const type = p.serialized ? '<span class="badge b-prog">Serialized</span>'
          : (p.variant_count ? `<span class="badge b-new">${p.variant_count} variant${p.variant_count > 1 ? 's' : ''}</span>` : '<span style="color:var(--tx-4)">—</span>');
        const ck = this._selected.has(p.id) ? 'checked' : '';
        return `<tr data-id="${p.id}" class="${this._selected.has(p.id) ? 'row-sel' : ''}">
          <td onclick="event.stopPropagation()"><input type="checkbox" ${ck} onclick="ProductsPage.toggleRow('${p.id}',this.checked)"></td>
          <td ${open(p.id)}><span style="display:flex;align-items:center;gap:10px"><span onclick="event.stopPropagation()">${thumbImg(p.image_data)}</span><span style="font-weight:800">${p.name}</span></span></td>
          <td ${open(p.id)}>${p.category_id ? `<span class="badge b-new">${this.catName(p.category_id) || 'Category'}</span>` : '<span style="color:var(--tx-4)">—</span>'}</td>
          <td ${open(p.id)} style="color:var(--tx-2)">${p.sku || p.barcode || '—'}</td>
          <td ${open(p.id)}><span class="tbl-strong">${fmt.currency(p.sell_price)}</span>${p.repair_price && p.repair_price !== p.sell_price ? `<div style="font-size:10.5px;color:var(--tx-3)">repair ${fmt.currency(p.repair_price)}</div>` : ''}</td>
          <td ${open(p.id)} style="color:var(--tx-2)">${m}</td>
          <td ${open(p.id)}><span style="font-weight:800;color:${stockColor}">${(+p.qty_available).toLocaleString()}</span></td>
          <td ${open(p.id)}>${type}</td>
          <td><button class="btn btn-glass btn-sm" onclick="event.stopPropagation();ProductsPage.editModal('${p.id}')">Open</button></td>
        </tr>`;
      }).join('');
      this._syncSel();
      if (window.ColTools) ColTools.resizable('pr-list-table', 'products');
      if (window.ListExtras) {
        const byId = {}; data.items.forEach(p => { byId[p.id] = p; });
        ListExtras.attach('pr-list-table', 'product', byId);
      }
    } catch (e) { Toast.error(e.message); }
  },
  columnsMenu() { if (window.ListExtras) ListExtras.openMenu('product', () => this.reload()); },

  /* ── selection ── */
  toggleRow(id, ck) { ck ? this._selected.add(id) : this._selected.delete(id); this._syncSel(); },
  toggleAll(ck) { (this._rows || []).forEach(p => ck ? this._selected.add(p.id) : this._selected.delete(p.id)); this.reload(); },
  _syncSel() {
    this._renderTopbar(this._selected.size);
    const all = document.getElementById('pr-all');
    if (all) all.checked = (this._rows || []).length > 0 && this._rows.every(p => this._selected.has(p.id));
    document.querySelectorAll('#pr-tbody tr[data-id]').forEach(tr => tr.classList.toggle('row-sel', this._selected.has(tr.dataset.id)));
  },

  /* ── bulk actions ── */
  async bulkCode(type) {
    const ids = [...this._selected]; if (!ids.length) return;
    let ok = 0;
    for (const id of ids) { try { await post(`/api/catalog/products/${id}/barcode/generate?type=${type}`, {}); ok++; } catch (e) { /* skip */ } }
    Toast.success(`${type === 'qr' ? 'QR code' : 'Barcode'} generated for ${ok} product${ok === 1 ? '' : 's'}`);
    this.reload();
  },
  async printLabels() {
    const ids = [...this._selected]; if (!ids.length) return;
    let tpls = [];
    try { tpls = ((await get('/api/documents/templates')).items || []).filter(t => ['label', 'price_tag', 'product_label', 'vehicle_label'].includes(t.doc_type)); } catch (e) {}
    if (!tpls.length) return Toast.error('No label templates yet — add one in Settings → Document templates');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Print labels · ${ids.length} products</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Label template</label>
          <select class="field-select" id="lbl-tpl">${tpls.map(t => `<option value="${t.id}">${t.name}</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Copies of each</label>
          <input class="field-input" id="lbl-copies" type="number" value="1" min="1" max="100"></div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ProductsPage._doPrintLabels()">${icon('file', 14)} Print / PDF</button></div>`);
  },
  async _doPrintLabels() {
    const ids = [...this._selected];
    const tid = document.getElementById('lbl-tpl').value;
    const copies = parseInt(document.getElementById('lbl-copies').value, 10) || 1;
    try {
      const r = await post('/api/documents/render-labels', { template_id: tid, part_ids: ids, copies });
      const w = window.open('', '_blank');
      if (w) { w.document.write(`<html><head><title>${r.name}</title></head><body>${r.html}<script>window.onload=()=>window.print()<\/script></body></html>`); w.document.close(); }
      Modal.close(); Toast.success(`Rendered ${r.count} labels`);
    } catch (e) { Toast.error(e.message); }
  },
  moveCategory() {
    const ids = [...this._selected]; if (!ids.length) return;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Move ${ids.length} to category</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body"><div class="field"><label class="field-label">Category</label>
        <select class="field-select" id="mv-cat"><option value="">No category</option>${this.cats.map(c => `<option value="${c.id}">${c.name}</option>`).join('')}</select></div></div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ProductsPage._doMove()">Move</button></div>`);
  },
  async _doMove() {
    const ids = [...this._selected];
    const category_id = document.getElementById('mv-cat').value || null;
    try {
      const r = await post('/api/catalog/products/bulk', { ids, action: 'category', category_id });
      Modal.close(); Toast.success(`Moved ${r.count} product${r.count === 1 ? '' : 's'}`);
      this._selected.clear(); await this.loadCats(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },
  bulkPrice() {
    const ids = [...this._selected]; if (!ids.length) return;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Bulk price · ${ids.length} products</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Action</label>
          <select class="field-select" id="bp-mode"><option value="set">Set price to…</option><option value="pct">Adjust by percent…</option></select></div>
        <div class="field"><label class="field-label">Value</label><input class="field-input" id="bp-val" type="number" placeholder="e.g. 999 or 10"></div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ProductsPage._doPrice()">Apply</button></div>`);
  },
  async _doPrice() {
    const ids = [...this._selected];
    const mode = document.getElementById('bp-mode').value;
    const val = parseFloat(document.getElementById('bp-val').value);
    if (isNaN(val)) return Toast.error('Enter a value');
    const body = mode === 'set' ? { ids, action: 'price', price_set: val } : { ids, action: 'price', price_pct: val };
    try {
      const r = await post('/api/catalog/products/bulk', body);
      Modal.close(); Toast.success(`Updated ${r.count} price${r.count === 1 ? '' : 's'}`);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },
  async bulkDelete() {
    const ids = [...this._selected]; if (!ids.length) return;
    if (!await Modal.confirm(`Delete / write-off ${ids.length} product${ids.length === 1 ? '' : 's'}? They stop appearing in the catalog; history is kept.`, {danger:true})) return;
    try {
      const r = await post('/api/catalog/products/bulk', { ids, action: 'delete' });
      Toast.success(`Removed ${r.count} product${r.count === 1 ? '' : 's'}`);
      this._selected.clear(); await this.loadCats(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async editModal(id) {
    let p = null, variants = [], serials = [];
    if (id) {
      const data = await get(`/api/catalog/products/${id}`);
      p = data.product; variants = data.variants || []; serials = data.serials_in_stock || [];
    }
    // Build 144as — custom product fields configured in Settings → Forms → Products.
    this._productFields = await CustomFields.fetch('product');
    // suppliers for the "default supplier" picker
    let suppliers = [];
    try { suppliers = (await get('/api/suppliers')).items || []; } catch (e) {}
    const esc = (s) => String(s == null ? '' : s).replace(/"/g, '&quot;');
    const catOpts = `<option value="">No category</option>` +
      this.cats.map(c => `<option value="${c.id}" ${p && p.category_id === c.id ? 'selected' : ''}>${c.name}</option>`).join('');
    const uoms = [['pcs', 'Quantity › pcs'], ['set', 'Quantity › set'], ['m', 'Length › m'], ['kg', 'Weight › kg'], ['l', 'Volume › l'], ['hr', 'Time › hr']];
    const supOpts = `<option value="">— none —</option>` + suppliers.map(s => `<option value="${s.id}" ${p && p.default_supplier_id === s.id ? 'selected' : ''}>${esc(s.name)}</option>`).join('');
    const hasImg = !!(p && p.image_data);
    const showDim = !!(p && (p.weight != null || p.dimensions));
    const showWarr = !!(p && p.warranty_days);
    const showSup = !!(p && p.default_supplier_id);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${p ? p.name : 'New product'}</span>
        <button class="modal-close" onclick="Modal.close()">&times;</button></div>
      ${p ? `<div class="wo-tabs" style="padding:0 22px">
        <button class="wo-tab active" onclick="ProductsPage.pTab(event,'general')">General</button>
        <button class="wo-tab" onclick="ProductsPage.pTab(event,'stock')">Stock</button>
        <button class="wo-tab" onclick="ProductsPage.pTab(event,'batches')">Batches</button>
        <button class="wo-tab" onclick="ProductsPage.pTab(event,'history')">History</button>
        <button class="wo-tab" onclick="ProductsPage.pTab(event,'pos')">Purchase orders</button>
      </div>` : ''}
      <div class="modal-body">
       <div class="p-panel active" data-ppanel="general">
        <div class="pf-grid">
          <div class="pf-side">
            <div class="pf-drop" id="p-drop" onclick="ProductsPage.pickImage()"
                 ondragover="event.preventDefault();this.classList.add('over')"
                 ondragleave="this.classList.remove('over')"
                 ondrop="ProductsPage._dropImage(event)">
              <img id="p-img-preview" src="${p?.image_data || ''}" alt="" style="${hasImg ? '' : 'display:none'}">
              <div class="pf-drop-inner" id="p-drop-inner" style="${hasImg ? 'display:none' : ''}">
                <span class="pf-drop-ic">${icon('image', 26)}</span>
                <div class="pf-drop-t">Drop image or <b>browse</b><br>or take a photo</div>
              </div>
            </div>
            <button type="button" class="btn btn-glass btn-sm" onclick="ProductsPage.clearImage()" id="p-img-rm" style="margin-top:10px;${hasImg ? '' : 'display:none'}">Remove image</button>
            <div class="pf-hint">A square photo looks best.<br>PNG or JPG.</div>
            <input type="hidden" id="p-img" value="${esc(p?.image_data)}">
            <input type="hidden" id="p-brand" value="${esc(p?.brand)}">
            <input type="hidden" id="p-min" value="${p?.min_stock ?? 0}">
          </div>

          <div class="pf-main">
            <div class="pf-sec" style="margin-top:2px">Product details</div>
            <div class="pf-row2">
              <div class="field"><label class="field-label">Type</label>
                <select class="field-select" id="p-type"><option>Default</option></select></div>
              <div class="field"><label class="field-label">Category <span class="pf-req">*</span></label>
                <div class="pf-cat">
                  <select class="field-select" id="p-cat">${catOpts}</select>
                  <button type="button" class="btn btn-glass btn-sm pf-catbtn" onclick="ProductsPage.addCategoryInline()">${icon('plus', 13)} Category</button>
                </div></div>
            </div>
            <div class="field"><label class="field-label">Name <span class="pf-req">*</span></label>
              <input class="field-input" id="p-name" value="${esc(p?.name)}" placeholder="e.g. USB-C Cable 1m"></div>
            <div class="field"><label class="field-label">Description</label>
              <textarea class="field-textarea" id="p-desc" placeholder="Optional details">${p?.description || ''}</textarea></div>
            <div class="pf-row3">
              <div class="field"><label class="field-label">Unit <span class="pf-req">*</span></label>
                <select class="field-select" id="p-uom">${uoms.map(([v, l]) => `<option value="${v}" ${p?.uom === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
              <div class="field"><label class="field-label">Code</label><input class="field-input" id="p-code" value="${esc(p?.code)}"></div>
              <div class="field"><label class="field-label">SKU</label><input class="field-input" id="p-sku" value="${esc(p?.sku)}"></div>
            </div>
            <div class="pf-sec">Barcodes</div>
            <div class="field">
              <div class="pf-btnrow">
                <button type="button" class="btn btn-glass btn-sm" onclick="ProductsPage.toggleBarcode()">${icon('plus', 13)} Barcode</button>
                <button type="button" class="btn btn-glass btn-sm" onclick="ProductsPage.genCode('code128', ${p ? `'${p.id}'` : 'null'})">${icon('box', 13)} Generate barcode</button>
                <button type="button" class="btn btn-glass btn-sm" onclick="ProductsPage.genCode('qr', ${p ? `'${p.id}'` : 'null'})">${icon('grid', 13)} Generate QR</button>
              </div>
              <div id="p-bc-wrap" style="display:${p?.barcode ? 'flex' : 'none'};gap:6px;margin-top:8px">
                <input class="field-input" id="p-barcode" value="${esc(p?.barcode)}" placeholder="Barcode value" style="flex:1" oninput="ProductsPage._bcPreview()">
                <select class="field-select" id="p-btype" style="max-width:110px" onchange="ProductsPage._bcPreview()">${['code128', 'ean13', 'ean8', 'upca', 'qr'].map(t => `<option ${p?.barcode_type === t ? 'selected' : ''}>${t}</option>`).join('')}</select>
              </div>
              <div id="p-bc-preview" style="margin-top:8px">${p?.barcode ? '' : ''}</div>
            </div>
            <div class="pf-sec">Options</div>
            <div class="pf-checks">
              <label class="pf-check"><input type="checkbox" id="p-dimtoggle" ${showDim ? 'checked' : ''} onchange="ProductsPage.toggleWrap('p-dim-wrap',this.checked)"> Set dimensions &amp; weight</label>
              <div id="p-dim-wrap" class="pf-sub pf-dimgrid" style="display:${showDim ? 'grid' : 'none'}">
                <div class="field"><label class="field-label">Weight (kg)</label><input class="field-input" id="p-weight" type="number" step="0.001" value="${p?.weight ?? ''}"></div>
                <div class="field"><label class="field-label">Dimensions (L×W×H cm)</label><input class="field-input" id="p-dimensions" value="${esc(p?.dimensions)}"></div>
              </div>
              <label class="pf-check"><input type="checkbox" id="p-warrtoggle" ${showWarr ? 'checked' : ''} onchange="ProductsPage.toggleWrap('p-warr-wrap',this.checked)"> Warranty to client</label>
              <div id="p-warr-wrap" class="pf-sub" style="display:${showWarr ? 'block' : 'none'}">
                <div class="field"><label class="field-label">Warranty (days)</label><input class="field-input" id="p-warr" type="number" value="${p?.warranty_days ?? 0}"></div>
              </div>
              <label class="pf-check"><input type="checkbox" id="p-expiry" ${p?.expiry_tracking ? 'checked' : ''}> Expiration tracking</label>
              ${p && p.serialized
                ? `<label class="pf-check"><input type="checkbox" checked disabled> Enable serial-number tracking <span class="pf-tag">one-way</span></label>
                   <div class="pf-sub" style="color:var(--green);font-size:12px;font-weight:700">Serialized — ${serials.length} in stock</div>`
                : `<label class="pf-check"><input type="checkbox" id="p-serial"> Enable serial-number tracking <span class="pf-tag">one-way</span></label>`}
              <label class="pf-check"><input type="checkbox" id="p-suptoggle" ${showSup ? 'checked' : ''} onchange="ProductsPage.toggleWrap('p-sup-wrap',this.checked)"> Set default supplier</label>
              <div id="p-sup-wrap" class="pf-sub" style="display:${showSup ? 'block' : 'none'}">
                <div class="field"><label class="field-label">Default supplier</label><select class="field-select" id="p-supplier">${supOpts}</select></div>
              </div>
            </div>
            <div class="pf-sec">Pricing</div>
            <div class="field">
              <table class="pf-prices"><thead><tr><th>Price</th><th>Value</th></tr></thead><tbody>
                <tr><td>Cost price</td><td><input class="field-input" id="p-cost" type="number" step="0.01" value="${p?.cost_price ?? 0}"></td></tr>
                <tr><td>Store price</td><td><input class="field-input" id="p-sell" type="number" step="0.01" value="${p?.sell_price ?? 0}"></td></tr>
                <tr><td>Repair price <span style="color:var(--tx-4);font-size:11px">(on work orders)</span></td><td><input class="field-input" id="p-repair" type="number" step="0.01" placeholder="= store price" value="${p?.repair_price || ''}"></td></tr>
              </tbody></table>
            </div>
            ${p ? this.variantsBlock(p, variants) : ''}
          </div>
        </div>
        <div id="p-cf-wrap">${CustomFields.render(this._productFields || [], (p && p.custom_data) || {}, 'p-cf', { editing: !!p })}</div>
       </div>
       ${p ? `
       <div class="p-panel" data-ppanel="stock" style="display:none"><div id="p-stock"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
       <div class="p-panel" data-ppanel="batches" style="display:none"><div id="p-batches"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
       <div class="p-panel" data-ppanel="history" style="display:none"><div id="p-history"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
       <div class="p-panel" data-ppanel="pos" style="display:none"><div id="p-pos"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
       ` : ''}
      </div>
      <div class="modal-footer">
        ${p ? `<button class="btn btn-glass" onclick="ProductsPage.remove('${p.id}')" style="margin-right:auto">Delete</button>` : ''}
        ${p ? `<button class="btn btn-glass" onclick="ProductsPage.printLabel('${p.id}')">${icon('file',13)} Print label</button>` : ''}
        <button class="btn btn-ghost" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ProductsPage.submit(${p ? `'${p.id}'` : 'null'})">${p ? 'Save' : 'Create product'}</button>
      </div>`, 'modal-side-wide');
    if (p) this._activeProduct = p.id;
    if (p && p.barcode) setTimeout(() => ProductsPage._bcPreview(), 40);  // 144ef: show real code
  },

  pTab(e, panel) {
    document.querySelectorAll('.wo-tab').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    document.querySelectorAll('.p-panel').forEach(p => p.style.display = p.dataset.ppanel === panel ? 'block' : 'none');
    if (panel === 'stock') this._loadPStock();
    else if (panel === 'batches') this._loadPBatches();
    else if (panel === 'history') this._loadPHistory();
    else if (panel === 'pos') this._loadPPos();
  },

  _batchFilter: 'all',
  setBatchFilter(f, el) {
    this._batchFilter = f;
    el.parentElement.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    this._renderBatches();
  },
  async _loadPBatches() {
    const box = document.getElementById('p-batches'); if (!box || !this._activeProduct) return;
    try {
      this._batches = (await get(`/api/inventory/parts/${this._activeProduct}/batches`)).items || [];
      this._batchFilter = 'all';
      this._renderBatches();
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },
  _renderBatches() {
    const box = document.getElementById('p-batches'); if (!box) return;
    const all = this._batches || [];
    const rows = all.filter(b => this._batchFilter === 'all' ? true : this._batchFilter === 'in' ? b.in_stock : !b.in_stock);
    const seg = (v, l) => `<button class="tab ${this._batchFilter===v?'active':''}" onclick="ProductsPage.setBatchFilter('${v}',this)">${l}</button>`;
    box.innerHTML = `
      <div class="tabs" style="margin-bottom:12px">${seg('all','All')}${seg('in','In stock')}${seg('out','Out of stock')}</div>
      ${rows.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Document</th><th>Supplier</th><th style="text-align:right">Price</th><th style="text-align:right">Received</th><th style="text-align:right">Remaining</th><th style="text-align:right">Amount</th><th>Expiry</th><th>Received</th></tr></thead>
        <tbody>${rows.map(b=>`<tr>
          <td class="tbl-strong">${b.document || '<span style="color:var(--tx-4)">—</span>'}</td>
          <td style="color:var(--tx-2)">${b.supplier || '<span style="color:var(--tx-4)">—</span>'}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">${fmt.currency(b.unit_cost)}</td>
          <td style="text-align:right;color:var(--tx-3);font-variant-numeric:tabular-nums">${b.qty_received}</td>
          <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${b.in_stock?'var(--green)':'var(--tx-4)'}">${b.qty_remaining}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">${fmt.currency(b.amount)}</td>
          <td style="color:${b.expiry_date?'var(--tx-2)':'var(--tx-4)'};font-size:12px">${b.expiry_date?fmt.date(b.expiry_date):'—'}</td>
          <td style="color:var(--tx-3);font-size:12px">${fmt.date(b.received_at)}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No batches in this view. Batches are created when stock is received via a posting or purchase order.</div>'}`;
  },

  async _loadPStock() {
    const box = document.getElementById('p-stock'); if (!box || !this._activeProduct) return;
    try {
      const d = await get(`/api/inventory/parts/${this._activeProduct}/stock`);
      box.innerHTML = `
        <div class="detail-blob" style="margin-bottom:12px"><div class="d-label">Total available</div>
          <div class="d-value" style="font-size:20px;font-weight:900">${d.total_available}</div></div>
        ${d.items.length ? `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Warehouse / location</th><th style="text-align:right">Available</th><th style="text-align:right">Reserved</th></tr></thead>
          <tbody>${d.items.map(r=>`<tr><td class="tbl-strong">${r.location}</td>
            <td style="text-align:right;font-variant-numeric:tabular-nums">${r.available}</td>
            <td style="text-align:right;color:var(--tx-3);font-variant-numeric:tabular-nums">${r.reserved}</td></tr>`).join('')}</tbody>
        </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Not stocked in any warehouse yet.</div>'}`;
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async _loadPHistory() {
    const box = document.getElementById('p-history'); if (!box || !this._activeProduct) return;
    try {
      const rows = await get(`/api/inventory/parts/${this._activeProduct}/movements`);
      const typeLabel = { posting:'Posting', sale:'Sale', transfer:'Transfer', writeoff:'Write-off',
                          adjustment:'Adjustment', receipt:'Receipt', reservation:'Reservation' };
      box.innerHTML = rows.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Flow</th><th>Type</th><th>Reference</th><th>Location</th><th style="text-align:right">Qty</th><th>Date</th></tr></thead>
        <tbody>${rows.map(m=>{
          const inbound = m.qty >= 0;
          return `<tr>
          <td><span class="badge ${inbound?'b-done':'b-overdue'}" style="font-size:10px">${inbound?'▼ In':'▲ Out'}</span></td>
          <td class="tbl-strong">${typeLabel[m.type]||m.type}</td>
          <td style="color:var(--tx-3)">${m.ref_type||'—'}</td>
          <td style="color:var(--tx-3)">${m.location||'—'}</td>
          <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${inbound?'var(--green)':'var(--red)'}">${inbound?'+':''}${m.qty}</td>
          <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(m.at)}</td></tr>`;}).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No stock movements yet. Post, sell or transfer this product to build its history.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async _loadPPos() {
    const box = document.getElementById('p-pos'); if (!box || !this._activeProduct) return;
    try {
      const d = await get(`/api/inventory/parts/${this._activeProduct}/purchase-orders`);
      box.innerHTML = d.items.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>PO #</th><th>Status</th><th style="text-align:right">Ordered</th><th style="text-align:right">Received</th><th>Expected</th></tr></thead>
        <tbody>${d.items.map(po=>`<tr>
          <td class="tbl-strong">${po.po_number}</td><td>${badge(po.status)}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">${po.qty_ordered}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">${po.qty_received}</td>
          <td style="color:var(--tx-3);font-size:12px">${po.expected_at?fmt.date(po.expected_at):'—'}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No open purchase orders for this product.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  variantsBlock(p, variants) {
    return `<div class="side-h" style="font-weight:800;margin:16px 0 6px">Variants (size / color / config)</div>
      <div id="p-variants">${variants.length
        ? variants.map(v => `<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line)">
            <span>${v.name}</span><span style="color:var(--tx-2)">${fmt.currency(v.sell_price)}</span></div>`).join('')
        : '<p style="color:var(--tx-4);font-size:13px">No variants.</p>'}</div>
      <div style="display:flex;gap:6px;margin-top:8px">
        <input class="field-input" id="p-var-attr" placeholder='e.g. Color: Red' style="flex:1">
        <input class="field-input" id="p-var-price" type="number" step="0.01" placeholder="price" style="max-width:90px">
        <button class="btn btn-glass btn-sm" onclick="ProductsPage.addVariant('${p.id}')">+ Add</button>
      </div>`;
  },

  async submit(id) {
    const V = (i) => { const el = document.getElementById(i); return el ? el.value : ''; };
    const CK = (i) => { const el = document.getElementById(i); return !!(el && el.checked); };
    const dim = CK('p-dimtoggle'), warr = CK('p-warrtoggle'), sup = CK('p-suptoggle');
    const serEl = document.getElementById('p-serial');
    const body = {
      name: V('p-name').trim(),
      brand: V('p-brand').trim() || null,
      description: V('p-desc').trim() || null,
      category_id: V('p-cat') || null,
      cost_price: parseFloat(V('p-cost')) || 0,
      sell_price: parseFloat(V('p-sell')) || 0,
      repair_price: parseFloat(V('p-repair')) || 0,
      uom: V('p-uom'),
      sku: V('p-sku').trim() || null,
      code: V('p-code').trim() || null,
      barcode: V('p-barcode').trim() || null,
      barcode_type: V('p-btype'),
      min_stock: parseInt(V('p-min')) || 0,
      warranty_days: warr ? (parseInt(V('p-warr')) || 0) : 0,
      weight: dim ? (parseFloat(V('p-weight')) || null) : null,
      dimensions: dim ? (V('p-dimensions').trim() || null) : null,
      default_supplier_id: sup ? (V('p-supplier') || null) : null,
      image_data: V('p-img') || null,
      expiry_tracking: CK('p-expiry'),
      // no p-serial element means the product is already serialized → keep it true
      serialized: serEl ? serEl.checked : true,
      custom_data: CustomFields.collect(this._productFields || [], 'p-cf'),
    };
    if (!body.name) { Toast.error('Name is required'); return; }
    const missing = CustomFields.validate(this._productFields || [], 'p-cf');
    if (missing) { Toast.error(`"${missing}" is required`); return; }
    try {
      if (id) await put(`/api/catalog/products/${id}`, body);
      else await post('/api/catalog/products', body);
      Modal.close(); await this.reload();
      Toast.success(id ? 'Product saved' : 'Product created');
    } catch (e) { Toast.error(e.message); }
  },

  async genBarcode(id) {
    const type = document.getElementById('p-btype').value;
    try {
      const r = await post(`/api/catalog/products/${id}/barcode/generate?type=${type}`, {});
      document.getElementById('p-barcode').value = r.barcode;
      Toast.success('Barcode generated');
    } catch (e) { Toast.error(e.message); }
  },

  async serialize(id) {
    if (!(await confirm2('Enable serialization? This is irreversible — every unit will then require its own serial number.'))) return;
    try { await post(`/api/catalog/products/${id}/serialize`, {}); await this.editModal(id); }
    catch (e) { Toast.error(e.message); }
  },

  async addVariant(id) {
    const raw = document.getElementById('p-var-attr').value.trim();
    if (!raw || !raw.includes(':')) { Toast.error('Use "Attribute: Value", e.g. Color: Red'); return; }
    const [k, v] = raw.split(':').map(s => s.trim());
    const price = parseFloat(document.getElementById('p-var-price').value);
    try {
      await post(`/api/catalog/products/${id}/variants`, { variant_attrs: { [k]: v }, sell_price: isNaN(price) ? undefined : price });
      await this.editModal(id); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async remove(id) {
    if (!(await confirm2('Delete this product?'))) return;
    try { await del(`/api/catalog/products/${id}`); Modal.close(); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  async doExport() {
    try {
      const res = await fetch('/api/catalog/products/export', { headers: { Authorization: 'Bearer ' + Auth.token } });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = 'products.xlsx';
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) { Toast.error(e.message); }
  },

  // Build 144aq/144av — product image via the shared picker (take photo or choose file),
  // rendered as an Orderry-style drop-zone that also accepts drag-drop.
  pickImage() { PhotoInput.capture({ title: 'Product image', onPhoto: (dataUrl) => this._setImg(dataUrl) }); },
  _setImg(dataUrl) {
    const h = document.getElementById('p-img'); if (h) h.value = dataUrl;
    const prev = document.getElementById('p-img-preview'); if (prev) { prev.src = dataUrl; prev.style.display = ''; }
    const inner = document.getElementById('p-drop-inner'); if (inner) inner.style.display = 'none';
    const rm = document.getElementById('p-img-rm'); if (rm) rm.style.display = '';
  },
  _dropImage(e) {
    e.preventDefault();
    document.getElementById('p-drop')?.classList.remove('over');
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f || !f.type.startsWith('image/')) return;
    const r = new FileReader(); r.onload = () => this._setImg(r.result); r.readAsDataURL(f);
  },
  clearImage() {
    const h = document.getElementById('p-img'); if (h) h.value = '';
    const prev = document.getElementById('p-img-preview'); if (prev) { prev.src = ''; prev.style.display = 'none'; }
    const inner = document.getElementById('p-drop-inner'); if (inner) inner.style.display = '';
    const rm = document.getElementById('p-img-rm'); if (rm) rm.style.display = 'none';
  },
  toggleWrap(id, on) { const el = document.getElementById(id); if (el) el.style.display = on ? (id === 'p-dim-wrap' ? 'grid' : 'block') : 'none'; },
  toggleBarcode() { const w = document.getElementById('p-bc-wrap'); if (w) w.style.display = w.style.display === 'none' ? 'flex' : 'none'; this._bcPreview(); },
  _bcType() { const s = document.getElementById('p-btype'); return s ? s.value : 'code128'; },
  _bcPreview() {
    const b = document.getElementById('p-barcode'); if (!b) return;
    Barcode.render('p-bc-preview', (b.value || '').trim(), this._bcType());
  },
  async genCode(type, id) {
    const w = document.getElementById('p-bc-wrap'); if (w) w.style.display = 'flex';
    const bt = document.getElementById('p-btype'); if (bt) bt.value = type;
    const b = document.getElementById('p-barcode');
    // 144ef — a saved product gets a server-issued value; a new one gets a
    // client value from the shared Barcode helper so you don't have to save first.
    if (id) {
      try {
        const r = await post(`/api/catalog/products/${id}/barcode/generate?type=${type}`, {});
        if (b) b.value = r.barcode;
      } catch (e) { return Toast.error(e.message); }
    } else if (b) {
      b.value = Barcode.value('P', type);
    }
    this._bcPreview();
    Toast.success(type === 'qr' ? 'QR code generated' : 'Barcode generated');
  },

  async printLabel(id) {
    const { product: p } = await get(`/api/catalog/products/${id}`);
    const w = window.open('', '_blank', 'width=420,height=320');
    if (!w) { Toast.error('Allow pop-ups to print labels'); return; }
    w.document.write(`<!doctype html><html><head><title>Label — ${p.name}</title>
      <style>
        @page { size: 60mm 40mm; margin: 4mm; }
        body { font-family: -apple-system, Segoe UI, sans-serif; margin: 0; padding: 8px; }
        .label { border: 1px solid #ccc; border-radius: 6px; padding: 10px 12px; width: 220px; }
        .name { font-weight: 800; font-size: 14px; margin-bottom: 2px; }
        .meta { color: #555; font-size: 11px; }
        .price { font-size: 20px; font-weight: 800; margin: 6px 0; }
        .code { font-family: monospace; letter-spacing: 2px; font-size: 13px; border-top: 1px dashed #999; padding-top: 6px; }
        .bars { font-family: monospace; font-size: 26px; letter-spacing: -1px; line-height: 1; }
      </style></head><body>
      <div class="label">
        <div class="name">${p.name}</div>
        <div class="meta">${p.sku ? 'SKU ' + p.sku + ' · ' : ''}per ${p.uom}</div>
        <div class="price">${fmt.currency(p.sell_price)}</div>
        ${p.barcode ? `<div class="bars">|| ||| | || |||| | ||</div><div class="code">${p.barcode}</div>` : '<div class="meta">No barcode — generate one first</div>'}
      </div>
      <script>window.onload=()=>{window.print();}<\/script>
      </body></html>`);
    w.document.close();
  },

  importModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Import products from Excel</span>
        <button class="modal-close" onclick="Modal.close()">&times;</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-2);font-size:13px">Columns: Code, SKU, Barcode, <b>Name</b> (required, unique), Category (<code>A &gt; B &gt; C</code>), UoM, Cost price, Sell price, Min stock, Client warranty (days), Serialized (yes/no). Existing names are updated.</p>
        <input type="file" id="p-file" accept=".xlsx" class="field-input" style="margin-top:10px">
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ProductsPage.doImport()">Import</button>
      </div>`);
  },

  async doImport() {
    const f = document.getElementById('p-file').files[0];
    if (!f) { Toast.error('Choose an .xlsx file'); return; }
    const fd = new FormData(); fd.append('file', f);
    try {
      const res = await fetch('/api/catalog/products/import', { method: 'POST', headers: { Authorization: 'Bearer ' + Auth.token }, body: fd });
      if (!res.ok) throw new Error((await res.json()).detail || 'Import failed');
      const r = await res.json();
      Modal.close(); await this.loadCats(); await this.reload();
      Toast.success(`Imported — ${r.created} created, ${r.updated} updated`);
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('products', () => ProductsPage.load());
