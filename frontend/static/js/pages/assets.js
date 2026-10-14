/* ════════════════════════════════════════════════════
   Assets — the Asset Warehouse (client devices + company tools)
════════════════════════════════════════════════════ */

const AssetsPage = {
  _filter: { owner_type: '', asset_type: '' },

  async load() {
    setTopbarActions('');
    setContent(`
      <div class="list-toolbar">
        <div class="lt-left">
          <button class="btn btn-primary" onclick="AssetsPage.newModal()">+ Add asset</button>
          <div class="tabs">
            ${[['','All'],['company','Company-owned'],['client','Client-owned']].map(([v,l],i)=>
              `<button class="tab${i===0?' active':''}" data-owner="${v}" onclick="AssetsPage.filterOwner('${v}',this)">${l}</button>`).join('')}
          </div>
        </div>
        <div class="lt-right">
          <div class="search-box lt-search">
            <span class="search-icon">${icon('search',14)}</span>
            <input class="field-input search-input" id="a-search" placeholder="Search assets…" oninput="AssetsPage.reload()">
          </div>
          <button class="ic-btn" title="Columns & views" onclick="AssetsPage.columnsMenu()">${icon('bolt',16)}</button>
        </div>
      </div>
      <div class="card">
        <div class="tbl-wrap"><table class="tbl" id="a-list-table">
          <thead><tr><th style="width:34px"><input type="checkbox" id="a-all" onclick="AssetsPage.toggleAll(this.checked)"></th><th style="width:44px"></th><th>Type</th><th>Asset</th><th>Serial / IMEI</th><th>Owner</th><th>Condition</th><th>Orders</th><th></th></tr></thead>
          <tbody id="a-tbody">${skeletonRows(9)}</tbody>
        </table></div>
      </div>`);
    await this.reload();
  },

  filterOwner(v, el) {
    this._filter.owner_type = v;
    document.querySelectorAll('.list-toolbar .tab').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    this.reload();
  },

  async reload() {
    const tbody = document.getElementById('a-tbody');
    if (!tbody) return;
    const search = document.getElementById('a-search')?.value || '';
    const p = new URLSearchParams();
    if (this._filter.owner_type) p.set('owner_type', this._filter.owner_type);
    if (search) p.set('search', search);
    try {
      const data = await get(`/api/assets?${p.toString()}`);
      this._items = data.items;
      if (!this._sel) this._sel = new Set();
      if (!data.items.length) { tbody.innerHTML = emptyRow(9,'device','No assets yet','Assets are created with orders or added here'); this._syncBulk(); return; }
      tbody.innerHTML = data.items.map(a => `
        <tr data-rid="${a.id}" class="${this._sel.has(a.id)?'row-sel':''}" onclick="AssetsPage.viewAsset('${a.id}')">
          <td onclick="event.stopPropagation()"><input type="checkbox" ${this._sel.has(a.id)?'checked':''} onclick="AssetsPage.toggleRow('${a.id}',this.checked)"></td>
          <td onclick="event.stopPropagation()">${thumbImg(a.image_data)}</td>
          <td><span class="badge b-new">${a.asset_type}</span></td>
          <td><span style="font-weight:800">${[a.brand,a.model].filter(Boolean).join(' ')||'—'}</span></td>
          <td><span class="tbl-num" style="font-size:12px">${a.vin||a.hin||a.serial||a.imei||'—'}</span>${a.is_generated_serial?' <span class="gen-tag">auto</span>':''}</td>
          <td>${a.owner_type==='company'?'<span style="color:var(--tx-3)">Company</span>':(a.owner_name||'—')}</td>
          <td style="color:var(--tx-2)">${a.condition||'—'}</td>
          <td><span class="tbl-strong">${a.order_count}</span></td>
          <td><button class="btn btn-glass btn-sm" onclick="event.stopPropagation();AssetsPage.viewAsset('${a.id}')">Open →</button></td>
        </tr>`).join('');
      if (window.ColTools) ColTools.resizable('a-list-table', 'assets');
      if (window.ListExtras) {
        const byId = {}; data.items.forEach(a => { byId[a.id] = a; });
        ListExtras.attach('a-list-table', 'device', byId);
      }
      const all = document.getElementById('a-all');
      if (all) all.checked = this._items.length > 0 && this._items.every(a => this._sel.has(a.id));
      this._syncBulk();
    } catch (e) { Toast.error(e.message); }
  },
  toggleRow(id, ck) {
    if (!this._sel) this._sel = new Set();
    ck ? this._sel.add(id) : this._sel.delete(id);
    const all = document.getElementById('a-all');
    if (all) all.checked = this._items.length > 0 && this._items.every(a => this._sel.has(a.id));
    const tr = document.querySelector(`#a-tbody tr[data-rid="${id}"]`);
    if (tr) tr.classList.toggle('row-sel', ck);
    this._syncBulk();
  },
  toggleAll(ck) { this._sel = new Set(ck ? (this._items||[]).map(a => a.id) : []); this.reload(); },
  _syncBulk() {
    if (typeof BulkBar === 'undefined') return;
    const n = (this._sel && this._sel.size) || 0;
    BulkBar.show(n, `<button class="btn btn-glass btn-sm" onclick="AssetsPage.exportSelected()">Export CSV</button>`,
                 () => { this._sel = new Set(); this.reload(); });
  },
  exportSelected() {
    const rows = (this._items || []).filter(a => this._sel.has(a.id));
    if (!rows.length) return;
    const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const head = ['Type', 'Brand', 'Model', 'Serial/IMEI', 'Owner', 'Condition', 'Orders'];
    const body = rows.map(a => [a.asset_type, a.brand, a.model, a.vin||a.hin||a.serial||a.imei||'',
      a.owner_type==='company'?'Company':(a.owner_name||''), a.condition, a.order_count].map(esc).join(','));
    const csv = [head.join(','), ...body].join('\n');
    const el = document.createElement('a');
    el.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    el.download = `assets-${new Date().toISOString().slice(0, 10)}.csv`; el.click(); URL.revokeObjectURL(el.href);
    if (typeof Toast !== 'undefined') Toast.success(`Exported ${rows.length} asset(s)`);
  },
  columnsMenu() { if (window.ListExtras) ListExtras.openMenu('device', () => this.reload()); },

  // 144da — full Orderry-style "New asset / vehicle" form. Opts: { clientId, assetType,
  // onCreate(createdAsset) }. When onCreate is given (e.g. opened from "+ Asset" on a
  // work order), the created asset is handed back instead of reloading the list.
  async newModal(opts = {}) {
    this._newOpts = opts || {};
    this._newAssetImg = null;
    this._assetCF = [];
    let clients = [];
    try { clients = (await get('/api/clients?limit=200')).items; } catch {}
    try { this._assetCF = await CustomFields.fetch('device'); } catch {}
    const preClient = opts.clientId || '';
    const preType = opts.assetType || 'Vehicle';
    const isVehicleDefault = /vehicle|car|truck|bike|boat/i.test(preType);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New asset</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="asset-form-grid">
          <!-- image drop zone -->
          <div class="asset-drop" id="a-drop" onclick="document.getElementById('a-file').click()">
            <input type="file" id="a-file" accept="image/png,image/jpeg,image/jpg" style="display:none" onchange="AssetsPage._pickImg(this)">
            <div id="a-drop-inner">
              ${icon('camera',22)}
              <div style="font-weight:700;margin-top:8px">Drop a photo here or <span class="wo-link">browse</span></div>
              <div style="font-size:11px;color:var(--tx-4);margin-top:4px">JPG / PNG · up to 5 MB</div>
            </div>
          </div>
          <div class="asset-form-fields">
            <div class="field">
              <label class="field-label">Owner</label>
              <div class="type-chips">
                <div class="type-chip active" data-owner="client" onclick="AssetsPage.pickOwner('client',this)" style="--tc:#4f8ef7"><span class="type-chip-dot" style="background:#4f8ef7"></span>Client device</div>
                <div class="type-chip" data-owner="company" onclick="AssetsPage.pickOwner('company',this)" style="--tc:#9b7bf4"><span class="type-chip-dot" style="background:#9b7bf4"></span>Company tool</div>
              </div>
            </div>
            <div class="field" id="a-client-wrap">
              <label class="field-label">Client *</label>
              <select class="field-select" id="a-client">
                <option value="">Select client…</option>
                ${clients.map(c=>`<option value="${c.id}"${c.id===preClient?' selected':''}>${c.name}${c.phone?' · '+c.phone:''}</option>`).join('')}
              </select>
            </div>
            <div class="field"><label class="field-label">Type</label>
              <select class="field-select" id="a-type">
                ${['Vehicle','Device','Phone','Laptop','Tablet','Appliance','Tool','Other'].map(t=>`<option${t===preType?' selected':''}>${t}</option>`).join('')}
              </select>
            </div>
          </div>
        </div>

        <div class="field">
          <label class="field-label">VIN</label>
          <div style="display:flex;gap:8px">
            <input class="field-input" id="a-vin" placeholder="Vehicle identification number" style="flex:1">
            <button class="btn btn-glass" type="button" onclick="AssetsPage.genVin()">${icon('refresh',14)} Generate</button>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Number plate</label><input class="field-input" id="a-plate" placeholder="e.g. AB12 CDE"></div>
          <div class="field"><label class="field-label">Condition</label>
            <select class="field-select" id="a-cond"><option value="">—</option><option>New</option><option>Good</option><option>Fair</option><option>Damaged</option></select>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Brand</label><input class="field-input" id="a-brand" placeholder="e.g. Toyota"></div>
          <div class="field"><label class="field-label">Model</label><input class="field-input" id="a-model" placeholder="e.g. Corolla"></div>
        </div>
        <div class="field">
          <label class="field-label">IMEI / Serial number</label>
          <div style="display:flex;gap:8px">
            <input class="field-input" id="a-serial" placeholder="Enter serial, or generate one" style="flex:1">
            <button class="btn btn-glass" type="button" onclick="AssetsPage.genSerial()">${icon('refresh',14)} Generate</button>
          </div>
          <div style="font-size:11px;color:var(--tx-4);margin-top:5px">Leave blank and we'll generate a unique code if there's no VIN or serial.</div>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em;margin:6px 0 8px">Vehicle details</div>
        <div class="field-row">
          <div class="field"><label class="field-label">Year</label><input class="field-input" id="a-year" type="number" placeholder="e.g. 2022"></div>
          <div class="field"><label class="field-label">Tyre size</label><input class="field-input" id="a-tyre" placeholder="e.g. 205/55 R16"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Engine displacement</label><input class="field-input" id="a-engine-cc" placeholder="e.g. 2.0 L"></div>
          <div class="field"><label class="field-label">Engine power (HP)</label><input class="field-input" id="a-engine-hp" type="number" placeholder="e.g. 150"></div>
        </div>
        <div class="field"><label class="field-label">Description</label><textarea class="field-textarea" id="a-desc" placeholder="Notes about the asset…"></textarea></div>
        ${this._assetCF.length ? `<div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em;margin:6px 0 8px">Custom fields</div>${CustomFields.render(this._assetCF, {}, 'a-cf')}` : ''}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="AssetsPage.submit()">Create asset</button>
      </div>`, 'modal-side-wide');
    this._owner = 'client';
    if (opts.clientId) { const w = document.getElementById('a-client-wrap'); /* keep visible, preselected */ }
  },

  _newAssetImg: null, _newOpts: {}, _assetCF: [],

  _pickImg(input) {
    const f = input.files && input.files[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) return Toast.error('Image must be under 5 MB');
    const r = new FileReader();
    r.onload = () => {
      this._newAssetImg = r.result;
      const inner = document.getElementById('a-drop-inner');
      if (inner) inner.innerHTML = `<img src="${r.result}" alt="asset" style="max-height:120px;max-width:100%;border-radius:10px">
        <div style="font-size:11px;color:var(--tx-3);margin-top:6px">Click to replace</div>`;
    };
    r.readAsDataURL(f);
  },

  genVin() {
    const chars = 'ABCDEFGHJKLMNPRSTUVWXYZ0123456789';
    let v = ''; for (let i = 0; i < 17; i++) v += chars[Math.floor(Math.random() * chars.length)];
    const el = document.getElementById('a-vin'); if (el) el.value = v;
    Toast.success('VIN generated');
  },

  _owner: 'client',

  pickOwner(o, el) {
    this._owner = o;
    document.querySelectorAll('.type-chips .type-chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('a-client-wrap').style.display = o === 'client' ? '' : 'none';
  },

  async genSerial() {
    const type = document.getElementById('a-type')?.value || 'Device';
    try {
      const r = await get(`/api/assets/generate-serial?asset_type=${encodeURIComponent(type)}`);
      document.getElementById('a-serial').value = r.serial;
      Toast.success('Serial generated');
    } catch (e) { Toast.error(e.message); }
  },

  async submit() {
    const owner = this._owner;
    const client_id = document.getElementById('a-client')?.value || null;
    if (owner === 'client' && !client_id) return Toast.error('Select a client');
    const val = (id) => (document.getElementById(id)?.value || '').trim();
    // system extras + custom fields fold into custom_data
    const custom_data = CustomFields.collect(this._assetCF || [], 'a-cf') || {};
    const extras = { number_plate: val('a-plate'), year: val('a-year'), engine_displacement: val('a-engine-cc'),
      engine_power_hp: val('a-engine-hp'), tyre_size: val('a-tyre'), description: val('a-desc') };
    Object.entries(extras).forEach(([k, v]) => { if (v) custom_data[k] = v; });
    try {
      const created = await post('/api/assets', {
        owner_type: owner,
        client_id: owner === 'client' ? client_id : null,
        asset_type: val('a-type') || 'Vehicle',
        brand: val('a-brand') || null,
        model: val('a-model') || null,
        serial: val('a-serial') || null,
        vin: val('a-vin') || null,
        condition: val('a-cond') || null,
        image_data: this._newAssetImg || null,
        custom_data,
      });
      Toast.success('Asset created');
      Modal.close();
      const cb = this._newOpts && this._newOpts.onCreate;
      this._newOpts = {}; this._newAssetImg = null;
      if (typeof cb === 'function') { cb(created); return; }
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async viewAsset(id) {
    let a, clients = [];
    try { a = await get(`/api/assets/${id}`); } catch (e) { return Toast.error(e.message); }
    try { clients = (await get('/api/clients?limit=200')).items; } catch {}
    this._active = a; this._owner = a.owner_type || 'client';
    const bal = a.client_balance;
    const balHtml = (a.owner_type === 'client' && bal != null)
      ? `<div style="font-size:12px;margin-top:6px">Balance <b style="color:${bal>0?'var(--green)':bal<0?'var(--red)':'var(--tx-1)'}">${fmt.currency(bal)}</b>
         <span style="color:var(--tx-4)">${bal>0?'(client owes us)':bal<0?'(we owe client)':'(settled)'}</span></div>` : '';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${[a.brand,a.model].filter(Boolean).join(' ')||a.asset_type}${a.vin||a.serial?` — ${a.vin||a.serial}`:''}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="wo-tabs" style="padding:0 22px">
        <button class="wo-tab active" onclick="AssetsPage.aTab(event,'general')">General</button>
        <button class="wo-tab" onclick="AssetsPage.aTab(event,'orders')">Work orders (${a.history.length})</button>
        <button class="wo-tab" onclick="AssetsPage.aTab(event,'history')">History</button>
      </div>
      <div class="modal-body">
        <div class="a-panel active" data-apanel="general">
          <div class="field"><label class="field-label">Owner</label>
            <div class="type-chips">
              <div class="type-chip ${this._owner==='company'?'active':''}" data-owner="company" onclick="AssetsPage.editOwner('company',this)" style="--tc:#9b7bf4"><span class="type-chip-dot" style="background:#9b7bf4"></span>We (company)</div>
              <div class="type-chip ${this._owner==='client'?'active':''}" data-owner="client" onclick="AssetsPage.editOwner('client',this)" style="--tc:#4f8ef7"><span class="type-chip-dot" style="background:#4f8ef7"></span>Client</div>
            </div>
          </div>
          <div class="field" id="a-edit-client-wrap" style="${this._owner==='client'?'':'display:none'}">
            <label class="field-label">Client</label>
            <select class="field-select" id="a-edit-client">
              <option value="">Select client…</option>
              ${clients.map(c=>`<option value="${c.id}" ${a.client_id===c.id?'selected':''}>${c.name}${c.phone?' · '+c.phone:''}</option>`).join('')}
            </select>
            ${balHtml}
          </div>
          <div class="field-row">
            <div class="field"><label class="field-label">Type</label>
              <select class="field-select" id="a-edit-type">
                ${['Device','Phone','Laptop','Tablet','Vehicle','Car','Appliance','Tool','Other'].map(t=>`<option ${a.asset_type===t?'selected':''}>${t}</option>`).join('')}
              </select></div>
            <div class="field"><label class="field-label">Condition</label>
              <select class="field-select" id="a-edit-cond"><option value="">—</option>${['New','Good','Fair','Damaged'].map(x=>`<option ${a.condition===x?'selected':''}>${x}</option>`).join('')}</select></div>
          </div>
          <div class="field-row">
            <div class="field"><label class="field-label">Brand</label><input class="field-input" id="a-edit-brand" value="${a.brand||''}"></div>
            <div class="field"><label class="field-label">Model</label><input class="field-input" id="a-edit-model" value="${a.model||''}"></div>
          </div>
          <div class="field-row">
            <div class="field"><label class="field-label">Serial / IMEI</label><input class="field-input" id="a-edit-serial" value="${a.serial||a.imei||''}"></div>
            <div class="field"><label class="field-label">VIN</label><input class="field-input" id="a-edit-vin" value="${a.vin||''}"></div>
          </div>
          <div class="field"><label class="field-label">Colour</label><input class="field-input" id="a-edit-color" value="${a.color||''}"></div>
        </div>

        <div class="a-panel" data-apanel="orders" style="display:none">
          ${a.history.length ? `<div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Order #</th><th>Status</th><th>Total</th><th>Date</th></tr></thead>
            <tbody>${a.history.map(o=>`<tr style="cursor:pointer" onclick="Modal.close();Router.go('orders');setTimeout(()=>OrdersPage.viewOrder('${o.id}'),300)">
              <td><span class="tbl-num tbl-strong">${o.order_number}</span></td>
              <td>${badge(o.status)}</td><td>${fmt.currency(o.total)}</td>
              <td style="color:var(--tx-3);font-size:12px">${fmt.reltime(o.created_at)}</td>
            </tr>`).join('')}</tbody></table></div>`
            : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No work orders for this asset yet.</div>'}
        </div>

        <div class="a-panel" data-apanel="history" style="display:none">
          <div id="a-hist"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="AssetsPage.saveAsset('${id}')">Save</button>
      </div>`, 'modal-wide');
  },

  aTab(e, panel) {
    document.querySelectorAll('.wo-tab').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    document.querySelectorAll('.a-panel').forEach(p => p.style.display = p.dataset.apanel === panel ? 'block' : 'none');
    if (panel === 'history') this._loadAHistory();
  },

  editOwner(o, el) {
    this._owner = o;
    document.querySelectorAll('.a-panel[data-apanel="general"] .type-chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    document.getElementById('a-edit-client-wrap').style.display = o === 'client' ? '' : 'none';
  },

  async _loadAHistory() {
    const box = document.getElementById('a-hist'); if (!box || !this._active) return;
    try {
      const d = await get(`/api/assets/${this._active.id}/movements`);
      const evLabel = { zone:'Zone change', transfer:'Transfer', writeoff:'Write-off', owner:'Owner change', create:'Created' };
      box.innerHTML = (d.items && d.items.length) ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Event</th><th>From → To</th><th>Note</th><th>Date</th></tr></thead>
        <tbody>${d.items.map(m=>`<tr>
          <td class="tbl-strong">${evLabel[m.event_type]||m.event_type||'—'}</td>
          <td style="color:var(--tx-3)">${[m.from_zone,m.to_zone].filter(Boolean).join(' → ')||'—'}</td>
          <td style="color:var(--tx-3)">${m.note||'—'}</td>
          <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(m.moved_at||m.at)}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No movement history yet.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async saveAsset(id) {
    const owner = this._owner;
    const client_id = document.getElementById('a-edit-client')?.value || null;
    if (owner === 'client' && !client_id) return Toast.error('Select a client');
    const body = {
      owner_type: owner,
      client_id: owner === 'client' ? client_id : null,
      asset_type: document.getElementById('a-edit-type').value,
      condition: document.getElementById('a-edit-cond').value || null,
      brand: document.getElementById('a-edit-brand').value || null,
      model: document.getElementById('a-edit-model').value || null,
      serial: document.getElementById('a-edit-serial').value || null,
      vin: document.getElementById('a-edit-vin').value || null,
      color: document.getElementById('a-edit-color').value || null,
    };
    try {
      await put(`/api/assets/${id}`, body);
      Toast.success('Asset saved');
      Modal.close();
      this.reload();
    } catch (e) { Toast.error(e.message || 'Save failed'); }
  },
};

Router.register('assets', () => AssetsPage.load());
