/* ════════════════════════════════════════════════════
   Services & Labors directory
════════════════════════════════════════════════════ */

const ServicesPage = {
  _cache: [], cats: [], _catFilter: '',

  async load() {
    this._catFilter = '';
    setTopbarActions('');
    // Build 144by — primary left of search; a flash-gear on the far right opens
    // category management. (Taxes live in Settings → Finance, so they're not here.)
    setContent(`
      <div class="pd">
        <aside class="pd-tree glass" id="sv-tree"></aside>
        <div class="pd-main">
          <div class="pd-toolbar">
            <button class="btn btn-primary" onclick="ServicesPage.newModal()">${icon('plus',14)} Service</button>
            <div class="search-box">
              <span class="search-icon">${icon('search',14)}</span>
              <input class="field-input search-input" id="sv-search" placeholder="Search services & labors…" oninput="ServicesPage.reload()">
            </div>
            <button class="ic-btn" style="margin-left:auto" title="Manage categories" onclick="ServicesPage.manageCategories()">${icon('bolt',16)}</button>
          </div>
          <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Service / labor</th><th>Category</th><th>Price</th><th>Cost</th><th>Margin</th><th>Warranty</th></tr></thead>
            <tbody id="sv-tbody">${skeletonRows(6)}</tbody>
          </table></div></div>
        </div>
      </div>`);
    await this.loadCats();
    if (typeof CategoryTree !== 'undefined') CategoryTree.mount({
      el: 'sv-tree', entity: 'service', activeId: this._catFilter,
      onSelect: (id) => { this._catFilter = id; this.reload(); },
      onChange: async () => { await this.loadCats(); this.reload(); },
    });
    await this.reload();
  },

  async loadCats() {
    try {
      const data = await get('/api/catalog/categories?entity_type=service');
      this.cats = [];
      const flat = (nodes, d) => nodes.forEach(n => { this.cats.push({ id: n.id, name: `${'— '.repeat(d)}${n.name}` }); if (n.children?.length) flat(n.children, d + 1); });
      flat(data.tree || [], 0);
      const sel = document.getElementById('sv-cat-sel');
      if (sel) sel.innerHTML = `<option value="">No category</option>` + this.cats.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
    } catch (e) { /* categories optional */ }
  },
  catName(id) { const c = this.cats.find(x => x.id === id); return c ? c.name.replace(/— /g, '') : null; },
  manageCategories() {
    if (typeof CategoryManager === 'undefined') return;
    CategoryManager.open({ entity: 'service', onChange: async () => { await this.loadCats(); await this.reload(); } });
  },
  addCategoryInline() {
    if (typeof CategoryManager === 'undefined') return;
    CategoryManager.addPanel({ entity: 'service', stack: true, onDone: async (cat) => {
      await this.loadCats();
      const sel = document.getElementById('sv-cat-sel');
      if (sel && cat) { const o = document.createElement('option'); o.value = cat.id; o.textContent = cat.name; o.selected = true; sel.appendChild(o); }
    } });
  },

  async reload() {
    const s = document.getElementById('sv-search')?.value || '';
    const tbody = document.getElementById('sv-tbody');
    if (!tbody) return;
    try {
      const qs = [s ? 'search=' + encodeURIComponent(s) : '', this._catFilter ? 'category_id=' + this._catFilter : ''].filter(Boolean).join('&');
      const data = await get(`/api/catalog/services?limit=200${qs ? '&' + qs : ''}`);
      if (!data.items.length) { tbody.innerHTML = emptyRow(6,'wrench','No services yet','Add labor items with preset prices'); this._cache = []; return; }
      tbody.innerHTML = data.items.map(sv => {
        const mColor = sv.margin_pct > 60 ? 'var(--green)' : sv.margin_pct > 30 ? 'var(--amber)' : 'var(--tx-2)';
        const catLabel = sv.category_name || sv.category;
        return `<tr style="cursor:pointer" ondblclick="ServicesPage.editModal('${sv.id}')">
          <td><span style="font-weight:800">${sv.name}</span></td>
          <td>${catLabel ? `<span class="badge b-new">${catLabel}</span>` : '<span style="color:var(--tx-4)">—</span>'}</td>
          <td><span class="tbl-strong">${fmt.currency(sv.price)}</span></td>
          <td style="color:var(--tx-2)">${fmt.currency(sv.cost_price)}</td>
          <td><span style="font-weight:800;color:${mColor}">${sv.margin_pct}%</span></td>
          <td style="color:var(--tx-3)">${sv.warranty_days ? sv.warranty_days + ' days' : '—'}</td>
        </tr>`;
      }).join('');
      this._cache = data.items;
    } catch (e) { Toast.error(e.message); }
  },

  newModal() { this._modal(); },
  editModal(id) { const s = this._cache.find(x => x.id === id); if (s) this._modal(s); },

  // Build 144ee — New/Edit service as a right-side drawer (Orderry New Service
  // parity, our WO/Products drawer pattern). Type (Labor/Service), description,
  // unit of measurement, warranty (d/mo), barcode, duration (h/min), cost+price
  // with a From-cost helper and live margin, and an online-booking toggle.
  UNITS: [['pc','Piece'],['h','Hour'],['job','Job'],['m','Metre'],['m2','Square metre'],
          ['kg','Kilogram'],['l','Litre'],['set','Set'],['visit','Visit']],

  _modal(sv = null) {
    const isEdit = !!sv;
    const wd = sv?.warranty_days || 0;
    const warUnit = (wd && wd % 30 === 0) ? 'mo' : 'd';
    const warVal = warUnit === 'mo' ? wd / 30 : wd;
    const dur = sv?.duration_min ?? 60;
    const durH = Math.floor(dur / 60), durM = dur % 60;
    const unit = sv?.unit || 'pc';
    const isLabor = !!(sv && sv.is_labor);
    const bookable = sv ? !!sv.bookable : true;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${isEdit?'Edit service':'New service'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Type</label>
          <div class="seg-pill" id="sv-type" style="max-width:240px">
            <button type="button" class="${isLabor?'on':''}" data-t="labor" onclick="ServicesPage._type('labor')">Labor</button>
            <button type="button" class="${isLabor?'':'on'}" data-t="service" onclick="ServicesPage._type('service')">Service</button>
          </div>
        </div>

        <div class="field"><label class="field-label">Category</label>
          <div style="display:flex;gap:6px">
            <select class="field-select" id="sv-cat-sel" style="flex:1">
              <option value="">No category</option>
              ${this.cats.map(c => `<option value="${c.id}" ${sv && sv.category_id === c.id ? 'selected' : ''}>${c.name}</option>`).join('')}
            </select>
            <button class="btn btn-glass btn-sm" type="button" title="New category" onclick="ServicesPage.addCategoryInline()">${icon('plus',14)} Category</button>
          </div></div>

        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="sv-name" value="${sv?this._esc(sv.name):''}" placeholder="e.g. Screen replacement labour"></div>

        <div class="field"><label class="field-label">Description</label><textarea class="field-input" id="sv-desc" rows="2" placeholder="What this service includes (optional)…">${sv?this._esc(sv.description||''):''}</textarea></div>

        <div class="field-row">
          <div class="field"><label class="field-label">Unit of measurement</label>
            <select class="field-select" id="sv-unit">
              ${this.UNITS.map(([v,l])=>`<option value="${v}" ${unit===v?'selected':''}>${l}</option>`).join('')}
            </select>
          </div>
          <div class="field"><label class="field-label">Warranty to client</label>
            <div style="display:flex;gap:8px;align-items:center">
              <input class="field-input" id="sv-war" type="number" value="${warVal||0}" min="0" style="flex:1">
              <div class="seg-pill" id="sv-war-unit">
                <button type="button" class="${warUnit==='d'?'on':''}" data-u="d" onclick="ServicesPage._warUnit('d')">days</button>
                <button type="button" class="${warUnit==='mo'?'on':''}" data-u="mo" onclick="ServicesPage._warUnit('mo')">months</button>
              </div>
            </div>
          </div>
        </div>

        <div class="field-row">
          <div class="field"><label class="field-label">Duration</label>
            <div style="display:flex;gap:8px;align-items:center">
              <input class="field-input" id="sv-dur-h" type="number" value="${durH}" min="0" style="width:70px"><span style="font-size:12px;color:var(--tx-3)">h</span>
              <input class="field-input" id="sv-dur-m" type="number" value="${durM}" min="0" max="59" style="width:70px"><span style="font-size:12px;color:var(--tx-3)">min</span>
            </div>
          </div>
          <div class="field"><label class="field-label">Barcode</label>
            <div style="display:flex;gap:6px">
              <input class="field-input" id="sv-barcode" value="${sv?this._esc(sv.barcode||''):''}" placeholder="Scan or type…" oninput="ServicesPage._bcPreview()" style="flex:1">
              <select class="field-select" id="sv-bc-type" style="max-width:110px" onchange="ServicesPage._bcPreview()">
                ${['code128','qr','ean13','ean8','upca'].map(t=>`<option value="${t}">${t}</option>`).join('')}
              </select>
              <button class="btn btn-glass btn-sm" type="button" title="Generate a scannable code" onclick="ServicesPage._bcGen()">${icon('box',13)} Generate</button>
            </div>
            <div id="sv-bc-preview" style="margin-top:8px"></div>
          </div>
        </div>

        <div class="field-row">
          <div class="field"><label class="field-label">Cost ${fmt._sym}</label><input class="field-input" id="sv-cost" type="number" value="${sv?.cost_price||0}" min="0"></div>
          <div class="field"><label class="field-label">Price ${fmt._sym}</label>
            <div style="display:flex;gap:6px">
              <input class="field-input" id="sv-price" type="number" value="${sv?.price||0}" min="0" style="flex:1">
              <button class="btn btn-glass btn-sm" type="button" title="Apply a 40% margin over cost" onclick="ServicesPage._priceFromCost()">${icon('bolt',13)} From cost</button>
            </div>
          </div>
        </div>
        <div style="font-size:11.5px;color:var(--tx-4);margin-top:-4px">Margin: <span id="sv-margin">—</span></div>

        <div id="sv-price-wrap" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line,rgba(0,0,0,.07))">
          <div class="field-label" style="margin-bottom:8px">Prices by price type</div>
          <div id="sv-prices"></div>
        </div>

        <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line,rgba(0,0,0,.07))">
          <label class="cash-check" style="display:flex;align-items:center;gap:9px;font-size:13px">
            <input type="checkbox" id="sv-bookable" ${bookable?'checked':''}> Available for online booking
          </label>
          <div style="font-size:11.5px;color:var(--tx-4);margin-top:4px;padding-left:24px">When on, clients can pick this service on your public booking page.</div>
          <div class="field" style="margin-top:12px"><label class="field-label">Employees who can perform</label>
            <div id="sv-emps" style="display:flex;flex-wrap:wrap;gap:10px;font-size:13px"></div>
          </div>
          <div class="field"><label class="field-label">Required resource types</label>
            <input class="field-input" id="sv-res" placeholder="e.g. Lift, Bay 2 (comma-separated)" value="${sv?this._esc((sv.required_resource_types||[]).join(', ')):''}">
          </div>
        </div>

        <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line,rgba(0,0,0,.07))">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <div class="field-label" style="margin:0">Employee exceptional commissions</div>
            <button class="btn btn-glass btn-sm" type="button" onclick="ServicesPage._addCommRow()">${icon('plus',13)} Add</button>
          </div>
          <div style="font-size:11.5px;color:var(--tx-4);margin-bottom:8px">Override the default commission for this service. Leave the employee as "All staff" for a service-wide rate.</div>
          <div id="sv-comm"></div>
        </div>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:8px">
          <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
          <button class="btn btn-primary" onclick="ServicesPage.submit(${sv?`'${sv.id}'`:'null'})">${isEdit?'Save':'Create'}</button>
        </div>
        ${isEdit?`<button class="btn btn-danger btn-sm" onclick="ServicesPage.del('${sv.id}')">Delete</button>`:''}
      </div>`, 'modal-side-wide');
    this._warUnitV = warUnit;
    this._typeV = isLabor ? 'labor' : 'service';
    this._allowedEmps = sv ? (sv.allowed_employee_ids || []).slice() : [];
    setTimeout(() => {
      this._recalcMargin();
      this._bcPreview();
      this._loadComms(sv ? sv.id : null);
      this._loadPrices(sv ? sv.id : null);
      ['sv-cost','sv-price'].forEach(id => { const e = document.getElementById(id); if (e) e.oninput = () => ServicesPage._recalcMargin(); });
    }, 40);
  },

  _type(t) {
    this._typeV = t;
    document.querySelectorAll('#sv-type button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  },

  _warUnit(u) {
    this._warUnitV = u;
    document.querySelectorAll('#sv-war-unit button').forEach(b => b.classList.toggle('on', b.dataset.u === u));
  },

  _priceFromCost() {
    const cost = parseFloat(document.getElementById('sv-cost').value) || 0;
    if (cost > 0) { document.getElementById('sv-price').value = Math.round(cost / 0.6); this._recalcMargin(); }
  },

  _bcType() { const s = document.getElementById('sv-bc-type'); return s ? s.value : 'code128'; },
  _bcGen() {
    const v = Barcode.value('SVC', this._bcType());
    document.getElementById('sv-barcode').value = v;
    this._bcPreview();
  },
  _bcPreview() {
    const v = (document.getElementById('sv-barcode').value || '').trim();
    Barcode.render('sv-bc-preview', v, this._bcType());
  },

  /* ── Employee exceptional commissions (Orderry parity) ── */
  _comms: [],          // [{id?, employee_id, calc_type, amount}]
  _commsRemoved: [],   // ids to delete on save
  _emps: [],

  async _loadComms(serviceId) {
    this._comms = []; this._commsRemoved = [];
    try { this._emps = await get('/api/staff/employees'); } catch { this._emps = []; }
    if (serviceId) {
      try { this._comms = (await get(`/api/catalog/services/${serviceId}/commissions`)).items || []; } catch { this._comms = []; }
    }
    this._renderComms();
    this._renderEmps();
  },

  _renderEmps() {
    const el = document.getElementById('sv-emps'); if (!el) return;
    if (!this._emps.length) { el.innerHTML = `<span style="font-size:12px;color:var(--tx-4)">No staff yet</span>`; return; }
    const sel = this._allowedEmps || [];
    el.innerHTML = `<label class="cash-check" style="font-size:12.5px"><input type="checkbox" ${sel.length===0?'checked':''} onchange="ServicesPage._empAll(this.checked)"> All</label>` +
      this._emps.map(e => `<label class="cash-check" style="font-size:12.5px"><input type="checkbox" ${sel.includes(e.id)?'checked':''} onchange="ServicesPage._empToggle('${e.id}',this.checked)"> ${this._esc(e.name)}</label>`).join('');
  },
  _empAll(on) { if (on) { this._allowedEmps = []; this._renderEmps(); } },
  _empToggle(id, on) {
    this._allowedEmps = this._allowedEmps || [];
    if (on) { if (!this._allowedEmps.includes(id)) this._allowedEmps.push(id); }
    else this._allowedEmps = this._allowedEmps.filter(x => x !== id);
    this._renderEmps();
  },

  _renderComms() {
    const el = document.getElementById('sv-comm'); if (!el) return;
    if (!this._comms.length) { el.innerHTML = `<div style="font-size:12px;color:var(--tx-4)">No exceptional commissions — the default payroll rules apply.</div>`; return; }
    el.innerHTML = this._comms.map((c, i) => `
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px">
        <select class="field-select" style="flex:1" onchange="ServicesPage._commSet(${i},'employee_id',this.value)">
          <option value="">All staff</option>
          ${this._emps.map(e => `<option value="${e.id}" ${c.employee_id===e.id?'selected':''}>${this._esc(e.name)}</option>`).join('')}
        </select>
        <input class="field-input" type="number" min="0" value="${c.amount}" style="width:90px" oninput="ServicesPage._commSet(${i},'amount',this.value)">
        <select class="field-select" style="max-width:90px" onchange="ServicesPage._commSet(${i},'calc_type',this.value)">
          <option value="percent" ${c.calc_type!=='flat'?'selected':''}>%</option>
          <option value="flat" ${c.calc_type==='flat'?'selected':''}>${fmt._sym}</option>
        </select>
        <button class="btn btn-glass btn-sm" type="button" title="Remove" onclick="ServicesPage._removeCommRow(${i})">${icon('x',13)}</button>
      </div>`).join('');
  },

  _commSet(i, field, val) { this._comms[i][field] = field === 'amount' ? (parseFloat(val)||0) : val; },
  _addCommRow() { this._comms.push({ employee_id: '', calc_type: 'percent', amount: 0 }); this._renderComms(); },
  _removeCommRow(i) { const c = this._comms[i]; if (c && c.id) this._commsRemoved.push(c.id); this._comms.splice(i, 1); this._renderComms(); },

  async _saveComms(serviceId) {
    if (!serviceId) return;
    for (const rid of this._commsRemoved) {
      try { await del(`/api/catalog/services/${serviceId}/commissions/${rid}`); } catch (e) {}
    }
    for (const c of this._comms) {
      if (c.id) continue;  // existing rows are immutable here; add-only semantics keep it simple
      try { await post(`/api/catalog/services/${serviceId}/commissions`, {
        employee_id: c.employee_id || null, calc_type: c.calc_type, amount: c.amount }); } catch (e) {}
    }
  },

  /* ── Prices by price type (Orderry "Price" table) — reuses /api/pricing ── */
  _priceTypes: [],   // [{id, name, value}]

  async _loadPrices(serviceId) {
    try { this._priceTypes = ((await get('/api/pricing/types')).items || []).map(t => ({ id: t.id, name: t.name, value: 0 })); }
    catch { this._priceTypes = []; }
    if (serviceId) {
      for (const pt of this._priceTypes) {
        try {
          const items = (await get(`/api/pricing/types/${pt.id}/items`)).items || [];
          const hit = items.find(i => i.item_type === 'service' && i.item_id === serviceId);
          if (hit) pt.value = hit.price;
        } catch (e) {}
      }
    }
    this._renderPrices();
  },

  _renderPrices() {
    const el = document.getElementById('sv-prices'); if (!el) return;
    if (!this._priceTypes.length) { el.innerHTML = `<div style="font-size:12px;color:var(--tx-4)">No price types yet — add them in Settings › Prices and discounts.</div>`; return; }
    el.innerHTML = this._priceTypes.map((p, i) => `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <span style="flex:1;font-size:13px">${this._esc(p.name)}</span>
        <input class="field-input" type="number" min="0" value="${p.value}" style="width:120px" oninput="ServicesPage._priceTypes[${i}].value=parseFloat(this.value)||0">
      </div>`).join('');
  },

  async _savePrices(serviceId) {
    if (!serviceId) return;
    for (const p of this._priceTypes) {
      try { await put(`/api/pricing/types/${p.id}/items`, { item_type: 'service', item_id: serviceId, price: p.value }); } catch (e) {}
    }
  },

  _recalcMargin() {
    const cost = parseFloat(document.getElementById('sv-cost').value) || 0;
    const price = parseFloat(document.getElementById('sv-price').value) || 0;
    const el = document.getElementById('sv-margin'); if (!el) return;
    el.textContent = price > 0 ? Math.round((price - cost) / price * 100) + '%' : '—';
  },

  async submit(id = null) {
    const name = document.getElementById('sv-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const warVal = parseInt(document.getElementById('sv-war').value) || 0;
    const warDays = this._warUnitV === 'mo' ? warVal * 30 : warVal;
    const durH = parseInt(document.getElementById('sv-dur-h').value) || 0;
    const durM = parseInt(document.getElementById('sv-dur-m').value) || 0;
    const body = {
      name, description: document.getElementById('sv-desc').value || '',
      category_id: document.getElementById('sv-cat-sel').value || null,
      is_labor: this._typeV === 'labor',
      unit: document.getElementById('sv-unit').value || 'pc',
      price: parseFloat(document.getElementById('sv-price').value) || 0,
      cost_price: parseFloat(document.getElementById('sv-cost').value) || 0,
      duration_min: durH * 60 + durM,
      warranty_days: warDays,
      barcode: document.getElementById('sv-barcode').value.trim() || null,
      bookable: document.getElementById('sv-bookable').checked,
      allowed_employee_ids: this._allowedEmps || [],
      required_resource_types: (document.getElementById('sv-res').value || '').split(',').map(s => s.trim()).filter(Boolean),
    };
    try {
      let sid = id;
      if (id) { await put(`/api/catalog/services/${id}`, body); Toast.success('Service updated'); }
      else { const created = await post('/api/catalog/services', body); sid = created.id; Toast.success('Service created'); }
      await this._saveComms(sid);   // 144ef: persist exceptional commissions (needs the id)
      await this._savePrices(sid);  // 144ek: persist price-type values (needs the id)
      Modal.close(); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async del(id) {
    const ok = await confirm2('Delete this service?');
    if (!ok) return;
    try { await del(`/api/catalog/services/${id}`); Toast.success('Service deleted'); Modal.close(); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  _esc(s) { return (s || '').replace(/"/g, '&quot;'); },

  /* ── Taxes management ── */
  async taxesModal() {
    let taxes = [];
    try { taxes = (await get('/api/catalog/taxes')).items; } catch {}
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Tax rates</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:12px;color:var(--tx-3);margin-bottom:14px">Named, reusable rates. <b>Imposed</b> tax is added on top of the price (customer sees it); <b>Included</b> tax is already inside the price (extracted for reporting only).</div>
        <div id="tax-list">${this._taxRows(taxes)}</div>
        <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line,rgba(0,0,0,.07))">
          <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Add a tax</div>
          <div style="display:grid;grid-template-columns:1fr 80px 110px auto;gap:8px;align-items:end">
            <label class="cat-f"><span>Name</span><input class="field-input" id="tax-name" placeholder="e.g. GST 18%"></label>
            <label class="cat-f"><span>Rate %</span><input class="field-input" id="tax-rate" type="number" min="0" value="18"></label>
            <label class="cat-f"><span>Mode</span><select class="field-select" id="tax-mode"><option value="imposed">Imposed</option><option value="included">Included</option></select></label>
            <button class="btn btn-primary" onclick="ServicesPage.addTax()">Add</button>
          </div>
        </div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Close</button></div>`, 'modal-wide');
  },

  _taxRows(taxes) {
    if (!taxes.length) return '<div style="color:var(--tx-4);font-size:13px;padding:8px">No taxes yet.</div>';
    return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Name</th><th>Rate</th><th>Mode</th><th>Default</th><th></th></tr></thead><tbody>
      ${taxes.map(t=>`<tr>
        <td style="font-weight:700">${t.name}</td><td>${t.rate}%</td>
        <td>${t.mode==='included'?'<span class="badge b-new">Included</span>':'<span class="badge b-warn">Imposed</span>'}</td>
        <td>${t.is_default?'<span class="gen-tag">default</span>':''}</td>
        <td><button class="cat-line-del" onclick="ServicesPage.delTax('${t.id}')" title="Remove">${icon('x',13)}</button></td>
      </tr>`).join('')}
    </tbody></table></div>`;
  },

  async addTax() {
    const name = document.getElementById('tax-name').value.trim();
    if (!name) return Toast.error('Name is required');
    try {
      await post('/api/catalog/taxes', {
        name, rate: parseFloat(document.getElementById('tax-rate').value) || 0,
        mode: document.getElementById('tax-mode').value, is_default: false,
      });
      Toast.success('Tax added');
      const taxes = (await get('/api/catalog/taxes')).items;
      document.getElementById('tax-list').innerHTML = this._taxRows(taxes);
      document.getElementById('tax-name').value = '';
    } catch (e) { Toast.error(e.message); }
  },

  async delTax(id) {
    try {
      await del(`/api/catalog/taxes/${id}`);
      const taxes = (await get('/api/catalog/taxes')).items;
      document.getElementById('tax-list').innerHTML = this._taxRows(taxes);
      Toast.success('Tax removed');
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('services', () => ServicesPage.load());
