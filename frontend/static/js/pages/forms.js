/* ════════════════════════════════════════════════════
   Settings → Forms Editor
   Manage work-order types and their custom fields.
════════════════════════════════════════════════════ */

const FormsPage = {
  types: [],
  activeType: null,

  activeTab: 'order',

  // 144da — Forms Editor, Orderry-style: entity TABS across the top, each showing
  // that entity's System fields (toggle Show / Required) + its Custom fields.
  _TABS: [['order', 'Work orders'], ['invoice', 'Invoices'], ['contact', 'Contacts'], ['product', 'Products'], ['vehicle', 'Vehicles']],

  // System-field catalogs per entity. Labels only — the backend stores show/required
  // by key. These mirror Orderry's built-in fields for each form.
  _SYS: {
    order: [['client', 'Client'], ['vehicle', 'Vehicle / asset'], ['manager', 'Manager'], ['assignee', 'Specialist (assignee)'], ['type', 'Type'], ['scheduled_for', 'Scheduled for'], ['due_at', 'Due date'], ['priority', 'Priority (urgent)'], ['complaint', 'Malfunction / complaint'], ['tech_notes', 'Manager notes'], ['payer', 'Payer'], ['estimated_price', 'Estimated price'], ['comment', 'Comment']],
    invoice: [['client', 'Client'], ['payer', 'Payer'], ['issued_at', 'Issue date'], ['due_at', 'Due date'], ['discount', 'Discount'], ['tax', 'Tax'], ['comment', 'Comment']],
    contact: [['name', 'Name'], ['phone', 'Phone'], ['email', 'Email'], ['address', 'Address'], ['company', 'Company'], ['manager', 'Manager'], ['contact_type', 'Type (person / company)'], ['discount', 'Discount'], ['comment', 'Comment']],
    product: [['name', 'Name'], ['sku', 'SKU'], ['barcode', 'Barcode'], ['category', 'Category'], ['brand', 'Brand'], ['cost', 'Cost'], ['price', 'Price'], ['unit', 'Unit'], ['warranty', 'Warranty'], ['description', 'Description']],
    vehicle: [['owner', 'Owner (we / client)'], ['client', 'Client'], ['type', 'Type'], ['vin', 'VIN'], ['number_plate', 'Number plate'], ['group', 'Group'], ['brand', 'Brand'], ['model', 'Model'], ['year', 'Year'], ['engine', 'Engine'], ['tyre_size', 'Tyre size'], ['color', 'Colour'], ['condition', 'Condition'], ['serial', 'Serial / IMEI'], ['description', 'Description']],
  },

  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="FormsPage.newType()">+ New type</button>`);
    setContent(`
      <div class="forms-tabbar" id="forms-tabbar"></div>
      <div id="forms-typebar"></div>
      <div id="forms-sys"></div>
      <div class="forms-main" id="forms-fields"></div>`);
    try { this.types = (await get('/api/forms/types')).items; } catch { this.types = []; }
    this._drawTabs();
    this.selectTab(this.activeTab || 'order');
  },

  async reloadTypes() {
    try { this.types = (await get('/api/forms/types')).items; } catch { this.types = []; }
    this.selectTab('order');
  },

  _entity() { return (this.activeType || '').startsWith('entity:') ? this.activeType.slice(7) : null; },

  _drawTabs() {
    document.getElementById('forms-tabbar').innerHTML = this._TABS.map(([k, l]) =>
      `<button class="forms-tab ${this.activeTab === k ? 'on' : ''}" onclick="FormsPage.selectTab('${k}')">${l}</button>`).join('');
  },

  _emptyMain() {
    return `<div class="card"><div class="empty"><div class="empty-blob">${icon('list',30)}</div>
      <div class="empty-title">No work-order type yet</div>
      <div class="empty-sub">Create a type to configure its custom fields</div></div></div>`;
  },

  async selectTab(key) {
    this.activeTab = key;
    this._drawTabs();
    const tb = document.getElementById('forms-typebar');
    if (key === 'order') {
      tb.innerHTML = `<div class="forms-typechips">${this.types.map(t =>
        `<button class="forms-typechip ${this.activeType === t.id ? 'on' : ''}" onclick="FormsPage.pickOrderType('${t.id}')"><span class="dot" style="background:${t.color}"></span>${t.name}${t.is_default ? ' <span class="forms-default-tag">default</span>' : ''}</button>`).join('')}
        <button class="btn btn-glass btn-sm" onclick="FormsPage.newType()">${icon('plus',13)} Type</button></div>`;
      if (!this.types.find(t => t.id === this.activeType)) this.activeType = this.types[0] ? this.types[0].id : null;
      await this._renderSys('order', this.activeType);
      if (this.activeType) this.loadFields(this.activeType);
      else document.getElementById('forms-fields').innerHTML = this._emptyMain();
    } else {
      tb.innerHTML = '';
      const entMap = { invoice: 'invoice', contact: 'client', product: 'product', vehicle: 'device' };
      const ent = entMap[key];
      this.activeType = 'entity:' + ent;
      await this._renderSys(key, '');
      this.loadEntityFields(ent);
    }
  },

  pickOrderType(id) { this.activeType = id; this.selectTab('order'); },

  // System-fields section — catalog rows with Show / Required toggles, persisted.
  async _renderSys(entity, typeId) {
    const host = document.getElementById('forms-sys');
    const cat = this._SYS[entity] || [];
    let saved = [];
    try { saved = (await get(`/api/forms/system-layout?entity=${entity}${typeId ? '&type_id=' + typeId : ''}`)).fields || []; } catch {}
    const byKey = {}; saved.forEach(s => { byKey[s.key] = s; });
    this._sysState = cat.map(([key, label]) => { const s = byKey[key] || {}; return { key, label, visible: s.visible !== false, required: !!s.required }; });
    this._sysEntity = entity; this._sysType = typeId || '';
    host.innerHTML = `
      <div class="card" style="margin-bottom:14px">
        <div class="section-hd" style="padding:14px 18px;border-bottom:1px solid var(--g-border)">
          <div><div class="section-title">System fields</div>
          <div class="section-sub">Built-in fields for this form. Toggle what shows and what's required.</div></div>
        </div>
        <div style="padding:6px 10px">
          ${this._sysState.map((f, i) => `
            <div class="forms-sys-row">
              <span class="forms-sys-name">${f.label}</span>
              <label class="forms-sys-tog"><input type="checkbox" ${f.visible ? 'checked' : ''} onchange="FormsPage._sysToggle(${i},'visible',this.checked)"> Show</label>
              <label class="forms-sys-tog"><input type="checkbox" ${f.required ? 'checked' : ''} onchange="FormsPage._sysToggle(${i},'required',this.checked)"> Required</label>
            </div>`).join('')}
        </div>
      </div>`;
  },

  _sysToggle(i, k, v) { if (!this._sysState[i]) return; this._sysState[i][k] = v; this._saveSys(); },

  async _saveSys() {
    try {
      await put('/api/forms/system-layout', {
        entity: this._sysEntity, type_id: this._sysType || null,
        fields: this._sysState.map((f, idx) => ({ key: f.key, visible: f.visible, required: f.required, sort: idx })),
      });
    } catch (e) { Toast.error(e.message); }
  },

  // The "Work order" entity surfaces the two built-in scheduling fields
  // (Order.scheduled_for / scheduled_to). They're first-class columns, not custom
  // fields, so this view is read-only — no backend call, nothing to add or delete.
  loadScheduleFields() {
    const el = document.getElementById('forms-fields');
    const row = (label, sub) => `
      <div class="forms-field" style="cursor:default">
        <span class="forms-field-icon" style="color:var(--blue)">${icon('calendar',15)}</span>
        <div class="forms-field-info">
          <div class="forms-field-label">${label} <span class="forms-req" style="background:var(--g-border,#e3e6ee);color:var(--tx-3)">built-in</span></div>
          <div class="forms-field-meta">Date &amp; time · ${sub}</div>
        </div>
      </div>`;
    el.innerHTML = `
      <div class="card">
        <div class="section-hd" style="padding:16px 20px;border-bottom:1px solid var(--g-border)">
          <div style="display:flex;align-items:center;gap:10px">
            <span class="forms-type-dot" style="width:14px;height:14px;background:linear-gradient(135deg,var(--blue),var(--purple))"></span>
            <div><div class="section-title">Work order scheduling</div>
            <div class="section-sub">Built-in fields every work order carries. Used on the create modal, the edit view, the scheduler, and when duplicating a work order.</div></div>
          </div>
        </div>
        <div id="forms-field-list" style="padding:14px 16px">
          ${row('Schedule from', 'when the job starts (booked onto the calendar)')}
          ${row('Schedule to', 'when the job is expected to finish')}
        </div>
      </div>`;
  },

  async loadEntityFields(entity) {
    const el = document.getElementById('forms-fields');
    el.innerHTML = `<div class="card"><div class="skeleton" style="height:240px"></div></div>`;
    let fields = [];
    try { fields = (await get(`/api/forms/entity-fields?entity=${entity}`)).items; }
    catch (e) { Toast.error(e.message); }
    const typeIcons = { text:'list', textarea:'message', number:'chart', select:'grid', date:'calendar', datetime:'calendar', address:'box', bool:'check-circle', header:'folder' };
    const label = entity === 'client' ? 'Client' : entity === 'product' ? 'Product' : 'Asset';
    const sub = entity === 'client'
      ? 'Shown on the client form (add & edit).'
      : entity === 'product'
      ? 'Shown on the product form (add & edit). The built-in fields stay as-is; these appear below them.'
      : 'Shown on the asset form and the work-order create modal.';
    el.innerHTML = `
      <div class="card">
        <div class="section-hd" style="padding:16px 20px;border-bottom:1px solid var(--g-border)">
          <div style="display:flex;align-items:center;gap:10px">
            <span class="forms-type-dot" style="width:14px;height:14px;background:linear-gradient(135deg,var(--blue),var(--purple))"></span>
            <div><div class="section-title">${label} fields</div>
            <div class="section-sub">${sub}</div></div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="FormsPage.addField()">+ Add field</button>
        </div>
        <div id="forms-field-list" style="padding:14px 16px">
          ${fields.length ? fields.map(f => this._fieldRow(f, typeIcons)).join('') :
            `<div style="text-align:center;padding:30px;color:var(--tx-4);font-size:13px">No custom ${label.toLowerCase()} fields yet. Add the first one.</div>`}
        </div>
      </div>`;
    this._fields = fields;
  },

  async _reloadFields() {
    const ent = this._entity();
    if (ent === 'order') return this.loadScheduleFields();
    if (ent) return this.loadEntityFields(ent);
    await this.loadFields(this.activeType); await this.reloadTypes();
  },

  async loadFields(typeId) {
    const el = document.getElementById('forms-fields');
    const t = this.types.find(x => x.id === typeId);
    el.innerHTML = `<div class="card"><div class="skeleton" style="height:240px"></div></div>`;
    let fields = [];
    try { fields = (await get(`/api/forms/types/${typeId}/fields`)).items; }
    catch (e) { Toast.error(e.message); }

    const typeIcons = {
      text:'list', textarea:'message', number:'chart', select:'grid',
      date:'calendar', datetime:'calendar', address:'box', bool:'check-circle', header:'folder',
    };

    el.innerHTML = `
      <div class="card">
        <div class="section-hd" style="padding:16px 20px;border-bottom:1px solid rgba(255,255,255,.6)">
          <div style="display:flex;align-items:center;gap:10px">
            <span class="forms-type-dot" style="background:${t?.color||'#4f8ef7'};width:14px;height:14px"></span>
            <div><div class="section-title">${t?.name||'Type'} form</div>
            <div class="section-sub">Fields shown when creating this work order type</div></div>
          </div>
          <button class="btn btn-primary btn-sm" onclick="FormsPage.addField()">+ Add field</button>
        </div>
        <div id="forms-field-list" style="padding:14px 16px">
          ${fields.length ? fields.map(f => this._fieldRow(f, typeIcons)).join('') :
            `<div style="text-align:center;padding:30px;color:var(--tx-4);font-size:13px">No fields yet. Add the first one.</div>`}
        </div>
      </div>`;
    this._fields = fields;
  },

  _fields: [],

  _fieldRow(f, typeIcons) {
    const typeLabel = {
      text:'Text', textarea:'Text area', number:'Number', select:'Value list',
      date:'Date', datetime:'Date & time', address:'Address', bool:'Checkbox', header:'Header',
    }[f.field_type] || f.field_type;
    return `
      <div class="forms-field" data-id="${f.id}" draggable="true"
           ondragstart="FormsPage._dragStart(event,'${f.id}')"
           ondragover="FormsPage._dragOver(event)"
           ondrop="FormsPage._drop(event,'${f.id}')"
           ondragend="FormsPage._dragEnd(event)">
        <span class="forms-field-grip">${icon('list',14)}</span>
        <span class="forms-field-icon" style="color:var(--blue)">${icon(typeIcons[f.field_type]||'list',15)}</span>
        <div class="forms-field-info">
          <div class="forms-field-label">${f.field_label}${f.required?' <span class="forms-req">required</span>':''}${f.lock_after_create?` <span class="forms-req" style="background:rgba(120,140,190,.16);color:var(--tx-3)">locked</span>`:''}</div>
          <div class="forms-field-meta">${typeLabel}${f.options?.length?` · ${f.options.length} options`:''}</div>
        </div>
        <button class="forms-field-btn" onclick="FormsPage.editField('${f.id}')" title="Edit">${icon('list',13)}</button>
        <button class="forms-field-btn forms-field-del" onclick="FormsPage.deleteField('${f.id}')" title="Delete">${icon('x',13)}</button>
      </div>`;
  },

  /* ── drag reorder ── */
  _dragId: null,
  _dragStart(e, id) { this._dragId = id; e.currentTarget.classList.add('dragging'); },
  _dragOver(e) { e.preventDefault();
    const row = e.currentTarget;
    if (row.dataset.id !== this._dragId) row.classList.add('drag-over');
  },
  _dragEnd(e) {
    document.querySelectorAll('.forms-field').forEach(r => r.classList.remove('dragging','drag-over'));
  },
  async _drop(e, targetId) {
    e.preventDefault();
    if (!this._dragId || this._dragId === targetId) return;
    const ids = this._fields.map(f => f.id);
    const from = ids.indexOf(this._dragId);
    const to = ids.indexOf(targetId);
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    // optimistic reorder
    this._fields.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
    this.loadFieldsLocal();
    try {
      if (this._entity()) await patch(`/api/forms/entity-fields/reorder?entity=${this._entity()}`, { ordered_ids: ids });
      else await patch(`/api/forms/types/${this.activeType}/fields/reorder`, { ordered_ids: ids });
    } catch (err) { Toast.error(err.message); }
  },
  loadFieldsLocal() {
    const typeIcons = { text:'list', textarea:'message', number:'chart', select:'grid', date:'calendar', datetime:'calendar', address:'box', bool:'check-circle', header:'folder' };
    const list = document.getElementById('forms-field-list');
    if (list) list.innerHTML = this._fields.map(f => this._fieldRow(f, typeIcons)).join('');
  },

  /* ── type CRUD ── */
  newType() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New work order type</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="ty-name" placeholder="e.g. Repair, Warranty, Rental"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Color</label>
            <input class="field-input" id="ty-color" type="color" value="#4f8ef7" style="height:44px;padding:4px">
          </div>
          <div class="field"><label class="field-label">Initial status</label>
            <select class="field-select" id="ty-status">
              <option value="new">new</option><option value="diagnosed">diagnosed</option><option value="in_repair">in_repair</option>
            </select>
          </div>
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer">
          <input type="checkbox" id="ty-default" style="width:auto;accent-color:var(--blue)"> Make this the default type
        </label>
        <div class="field" style="margin-top:12px"><label class="field-label">Default asset type</label>
          <select class="field-select" id="ty-asset-type">
            ${['Device','Phone','Laptop','Tablet','Vehicle','Appliance','Tool','Other'].map(a=>`<option value="${a}">${a}</option>`).join('')}
          </select>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">Scheduling defaults</div>
        <div class="field"><label class="field-label">Default duration (minutes)</label>
          <input class="field-input" id="ty-duration" type="number" min="0" step="5" value="60">
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer">
          <input type="checkbox" id="ty-sched-req" style="width:auto;accent-color:var(--blue)"> Require a schedule slot ("Scheduled for") on this type
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="FormsPage.submitType()">Create type</button>
      </div>`, 'modal-narrow');
  },

  async editType(id) {
    const t = this.types.find(x => x.id === id);
    if (!t) return;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Edit type</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="ty-name" value="${t.name}"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Color</label>
            <input class="field-input" id="ty-color" type="color" value="${t.color}" style="height:44px;padding:4px">
          </div>
          <div class="field"><label class="field-label">Initial status</label>
            <select class="field-select" id="ty-status">
              ${['new','diagnosed','in_repair'].map(s=>`<option value="${s}"${t.initial_status===s?' selected':''}>${s}</option>`).join('')}
            </select>
          </div>
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer">
          <input type="checkbox" id="ty-default" ${t.is_default?'checked':''} style="width:auto;accent-color:var(--blue)"> Default type
        </label>
        <div class="field" style="margin-top:12px"><label class="field-label">Default asset type</label>
          <select class="field-select" id="ty-asset-type">
            ${['Device','Phone','Laptop','Tablet','Vehicle','Appliance','Tool','Other'].map(a=>`<option value="${a}"${t.default_asset_type===a?' selected':''}>${a}</option>`).join('')}
          </select>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">Scheduling defaults</div>
        <div class="field"><label class="field-label">Default duration (minutes)</label>
          <input class="field-input" id="ty-duration" type="number" min="0" step="5" value="${t.sched_default_duration ?? 60}">
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer">
          <input type="checkbox" id="ty-sched-req" ${t.sched_required?'checked':''} style="width:auto;accent-color:var(--blue)"> Require a schedule slot ("Scheduled for") on this type
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-danger btn-sm" onclick="FormsPage.deleteType('${id}')">Delete type</button>
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="FormsPage.submitType('${id}')">Save</button>
      </div>`, 'modal-narrow');
  },

  async submitType(id = null) {
    const name = document.getElementById('ty-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const body = {
      name, color: document.getElementById('ty-color').value,
      initial_status: document.getElementById('ty-status').value,
      default_asset_type: document.getElementById('ty-asset-type').value,
      sched_default_duration: parseInt(document.getElementById('ty-duration')?.value) || 60,
      sched_required: document.getElementById('ty-sched-req')?.checked || false,
      is_default: document.getElementById('ty-default').checked,
    };
    try {
      if (id) { await put(`/api/forms/types/${id}`, body); Toast.success('Type updated'); }
      else {
        const r = await post('/api/forms/types', body);
        this.activeType = r.id; Toast.success('Type created');
      }
      Modal.close(); await this.reloadTypes();
    } catch (e) { Toast.error(e.message); }
  },

  async deleteType(id) {
    const ok = await confirm2('Delete this type and all its fields? Existing orders keep their data.');
    if (!ok) return;
    try {
      await del(`/api/forms/types/${id}`);
      if (this.activeType === id) this.activeType = null;
      Toast.success('Type deleted'); Modal.close(); await this.reloadTypes();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── field CRUD ── */
  addField() { this._fieldModal(); },

  editField(id) {
    const f = this._fields.find(x => x.id === id);
    if (f) this._fieldModal(f);
  },

  _fieldModal(field = null) {
    const isEdit = !!field;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${isEdit?'Edit field':'Add field'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Label *</label><input class="field-input" id="fl-label" value="${field?.field_label||''}" placeholder="e.g. Fault reported"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Field type</label>
            <select class="field-select" id="fl-type" onchange="FormsPage._toggleOptions()">
              ${[['text','Text'],['textarea','Text area'],['select','Value list'],['bool','Checkbox'],['date','Date'],['datetime','Date & time'],['number','Number'],['address','Address'],['header','Header (label only)']]
                .map(([v,l])=>`<option value="${v}"${field?.field_type===v?' selected':''}>${l}</option>`).join('')}
            </select>
          </div>
          <div class="field"><label class="field-label">Placeholder</label><input class="field-input" id="fl-ph" value="${field?.placeholder||''}" placeholder="Optional hint"></div>
        </div>
        <div class="field" id="fl-options-wrap" style="display:none">
          <label class="field-label">Dropdown options (one per line)</label>
          <textarea class="field-textarea" id="fl-options" placeholder="Option A&#10;Option B&#10;Option C">${(field?.options||[]).join('\n')}</textarea>
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer">
          <input type="checkbox" id="fl-req" ${field?.required?'checked':''} style="width:auto;accent-color:var(--blue)"> Required field
        </label>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer;margin-top:8px">
          <input type="checkbox" id="fl-lock" ${field?.lock_after_create?'checked':''} style="width:auto;accent-color:var(--blue)"> Disable editing after creating
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="FormsPage.submitField(${field?`'${field.id}'`:'null'})">${isEdit?'Save':'Add field'}</button>
      </div>`, 'modal-narrow');
    this._toggleOptions();
  },

  _toggleOptions() {
    const t = document.getElementById('fl-type')?.value;
    const wrap = document.getElementById('fl-options-wrap');
    if (wrap) wrap.style.display = t === 'select' ? '' : 'none';
  },

  async submitField(id = null) {
    const label = document.getElementById('fl-label').value.trim();
    if (!label) return Toast.error('Label is required');
    const type = document.getElementById('fl-type').value;
    const options = type === 'select'
      ? document.getElementById('fl-options').value.split('\n').map(s => s.trim()).filter(Boolean)
      : [];
    const body = {
      field_label: label, field_type: type,
      required: document.getElementById('fl-req').checked,
      lock_after_create: document.getElementById('fl-lock')?.checked || false,
      placeholder: document.getElementById('fl-ph').value || null,
      options,
    };
    try {
      if (id) { await put(`/api/forms/fields/${id}`, body); Toast.success('Field updated'); }
      else if (this._entity()) { await post('/api/forms/entity-fields', { entity_type: this._entity(), ...body }); Toast.success('Field added'); }
      else { await post(`/api/forms/types/${this.activeType}/fields`, body); Toast.success('Field added'); }
      Modal.close(); await this._reloadFields();
    } catch (e) { Toast.error(e.message); }
  },

  async deleteField(id) {
    const ok = await confirm2('Delete this field?');
    if (!ok) return;
    try { await del(`/api/forms/fields/${id}`); Toast.success('Field deleted'); await this._reloadFields(); }
    catch (e) { Toast.error(e.message); }
  },
};

Router.register('forms', () => FormsPage.load());
