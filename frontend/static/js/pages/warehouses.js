/* Build 84/89/90 — Settings → Warehouses.

   Two kinds live here, and they behave differently on purpose:
     · Product warehouses hold shop inventory. You create these.
     · Asset warehouses hold the items being serviced. Most are created and moved
       automatically (each client and technician gets one; jobs push assets through
       "Pending repair" and "Ready for pickup"), so they're shown read-only.
   The word used for assets comes from the industry template — Devices, Vehicles,
   Tools — so this page adapts to the business. */
const WarehousesPage = {
  _tab: 'product',
  _items: [],
  _actions: [],
  _assetLabel: 'Assets',
  _locations: [],
  // Build 144f: when hosted inside Settings → Inventory, render into this
  // element (with the "+ New warehouse" button inline) instead of taking over
  // the page — so warehouses live under Inventory, not as a separate section.
  _embedBody: null,
  async mountInto(bodyId) { this._embedBody = bodyId; await this.load(); },

  async load() {
    const newBtn = `<button class="btn btn-primary btn-sm" onclick="WarehousesPage.edit()">+ New warehouse</button>`;
    const tabs = `
      <div class="tabs" style="margin:0">
        <button class="tab ${this._tab === 'product' ? 'active' : ''}" onclick="WarehousesPage.go('product')">Product warehouses</button>
        <button class="tab ${this._tab === 'asset' ? 'active' : ''}" onclick="WarehousesPage.go('asset')" id="wh-asset-tab">Asset warehouses</button>
        <button class="tab ${this._tab === 'deleted' ? 'active' : ''}" onclick="WarehousesPage.go('deleted')">Deleted</button>
      </div>`;
    const bodyHtml = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
        ${tabs}${newBtn}
      </div>
      <div id="wh-body"><div class="card" style="padding:18px;color:var(--tx-4)">Loading…</div></div>`;
    if (this._embedBody) {
      const el = document.getElementById(this._embedBody);
      if (el) { el.innerHTML = bodyHtml; }
      else { this._embedBody = null; setTopbarActions(''); setContent(bodyHtml); }
    } else {
      setTopbarActions('');
      setContent(bodyHtml);
    }
    await this.reload();
  },

  async go(tab) { this._tab = tab; await this.load(); },

  async reload() {
    try {
      const qs = this._tab === 'deleted' ? '?include_deleted=true' : `?kind=${this._tab}`;
      const r = await get(`/api/settings/warehouses${qs}`);
      this._items = r.items || [];
      this._actions = r.actions || [];
      this._assetLabel = r.asset_label || 'Assets';
      this._locations = (await get('/api/staff/locations')).items || [];
    } catch (e) { return Toast.error(e.message); }

    const tab = document.getElementById('wh-asset-tab');
    if (tab) tab.textContent = `${this._assetLabel} warehouses`;

    const rows = this._tab === 'deleted'
      ? this._items.filter(w => !w.active)
      : this._items;
    const body = document.getElementById('wh-body');
    if (!body) return;

    if (this._tab === 'asset') {
      body.innerHTML = `
        <div style="font-size:13px;color:var(--tx-3);margin-bottom:12px;max-width:760px">
          ${this._assetLabel} move between these automatically as a job progresses:
          check-in sends the item to <b>Pending repair</b>, assigning it sends it to the
          technician, marking it ready sends it to <b>Ready for pickup</b>, and closing the
          order returns it to the client. Each client and technician has their own.
        </div>
        ${this._table(rows, true)}`;
      return;
    }
    body.innerHTML = this._table(rows, false);
  },

  _table(rows, readonly) {
    const locName = (id) => (this._locations.find(l => l.id === id) || {}).name || '—';
    return `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Name</th><th>Reach</th><th>Location</th><th>Allowed actions</th>
        <th style="text-align:right">Balance</th><th></th></tr></thead>
      <tbody>${rows.map(w => `<tr>
        <td class="tbl-strong">${w.name}
          ${w.is_default ? '<span class="badge b-new" style="margin-left:6px">default</span>' : ''}
          ${w.system_key ? '<span class="badge" style="margin-left:6px">system</span>' : ''}</td>
        <td>${w.scope === 'company'
          ? '<span class="badge b-done">Company-wide</span>'
          : '<span class="badge">Location only</span>'}</td>
        <td style="color:var(--tx-3)">${w.scope === 'company' ? 'Any' : locName(w.location_id)}</td>
        <td style="font-size:12px;color:var(--tx-3)">${(w.allowed_actions || []).join(', ') || '—'}</td>
        <td style="text-align:right">${w.balance ?? 0}</td>
        <td style="text-align:right;white-space:nowrap">
          ${!w.active
            ? `<button class="btn btn-glass btn-sm" onclick="WarehousesPage.restore('${w.id}')">Restore</button>`
            : readonly ? '<span style="color:var(--tx-4);font-size:12px">automatic</span>'
            : `<button class="btn btn-glass btn-sm" onclick="WarehousesPage.access('${w.id}')">Access</button>
               <button class="btn btn-glass btn-sm" onclick="WarehousesPage.edit('${w.id}')">Edit</button>
               ${w.is_default ? '' :
                 `<button class="btn btn-glass btn-sm" onclick="WarehousesPage.remove('${w.id}')">Delete</button>`}`}
        </td>
      </tr>`).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:24px">
        No warehouses here yet</td></tr>`}
      </tbody></table></div></div>`;
  },

  edit(id = null) {
    const w = id ? this._items.find(x => x.id === id) : null;
    const acts = this._actions.length ? this._actions
      : ['post', 'writeoff', 'transfer', 'sell', 'stocktake'];
    const on = (a) => !w || (w.allowed_actions || []).includes(a);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${w ? 'Edit' : 'New'} warehouse</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name</label>
          <input class="field-input" id="wh-name" value="${w?.name || ''}"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Type</label>
            <select class="field-select" id="wh-kind" ${w ? 'disabled' : ''}>
              <option value="product" ${w?.kind !== 'asset' ? 'selected' : ''}>Product (inventory)</option>
              <option value="asset" ${w?.kind === 'asset' ? 'selected' : ''}>${this._assetLabel}</option>
            </select></div>
          <div class="field"><label class="field-label">Reach</label>
            <select class="field-select" id="wh-scope" onchange="WarehousesPage._scopeToggle()"
                    ${w?.is_default ? 'disabled' : ''}>
              <option value="location" ${w?.scope !== 'company' ? 'selected' : ''}>This location only</option>
              <option value="company" ${w?.scope === 'company' ? 'selected' : ''}>Any location (company-wide)</option>
            </select></div>
        </div>
        <div class="field" id="wh-loc-wrap" style="${w?.scope === 'company' ? 'display:none' : ''}">
          <label class="field-label">Location</label>
          <select class="field-select" id="wh-loc">
            ${this._locations.map(l => `<option value="${l.id}" ${w?.location_id === l.id ? 'selected' : ''}>${l.name}</option>`).join('')}
          </select>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 6px">Allowed actions</div>
        <p style="color:var(--tx-4);font-size:12px;margin:0 0 8px">What this warehouse permits at all. An employee still needs access on top of this.</p>
        ${acts.map(a => `<label class="cash-check" style="margin-bottom:6px">
          <input type="checkbox" data-act="${a}" ${on(a) ? 'checked' : ''}> ${a}</label>`).join('')}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="WarehousesPage.save(${id ? `'${id}'` : 'null'})">Save</button>
      </div>`);
  },

  _scopeToggle() {
    const company = document.getElementById('wh-scope').value === 'company';
    document.getElementById('wh-loc-wrap').style.display = company ? 'none' : '';
  },

  async save(id) {
    const name = document.getElementById('wh-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const scope = document.getElementById('wh-scope').value;
    const actions = [...document.querySelectorAll('[data-act]')]
      .filter(cb => cb.checked).map(cb => cb.dataset.act);
    const body = {
      name,
      kind: document.getElementById('wh-kind').value,
      scope,
      location_id: scope === 'location' ? document.getElementById('wh-loc').value : null,
      allowed_actions: actions,
      active: true,
    };
    try {
      if (id) await put(`/api/settings/warehouses/${id}`, body);
      else await post('/api/settings/warehouses', body);
      Modal.close();
      Toast.success(id ? 'Warehouse updated' : 'Warehouse created');
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Save failed'); }
  },

  async remove(id) {
    const w = this._items.find(x => x.id === id);
    if (!await confirm2(`Delete “${w?.name}”? It can be restored later.`)) return;
    try {
      await del(`/api/settings/warehouses/${id}`);
      Toast.success('Warehouse deleted — restorable from the Deleted tab');
      await this.reload();
    } catch (e) {
      // the API refuses while stock is still inside, and says how much
      Toast.error(e.message || 'Delete failed');
    }
  },

  async restore(id) {
    try {
      await post(`/api/settings/warehouses/${id}/restore`, {});
      Toast.success('Warehouse restored');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── Build 89: per-employee access ── */
  async access(id) {
    const w = this._items.find(x => x.id === id);
    let data;
    try { data = await get(`/api/settings/warehouses/${id}/access`); }
    catch (e) { return Toast.error(e.message); }
    const acts = w?.allowed_actions || this._actions;

    Modal.open(`
      <div class="modal-header"><span class="modal-title">Access — ${w?.name}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Tick an employee to set their rights <em>here specifically</em>. Left unticked,
          they fall back to whatever their role allows.
        </p>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Employee</th><th>Explicit</th>${acts.map(a => `<th>${a}</th>`).join('')}</tr></thead>
          <tbody>${data.items.map(e => `<tr>
            <td class="tbl-strong">${e.name}</td>
            <td><input type="checkbox" data-exp="${e.employee_id}" ${e.explicit ? 'checked' : ''}></td>
            ${acts.map(a => `<td><input type="checkbox" data-emp="${e.employee_id}" data-a="${a}"
              ${e.actions.includes(a) ? 'checked' : ''}></td>`).join('')}
          </tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="WarehousesPage.saveAccess('${id}')">Save access</button>
      </div>`, 'modal-wide');
  },

  async saveAccess(id) {
    const rows = [...document.querySelectorAll('[data-exp]')];
    try {
      for (const cb of rows) {
        const emp = cb.dataset.exp;
        const actions = [...document.querySelectorAll(`[data-emp="${emp}"]`)]
          .filter(x => x.checked).map(x => x.dataset.a);
        await post(`/api/settings/warehouses/${id}/access`, {
          employee_id: emp, actions, explicit: cb.checked,
        });
      }
      Modal.close();
      Toast.success('Access updated');
    } catch (e) { Toast.error(e.message || 'Could not save access'); }
  },
};
