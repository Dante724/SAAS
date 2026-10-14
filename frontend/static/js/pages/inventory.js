/* ════════════════════════════════════════════════════
   Inventory & Parts page
════════════════════════════════════════════════════ */

const InventoryPage = {

  _tab: 'stock',

  // Orderry's Inventory tab bar. External tabs reuse existing standalone pages.
  TABS: [
    { key: 'stock',        label: 'Stock' },
    { key: 'devices',      label: 'Devices' },
    { key: 'po',           label: 'Purchase orders' },
    { key: 'postings',     label: 'Postings' },
    { key: 'reservations', label: 'Reservations' },
    { key: 'conversions',  label: 'Conversions' },
    { key: 'transfers',    label: 'Transfers',  ext: () => TransfersPage.load() },
    { key: 'stocktakes',   label: 'Stock takes', ext: () => StocktakePage.load() },
    { key: 'writeoffs',    label: 'Write-offs' },
    { key: 'returns',      label: 'Returns',    ext: () => ReturnsPage.load() },
  ],

  async load() {
    this._restoreShim();
    this._tab = this._tab || 'stock';
    this._renderTabShell();
    await this._dispatch();
  },

  _renderTabShell() {
    setContent(`
      <div class="rep-tabs inv-tabs" style="margin-bottom:16px;flex-wrap:wrap">
        ${this.TABS.map(t => `
          <button class="rep-tab ${t.key === this._tab ? 'active' : ''}" onclick="InventoryPage.setTab('${t.key}')">${t.label}</button>`).join('')}
      </div>
      <div id="inv-body"></div>`);
  },

  async setTab(t) {
    this._restoreShim();
    this._tab = t;
    // update active state without re-rendering the whole shell when possible
    const shell = document.querySelector('.inv-tabs');
    if (shell) {
      shell.querySelectorAll('.rep-tab').forEach((el, i) =>
        el.classList.toggle('active', this.TABS[i].key === t));
    } else {
      this._renderTabShell();
    }
    await this._dispatch();
  },

  async _dispatch() {
    const tab = this.TABS.find(t => t.key === this._tab);
    if (tab && tab.ext) { this._mountExternal(tab.ext); return; }
    setTopbarActions('');
    if (this._tab === 'stock')        return this._loadStockTab();
    if (this._tab === 'devices')      return this._loadAssetsTab();
    if (this._tab === 'po')           return this._loadPoTab();
    if (this._tab === 'postings')     return this._loadMovementsTab('posting');
    if (this._tab === 'writeoffs')    return this._loadMovementsTab('writeoff');
    if (this._tab === 'reservations') return this._loadReservationsTab();
    if (this._tab === 'conversions')  return this._loadConversionsTab();
  },

  // Host an existing standalone page inside #inv-body by redirecting its
  // setContent into the pane (same proven shim SettingsPage uses).
  _mountExternal(renderFn) {
    if (!InventoryPage._shimActive) {
      InventoryPage._realSetContent = window.setContent;
      window.setContent = (html) => {
        const p = document.getElementById('inv-body');
        if (p) p.innerHTML = html; else InventoryPage._realSetContent(html);
      };
      InventoryPage._shimActive = true;
    }
    renderFn();
  },

  _restoreShim() {
    if (InventoryPage._shimActive) {
      window.setContent = InventoryPage._realSetContent;
      InventoryPage._shimActive = false;
    }
  },

  _loadStockTab() {
    setTopbarActions('');
    document.getElementById('inv-body').innerHTML = `
      <div class="inv-toolbar">
        <div class="inv-filters">
          <button class="btn btn-primary btn-sm" onclick="InventoryPage.newModal()">+ Product</button>
          <select class="field-select inv-flt" id="pt-loc" onchange="InventoryPage.reload()"><option value="">Warehouse: All</option></select>
          <select class="field-select inv-flt" id="pt-avail" onchange="InventoryPage.reload()">
            <option value="">Availability: All</option>
            <option value="in">In stock</option>
            <option value="low">Low stock</option>
            <option value="out">Out of stock</option>
          </select>
          <input class="field-input inv-flt" id="pt-cat" placeholder="Category: All" oninput="InventoryPage.reload()">
          <div class="search-box" style="flex:1;min-width:160px">
            <span class="search-icon">${icon('search',14)}</span>
            <input class="field-input search-input" id="pt-search" placeholder="Search products…" oninput="InventoryPage.reload()">
          </div>
        </div>
        <div class="inv-actions">
          <div class="inv-menu-wrap">
            <button class="btn btn-glass btn-sm" onclick="InventoryPage.toggleActionsMenu(event)">Actions</button>
            <div class="inv-menu" id="inv-actions-menu" hidden>
              <button onclick="InventoryPage.createPoFromSelection();InventoryPage.closeActionsMenu()">${icon('box',14)} Create PO from selected</button>
              <button onclick="InventoryPage.reorderModal();InventoryPage.closeActionsMenu()">${icon('refresh',14)} Reorder list</button>
              <button onclick="InventoryPage.newPostingFromStock();InventoryPage.closeActionsMenu()">${icon('box',14)} New posting</button>
              <button onclick="InventoryPage.newWriteoffFromStock();InventoryPage.closeActionsMenu()">${icon('alert',14)} New write-off</button>
              <button onclick="InventoryPage.bulkModal();InventoryPage.closeActionsMenu()">${icon('list',14)} Bulk edit filtered</button>
              <button onclick="InventoryPage.expiringModal();InventoryPage.closeActionsMenu()">${icon('alert',14)} Expiring stock to clear</button>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th style="width:26px"><input type="checkbox" id="pt-all" onclick="InventoryPage.toggleAllParts(this)" title="Select all"></th><th>Product</th><th>SKU</th><th>Category</th><th>Unit</th><th>Cost</th><th>Sell price</th><th>Margin</th><th>In stock</th><th>Reserved</th><th>Status</th><th></th></tr></thead>
          <tbody id="pt-tbody">${skeletonRows(10)}</tbody>
        </table></div>
      </div>`;
    this._lowOnly = false;
    this._loadStockFilters();
    this.reload();
  },

  async _loadStockFilters() {
    try {
      const locs = (await get('/api/staff/locations')).items || [];
      const sel = document.getElementById('pt-loc');
      if (sel) sel.innerHTML = `<option value="">Warehouse: All</option>` +
        locs.map(l => `<option value="${l.id}">${l.name}</option>`).join('');
    } catch { /* filter is best-effort */ }
  },

  toggleActionsMenu(e) {
    e.stopPropagation();
    const m = document.getElementById('inv-actions-menu');
    if (m) m.hidden = !m.hidden;
    document.addEventListener('click', () => this.closeActionsMenu(), { once: true });
  },
  closeActionsMenu() { const m = document.getElementById('inv-actions-menu'); if (m) m.hidden = true; },

  async newPostingFromStock() { this._tab = 'postings'; await this.setTab('postings'); this.newMovementModal('posting'); },
  async newWriteoffFromStock() { this._tab = 'writeoffs'; await this.setTab('writeoffs'); this.newMovementModal('writeoff'); },

  async _loadAssetsTab() {
    setTopbarActions(`<button class="btn btn-primary" onclick="InventoryPage.newAssetModal()">+ Add asset</button>`);
    const body = document.getElementById('inv-body');
    body.innerHTML = `
      <div style="display:flex;gap:10px;margin-bottom:18px;flex-wrap:wrap;align-items:center">
        <div class="search-box" style="flex:1;min-width:180px">
          <span class="search-icon">${icon('search',14)}</span>
          <input class="field-input search-input" id="as-search" placeholder="Search assets…" oninput="InventoryPage._loadAssetsTab()">
        </div>
        <div class="o-doctype-seg">
          <button class="ods-btn ${this._assetOwner!=='company'&&this._assetOwner!=='client'?'active':''}" onclick="InventoryPage.setAssetOwner('')">All</button>
          <button class="ods-btn ${this._assetOwner==='company'?'active':''}" onclick="InventoryPage.setAssetOwner('company')">Company-owned</button>
          <button class="ods-btn ${this._assetOwner==='client'?'active':''}" onclick="InventoryPage.setAssetOwner('client')">Client-owned</button>
        </div>
      </div>
      <div class="card"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Asset</th><th>Type</th><th>Serial / IMEI</th><th>Owner</th><th>Status</th><th>Orders</th><th></th></tr></thead>
        <tbody id="as-tbody">${skeletonRows(8)}</tbody>
      </table></div></div>`;
    const search = document.getElementById('as-search')?.value || '';
    let data;
    try {
      const q = new URLSearchParams();
      if (this._assetOwner) q.set('owner_type', this._assetOwner);
      if (search) q.set('search', search);
      data = await get(`/api/assets?${q.toString()}`);
    } catch (e) { return Toast.error(e.message); }
    const tb = document.getElementById('as-tbody');
    const statusLabel = { in_use:'In use', in_stock:'In stock', for_sale:'For sale', sold:'Sold', written_off:'Written off' };
    tb.innerHTML = data.items.length ? data.items.map(a => `
      <tr onclick="InventoryPage.viewAsset('${a.id}')" style="cursor:pointer">
        <td><span class="tbl-strong">${a.brand||''} ${a.model||''}</span></td>
        <td style="color:var(--tx-3)">${a.asset_type||'—'}</td>
        <td style="color:var(--tx-3);font-size:12px">${a.serial||a.imei||'—'}</td>
        <td>${a.owner_type==='company'?'<span class="badge b-ready badge-solid">Company</span>':`<span style="font-weight:600">${a.owner_name||'Client'}</span>`}</td>
        <td><span class="badge badge-est" style="--est:${a.asset_status==='for_sale'?'#f5a623':a.asset_status==='sold'?'#5b647e':'#3dca7a'}">${statusLabel[a.asset_status]||a.asset_status}</span></td>
        <td>${a.order_count||0}</td>
        <td>${a.owner_type==='company'&&!a.converted_to_part_id ? `<button class="btn btn-glass btn-sm" onclick="event.stopPropagation();InventoryPage.convertAssetModal('${a.id}','${(a.brand+' '+a.model).replace(/'/g,"")}')">→ Product</button>` : a.converted_to_part_id?'<span style="font-size:11px;color:var(--tx-4)">converted</span>':''}</td>
      </tr>`).join('') : emptyRow(7,'device','No assets yet','Company equipment or client devices');
  },

  _assetOwner: '',
  setAssetOwner(o) { this._assetOwner = o; this._loadAssetsTab(); },

  _lowOnly: false,
  toggleLowStock() {
    this._lowOnly = !this._lowOnly;
    document.getElementById('low-toggle-icon').innerHTML = icon(this._lowOnly ? 'box' : 'alert', 15);
    this.reload();
  },

  async reload() {
    const s   = document.getElementById('pt-search')?.value || '';
    const cat = document.getElementById('pt-cat')?.value || '';
    const avail = document.getElementById('pt-avail')?.value || '';
    const tbody = document.getElementById('pt-tbody');
    if (!tbody) return;
    try {
      const _lp = (typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : '';
      const qs = `/api/inventory/parts?limit=300${s?'&search='+encodeURIComponent(s):''}${cat?'&category='+encodeURIComponent(cat):''}${this._lowOnly?'&low_stock=true':''}${_lp}`;
      const data = await get(qs);
      let items = data.items;
      if (avail === 'in')  items = items.filter(p => p.qty_available > 0);
      if (avail === 'low') items = items.filter(p => p.low_stock);
      if (avail === 'out') items = items.filter(p => p.qty_available <= 0);
      this._visible = items;   // Build 96: bulk actions operate on the filtered list
      if (!items.length) { tbody.innerHTML = emptyRow(12,'box','No products found','Add products to your catalog'); return; }
      tbody.innerHTML = items.map(p => {
        const mColor = p.margin_pct > 40 ? 'var(--green)' : p.margin_pct > 20 ? 'var(--amber)' : 'var(--red)';
        return `<tr onclick="InventoryPage.viewPart('${p.id}')">
          <td onclick="event.stopPropagation()"><input type="checkbox" class="pt-check" value="${p.id}"></td>
          <td><div style="display:flex;align-items:center;gap:10px"><span onclick="event.stopPropagation()">${thumbImg(p.image_data)}</span><span><div style="font-weight:800">${p.name}</div>${p.brand?`<div style="font-size:11px;color:var(--tx-3);margin-top:1px">${p.brand}</div>`:''}</span></div></td>
          <td><kbd>${p.sku||'—'}</kbd></td>
          <td style="color:var(--tx-2)">${p.category||'—'}</td>
          <td style="color:var(--tx-3)">${p.uom||'pcs'}</td>
          <td style="color:var(--tx-2)">${fmt.currency(p.cost_price)}</td>
          <td><span class="tbl-strong">${fmt.currency(p.sell_price)}</span></td>
          <td><span style="font-weight:900;color:${mColor}">${p.margin_pct}%</span></td>
          <td><span class="tbl-strong tbl-num">${p.qty_available}</span></td>
          <td style="color:var(--tx-3)">${p.qty_reserved||0}</td>
          <td>${p.low_stock
            ? `<span class="badge b-overdue">Low stock</span>`
            : `<span class="badge b-done">OK</span>`}</td>
          <td><button class="btn btn-glass btn-sm" onclick="event.stopPropagation();InventoryPage.adjustModal('${p.id}','${p.name.replace(/'/g,"\\'")}')">Adjust</button></td>
        </tr>`;
      }).join('');
    } catch (e) { Toast.error(e.message); }
  },

  async viewPart(id) {
    let p;
    try { p = await get(`/api/inventory/parts/${id}`); }
    catch (e) { Toast.error(e.message); return; }
    this._activePart = id;
    const mColor = p.margin_pct > 40 ? 'var(--green)' : p.margin_pct > 20 ? 'var(--amber)' : 'var(--red)';
    Modal.open(`
      <div class="modal-header">
        <span class="modal-title">${p.name}</span>
        <button class="modal-close" onclick="Modal.close()">×</button>
      </div>
      <div class="wo-tabs" style="padding:0 22px">
        <button class="wo-tab active" onclick="InventoryPage.partTab(event,'general')">General</button>
        <button class="wo-tab" onclick="InventoryPage.partTab(event,'stock')">Stock</button>
        <button class="wo-tab" onclick="InventoryPage.partTab(event,'history')">History</button>
        <button class="wo-tab" onclick="InventoryPage.partTab(event,'pos')">Purchase orders</button>
      </div>
      <div class="modal-body">
        <div class="ip-panel active" data-ippanel="general">
          <div class="detail-grid">
            <div class="detail-blob"><div class="d-label">SKU</div><div class="d-value"><kbd>${p.sku||'—'}</kbd></div></div>
            <div class="detail-blob"><div class="d-label">Brand / Category</div><div class="d-value">${[p.brand,p.category].filter(Boolean).join(' · ')||'—'}</div></div>
            <div class="detail-blob"><div class="d-label">Cost price</div><div class="d-value" style="font-size:16px;font-weight:900">${fmt.currency(p.cost_price)}</div></div>
            <div class="detail-blob"><div class="d-label">Sell price</div><div class="d-value" style="font-size:16px;font-weight:900;color:var(--blue-dark)">${fmt.currency(p.sell_price)}</div></div>
            <div class="detail-blob"><div class="d-label">Margin</div><div class="d-value" style="font-size:18px;font-weight:900;color:${mColor}">${p.margin_pct}%</div></div>
            <div class="detail-blob"><div class="d-label">In stock</div><div class="d-value" style="font-size:18px;font-weight:900;color:${p.low_stock?'var(--red)':'var(--green)'}">${p.qty_available} <span style="font-size:12px;color:var(--tx-3)">/ ${p.min_stock} min</span></div></div>
          </div>
          <div style="display:flex;gap:8px;margin-top:16px">
            <button class="btn btn-success btn-sm" onclick="InventoryPage.adjustModal('${id}','${p.name.replace(/'/g,"\\'")}')">+ Adjust stock</button>
            <button class="btn btn-glass btn-sm" onclick="LabelPrint.open(['${id}'])">Print labels</button>
          </div>
        </div>
        <div class="ip-panel" data-ippanel="stock" style="display:none"><div id="ip-stock"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
        <div class="ip-panel" data-ippanel="history" style="display:none"><div id="ip-history"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
        <div class="ip-panel" data-ippanel="pos" style="display:none"><div id="ip-pos"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
      </div>`, 'modal-wide');
  },

  partTab(e, panel) {
    document.querySelectorAll('.wo-tab').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    document.querySelectorAll('.ip-panel').forEach(p => p.style.display = p.dataset.ippanel === panel ? 'block' : 'none');
    if (panel === 'stock') this._loadIpStock();
    else if (panel === 'history') this._loadIpHistory();
    else if (panel === 'pos') this._loadIpPos();
  },

  async _loadIpStock() {
    const box = document.getElementById('ip-stock'); if (!box || !this._activePart) return;
    try {
      const d = await get(`/api/inventory/parts/${this._activePart}/stock`);
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

  async _loadIpHistory() {
    const box = document.getElementById('ip-history'); if (!box || !this._activePart) return;
    try {
      const rows = await get(`/api/inventory/parts/${this._activePart}/movements`);
      const typeLabel = { posting:'Posting', receive:'Receipt', sale:'Sale', transfer:'Transfer',
                          writeoff:'Write-off', adjustment:'Adjustment', reservation:'Reservation' };
      box.innerHTML = rows.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Type</th><th>Reference</th><th>Location</th><th style="text-align:right">Qty</th><th>Date</th></tr></thead>
        <tbody>${rows.map(m=>`<tr>
          <td class="tbl-strong">${typeLabel[m.type]||m.type}</td>
          <td style="color:var(--tx-3)">${m.ref_type||'—'}</td>
          <td style="color:var(--tx-3)">${m.location||'—'}</td>
          <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${m.qty>=0?'var(--green)':'var(--red)'}">${m.qty>=0?'+':''}${m.qty}</td>
          <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(m.at)}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No stock movements yet. Post, sell or transfer this product to build its history.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async _loadIpPos() {
    const box = document.getElementById('ip-pos'); if (!box || !this._activePart) return;
    try {
      const d = await get(`/api/inventory/parts/${this._activePart}/purchase-orders`);
      box.innerHTML = d.items.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>PO #</th><th>Status</th><th style="text-align:right">Ordered</th><th style="text-align:right">Received</th><th>Expected</th></tr></thead>
        <tbody>${d.items.map(po=>`<tr style="cursor:pointer" onclick="InventoryPage.openPo('${po.id}')" title="Open purchase order">
          <td class="tbl-strong">${po.po_number}</td><td>${badge(po.status)}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">${po.qty_ordered}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums">${po.qty_received}</td>
          <td style="color:var(--tx-3);font-size:12px">${po.expected_at?fmt.date(po.expected_at):'—'}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No open purchase orders for this product.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async adjustModal(partId, partName) {
    const me = await get('/api/auth/me').catch(()=>null);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Adjust stock — ${partName}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Movement type</label>
            <select class="field-select" id="adj-type">
              <option value="receive">Receive (add)</option>
              <option value="adjust">Manual adjust</option>
              <option value="writeoff">Write-off (remove)</option>
            </select>
          </div>
          <div class="field"><label class="field-label">Quantity</label><input class="field-input" id="adj-qty" type="number" value="1" min="0.1" step="0.1"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitAdjust('${partId}','${me?.location_id||''}')">Apply</button>
      </div>`, 'modal-narrow');
  },

  async submitAdjust(partId, locationId) {
    if (!locationId) return Toast.error('No location found for your account');
    try {
      await post(`/api/inventory/parts/${partId}/adjust`, {
        location_id: locationId,
        qty: parseFloat(document.getElementById('adj-qty').value) || 0,
        movement_type: document.getElementById('adj-type').value,
      });
      Toast.success('Stock adjusted'); Modal.close(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  newModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add product</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="np-name" placeholder="e.g. iPhone 14 Pro Screen"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">SKU</label><input class="field-input" id="np-sku" placeholder="SCRN-IP14P"></div>
          <div class="field"><label class="field-label">Unit</label><select class="field-select" id="np-uom">
            <option value="pcs">pcs</option><option value="m">m</option><option value="kg">kg</option>
            <option value="l">l</option><option value="hr">hr</option><option value="set">set</option></select></div>
          <div class="field"><label class="field-label">Barcode</label><input class="field-input" id="np-bar" placeholder="8901234567890"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Brand</label><input class="field-input" id="np-brand" placeholder="Apple"></div>
          <div class="field"><label class="field-label">Category</label><input class="field-input" id="np-cat" placeholder="Screens"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Cost price ₹</label><input class="field-input" id="np-cost" type="number" value="0" min="0"></div>
          <div class="field"><label class="field-label">Sell price ₹</label><input class="field-input" id="np-sell" type="number" value="0" min="0"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Min stock threshold</label><input class="field-input" id="np-min" type="number" value="5" min="0"></div>
          <div class="field"><label class="field-label">Min order qty <span style="color:var(--tx-4);font-weight:600">— default when reordering</span></label><input class="field-input" id="np-moq" type="number" value="0" min="0"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitNew()">Add product</button>
      </div>`);
  },

  async submitNew() {
    const name = document.getElementById('np-name').value.trim();
    if (!name) return Toast.error('Name is required');
    try {
      await post('/api/inventory/parts', {
        name, sku: document.getElementById('np-sku').value||null,
      uom: document.getElementById('np-uom') ? document.getElementById('np-uom').value : 'pcs',
        barcode: document.getElementById('np-bar').value||null,
        brand: document.getElementById('np-brand').value||null,
        category: document.getElementById('np-cat').value||null,
        cost_price: parseFloat(document.getElementById('np-cost').value)||0,
        sell_price: parseFloat(document.getElementById('np-sell').value)||0,
        min_stock: parseInt(document.getElementById('np-min').value)||0,
        min_order_qty: parseFloat(document.getElementById('np-moq').value)||0,
      });
      Toast.success('Part added'); Modal.close(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  // ── Build 144aa: select products → create a purchase order ──
  toggleAllParts(box) {
    document.querySelectorAll('.pt-check').forEach(c => { c.checked = box.checked; });
  },
  _selectedParts() {
    const ids = Array.from(document.querySelectorAll('.pt-check:checked')).map(c => c.value);
    return (this._visible || []).filter(p => ids.includes(p.id));
  },
  async createPoFromSelection() {
    const chosen = this._selectedParts();
    if (!chosen.length) return Toast.error('Tick one or more products first');
    let suppliers = [], locations = [];
    try { suppliers = (await get('/api/suppliers')).items || []; } catch {}
    try { locations = (await get('/api/staff/locations')).items || []; } catch {}
    // default order qty: the product's min order qty, else the gap below min stock, else 1
    const dq = (p) => (p.min_order_qty > 0 ? p.min_order_qty
      : Math.max((p.min_stock || 0) - (p.qty_available || 0), 0)) || 1;
    const rows = chosen.map(p => `
      <tr>
        <td style="font-weight:700">${p.name}<div style="font-size:11px;color:var(--tx-3)">${p.sku || ''}</div></td>
        <td><input class="field-input po-q" data-pid="${p.id}" type="number" min="0" step="any" value="${dq(p)}" style="width:90px"></td>
        <td><input class="field-input po-c" data-pid="${p.id}" type="number" min="0" step="any" value="${p.cost_price || 0}" style="width:110px"></td>
      </tr>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Create purchase order — ${chosen.length} product(s)</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Supplier *</label>
            <select class="field-select" id="po-sup"><option value="">— Select supplier —</option>
              ${suppliers.map(s => `<option value="${s.id}">${(s.name || '').replace(/</g,'&lt;')}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Deliver to warehouse *</label>
            <select class="field-select" id="po-loc">
              ${locations.map(l => `<option value="${l.id}">${l.name}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label class="field-label">Expected delivery</label>
          <input class="field-input" id="po-exp" type="date"></div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Product</th><th>Order qty</th><th>Unit cost</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <label class="cash-check" style="margin-top:10px"><input type="checkbox" id="po-credit"> Buy on credit (adds to supplier's balance)</label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitPoFromSelection()">Create purchase order</button>
      </div>`, 'modal-wide');
  },
  async submitPoFromSelection() {
    const supplier_id = document.getElementById('po-sup').value;
    const location_id = document.getElementById('po-loc').value;
    if (!supplier_id) return Toast.error('Choose a supplier');
    if (!location_id) return Toast.error('Choose a warehouse');
    const costs = {};
    document.querySelectorAll('.po-c').forEach(c => { costs[c.dataset.pid] = parseFloat(c.value) || 0; });
    const lines = Array.from(document.querySelectorAll('.po-q'))
      .map(q => ({ part_id: q.dataset.pid, qty_ordered: parseFloat(q.value) || 0, unit_cost: costs[q.dataset.pid] || 0 }))
      .filter(l => l.qty_ordered > 0);
    if (!lines.length) return Toast.error('Set an order quantity on at least one product');
    const exp = document.getElementById('po-exp').value;
    try {
      const r = await post('/api/inventory/po', {
        supplier_id, location_id, lines,
        on_credit: document.getElementById('po-credit').checked,
        expected_at: exp ? new Date(exp).toISOString() : null,
      });
      Modal.close();
      Toast.success(`Purchase order ${r.po_number} created`);
      this.reload();
    } catch (e) { Toast.error(e.message || 'Could not create PO'); }
  },

  /* ── Purchase orders (Phase B.6) ── */
  async poListModal() {
    let pos = [];
    try { pos = (await get(`/api/inventory/po${(typeof App !== 'undefined' && App.locParam) ? App.locParam('?') : ''}`)).items; } catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Purchase orders</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
          <button class="btn btn-primary btn-sm" onclick="InventoryPage.newPoModal()">+ New purchase order</button>
          <button class="btn btn-glass btn-sm" onclick="InventoryPage.groupSelected()">${icon('layers',13)} Group selected</button>
        </div>
        ${pos.length ? `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th style="width:26px"></th><th>PO #</th><th>Status</th><th>Lines</th><th>Total</th><th>Expected</th><th style="text-align:right">Actions</th></tr></thead>
          <tbody>${pos.map(po => `<tr>
            <td>${(po.status==='ordered') ? `<input type="checkbox" class="po-check" value="${po.id}">` : ''}</td>
            <td><span class="tbl-num tbl-strong">${po.po_number}</span></td>
            <td><span class="badge b-${po.status === 'received' ? 'done' : po.status === 'grouped' ? 'muted' : 'new'}">${po.status}</span></td>
            <td>${po.lines.length}</td>
            <td>${fmt.currency(po.total)}</td>
            <td style="color:var(--tx-3);font-size:12px">${po.expected_at ? fmt.datetime(po.expected_at) : '—'}</td>
            <td style="text-align:right;white-space:nowrap">
              <button class="btn btn-glass btn-sm" onclick="openPrintPdf('/api/print/purchase-order/${po.id}','PO.pdf')" title="Print / PDF">${icon('file',13)}</button>
              <button class="btn btn-glass btn-sm" onclick="InventoryPage.sendPoModal('${po.id}','${po.po_number}')" title="Send to supplier">${icon('send',13)||'Send'}</button>
              ${po.status !== 'received' && po.status !== 'grouped' ? `<button class="btn btn-success btn-sm" onclick="InventoryPage.receivePo('${po.id}')">Receive</button>` : ''}
            </td>
          </tr>`).join('')}</tbody></table></div>`
          : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No purchase orders yet.</div>'}
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Close</button></div>`, 'modal-wide');
  },

  _selectedPos() { return Array.from(document.querySelectorAll('.po-check:checked')).map(c => c.value); },

  async groupSelected() {
    const ids = this._selectedPos();
    if (ids.length < 2) return Toast.error('Select at least two open POs (same supplier) to group');
    try {
      const r = await post('/api/inventory/po/group', { po_ids: ids });
      Toast.success(`Grouped ${r.grouped} POs into ${r.po_number}`);
      this.poListModal();
    } catch (e) { Toast.error(e.message || 'Could not group'); }
  },

  sendPoModal(poId, poNum) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Send ${poNum} to supplier</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Channel</label>
          <select class="field-select" id="po-send-ch">
            <option value="email">Email</option><option value="sms">SMS</option><option value="whatsapp">WhatsApp</option>
          </select></div>
        <p style="color:var(--tx-4);font-size:12px;margin:4px 0 0">The purchase order is logged to the supplier's conversation. Actual delivery uses your connected provider for that channel.</p>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="InventoryPage.poListModal()">Back</button>
        <button class="btn btn-primary" onclick="InventoryPage.sendPo('${poId}')">Send</button>
      </div>`, 'modal-narrow');
  },
  async sendPo(poId) {
    const channel = document.getElementById('po-send-ch').value;
    try {
      const r = await post(`/api/inventory/po/${poId}/send`, { channel });
      Toast.success(`${r.po_number} sent via ${channel}`);
      Modal.close();
    } catch (e) { Toast.error(e.message || 'Could not send'); }
  },

  async receivePo(poId) {
    try {
      const r = await post(`/api/inventory/po/${poId}/receive`, { lines: [] });
      Toast.success(`PO received (${r.status})`);
      this.poListModal();
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async newPoModal() {
    let parts = [], suppliers = [], me = {};
    try { parts = (await get('/api/inventory/parts')).items; } catch {}
    try { me = await get('/api/auth/me'); } catch {}
    // suppliers via a lightweight endpoint fallback: reuse parts' org; ask backend
    try { suppliers = (await get('/api/inventory/suppliers')).items; } catch { suppliers = []; }
    this._poLines = [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New purchase order</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Supplier</label>
          <select class="field-select" id="po-supplier" onchange="InventoryPage.onPoSupplierChange(this.value)">
            ${suppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('')}
            <option value="__new">➕ Add new supplier…</option>
          </select>
          <div id="po-newsup" style="display:none;margin-top:8px;padding:12px;border:1px solid var(--g-border);border-radius:var(--r-md);background:rgba(79,142,247,.04)">
            <div class="field" style="margin-bottom:8px"><label class="field-label">Supplier name</label><input class="field-input" id="po-sup-name" placeholder="e.g. TechParts Wholesale"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <input class="field-input" id="po-sup-phone" placeholder="Phone">
              <input class="field-input" id="po-sup-email" placeholder="Email">
            </div>
            <button class="btn btn-primary btn-sm" style="margin-top:8px" onclick="InventoryPage.createPoSupplier()">Save supplier</button>
          </div>
        </div>
        <div class="field"><label class="field-label">Expected delivery</label><input class="field-input" type="date" id="po-expected"></div>
        <div class="field"><label class="field-label">Add part</label>
          <div style="display:flex;gap:8px">
            <select class="field-select" id="po-part" style="flex:1">${parts.map(p => `<option value="${p.id}" data-cost="${p.cost_price}">${p.name}</option>`).join('')}</select>
            <input class="field-input" id="po-qty" type="number" min="1" value="1" style="width:80px" placeholder="Qty">
            <button class="btn btn-glass btn-sm" onclick="InventoryPage.addPoLine()">Add</button>
          </div>
        </div>
        <div id="po-lines"></div>
        <label class="cash-check" style="margin-top:12px"><input type="checkbox" id="po-credit"> Buy on credit (adds to supplier's outstanding balance)</label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitPo('${me.location_id || ''}')">Create PO</button>
      </div>`, 'modal-wide');
    this._poParts = parts;
  },

  onPoSupplierChange(val) {
    const box = document.getElementById('po-newsup');
    if (box) box.style.display = val === '__new' ? 'block' : 'none';
  },

  async createPoSupplier() {
    const name = document.getElementById('po-sup-name').value.trim();
    if (!name) return Toast.error('Enter a supplier name');
    const phone = document.getElementById('po-sup-phone').value.trim();
    const email = document.getElementById('po-sup-email').value.trim();
    try {
      const s = await post('/api/suppliers', { name, phone: phone || null, email: email || null });
      Toast.success('Supplier added');
      // insert into the dropdown and select it, hide the form
      const sel = document.getElementById('po-supplier');
      const opt = document.createElement('option');
      opt.value = s.id; opt.textContent = s.name;
      sel.insertBefore(opt, sel.querySelector('option[value="__new"]'));
      sel.value = s.id;
      document.getElementById('po-newsup').style.display = 'none';
    } catch (e) { Toast.error(e.message || 'Could not add supplier'); }
  },

  _poLines: [],
  _poParts: [],
  addPoLine() {
    const sel = document.getElementById('po-part');
    const pid = sel.value;
    const part = this._poParts.find(p => p.id === pid);
    const qty = parseFloat(document.getElementById('po-qty').value) || 1;
    if (!part) return;
    this._poLines.push({ part_id: pid, name: part.name, qty_ordered: qty, unit_cost: part.cost_price || 0 });
    this._renderPoLines();
  },
  _renderPoLines() {
    const el = document.getElementById('po-lines');
    if (!el) return;
    el.innerHTML = this._poLines.length ? `<div class="wo-inv-block" style="margin-top:10px">
      ${this._poLines.map((l, i) => `<div class="wo-pay-row"><span>${l.name} × ${l.qty_ordered}</span><span>${fmt.currency(l.qty_ordered * l.unit_cost)} <button class="cat-line-del" onclick="InventoryPage.rmPoLine(${i})">${icon('x', 12)}</button></span></div>`).join('')}
    </div>` : '';
  },
  rmPoLine(i) { this._poLines.splice(i, 1); this._renderPoLines(); },

  async submitPo(locationId) {
    if (!this._poLines.length) return Toast.error('Add at least one part');
    const supplierSel = document.getElementById('po-supplier');
    if (!supplierSel || !supplierSel.value) return Toast.error('Select a supplier');
    try {
      await post('/api/inventory/po', {
        supplier_id: supplierSel.value,
        location_id: locationId,
        expected_at: document.getElementById('po-expected').value || null,
        on_credit: document.getElementById('po-credit')?.checked || false,
        lines: this._poLines.map(l => ({ part_id: l.part_id, qty_ordered: l.qty_ordered, unit_cost: l.unit_cost })),
      });
      Toast.success('Purchase order created');
      this.poListModal();
    } catch (e) { Toast.error(e.message); }
  },

  async reorderModal() {
    let items = [];
    try { items = (await get('/api/inventory/reorder-list')).items; } catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Reorder suggestions</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        ${items.length ? `<div style="font-size:12px;color:var(--tx-3);margin-bottom:12px">Parts at or below their minimum stock level.</div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Part</th><th>SKU</th><th>Available</th><th>Min</th><th>Suggested</th></tr></thead>
          <tbody>${items.map(p => `<tr>
            <td style="font-weight:700">${p.name}</td><td style="color:var(--tx-3)">${p.sku || '—'}</td>
            <td>${p.available}</td><td>${p.min_stock}</td>
            <td><span class="tbl-strong" style="color:var(--blue-dark)">${p.suggested_qty}</span></td>
          </tr>`).join('')}</tbody></table></div>`
          : '<div style="color:var(--green);font-size:13px;padding:14px;text-align:center">Everything is above its minimum stock level.</div>'}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
        ${items.length ? `<button class="btn btn-primary" onclick="InventoryPage.newPoModal()">Create purchase order</button>` : ''}
      </div>`, 'modal-wide');
  },
  async newAssetModal() {
    let clients = [], locations = [];
    try { clients = (await get('/api/clients?limit=200')).items; } catch {}
    try { locations = (await get('/api/staff/locations')).items || []; } catch {}
    this._assetClients = clients;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New asset</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Owner</label>
          <div class="type-chips">
            <div class="type-chip active" id="ao-company" onclick="InventoryPage._assetOwnerPick('company',this)">Company-owned</div>
            <div class="type-chip" id="ao-client" onclick="InventoryPage._assetOwnerPick('client',this)">Client-owned</div>
          </div>
        </div>
        <div class="field" id="ao-client-row" style="display:none"><label class="field-label">Client</label>
          <select class="field-select" id="a-client"><option value="">—</option>${clients.map(c=>`<option value="${c.id}">${c.name}</option>`).join('')}</select>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Type</label>
            <select class="field-select" id="a-type">${['Device','Phone','Laptop','Tablet','Vehicle','Appliance','Tool','Other'].map(t=>`<option>${t}</option>`).join('')}</select>
          </div>
          <div class="field"><label class="field-label">Warehouse / location</label>
            <select class="field-select" id="a-loc"><option value="">—</option>${locations.map(l=>`<option value="${l.id}">${l.name}</option>`).join('')}</select>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Brand</label><input class="field-input" id="a-brand"></div>
          <div class="field"><label class="field-label">Model</label><input class="field-input" id="a-model"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Serial</label><input class="field-input" id="a-serial" placeholder="blank = auto-generate"></div>
          <div class="field"><label class="field-label">IMEI</label><input class="field-input" id="a-imei"></div>
        </div>
        <div class="field-row" id="ao-price-row">
          <div class="field"><label class="field-label">Purchase price</label><input class="field-input" id="a-pp" type="number" min="0" value="0"></div>
          <div class="field"><label class="field-label">Sale price</label><input class="field-input" id="a-sp" type="number" min="0" value="0"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitAsset()">Add asset</button>
      </div>`, 'modal-wide');
    this._aOwner = 'company';
  },
  _aOwner: 'company',
  _assetOwnerPick(o, el) {
    this._aOwner = o;
    el.parentElement.querySelectorAll('.type-chip').forEach(c=>c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('ao-client-row').style.display = o==='client'?'':'none';
    document.getElementById('ao-price-row').style.display = o==='company'?'':'none';
  },
  async submitAsset() {
    const body = {
      owner_type: this._aOwner,
      client_id: this._aOwner==='client' ? (document.getElementById('a-client').value||null) : null,
      asset_type: document.getElementById('a-type').value,
      location_id: document.getElementById('a-loc').value || null,
      brand: document.getElementById('a-brand').value || null,
      model: document.getElementById('a-model').value || null,
      serial: document.getElementById('a-serial').value || null,
      imei: document.getElementById('a-imei').value || null,
      purchase_price: parseFloat(document.getElementById('a-pp').value)||0,
      sale_price: parseFloat(document.getElementById('a-sp').value)||0,
      asset_status: this._aOwner==='company' ? 'in_stock' : 'in_use',
    };
    if (this._aOwner==='client' && !body.client_id) return Toast.error('Pick a client for a client-owned asset');
    try { await post('/api/assets', body); Toast.success('Asset added'); Modal.close(); this._loadAssetsTab(); }
    catch (e) { Toast.error(e.message); }
  },
  async viewAsset(id) {
    let a; try { a = await get(`/api/assets/${id}`); } catch (e) { return Toast.error(e.message); }
    this._activeAsset = a;
    const statusLabel = { in_use:'In use', in_stock:'In stock', for_sale:'For sale', sold:'Sold', written_off:'Written off' };
    const hist = a.history || [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${a.brand||''} ${a.model||''}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="wo-tabs" style="padding:0 22px">
        <button class="wo-tab active" onclick="InventoryPage.assetTab(event,'general')">General</button>
        <button class="wo-tab" onclick="InventoryPage.assetTab(event,'tickets')">Work orders (${hist.length})</button>
        <button class="wo-tab" onclick="InventoryPage.assetTab(event,'history')">History</button>
      </div>
      <div class="modal-body">
        <div class="ia-panel active" data-iapanel="general">
          <div class="detail-grid">
            <div class="detail-blob"><div class="d-label">Type</div><div class="d-value">${a.asset_type||'—'}</div></div>
            <div class="detail-blob"><div class="d-label">Owner</div><div class="d-value">${a.owner_type==='company'?'Company':(a.owner_name||'Client')}</div></div>
            <div class="detail-blob"><div class="d-label">Serial</div><div class="d-value" style="font-size:12px">${a.serial||'—'}</div></div>
            <div class="detail-blob"><div class="d-label">IMEI</div><div class="d-value" style="font-size:12px">${a.imei||'—'}</div></div>
            <div class="detail-blob"><div class="d-label">Status</div><div class="d-value">${statusLabel[a.asset_status]||a.asset_status}</div></div>
            <div class="detail-blob"><div class="d-label">Condition</div><div class="d-value">${a.condition||'—'}</div></div>
          </div>
          ${a.converted_to_part_id?'<div class="badge b-done" style="margin-top:14px">Converted to product</div>':''}
        </div>
        <div class="ia-panel" data-iapanel="tickets" style="display:none">
          ${hist.length ? `<div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Order #</th><th>Status</th><th>Total</th><th>Created</th></tr></thead>
            <tbody>${hist.map(o=>`<tr style="cursor:pointer" onclick="Modal.close();Router.go('orders');setTimeout(()=>OrdersPage.viewOrder('${o.id}'),300)">
              <td><span class="tbl-num tbl-strong">${o.order_number}</span></td>
              <td>${badge(o.status)}</td><td>${fmt.currency(o.total)}</td>
              <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(o.created_at)}</td></tr>`).join('')}</tbody>
          </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No work orders for this asset yet.</div>'}
        </div>
        <div class="ia-panel" data-iapanel="history" style="display:none">
          <div id="ia-history"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading movement history…</div></div>
        </div>
      </div>
      <div class="modal-footer">
        ${a.owner_type==='company'&&!a.converted_to_part_id?`<button class="btn btn-glass" onclick="InventoryPage.convertAssetModal('${a.id}','${(a.brand+' '+a.model).replace(/'/g,'')}')">Convert to product</button>`:''}
        ${a.asset_status!=='written_off'?`<button class="btn btn-glass" onclick="InventoryPage.transferAssetModal('${a.id}')">Transfer</button>
        <button class="btn btn-glass" onclick="InventoryPage.writeOffAssetModal('${a.id}')">Write off</button>`:''}
        <button class="btn btn-primary" onclick="Modal.close()">Close</button>
      </div>`, 'modal-wide');
  },

  assetTab(e, panel) {
    document.querySelectorAll('.wo-tab').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    document.querySelectorAll('.ia-panel').forEach(p => p.style.display = p.dataset.iapanel === panel ? 'block' : 'none');
    if (panel === 'history') this._loadAssetHistory();
  },

  async _loadAssetHistory() {
    const box = document.getElementById('ia-history'); if (!box || !this._activeAsset) return;
    const a = this._activeAsset;
    const eventLabel = { create:'Created', transfer:'Transfer', writeoff:'Written off',
                         owner:'Ownership change', zone:'Moved' };
    try {
      const log = await get(`/api/assets/${a.id}/movements`);
      const rows = [];
      (log.items || []).forEach(m => rows.push({
        when: m.at,
        event: eventLabel[m.event_type] || m.event_type,
        detail: [m.from_zone && m.to_zone ? `${m.from_zone} → ${m.to_zone}` : (m.to_zone||''), m.note].filter(Boolean).join(' · '),
        by: m.by }));
      (a.history || []).forEach(o => rows.push({
        when: o.created_at, event: 'Work order ' + o.order_number,
        detail: 'Status: ' + o.status, by: null }));
      rows.sort((x, y) => new Date(y.when||0) - new Date(x.when||0));
      box.innerHTML = rows.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Event</th><th>Detail</th><th>By</th><th>Date</th></tr></thead>
        <tbody>${rows.map(r=>`<tr>
          <td class="tbl-strong">${r.event}</td>
          <td style="color:var(--tx-3)">${r.detail||'—'}</td>
          <td style="color:var(--tx-3)">${r.by||'—'}</td>
          <td style="color:var(--tx-3);font-size:12px">${r.when?fmt.datetime(r.when):'—'}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No history yet.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async transferAssetModal(id) {
    let clients = [];
    try { clients = (await get('/api/clients?limit=200')).items || []; } catch (e) { /* ok */ }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Transfer asset</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Move this asset to a different owner. The transfer is recorded in the asset's history.</p>
        <div class="field"><label class="field-label">New owner</label>
          <select class="field-select" id="tr-owner">
            <option value="">Company</option>
            ${clients.map(c=>`<option value="${c.id}">${c.name}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">Note (optional)</label>
          <input class="field-input" id="tr-note" placeholder="Reason / reference"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitTransfer('${id}')">Transfer</button>
      </div>`, 'modal-narrow');
  },

  async submitTransfer(id) {
    const to = document.getElementById('tr-owner').value;
    const note = document.getElementById('tr-note').value.trim();
    try {
      await post(`/api/assets/${id}/transfer`, { to_client_id: to || null, note: note || null });
      Toast.success('Asset transferred');
      Modal.close();
      this._loadAssetsTab && this._loadAssetsTab();
    } catch (e) { Toast.error(e.message || 'Could not transfer'); }
  },

  writeOffAssetModal(id) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Write off asset</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Mark this asset as written off (damaged, lost or scrapped). It stays on record with full history but is removed from active stock.</p>
        <div class="field"><label class="field-label">Reason (optional)</label>
          <input class="field-input" id="wo-note" placeholder="e.g. water damage, beyond repair"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-danger" onclick="InventoryPage.submitWriteOff('${id}')">Write off</button>
      </div>`, 'modal-narrow');
  },

  async submitWriteOff(id) {
    const note = document.getElementById('wo-note').value.trim();
    try {
      await post(`/api/assets/${id}/write-off`, { note: note || null });
      Toast.success('Asset written off');
      Modal.close();
      this._loadAssetsTab && this._loadAssetsTab();
    } catch (e) { Toast.error(e.message || 'Could not write off'); }
  },
  convertAssetModal(id, name) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Convert to product</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px">Turn this company asset into sellable stock. It will appear under Stock (products) with 1 unit.</div>
        <div class="field"><label class="field-label">Product name</label><input class="field-input" id="cv-name" value="${name}"></div>
        <div class="field"><label class="field-label">Sale price</label><input class="field-input" id="cv-price" type="number" min="0" value="0"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.submitConvert('${id}')">Convert</button>
      </div>`, 'modal-narrow');
  },
  async submitConvert(id) {
    const body = { sale_price: parseFloat(document.getElementById('cv-price').value)||0 };
    try { await post(`/api/assets/${id}/convert-to-product`, body); Toast.success('Converted to product'); Modal.close(); this._loadAssetsTab(); }
    catch (e) { Toast.error(e.message); }
  },

  /* ── Purchase orders tab ── */
  async _loadPoTab() {
    setTopbarActions(`<button class="btn btn-primary" onclick="InventoryPage.newPoModal()">+ New purchase order</button>`);
    const body = document.getElementById('inv-body');
    body.innerHTML = `<div id="po-tab-list"><div class="card" style="padding:18px;color:var(--tx-4)">Loading…</div></div>`;
    try {
      const pos = (await get('/api/inventory/po')).items;
      document.getElementById('po-tab-list').innerHTML = `
        <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>PO #</th><th>Status</th><th>Lines</th><th>Total</th><th>Expected</th><th style="text-align:right"></th></tr></thead>
          <tbody>${pos.map(po => `<tr style="cursor:pointer" onclick="InventoryPage.openPo('${po.id}')">
            <td class="tbl-strong tbl-num">${po.po_number}</td>
            <td><span class="badge b-${po.status === 'received' ? 'done' : 'new'}">${po.status}</span></td>
            <td>${po.lines.length}</td>
            <td>${fmt.currency(po.total)}</td>
            <td style="color:var(--tx-3);font-size:12px">${po.expected_at ? fmt.date(po.expected_at) : '—'}</td>
            <td style="text-align:right" onclick="event.stopPropagation()">
              <button class="btn btn-glass btn-sm" onclick="InventoryPage.openPo('${po.id}')">Open →</button>
              ${po.status !== 'received' ? `<button class="btn btn-success btn-sm" onclick="InventoryPage.receivePoTab('${po.id}')">Receive</button>` : ''}
            </td>
          </tr>`).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:22px">No purchase orders yet</td></tr>`}
          </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  /* ══ Build 144al — editable Purchase Order detail (2 tabs: General + Products) ══ */
  async openPo(id) {
    let d, parts, chargeTypes;
    try {
      [d, parts, chargeTypes] = await Promise.all([
        get(`/api/inventory/po/${id}`),
        get('/api/inventory/parts?limit=500').then(r => r.items || []).catch(() => []),
        get('/api/inventory/po-charge-types').then(r => r.types).catch(() => ['Shipping', 'In-transit']),
      ]);
    } catch (e) { return Toast.error(e.message); }
    this._po = d;
    this._poParts = parts;
    this._poChargeTypes = chargeTypes;
    this._poLines = (d.lines || []).map(l => ({ id: l.id, part_id: l.part_id, part: l.part, qty_ordered: l.qty_ordered, unit_cost: l.unit_cost, qty_received: l.qty_received || 0 }));
    this._poCharges = (d.charges || []).map(c => ({ label: c.label, amount: c.amount }));
    this._poChargesToCost = !!d.charges_to_cost;
    const ed = !!d.editable;
    const sup = d.supplier || {};
    const dt = (s) => (s || '').slice(0, 10);
    Modal.open(`
      <div class="modal-header">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span class="modal-title">Purchase order ${d.po_number}</span>
          <span class="badge b-${d.status === 'received' ? 'done' : 'new'}">${d.status}</span>
          ${ed ? '' : '<span style="font-size:11px;color:var(--tx-3)">received — locked</span>'}
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-glass btn-sm" onclick="openPrintPdf('/api/print/purchase-order/${d.id}','PO-${d.po_number}.pdf')">${icon('file', 14)} Print</button>
          <button class="modal-close" onclick="Modal.close()">×</button>
        </div>
      </div>
      <div class="modal-body">
        <div class="wo-tabs">
          <button class="wo-tab active" onclick="InventoryPage.poTab(event,'general')">General</button>
          <button class="wo-tab" onclick="InventoryPage.poTab(event,'products')">Products to order</button>
        </div>

        <div class="wo-tab-panel active" data-panel="general">
          <div class="detail-grid detail-grid-lg">
            <div class="detail-blob">
              <div class="d-label">Supplier</div>
              <div class="d-value">${sup.name || '—'}</div>
              ${sup.phone ? `<div style="font-size:11px;color:var(--tx-3);margin-top:2px">${sup.phone}</div>` : ''}
              ${sup.email ? `<div style="font-size:11px;color:var(--tx-3)">${sup.email}</div>` : ''}
              ${sup.balance ? `<div style="font-size:11px;color:var(--amber);margin-top:3px">We owe: ${fmt.currency(sup.balance)}</div>` : ''}
            </div>
            <div class="detail-blob">
              <div class="d-label">Our PO number</div><div class="d-value">${d.po_number}</div>
              <div class="d-label" style="margin-top:8px">Expected</div>
              <input class="field-input" type="date" id="po-expected" value="${dt(d.expected_at)}" ${ed ? '' : 'disabled'}>
            </div>
            <div class="detail-blob">
              <div class="d-label">Supplier invoice / bill no</div>
              <input class="field-input" id="po-invno" value="${(d.supplier_invoice_no || '').replace(/"/g, '&quot;')}" placeholder="e.g. INV-1024" ${ed ? '' : 'disabled'}>
              <div class="d-label" style="margin-top:8px">Invoice date</div>
              <input class="field-input" type="date" id="po-invdate" value="${dt(d.supplier_invoice_date)}" ${ed ? '' : 'disabled'}>
            </div>
            <div class="detail-blob">
              <div class="d-label">Buying on credit</div>
              <label class="cash-check" style="margin-top:4px"><input type="checkbox" id="po-oncredit" ${d.on_credit ? 'checked' : ''} ${ed ? '' : 'disabled'}> Adds to supplier balance on receive</label>
            </div>
          </div>
          <div class="field" style="margin-top:6px"><label class="field-label">Notes</label>
            <textarea class="field-textarea" id="po-notes" placeholder="Anything to remember about this order…" ${ed ? '' : 'disabled'}>${d.notes || ''}</textarea></div>
        </div>

        <div class="wo-tab-panel" data-panel="products">
          ${ed ? `<div class="field-row" style="margin-bottom:8px">
            <div class="field"><label class="field-label">Add a product to order</label>
              <select class="field-select" id="po-addpart" onchange="InventoryPage.poAddLine()">
                <option value="">— pick a product —</option>
                ${parts.map(p => `<option value="${p.id}" data-cost="${p.cost_price || p.avg_cost || 0}">${p.name}${p.sku ? ' · ' + p.sku : ''}</option>`).join('')}
              </select></div>
          </div>` : ''}
          <div id="po-lines"></div>
          <div style="margin-top:14px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
              <div class="d-label">Charges (optional)</div>
              ${ed ? `<div style="display:flex;gap:6px">
                <button class="btn btn-glass btn-sm" onclick="InventoryPage.poManageChargeTypes()" title="Customize the charge types">${icon('bolt',12)} Types</button>
                <button class="btn btn-glass btn-sm" onclick="InventoryPage.poAddCharge()">＋ Add charge</button>
              </div>` : ''}
            </div>
            <div id="po-charges"></div>
            ${ed ? `<label class="cash-check" style="margin-top:8px"><input type="checkbox" id="po-tocost" ${this._poChargesToCost ? 'checked' : ''}> Add charges to item cost (landed cost — spreads across received units)</label>` : ''}
          </div>
          <div id="po-totals" style="margin-top:14px"></div>
        </div>
      </div>
      <div class="modal-footer">
        ${ed && d.status !== 'received' ? `<button class="btn btn-success" onclick="InventoryPage.receiveFromDetail('${d.id}')">Receive stock</button>` : ''}
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
        ${ed ? `<button class="btn btn-primary" onclick="InventoryPage.savePo('${d.id}')">Save changes</button>` : ''}
      </div>`, 'modal-wide');
    this._renderPoLines();
  },

  poTab(e, panel) {
    e.currentTarget.parentElement.querySelectorAll('.wo-tab').forEach(t => t.classList.remove('active'));
    e.currentTarget.classList.add('active');
    document.querySelectorAll('.wo-tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === panel));
  },

  poAddLine() {
    const sel = document.getElementById('po-addpart');
    const id = sel.value; if (!id) return;
    const opt = sel.selectedOptions[0];
    const p = (this._poParts || []).find(x => x.id === id);
    this._poLines.push({ part_id: id, part: p ? p.name : 'Product', qty_ordered: 1, unit_cost: parseFloat(opt.dataset.cost) || 0, qty_received: 0 });
    sel.value = '';
    this._renderPoLines();
  },
  poUpd(i, k, v) { this._poLines[i][k] = parseFloat(v) || 0; this._renderPoLines(); },
  poRm(i) {
    if ((this._poLines[i].qty_received || 0) > 0) return Toast.error('This line has received stock and can\'t be removed');
    this._poLines.splice(i, 1); this._renderPoLines();
  },
  poAddCharge() {
    const types = this._poChargeTypes || ['Shipping'];
    this._poCharges.push({ label: types[0] || 'Shipping', amount: 0 });
    this._renderPoLines();
  },
  // Customizable charge types — edit the org-wide list without leaving the PO.
  async poManageChargeTypes() {
    const cur = (this._poChargeTypes || []).join(', ');
    const next = await Modal.prompt('Charge types (comma separated) — used across all purchase orders:', {default: cur});
    if (next === null) return;
    const list = next.split(',').map(s => s.trim()).filter(Boolean);
    try {
      const r = await put('/api/inventory/po-charge-types', { types: list });
      this._poChargeTypes = r.types;
      Toast.success('Charge types updated');
      this._renderPoLines();
    } catch (e) { Toast.error(e.message); }
  },
  poUpdCharge(i, k, v) { this._poCharges[i][k] = k === 'amount' ? (parseFloat(v) || 0) : v; this._renderPoLines(); },
  poRmCharge(i) { this._poCharges.splice(i, 1); this._renderPoLines(); },

  _renderPoLines() {
    const ed = !!(this._po && this._po.editable);
    const el = document.getElementById('po-lines');
    if (el) {
      el.innerHTML = this._poLines.length ? `
        <table class="tbl" style="margin-top:2px"><thead><tr>
          <th>Product</th><th style="width:90px">Qty</th><th style="width:110px">Unit cost</th><th style="width:110px;text-align:right">Line total</th>${ed ? '<th style="width:36px"></th>' : ''}
        </tr></thead><tbody>${this._poLines.map((l, i) => `<tr>
          <td>${l.part}${l.qty_received ? `<span style="font-size:10.5px;color:var(--green);margin-left:6px">${l.qty_received} received</span>` : ''}</td>
          <td>${ed ? `<input class="field-input" style="padding:4px 8px" type="number" min="0" value="${l.qty_ordered}" onchange="InventoryPage.poUpd(${i},'qty_ordered',this.value)">` : l.qty_ordered}</td>
          <td>${ed ? `<input class="field-input" style="padding:4px 8px" type="number" min="0" step="0.01" value="${l.unit_cost}" onchange="InventoryPage.poUpd(${i},'unit_cost',this.value)">` : fmt.currency(l.unit_cost)}</td>
          <td style="text-align:right;font-weight:700">${fmt.currency((l.qty_ordered || 0) * (l.unit_cost || 0))}</td>
          ${ed ? `<td style="text-align:center"><button class="btn btn-glass btn-sm" onclick="InventoryPage.poRm(${i})">×</button></td>` : ''}
        </tr>`).join('')}</tbody></table>` : '<div style="text-align:center;color:var(--tx-4);font-size:12.5px;padding:16px 0">No products on this order yet.</div>';
    }
    const cEl = document.getElementById('po-charges');
    if (cEl) {
      const types = this._poChargeTypes || [];
      cEl.innerHTML = this._poCharges.length ? this._poCharges.map((c, i) => `
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
          ${ed ? `<select class="field-select" style="flex:1" onchange="InventoryPage.poUpdCharge(${i},'label',this.value)">
            ${types.map(t => `<option ${t === c.label ? 'selected' : ''}>${t}</option>`).join('')}
            ${types.includes(c.label) ? '' : `<option selected>${c.label}</option>`}
          </select>
          <input class="field-input" style="width:120px" type="number" min="0" step="0.01" value="${c.amount}" onchange="InventoryPage.poUpdCharge(${i},'amount',this.value)">
          <button class="btn btn-glass btn-sm" onclick="InventoryPage.poRmCharge(${i})">×</button>`
          : `<span style="flex:1">${c.label}</span><span style="font-weight:700">${fmt.currency(c.amount)}</span>`}
        </div>`).join('') : '<div style="color:var(--tx-4);font-size:12px">No charges — optional.</div>';
    }
    const tEl = document.getElementById('po-totals');
    if (tEl) {
      const subtotal = this._poLines.reduce((a, l) => a + (l.qty_ordered || 0) * (l.unit_cost || 0), 0);
      const charges = this._poCharges.reduce((a, c) => a + (c.amount || 0), 0);
      tEl.innerHTML = `<div style="display:flex;flex-direction:column;gap:3px;align-items:flex-end;font-size:13px">
        <div style="color:var(--tx-3)">Subtotal: <b style="color:var(--tx-1)">${fmt.currency(subtotal)}</b></div>
        ${charges ? `<div style="color:var(--tx-3)">Charges: <b style="color:var(--tx-1)">${fmt.currency(charges)}</b></div>` : ''}
        <div style="font-size:16px;font-weight:900">Total: ${fmt.currency(subtotal + charges)}</div>
      </div>`;
    }
  },

  _poBody() {
    const g = (x) => document.getElementById(x);
    return {
      supplier_invoice_no: g('po-invno') ? g('po-invno').value || null : undefined,
      supplier_invoice_date: g('po-invdate') ? (g('po-invdate').value || null) : undefined,
      expected_at: g('po-expected') ? (g('po-expected').value ? g('po-expected').value + 'T00:00:00' : null) : undefined,
      notes: g('po-notes') ? g('po-notes').value || null : undefined,
      on_credit: g('po-oncredit') ? g('po-oncredit').checked : undefined,
      charges_to_cost: g('po-tocost') ? g('po-tocost').checked : undefined,
      charges: this._poCharges.map(c => ({ label: c.label, amount: c.amount })),
      lines: this._poLines.map(l => ({ id: l.id, part_id: l.part_id, qty_ordered: l.qty_ordered, unit_cost: l.unit_cost })),
    };
  },

  async _savePoQuiet(id) {
    await patch(`/api/inventory/po/${id}`, this._poBody());
  },

  async savePo(id) {
    try {
      await this._savePoQuiet(id);
      Toast.success('Purchase order saved');
      Modal.close();
      this._loadPoTab();
    } catch (e) { Toast.error(e.message); }
  },

  async receiveFromDetail(id) {
    // persist edits first; only receive if the save succeeded
    try { await this._savePoQuiet(id); }
    catch (e) { return Toast.error(e.message); }
    try {
      const r = await post(`/api/inventory/po/${id}/receive`, { lines: [] });
      Toast.success(`Received (${r.status})`);
      Modal.close();
      this._loadPoTab();
    } catch (e) { Toast.error(e.message); }
  },
  async receivePoTab(poId) {
    try { const r = await post(`/api/inventory/po/${poId}/receive`, { lines: [] });
      Toast.success(`PO received (${r.status})`); this._loadPoTab();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── Postings / Write-offs tabs (shared) ── */
  async _loadMovementsTab(kind) {
    const isWO = kind === 'writeoff';
    setTopbarActions(`<button class="btn btn-primary" onclick="InventoryPage.newMovementModal('${kind}')">+ New ${isWO ? 'write-off' : 'posting'}</button>`);
    const body = document.getElementById('inv-body');
    body.innerHTML = `
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:720px">
        ${isWO ? 'Goods leaving inventory by hand — damage, loss, internal use. Each write-off lowers stock and is recorded on the ledger.'
               : 'Stock coming into inventory — manual stock-in and corrections. Postings raise the quantity and are recorded on the ledger.'}</div>
      <div id="mv-list"><div class="card" style="padding:18px;color:var(--tx-4)">Loading…</div></div>`;
    try {
      const data = await get(`/api/inventory/movements?kind=${kind}`);
      document.getElementById('mv-list').innerHTML = `
        <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>When</th><th>Product</th><th>Warehouse</th><th style="text-align:right">Qty</th><th>Source</th></tr></thead>
          <tbody>${data.items.map(m => `<tr>
            <td style="color:var(--tx-3);white-space:nowrap">${fmt.datetime(m.at)}</td>
            <td class="tbl-strong">${m.part_name}</td>
            <td style="color:var(--tx-3)">${m.location}</td>
            <td style="text-align:right;font-weight:800;color:${isWO ? 'var(--red)' : 'var(--green)'}">${isWO ? '-' : '+'}${m.qty}</td>
            <td style="color:var(--tx-3)">${(m.ref_type || 'manual').replace('_',' ')}</td>
          </tr>`).join('') || `<tr><td colspan="5" style="text-align:center;color:var(--tx-4);padding:22px">No ${isWO ? 'write-offs' : 'postings'} yet</td></tr>`}
          </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  async newMovementModal(kind) {
    const isWO = kind === 'writeoff';
    let parts = [], locs = [], suppliers = [];
    try {
      parts = (await get('/api/inventory/parts?limit=300')).items;
      locs = (await get('/api/staff/locations')).items || [];
      if (!isWO) { try { suppliers = (await get('/api/inventory/suppliers')).items || []; } catch { suppliers = []; } }
    } catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New ${isWO ? 'write-off' : 'posting — bulk stock inflow'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Warehouse *</label>
          <select class="field-select" id="mv-loc">${locs.map(l => `<option value="${l.id}">${l.name}</option>`).join('')}</select></div>
        ${isWO ? `
        <div class="field"><label class="field-label">Product</label>
          <select class="field-select" id="mv-part">${parts.map(p => `<option value="${p.id}">${p.name}${p.sku ? ' · ' + p.sku : ''}</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Quantity to remove</label>
          <input class="field-input" id="mv-qty" type="number" min="0.001" step="any" value="1"></div>
        <div class="field"><label class="field-label">Note</label>
          <input class="field-input" id="mv-note" placeholder="e.g. Damaged in handling"></div>
        ` : `
        <div class="field"><label class="field-label">Invoice / note</label>
          <input class="field-input" id="mv-note" placeholder="Supplier invoice #"></div>
        <div class="field"><label class="field-label">Supplier</label>
          <select class="field-select" id="mv-supplier" onchange="InventoryPage.onPostSupplierChange(this.value)">
            <option value="">— No supplier —</option>
            ${suppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('')}
            <option value="__new">➕ Add new supplier…</option>
          </select>
          <div id="mv-newsup" style="display:none;margin-top:8px;padding:12px;border:1px solid var(--g-border);border-radius:var(--r-md);background:rgba(79,142,247,.04)">
            <div class="field" style="margin-bottom:8px"><label class="field-label">Supplier name</label><input class="field-input" id="mv-sup-name" placeholder="e.g. TechParts Wholesale"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <input class="field-input" id="mv-sup-phone" placeholder="Phone">
              <input class="field-input" id="mv-sup-email" placeholder="Email">
            </div>
            <button class="btn btn-primary btn-sm" style="margin-top:8px" onclick="InventoryPage.createPostSupplier()">Save supplier</button>
          </div>
        </div>
        <p style="font-size:12px;color:var(--tx-3);margin:4px 0 12px">Add products to this posting. Purchase price is required per line; enter a markup&nbsp;% to auto-set the new sell price.</p>
        <div class="set-grid" style="grid-template-columns:2fr 1fr">
          <div class="field"><label class="field-label">Product</label>
            <select class="field-select" id="pl-part">${parts.map(p => `<option value="${p.id}" data-name="${(p.name||'').replace(/"/g,'')}">${p.name}${p.sku ? ' · ' + p.sku : ''}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Qty</label><input class="field-input" id="pl-qty" type="number" min="0.001" step="any" value="1"></div>
        </div>
        <div class="set-grid" style="grid-template-columns:1fr 1fr">
          <div class="field"><label class="field-label">Purchase price *</label><input class="field-input" id="pl-pp" type="number" min="0" step="any" placeholder="0"></div>
          <div class="field"><label class="field-label">Markup %</label><input class="field-input" id="pl-mk" type="number" step="any" placeholder="keep 0%"></div>
        </div>
        <button class="btn btn-glass" style="width:100%;margin-bottom:14px" onclick="InventoryPage.addPostingLine()">+ Add</button>
        <table class="tbl" style="width:100%"><thead><tr><th>Product</th><th style="text-align:right">Qty</th><th style="text-align:right">Purchase $</th><th style="text-align:right">New sell $</th><th></th></tr></thead>
          <tbody id="pl-rows"><tr><td colspan="5" style="color:var(--tx-4);padding:12px">No lines yet.</td></tr></tbody>
          <tfoot><tr><td colspan="4" style="text-align:right;font-weight:700;padding-top:8px">Total cost</td><td style="text-align:right;font-weight:700;padding-top:8px" id="pl-total">${fmt.currency(0)}</td></tr></tfoot>
        </table>`}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.${isWO ? `submitMovement('${kind}')` : 'submitPosting()'}">${isWO ? 'Write off' : 'Post stock'}</button>
      </div>`, 'modal-wide');
    if (!isWO) this._postingLines = [];
  },

  onPostSupplierChange(val) {
    const box = document.getElementById('mv-newsup');
    if (box) box.style.display = val === '__new' ? 'block' : 'none';
  },

  async createPostSupplier() {
    const name = document.getElementById('mv-sup-name').value.trim();
    if (!name) return Toast.error('Enter a supplier name');
    const phone = document.getElementById('mv-sup-phone').value.trim();
    const email = document.getElementById('mv-sup-email').value.trim();
    try {
      const s = await post('/api/suppliers', { name, phone: phone || null, email: email || null });
      Toast.success('Supplier added');
      const sel = document.getElementById('mv-supplier');
      const opt = document.createElement('option');
      opt.value = s.id; opt.textContent = s.name;
      sel.insertBefore(opt, sel.querySelector('option[value="__new"]'));
      sel.value = s.id;
      document.getElementById('mv-newsup').style.display = 'none';
    } catch (e) { Toast.error(e.message || 'Could not add supplier'); }
  },

  _postingLines: [],
  addPostingLine() {
    const sel = document.getElementById('pl-part');
    const part_id = sel.value;
    const name = sel.options[sel.selectedIndex]?.dataset.name || 'Product';
    const qty = parseFloat(document.getElementById('pl-qty').value) || 0;
    const pp = parseFloat(document.getElementById('pl-pp').value);
    const mkRaw = document.getElementById('pl-mk').value;
    const markup_pct = mkRaw === '' ? null : parseFloat(mkRaw);
    if (!part_id) return Toast.error('Pick a product');
    if (qty <= 0) return Toast.error('Enter a positive quantity');
    if (isNaN(pp) || pp < 0) return Toast.error('Purchase price is required');
    const new_sell = markup_pct != null ? Math.round(pp * (1 + markup_pct / 100) * 100) / 100 : pp;
    this._postingLines.push({ part_id, name, qty, purchase_price: pp, markup_pct, new_sell });
    document.getElementById('pl-pp').value = '';
    document.getElementById('pl-mk').value = '';
    this._renderPostingLines();
  },
  removePostingLine(i) { this._postingLines.splice(i, 1); this._renderPostingLines(); },
  _renderPostingLines() {
    const rows = document.getElementById('pl-rows');
    const L = this._postingLines;
    let total = 0;
    rows.innerHTML = L.length ? L.map((l, i) => {
      total += l.qty * l.purchase_price;
      return `<tr><td class="tbl-strong">${l.name}</td><td style="text-align:right">${l.qty}</td>
        <td style="text-align:right">${fmt.currency(l.purchase_price)}</td>
        <td style="text-align:right;color:var(--blue-dark)">${fmt.currency(l.new_sell)}</td>
        <td style="text-align:right"><button class="btn btn-glass btn-sm" onclick="InventoryPage.removePostingLine(${i})">×</button></td></tr>`;
    }).join('') : '<tr><td colspan="5" style="color:var(--tx-4);padding:12px">No lines yet.</td></tr>';
    document.getElementById('pl-total').textContent = fmt.currency(total);
  },
  async submitPosting() {
    const location_id = document.getElementById('mv-loc').value;
    if (!location_id) return Toast.error('Pick a warehouse');
    if (!this._postingLines.length) return Toast.error('Add at least one product line');
    try {
      const r = await post('/api/inventory/postings', {
        location_id,
        note: document.getElementById('mv-note').value.trim() || null,
        supplier_id: (document.getElementById('mv-supplier')?.value || '') || null,
        lines: this._postingLines.map(l => ({ part_id: l.part_id, qty: l.qty, purchase_price: l.purchase_price, markup_pct: l.markup_pct })),
      });
      Toast.success(`Posted ${r.lines_posted} line${r.lines_posted === 1 ? '' : 's'} · ${fmt.currency(r.total_cost)}`);
      Modal.close(); this._loadMovementsTab('posting');
    } catch (e) { Toast.error(e.message); }
  },
  async submitMovement(kind) {
    const part_id = document.getElementById('mv-part').value;
    const location_id = document.getElementById('mv-loc').value;
    const qty = parseFloat(document.getElementById('mv-qty').value);
    if (!location_id) return Toast.error('Pick a warehouse');
    if (!qty || qty <= 0) return Toast.error('Enter a positive quantity');
    try {
      await post(`/api/inventory/parts/${part_id}/adjust`, {
        location_id, qty, movement_type: kind === 'writeoff' ? 'writeoff' : 'receive',
        note: document.getElementById('mv-note').value.trim() || null,
      });
      Toast.success(kind === 'writeoff' ? 'Stock written off' : 'Stock posted');
      Modal.close(); this._loadMovementsTab(kind);
    } catch (e) { Toast.error(e.message); }
  },

  /* ── Reservations tab ── */
  async _loadReservationsTab() {
    setTopbarActions('');
    const body = document.getElementById('inv-body');
    body.innerHTML = `
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:720px">
        Stock set aside for specific orders — excluded from available quantity until released or fulfilled.</div>
      <div id="rsv-list"><div class="card" style="padding:18px;color:var(--tx-4)">Loading…</div></div>`;
    try {
      const data = await get('/api/inventory/reservations');
      document.getElementById('rsv-list').innerHTML = `
        <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Product</th><th style="text-align:right">Reserved</th><th>For order</th><th>Expires</th><th style="text-align:right"></th></tr></thead>
          <tbody>${data.items.map(r => `<tr>
            <td class="tbl-strong">${r.part || '?'}</td>
            <td style="text-align:right;font-weight:700">${r.qty}</td>
            <td style="color:var(--tx-3)">${r.order_id ? r.order_id.slice(0,8) : '—'}</td>
            <td style="color:var(--tx-3)">${r.expires_at ? fmt.datetime(r.expires_at) : '—'}</td>
            <td style="text-align:right"><button class="btn btn-glass btn-sm" onclick="InventoryPage.releaseReservation('${r.id}')">Release</button></td>
          </tr>`).join('') || `<tr><td colspan="5" style="text-align:center;color:var(--tx-4);padding:22px">No active reservations</td></tr>`}
          </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },
  async releaseReservation(id) {
    try { await post(`/api/inventory/reservations/${id}/release`, {});
      Toast.success('Reservation released'); this._loadReservationsTab();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── Conversions tab ── */
  async _loadConversionsTab() {
    // Build 77: the tab now hosts the full two-way conversion document log
    // (ConversionsPage), replacing the old one-way "assets that became products" list.
    this._mountExternal(() => ConversionsPage.load());
  },

  /* ── Build 96: bulk edit the currently filtered products ── */
  bulkModal() {
    const items = this._visible || [];
    if (!items.length) return Toast.error('No products in the current filter');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Bulk edit ${items.length} product(s)</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Applies to every product matching the current search and filters. Price changes are a
          <b>percentage</b> — the only thing that makes sense across a mixed selection.
        </p>
        <div class="field"><label class="field-label">Action</label>
          <select class="field-select" id="blk-action" onchange="InventoryPage._bulkFields()">
            <option value="adjust_price_pct">Adjust sell price by %</option>
            <option value="set_min_stock">Set minimum stock</option>
            <option value="set_active">Set active / archived</option>
          </select></div>
        <div id="blk-fields"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InventoryPage.bulkApply()">Apply to ${items.length}</button>
      </div>`);
    this._bulkFields();
  },

  _bulkFields() {
    const a = document.getElementById('blk-action').value;
    const host = document.getElementById('blk-fields');
    if (a === 'adjust_price_pct') {
      host.innerHTML = `<div class="field"><label class="field-label">Percentage change (e.g. 5 or -10)</label>
        <input class="field-input" type="number" id="blk-num" value="5"></div>`;
    } else if (a === 'set_min_stock') {
      host.innerHTML = `<div class="field"><label class="field-label">Minimum stock</label>
        <input class="field-input" type="number" id="blk-num" value="0"></div>`;
    } else {
      host.innerHTML = `<div class="field"><label class="field-label">State</label>
        <select class="field-select" id="blk-num"><option value="1">Active</option><option value="0">Archived</option></select></div>`;
    }
  },

  async bulkApply() {
    const action = document.getElementById('blk-action').value;
    const raw = document.getElementById('blk-num').value;
    const ids = (this._visible || []).map(p => p.id);
    try {
      const r = await post('/api/inventory/parts/bulk', {
        part_ids: ids, action, number: parseFloat(raw) });
      Modal.close();
      Toast.success(`Updated ${r.changed} product(s)`);
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Bulk update failed'); }
  },


  /* ── Build 109: FEFO expiring-stock clearance list ── */
  async expiringModal() {
    let data;
    try { data = await get('/api/stock-docs/write-offs/suggestions?within_days=30'); }
    catch (e) { return Toast.error(e.message); }
    const money = (n) => n.toFixed(2);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Expiring stock to clear</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Batches expired or expiring within 30 days, earliest first. Writing these off consumes
          them first-expired-first-out. Total at risk: <b>${money(data.total_value)}</b>.
        </p>
        <div class="tbl-wrap" style="max-height:360px;overflow:auto"><table class="tbl">
          <thead><tr><th>Product</th><th>Warehouse</th><th>Qty</th><th>Expires</th><th>Value</th></tr></thead>
          <tbody>${data.items.map(i => `<tr>
            <td class="tbl-strong">${i.part_name}${i.expired ? ' <span class="badge">expired</span>' : ''}</td>
            <td>${i.warehouse_name}</td><td>${i.qty}</td>
            <td style="color:${i.expired ? 'var(--red,#e5484d)' : 'var(--tx-3)'};font-weight:${i.expired ? '800' : '500'}">
              ${i.expiry_date} (${i.days_left}d)</td>
            <td>${money(i.value_at_risk)}</td>
          </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--tx-4);padding:22px">Nothing expiring — all clear</td></tr>'}
          </tbody></table></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
      </div>`, 'modal-wide');
  },

};

Router.register('inventory', () => InventoryPage.load());
