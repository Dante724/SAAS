/* ════════════════════════════════════════════════════
   Work Orders page
════════════════════════════════════════════════════ */

const OrdersPage = {

  _woBtn() { return (window.__tpl && window.__tpl.wo_button) || '+ Order'; },
  // A device may have only a serial (no brand/model). Join what exists instead
  // of printing "null null".
  _deviceName(d) {
    if (!d) return '—';
    const name = [d.brand, d.model].filter(Boolean).join(' ').trim();
    return name || (d.serial ? 'Serial only' : '—');
  },

  // Build 144ai — a small asset thumbnail with a hover-enlarge popover, used in the
  // work-order list. Renders nothing when the asset has no photo.
  _assetThumb(d) {
    if (!d || !d.image_data) return '';
    return `<span class="asset-thumb"><img src="${d.image_data}" alt="asset">` +
           `<span class="asset-thumb-pop"><img src="${d.image_data}" alt="asset"></span></span>`;
  },

  async load() {
    await this._loadTpl();
    setTopbarActions(`
      <div class="view-toggle">
        <button class="vt-btn active" id="vt-table" onclick="OrdersPage.setView('table')">${icon('list',14)} Table</button>
        <button class="vt-btn" id="vt-board" onclick="OrdersPage.setView('board')">${icon('grid',14)} Board</button>
        <button class="vt-btn" id="vt-sched" onclick="OrdersPage.setView('scheduler')">${icon('calendar',14)} Scheduler</button>
      </div>`);
    setContent(`
      <div id="o-kpis" class="o-kpi-row"></div>
      <div id="view-table">
        <div class="o-toolbar">
          <div class="o-toolbar-left">
            <button class="btn btn-primary" data-perm="can_create_tickets" onclick="OrdersPage.newModal()">${OrdersPage._woBtn()}</button>
            <button class="btn btn-success-outline" data-perm="can_create_estimates" onclick="OrdersPage.newEstimate()">+ Estimate</button>
            <span class="o-toolbar-div"></span>
            <button class="btn btn-glass btn-sm" id="o-filter-btn" onclick="OrdersPage.toggleFilters()">${icon('list',14)} Filter <span id="o-filter-count"></span></button>
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.archivedModal()" title="Deleted work orders, with the reason each was removed">${icon('trash',14)} Archived</button>
            <div id="o-saved-filters" class="saved-row"></div>
          </div>
          <div class="o-toolbar-right">
            <div class="o-doctype-seg">
              <button class="ods-btn active" data-dt="all" onclick="OrdersPage.setDocType('all')">All</button>
              <button class="ods-btn" data-dt="wo" onclick="OrdersPage.setDocType('wo')">Work orders</button>
              <button class="ods-btn" data-dt="est" onclick="OrdersPage.setDocType('est')">Estimates</button>
            </div>
            <div class="search-box o-search-box">
              <span class="search-icon">${icon('search',14)}</span>
              <input class="field-input search-input" id="o-search" placeholder="At least 3 characters…" oninput="OrdersPage.reload()">
            </div>
            <div style="position:relative">
              <button class="ic-btn col-gear" title="Columns" aria-label="Columns" onclick="OrdersPage.toggleColMenu(event)">${icon('bolt',16)}</button>
              <div class="col-menu" id="wo-col-menu" style="display:none"></div>
            </div>
          </div>
        </div>
        <div id="o-filter-panel" class="filter-panel" style="display:none"></div>
        <div class="chip-row" id="o-status-chips" style="margin-bottom:14px">
          <div class="chip active" data-status="" onclick="OrdersPage.filterStatus('',this)">All</div>
          ${this._STATUSES.map(s =>
            `<div class="chip" data-status="${s}" onclick="OrdersPage.filterStatus('${s}',this)">${this._statusName(s)}</div>`
          ).join('')}
        </div>
        <div class="card">
          <div class="tbl-wrap"><table class="tbl" id="wo-list-table">
            <thead><tr>
              <th style="width:36px"><input type="checkbox" id="o-check-all" onclick="OrdersPage.toggleAll(this.checked)" style="cursor:pointer;accent-color:var(--blue)"></th>
              <th>Order #</th><th class="col-client">Client</th><th class="col-device">Device</th><th class="col-status">Status</th><th class="col-invoice">Invoice</th><th class="col-priority">Priority</th><th class="col-total">Total</th><th class="col-assigned">Assigned</th><th class="col-created">Created</th><th></th>
            </tr></thead>
            <tbody id="o-tbody">${skeletonRows(10)}</tbody>
          </table></div>
        </div>
      </div>
      <div id="view-scheduler" style="display:none"></div>
      <div id="view-board" style="display:none"></div>
      <div id="o-bulk-bar" class="bulk-bar" style="display:none"></div>
    `);
    this._status = '';
    this._view = 'table';
    this._isEstimate = false;
    this._filters = {};
    this._selected = new Set();
    this._lastOrders = [];
    await this.loadStatuses();
    await this.loadSavedFilters();
    await this.reload();
    this._loadKpis();
  },

  _isEstimate: false,
  _docType: 'all',   // Build 144di: unified list — 'all' | 'wo' | 'est'
  setDocType(t) {
    // Back-compat: older callers pass a boolean (true = estimates).
    if (t === true) t = 'est'; else if (t === false) t = 'wo';
    this._docType = t;
    this._isEstimate = (t === 'est');   // drives the status set + create default
    document.querySelectorAll('.ods-btn').forEach(b => b.classList.toggle('active', b.dataset.dt === t));
    this._status = '';
    this.loadStatuses();
    this.refreshActiveView();
  },

  async _loadKpis() {
    const row = document.getElementById('o-kpis');
    if (!row) return;
    try {
      const all = (await get(`/api/orders?is_estimate=false&limit=500${(typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : ''}`)).items;
      const me = await get('/api/auth/me');
      const openGroups = ['new','in_progress','pending','ready'];
      const incomplete = all.filter(o => !o.closed_at).length;
      const mine = all.filter(o => o.assigned_to?.id === me.id && !o.closed_at).length;
      const urgent = all.filter(o => o.priority >= 1 && !o.closed_at).length;
      const overdue = all.filter(o => o.overdue_by_date).length;
      const receivables = all.reduce((s,o)=> s + (o.total||0), 0);
      const cards = [
        { n: incomplete, label: 'Incomplete', cls: 'kpi-amber', icon: 'grid' },
        { n: mine, label: 'My work orders', cls: 'kpi-green', icon: 'wrench' },
        { n: urgent, label: 'Urgent', cls: 'kpi-red', icon: 'bolt' },
        { n: overdue, label: 'Overdue', cls: 'kpi-amber', icon: 'clock' },
        { n: fmt.currency(receivables), label: 'Receivables', cls: 'kpi-dark', icon: 'coin' },
      ];
      row.innerHTML = cards.map(c => `
        <div class="o-kpi ${c.cls}">
          <span class="o-kpi-ic">${icon(c.icon,18)}</span>
          <div><div class="o-kpi-n">${c.n}</div><div class="o-kpi-l">${c.label}</div></div>
        </div>`).join('');
    } catch {}
  },

  newEstimate() { this.newModal({ is_estimate: true }); },

  _view: 'table',
  _filters: {},
  _selected: new Set(),
  _lastOrders: [],
  _employees: [],
  _types: [],
  _savedFilters: [],
  _statusDefs: [],
  _STATUSES: ['new','diagnosed','waiting_parts','in_repair','done','delivered'],

  async loadStatuses() {
    try {
      const data = await get(`/api/statuses?is_estimate=${this._isEstimate ? 'true' : 'false'}`);
      this._statusDefs = data.items;
      this._STATUSES = data.items.map(s => s.key);
      // rebuild the status chip row for the current document type
      const row = document.getElementById('o-status-chips');
      if (row) {
        row.innerHTML = `<div class="chip active" data-status="" onclick="OrdersPage.filterStatus('',this)">All</div>` +
          this._STATUSES.map(s => `<div class="chip" data-status="${s}" onclick="OrdersPage.filterStatus('${s}',this)">${this._statusName(s)}</div>`).join('');
      }
    } catch { /* keep fallback list */ }
  },

  _statusDef(key) { return this._statusDefs.find(s => s.key === key); },
  _statusName(key) { const d = this._statusDef(key); return d ? d.name : key.replace(/_/g,' '); },

  setView(v) {
    this._view = v;
    document.getElementById('view-table').style.display     = v === 'table' ? '' : 'none';
    document.getElementById('view-scheduler').style.display = v === 'scheduler' ? '' : 'none';
    document.getElementById('view-board').style.display     = v === 'board' ? '' : 'none';
    document.getElementById('vt-table').classList.toggle('active', v === 'table');
    document.getElementById('vt-board').classList.toggle('active', v === 'board');
    document.getElementById('vt-sched').classList.toggle('active', v === 'scheduler');
    if (v === 'scheduler') Scheduler.render(document.getElementById('view-scheduler'));
    if (v === 'board') this.loadBoard();
  },

  /* ── Board (Kanban) ── */
  async loadBoard() {
    await this._loadTpl();
    const wrap = document.getElementById('view-board');
    wrap.innerHTML = '<div class="card" style="padding:30px;text-align:center;color:var(--tx-4)">Loading board…</div>';
    let data;
    try { data = await get(`/api/orders/board?is_estimate=${this._isEstimate ? 'true' : 'false'}${(typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : ''}`); }
    catch (e) { return Toast.error(e.message); }
    wrap.innerHTML = `
      <div class="o-toolbar" style="margin-bottom:14px">
        <div class="o-toolbar-left">
          <button class="btn btn-primary" onclick="OrdersPage.newModal()">${OrdersPage._woBtn()}</button>
          <button class="btn btn-success-outline" onclick="OrdersPage.newEstimate()">+ Estimate</button>
        </div>
        <div class="o-toolbar-right">
          <div class="o-doctype-seg">
            <button class="ods-btn ${this._docType==='all'?'active':''}" data-dt="all" onclick="OrdersPage.setDocType('all')">All</button>
            <button class="ods-btn ${this._docType==='wo'?'active':''}" data-dt="wo" onclick="OrdersPage.setDocType('wo')">Work orders</button>
            <button class="ods-btn ${this._docType==='est'?'active':''}" data-dt="est" onclick="OrdersPage.setDocType('est')">Estimates</button>
          </div>
        </div>
      </div>
      <div class="kanban">${data.columns.map(col => `
      <div class="kan-col" data-group="${col.group}">
        <div class="kan-col-head">
          <span class="kan-col-title">${col.label}</span>
          <span class="kan-col-count">${col.cards.length}</span>
        </div>
        <div class="kan-col-body" data-target-status="${col.statuses[0]?.key || ''}">
          ${col.cards.map(c => this._kanCard(c)).join('') || '<div class="kan-empty">Drop here</div>'}
        </div>
      </div>`).join('')}</div>`;
  },

  _kanCard(c) {
    const pri = c.priority >= 2 ? '<span class="kan-pri vip">VIP</span>' : c.priority === 1 ? '<span class="kan-pri urgent">Urgent</span>' : '';
    const img = c.device_image ? `<div class="kan-thumb"><img src="${c.device_image}" alt="asset"></div>` : '';
    const msg = (c.client_msg_unread ? `<span class="msg-dot" title="${c.client_msg_unread} new message${c.client_msg_unread > 1 ? 's' : ''} from customer">${c.client_msg_unread}</span>` : '')
      + (c.rec_unread ? `<span class="msg-dot msg-dot-rec" title="${c.rec_unread} recommendation decision${c.rec_unread > 1 ? 's' : ''} from customer">${c.rec_unread}</span>` : '');
    return `<div class="kan-card${c.needs_attention ? ' attn' : ''}${c.overdue ? ' overdue' : ''}" data-card-id="${c.id}"
      onpointerdown="OrdersPage._kanDown(event,'${c.id}')">
      ${msg}${img}
      <div class="kan-card-top"><span class="kan-num">${c.order_number}</span>${pri}</div>
      <div class="kan-client">${c.client_name || 'Walk-in'}</div>
      ${c.device ? `<div class="kan-device">${c.device}</div>` : ''}
      <div class="kan-card-foot">
        <span class="kan-total">${fmt.currency(c.total)}</span>
        ${c.assigned_to_name ? `<span class="kan-assignee">${c.assigned_to_name}</span>` : ''}
      </div>
      ${c.needs_attention ? '<div class="kan-flag">Scheduled · still New</div>' : ''}
    </div>`;
  },

  /* Build 144ai — pointer-based drag & drop. Native HTML5 DnD was unreliable on
     trackpads and fought the click-to-open handler. This uses pointer events with a
     small movement threshold: a tap opens the order, a drag moves it between columns. */
  _kanDrag: { id: null, active: false, ghost: null, el: null, moveH: null, upH: null, sx: 0, sy: 0, ox: 0, oy: 0 },

  _kanDown(e, id) {
    if (e.button !== undefined && e.button !== 0) return;   // primary button / touch only
    const d = this._kanDrag;
    d.id = id; d.active = false; d.sx = e.clientX; d.sy = e.clientY; d.el = e.currentTarget;
    d.moveH = (ev) => this._kanMove(ev);
    d.upH = (ev) => this._kanUp(ev);
    document.addEventListener('pointermove', d.moveH);
    document.addEventListener('pointerup', d.upH);
  },

  _kanBodyAt(x, y) {
    if (this._kanDrag.ghost) this._kanDrag.ghost.style.pointerEvents = 'none';
    const el = document.elementFromPoint(x, y);
    return el ? el.closest('.kan-col-body') : null;
  },

  _kanMove(e) {
    const d = this._kanDrag;
    if (!d.id) return;
    if (!d.active) {
      if (Math.abs(e.clientX - d.sx) + Math.abs(e.clientY - d.sy) < 6) return;
      d.active = true;
      d.el.classList.add('kan-dragging');
      const r = d.el.getBoundingClientRect();
      const g = d.el.cloneNode(true);
      g.classList.add('kan-ghost');
      g.style.width = r.width + 'px';
      d.ox = e.clientX - r.left; d.oy = e.clientY - r.top;
      document.body.appendChild(g);
      d.ghost = g;
    }
    if (d.ghost) {
      d.ghost.style.left = (e.clientX - d.ox) + 'px';
      d.ghost.style.top = (e.clientY - d.oy) + 'px';
    }
    const body = this._kanBodyAt(e.clientX, e.clientY);
    document.querySelectorAll('.kan-col-body.kan-drop').forEach(el => { if (el !== body) el.classList.remove('kan-drop'); });
    if (body) body.classList.add('kan-drop');
    e.preventDefault();
  },

  async _kanUp(e) {
    const d = this._kanDrag;
    document.removeEventListener('pointermove', d.moveH);
    document.removeEventListener('pointerup', d.upH);
    const wasActive = d.active, id = d.id;
    const body = wasActive ? this._kanBodyAt(e.clientX, e.clientY) : null;
    if (d.ghost) d.ghost.remove();
    if (d.el) d.el.classList.remove('kan-dragging');
    document.querySelectorAll('.kan-col-body.kan-drop').forEach(el => el.classList.remove('kan-drop'));
    this._kanDrag = { id: null, active: false, ghost: null, el: null, moveH: null, upH: null, sx: 0, sy: 0, ox: 0, oy: 0 };
    if (!wasActive) { if (id) this.viewOrder(id); return; }   // a tap → open the order
    if (!body || !id) return;
    const targetStatus = body.getAttribute('data-target-status');
    if (!targetStatus) return;
    try {
      await patch(`/api/orders/${id}/status`, { status: targetStatus });
      Toast.success('Status updated');
      this.loadBoard();
      this.reload();
    } catch (err) {
      Toast.error(err.message);  // transfer-rule violations surface here
      this.loadBoard();
    }
  },

  /* Build 144ai — reschedule a work order from the list row or the general-info page. */
  rescheduleModal(orderId) {
    const cur = (this._lastOrders || []).find(o => o.id === orderId) || {};
    const dt = (s) => (s || '').slice(0, 16);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Reschedule</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Start</label>
          <input class="field-input" type="datetime-local" id="rs-from" value="${dt(cur.scheduled_for)}"></div>
        <div class="field"><label class="field-label">End (optional)</label>
          <input class="field-input" type="datetime-local" id="rs-to" value="${dt(cur.scheduled_to)}"></div>
        <div style="font-size:11.5px;color:var(--tx-3);margin-top:4px">Leave both blank to clear the schedule.</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage._saveReschedule('${orderId}')">Save</button>
      </div>`);
  },

  async _saveReschedule(orderId) {
    const from = document.getElementById('rs-from').value || null;
    const to = document.getElementById('rs-to').value || null;
    try {
      await patch(`/api/orders/${orderId}/schedule`, { scheduled_for: from, scheduled_to: to });
      Modal.close();
      Toast.success(from ? 'Rescheduled' : 'Schedule cleared');
      this.reload();
      if (this._view === 'scheduler' && typeof Scheduler !== "undefined") Scheduler.load();
    } catch (e) { Toast.error(e.message); }
  },

  /* Build 144ai/144aq — attach or replace the asset photo via the shared picker
     (take photo or choose a file); downscaled to a small JPEG data URL. */
  changeAssetPhoto(deviceId, orderId) {
    if (!deviceId) return;
    PhotoInput.capture({
      title: 'Asset photo',
      onPhoto: async (dataUrl) => {
        try {
          await put(`/api/assets/${deviceId}`, { image_data: dataUrl });
          Toast.success('Photo saved');
          if (orderId) this.viewOrder(orderId);
          this.reload();
        } catch (e) { Toast.error(e.message); }
      },
    });
  },

  _status: '',

  filterStatus(s, el) {
    this._status = s;
    document.querySelectorAll('#o-status-chips .chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    this.reload();
  },

  _buildQuery() {
    const search = document.getElementById('o-search')?.value || '';
    const p = new URLSearchParams();
    p.set('limit', '100');
    // Build 144di: unified list — only constrain the type when a specific one is chosen.
    if (this._docType === 'wo') p.set('is_estimate', 'false');
    else if (this._docType === 'est') p.set('is_estimate', 'true');
    if (this._status) p.set('status', this._status);
    if (search) p.set('search', search);
    const f = this._filters || {};
    if (f.type_id) p.set('type_id', f.type_id);
    if (f.assigned_to_id) p.set('assigned_to_id', f.assigned_to_id);
    if (f.priority !== undefined && f.priority !== '') p.set('priority', f.priority);
    if (f.client_id) p.set('client_id', f.client_id);
    // build 138: an explicit filter wins; otherwise follow the global location scope.
    const loc = f.location_id || (typeof App !== 'undefined' ? App.locationId : '');
    if (loc) p.set('location_id', loc);
    if (f.brand) p.set('brand', f.brand);
    if (f.model) p.set('model', f.model);
    if (f.created_from) p.set('created_from', f.created_from);
    if (f.created_to) p.set('created_to', f.created_to);
    return p.toString();
  },

  async reload() {
    const tbody = document.getElementById('o-tbody');
    if (!tbody) return;
    try {
      const data = await get(`/api/orders?${this._buildQuery()}`);
      this._lastOrders = data.items;
      if (!data.items.length) { tbody.innerHTML = emptyRow(10,'wrench','No work orders','Try adjusting filters or create an order'); this._renderBulkBar(); return; }
      tbody.innerHTML = data.items.map(o => {
        const checked = this._selected.has(o.id) ? 'checked' : '';
        return `
        <tr class="${this._selected.has(o.id)?'row-sel':''}" data-id="${o.id}">
          <td onclick="event.stopPropagation()"><input type="checkbox" ${checked} onclick="OrdersPage.toggleRow('${o.id}',this.checked)" style="cursor:pointer;accent-color:var(--blue)"></td>
          <td onclick="OrdersPage.viewOrder('${o.id}')"><span class="tbl-num tbl-strong">${o.order_number}</span>${o.overdue_by_date ? '<span class="od-badge od-date">overdue</span>' : ''}${o.client_msg_unread ? `<span class="msg-dot msg-dot-inline" title="${o.client_msg_unread} new customer message${o.client_msg_unread > 1 ? 's' : ''}">${o.client_msg_unread}</span>` : ''}${o.rec_unread ? `<span class="msg-dot msg-dot-inline msg-dot-rec" title="${o.rec_unread} recommendation decision${o.rec_unread > 1 ? 's' : ''}">${o.rec_unread}</span>` : ''}</td>
          <td class="col-client" onclick="OrdersPage.viewOrder('${o.id}')"><span style="font-weight:700">${o.client?.name || '<span style="color:var(--tx-3)">Walk-in</span>'}</span></td>
          <td class="col-device" onclick="OrdersPage.viewOrder('${o.id}')" style="color:var(--tx-2)"><span style="display:inline-flex;align-items:center;gap:8px">${OrdersPage._assetThumb(o.device)}${OrdersPage._deviceName(o.device)}</span></td>
          <td class="col-status">${this._statusDropdown(o)}</td>
          <td class="col-invoice" onclick="OrdersPage.viewOrder('${o.id}')">${OrdersPage._invoiceChip(o.invoice_status)}</td>
          <td class="col-priority" onclick="OrdersPage.viewOrder('${o.id}')">${priBadge(o.priority)}</td>
          <td class="col-total" onclick="OrdersPage.viewOrder('${o.id}')"><span class="tbl-strong">${fmt.currency(o.total)}</span></td>
          <td class="col-assigned" onclick="OrdersPage.viewOrder('${o.id}')">${o.assigned_to ? `<div style="display:flex;align-items:center;gap:7px">${avatarHtml(o.assigned_to.name,22)}<span style="font-size:12px;font-weight:700">${o.assigned_to.name}</span></div>` : '<span style="color:var(--tx-4)">—</span>'}</td>
          <td class="col-created" onclick="OrdersPage.viewOrder('${o.id}')" style="color:var(--tx-3);font-size:12px">${fmt.reltime(o.created_at)}</td>
          <td><div style="display:flex;gap:6px;justify-content:flex-end">
            <button class="btn btn-glass btn-sm" title="Reschedule" onclick="event.stopPropagation();OrdersPage.rescheduleModal('${o.id}')">${icon('calendar',14)}</button>
            <button class="btn btn-glass btn-sm" onclick="event.stopPropagation();OrdersPage.viewOrder('${o.id}')">Open →</button>
          </div></td>
        </tr>`;
      }).join('');
      this._syncCheckAll();
      this._renderBulkBar();
      this._applyCols();
      if (window.ColTools) ColTools.resizable('wo-list-table', 'workflows');
    } catch (e) { Toast.error(e.message); }
  },

  _statusDropdown(o) {
    // Build 144dy — work orders get a solid coloured chip; estimates stay a
    // transparent outline (Orderry parity), so the two never look alike.
    const wo = o.is_estimate ? '' : ' wo';
    return `<select class="row-status-sel${wo}" onclick="event.stopPropagation()" onchange="OrdersPage.inlineStatus('${o.id}',this.value)" style="--sc:${this._statusColor(o.status)}">
      ${this._STATUSES.map(s=>`<option value="${s}"${o.status===s?' selected':''}>${this._statusName(s)}</option>`).join('')}
    </select>`;
  },

  _statusColor(s) {
    const d = this._statusDef(s);
    if (d && d.color) return d.color;
    return ({new:'#4f8ef7',diagnosed:'#f5a623',waiting_parts:'#f06daa',in_repair:'#9b7bf4',done:'#3dca7a',delivered:'#2cd4c0'})[s] || '#90a0c0';
  },

  async inlineStatus(id, status) {
    try {
      const r = await patch(`/api/orders/${id}/status`, { status });
      Toast.success('Status updated');
      if (r && r.triggers_payment) await this.maybePaymentWindow(id);
      this.reload();
    } catch (e) { Toast.error(e.message); this.reload(); }
  },

  toggleRow(id, on) {
    if (on) this._selected.add(id); else this._selected.delete(id);
    document.querySelector(`#o-tbody tr[data-id="${id}"]`)?.classList.toggle('row-sel', on);
    this._syncCheckAll();
    this._renderBulkBar();
  },

  /* ── 144bj: configurable work-order list columns (persisted per browser) ── */
  _COLS: [['client', 'Client'], ['device', 'Device'], ['status', 'Status'], ['invoice', 'Invoice'], ['priority', 'Priority'], ['total', 'Total'], ['assigned', 'Assigned'], ['created', 'Created']],
  // Build 144ds — invoice status pill on the orders list
  _invoiceChip(s) {
    if (!s) return '<span style="color:var(--tx-4)">—</span>';
    const map = { draft: ['Draft', 'var(--tx-3)', 'rgba(148,163,184,.16)'], issued: ['Issued', 'var(--blue-dark,#1d4ed8)', 'color-mix(in srgb,var(--blue) 14%,#fff)'], paid: ['Paid', '#0b7a45', 'color-mix(in srgb,var(--green) 18%,#fff)'] };
    const [label, color, bg] = map[s] || [s, 'var(--tx-2)', 'rgba(148,163,184,.16)'];
    return `<span style="font-size:10.5px;font-weight:800;border-radius:999px;padding:2px 9px;color:${color};background:${bg}">${label}</span>`;
  },
  _hiddenCols() { try { return new Set(JSON.parse(localStorage.getItem('ros_wo_cols_hidden') || '[]')); } catch (e) { return new Set(); } },
  _applyCols() {
    const t = document.getElementById('wo-list-table'); if (!t) return;
    const hidden = this._hiddenCols();
    this._COLS.forEach(([c]) => t.classList.toggle('hide-' + c, hidden.has(c)));
  },
  toggleColMenu(e) {
    e.stopPropagation();
    const m = document.getElementById('wo-col-menu'); if (!m) return;
    if (m.style.display === 'block') { m.style.display = 'none'; return; }
    const hidden = this._hiddenCols();
    m.innerHTML = this._COLS.map(([c, label]) =>
      `<label><input type="checkbox" ${hidden.has(c) ? '' : 'checked'} onchange="OrdersPage.setCol('${c}',this.checked)"> ${label}</label>`).join('');
    m.style.display = 'block';
    setTimeout(() => document.addEventListener('click', OrdersPage._closeColMenu), 0);
  },
  _closeColMenu(ev) {
    const m = document.getElementById('wo-col-menu');
    if (m && !m.contains(ev.target)) { m.style.display = 'none'; document.removeEventListener('click', OrdersPage._closeColMenu); }
  },
  setCol(col, visible) {
    const hidden = this._hiddenCols();
    if (visible) hidden.delete(col); else hidden.add(col);
    try { localStorage.setItem('ros_wo_cols_hidden', JSON.stringify([...hidden])); } catch (e) {}
    this._applyCols();
  },

  toggleAll(on) {
    this._lastOrders.forEach(o => { if (on) this._selected.add(o.id); else this._selected.delete(o.id); });
    document.querySelectorAll('#o-tbody tr').forEach(tr => {
      const cb = tr.querySelector('input[type=checkbox]');
      if (cb) cb.checked = on;
      tr.classList.toggle('row-sel', on);
    });
    this._renderBulkBar();
  },

  _syncCheckAll() {
    const all = document.getElementById('o-check-all');
    if (!all) return;
    const ids = this._lastOrders.map(o => o.id);
    all.checked = ids.length > 0 && ids.every(id => this._selected.has(id));
  },

  _renderBulkBar() {
    const bar = document.getElementById('o-bulk-bar');
    if (!bar) return;
    const n = this._selected.size;
    if (!n) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    bar.innerHTML = `
      <span class="bulk-count">${n} selected</span>
      <div class="bulk-menu-wrap">
        <button class="bulk-actions-btn" onclick="OrdersPage.toggleBulkMenu(event)">${icon('bolt',13)} Actions \u25be</button>
        <div class="bulk-menu" id="o-bulk-menu" style="display:none">${this._bulkMenuBody()}</div>
      </div>
      <button class="bulk-clear" onclick="OrdersPage.clearSelection()">Clear</button>`;
  },

  _bulkSub: null,   // null = top level, else 'status' | 'assign' | 'priority'

  _bulkMenuBody() {
    if (this._bulkSub === 'status') {
      return `<button class="bulk-menu-back" onclick="event.stopPropagation();OrdersPage.bulkSubmenu(null)">\u2190 Change status</button>
        <div class="bulk-menu-div"></div>
        ${this._STATUSES.map(x=>`<button class="bulk-menu-item" onclick="OrdersPage.bulkSet('set_status','${x}');OrdersPage.closeBulkMenu()">${this._statusName(x)}</button>`).join('')}`;
    }
    if (this._bulkSub === 'assign') {
      return `<button class="bulk-menu-back" onclick="event.stopPropagation();OrdersPage.bulkSubmenu(null)">\u2190 Assign to</button>
        <div class="bulk-menu-div"></div>
        <button class="bulk-menu-item" onclick="OrdersPage.bulkSet('assign','');OrdersPage.closeBulkMenu()">Unassigned</button>
        ${this._employees.map(e=>`<button class="bulk-menu-item" onclick="OrdersPage.bulkSet('assign','${e.id}');OrdersPage.closeBulkMenu()">${e.name}</button>`).join('')}`;
    }
    if (this._bulkSub === 'priority') {
      return `<button class="bulk-menu-back" onclick="event.stopPropagation();OrdersPage.bulkSubmenu(null)">\u2190 Priority</button>
        <div class="bulk-menu-div"></div>
        <button class="bulk-menu-item" onclick="OrdersPage.bulkSet('set_priority','0');OrdersPage.closeBulkMenu()">Normal</button>
        <button class="bulk-menu-item" onclick="OrdersPage.bulkSet('set_priority','1');OrdersPage.closeBulkMenu()">Urgent</button>
        <button class="bulk-menu-item" onclick="OrdersPage.bulkSet('set_priority','2');OrdersPage.closeBulkMenu()">VIP</button>`;
    }
    return `
      <button class="bulk-menu-item" onclick="event.stopPropagation();OrdersPage.bulkSubmenu('status')">${icon('refresh',13)} Change status <span class="bulk-menu-arrow">\u203a</span></button>
      <button class="bulk-menu-item" onclick="event.stopPropagation();OrdersPage.bulkSubmenu('assign')">${icon('user',13)} Assign to <span class="bulk-menu-arrow">\u203a</span></button>
      <button class="bulk-menu-item" onclick="event.stopPropagation();OrdersPage.bulkSubmenu('priority')">${icon('bolt',13)} Priority <span class="bulk-menu-arrow">\u203a</span></button>
      <div class="bulk-menu-div"></div>
      <button class="bulk-menu-item" onclick="OrdersPage.bulkInvoice();OrdersPage.closeBulkMenu()">${icon('receipt',13)} Create invoices</button>
      <button class="bulk-menu-item" onclick="OrdersPage.bulkInvoice(true);OrdersPage.closeBulkMenu()">${icon('file',13)} Group invoice</button>`;
  },

  bulkSubmenu(sub) {
    this._bulkSub = sub;
    const m = document.getElementById('o-bulk-menu');
    if (m) { m.innerHTML = this._bulkMenuBody(); m.style.display = 'block'; }
  },

  toggleBulkMenu(e) {
    if (e) e.stopPropagation();
    const m = document.getElementById('o-bulk-menu');
    if (!m) return;
    const open = m.style.display !== 'none';
    if (!open) { this._bulkSub = null; m.innerHTML = this._bulkMenuBody(); }
    m.style.display = open ? 'none' : 'block';
    if (!open) {
      setTimeout(() => document.addEventListener('click', OrdersPage._bulkOutside), 0);
    }
  },
  _bulkOutside(e) {
    // Only close when the click lands outside the whole Actions widget. Checking the
    // wrapper (which is stable) instead of the clicked node avoids closing when a
    // submenu re-render detaches the element that was clicked.
    if (!e.target.closest('.bulk-menu-wrap') && !e.target.closest('.bulk-bar')) {
      OrdersPage.closeBulkMenu();
    }
  },
  closeBulkMenu() {
    const m = document.getElementById('o-bulk-menu');
    if (m) m.style.display = 'none';
    this._bulkSub = null;
    document.removeEventListener('click', OrdersPage._bulkOutside);
  },

  async bulkInvoice(group = false) {
    const ids = [...this._selected];
    if (!ids.length) return;
    try {
      const r = await post('/api/orders/bulk', {
        order_ids: ids, action: group ? 'group_invoice' : 'create_invoice', value: '' });
      Toast.success(group
        ? `Grouped ${ids.length} orders into one invoice`
        : `Created invoices for ${r.updated} order${r.updated===1?'':'s'}`);
      this.clearSelection();
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  clearSelection() {
    this._selected.clear();
    document.querySelectorAll('#o-tbody tr').forEach(tr => {
      const cb = tr.querySelector('input[type=checkbox]'); if (cb) cb.checked = false;
      tr.classList.remove('row-sel');
    });
    this._syncCheckAll();
    this._renderBulkBar();
  },

  async bulkSet(action, value) {
    if (value === '__none') return;
    if (action !== 'assign' && !value && value !== '0') return;
    const ids = [...this._selected];
    try {
      const r = await post('/api/orders/bulk', { order_ids: ids, action, value: String(value) });
      Toast.success(`Updated ${r.updated} order${r.updated===1?'':'s'}`);
      this.clearSelection();
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async toggleFilters() {
    const panel = document.getElementById('o-filter-panel');
    if (panel.style.display === 'none') {
      if (!this._employees.length) { try { this._employees = await get('/api/staff/employees'); } catch {} }
      if (!this._types.length) { try { this._types = (await get('/api/forms/types')).items; } catch {} }
      if (!this._clients) { try { this._clients = (await get('/api/clients?limit=200')).items; } catch { this._clients = []; } }
      if (!this._locations) { try { this._locations = (await get('/api/staff/locations')).items || []; } catch { this._locations = []; } }
      panel.innerHTML = this._filterPanelHtml();
      panel.style.display = '';
    } else {
      panel.style.display = 'none';
    }
  },

  _filterPanelHtml() {
    const f = this._filters || {};
    return `
      <div class="filter-grid">
        <div class="field"><label class="field-label">Work order type</label>
          <select class="field-select" id="f-type"><option value="">Any</option>
            ${this._types.map(t=>`<option value="${t.id}"${f.type_id===t.id?' selected':''}>${t.name}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">Assigned technician</label>
          <select class="field-select" id="f-assign"><option value="">Anyone</option>
            ${this._employees.map(e=>`<option value="${e.id}"${f.assigned_to_id===e.id?' selected':''}>${e.name}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">Priority</label>
          <select class="field-select" id="f-pri"><option value="">Any</option>
            <option value="0"${f.priority==='0'?' selected':''}>Normal</option>
            <option value="1"${f.priority==='1'?' selected':''}>Urgent</option>
            <option value="2"${f.priority==='2'?' selected':''}>VIP</option>
          </select></div>
        <div class="field"><label class="field-label">Client</label>
          <select class="field-select" id="f-client"><option value="">Any</option>
            ${(this._clients||[]).map(c=>`<option value="${c.id}"${f.client_id===c.id?' selected':''}>${c.name}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">Location</label>
          <select class="field-select" id="f-location"><option value="">Any</option>
            ${(this._locations||[]).map(l=>`<option value="${l.id}"${f.location_id===l.id?' selected':''}>${l.name}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">Asset brand</label>
          <input class="field-input" id="f-brand" value="${f.brand||''}" placeholder="e.g. Apple"></div>
        <div class="field"><label class="field-label">Asset model</label>
          <input class="field-input" id="f-model" value="${f.model||''}" placeholder="e.g. iPhone 14"></div>
        <div class="field"><label class="field-label">Created from</label>
          <input class="field-input" type="date" id="f-from" value="${f.created_from||''}"></div>
        <div class="field"><label class="field-label">Created to</label>
          <input class="field-input" type="date" id="f-to" value="${f.created_to||''}"></div>
      </div>
      <div class="filter-foot">
        <button class="btn btn-glass btn-sm" onclick="OrdersPage.resetFilters()">Reset</button>
        <button class="btn btn-glass btn-sm" onclick="OrdersPage.saveFilterPrompt()">Save filter…</button>
        <button class="btn btn-primary btn-sm" onclick="OrdersPage.applyFilters()">Apply</button>
      </div>`;
  },

  applyFilters() {
    this._filters = {
      type_id: document.getElementById('f-type')?.value || '',
      assigned_to_id: document.getElementById('f-assign')?.value || '',
      priority: document.getElementById('f-pri')?.value ?? '',
      client_id: document.getElementById('f-client')?.value || '',
      location_id: document.getElementById('f-location')?.value || '',
      brand: document.getElementById('f-brand')?.value || '',
      model: document.getElementById('f-model')?.value || '',
      created_from: document.getElementById('f-from')?.value || '',
      created_to: document.getElementById('f-to')?.value || '',
    };
    this._updateFilterCount();
    this.reload();
  },

  resetFilters() {
    this._filters = {};
    document.getElementById('o-filter-panel').innerHTML = this._filterPanelHtml();
    this._updateFilterCount();
    this.reload();
  },

  _updateFilterCount() {
    const n = Object.values(this._filters).filter(v => v !== '' && v !== undefined).length;
    const el = document.getElementById('o-filter-count');
    if (el) el.innerHTML = n ? `<span class="filter-badge">${n}</span>` : '';
  },

  async loadSavedFilters() {
    try { this._savedFilters = (await get('/api/orders/filters/saved')).items; }
    catch { this._savedFilters = []; }
    this._renderSavedFilters();
  },

  _renderSavedFilters() {
    const el = document.getElementById('o-saved-filters');
    if (!el) return;
    el.innerHTML = this._savedFilters.map(sf =>
      `<button class="saved-chip" onclick="OrdersPage.applySaved('${sf.id}')" title="${sf.is_shared?'Shared with all staff':'Private'}">
        ${sf.is_shared?icon('users',12):icon('user',12)} ${this._esc(sf.name)}
        <span class="saved-x" onclick="event.stopPropagation();OrdersPage.deleteSaved('${sf.id}')">${icon('x',11)}</span>
      </button>`).join('');
  },

  applySaved(id) {
    const sf = this._savedFilters.find(x => x.id === id);
    if (!sf) return;
    const p = sf.params || {};
    this._filters = { type_id:p.type_id||'', assigned_to_id:p.assigned_to_id||'', priority:p.priority??'', created_from:p.created_from||'', created_to:p.created_to||'' };
    if (p.status !== undefined) {
      this._status = p.status || '';
      document.querySelectorAll('#o-status-chips .chip').forEach(c =>
        c.classList.toggle('active', (c.dataset.status||'') === this._status));
    }
    this._updateFilterCount();
    this.reload();
    Toast.success(`Filter "${sf.name}" applied`);
  },

  saveFilterPrompt() {
    this.applyFilters();
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Save filter</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Filter name *</label><input class="field-input" id="sf-name" placeholder="e.g. My urgent repairs"></div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer">
          <input type="checkbox" id="sf-shared" style="width:auto;accent-color:var(--blue)"> Share with all staff
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.saveFilter()">Save</button>
      </div>`, 'modal-narrow');
  },

  async saveFilter() {
    const name = document.getElementById('sf-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const params = { ...this._filters, status: this._status };
    try {
      await post('/api/orders/filters/saved', { name, params, is_shared: document.getElementById('sf-shared').checked });
      Toast.success('Filter saved');
      Modal.close();
      await this.loadSavedFilters();
    } catch (e) { Toast.error(e.message); }
  },

  async deleteSaved(id) {
    try { await del(`/api/orders/filters/saved/${id}`); await this.loadSavedFilters(); Toast.success('Filter removed'); }
    catch (e) { Toast.error(e.message); }
  },

  _esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; },

  /* (Build 144bx) old duplicate column menu removed — the col-gear (toggleColMenu)
     in the search row is the single, working column picker. */

  async viewOrder(id) {
    await this._loadEmps();
    let o;
    try { o = await get(`/api/orders/${id}`); } catch (e) { Toast.error(e.message); return; }
    // Build 122: display currency for this document (base unless the doc/client set one).
    const cur = (o.currency_code && o.fx_rate)
      ? { code: o.currency_code, symbol: o.currency_symbol || o.currency_code, rate: o.fx_rate }
      : null;
    this._docCur = cur;
    const statuses = this._STATUSES;
    Modal.open(`
      <div class="modal-header">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <span class="modal-title">${o.order_number}</span>${OrdersPage._woStatusPill(o, id)}${priBadge(o.priority)}
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <!-- 144el — ranked header: Print/Message are icon-only, the rest live in Actions -->
          <button class="btn btn-glass btn-sm btn-ico" onclick="OrdersPage.printDocModal('${id}')" title="Print — labels, invoices, tickets, documents">${icon('file',15)}</button>
          <button class="btn btn-glass btn-sm btn-ico" onclick="OrdersPage.messageModal('${id}')" title="Message the customer (email or SMS)">${icon('message',15)}</button>
          <div class="wo-actions-wrap" style="position:relative;display:inline-block;z-index:2000">
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.toggleActions(event)">${icon('bolt',14)} Actions ${icon('chevron-down',12)}</button>
            <div class="wo-actions-menu" style="display:none;position:absolute;right:0;top:calc(100% + 4px);z-index:2000;min-width:220px;background:var(--elev-3,var(--g-fill-heavy));border:1px solid var(--g-border-mid);border-radius:var(--r-md);box-shadow:var(--g-shadow-lg);padding:6px;">
              <button class="wo-act" onclick="OrdersPage.orderCodeModal('${id}');OrdersPage.toggleActions(event)">${icon('grid',14)} Order code (barcode / QR)</button>
              <button class="wo-act" onclick="OrdersPage.currencyModal('${id}');OrdersPage.toggleActions(event)">${icon('coin',14)} Billing currency${cur?` · ${cur.code}`:''}</button>
              <button class="wo-act" onclick="OrdersPage.copyPublicLink('${id}');OrdersPage.toggleActions(event)">${icon('external-link',14)} Copy public link</button>
              <button class="wo-act" onclick="OrdersPage.duplicateOrder('${id}');OrdersPage.toggleActions(event)">${icon('copy',14)} Duplicate</button>
              <button class="wo-act" onclick="OrdersPage.createPoModal('${id}');OrdersPage.toggleActions(event)">${icon('box',14)} Create purchase order</button>
              <button class="wo-act wo-act-danger" onclick="OrdersPage.archiveModal('${id}');OrdersPage.toggleActions(event)">${icon('trash',14)} Delete</button>
            </div>
          </div>
          <button class="modal-close" onclick="Modal.close()">×</button>
        </div>
      </div>
      <div class="modal-body">
        ${(o.client?.name || o.device) ? `<div class="wo-hub-id">
          ${o.client?.name ? `<div class="wo-hub-id-chip"><div class="wo-hub-av">${(o.client.name||'?').trim().charAt(0).toUpperCase()}</div>${this._escAttr(o.client.name)}</div>` : ''}
          ${o.device ? `<div class="wo-hub-id-chip">${icon('device',12)} ${this._deviceName(o.device)}</div>` : ''}
          ${o.scheduled_for ? `<div class="wo-hub-id-chip">${icon('calendar',12)} ${fmt.date(o.scheduled_for)}</div>` : ''}
        </div>` : ''}
        <div class="wo-tabs">
          <button class="wo-tab active" onclick="OrdersPage.woTab(event,'general')">${icon('file',13)} General info</button>
          <button class="wo-tab" onclick="OrdersPage.woTab(event,'lines')">${icon('wrench',13)} Services &amp; Products${o.rec_unread ? `<span class="msg-dot msg-dot-tab msg-dot-rec">${o.rec_unread}</span>` : ''}</button>
          <button class="wo-tab" onclick="OrdersPage.woTab(event,'checklists')">${icon('check',13)} Checklists</button>
          <button class="wo-tab" data-perm="can_view_invoices_tab" onclick="OrdersPage.woTab(event,'invoices')">${icon('coin',13)} Invoices &amp; payments</button>
          <button class="wo-tab" onclick="OrdersPage.woTab(event,'files')">${icon('message',13)} Activity${o.client_msg_unread ? `<span class="msg-dot msg-dot-tab">${o.client_msg_unread}</span>` : ''}</button>
        </div>

        <div class="wo-body-row"><div class="wo-panels">
        <div class="wo-tab-panel active" data-panel="general">
          ${o.converted_order_id ? `<div class="conv-banner conv-done">${icon('check',14)} This estimate was converted to work order <button class="conv-link" onclick="OrdersPage.viewOrder('${o.converted_order_id}')">${o.converted_order_number||'open'} →</button></div>` : ''}
          ${o.converted_from_id ? `<div class="conv-banner conv-from">${icon('file',14)} Created from estimate <button class="conv-link" onclick="OrdersPage.viewOrder('${o.converted_from_id}')">${o.converted_from_number||'open'} →</button></div>` : ''}
          <div style="display:flex;justify-content:flex-end;margin-bottom:10px">
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.aiSummary('${id}')">${icon('bolt',13)} AI summarize</button>
          </div>
          <div id="wo-ai-summary"></div>
          ${OrdersPage._woOverview(o, id)}
        </div>

        <div class="wo-tab-panel" data-panel="lines">
          <div class="wo-lines-head">
            <span style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em">Services &amp; products</span>
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.aiDraftLines('${id}')">${icon('bolt',13)} Draft from complaint</button>
          </div>
          <!-- Build 144dj — inline Orderry-style adder: pick a technician, search a service/product
               or type custom, and the line drops straight into the table under that tech. -->
          <div class="wo-addbar">
            <select id="wo-add-tech" class="field-select wo-add-tech">
              <option value="">— No technician —</option>
              ${(this._employees||[]).map(e=>`<option value="${e.id}">${(e.name||'').replace(/</g,'&lt;')}</option>`).join('')}
            </select>
            <div class="wo-addsearch">
              <input id="wo-add-search" class="field-input" autocomplete="off"
                placeholder="Name, barcode, code, SKU — search, or type to add custom…"
                oninput="OrdersPage.woSearch('${id}')"
                onfocus="OrdersPage.woSearch('${id}')"
                onkeydown="if(event.key==='Enter'){event.preventDefault();OrdersPage.woAddCustom('${id}')}">
              <div id="wo-add-results" class="wo-add-results"></div>
            </div>
          </div>
        ${o.lines?.length ? `
          <div class="wo-jobs">${OrdersPage._woJobCards(o, id)}</div>
          ${this._invoiceTotals(o)}
          ` : '<div class="wo-empty-card">No services or products yet.<div class="wo-empty-sub">Pick a technician above, then search a service or product — or type a custom line.</div></div>'}

          <div class="wo-recs-sec">
            <div class="wo-lines-head" style="margin-top:6px">
              <span style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em">${icon('clipboard',12)} Recommendations for the customer</span>
              <div style="display:flex;gap:8px">
                <button class="btn btn-glass btn-sm" data-perm="can_publish_recommend" onclick="OrdersPage.publishAllRecs('${id}')">${icon('external-link',13)} Publish all drafts</button>
                <button class="btn btn-primary btn-sm" data-perm="can_recommend" onclick="OrdersPage.addRecModal('${id}')">+ Recommendation</button>
              </div>
            </div>
            <div id="wo-recs-${id}" class="wo-recs"></div>
          </div>

          <div class="wo-notes-sec">
            <div class="field"><label class="field-label">Technician notes</label>
              <textarea class="field-input" id="wo-tech-notes" rows="2" placeholder="Working notes for the shop…">${(o.tech_notes||'').replace(/</g,'&lt;')}</textarea></div>
            <div class="field"><label class="field-label">Conclusion / client recommendations</label>
              <textarea class="field-input" id="wo-conclusion" rows="2" placeholder="What you told the customer…">${(o.conclusion||'').replace(/</g,'&lt;')}</textarea></div>
            <div style="display:flex;justify-content:flex-end"><button class="btn btn-glass btn-sm" onclick="OrdersPage.saveNotes('${id}')">Save notes</button></div>
          </div>
        </div>

        <div class="wo-tab-panel" data-panel="checklists">
          <div id="wo-cl-host" data-order="${id}" data-type="${o.type_id || ''}"></div>
        </div>

        <div class="wo-tab-panel" data-panel="invoices">
          ${OrdersPage._woBalanceHero(o, id)}
          <div class="wo-lines-head">
            <span style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em">Invoices &amp; payments</span>
            <div style="display:flex;gap:8px">
              <button class="btn btn-glass btn-sm" onclick="OrdersPage.prepayModal('${id}')">${icon('wallet',13)} Take prepayment</button>
              <button class="btn btn-glass btn-sm" onclick="OrdersPage.maybePaymentWindow('${id}',true)">${icon('coin',13)} Take payment</button>
              <button class="btn btn-primary btn-sm" onclick="OrdersPage.invoiceModal('${id}')">+ Invoice</button>
            </div>
          </div>
          <div class="wo-redeem-bar">
            <span class="wo-redeem-lbl">Redeem</span>
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.applyCouponModal('${id}')">${icon('receipt',13)} Coupon</button>
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.redeemGiftModal('${id}')">${icon('card',13)} Gift card</button>
            <button class="btn btn-glass btn-sm" onclick="OrdersPage.redeemPointsModal('${id}')">${icon('crown',13)} Points</button>
          </div>
          <div id="wo-invoices-${id}" class="wo-invoices"></div>
        </div>

        <div class="wo-tab-panel" data-panel="files">
          ${o.allow_staff_reply !== false ? `
          <div class="reply-box" data-perm="can_reply_customer">
            <div class="reply-h">${icon('message',13)} Reply to the customer <span class="reply-sub">shows on their tracking page</span></div>
            <textarea id="reply-body-${id}" class="field-input" rows="2" placeholder="Type a reply the customer will see…" style="width:100%;resize:vertical"></textarea>
            <div style="display:flex;justify-content:flex-end;margin-top:8px">
              <button class="btn btn-primary btn-sm" onclick="OrdersPage.sendCustomerReply('${id}')">Send reply</button>
            </div>
          </div>` : ''}
          <div id="order-feed"></div>
          ${o.status_logs?.length ? `
          <div class="wo-lines-head" style="margin-top:18px">
            <span style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em">${icon('refresh',12)} Status history</span>
          </div>
          <div class="card-sm" style="padding:8px 14px">
            <div class="timeline">
              ${o.status_logs.map(l=>`<div class="tl-item">
                <div class="tl-dot"></div>
                <div><span class="tl-from">${l.from||'created'}</span> → <span class="tl-to">${l.to}</span>${l.note?`<div style="font-size:11px;color:var(--tx-3);font-style:italic;margin-top:2px">${l.note}</div>`:''}
                <div class="tl-time">${fmt.datetime(l.at)}</div></div>
              </div>`).join('')}
            </div>
          </div>` : ''}
        </div>
        </div><aside class="wo-rail">${OrdersPage._woRail(o)}</aside></div>
      </div>
      <div class="wo-hub-footer">
        <div class="wo-hub-footer-left">
          <div class="wo-hub-autosave"><span class="wo-hub-autosave-dot"></span> Autosaved</div>
          <button class="btn btn-glass btn-sm" onclick="OrdersPage.copyPublicLink('${id}')">${icon('external-link',13)} Tracking link</button>
          <button class="btn btn-glass btn-sm" onclick="OrdersPage.printDocModal('${id}')">${icon('file',13)} Print</button>
        </div>
        <div class="wo-hub-footer-right">
          <button class="btn btn-danger btn-sm" onclick="OrdersPage.deleteOrder('${id}')">${icon('trash',13)} Delete</button>
          <button class="btn btn-glass" onclick="Modal.close()">Close</button>
          <button class="btn btn-primary" onclick="OrdersPage.chooseStatus('${id}','done',this);Modal.close()">${icon('check',14)} Mark Done &amp; Close</button>
        </div>
      </div>`, 'modal-side-wide');
    Feed.mount('order', id, 'order-feed');
    this._loadOrderInvoices(id);
    this._loadRecs(id);
  },

  // Build 144el — Blend layout: a persistent right rail with the customer, the
  // money summary, and a short activity slice. Reads the already-loaded order; no
  // extra fetch. Totals fall back gracefully across the fields the API may send.
  _woRail(o) {
    const cur = (n) => fmt.currency(Number(n) || 0);
    const sub = o.subtotal != null ? o.subtotal : ((o.labour_total || 0) + (o.parts_total || 0));
    const total = o.total != null ? o.total : sub;
    const paid = o.prepayment_total || o.amount_paid || 0;
    const due = Math.max(total - paid, 0);
    const logs = (o.status_logs || []).slice(-4).reverse();
    const c = o.client || {};
    const dev = o.device ? OrdersPage._deviceName(o.device) : null;
    return `
      <div class="wo-rail-card">
        <div class="wo-rail-cust">
          <div class="wo-rail-av">${(c.name || 'W').trim().charAt(0).toUpperCase()}</div>
          <div style="min-width:0">
            <div class="wo-rail-name">${c.id ? `<span class="wo-link" onclick="OrdersPage.openClient('${c.id}')">${c.name}</span>` : (c.name || 'Walk-in')}</div>
            ${c.phone ? `<div class="wo-rail-sub">${c.phone}</div>` : ''}
          </div>
        </div>
        ${dev ? `<div class="wo-rail-sub" style="margin-top:8px">${dev}${o.device && o.device.serial ? ' · SN ' + o.device.serial : ''}</div>` : ''}
        ${c.balance ? `<div class="wo-bal ${c.balance > 0 ? 'owe' : 'credit'}" style="margin-top:8px">${c.balance > 0 ? 'owes us' : 'in credit'} ${cur(Math.abs(c.balance))}</div>` : ''}
      </div>
      <div class="wo-rail-card">
        <div class="wo-rail-tot"><span>Subtotal</span><b class="tbl-num">${cur(sub)}</b></div>
        ${o.discount ? `<div class="wo-rail-tot"><span>Discount</span><b class="tbl-num">-${cur(o.discount)}</b></div>` : ''}
        ${o.tax ? `<div class="wo-rail-tot"><span>Tax</span><b class="tbl-num">${cur(o.tax)}</b></div>` : ''}
        <div class="wo-rail-tot big"><span>Total</span><b class="tbl-num">${cur(total)}</b></div>
        ${paid ? `<div class="wo-rail-tot"><span>Paid</span><b class="tbl-num">${cur(paid)}</b></div>
        <div class="wo-rail-tot" style="color:var(--red)"><span>Balance due</span><b class="tbl-num">${cur(due)}</b></div>` : ''}
        <button class="btn btn-primary" style="width:100%;justify-content:center;margin-top:10px" onclick="OrdersPage.woTab({target:document.querySelector('.wo-tab[onclick*=invoices]')},'invoices')">${icon('coin',13)} Invoices &amp; payments</button>
      </div>
      ${logs.length ? `<div class="wo-rail-card">
        <div class="wo-rail-h">Recent activity</div>
        <div class="wo-rail-feed">
          ${logs.map(l => `<div class="wo-rail-fi"><span class="wo-rail-fdot"></span><div><div style="font-size:12.5px"><span style="color:var(--tx-3)">${l.from || 'created'}</span> → <b>${l.to}</b></div><div class="wo-rail-sub">${fmt.datetime(l.at)}</div></div></div>`).join('')}
        </div>
      </div>` : ''}`;
  },

  // 144cv — Tekmetric-style status control at the very top of the drawer: a
  // click-to-open pill that lists every status. Changing it patches the order and
  // updates the pill in place (no drawer re-render, so a triggered payment window
  // survives). Replaces the old bottom "Move to status" chip row.
  _woStatusPill(o, id) {
    const statuses = this._STATUSES;
    return `<div class="wo-status-wrap" style="position:relative;display:inline-block">
      <button class="wo-status-pill" style="--sc:${this._statusColor(o.status)}" onclick="OrdersPage.toggleStatusMenu(event)">
        <span class="wo-status-dot"></span><span class="wo-status-label">${this._statusName(o.status)}</span>${o.is_estimate ? '<span class="wo-status-est">Estimate</span>' : ''}${icon('chevron-down', 12)}
      </button>
      <div class="wo-status-menu" style="display:none">
        ${statuses.map(s => `<button class="wo-status-opt${o.status === s ? ' on' : ''}" data-s="${s}" onclick="OrdersPage.chooseStatus('${id}','${s}',this)">${this._statusName(s)}</button>`).join('')}
      </div>
    </div>`;
  },

  toggleStatusMenu(e) {
    e.stopPropagation();
    const open = document.querySelector('.wo-status-menu.portaled');
    if (open) { open.remove(); return; }
    const btn = e.currentTarget.closest('.wo-status-pill') || e.currentTarget;
    const wrap = btn.closest('.wo-status-wrap');
    const menu = wrap?.querySelector('.wo-status-menu');
    if (!menu) return;
    const r = btn.getBoundingClientRect();
    menu.classList.add('portaled');
    Object.assign(menu.style, {
      position: 'fixed', top: (r.bottom + 6) + 'px', left: r.left + 'px', right: 'auto',
      zIndex: '99999', display: 'block',
    });
    document.body.appendChild(menu);
    const close = (ev) => {
      if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('mousedown', close); }
    };
    setTimeout(() => document.addEventListener('mousedown', close), 0);
  },

  async chooseStatus(id, status, optEl) {
    const menu = optEl.closest('.wo-status-menu');
    const pill = document.querySelector('.wo-status-pill');
    await this.setStatus(id, status);
    if (pill) {
      pill.style.setProperty('--sc', this._statusColor(status));
      const lbl = pill.querySelector('.wo-status-label');
      if (lbl) lbl.textContent = this._statusName(status);
    }
    if (menu && menu.classList.contains('portaled')) menu.remove();
  },

  // 144cp — Orderry-style people bar on the Overview: the WO Type, the Manager
  // (owner) and the Specialist (assignee). Both people are clickable to reassign;
  // the assignee change notifies the newly-assigned technician (backend).
  _woPeopleBar(o, id) {
    const av = (p) => `<span class="wo-person-av">${(p.name || '?').trim().charAt(0).toUpperCase()}</span>`;
    const chip = (role, label, p) => `
      <div class="wo-person" onclick="OrdersPage.pickPerson('${id}','${role}')" title="Change ${label.toLowerCase()}">
        <div class="wo-person-lbl">${label}</div>
        <div class="wo-person-v">${p ? `${av(p)}<span class="wo-person-nm">${this._escAttr(p.name)}</span>` : '<span class="wo-person-empty">Assign</span>'} ${icon('chevron-down', 11)}</div>
      </div>`;
    return `<div class="wo-people">
      <div class="wo-person wo-person-type"><div class="wo-person-lbl">Type</div><div class="wo-person-v"><b>${o.is_estimate ? 'Estimate' : 'Work order'}</b></div></div>
      ${chip('manager', 'Manager', o.manager)}
      ${chip('assigned_to', 'Specialist', o.assigned_to)}
    </div>`;
  },

  // 144cw — Repair Canvas Overview, matched to the prototype: a Type · Manager ·
  // Specialist · Schedule strip, two rich clickable cards (Client → profile, Asset →
  // info/history), then the complaint. Keeps every handler (pickPerson, openClient,
  // openAsset, changeAssetPhoto, rescheduleModal) and the multi-tech data behind it.
  _woOverview(o, id) {
    const M = (n) => this._docCur ? fmt.money(Number(n) || 0, this._docCur) : fmt.currency(Number(n) || 0);
    const initial = (s) => (s || '?').trim().charAt(0).toUpperCase();
    const person = (role, label, p) => `
      <div class="rc-strip-item">
        <div class="rc-k">${label}</div>
        <button class="rc-chip${p ? '' : ' rc-chip-empty'}" onclick="OrdersPage.pickPerson('${id}','${role}')" title="Change ${label.toLowerCase()}">
          <span class="rc-ppl${p ? '' : ' rc-ppl-empty'}">${p ? initial(p.name) : icon('user', 12)}</span>
          <span class="rc-person-nm">${p ? this._escAttr(p.name) : 'Assign'}</span>${icon('chevron-down', 10)}
        </button>
      </div>`;
    const sched = o.scheduled_for ? fmt.datetime(o.scheduled_for) : 'Not scheduled';

    const c = o.client || {};
    const clientRows = [
      c.phone ? ['Mobile', this._escAttr(c.phone)] : null,
      (c.balance ? ['Balance', `<b style="color:${c.balance > 0 ? 'var(--red)' : 'var(--green)'}">${c.balance > 0 ? 'owes us' : 'in credit'} ${M(Math.abs(c.balance))}</b>`] : null),
    ].filter(Boolean);

    const d = o.device || null;
    const assetRows = d ? [
      d.serial ? ['Serial', this._escAttr(d.serial)] : null,
      d.imei ? ['IMEI', this._escAttr(d.imei)] : null,
      d.vin ? ['VIN', this._escAttr(d.vin)] : null,
      d.visit_count ? ['Prior visits', `<span class="wo-link" onclick="event.stopPropagation();OrdersPage.openAsset('${d.id}')">${d.visit_count} visit${d.visit_count > 1 ? 's' : ''} ↗</span>`] : null,
      o.warranty_until ? ['Warranty', `<b style="color:var(--green)">${fmt.date(o.warranty_until)}</b>`] : null,
    ].filter(Boolean) : [];

    const rowsHtml = (rows, empty) => rows.length
      ? `<div class="rc-sysc-b">${rows.map(([k, v]) => `<div class="rc-row"><span class="sub">${k}</span><span>${v}</span></div>`).join('')}</div>`
      : `<div class="rc-sysc-b"><div class="rc-row rc-row-empty">${empty}</div></div>`;

    // v2 — Stitch fidelity: SLA banner + identity badge pills
    const _vipPill = (c.is_vip || (c.priority || 0) >= 2) ? '<span class="v2-pill v2-pill-vip">Verified VIP</span>' : '';
    const _visits = (c.visit_count != null ? c.visit_count : c.order_count);
    const _visitsPill = _visits ? `<span class="v2-pill v2-pill-visits">${_visits} prior visit${_visits > 1 ? 's' : ''}</span>` : '';
    const _warrantyPill = o.warranty_until ? '<span class="v2-pill v2-pill-warranty">Warranty active</span>' : '';
    const _slaWhen = o.due_date || o.scheduled_for;
    const _slaBanner = (o.priority >= 1 || _slaWhen) ? `
      <div class="v2-sla">
        <div class="v2-sla-ic">${icon('clock', 16)}</div>
        <div style="flex:1;min-width:0">
          <div class="v2-sla-tt">${o.priority >= 2 ? 'Priority VIP order' : o.priority === 1 ? 'Urgent order — expedited' : 'Scheduled order'}</div>
          <div class="v2-sla-sub">${_slaWhen ? 'Target delivery: ' + fmt.datetime(_slaWhen) : 'No due date set yet'}</div>
        </div>
        ${o.priority >= 1 ? '<span class="v2-sla-flag">Priority high</span>' : ''}
      </div>` : '';

    return `
    ${_slaBanner}
    <div class="rc-strip">
      <div class="rc-strip-item"><div class="rc-k">Type</div><span class="rc-chip rc-chip-static">${o.is_estimate ? 'Estimate' : 'Work order'}</span></div>
      ${person('manager', 'Manager', o.manager)}
      ${person('assigned_to', 'Specialist', o.assigned_to)}
      <div class="rc-strip-item rc-strip-sched">
        <div class="rc-k">Schedule</div>
        <button class="rc-chip" title="${o.scheduled_for ? 'Reschedule' : 'Schedule'}" onclick="OrdersPage.rescheduleModal('${id}')">${icon('calendar', 12)} <span class="rc-person-nm">${sched}</span></button>
      </div>
    </div>

    <div class="rc-cards2">
      <div class="rc-sysc${c.id ? ' rc-click' : ''}" ${c.id ? `onclick="OrdersPage.openClient('${c.id}')" title="Open client profile"` : ''}>
        <div class="rc-sysc-h">
          <span class="rc-ppl rc-ppl-lg">${initial(c.name || 'W')}</span>
          <div style="flex:1;min-width:0"><div class="rc-sysc-name">${c.id ? `<span class="wo-link">${this._escAttr(c.name)} ${icon('external-link', 12)}</span>` : (this._escAttr(c.name) || 'Walk-in')}${_visitsPill}${_vipPill}</div><div class="sub">Client${c.id ? ' · opens profile &amp; history' : ''}</div></div>
        </div>
        ${rowsHtml(clientRows, c.id ? 'No contact details on file' : 'Walk-in customer — no saved profile')}
      </div>

      <div class="rc-sysc${d && d.id ? ' rc-click' : ''}" ${d && d.id ? `onclick="OrdersPage.openAsset('${d.id}')" title="Open asset info + history"` : ''}>
        <div class="rc-sysc-h">
          ${d ? `<div class="asset-photo rc-asset-ph${d.image_data ? '' : ' asset-photo-empty'}" onclick="event.stopPropagation();OrdersPage.changeAssetPhoto('${d.id}','${id}')" title="${d.image_data ? 'Change photo' : 'Add photo'}">${d.image_data ? `<img src="${d.image_data}" alt="asset">` : icon('camera', 18)}</div>` : `<span class="rc-ppl rc-ppl-lg">?</span>`}
          <div style="flex:1;min-width:0"><div class="rc-sysc-name">${d && d.id ? `<span class="wo-link">${this._deviceName(d)} ${icon('external-link', 12)}</span>` : this._deviceName(d)}${_warrantyPill}</div><div class="sub">Asset${d && d.id ? ' · opens info &amp; history' : ''}</div></div>
        </div>
        ${rowsHtml(assetRows, d ? 'No serial or warranty recorded' : 'No device linked to this order')}
      </div>
    </div>

    <div class="rc-complaint"><div class="rc-k">Complaint</div><div style="font-weight:600;line-height:1.6">${o.complaint || '<span style="color:var(--tx-4);font-weight:500">No complaint recorded yet.</span>'}</div></div>

    <div class="rc-stats">
      <div class="rc-stat"><div class="rc-k">Created</div><b>${fmt.reltime(o.created_at)}</b></div>
      <div class="rc-stat"><div class="rc-k">Warranty</div><b style="${o.warranty_until ? 'color:var(--green)' : 'color:var(--tx-4)'}">${o.warranty_until ? fmt.date(o.warranty_until) : 'None'}</b></div>
      <div class="rc-stat"><div class="rc-k">Prior visits</div><b>${(d && d.visit_count) || 0}</b></div>
      <div class="rc-stat"><div class="rc-k">Client balance</div><b style="color:${c.balance > 0 ? 'var(--red)' : c.balance < 0 ? 'var(--green)' : 'var(--tx-2)'}">${c.balance ? (c.balance > 0 ? 'owes ' : 'credit ') + M(Math.abs(c.balance)) : '—'}</b></div>
    </div>

    <div class="rc-notes">
      <div class="rc-k">Notes &amp; diagnosis</div>
      <textarea class="field-input" id="wo-ov-tech" rows="2" placeholder="Working notes for the shop…">${(o.tech_notes || '').replace(/</g, '&lt;')}</textarea>
      <textarea class="field-input" id="wo-ov-concl" rows="2" placeholder="Conclusion the customer sees…" style="margin-top:8px">${(o.conclusion || '').replace(/</g, '&lt;')}</textarea>
      <div style="display:flex;justify-content:flex-end;margin-top:8px"><button class="btn btn-glass btn-sm" onclick="OrdersPage.saveOvNotes('${id}')">Save notes</button></div>
    </div>

    ${o.custom_data && Object.keys(o.custom_data).length ? `
      <h3 class="rc-sec">Type details</h3>
      <div class="rc-cards2">
        ${Object.entries(o.custom_data).filter(([k, v]) => v !== '' && v !== false && v != null).map(([k, v]) => `<div class="rc-mini"><div class="rc-k">${k.replace(/_\d+$/, '').replace(/_/g, ' ')}</div><b>${v === true ? 'Yes' : v}</b></div>`).join('') || ''}
      </div>` : ''}
    <div class="sub" style="margin-top:12px;padding-left:2px">These are <b>system fields</b> from the Forms editor — reorder or hide any from print; the record still shows them here.</div>`;
  },

  async pickPerson(orderId, role) {
    await this._loadEmps();
    const field = role === 'manager' ? 'manager_id' : 'assigned_to_id';
    const label = role === 'manager' ? 'Manager' : 'Specialist (assignee)';
    const opts = `<option value="">— None —</option>` +
      (this._employees || []).map(e => `<option value="${e.id}">${(e.name || '').replace(/</g, '&lt;')}</option>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${label}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body"><div class="field"><label class="field-label">${label}</label>
        <select class="field-select" id="wo-person-sel">${opts}</select></div></div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.savePerson('${orderId}','${field}')">Save</button></div>`, 'modal-narrow');
  },

  async savePerson(orderId, field) {
    const v = document.getElementById('wo-person-sel').value;
    try { await patch(`/api/orders/${orderId}/assign`, { [field]: v }); Modal.close(); this.viewOrder(orderId); }
    catch (e) { Toast.error(e.message); }
  },

  // 144dc — save tech notes + conclusion from Overview tab
  async saveOvNotes(orderId) {
    const tech = (document.getElementById('wo-ov-tech') || {}).value || '';
    const concl = (document.getElementById('wo-ov-concl') || {}).value || '';
    try {
      await patch(`/api/orders/${orderId}`, { tech_notes: tech, conclusion: concl });
      Toast.ok('Notes saved');
    } catch (e) { Toast.error(e.message); }
  },

  // 144cp — balance hero atop the Invoices & payments tab: what's owed, big and
  // clear, with a single Collect-payment CTA. Reuses order totals; no extra fetch.
  _woBalanceHero(o, id) {
    const M = (n) => this._docCur ? fmt.money(Number(n) || 0, this._docCur) : fmt.currency(Number(n) || 0);
    const total = o.total != null ? o.total : ((o.labour_total || 0) + (o.parts_total || 0));
    const paid = o.prepayment_total || o.amount_paid || 0;
    const due = Math.max(total - paid, 0);
    return `<div class="wo-bal-hero">
      <div>
        <div class="k">Balance due</div>
        <div class="amt${due > 0 ? ' due' : ''} tbl-num">${M(due)}</div>
        <div style="font-size:12px;color:var(--tx-3);margin-top:2px">of ${M(total)}${paid ? ` · paid ${M(paid)}` : ''}</div>
      </div>
      ${due > 0 ? `<button class="btn btn-primary" onclick="OrdersPage.maybePaymentWindow('${id}',true)">${icon('coin', 14)} Collect payment</button>` : `<span class="badge b-done">${icon('check', 13)} Settled</span>`}
    </div>`;
  },

  async _loadOrderInvoices(id) {
    const el = document.getElementById(`wo-invoices-${id}`);
    if (!el) return;
    try {
      const data = await get(`/api/cash/orders/${id}/invoices`);
      if (!data.invoices.length && !data.payments.length) {
        el.innerHTML = '<div style="color:var(--tx-4);font-size:13px;padding:22px;text-align:center">No invoices or payments yet. Use <b>+ Invoice</b> to bill this order, or <b>Take prepayment</b>.</div>';
        return;
      }
      const payRows = data.payments.map(p =>
        `<div class="wo-pay-row"><span>${p.kind === 'prepayment' ? 'Prepayment' : p.kind === 'refund' ? 'Refund' : 'Payment'} · ${p.method}</span><span class="${p.kind === 'refund' ? 'neg' : 'pos'}">${p.kind === 'refund' ? '−' : ''}${fmt.currency(p.amount)}</span></div>`).join('');
      const invRows = data.invoices.map(i =>
        `<div class="wo-pay-row"><span>${i.invoice_number} · <span class="badge b-${i.status === 'paid' ? 'done' : 'new'}">${i.status}</span></span><span>${fmt.currency(i.total)}</span></div>`).join('');
      el.innerHTML = `
        <div class="wo-inv-block">
          ${data.invoices.length ? `<div class="wo-inv-h">Invoices</div>${invRows}` : ''}
          ${data.payments.length ? `<div class="wo-inv-h">Payments</div>${payRows}` : ''}
        </div>`;
    } catch (e) { el.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async invoiceModal(id) {
    let o;
    try { o = await get(`/api/orders/${id}`); } catch (e) { return Toast.error(e.message); }
    const lines = o.lines || [];
    if (!lines.length) return Toast.error('Add services or products before invoicing');
    const cur = this._docCur;
    const M = (n) => cur ? fmt.money(n, cur) : fmt.currency(n);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New invoice · ${o.order_number}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:12px">Choose which lines to bill. Unchecked lines can be billed on a later invoice.</div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em">Lines</div>
          <label class="cash-check" style="font-size:12px"><input type="checkbox" id="inv-all" checked onchange="OrdersPage._invToggleAll(this.checked)"> Select all</label>
        </div>
        <div class="card-sm" style="padding:2px 0;margin-bottom:14px">
          <table style="width:100%;font-size:12.5px;border-collapse:collapse">
            <tbody>${lines.map(l=>`<tr style="border-top:1px solid rgba(255,255,255,.45)">
              <td style="padding:9px 12px;width:26px"><input type="checkbox" class="inv-line" value="${l.id}" checked onchange="OrdersPage._invRecalc()"></td>
              <td style="padding:9px 6px;font-weight:600">${l.description}</td>
              <td style="padding:9px 6px;text-align:center;color:var(--tx-3)">×${l.qty}</td>
              <td style="padding:9px 12px;text-align:right;font-weight:800">${M(l.total)}</td>
            </tr>`).join('')}</tbody>
          </table>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Payment method (for reference)</div>
        <div class="type-chips" id="inv-method">
          ${[['cash','Cash'],['cashless','Cashless'],['card','Credit card']].map(([v,l],i)=>`<div class="type-chip${i===0?' active':''}" data-m="${v}" onclick="OrdersPage._invMethod('${v}',this)" style="--tc:#4f8ef7"><span class="type-chip-dot" style="background:#4f8ef7"></span>${l}</div>`).join('')}
        </div>
        <div style="display:flex;justify-content:flex-end;font-weight:800;font-size:15px;margin-top:16px">Invoice total: <span id="inv-total" style="margin-left:10px;color:var(--blue)">${M(o.total||0)}</span></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.submitInvoice('${id}')">Create invoice</button>
      </div>`, 'modal-wide');
    this._invLineMap = {}; lines.forEach(l => this._invLineMap[l.id] = l.total || 0);
    this._invMethodVal = 'cash';
  },

  _invToggleAll(on) {
    document.querySelectorAll('.inv-line').forEach(c => { c.checked = on; });
    this._invRecalc();
  },
  _invMethod(v, el) {
    this._invMethodVal = v;
    document.querySelectorAll('#inv-method .type-chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
  },
  _invRecalc() {
    const cur = this._docCur;
    const M = (n) => cur ? fmt.money(n, cur) : fmt.currency(n);
    let sum = 0;
    document.querySelectorAll('.inv-line:checked').forEach(c => { sum += this._invLineMap[c.value] || 0; });
    const el = document.getElementById('inv-total'); if (el) el.textContent = M(sum);
  },
  async submitInvoice(id) {
    const ids = Array.from(document.querySelectorAll('.inv-line:checked')).map(c => c.value);
    if (!ids.length) return Toast.error('Select at least one line');
    try {
      const r = await post(`/api/cash/orders/${id}/invoices`, { line_ids: ids });
      Toast.success(`Invoice ${r.invoice_number} created`);
      Modal.close();
      await this._loadOrderInvoices(id);
    } catch (e) { Toast.error(e.message || 'Could not create invoice'); }
  },

  async prepayModal(orderId) {
    let accounts = [];
    try { accounts = (await get('/api/cash/accounts')).items; } catch {}
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Take prepayment</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Amount</label><input class="field-input" type="number" id="pp-amount" min="0" placeholder="0.00"></div>
        <div class="field"><label class="field-label">To cashbox</label>
          <select class="field-select" id="pp-account">${accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('')}</select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.submitPrepay('${orderId}')">Take prepayment</button>
      </div>`, 'modal-narrow');
  },

  async submitPrepay(orderId) {
    const amount = parseFloat(document.getElementById('pp-amount').value);
    if (!amount || amount <= 0) return Toast.error('Enter an amount');
    const account_id = document.getElementById('pp-account').value;
    try {
      await post(`/api/cash/orders/${orderId}/prepay`, { amount, account_id });
      Toast.success('Prepayment recorded');
      Modal.close();
      this._loadOrderInvoices(orderId);
    } catch (e) { Toast.error(e.message); }
  },

  woTab(e, panel) {
    const modal = e.target.closest('.modal-body');
    modal.querySelectorAll('.wo-tab').forEach(t => t.classList.remove('active'));
    e.target.classList.add('active');
    modal.querySelectorAll('.wo-tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === panel));
    if (panel === 'lines' && this._recOrderId) this._clearRecDot(this._recOrderId, e.target);
    if (panel === 'checklists' && typeof WOChecklist !== 'undefined') {
      const h = modal.querySelector('#wo-cl-host');
      if (h) WOChecklist.load(h.dataset.order, h.dataset.type);
    }
  },

  _clearRecDot(id, tabBtn) {
    const dot = tabBtn && tabBtn.querySelector('.msg-dot-tab');
    if (!dot) return;                      // nothing unread
    dot.remove();
    post(`/api/orders/${id}/recommendations/seen`, {}).catch(() => {});
  },

  /* ── 144bi: jump from the work order to the client / asset profile ── */
  openClient(cid) {
    if (typeof ClientsPage !== 'undefined' && ClientsPage.viewClient) { Modal.close(); Router.go('clients'); setTimeout(() => ClientsPage.viewClient(cid), 60); }
  },
  openAsset(aid) {
    if (typeof AssetsPage !== 'undefined' && AssetsPage.viewAsset) { Modal.close(); Router.go('assets'); setTimeout(() => AssetsPage.viewAsset(aid), 60); }
  },

  /* ── Recommendations (144bd): upsell checklist the customer approves ── */
  _recOrderId: null,

  async _loadRecs(id) {
    this._recOrderId = id;
    const el = document.getElementById(`wo-recs-${id}`);
    if (!el) return;
    try {
      const data = await get(`/api/orders/${id}/recommendations`);
      this._renderRecs(id, data.items || []);
    } catch (e) {
      el.innerHTML = '<div class="wo-recs-empty">Could not load recommendations.</div>';
    }
  },

  _recStatusMeta(s) {
    return ({
      draft:    { cls: 'rec-draft',    label: 'Draft · internal' },
      sent:     { cls: 'rec-sent',     label: 'Sent to client' },
      approved: { cls: 'rec-approved', label: 'Approved' },
      declined: { cls: 'rec-declined', label: 'Declined' },
    })[s] || { cls: 'rec-draft', label: s };
  },

  _renderRecs(id, items) {
    const el = document.getElementById(`wo-recs-${id}`);
    if (!el) return;
    if (!items.length) {
      el.innerHTML = `<div class="wo-recs-empty">${icon('clipboard',26)}<div>No recommendations yet.</div>
        <div class="wo-recs-empty-sub">Suggest extra services or products — the customer approves them from their tracking page.</div></div>`;
      return;
    }
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    el.innerHTML = items.map(r => {
      const m = this._recStatusMeta(r.status);
      const decided = r.status === 'approved' || r.status === 'declined';
      const canPull = r.status === 'sent';
      return `<div class="rec-card">
        <div class="rec-card-main">
          ${r.media_data ? `<img class="rec-thumb" src="${esc(r.media_data)}" alt="">` : `<div class="rec-thumb rec-thumb-empty">${icon('camera',16)}</div>`}
          <div class="rec-body">
            <div class="rec-top">
              <div class="rec-title">${esc(r.title)}</div>
              <span class="rec-badge ${m.cls}">${m.label}</span>
            </div>
            <div class="rec-price">${fmt.currency(r.line_total)}${r.qty && r.qty !== 1 ? ` · ${r.qty} × ${fmt.currency(r.unit_price)}` : ''}<span class="rec-src">${r.ref_id ? icon('link', 11) + ' linked to catalog' : 'manual'}</span>${r.author_name ? `<span class="rec-src">${icon('user', 11)} ${esc(r.author_name)}</span>` : ''}</div>
            ${r.note ? `<div class="rec-note">${icon('message',12)} ${esc(r.note)}</div>` : ''}
          </div>
        </div>
        <div class="rec-actions">
          ${!decided ? `<button class="rec-mini" onclick="OrdersPage.editRecModal('${id}','${r.id}')" title="Edit">${icon('bolt',13)}</button>` : ''}
          ${r.status === 'draft' ? `<button class="rec-mini rec-mini-go" data-perm="can_publish_recommend" onclick="OrdersPage.publishRec('${id}','${r.id}')">Publish</button>` : ''}
          ${canPull ? `<button class="rec-mini" data-perm="can_publish_recommend" onclick="OrdersPage.unpublishRec('${id}','${r.id}')">Unpublish</button>` : ''}
          ${!decided ? `<button class="rec-mini rec-mini-del" onclick="OrdersPage.deleteRec('${id}','${r.id}')" title="Delete">${icon('trash',13)}</button>` : ''}
        </div>
      </div>`;
    }).join('');
    if (typeof App !== 'undefined' && App.applyPermissions) App.applyPermissions(el);
  },

  _recFormHtml(r, opts) {
    r = r || {}; opts = opts || {};
    const pick = opts.pick !== false && !r.id;   // show catalog search on add, not edit
    return `
      ${pick ? `
      <div class="rec-pick">
        <div class="rec-pick-chips">
          <button type="button" class="rec-chip active" data-f="all" onclick="OrdersPage._recFilter('all',this)">${icon('search',12)} All</button>
          <button type="button" class="rec-chip" data-f="product" onclick="OrdersPage._recFilter('product',this)">${icon('box',12)} Inventory product</button>
          <button type="button" class="rec-chip" data-f="service" onclick="OrdersPage._recFilter('service',this)">${icon('wrench',12)} Service / labour</button>
        </div>
        <input class="field-input" id="rec-search" placeholder="Search the catalog, or fill in manually below…" autocomplete="off" oninput="OrdersPage._recSearch()">
        <div class="rec-results" id="rec-results"></div>
        <div class="rec-picked" id="rec-picked"></div>
      </div>` : ''}
      <div class="field"><label class="field-label">What are you recommending?</label>
        <input class="field-input" id="rec-title" value="${(r.title || '').replace(/"/g, '&quot;')}" placeholder="e.g. Replace brake pads"></div>
      <div class="rec-form-row">
        <div class="field"><label class="field-label">Type</label>
          <select class="field-input" id="rec-kind">
            <option value="service"${r.kind === 'service' ? ' selected' : ''}>Service</option>
            <option value="product"${r.kind === 'product' ? ' selected' : ''}>Product</option>
            <option value="custom"${(!r.kind || r.kind === 'custom') ? ' selected' : ''}>Custom</option>
          </select></div>
        <div class="field"><label class="field-label">Qty</label>
          <input class="field-input" id="rec-qty" type="number" min="0" step="0.5" value="${r.qty != null ? r.qty : 1}"></div>
        <div class="field"><label class="field-label">Unit price</label>
          <input class="field-input" id="rec-price" type="number" min="0" step="0.01" value="${r.unit_price != null ? r.unit_price : 0}"></div>
      </div>
      <div class="field"><label class="field-label">Why (shown to customer)</label>
        <textarea class="field-input" id="rec-note" rows="2" placeholder="Worn to 2mm — safety.">${(r.note || '').replace(/</g, '&lt;')}</textarea></div>
      <div class="field"><label class="field-label">Photo proof (optional)</label>
        <input type="file" accept="image/*" id="rec-photo" class="field-input" onchange="OrdersPage._recPhoto(event)">
        <div id="rec-photo-prev">${r.media_data ? `<img src="${r.media_data}" class="rec-prev-img">` : ''}</div>
      </div>`;
  },

  /* ── 144bg: catalog-or-manual add. Pick an inventory product / service to
     prefill the rec (links ref_id + kind + price), or type it manually. ── */
  _recFilterVal: 'all', _recRefId: null, _recDebounce: null,
  _recFilter(f, el) {
    this._recFilterVal = f;
    el.parentElement.querySelectorAll('.rec-chip').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    this._recSearch();
  },
  _recSearch() {
    clearTimeout(this._recDebounce);
    this._recDebounce = setTimeout(async () => {
      const q = (document.getElementById('rec-search')?.value || '').trim();
      const box = document.getElementById('rec-results');
      if (!box) return;
      if (!q) { box.innerHTML = ''; return; }
      let items = [];
      try { items = (await get(`/api/catalog/search?q=${encodeURIComponent(q)}&type=${this._recFilterVal}`)).items || []; } catch (e) {}
      box.innerHTML = items.length
        ? items.map(it => `<div class="rec-result" onclick='OrdersPage._recChoose(${JSON.stringify(it).replace(/'/g, "&#39;")})'>
            <span class="cat-kind cat-kind-${it.kind === 'service' ? 'svc' : 'prod'}">${it.kind}</span>
            <span class="rec-result-nm">${this._escAttr(it.name)}</span>
            <span class="rec-result-pr">${fmt.currency(it.price || 0)}</span></div>`).join('')
        : '<div class="rec-result rec-result-none">No match — fill in manually below.</div>';
    }, 200);
  },
  _recChoose(it) {
    this._recRefId = it.ref_id || null;
    document.getElementById('rec-title').value = it.name || '';
    document.getElementById('rec-kind').value = it.kind === 'service' ? 'service' : 'product';
    document.getElementById('rec-price').value = it.price != null ? it.price : 0;
    document.getElementById('rec-results').innerHTML = '';
    document.getElementById('rec-search').value = '';
    document.getElementById('rec-picked').innerHTML =
      `<div class="rec-picked-tag">${icon('link', 12)} Linked: ${this._escAttr(it.name)} <button type="button" onclick="OrdersPage._recUnpick()">${icon('x', 11)}</button></div>`;
  },
  _recUnpick() { this._recRefId = null; const p = document.getElementById('rec-picked'); if (p) p.innerHTML = ''; },
  _escAttr(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); },

  _recPhotoData: null,
  _recPhoto(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) { Toast.error('Image must be under 5 MB'); e.target.value = ''; return; }
    const rd = new FileReader();
    rd.onload = () => { this._recPhotoData = rd.result; document.getElementById('rec-photo-prev').innerHTML = `<img src="${rd.result}" class="rec-prev-img">`; };
    rd.readAsDataURL(f);
  },

  addRecModal(id) {
    this._recPhotoData = null;
    this._recRefId = null;
    this._recFilterVal = 'all';
    Modal.open(`
      <div class="modal-header"><div class="modal-title">Add recommendation</div><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">${this._recFormHtml()}</div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.saveRec('${id}')">Save draft</button>
      </div>`, '', true);
  },

  async editRecModal(id, recId) {
    this._recPhotoData = null;
    let r = {};
    try { r = (await get(`/api/orders/${id}/recommendations`)).items.find(x => x.id === recId) || {}; } catch (e) {}
    Modal.open(`
      <div class="modal-head"><div class="modal-title">Edit recommendation</div><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">${this._recFormHtml(r)}</div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.saveRec('${id}','${recId}')">Save</button>
      </div>`, '', true);
  },

  async saveRec(id, recId) {
    const title = document.getElementById('rec-title').value.trim();
    if (!title) { Toast.error('Enter what you are recommending'); return; }
    const body = {
      title,
      kind: document.getElementById('rec-kind').value,
      qty: parseFloat(document.getElementById('rec-qty').value) || 1,
      unit_price: parseFloat(document.getElementById('rec-price').value) || 0,
      note: document.getElementById('rec-note').value.trim() || null,
    };
    if (!recId && this._recRefId) body.ref_id = this._recRefId;   // 144bg: link to catalog item
    if (this._recPhotoData) body.media_data = this._recPhotoData;
    try {
      if (recId) await patch(`/api/orders/${id}/recommendations/${recId}`, body);
      else await post(`/api/orders/${id}/recommendations`, body);
      Modal.close(true);
      Toast.success('Saved');
      this._loadRecs(id);
    } catch (e) { Toast.error(e.message); }
  },

  async publishRec(id, recId) {
    try { await post(`/api/orders/${id}/recommendations/${recId}/publish`, {}); Toast.success('Sent to customer'); this._loadRecs(id); }
    catch (e) { Toast.error(e.message); }
  },

  async unpublishRec(id, recId) {
    try { await post(`/api/orders/${id}/recommendations/${recId}/unpublish`, {}); this._loadRecs(id); }
    catch (e) { Toast.error(e.message); }
  },

  async publishAllRecs(id) {
    try { const r = await post(`/api/orders/${id}/recommendations/publish-all`, {}); Toast.success(`${r.published} sent to customer`); this._loadRecs(id); }
    catch (e) { Toast.error(e.message); }
  },

  async deleteRec(id, recId) {
    if (!await Modal.confirm('Delete this recommendation?', {danger:true})) return;
    try { await del(`/api/orders/${id}/recommendations/${recId}`); this._loadRecs(id); }
    catch (e) { Toast.error(e.message); }
  },

  /* ── 144bj: technician notes + conclusion on the work order ── */
  async saveNotes(id) {
    try {
      await patch(`/api/orders/${id}/notes`, {
        tech_notes: document.getElementById('wo-tech-notes').value,
        conclusion: document.getElementById('wo-conclusion').value,
      });
      Toast.success('Notes saved');
    } catch (e) { Toast.error(e.message); }
  },

  /* ── 144be: staff reply to the customer (shows on track/portal) ── */
  async sendCustomerReply(id) {
    const el = document.getElementById(`reply-body-${id}`);
    const body = (el && el.value || '').trim();
    if (!body) { Toast.error('Type a reply first'); return; }
    try {
      await post(`/api/orders/${id}/customer-reply`, { body });
      el.value = '';
      Toast.success('Reply sent to the customer');
      if (typeof Feed !== 'undefined' && Feed.entityId === id) Feed.reload();
    } catch (e) { Toast.error(e.message); }
  },

  /* 144bh WS2 — order lines grouped by technician (Orderry pattern). Each tech gets a
     subheader row; lines with no tech fall under "Unassigned". Row markup unchanged. */
  // 144cv — Repair Canvas: services & products as per-technician JOB CARDS
  // (Shopmonkey pattern) instead of a spreadsheet table. Each card groups a tech's
  // lines with a per-job subtotal. Every inline handler (edit qty/price, reassign
  // tech, receive stock, delete) is preserved — nothing lost, just re-laid-out.
  _woJobCards(o, id) {
    const lines = o.lines || [];
    const groups = new Map();
    lines.forEach(l => {
      const tid = l.technician_id || '';
      if (!groups.has(tid)) groups.set(tid, { tid, name: (tid && this._empName(tid)) || 'Unassigned', lines: [] });
      groups.get(tid).lines.push(l);
    });
    const M = (n) => this._docCur ? fmt.money(Number(n) || 0, this._docCur) : fmt.currency(Number(n) || 0);
    let html = '';
    for (const g of groups.values()) {
      const sub = g.lines.reduce((s, l) => s + (l.total || 0), 0);
      const av = (g.name || '?').trim().charAt(0).toUpperCase();
      html += `<div class="wo-job">
        <div class="wo-job-h">
          <span class="wo-job-tech${g.tid ? '' : ' none'}">${g.tid ? `<span class="wo-job-av">${av}</span>` : ''}${this._escAttr(g.name)}</span>
          <span class="wo-job-sub tbl-num">${M(sub)}</span>
        </div>
        ${g.lines.map(l => this._woJobLine(id, l)).join('')}
      </div>`;
    }
    return html;
  },

  _woJobLine(id, l) {
    const M = (n) => this._docCur ? fmt.money(Number(n) || 0, this._docCur) : fmt.currency(Number(n) || 0);
    const kindTag = l.kind === 'service' ? '<span class="cat-kind cat-kind-svc">service</span>'
      : l.kind === 'product' ? '<span class="cat-kind cat-kind-prod">product</span>'
      : '<span class="cat-kind">custom</span>';
    const profit = (l.unit_price - (l.cost_price || 0)) * l.qty;
    const discTxt = l.discount ? (l.discount_type === 'fixed' ? M(l.discount) : l.discount + '%') : '';
    const taxTxt = l.tax_rate ? l.tax_rate + '%' + (l.tax_mode === 'included' ? ' inc' : '') : '';
    return `<div class="wo-job-line">
      <div class="wo-jl-main">
        <div class="wo-jl-desc">${kindTag}<span>${l.description}</span></div>
        <div class="wo-jl-meta">
          <span class="line-edit" onclick="OrdersPage.editLineField('${id}','${l.id}','qty',${l.qty})">${l.qty}</span> ×
          <span class="line-edit" onclick="OrdersPage.editLineField('${id}','${l.id}','unit_price',${l.unit_price})">${M(l.unit_price)}</span>
          ${discTxt
            ? ` · <span class="line-edit" style="color:var(--green)" onclick="OrdersPage.editLineDiscount('${id}','${l.id}',${l.discount||0},'${l.discount_type||'pct'}')">−${discTxt}</span>`
            : ` · <span class="line-edit" style="color:var(--tx-4);font-size:11px" onclick="OrdersPage.editLineDiscount('${id}','${l.id}',0,'pct')">+disc</span>`}
          ${taxTxt
            ? ` · <span class="line-edit" onclick="OrdersPage.editLineTax('${id}','${l.id}','${l.tax_id||''}')">${taxTxt}</span>`
            : ` · <span class="line-edit" style="color:var(--tx-4);font-size:11px" onclick="OrdersPage.editLineTax('${id}','${l.id}','')">+tax</span>`}
          · <span class="line-edit" onclick="OrdersPage.pickLineTech('${id}','${l.id}')">${OrdersPage._empName(l.technician_id) || 'assign tech'}</span>
          <span class="wo-jl-cp">cost ${M(l.cost_price || 0)} · <b style="color:${profit > 0 ? 'var(--green)' : 'var(--tx-3)'}">${M(profit)} profit</b></span>
        </div>
        ${l.pending_stock > 0 ? `<div class="wo-jl-pending">${l.pending_stock} unit(s) pending stock <button class="btn btn-glass btn-sm" style="padding:2px 8px" onclick="OrdersPage.receiveStock('${id}','${l.id}')">Receive</button></div>` : ''}
      </div>
      <div class="wo-jl-total tbl-num">${M(l.total)}</div>
      <button class="cat-line-del" title="Remove" onclick="OrdersPage.deleteLine('${id}','${l.id}')">${icon('x', 13)}</button>
    </div>`;
  },

  _woLinesGrouped(o, id) {
    const lines = o.lines || [];
    const groups = new Map();               // techId -> {name, lines[]}
    lines.forEach(l => {
      const tid = l.technician_id || '';
      if (!groups.has(tid)) groups.set(tid, { name: (tid && this._empName(tid)) || 'Unassigned', lines: [] });
      groups.get(tid).lines.push(l);
    });
    const single = groups.size <= 1;        // don't clutter with a header when one tech
    let html = '';
    for (const g of groups.values()) {
      if (!single) {
        html += `<tr class="wo-line-grp"><td colspan="10">${icon('user', 11)} ${this._escAttr(g.name)}</td></tr>`;
      }
      html += g.lines.map(l => this._woLineRow(id, l)).join('');
    }
    return html;
  },

  _woLineRow(id, l) {
    const profit = (l.unit_price - (l.cost_price || 0)) * l.qty;
    const kindTag = l.kind === 'service' ? '<span class="cat-kind cat-kind-svc" style="margin-right:6px">service</span>' : l.kind === 'product' ? '<span class="cat-kind cat-kind-prod" style="margin-right:6px">product</span>' : '';
    const discTxt = l.discount ? (l.discount_type === 'fixed' ? fmt.currency(l.discount) : l.discount + '%') : '—';
    const taxTxt = l.tax_rate ? l.tax_rate + '%' + (l.tax_mode === 'included' ? ' inc' : '') : '—';
    return `<tr style="border-top:1px solid var(--line,rgba(60,100,170,.08))">
      <td style="padding:13px 16px;font-weight:600">${kindTag}${l.description}${l.pending_stock > 0 ? `<div style="font-size:10.5px;color:var(--amber);font-weight:700;margin-top:3px">${l.pending_stock} unit(s) pending stock <button class="btn btn-glass btn-sm" style="padding:2px 8px;margin-left:4px" onclick="OrdersPage.receiveStock('${id}','${l.id}')">Receive</button></div>` : ''}</td>
      <td style="padding:13px 8px;font-size:11.5px;color:var(--tx-2)"><span class="line-edit" onclick="OrdersPage.pickLineTech('${id}','${l.id}')">${OrdersPage._empName(l.technician_id) || '<span style=&quot;color:var(--tx-4)&quot;>— assign</span>'}</span></td>
      <td style="text-align:center;color:var(--tx-3)"><span class="line-edit" onclick="OrdersPage.editLineField('${id}','${l.id}','qty',${l.qty})">${l.qty}</span></td>
      <td style="text-align:right;color:var(--tx-2)"><span class="line-edit" onclick="OrdersPage.editLineField('${id}','${l.id}','unit_price',${l.unit_price})">${OrdersPage._docCur ? fmt.money(l.unit_price, OrdersPage._docCur) : fmt.currency(l.unit_price)}</span></td>
      <td style="text-align:right;color:var(--green);font-size:11.5px"><span class="line-edit" onclick="OrdersPage.editLineDiscount('${id}','${l.id}',${l.discount||0},'${l.discount_type||'pct'}')">${discTxt}</span></td>
      <td style="text-align:right;color:var(--tx-3);font-size:11.5px"><span class="line-edit" onclick="OrdersPage.editLineTax('${id}','${l.id}','${l.tax_id||''}')">${taxTxt}</span></td>
      <td style="text-align:right;color:var(--tx-3)">${fmt.currency(l.cost_price || 0)}</td>
      <td style="text-align:right;font-weight:700;color:${profit > 0 ? 'var(--green)' : 'var(--tx-3)'}">${fmt.currency(profit)}</td>
      <td style="text-align:right;font-weight:800">${OrdersPage._docCur ? fmt.money(l.total, OrdersPage._docCur) : fmt.currency(l.total)}</td>
      <td style="text-align:center"><button class="cat-line-del" title="Remove" onclick="OrdersPage.deleteLine('${id}','${l.id}')">${icon('x', 13)}</button></td>
    </tr>`;
  },

  _invoiceTotals(o) {
    // compute subtotal (pre-tax, post-discount), total discount, total tax
    let subtotal = 0, discTotal = 0, taxTotal = 0;
    o.lines.forEach(l => {
      const base = l.qty * l.unit_price;
      const afterDisc = l.discount_type === 'fixed'
        ? Math.max(base - (l.discount||0), 0)
        : base * (1 - (l.discount||0)/100);
      discTotal += base - afterDisc;
      subtotal += afterDisc;
      const rate = (l.tax_rate||0)/100;
      if (rate) {
        taxTotal += l.tax_mode === 'included'
          ? afterDisc - afterDisc/(1+rate)
          : afterDisc * rate;
      }
    });
    const hasImposed = o.lines.some(l => l.tax_mode !== 'included' && l.tax_rate);
    const profitTotal = o.lines.reduce((s, l) => s + (l.unit_price - (l.cost_price || 0)) * l.qty, 0);
    const cur = this._docCur;
    const M = (n) => cur ? fmt.money(n, cur) : fmt.currency(n);
    return `<div class="inv-totals">
      <div class="inv-trow"><span>Subtotal</span><span class="v">${M(subtotal)}</span></div>
      ${discTotal>0?`<div class="inv-trow disc"><span>Discount</span><span class="v">− ${M(discTotal)}</span></div>`:''}
      ${taxTotal>0?`<div class="inv-trow"><span>Tax${hasImposed?'':' (incl.)'}</span><span class="v">${M(taxTotal)}</span></div>`:''}
      <div class="inv-trow profit"><span>Estimated profit</span><span class="v">${M(profitTotal)}</span></div>
      <div class="inv-trow grand"><span>Total</span><span class="v">${M(o.total)}</span></div>
      ${cur?`<div class="inv-trow" style="border:0;padding-top:6px"><span style="font-size:11px;color:var(--tx-4)">Billed in ${cur.code} · 1 base = ${cur.rate} ${cur.code}</span><span></span></div>`:''}
    </div>`;
  },

  copyTrackLink(tok) {
    const url = `${window.location.origin}/track/${tok}`;
    navigator.clipboard?.writeText(url).then(
      () => Toast.success('Tracking link copied'),
      () => { window.prompt('Copy this tracking link:', url); }
    );
    window.open(url, '_blank');
  },

  async setStatus(id, status, el) {
    try {
      const r = await patch(`/api/orders/${id}/status`, { status });
      document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      if (el) el.classList.add('active');
      Toast.success('Status updated');
      if (r && r.triggers_payment) await this.maybePaymentWindow(id);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async maybePaymentWindow(orderId, force) {
    let ctx;
    try { ctx = await get(`/api/cash/orders/${orderId}/payment-context`); } catch { return; }
    // overpaid (prepayments exceed total) → offer a refund instead
    if (ctx.overpaid > 0) {
      let accounts = [];
      try { accounts = (await get('/api/cash/accounts')).items; } catch {}
      Modal.open(`
        <div class="modal-header"><span class="modal-title">Refund overpayment</span><button class="modal-close" onclick="Modal.close()">×</button></div>
        <div class="modal-body">
          <div class="pay-amount">${fmt.currency(ctx.overpaid)}<span>to refund</span></div>
          <div class="field"><label class="field-label">From cashbox</label>
            <select class="field-select" id="rf-account">${accounts.map(a => `<option value="${a.id}">${a.name}</option>`).join('')}</select>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-glass" onclick="Modal.close()">Later</button>
          <button class="btn btn-primary" onclick="OrdersPage.submitRefund('${orderId}', ${ctx.overpaid})">Refund</button>
        </div>`, 'modal-narrow');
      return;
    }
    // skip if nothing to charge or already settled (Orderry: no window when item cost is 0)
    if (!force && (ctx.item_cost <= 0 || ctx.remaining <= 0)) return;
    if (ctx.remaining <= 0) { Toast.success('Order is fully paid'); return; }
    const byM = ctx.accounts_by_method || {};
    const methodOpts = [];
    if ((byM.cash||[]).length) methodOpts.push(['cash','Cash']);
    if ((byM.cashless||[]).length) methodOpts.push(['cashless','Cashless']);
    if ((byM.card||[]).length) methodOpts.push(['card','Card']);
    methodOpts.push(['balance','On account (debt)']);
    this._payCtx = ctx;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Take payment</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="pay-amount">${fmt.currency(ctx.remaining)}<span>due</span></div>
        ${ctx.already_paid>0?`<div style="font-size:12px;color:var(--tx-3);text-align:center;margin-bottom:10px">${fmt.currency(ctx.already_paid)} already paid of ${fmt.currency(ctx.order_total)}</div>`:''}
        <div class="field"><label class="field-label">Payment method</label>
          <div class="pay-methods">
            ${methodOpts.map(([v,l],i)=>`<button type="button" class="pay-method${i===0?' active':''}" data-m="${v}" onclick="OrdersPage.pickPayMethod('${v}',this)">${l}</button>`).join('')}
          </div>
        </div>
        <div class="field" id="pay-acct-wrap"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Skip for now</button>
        <button class="btn btn-primary" onclick="OrdersPage.submitPayment('${orderId}')">Take payment</button>
      </div>`, 'modal-narrow');
    this._payMethod = methodOpts[0][0];
    this._renderPayAccounts();
  },

  pickPayMethod(m, el) {
    this._payMethod = m;
    el.parentElement.querySelectorAll('.pay-method').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    this._renderPayAccounts();
  },

  _renderPayAccounts() {
    const wrap = document.getElementById('pay-acct-wrap');
    if (!wrap) return;
    if (this._payMethod === 'balance') {
      wrap.innerHTML = `<div style="font-size:12.5px;color:var(--tx-3);padding:6px 0">The amount will be added to the client's account balance as debt.</div>`;
      return;
    }
    const accts = (this._payCtx.accounts_by_method || {})[this._payMethod] || [];
    wrap.innerHTML = `<label class="field-label">Cashbox</label>
      <select class="field-select" id="pay-account">
        ${accts.map(a=>`<option value="${a.id}">${a.name}${a.fee_pct?` · ${a.fee_pct}% fee`:''}</option>`).join('')}
      </select>`;
  },

  async submitPayment(orderId) {
    const body = { method: this._payMethod };
    if (this._payMethod !== 'balance') {
      const sel = document.getElementById('pay-account');
      if (!sel || !sel.value) return Toast.error('Select a cashbox');
      body.account_id = sel.value;
    }
    try {
      const r = await post(`/api/cash/orders/${orderId}/pay`, body);
      Toast.success(r.method === 'balance' ? 'Closed on client balance' : `Payment taken${r.fee?` (− ${fmt.currency(r.fee)} fee)`:''}`);
      Modal.close();
      this._loadOrderInvoices(orderId);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async submitRefund(orderId, amount) {
    const account_id = document.getElementById('rf-account').value;
    try {
      await post(`/api/cash/orders/${orderId}/refund`, { amount, account_id });
      Toast.success('Refund issued');
      Modal.close();
      this._loadOrderInvoices(orderId);
    } catch (e) { Toast.error(e.message); }
  },

  async receiveStock(orderId, lineId) {
    try {
      await post(`/api/orders/${orderId}/lines/${lineId}/receive-stock`, {});
      Toast.success('Stock written off');
      Modal.close(); this.viewOrder(orderId); this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── AI assists (Phase 3B) ── */
  async aiSummary(orderId) {
    const box = document.getElementById('wo-ai-summary');
    if (box) box.innerHTML = `<div class="card" style="padding:12px;color:var(--tx-4)">AI is summarizing…</div>`;
    try {
      const r = await post(`/api/ai/orders/${orderId}/summary`, {});
      if (box) box.innerHTML = `<div class="card" style="padding:14px;margin-bottom:12px;border-left:3px solid var(--accent)">
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">AI summary${r.provider === 'stub' ? ' · offline draft' : ''}</div>
        <div style="font-size:13px;line-height:1.55">${r.summary}</div></div>`;
    } catch (e) { Toast.error(e.message); if (box) box.innerHTML = ''; }
  },

  async currencyModal(orderId) {
    let company = { currency_code: 'INR', currencies: [] };
    try { company = await get('/api/staff/company'); } catch {}
    const base = company.currency_code || 'INR';
    const opts = [['', `Base (${base})`]].concat((company.currencies || []).map(c => [c.code, `${c.code} — ${c.symbol||''} (1 ${base} = ${c.rate} ${c.code})`]));
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Document currency</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Choose the currency this document is billed and printed in. Amounts are stored in your base currency (${base}) and shown converted at the selected rate.</p>
        <div class="field"><label class="field-label">Currency</label>
          <select class="field-select" id="oc-currency">
            ${opts.map(([k,l])=>`<option value="${k}" ${(this._docCur&&this._docCur.code===k)||(!this._docCur&&!k)?'selected':''}>${l}</option>`).join('')}
          </select></div>
        ${(company.currencies||[]).length===0?'<div style="color:var(--amber);font-size:12px">No additional currencies configured. Add them in Settings → General.</div>':''}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.setOrderCurrency('${orderId}')">Apply</button>
      </div>`, 'modal-narrow');
  },

  async setOrderCurrency(orderId) {
    const code = document.getElementById('oc-currency').value || null;
    try {
      await patch(`/api/orders/${orderId}/currency`, { currency_code: code });
      Toast.success('Currency updated');
      Modal.close();
      this.viewOrder(orderId);
    } catch (e) { Toast.error(e.message || 'Could not change currency'); }
  },

  async aiDraftLines(orderId) {
    let r;
    try {
      const o = await get(`/api/orders/${orderId}`);
      if (!o.complaint) { Toast.error('Add a complaint first so AI has something to work from'); return; }
      r = await post('/api/ai/estimate/draft', { complaint: o.complaint, device: o.device ? `${o.device.brand} ${o.device.model}` : '' });
    } catch (e) { return Toast.error(e.message); }
    const rows = r.lines.map((l, i) => `<tr>
      <td>${l.description}</td>
      <td style="text-align:right">${l.qty}</td>
      <td style="text-align:right">${fmt.currency(l.unit_price)}</td>
      <td><label class="field-check" style="margin:0"><input type="checkbox" class="ai-line" data-i="${i}" checked></label></td>
    </tr>`).join('');
    window._aiDraft = r.lines;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">AI-drafted lines${r.provider === 'stub' ? ' · offline draft' : ''}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px">Suggested from the complaint. Untick any you don't want, then add the rest as custom lines (prices are editable on the order).</p>
        <table class="tbl"><thead><tr><th>Description</th><th style="text-align:right">Qty</th><th style="text-align:right">Price</th><th>Add</th></tr></thead>
        <tbody>${rows}</tbody></table>
      </div>
      <div class="modal-footer"><button class="btn btn-ghost" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.applyAiLines('${orderId}')">Add selected</button></div>`, 'modal-wide', true);
  },

  async applyAiLines(orderId) {
    const picks = Array.from(document.querySelectorAll('.ai-line:checked')).map(c => window._aiDraft[+c.dataset.i]);
    if (!picks.length) { Toast.error('Select at least one line'); return; }
    try {
      for (const l of picks) {
        await post(`/api/orders/${orderId}/lines`, {
          line_type: 'labour', kind: 'custom', description: l.description,
          qty: l.qty, unit_price: l.unit_price });
      }
      Modal.close();
      Toast.success(`${picks.length} line(s) added`);
      this.viewOrder(orderId);
    } catch (e) { Toast.error(e.message); }
  },

  /* ── Add / edit / delete lines on an existing order (Orderry) ── */
  _emps: null,
  async _loadEmps() {
    if (!this._emps) { try { this._emps = await get('/api/staff/employees'); } catch (e) { this._emps = []; } }
    return this._emps;
  },
  _empName(id) {
    if (!id || !this._emps) return '';
    const e = this._emps.find(x => x.id === id);
    return e ? e.name : '';
  },
  async pickLineTech(orderId, lineId) {
    const emps = await this._loadEmps();
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Line technician</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Performs this task (payroll commission goes to them)</label>
          <select class="field-select" id="lt-emp">
            <option value="">— none (falls back to assigned tech)</option>
            ${emps.map(e=>`<option value="${e.id}">${e.name}</option>`).join('')}
          </select></div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="lt-save">Save</button></div>`, '', true);
    document.getElementById('lt-save').addEventListener('click', async () => {
      const v = document.getElementById('lt-emp').value || null;
      try { await patch(`/api/orders/${orderId}/lines/${lineId}`, { technician_id: v }); Modal.close(); this.viewOrder(orderId); }
      catch (e) { Toast.error(e.message || 'Failed'); }
    });
  },
  async applyCouponModal(orderId) {
    // 144bi: offer this client's issued coupons first, plus a manual code fallback.
    let clientId = null, coupons = [];
    try {
      const o = await get(`/api/orders/${orderId}`);
      clientId = o.client?.id || null;
      if (clientId) coupons = ((await get(`/api/clients/${clientId}/coupons`)).items || []).filter(c => !c.spent && c.active);
    } catch (e) {}
    const cv = (c) => c.kind === 'pct' ? `${c.value}%` : fmt.currency(c.value);
    Modal.open(`
      <div class="modal-head"><div class="modal-title">Redeem coupon</div><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        ${coupons.length ? `<div style="font-size:11px;color:var(--tx-3);margin-bottom:8px">This customer's coupons</div>
          ${coupons.map(c => `<div class="ccoupon">
            <div><span class="ccoupon-code">${c.code}</span> <span class="ccoupon-sub">· ${cv(c)} off${c.min_total ? ` · min ${fmt.currency(c.min_total)}` : ''}</span></div>
            <button class="btn btn-primary btn-sm" onclick="OrdersPage.applyCoupon('${orderId}','${c.code}')">Apply</button></div>`).join('')}
          <div style="height:10px"></div>` : '<div style="font-size:12px;color:var(--tx-4);margin-bottom:8px">No coupons issued to this customer.</div>'}
        <div class="field"><label class="field-label">Or enter a code</label>
          <input class="field-input" id="cp-manual" placeholder="Coupon code"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.applyCoupon('${orderId}', document.getElementById('cp-manual').value)">Apply code</button>
      </div>`, '', true);
  },

  async applyCoupon(orderId, code) {
    code = (code || '').trim();
    if (!code) return Toast.error('Enter or pick a coupon');
    try {
      const r = await post(`/api/orders/${orderId}/apply-coupon`, { code });
      Modal.close(true);
      Toast.success(`Coupon applied — ${fmt.currency(r.discount)} off`);
      this.viewOrder(orderId);
    } catch (e) { Toast.error(e.message || 'Coupon rejected'); }
  },
  async redeemGiftModal(orderId) {
    const code = window.prompt('Gift card code:');
    if (!code) return;
    try { const r = await post(`/api/orders/${orderId}/redeem-giftcard`, { code }); Toast.success(`Redeemed ${fmt.currency(r.redeemed)} — card balance ${fmt.currency(r.card_balance)}`); this.viewOrder(orderId); }
    catch (e) { Toast.error(e.message || 'Gift card rejected'); }
  },
  async redeemPointsModal(orderId) {
    const pts = window.prompt('Points to redeem:');
    if (!pts) return;
    try { const r = await post(`/api/orders/${orderId}/redeem-loyalty`, { points: parseFloat(pts) }); Toast.success(`Applied ${fmt.currency(r.discount)} — ${r.points_left} points left`); this.viewOrder(orderId); }
    catch (e) { Toast.error(e.message || 'Redemption rejected'); }
  },
  // Build 144dj — inline add on the Services & Products tab (Orderry flow).
  // Build 144dl — Orderry-style dropdown: category tabs (All / Services / Products / Bundles)
  //              and results grouped under their catalog category headers.
  _woSearchT: null,
  _woTab: 'all',
  woSetTab(id, tab) { this._woTab = tab; this.woSearch(id, true); document.getElementById('wo-add-search')?.focus(); },
  woSearch(id, immediate) {
    clearTimeout(this._woSearchT);
    const box = document.getElementById('wo-add-results');
    if (!box) return;
    const run = async () => {
      const q = (document.getElementById('wo-add-search')?.value || '').trim();
      const tab = this._woTab || 'all';
      const type = tab === 'all' ? 'all' : tab; // service | product | bundle | all
      let items = [];
      try { items = (await get(`/api/catalog/search?q=${encodeURIComponent(q)}&type=${type}`)).items || []; } catch (e) {}
      // Orderry-style tab bar
      const tabs = [['all','All items'],['service','Services'],['product','Products'],['bundle','Bundles']];
      const tabBar = `<div class="wo-add-tabs">${tabs.map(([v,l]) =>
        `<button type="button" class="wo-add-tab${tab===v?' on':''}" onclick="OrdersPage.woSetTab('${id}','${v}')">${l}</button>`
      ).join('')}</div>`;
      // group results by category
      const optRow = (it) => {
        const price = (it.kind === 'product' && it.repair_price) ? it.repair_price : (it.price || 0);
        const meta = it.kind === 'product'
          ? `<span class="cat-kind cat-kind-prod">product</span>${it.available != null ? ` · ${it.available} in stock` : ''}${it.sku ? ` · ${(''+it.sku).replace(/</g,'&lt;')}` : ''}`
          : it.kind === 'bundle' ? '<span class="cat-kind">bundle</span>' : '<span class="cat-kind cat-kind-svc">service</span>';
        return `<div class="wo-add-opt" onclick="OrdersPage.woPick('${id}','${it.kind}','${it.ref_id}',${JSON.stringify(it.name)},${price})">
          <div class="wo-add-opt-name">${(it.name || '').replace(/</g, '&lt;')}</div>
          <div class="wo-add-opt-meta">${meta}</div>
          <div class="wo-add-opt-price">${OrdersPage._docCur ? fmt.money(price, OrdersPage._docCur) : fmt.currency(price)}</div>
        </div>`;
      };
      const groups = {};
      items.forEach(it => { const c = it.category || 'Uncategorized'; (groups[c] = groups[c] || []).push(it); });
      let body = Object.keys(groups).sort().map(cat =>
        `<div class="wo-add-group-head">${(cat || '').replace(/</g, '&lt;')}</div>` + groups[cat].map(optRow).join('')
      ).join('');
      if (!items.length) body = `<div class="wo-add-empty">${q ? 'No matches in catalog' : 'Start typing to search the catalog'}</div>`;
      const custom = q ? `<div class="wo-add-opt wo-add-custom" onclick="OrdersPage.woAddCustom('${id}')">
        <div class="wo-add-opt-name" style="color:var(--blue-dark)">Add "${q.replace(/</g, '&lt;')}" as a custom line</div>
        <div class="wo-add-opt-meta">Free text · set your own price</div></div>` : '';
      box.innerHTML = tabBar + `<div class="wo-add-scroll">${body}${custom}</div>`;
      // position:fixed so the list escapes the modal's overflow clip and sits above it.
      // A transformed/filtered ancestor (the animated tab panel) becomes the containing
      // block for position:fixed, so offset against it — falls back to the viewport (0,0).
      const ir = document.getElementById('wo-add-search').getBoundingClientRect();
      let cb = box.parentElement, cr = { left: 0, top: 0 };
      while (cb) {
        const s = getComputedStyle(cb);
        if (s.transform !== 'none' || s.perspective !== 'none' || s.filter !== 'none') { cr = cb.getBoundingClientRect(); break; }
        cb = cb.parentElement;
      }
      box.style.left = (ir.left - cr.left) + 'px';
      box.style.top = (ir.bottom - cr.top + 6) + 'px';
      box.style.width = ir.width + 'px';
      box.classList.add('open');
      if (!this._woOutside) {
        this._woOutside = (ev) => {
          const b = document.getElementById('wo-add-results');
          if (!b || !b.classList.contains('open')) return;
          if (b.contains(ev.target) || ev.target.closest('.wo-addsearch')) return;
          b.classList.remove('open');
        };
        document.addEventListener('mousedown', this._woOutside, true);
      }
    };
    if (immediate) run(); else this._woSearchT = setTimeout(run, 180);
  },
  _woTech() { return document.getElementById('wo-add-tech')?.value || null; },
  async _woPost(id, body) {
    try { await post(`/api/orders/${id}/lines`, body); }
    catch (e) { return Toast.error(e.message); }
    const s = document.getElementById('wo-add-search'); if (s) s.value = '';
    const b = document.getElementById('wo-add-results'); if (b) { b.innerHTML = ''; b.classList.remove('open'); }
    this.viewOrder(id);
  },
  woPick(id, kind, ref, name, price) {
    const lt = kind === 'service' ? 'labour' : kind === 'product' ? 'part' : kind === 'bundle' ? 'fee' : 'fee';
    this._woPost(id, { line_type: lt, kind, ref_id: ref, description: name, qty: 1, unit_price: price, cost_price: 0,
      technician_id: this._woTech(), discount: 0, discount_type: 'pct', tax_rate: 0, tax_mode: 'imposed' });
  },
  woAddCustom(id) {
    const name = (document.getElementById('wo-add-search')?.value || '').trim();
    if (!name) return;
    this._woPost(id, { line_type: 'fee', kind: 'custom', ref_id: null, description: name, qty: 1, unit_price: 0, cost_price: 0,
      technician_id: this._woTech(), discount: 0, discount_type: 'pct', tax_rate: 0, tax_mode: 'imposed' });
  },

  async addLineModal(orderId) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add services &amp; products</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body" style="min-height:420px"><div id="add-line-catalog"></div></div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.submitAddLines('${orderId}')">Add to order</button>
      </div>`, 'modal-wide');
    CatalogPicker.repairPricing = true;
    await CatalogPicker.mount('add-line-catalog');
  },

  async submitAddLines(orderId) {
    const lines = CatalogPicker.payload();
    if (!lines.length) return Toast.error('Pick at least one item');
    try {
      for (const l of lines) {
        await post(`/api/orders/${orderId}/lines`, l);
      }
      Toast.success(`${lines.length} item(s) added`);
      Modal.close();
      this.viewOrder(orderId);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async editLineField(orderId, lineId, field, current) {
    const label = field === 'qty' ? 'Quantity' : field === 'unit_price' ? 'Unit price' : 'Value';
    const val = await Modal.prompt(`${label}:`, {default: current});
    if (val === null) return;
    const num = parseFloat(val);
    if (isNaN(num) || num < 0) return Toast.error('Enter a valid number');
    try {
      await patch(`/api/orders/${orderId}/lines/${lineId}`, { [field]: num });
      this.viewOrder(orderId);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async editLineDiscount(orderId, lineId, current, currentType) {
    const html = `<div style="padding:20px">
      <div class="form-label" style="font-weight:600;margin-bottom:12px">Discount</div>
      <div style="display:flex;gap:8px;align-items:center">
        <input class="field-input" id="disc-val-inp" type="number" min="0" step="any" value="${current || 0}" style="flex:1" autofocus>
        <select class="field-select" id="disc-type-sel" style="width:74px">
          <option value="pct"${currentType !== 'fixed' ? ' selected' : ''}>%</option>
          <option value="fixed"${currentType === 'fixed' ? ' selected' : ''}>$</option>
        </select>
      </div>
      <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary btn-sm" onclick="OrdersPage._applyLineDiscount('${orderId}','${lineId}')">Apply</button>
      </div>
    </div>`;
    Modal.open(html, 'modal-narrow');
    setTimeout(() => document.getElementById('disc-val-inp')?.focus(), 50);
  },

  async _applyLineDiscount(orderId, lineId) {
    const val = parseFloat(document.getElementById('disc-val-inp')?.value || 0) || 0;
    const type = document.getElementById('disc-type-sel')?.value || 'pct';
    if (val < 0) return Toast.error('Discount must be 0 or greater');
    Modal.close();
    try {
      await patch(`/api/orders/${orderId}/lines/${lineId}`, { discount: val, discount_type: type });
      this.viewOrder(orderId);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async editLineTax(orderId, lineId, currentTaxId) {
    let taxes = [];
    try { taxes = (await get('/api/catalog/taxes')).items || []; } catch {}
    const opts = `<option value="">No tax</option>` + taxes.map(t =>
      `<option value="${t.id}"${currentTaxId === t.id ? ' selected' : ''}>${t.name} (${t.rate}%${t.mode === 'included' ? ' incl' : ''})</option>`
    ).join('');
    const html = `<div style="padding:20px">
      <div class="form-label" style="font-weight:600;margin-bottom:12px">Tax rate</div>
      <select class="field-select" id="tax-pick-sel" style="width:100%">${opts}</select>
      <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end">
        <button class="btn btn-ghost btn-sm" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary btn-sm" onclick="OrdersPage._applyLineTax('${orderId}','${lineId}')">Apply</button>
      </div>
    </div>`;
    Modal.open(html, 'modal-narrow');
  },

  async _applyLineTax(orderId, lineId) {
    const sel = document.getElementById('tax-pick-sel');
    if (!sel) return;
    const taxId = sel.value;
    let taxes = [];
    try { taxes = (await get('/api/catalog/taxes')).items || []; } catch {}
    const t = taxes.find(x => x.id === taxId);
    const payload = taxId
      ? { tax_id: taxId, tax_rate: t?.rate || 0, tax_mode: t?.mode || 'imposed' }
      : { tax_id: null, tax_rate: 0, tax_mode: 'imposed' };
    Modal.close();
    try {
      await patch(`/api/orders/${orderId}/lines/${lineId}`, payload);
      this.viewOrder(orderId);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async deleteLine(orderId, lineId) {
    const ok = await confirm2('Remove this item from the order?');
    if (!ok) return;
    try {
      await del(`/api/orders/${orderId}/lines/${lineId}`);
      Toast.success('Item removed');
      this.viewOrder(orderId);
      this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  toggleActions(e) {
    e.stopPropagation();
    // If a menu is already open (portaled to <body>), this is a toggle-off / an
    // item was clicked — close it and stop.
    const openMenu = document.querySelector('.wo-actions-menu.portaled');
    if (openMenu) { openMenu.remove(); return; }
    const wrap = e.currentTarget.closest('.wo-actions-wrap');
    const menu = wrap?.querySelector('.wo-actions-menu');
    if (!menu) return;
    // Portal to <body> with fixed positioning so the menu escapes the modal's
    // stacking context and overflow (otherwise the tab-panel content, e.g. the
    // "AI summarize" button, renders over it and the items aren't clickable).
    const r = e.currentTarget.getBoundingClientRect();
    menu.classList.add('portaled');
    Object.assign(menu.style, {
      position: 'fixed', top: (r.bottom + 6) + 'px',
      right: (window.innerWidth - r.right) + 'px', left: 'auto',
      zIndex: '99999', display: 'block',
    });
    document.body.appendChild(menu);
    const close = (ev) => {
      if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('mousedown', close); }
    };
    setTimeout(() => document.addEventListener('mousedown', close), 0);
  },

  async copyPublicLink(orderId) {
    try {
      const r = await post(`/api/orders/${orderId}/public-link`, {});
      const url = `${location.origin}${r.path}`;
      const ok = await this._copyText(url);
      if (ok) Toast.success('Public tracking link copied');
      else window.prompt('Copy this tracking link:', url);
    } catch (e) { Toast.error(e.message || 'Failed to create public link'); }
  },

  async _copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch { return false; }
  },

  async duplicateOrder(orderId) {
    // Prefill "Schedule from/to" from the source order so a repeat job can be
    // re-booked in one click; both are optional (blank = unscheduled draft).
    let src = {};
    try { src = await get(`/api/orders/${orderId}`); src = src.order || src; } catch {}
    const dtLocal = (s) => (s || '').slice(0, 16);   // ISO → datetime-local value
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Duplicate work order</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Copies the client, asset and all service &amp; product lines into a new draft. Set a schedule below to book the copy straight onto the calendar, or leave both blank to schedule it later.</p>
        <div class="field-row">
          <div class="field">
            <label class="field-label">Schedule from</label>
            <input class="field-input" type="datetime-local" id="dup-from" value="${dtLocal(src.scheduled_for)}">
          </div>
          <div class="field">
            <label class="field-label">Schedule to</label>
            <input class="field-input" type="datetime-local" id="dup-to" value="${dtLocal(src.scheduled_to)}">
          </div>
        </div>
        <p style="color:var(--tx-4);font-size:12px;margin:8px 0 0">These map to the work order's <strong>Schedule from</strong> / <strong>Schedule to</strong> fields (see Settings → Forms &amp; types → Work order).</p>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="dup-go" onclick="OrdersPage.submitDuplicate('${orderId}')">Duplicate</button>
      </div>`, 'modal-narrow');
  },

  async submitDuplicate(orderId) {
    const from = document.getElementById('dup-from').value || null;
    const to = document.getElementById('dup-to').value || null;
    if (from && to && to < from) return Toast.error('Schedule to must be on or after schedule from');
    const btn = document.getElementById('dup-go'); btn.disabled = true; btn.textContent = 'Duplicating…';
    try {
      const r = await post(`/api/orders/${orderId}/duplicate`, { scheduled_for: from, scheduled_to: to });
      const sched = r.scheduled_for ? ' · scheduled' : '';
      Toast.success(`Duplicated as ${r.order_number} (${r.line_count} line${r.line_count === 1 ? '' : 's'} copied)${sched}`);
      Modal.close();
      this.viewOrder(r.id);
    } catch (e) {
      Toast.error(e.message || 'Failed to duplicate');
      btn.disabled = false; btn.textContent = 'Duplicate';
    }
  },

  async createPoModal(orderId) {
    let suppliers = [];
    try { suppliers = (await get('/api/inventory/suppliers')).items || []; } catch (e) { return Toast.error(e.message); }
    if (!suppliers.length) return Toast.error('Add a supplier first (Inventory → Suppliers)');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Create purchase order</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Orders the parts this work order needs but doesn't have in stock. Missing catalog products are created automatically, and the PO is linked back to this work order so you know what it's for.</p>
        <div class="field"><label class="field-label">Supplier</label>
          <select class="field-select" id="po-supplier">${suppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('')}</select></div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--tx-2);margin-top:10px;cursor:pointer">
          <input type="checkbox" id="po-send" checked> Mark as ordered &amp; notify the supplier now</label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="po-go" onclick="OrdersPage.submitPo('${orderId}')">Create PO</button>
      </div>`, 'modal-narrow');
  },

  async submitPo(orderId) {
    const supplier_id = document.getElementById('po-supplier').value;
    const auto_send = document.getElementById('po-send').checked;
    const btn = document.getElementById('po-go'); btn.disabled = true; btn.textContent = 'Creating…';
    try {
      const r = await post(`/api/orders/${orderId}/create-po`, { supplier_id, auto_send });
      Toast.success(`${r.po_number} raised — ${r.line_count} part${r.line_count === 1 ? '' : 's'}${r.sent ? ' and sent to ' + r.supplier : ''}`);
      Modal.close();
    } catch (e) {
      Toast.error(e.message || 'Could not create purchase order');
      btn.disabled = false; btn.textContent = 'Create PO';
    }
  },

  archiveModal(orderId) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Delete work order</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Deleting archives the work order — it's removed from your lists but kept for the owner with the reason below (never permanently destroyed). Payments made on it are refunded.</p>
        <div class="field"><label class="field-label">Reason <span style="color:var(--red,#e5484d)">*</span></label>
          <textarea class="field-textarea" id="arch-reason" rows="3" placeholder="Why is this being removed? (e.g. customer cancelled, created in error)"></textarea></div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--tx-2);margin-top:8px;cursor:pointer">
          <input type="checkbox" id="arch-refund" checked> Refund payments made on this order</label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-danger" id="arch-go" onclick="OrdersPage.submitArchive('${orderId}')">Delete &amp; archive</button>
      </div>`, 'modal-narrow');
  },

  async submitArchive(orderId) {
    const reason = document.getElementById('arch-reason').value.trim();
    if (!reason) return Toast.error('Please enter a reason');
    const refund = document.getElementById('arch-refund').checked;
    const btn = document.getElementById('arch-go'); btn.disabled = true; btn.textContent = 'Archiving…';
    try {
      const r = await post(`/api/orders/${orderId}/archive`, { reason, refund });
      Toast.success(r.refunded > 0 ? `Archived — ${fmt.currency(r.refunded)} refunded` : 'Work order archived');
      Modal.close();
      this.load();
    } catch (e) {
      Toast.error(e.message || 'Could not archive');
      btn.disabled = false; btn.textContent = 'Delete & archive';
    }
  },

  async archivedModal() {
    let items = [];
    try { items = (await get('/api/orders/archived')).items || []; } catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Archived work orders</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        ${items.length ? `<table class="data-table"><thead><tr><th>Order</th><th>Client</th><th>Reason</th><th>By</th><th>When</th></tr></thead><tbody>
          ${items.map(o => `<tr><td>${o.order_number}</td><td>${o.client || '—'}</td><td>${o.archive_reason || '—'}</td><td>${o.archived_by || '—'}</td><td>${o.archived_at ? fmt.date(o.archived_at) : '—'}</td></tr>`).join('')}
        </tbody></table>` : '<div style="text-align:center;color:var(--tx-3);padding:22px">No archived orders.</div>'}
      </div>`, '');
  },

  messageModal(orderId) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Message customer</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Send an update to the customer about this order.</p>
        <div class="field"><label class="field-label">Channel</label>
          <select class="field-select" id="msg-channel">
            <option value="email">Email</option>
            <option value="sms">SMS</option>
          </select></div>
        <div class="field" id="msg-subject-wrap"><label class="field-label">Subject</label>
          <input class="field-input" id="msg-subject" placeholder="Update on your order"></div>
        <div class="field"><label class="field-label">Message</label>
          <textarea class="field-textarea" id="msg-body" rows="5" placeholder="Your device is ready for pickup."></textarea></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="msg-send" onclick="OrdersPage.sendMessage('${orderId}')">Send</button>
      </div>`, '', true);
    const chan = document.getElementById('msg-channel');
    chan.addEventListener('change', () => {
      document.getElementById('msg-subject-wrap').style.display = chan.value === 'email' ? '' : 'none';
    });
  },

  async sendMessage(orderId) {
    const channel = document.getElementById('msg-channel').value;
    const subject = document.getElementById('msg-subject')?.value || '';
    const body = document.getElementById('msg-body').value.trim();
    if (!body) return Toast.error('Enter a message');
    const btn = document.getElementById('msg-send'); btn.disabled = true; btn.textContent = 'Sending…';
    try {
      await post(`/api/orders/${orderId}/notify`, { channel, subject, body });
      Toast.success(channel === 'email' ? 'Email sent' : 'SMS sent');
      Modal.close();
    } catch (e) {
      Toast.error(e.message || 'Could not send — check the customer\u2019s contact details');
      btn.disabled = false; btn.textContent = 'Send';
    }
  },

  // Build 144eg — Order code (Orderry "Order code"): a scannable barcode OR QR of
  // the order's code, shared with the {Barcode}/{QR Code} print variables and the
  // /by-barcode scanner lookup, so screen + print + scan all resolve to this order.
  _ocMode: 'barcode',
  _ocType: 'code128',
  async orderCodeModal(orderId) {
    let o = {};
    try { o = await get(`/api/orders/${orderId}`); } catch (e) { return Toast.error(e.message); }
    this._ocMode = 'barcode'; this._ocType = 'code128';
    const val = o.barcode || o.order_number || '';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Order code</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:14px">Scan this barcode or QR code to open the order. It also prints on documents via the <b>{Barcode}</b> and <b>{QR Code}</b> variables.</div>
        <div class="seg-pill" id="oc-mode" style="margin-bottom:14px">
          <button type="button" class="on" data-m="barcode" onclick="OrdersPage._ocSetMode('barcode')">Barcode</button>
          <button type="button" data-m="qr" onclick="OrdersPage._ocSetMode('qr')">QR code</button>
        </div>
        <div class="field" id="oc-type-wrap"><label class="field-label">Barcode type</label>
          <select class="field-select" id="oc-type" onchange="OrdersPage._ocType=this.value;OrdersPage._ocPreview()">
            ${['code128','ean13','ean8','upca'].map(t=>`<option value="${t}">${t}</option>`).join('')}
          </select>
        </div>
        <div class="field"><label class="field-label">Code value</label>
          <div style="display:flex;gap:6px">
            <input class="field-input" id="oc-val" value="${(val+'').replace(/"/g,'&quot;')}" placeholder="Scan or type…" style="flex:1" oninput="OrdersPage._ocPreview()">
            <button class="btn btn-glass btn-sm" type="button" onclick="OrdersPage._ocGen()">${icon('box',13)} Generate</button>
          </div>
        </div>
        <div id="oc-preview" style="margin-top:14px;padding:14px;background:var(--g-fill-thin,rgba(120,140,190,.06));border-radius:var(--r-sm);text-align:center;min-height:80px;display:flex;align-items:center;justify-content:center"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage._ocSave('${orderId}')">Save</button>
      </div>`, 'modal-narrow');
    setTimeout(() => this._ocPreview(), 40);
  },

  _ocSetMode(m) {
    this._ocMode = m;
    document.querySelectorAll('#oc-mode button').forEach(b => b.classList.toggle('on', b.dataset.m === m));
    const tw = document.getElementById('oc-type-wrap'); if (tw) tw.style.display = m === 'qr' ? 'none' : '';
    this._ocPreview();
  },
  async _ocGen() {
    // 144ei — ask the server for a code guaranteed unique across every work order.
    try { const r = await get('/api/orders/code/generate'); document.getElementById('oc-val').value = r.barcode; }
    catch (e) { document.getElementById('oc-val').value = Barcode.value('WO', 'code128'); }
    this._ocPreview();
  },
  _ocPreview() {
    const v = (document.getElementById('oc-val').value || '').trim();
    Barcode.render('oc-preview', v, this._ocMode === 'qr' ? 'qr' : this._ocType, { height: 60 });
  },
  async _ocSave(orderId) {
    const v = (document.getElementById('oc-val').value || '').trim();
    try {
      await patch(`/api/orders/${orderId}/code`, { barcode: v });
      Toast.success('Order code saved'); Modal.close();
    } catch (e) { Toast.error(e.message); }
  },

  async printDocModal(orderId) {
    let tpls = [];
    // build 144o: order_id filters to docs allowed to print in this order's status
    try { tpls = (await get(`/api/documents/templates?order_id=${orderId}`)).items; } catch (e) { return Toast.error(e.message); }
    if (!tpls.length) return Toast.error('No document available for this order’s status — check the template’s print statuses in Settings → Document templates');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Print / generate document</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Template</label>
          <select class="field-select" id="doc-tpl">${tpls.map(t=>`<option value="${t.id}">${t.name}</option>`).join('')}</select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="OrdersPage.generateDoc('${orderId}')">Generate &amp; print</button>
      </div>`, 'modal-narrow');
  },

  async generateDoc(orderId) {
    const tid = document.getElementById('doc-tpl').value;
    try {
      const r = await get(`/api/documents/render/${orderId}/${tid}`);
      const w = window.open('', '_blank');
      if (w) {
        w.document.write(`<html><head><title>${r.name}</title></head><body>${r.html}<script>window.onload=()=>window.print()<\/script></body></html>`);
        w.document.close();
      }
      Modal.close();
      Toast.success('Document generated');
    } catch (e) { Toast.error(e.message); }
  },

  async deleteOrder(id) {
    // kept for callers that still reference it; routes to the archive flow
    this.archiveModal(id);
  },

  // ── Industry template wiring (Phase 4) ──────────────────
  IDENT_LABELS: { imei:'IMEI', vin:'VIN', hin:'HIN', serial:'Serial number', engine_serial:'Engine serial', asset_tag:'Asset tag', barcode:'Barcode' },

  async _loadTpl() {
    if (window.__tpl) return window.__tpl;
    try {
      const d = await get('/api/settings/industry-template');
      let groups = [];
      try { groups = (await get(`/api/settings/asset-groups?template_key=${d.current}`)).items || []; } catch (e) {}
      const identByGroup = { '': d.describe.identifier.primary };
      try {
        const rules = (await get(`/api/settings/asset-identifier-rules?template_key=${d.current}`)).items || [];
        rules.forEach(r => { identByGroup[r.asset_group_id || ''] = r.primary_kind; });
      } catch (e) {}
      window.__tpl = Object.assign({}, d.describe, { current: d.current, groups, identByGroup });
    } catch (e) { window.__tpl = null; }
    return window.__tpl;
  },

  _grpKind(groupId) {
    const t = window.__tpl;
    if (!t) return 'serial';
    return t.identByGroup[groupId || ''] || t.identByGroup[''] || 'serial';
  },

  async groupChanged() {
    const t = window.__tpl; if (!t) return;
    const groupId = document.getElementById('no-asset-group')?.value || '';
    const lbl = document.getElementById('no-ident-label');
    if (lbl) lbl.textContent = this.IDENT_LABELS[this._grpKind(groupId)] || 'Serial number';
    const dl = document.getElementById('no-brand-list');
    if (dl) {
      let url = `/api/settings/asset-brands?template_key=${t.current}`;
      if (groupId) url += `&asset_group_id=${groupId}`;
      try { dl.innerHTML = ((await get(url)).items || []).map(b => `<option value="${b.name}">`).join(''); } catch (e) {}
    }
    const md = document.getElementById('no-model-list'); if (md) md.innerHTML = '';
  },

  async brandChangedNew() {
    const t = window.__tpl; if (!t) return;
    const brand = (document.getElementById('no-asset-brand')?.value || '').trim();
    const md = document.getElementById('no-model-list'); if (!md || !brand) return;
    const groupId = document.getElementById('no-asset-group')?.value || '';
    let url = `/api/settings/asset-brands?template_key=${t.current}`;
    if (groupId) url += `&asset_group_id=${groupId}`;
    try {
      const brands = (await get(url)).items || [];
      const b = brands.find(x => x.name.toLowerCase() === brand.toLowerCase());
      if (!b) { md.innerHTML = ''; return; }
      const models = (await get(`/api/settings/asset-models?template_key=${t.current}&brand_id=${b.id}`)).items || [];
      md.innerHTML = models.map(m => `<option value="${m.name}">`).join('');
    } catch (e) {}
  },

  async scanResolveNew() {
    const t = window.__tpl;
    const val = (document.getElementById('no-asset-serial')?.value || '').trim();
    if (!val) return Toast.error('Enter an identifier first');
    const groupId = document.getElementById('no-asset-group')?.value || '';
    try {
      const body = { identifier: val, template_key: t?.current };
      if (groupId) body.asset_group_id = groupId;
      const r = await post('/api/assets/scan-resolve', body);
      if (r.matched && r.asset) {
        if (r.asset.brand) document.getElementById('no-asset-brand').value = r.asset.brand;
        if (r.asset.model) document.getElementById('no-asset-model').value = r.asset.model;
        return Toast.success(`Matched existing ${(t && t.asset_label) || 'asset'}: ${[r.asset.brand, r.asset.model].filter(Boolean).join(' ')}`.trim());
      }
      if (r.valid === false) return Toast.error(`Invalid ${(r.kind || '').toUpperCase()}`);
      const pf = r.prefill || {};
      if (pf.make || pf.brand) document.getElementById('no-asset-brand').value = pf.make || pf.brand;
      if (pf.model) document.getElementById('no-asset-model').value = pf.model;
      Toast.success(`${(r.kind || '').toUpperCase()} valid${pf.make ? ' · ' + pf.make : ''}`);
    } catch (e) { Toast.error(e.message || 'Lookup failed'); }
  },

  _newOrderAssetImg: null,

  // 144dc — photo for the asset being created inline on the New Work Order form.
  _pickOrderAssetImg(input) {
    const f = input.files && input.files[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) return Toast.error('Image must be under 5 MB');
    const r = new FileReader();
    r.onload = () => {
      this._newOrderAssetImg = r.result;
      const inner = document.getElementById('no-asset-drop-inner');
      if (inner) inner.innerHTML = `<img src="${r.result}" alt="asset" style="max-height:104px;max-width:100%;border-radius:10px"><div style="font-size:11px;color:var(--tx-3);margin-top:5px">Click to replace</div>`;
    };
    r.readAsDataURL(f);
  },

  // Open the full Orderry-style asset form; copy its fields back into the create form.
  async openFullAssetForm() {
    if (typeof AssetsPage === 'undefined' || !AssetsPage.newModal) return;
    const clientId = (window.ClientPicker && ClientPicker.value) || '';
    await AssetsPage.newModal({
      clientId,
      assetType: (document.getElementById('no-asset-group')?.selectedOptions?.[0]?.textContent || 'Vehicle').trim(),
      onCreate: (a) => {
        // reflect the created asset back onto the inline create form
        const set = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
        set('no-asset-brand', a.brand); set('no-asset-model', a.model);
        set('no-asset-serial', a.vin || a.serial || a.imei);
        if (a.image_data) { this._newOrderAssetImg = a.image_data; const inner = document.getElementById('no-asset-drop-inner'); if (inner) inner.innerHTML = `<img src="${a.image_data}" style="max-height:104px;max-width:100%;border-radius:10px">`; }
        Toast.success('Asset details filled in');
      },
    });
  },

  _identPayloadNew() {
    const val = document.getElementById('no-asset-serial')?.value || null;
    const kind = this._grpKind(document.getElementById('no-asset-group')?.value || '');
    return {
      asset_serial: ['serial', 'asset_tag', 'barcode', 'engine_serial'].includes(kind) ? val : null,
      asset_imei: kind === 'imei' ? val : null,
      asset_vin: kind === 'vin' ? val : null,
      asset_hin: kind === 'hin' ? val : null,
    };
  },

  async newModal(prefill = {}) {
    this._creatingEstimate = !!prefill.is_estimate;
    this._newOrderAssetImg = null;
    if (window.CatalogPicker) CatalogPicker.lines = [];   // Build 144dk: fresh picker; a step-1 create carries no stale lines
    await this._loadTpl();
    this._woAssetFields = await CustomFields.fetch('device');
    let employees = [], types = [], resources = [], me = null;
    try { employees = await get('/api/staff/employees'); } catch {}
    try { types = (await get('/api/forms/types')).items; } catch {}
    try { resources = (await get('/api/resources')).items; } catch {}
    try { me = await get('/api/auth/me'); } catch {}
    this._resources = resources;
    const schedVal = prefill.scheduled_for || '';
    // 144cz — the tech (specialist) and manager default to whoever is creating the
    // order, but both are freely changeable before saving.
    const meId = me && me.id ? me.id : '';
    const techVal = prefill.assigned_to_id || meId;
    const mgrVal  = prefill.manager_id || meId;
    this._types = types;
    const defaultType = types.find(t => t.is_default) || types[0];
    const tpl = window.__tpl;
    const assetLabel = (tpl && tpl.asset_label) || 'Asset';
    const tplGroups = (tpl && tpl.groups) || [];
    const ident0 = this.IDENT_LABELS[tpl ? this._grpKind(tplGroups[0]?.id || '') : 'serial'] || 'Serial number';

    Modal.open(`
      <div class="modal-header">
        <span class="modal-title">${this._creatingEstimate ? 'New estimate' : (tpl && tpl.wo_noun === 'ticket' ? 'New ticket' : 'New work order')}</span>
        <button class="modal-close" onclick="Modal.close()">×</button>
      </div>
      <div class="wo-steps">
        <div class="wo-step active" id="wostep-1"><span class="wo-step-n">1</span> General info</div>
        <div class="wo-step-line"></div>
        <div class="wo-step" id="wostep-2"><span class="wo-step-n">2</span> Services &amp; products</div>
      </div>
      <div class="modal-body">
        <!-- STEP 1 -->
        <div class="wo-page active" data-page="1">
          ${types.length ? `
          <div class="field">
            <label class="field-label">Work order type</label>
            <div class="type-chips" id="no-type-chips">
              ${types.map(t=>`<div class="type-chip${t.id===defaultType?.id?' active':''}" data-type="${t.id}" style="--tc:${t.color}" onclick="OrdersPage.pickType('${t.id}',this)">
                <span class="type-chip-dot" style="background:${t.color}"></span>${t.name}</div>`).join('')}
            </div>
          </div>` : ''}
          <div class="field">
            <label class="field-label">Client</label>
            <div id="no-client-picker"></div>
          </div>
          <div class="field-row">
            <div class="field">
              <label class="field-label">Priority</label>
              <select class="field-select" id="no-pri">
                <option value="0">Normal</option><option value="1">Urgent</option><option value="2">VIP</option>
              </select>
            </div>
            <div class="field">
              <label class="field-label">Manager</label>
              <select class="field-select" id="no-manager">
                <option value="">Unassigned</option>
                ${employees.map(e=>`<option value="${e.id}"${e.id===mgrVal?' selected':''}>${e.name}</option>`).join('')}
              </select>
            </div>
          </div>
          <div class="field-row">
            <div class="field">
              <label class="field-label">Specialist (assignee)</label>
              <select class="field-select" id="no-tech">
                <option value="">Unassigned</option>
                ${employees.map(e=>`<option value="${e.id}"${e.id===techVal?' selected':''}>${e.name}</option>`).join('')}
              </select>
            </div>
            <div class="field"></div>
          </div>
          <div class="field-row">
            <div class="field">
              <label class="field-label">Scheduled for</label>
              <input class="field-input" type="datetime-local" id="no-sched" value="${schedVal}">
            </div>
            <div class="field">
              <label class="field-label">Duration (minutes)</label>
              <input class="field-input" type="number" id="no-dur" value="60" min="15" step="15">
            </div>
          </div>
          ${resources.length ? `<div class="field">
            <label class="field-label">Resource (bay / room / equipment)</label>
            <select class="field-select" id="no-resource"><option value="">Not specified</option>
              ${resources.map(r=>`<option value="${r.id}">${r.name}${r.kind?' · '+r.kind:''}</option>`).join('')}
            </select>
          </div>` : ''}
          <div class="field">
            <label class="field-label">Complaint / description</label>
            <textarea class="field-textarea" id="no-complaint" placeholder="What's the issue?"></textarea>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;margin:6px 0 8px">
            <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em">${assetLabel} <span style="font-weight:600;text-transform:none;color:var(--tx-4)">— auto-added to the client's warehouse</span></div>
            <button class="btn btn-glass btn-sm" type="button" onclick="OrdersPage.openFullAssetForm()">${icon('external-link',12)} Full asset form (VIN, plate…)</button>
          </div>
          <div class="asset-form-grid" style="margin-bottom:10px">
            <div class="asset-drop" id="no-asset-drop" onclick="document.getElementById('no-asset-file').click()">
              <input type="file" id="no-asset-file" accept="image/png,image/jpeg,image/jpg" style="display:none" onchange="OrdersPage._pickOrderAssetImg(this)">
              <div id="no-asset-drop-inner">${icon('camera',20)}<div style="font-weight:700;margin-top:6px">Add a photo <span class="wo-link">browse</span></div><div style="font-size:11px;color:var(--tx-4);margin-top:3px">JPG / PNG · up to 5 MB</div></div>
            </div>
            <div class="asset-form-fields">
          <div class="field-row">
            <div class="field"><label class="field-label">Group</label>
              <select class="field-select" id="no-asset-group" onchange="OrdersPage.groupChanged()">
                ${tplGroups.length ? tplGroups.map(g=>`<option value="${g.id}">${g.label}</option>`).join('') : '<option value="">—</option>'}
              </select>
            </div>
            <div class="field"><label class="field-label">Brand</label>
              <input class="field-input" id="no-asset-brand" list="no-brand-list" placeholder="Type or pick…" oninput="OrdersPage.brandChangedNew()">
              <datalist id="no-brand-list"></datalist>
            </div>
          </div>
          <div class="field-row">
            <div class="field"><label class="field-label">Model</label>
              <input class="field-input" id="no-asset-model" list="no-model-list" placeholder="Type or pick…">
              <datalist id="no-model-list"></datalist>
            </div>
            <div class="field"><label class="field-label" id="no-ident-label">${ident0}</label>
              <div style="display:flex;gap:6px">
                <input class="field-input" id="no-asset-serial" placeholder="Scan or type…" style="flex:1">
                <button class="btn btn-glass btn-sm" type="button" title="Scan / look up" onclick="OrdersPage.scanResolveNew()">${icon('search',13)}</button>
                <button class="btn btn-glass btn-sm" type="button" onclick="OrdersPage.genOrderSerial()">${icon('refresh',13)}</button>
              </div>
            </div>
          </div>
            </div>
          </div>
          ${CustomFields.render(this._woAssetFields, {}, 'no-cf')}
          <div id="no-custom-fields"></div>
        </div>

        <!-- STEP 2 -->
        <div class="wo-page" data-page="2">
          <div style="font-size:12px;color:var(--tx-3);margin-bottom:10px">Add the services and products for this order. You can also create it now and add these later.</div>
          <div id="no-catalog"></div>
        </div>
      </div>
      <div class="modal-footer" id="wo-foot">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="wo-next" onclick="OrdersPage.woStep(2)">Next: Services &amp; products →</button>
      </div>`, 'modal-side-wide');
    ClientPicker.mount('no-client-picker');
    CatalogPicker.repairPricing = true;
    await CatalogPicker.mount('no-catalog');
    this._activeType = defaultType?.id || null;
    this._woStep = 1;
    if (this._activeType) {
      this.renderCustomFields(this._activeType);
      this._applyTypeAssetDefault(defaultType);
    }
    this.groupChanged();   // populate brand suggestions + identifier label for the default group
  },

  _woStep: 1,

  woStep(n) {
    this._woStep = n;
    document.querySelectorAll('.wo-page').forEach(p => p.classList.toggle('active', p.dataset.page == n));
    document.getElementById('wostep-1').classList.toggle('active', n >= 1);
    document.getElementById('wostep-2').classList.toggle('active', n >= 2);
    const foot = document.getElementById('wo-foot');
    if (n === 2) {
      const verb = this._creatingEstimate ? 'estimate' : (window.__tpl && window.__tpl.wo_noun === 'ticket' ? 'ticket' : 'order');
      foot.innerHTML = `
        <button class="btn btn-glass" onclick="OrdersPage.woStep(1)">← Back</button>
        <button class="btn btn-glass" onclick="OrdersPage.submitNew('another')" title="Create and immediately start another">Save &amp; create another</button>
        <button class="btn btn-glass" onclick="OrdersPage.submitNew('open')" title="Create and open the record">Create &amp; open</button>
        <button class="btn btn-primary" onclick="OrdersPage.submitNew()">Create ${verb}</button>`;
    } else {
      // Build 144dk — allow finishing from step 1 (no services/products needed);
      // Orderry lets you save a job straight away.
      const verb1 = this._creatingEstimate ? 'estimate' : (window.__tpl && window.__tpl.wo_noun === 'ticket' ? 'ticket' : 'order');
      foot.innerHTML = `
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-glass" onclick="OrdersPage.submitNew()">Create ${verb1}</button>
        <button class="btn btn-primary" onclick="OrdersPage.woStep(2)">Next: Services &amp; products →</button>`;
    }
  },

  _applyTypeAssetDefault(type) {
    if (!type || !type.default_asset_type) return;
    const sel = document.getElementById('no-asset-type');
    if (sel) sel.value = type.default_asset_type;
  },

  _types: [],
  _activeType: null,
  _customFieldDefs: [],

  pickType(id, el) {
    this._activeType = id;
    document.querySelectorAll('#no-type-chips .type-chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    this.renderCustomFields(id);
    const tdef = this._types.find(t => t.id === id);
    this._applyTypeAssetDefault(tdef);
    // Phase G: apply the type's default scheduling duration
    const durEl = document.getElementById('no-dur');
    if (durEl && tdef?.sched_default_duration) durEl.value = tdef.sched_default_duration;
  },

  async renderCustomFields(typeId) {
    const wrap = document.getElementById('no-custom-fields');
    if (!wrap) return;
    let fields = [];
    try { fields = (await get(`/api/forms/types/${typeId}/fields`)).items; } catch {}
    this._customFieldDefs = fields;
    if (!fields.length) { wrap.innerHTML = ''; return; }
    wrap.innerHTML = `
      <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.07em;margin:6px 0 10px">Type details</div>
      ${fields.map(f => this._customFieldInput(f)).join('')}`;
  },

  _customFieldInput(f) {
    const id = `cf-${f.field_key}`;
    if (f.field_type === 'header')
      return `<div style="font-size:11px;font-weight:800;color:var(--tx-2);margin:12px 0 6px;padding-bottom:4px;border-bottom:1px solid rgba(255,255,255,.5)">${f.field_label}</div>`;
    let input = '';
    if (f.field_type === 'textarea')
      input = `<textarea class="field-textarea" id="${id}" data-key="${f.field_key}" placeholder="${f.placeholder||''}"></textarea>`;
    else if (f.field_type === 'select')
      input = `<select class="field-select" id="${id}" data-key="${f.field_key}"><option value="">—</option>${(f.options||[]).map(o=>`<option>${o}</option>`).join('')}</select>`;
    else if (f.field_type === 'bool')
      input = `<label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--tx-2);cursor:pointer"><input type="checkbox" id="${id}" data-key="${f.field_key}" style="width:auto;accent-color:var(--blue)"> Yes</label>`;
    else {
      const t = f.field_type === 'number' ? 'number' : f.field_type === 'date' ? 'date' : 'text';
      input = `<input class="field-input" type="${t}" id="${id}" data-key="${f.field_key}" placeholder="${f.placeholder||''}">`;
    }
    return `<div class="field"><label class="field-label">${f.field_label}${f.required?' *':''}</label>${input}</div>`;
  },

  async submitNew(mode = 'create') {
    const lines = CatalogPicker.payload();
    try {
      const me = await get('/api/auth/me');
      const sched = document.getElementById('no-sched')?.value || null;
      const dur = parseInt(document.getElementById('no-dur')?.value) || 60;
      const resourceId = document.getElementById('no-resource')?.value || null;
      // Phase G: enforce the type's "Scheduled for required" toggle
      const tdef = (this._types || []).find(t => t.id === this._activeType);
      if (tdef?.sched_required && !sched) return Toast.error('This work order type requires a schedule slot');
      // compute scheduled_to from start + duration
      let scheduledTo = null;
      if (sched) {
        const end = new Date(sched);
        end.setMinutes(end.getMinutes() + dur);
        // keep local datetime format (no Z)
        const pad = n => String(n).padStart(2,'0');
        scheduledTo = `${end.getFullYear()}-${pad(end.getMonth()+1)}-${pad(end.getDate())}T${pad(end.getHours())}:${pad(end.getMinutes())}`;
      }
      // gather custom field values
      const custom_data = {};
      (this._customFieldDefs || []).forEach(f => {
        if (f.field_type === 'header') return;
        const el = document.getElementById(`cf-${f.field_key}`);
        if (!el) return;
        custom_data[f.field_key] = f.field_type === 'bool' ? el.checked : el.value;
      });
      const created = await post('/api/orders', {
        client_id: ClientPicker.value,
        complaint:  document.getElementById('no-complaint').value,
        priority:   parseInt(document.getElementById('no-pri').value) || 0,
        assigned_to_id: document.getElementById('no-tech').value || null,
        manager_id: document.getElementById('no-manager')?.value || null,
        scheduled_for: sched,
        scheduled_to: scheduledTo,
        duration_min: dur,
        resource_id: resourceId,
        type_id: this._activeType || null,
        custom_data,
        asset_brand:  document.getElementById('no-asset-brand')?.value || null,
        asset_model:  document.getElementById('no-asset-model')?.value || null,
        ...OrdersPage._identPayloadNew(),
        template_key: (window.__tpl && window.__tpl.current) || null,
        asset_group_id: document.getElementById('no-asset-group')?.value || null,
        asset_type:   document.getElementById('no-asset-group')?.selectedOptions?.[0]?.textContent?.trim() || null,
        asset_custom_data: CustomFields.collect(this._woAssetFields || [], 'no-cf'),
        asset_image: OrdersPage._newOrderAssetImg || null,
        location_id: me.location_id, lines,
        is_estimate: !!this._creatingEstimate,
      });
      Toast.success(this._creatingEstimate ? 'Estimate created' : 'Work order created');
      const wasEstimate = !!this._creatingEstimate;
      Modal.close();
      // 144bh WS4: throughput actions
      if (mode === 'another') {
        this.refreshActiveView();
        return this.newModal({ is_estimate: wasEstimate });   // straight into a fresh one
      }
      if (mode === 'open' && created && created.id) {
        this.refreshActiveView();
        return this.viewOrder(created.id);                    // open the record we just made
      }
      // default: jump to the matching doc-type so the new record is visible
      if (wasEstimate !== this._isEstimate) {
        this.setDocType(wasEstimate);
      } else {
        this.refreshActiveView();
      }
    } catch (e) { Toast.error(e.message); }
  },

  refreshActiveView() {
    // refresh whichever view is showing so new/edited orders appear immediately
    this.reload();                       // table tbody
    if (this._view === 'board') this.loadBoard();
    if (this._view === 'scheduler' && typeof Scheduler !== "undefined") Scheduler.load();
    this._loadKpis && this._loadKpis();
  },

  async genOrderSerial() {
    try {
      const r = await get('/api/assets/generate-serial?asset_type=Device');
      document.getElementById('no-asset-serial').value = r.serial;
      Toast.success('Serial generated');
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('orders', () => OrdersPage.load());
