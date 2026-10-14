/* Inventory > Conversions — the Product ↔ Asset conversion document log.

   Conversion documents are immutable: there is deliberately no delete action here.
   A conversion is undone by converting the item back the other way, which writes a
   second document and marks the first "reversed". */
const ConversionsPage = {
  _items: [], _filter: { direction: '', date_from: '', date_to: '' },

  async load() {
    setTopbarActions('');
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:760px">
        Every Product → Asset and Asset → Product conversion, newest first.
        Documents can't be deleted — to reverse one, convert the item back the other way.
      </div>
      <div class="card" style="padding:14px;margin-bottom:14px">
        <div class="field-row">
          <div class="field"><label class="field-label">Direction</label>
            <select class="field-select" id="cv-dir" onchange="ConversionsPage.apply()">
              <option value="">All</option>
              <option value="product_to_asset">Product → Asset</option>
              <option value="asset_to_product">Asset → Product</option>
            </select></div>
          <div class="field"><label class="field-label">From</label>
            <input class="field-input" type="date" id="cv-from" onchange="ConversionsPage.apply()"></div>
          <div class="field"><label class="field-label">To</label>
            <input class="field-input" type="date" id="cv-to" onchange="ConversionsPage.apply()"></div>
        </div>
      </div>
      <div id="cv-list"></div>`);
    await this.reload();
  },

  apply() {
    this._filter = {
      direction: document.getElementById('cv-dir').value,
      date_from: document.getElementById('cv-from').value,
      date_to: document.getElementById('cv-to').value,
    };
    this.reload();
  },

  async reload() {
    const el = document.getElementById('cv-list');
    if (!el) return;
    const qs = Object.entries(this._filter).filter(([, v]) => v)
      .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
    try {
      const r = await get(`/api/conversions${qs ? '?' + qs : ''}`);
      this._items = r.items || [];
    } catch (e) { return Toast.error(e.message); }

    const arrow = (d) => d === 'product_to_asset'
      ? '<span class="badge b-new">Product → Asset</span>'
      : '<span class="badge b-done">Asset → Product</span>';

    el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>When</th><th>Direction</th><th>Product</th><th>Asset</th>
        <th>Identifier</th><th>Owner</th><th>By</th><th>Status</th></tr></thead>
      <tbody>${this._items.map(c => `<tr>
        <td>${c.created_at ? new Date(c.created_at).toLocaleString() : ''}</td>
        <td>${arrow(c.direction)}</td>
        <td>${c.part_id
          ? `<a href="#" onclick="Router.go('inventory');return false" class="tbl-strong">${c.product_name || 'Product'}</a>`
          : '<span style="color:var(--tx-4)">—</span>'}</td>
        <td>${c.device_id
          ? `<a href="#" onclick="ConversionsPage.openAsset('${c.device_id}');return false">${c.asset_name || 'Asset'}</a>`
          : '<span style="color:var(--tx-4)">—</span>'}</td>
        <td style="font-family:ui-monospace,monospace;font-size:12px">${c.serial_identifier || '—'}</td>
        <td style="color:var(--tx-3);font-size:12px">${c.owner_before || '?'} → ${c.owner_after || '?'}</td>
        <td style="color:var(--tx-3)">${c.employee_name || '—'}</td>
        <td>${c.status === 'reversed'
          ? '<span class="badge" style="background:var(--g-border);color:var(--tx-3)">reversed</span>'
          : '<span class="badge b-done">completed</span>'}</td>
      </tr>`).join('') || `<tr><td colspan="8" style="text-align:center;color:var(--tx-4);padding:24px">
        No conversions yet. Convert a serialized stock unit into an asset, or an asset into a product.</td></tr>`}
      </tbody></table></div></div>`;
  },

  openAsset(id) { Router.go('clients'); Toast.info('Open the client to view this asset'); },

  /* ── Product → Asset ── */
  async productToAsset(unitId) {
    let p;
    try { p = await get(`/api/conversions/prefill/product/${unitId}`); }
    catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Convert product to asset</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">
          <b>${p.product_name}</b> · serial <code>${p.serial}</code>. This removes the unit from
          available stock and starts tracking it as an asset. The conversion is recorded permanently.
        </p>
        <div class="field-row">
          <div class="field"><label class="field-label">Owner</label>
            <select class="field-select" id="pa-owner" onchange="ConversionsPage._ownerToggle()">
              <option value="company">Company</option>
              <option value="client">Client</option>
              <option value="supplier">Supplier</option>
            </select></div>
          <div class="field" id="pa-client-wrap" style="display:none">
            <label class="field-label">Client</label>
            <input class="field-input" id="pa-client" placeholder="Client id"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Brand</label>
            <input class="field-input" id="pa-brand" value="${p.brand || ''}"></div>
          <div class="field"><label class="field-label">Model</label>
            <input class="field-input" id="pa-model" value="${p.model || ''}"></div>
        </div>
        <div class="field"><label class="field-label">Condition</label>
          <input class="field-input" id="pa-cond" value="${p.condition || ''}" placeholder="new / used"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="pa-go"
          onclick="ConversionsPage._submitPA('${unitId}')">Convert to asset</button>
      </div>`);
  },

  _ownerToggle() {
    const isClient = document.getElementById('pa-owner').value === 'client';
    document.getElementById('pa-client-wrap').style.display = isClient ? '' : 'none';
  },

  async _submitPA(unitId) {
    const owner = document.getElementById('pa-owner').value;
    const body = {
      serialized_item_id: unitId,
      owner_type: owner,
      client_id: owner === 'client' ? (document.getElementById('pa-client').value || null) : null,
      brand: document.getElementById('pa-brand').value || null,
      model: document.getElementById('pa-model').value || null,
      condition: document.getElementById('pa-cond').value || null,
    };
    const btn = document.getElementById('pa-go');
    btn.disabled = true; btn.textContent = 'Converting…';
    try {
      await post('/api/conversions/product-to-asset', body);
      Modal.close();
      Toast.success('Converted to asset');
      if (Router.current === 'conversions') await this.reload();
    } catch (e) {
      Toast.error(e.message || 'Conversion failed');
      btn.disabled = false; btn.textContent = 'Convert to asset';
    }
  },

  /* ── Asset → Product ── */
  async assetToProduct(deviceId) {
    let p;
    try { p = await get(`/api/conversions/prefill/asset/${deviceId}`); }
    catch (e) { return Toast.error(e.message); }
    const dupes = p.possible_duplicates || [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Convert asset to product</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        ${dupes.length ? `
          <div class="card" style="padding:12px;margin-bottom:14px;border-left:3px solid var(--blue)">
            <div style="font-weight:700;font-size:13px;margin-bottom:6px">Possible existing products</div>
            <div style="color:var(--tx-3);font-size:12px;margin-bottom:8px">Link one instead of creating a duplicate.</div>
            ${dupes.map(d => `<label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0">
              <input type="radio" name="ap-link" value="${d.id}"> ${d.name}${d.sku ? ` · ${d.sku}` : ''}
            </label>`).join('')}
            <label style="display:flex;gap:8px;align-items:center;font-size:13px;padding:3px 0">
              <input type="radio" name="ap-link" value="" checked> Create a new product
            </label>
          </div>` : ''}
        <div class="field"><label class="field-label">Product name</label>
          <input class="field-input" id="ap-name" value="${p.product_name || ''}"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">SKU <span style="color:var(--tx-4);font-weight:400">(optional)</span></label>
            <input class="field-input" id="ap-sku" placeholder="not the serial unless you say so"></div>
          <div class="field"><label class="field-label">Category</label>
            <input class="field-input" id="ap-cat"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Cost</label>
            <input class="field-input" type="number" id="ap-cost" value="${p.cost_price || 0}"></div>
          <div class="field"><label class="field-label">Sell price</label>
            <input class="field-input" type="number" id="ap-sell" value="${p.sell_price || 0}"></div>
        </div>
        ${p.serial_identifier ? `
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-top:6px">
          <input type="checkbox" id="ap-stock">
          Add to inventory as a serialized unit (<code>${p.serial_identifier}</code>)
        </label>` : ''}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="ap-go"
          onclick="ConversionsPage._submitAP('${deviceId}')">Convert to product</button>
      </div>`);
  },

  async _submitAP(deviceId) {
    const linked = document.querySelector('input[name="ap-link"]:checked');
    const stockEl = document.getElementById('ap-stock');
    const body = {
      device_id: deviceId,
      part_id: (linked && linked.value) || null,
      name: document.getElementById('ap-name').value || null,
      sku: document.getElementById('ap-sku').value || null,
      category: document.getElementById('ap-cat').value || null,
      cost_price: parseFloat(document.getElementById('ap-cost').value) || 0,
      sell_price: parseFloat(document.getElementById('ap-sell').value) || 0,
      create_stock_unit: !!(stockEl && stockEl.checked),
    };
    const btn = document.getElementById('ap-go');
    btn.disabled = true; btn.textContent = 'Converting…';
    try {
      await post('/api/conversions/asset-to-product', body);
      Modal.close();
      Toast.success('Converted to product');
      if (Router.current === 'conversions') await this.reload();
    } catch (e) {
      Toast.error(e.message || 'Conversion failed');
      btn.disabled = false; btn.textContent = 'Convert to product';
    }
  },
};

Router.register('conversions', () => ConversionsPage.load());
