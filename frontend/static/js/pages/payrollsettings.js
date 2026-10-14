/* Settings > Payroll — commission rules driving the accrual engine. */
const PayrollSettingsPage = {
  _rules: [], _employees: [], _events: [], _statuses: [], _types: [],
  EVENT_LABELS: { order_created: 'Order created', order_closed: 'Order closed',
                  order_closed_success: 'Order closed — successful', order_closed_fail: 'Order closed — unsuccessful',
                  labor_performed: 'Labor performed (per line)', product_sold: 'Product sold (per line)',
                  assignee_labor: 'Assignee — labor', assignee_product: 'Assignee — product' },

  // Build 144g: hosted as a tab under Settings → Employees & roles. When embedded,
  // render into the given element instead of taking over the settings pane.
  _embedBody: null,
  _setBody(html) {
    if (this._embedBody) { const el = document.getElementById(this._embedBody); if (el) { el.innerHTML = html; return; } }
    setContent(html);
  },
  _setActions(html) { if (!this._embedBody) setTopbarActions(html); },
  async mountInto(bodyId) { this._embedBody = bodyId; await this.load(); },

  async load() {
    this._setActions(`<button class="btn btn-primary" onclick="PayrollSettingsPage.ruleModal()">+ New rule</button>`);
    this._setBody(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:720px">
        Commission rules accrue automatically when orders close: <b>labor</b> pays the technician,
        <b>products</b> pay the seller. Percent rules compute on the line total or the margin; the
        coefficient scales for seniority. Company-paid discounts never reduce the basis.
      </div>
      <div id="pr-list"></div>`);
    await this.reload();
  },

  async reload() {
    const el = document.getElementById('pr-list');
    if (!el) return;
    try {
      const [r, emps, sts, types] = await Promise.all([
        get('/api/payroll/rules'),
        get('/api/staff/employees'),
        get('/api/statuses').catch(() => ({ items: [] })),
        get('/api/forms/types').catch(() => ({ items: [] })),
      ]);
      this._rules = r.items; this._events = r.events || Object.keys(this.EVENT_LABELS); this._employees = emps;
      this._statuses = (sts.items || sts || []).filter(s => s.key);
      this._types = types.items || [];
      const empName = id => { const e = this._employees.find(x => x.id === id); return e ? e.name : 'All employees'; };
      el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Rule</th><th>Event</th><th>Pays</th><th>Conditions</th><th>Who</th><th style="width:130px"></th></tr></thead>
        <tbody>${this._rules.map(x => `<tr>
          <td class="tbl-strong">${x.name}</td>
          <td>${this.EVENT_LABELS[x.event] || x.event}</td>
          <td>${x.calc_type === 'percent'
            ? `${x.amount}% of ${x.percent_basis}${x.coefficient !== 1 ? ` x${x.coefficient}` : ''}`
            : `${fmt.currency(x.amount)} flat${x.coefficient !== 1 ? ` x${x.coefficient}` : ''}`}</td>
          <td style="color:var(--tx-3);font-size:12px">${this._conds(x)}</td>
          <td style="color:var(--tx-3)">${empName(x.employee_id)}${x.category ? ` · ${x.category}` : ''}</td>
          <td style="text-align:right">
            <button class="btn btn-glass btn-sm" onclick="PayrollSettingsPage.ruleModal('${x.id}')">Edit</button>
            <button class="btn btn-glass btn-sm" onclick="PayrollSettingsPage.remove('${x.id}')">Delete</button></td>
        </tr>`).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:22px">No rules — commissions won't accrue until you add one</td></tr>`}
        </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  _conds(x) {
    const bits = [];
    if (x.calc_timing === 'immediately') bits.push('immediately');
    else if (x.calc_timing === 'on_status') {
      const s = this._statuses.find(s => s.key === x.trigger_status_key);
      bits.push(`at “${s ? s.name : x.trigger_status_key}”`);
    }
    if (x.term_condition && x.term_condition !== 'any') bits.push(x.term_condition.replace('_', ' '));
    if (x.wo_type_id) {
      const t = this._types.find(t => t.id === x.wo_type_id);
      if (t) bits.push(t.name);
    }
    if (x.cancel_if_deleted === false) bits.push('keeps on delete');
    return bits.length ? bits.join(' · ') : 'on close · any';
  },

  ruleModal(id) {
    const r = id ? this._rules.find(x => x.id === id) : null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${r ? 'Edit rule' : 'New commission rule'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Rule name</label>
          <input class="field-input" id="pr-name" value="${r ? r.name : ''}" placeholder="Tech labor 10%"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Trigger event</label>
            <select class="field-select" id="pr-event">${this._events.map(e => `<option value="${e}" ${r && r.event === e ? 'selected' : ''}>${this.EVENT_LABELS[e] || e}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Applies to</label>
            <select class="field-select" id="pr-emp"><option value="">All employees</option>
              ${this._employees.map(e => `<option value="${e.id}" ${r && r.employee_id === e.id ? 'selected' : ''}>${e.name}</option>`).join('')}</select></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Type</label>
            <select class="field-select" id="pr-calc">
              <option value="percent" ${!r || r.calc_type === 'percent' ? 'selected' : ''}>Percent</option>
              <option value="flat" ${r && r.calc_type === 'flat' ? 'selected' : ''}>Flat amount</option></select></div>
          <div class="field"><label class="field-label">Amount</label>
            <input class="field-input" id="pr-amount" type="number" value="${r ? r.amount : 10}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Percent basis</label>
            <select class="field-select" id="pr-basis">
              <option value="total" ${!r || r.percent_basis === 'total' ? 'selected' : ''}>Line total</option>
              <option value="margin" ${r && r.percent_basis === 'margin' ? 'selected' : ''}>Margin (price minus cost)</option></select></div>
          <div class="field"><label class="field-label">Coefficient</label>
            <input class="field-input" id="pr-coef" type="number" step="0.1" value="${r ? r.coefficient : 1}"></div>
        </div>
        <div style="margin:16px 0 8px;font-weight:800;font-size:13px">Conditions <span style="color:var(--tx-4);font-weight:500">— when this rule pays</span></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Calculate</label>
            <select class="field-select" id="pr-timing" onchange="PayrollSettingsPage._toggleTrigger()">
              <option value="on_close" ${!r || r.calc_timing === 'on_close' ? 'selected' : ''}>When the order closes</option>
              <option value="immediately" ${r && r.calc_timing === 'immediately' ? 'selected' : ''}>Immediately (any status change)</option>
              <option value="on_status" ${r && r.calc_timing === 'on_status' ? 'selected' : ''}>When status becomes…</option>
            </select></div>
          <div class="field" id="pr-trigger-wrap" style="${r && r.calc_timing === 'on_status' ? '' : 'display:none'}">
            <label class="field-label">Trigger status</label>
            <select class="field-select" id="pr-trigger">
              ${this._statuses.map(s => `<option value="${s.key}" ${r && r.trigger_status_key === s.key ? 'selected' : ''}>${s.name}</option>`).join('')}
            </select></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Execution term</label>
            <select class="field-select" id="pr-term">
              <option value="any" ${!r || r.term_condition === 'any' ? 'selected' : ''}>Any</option>
              <option value="on_time" ${r && r.term_condition === 'on_time' ? 'selected' : ''}>On time only</option>
              <option value="overdue" ${r && r.term_condition === 'overdue' ? 'selected' : ''}>Overdue only</option>
              <option value="urgent" ${r && r.term_condition === 'urgent' ? 'selected' : ''}>Urgent only</option>
            </select></div>
          <div class="field"><label class="field-label">Work order type</label>
            <select class="field-select" id="pr-wotype"><option value="">Any type</option>
              ${this._types.map(t => `<option value="${t.id}" ${r && r.wo_type_id === t.id ? 'selected' : ''}>${t.name}</option>`).join('')}
            </select></div>
        </div>
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-top:6px">
          <input type="checkbox" id="pr-cancel" ${!r || r.cancel_if_deleted ? 'checked' : ''}>
          Cancel the commission if the order is deleted/archived
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="PayrollSettingsPage.submit(${r ? `'${r.id}'` : 'null'})">${r ? 'Save' : 'Create rule'}</button>
      </div>`);
  },

  _toggleTrigger() {
    const on = document.getElementById('pr-timing').value === 'on_status';
    document.getElementById('pr-trigger-wrap').style.display = on ? '' : 'none';
  },

  async submit(id) {
    const timing = document.getElementById('pr-timing').value;
    const body = {
      name: document.getElementById('pr-name').value.trim() || 'Commission',
      event: document.getElementById('pr-event').value,
      employee_id: document.getElementById('pr-emp').value || null,
      calc_type: document.getElementById('pr-calc').value,
      amount: parseFloat(document.getElementById('pr-amount').value) || 0,
      percent_basis: document.getElementById('pr-basis').value,
      coefficient: parseFloat(document.getElementById('pr-coef').value) || 1,
      calc_timing: timing,
      trigger_status_key: timing === 'on_status' ? document.getElementById('pr-trigger').value : null,
      term_condition: document.getElementById('pr-term').value,
      wo_type_id: document.getElementById('pr-wotype').value || null,
      cancel_if_deleted: document.getElementById('pr-cancel').checked,
    };
    if (timing === 'on_status' && !body.trigger_status_key) return Toast.error('Pick a trigger status');
    try {
      if (id) await put(`/api/payroll/rules/${id}`, body); else await post('/api/payroll/rules', body);
      Modal.close(); Toast.success('Rule saved'); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async remove(id) {
    if (!(await confirm2('Delete this rule? Future closes stop accruing it (existing ledger entries stay).'))) return;
    try { await del(`/api/payroll/rules/${id}`); await this.reload(); } catch (e) { Toast.error(e.message); }
  },
};
