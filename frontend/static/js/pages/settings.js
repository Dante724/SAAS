/* ════════════════════════════════════════════════════
   Settings hub — groups Staff, Statuses, Notifications,
   Forms editor under one section (Orderry-style Settings)
════════════════════════════════════════════════════ */

const SettingsPage = {
  _sub: 'general',

  SUBS: [
    { key: 'general',      label: 'General & company', icon: 'box',     render: () => GeneralPage.load() },
    { key: 'staff',         label: 'Employees & roles', icon: 'users',   render: () => StaffPage.load() },
    { key: 'locations',    label: 'Locations',          icon: 'box',     render: () => LocationsSettingsPage.load() },
    { key: 'statuses',      label: 'Work order statuses', icon: 'bolt',  render: () => StatusesPage.load() },
    { key: 'pricing',      label: 'Prices & discounts', icon: 'coin',  render: () => PricingSettingsPage.load() },
    { key: 'financeset',   label: 'Finance & numbering', icon: 'coin',  render: () => FinanceSettingsPage.load() },
    { key: 'forms',         label: 'Forms & types',     icon: 'grid',    render: () => FormsPage.load() },
    { key: 'checklists',    label: 'Checklists',         icon: 'check-circle', render: () => ChecklistTemplatesPage.load() },
    { key: 'invset',       label: 'Inventory & warehouses', icon: 'box', render: () => InventorySettingsPage.load() },
    { key: 'directories',  label: 'Directories',        icon: 'box',     render: () => DirectoriesPage.load() },
    { key: 'industry',     label: 'Industry template',  icon: 'grid',    render: () => IndustryTemplatePage.load() },
    { key: 'documents',     label: 'Document templates', icon: 'file',   render: () => DocumentsHubPage.load() },
    { key: 'feedback',      label: 'Customer feedback',  icon: 'message', render: () => FeedbackPage.load() },
    { key: 'publicpages',   label: 'Public pages',       icon: 'grid',    render: () => PublicPagesPage.load() },
    { key: 'notifications', label: 'Notifications',      icon: 'message', render: () => NotificationsPage.load() },
    { key: 'integrations',  label: 'Integrations',       icon: 'grid',    render: () => IntegrationsSettingsPage.load() },
    { key: 'api',           label: 'API keys',           icon: 'bolt',    render: () => ApiKeysSettingsPage.load() },
    { key: 'webhooks',      label: 'Webhooks',           icon: 'refresh', render: () => WebhooksSettingsPage.load() },
  ],

  // Build 144ea — group the 18 settings into scannable clusters (Orderry parity).
  GROUPS: [
    ['Company',  ['general', 'staff', 'locations']],
    ['Workflow', ['statuses', 'checklists', 'forms', 'industry']],
    ['Catalog',  ['pricing', 'financeset', 'invset', 'directories', 'documents']],
    ['Customer', ['feedback', 'publicpages', 'notifications']],
    ['Advanced', ['integrations', 'api', 'webhooks']],
  ],

  // Pure render of the grouped rail — unit-testable in isolation.
  _railHtml() {
    const byKey = Object.fromEntries(this.SUBS.map(s => [s.key, s]));
    return this.GROUPS.map(([label, keys]) => {
      const items = keys.map(k => byKey[k]).filter(Boolean);
      if (!items.length) return '';
      return `<div class="settings-rail-group">${label}</div>` + items.map(s =>
        `<button class="settings-rail-item ${s.key === this._sub ? 'active' : ''}" data-key="${s.key}" onclick="SettingsPage.go('${s.key}')">
          <span class="sri-icon">${icon(s.icon, 16)}</span>${s.label}
        </button>`).join('');
    }).join('');
  },

  async load(sub) {
    this._sub = sub || this._sub || 'general';
    setContent(`
      <div class="settings-shell">
        <aside class="settings-rail">${this._railHtml()}</aside>
        <section class="settings-pane">
          <div id="settings-content"></div>
        </section>
      </div>`);
    await this._mountSub();
  },

  async go(sub) {
    this._sub = sub;
    document.querySelectorAll('.settings-rail-item').forEach(el => el.classList.toggle('active', el.dataset.key === sub));
    await this._mountSub();
  },

  async _mountSub() {
    // Sub-pages (StaffPage, StatusesPage, etc.) call the global setContent /
    // setTopbarActions. While inside Settings we redirect those into the pane.
    const pane = document.getElementById('settings-content');
    if (!pane) return;
    if (!SettingsPage._shimActive) {
      SettingsPage._realSetContent = window.setContent;
      SettingsPage._realTopbar = window.setTopbarActions;
      window.setContent = (html) => {
        const p = document.getElementById('settings-content');
        if (p) { p.innerHTML = html; SettingsPage._renderPaneToolbar(); } else { SettingsPage._realSetContent(html); }
      };
      window.setTopbarActions = (html) => {
        if (document.getElementById('settings-content')) { SettingsPage._paneActions = html || ''; SettingsPage._renderPaneToolbar(); }
        else SettingsPage._realTopbar(html);
      };
      SettingsPage._shimActive = true;
    }
    SettingsPage._paneActions = '';
    const sub = this.SUBS.find(s => s.key === this._sub);
    await sub.render();
  },

  restore() {
    if (SettingsPage._shimActive) {
      window.setContent = SettingsPage._realSetContent;
      window.setTopbarActions = SettingsPage._realTopbar;
      SettingsPage._shimActive = false;
    }
  },

  // Build 144bx: settings sub-page actions render as a left-aligned toolbar row
  // at the top of the pane (Layout A), not a stranded right-side bar.
  _renderPaneToolbar() {
    const p = document.getElementById('settings-content');
    if (!p) return;
    const oldBar = document.getElementById('settings-pane-topbar');
    if (oldBar) oldBar.remove();
    const html = SettingsPage._paneActions || '';
    let bar = p.querySelector(':scope > .app-toolbar');
    if (!html) { if (bar) bar.remove(); return; }
    if (!bar) { bar = document.createElement('div'); bar.className = 'app-toolbar'; p.insertBefore(bar, p.firstChild); }
    bar.innerHTML = html;
    if (typeof App !== 'undefined' && App.applyPermissions) App.applyPermissions(bar);
  },
};

Router.register('settings', () => SettingsPage.load());

/* ── Settings > Locations (build 136) ── */
const DAYS = [['mon','Mon'],['tue','Tue'],['wed','Wed'],['thu','Thu'],['fri','Fri'],['sat','Sat'],['sun','Sun']];
const TZS = ['UTC','Asia/Kolkata','Asia/Dubai','Europe/London','America/New_York','America/Los_Angeles','Asia/Singapore','Australia/Sydney','Europe/Berlin','America/Chicago','Asia/Tokyo','Pacific/Auckland'];
const LocationsSettingsPage = {
  _items: [],
  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="LocationsSettingsPage.edit()">+ New location</button>`);
    setContent(`<div id="loc-wrap"><div class="card" style="padding:22px;color:var(--tx-4)">Loading…</div></div>`);
    await this.reload();
  },
  async reload() {
    try { this._items = (await get('/api/staff/locations')).items; } catch (e) { return Toast.error(e.message); }
    const box = document.getElementById('loc-wrap'); if (!box) return;
    box.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Name</th><th>Address</th><th>Timezone</th><th>Hours</th><th>Status</th></tr></thead>
      <tbody>${this._items.length ? this._items.map(l=>`<tr style="cursor:pointer" ondblclick="LocationsSettingsPage.edit('${l.id}')">
        <td style="font-weight:800">${l.name}</td>
        <td style="color:var(--tx-3);font-size:12.5px">${(l.address||'—').split('\\n')[0]}</td>
        <td style="color:var(--tx-3);font-size:12px">${l.timezone||'UTC'}</td>
        <td style="color:var(--tx-3);font-size:12px">${this._hoursSummary(l.working_hours)}</td>
        <td>${l.active?'<span class="badge b-done">active</span>':'<span class="badge b-muted">archived</span>'}</td>
      </tr>`).join('') : emptyRow(5,'box','No locations yet','Add your first branch')}</tbody>
    </table></div></div>`;
  },
  _hoursSummary(wh) {
    if (!wh) return '<span style="color:var(--tx-4)">—</span>';
    const open = DAYS.filter(([k])=>wh[k] && !wh[k].closed).length;
    return open ? `${open} day(s) open` : 'closed';
  },
  edit(id) {
    const l = id ? this._items.find(x=>x.id===id) : null;
    const wh = (l && l.working_hours) || {};
    const dayRow = ([k,lbl]) => {
      const d = wh[k] || {};
      return `<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <span style="width:38px;font-weight:700;font-size:12.5px">${lbl}</span>
        <label class="cash-check" style="width:80px"><input type="checkbox" id="wh-${k}-closed" ${d.closed?'checked':''} onchange="LocationsSettingsPage._toggleDay('${k}')"> Closed</label>
        <input class="field-input" type="time" id="wh-${k}-open" value="${d.open||'09:00'}" style="max-width:120px" ${d.closed?'disabled':''}>
        <span style="color:var(--tx-4)">–</span>
        <input class="field-input" type="time" id="wh-${k}-close" value="${d.close||'18:00'}" style="max-width:120px" ${d.closed?'disabled':''}>
      </div>`;
    };
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${l?'Edit location':'New location'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Name *</label><input class="field-input" id="loc-name" value="${(l?.name||'').replace(/"/g,'&quot;')}" placeholder="Central Station"></div>
          <div class="field"><label class="field-label">Phone</label><input class="field-input" id="loc-phone" value="${l?.phone||''}"></div>
        </div>
        <div class="field"><label class="field-label">Address</label><textarea class="field-textarea" id="loc-address">${l?.address||''}</textarea></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Timezone</label>
            <select class="field-select" id="loc-tz">${TZS.map(t=>`<option ${((l?.timezone)||'UTC')===t?'selected':''}>${t}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Receipt header</label><input class="field-input" id="loc-receipt" value="${(l?.receipt_header||'').replace(/"/g,'&quot;')}"></div>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 8px">Working hours</div>
        <div class="set-section" style="padding:12px 14px">${DAYS.map(dayRow).join('')}</div>

        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:18px 0 8px">Location resources</div>
        <div style="font-size:12px;color:var(--tx-4);margin-bottom:8px">Schedulable workplaces at this branch — bays, lifts, rooms, equipment. They show up in the scheduler's <b>By resource</b> mode.</div>
        ${l ? `
        <div class="set-section" id="loc-res-list" style="padding:12px 14px"><span style="color:var(--tx-4)">Loading…</span></div>
        <div style="display:flex;gap:8px;margin-top:8px;align-items:flex-end;flex-wrap:wrap">
          <div class="field" style="flex:1;min-width:150px;margin:0"><label class="field-label">Name</label><input class="field-input" id="loc-res-name" placeholder="Car lift 1, Room A" onkeydown="if(event.key==='Enter'){event.preventDefault();LocationsSettingsPage.addResource('${l.id}')}"></div>
          <div class="field" style="margin:0"><label class="field-label">Kind</label>
            <select class="field-select" id="loc-res-kind">${[['bay','Bay / post'],['room','Room'],['equipment','Equipment'],['other','Other']].map(([k,lb])=>`<option value="${k}">${lb}</option>`).join('')}</select></div>
          <button class="btn btn-glass btn-sm" onclick="LocationsSettingsPage.addResource('${l.id}')">+ Add resource</button>
        </div>`
        : `<div style="font-size:11.5px;color:var(--tx-4)">Save this location first, then reopen it to add resources.</div>`}

        <div style="font-size:11.5px;color:var(--tx-4);margin-top:16px">Document numbering (work order / invoice) can be given a per-location sequence in <b>Finance & numbering</b>. Employee access to a location is controlled by their <b>role's location scope</b> (Employees & roles).</div>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:8px">
          <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
          <button class="btn btn-primary" onclick="LocationsSettingsPage.save(${l?`'${l.id}'`:'null'})">${l?'Save':'Create location'}</button>
        </div>
        ${l&&l.active?`<button class="btn btn-danger btn-sm" onclick="LocationsSettingsPage.deactivate('${l.id}')">Archive</button>`:''}
      </div>`, 'modal-wide');
    if (l) this._loadResources(l.id);
  },

  /* Build 144d: per-location resources, created/deleted right here in the
     location panel (the same list the standalone "Location resources" page
     shows, just filtered to this branch — no separate data). */
  _resCache: [],
  async _loadResources(locId) {
    const box = document.getElementById('loc-res-list');
    if (!box) return;
    try {
      this._resCache = ((await get('/api/resources')).items || []).filter(r => r.location_id === locId);
    } catch (e) { box.innerHTML = `<span style="color:var(--tx-4)">Could not load resources</span>`; return; }
    if (!this._resCache.length) { box.innerHTML = `<span style="color:var(--tx-4)">No resources yet at this location.</span>`; return; }
    box.innerHTML = this._resCache.map(r => `<div style="display:flex;align-items:center;gap:10px;padding:5px 0">
      <span style="display:inline-block;width:14px;height:14px;border-radius:4px;background:${r.color};flex:none"></span>
      <span style="font-weight:700;font-size:13px">${(r.name||'').replace(/</g,'&lt;')}</span>
      <span class="badge" style="text-transform:capitalize">${r.kind}</span>
      <button class="btn btn-glass btn-sm" style="margin-left:auto" onclick="LocationsSettingsPage.delResource('${r.id}','${locId}')">Remove</button>
    </div>`).join('');
  },
  async addResource(locId) {
    const nameEl = document.getElementById('loc-res-name');
    const name = (nameEl.value || '').trim();
    if (!name) return Toast.error('Resource name is required');
    const kind = document.getElementById('loc-res-kind').value;
    try {
      await post('/api/resources', { name, kind, color: '#2cd4c0', location_id: locId });
      nameEl.value = '';
      Toast.success('Resource added');
      await this._loadResources(locId);
    } catch (e) { Toast.error(e.message); }
  },
  async delResource(id, locId) {
    if (!await confirm2('Remove this resource?')) return;
    try { await del(`/api/resources/${id}`); Toast.success('Removed'); await this._loadResources(locId); }
    catch (e) { Toast.error(e.message); }
  },
  _toggleDay(k) {
    const closed = document.getElementById(`wh-${k}-closed`).checked;
    document.getElementById(`wh-${k}-open`).disabled = closed;
    document.getElementById(`wh-${k}-close`).disabled = closed;
  },
  _collectHours() {
    const wh = {};
    DAYS.forEach(([k]) => {
      const closed = document.getElementById(`wh-${k}-closed`).checked;
      wh[k] = closed ? { closed: true } : { open: document.getElementById(`wh-${k}-open`).value, close: document.getElementById(`wh-${k}-close`).value };
    });
    return wh;
  },
  async save(id) {
    const name = document.getElementById('loc-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const body = {
      name, phone: document.getElementById('loc-phone').value||null,
      address: document.getElementById('loc-address').value||null,
      timezone: document.getElementById('loc-tz').value,
      receipt_header: document.getElementById('loc-receipt').value||null,
      working_hours: this._collectHours(),
    };
    try {
      if (id) await put(`/api/staff/locations/${id}`, body); else await post('/api/staff/locations', body);
      Toast.success(id?'Location saved':'Location created'); Modal.close(); this.reload();
    } catch (e) { Toast.error(e.message||'Save failed'); }
  },
  async deactivate(id) {
    if (!await confirm2('Archive this location? It will be hidden from pickers but its history is kept.')) return;
    try { await del(`/api/staff/locations/${id}`); Toast.success('Location archived'); this.reload(); }
    catch (e) { Toast.error(e.message||'Could not archive'); }
  },
};

/* ── Settings > Inventory (build 133) ── */
const InventorySettingsPage = {
  async load() {
    setTopbarActions('');
    setContent(`<div id="invset-wrap"><div class="card" style="padding:24px;color:var(--tx-4)">Loading…</div></div>`);
    let c;
    try { c = await get('/api/staff/inventory-settings'); } catch (e) { return Toast.error(e.message); }
    const sel = (id, opts, val) => `<select class="field-select" id="${id}">${opts.map(([k,l])=>`<option value="${k}" ${val===k?'selected':''}>${l}</option>`).join('')}</select>`;
    document.getElementById('invset-wrap').innerHTML = `
      <div class="card" style="padding:24px;max-width:760px;margin-bottom:18px">
        <div class="dp-title" style="margin-bottom:4px">Inventory settings</div>
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:18px">Control how products are identified, numbered, reserved, and stored.</div>

        <div class="set-section" style="margin:0 0 16px">
          <div class="ss-head">${icon('box',16)} Stock</div>
          <label class="cash-check" style="margin-top:6px"><input type="checkbox" id="iv-bins" ${c.enable_bins?'checked':''}> Enable bin locations (shelf/bin per warehouse)</label>
          <label class="cash-check"><input type="checkbox" id="iv-resv" ${c.enable_reservations?'checked':''}> Enable product reservations</label>
          <div class="field" style="max-width:220px;margin-top:10px"><label class="field-label">Maximum reservation period (days)</label>
            <input class="field-input" id="iv-resvmax" type="number" min="0" value="${c.reservation_max_days ?? 14}"></div>
        </div>

        <div class="set-section" style="margin:0 0 16px">
          <div class="ss-head">${icon('grid',16)} Products</div>
          <div class="field-row">
            <div class="field"><label class="field-label">Unique product identifier</label>
              ${sel('iv-uid',[['name','Name'],['sku','SKU'],['barcode','Barcode'],['code','Code']],c.unique_identifier)}</div>
            <div class="field"><label class="field-label">Default product type</label>
              <input class="field-input" id="iv-ptype" value="${c.default_product_type||'default'}"></div>
          </div>
          <div class="field"><label class="field-label">Product serial-number template <span style="font-weight:600;color:var(--tx-4)">— e.g. 0-{C:19}</span></label>
            <input class="field-input" id="iv-pserial" value="${c.product_serial_template||''}" placeholder="0-{C:19}"></div>
        </div>

        <div class="set-section" style="margin:0 0 16px">
          <div class="ss-head">${icon('box',16)} Vehicles / assets</div>
          <div class="field-row">
            <div class="field"><label class="field-label">Default vehicle type</label>
              <input class="field-input" id="iv-vtype" value="${c.default_vehicle_type||'Car'}"></div>
            <div class="field"><label class="field-label">Asset serial-number template <span style="font-weight:600;color:var(--tx-4)">— e.g. 9-{C:4}</span></label>
              <input class="field-input" id="iv-aserial" value="${c.asset_serial_template||''}" placeholder="9-{C:4}"></div>
          </div>
        </div>

        <div style="display:flex;justify-content:flex-end"><button class="btn btn-primary" onclick="InventorySettingsPage.save()">Save inventory settings</button></div>
      </div>

      <div class="card" style="padding:24px">
        <div class="dp-title" style="margin-bottom:4px">Warehouses</div>
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:14px">Create warehouses and see all product and asset warehouses — company-wide or per location.</div>
        <div id="invset-wh"><div style="color:var(--tx-4)">Loading…</div></div>
      </div>`;
    // embed the full warehouse workspace (product / asset / deleted + create)
    try { await WarehousesPage.mountInto('invset-wh'); } catch (e) { /* non-fatal */ }
  },
  async save() {
    const body = {
      enable_bins: document.getElementById('iv-bins').checked,
      enable_reservations: document.getElementById('iv-resv').checked,
      reservation_max_days: parseInt(document.getElementById('iv-resvmax').value, 10) || 0,
      unique_identifier: document.getElementById('iv-uid').value,
      default_product_type: document.getElementById('iv-ptype').value || 'default',
      product_serial_template: document.getElementById('iv-pserial').value || null,
      default_vehicle_type: document.getElementById('iv-vtype').value || 'Car',
      asset_serial_template: document.getElementById('iv-aserial').value || null,
    };
    try { await put('/api/staff/inventory-settings', body); Toast.success('Inventory settings saved'); }
    catch (e) { Toast.error(e.message); }
  },
};
