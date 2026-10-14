/* Build 144w — Settings → Automation.

   Workflow rules engine: WHEN an event happens (and optional conditions match)
   THEN run an action — create a task, assign a technician, tag the order, notify,
   or call a webhook. Complements Notifications (messaging) and keeps the older
   "task from a missed call" background toggle as a convenience card. */
const AutomationsPage = {
  _rules: [], _meta: null, _statuses: [], _employees: [], _missed: null,
  _embedBody: null,   // build 144w: hosted inside the Notifications settings page (no new menu)

  _setActions(html) { if (!this._embedBody) setTopbarActions(html); },
  _setBody(html) {
    if (this._embedBody) { const el = document.getElementById(this._embedBody); if (el) { el.innerHTML = html; return; } }
    setContent(html);
  },
  async mountInto(bodyId) { this._embedBody = bodyId; await this.load(); },

  async load() {
    this._setActions(`
      <button class="btn btn-glass btn-sm" onclick="AutomationsPage.runSweep()" title="Fire time-based rules now (overdue / low stock)">Run sweep</button>
      <button class="btn btn-primary" onclick="AutomationsPage.edit()">+ New rule</button>`);
    this._setBody(`<div id="auto-body"><div class="card" style="padding:20px;color:var(--tx-4)">Loading…</div></div>`);
    try {
      this._meta = await get('/api/automations/meta');
      this._rules = (await get('/api/automations')).items || [];
    } catch (e) { return Toast.error(e.message); }
    try { this._statuses = (await get('/api/statuses?is_estimate=false')).items || []; } catch { this._statuses = []; }
    try { this._employees = (await get('/api/staff/employees')).items || []; } catch { this._employees = []; }
    try { this._missed = await get('/api/comms/automations/task_on_missed_call'); } catch { this._missed = null; }
    this._render();
  },

  _eventLabel(k) { return (this._meta.events.find(e => e.key === k) || {}).label || k; },
  _actionLabel(k) { return (this._meta.actions.find(a => a.key === k) || {}).label || k; },

  _summary(r) {
    const c = r.action_config || {};
    if (r.action_type === 'create_task') return `Create task “${c.title || 'Follow up'}”`;
    if (r.action_type === 'assign') { const e = this._employees.find(x => x.id === c.employee_id); return `Assign to ${e ? e.name : '—'}`; }
    if (r.action_type === 'add_tag') return `Tag “${c.tag || ''}”`;
    if (r.action_type === 'notify') return `Notify: ${(c.message || '').slice(0, 40)}`;
    if (r.action_type === 'webhook') return `Webhook ${c.url || ''}`;
    return this._actionLabel(r.action_type);
  },

  _render() {
    const rows = this._rules.map(r => `
      <tr>
        <td class="tbl-strong">${(r.name || '').replace(/</g, '&lt;')}</td>
        <td>${this._eventLabel(r.event)}${r.event === 'status_changed' && r.trigger_status ? ` → <b>${r.trigger_status}</b>` : ''}</td>
        <td>${(r.conditions || []).length ? (r.conditions).map(c => `${c.field} ${c.op} ${c.value}`).join(', ') : '<span style="color:var(--tx-4)">any</span>'}</td>
        <td>${this._summary(r)}</td>
        <td style="text-align:center">${r.fire_count || 0}</td>
        <td style="text-align:center">${r.active ? '<span class="badge b-ok">on</span>' : '<span class="badge">off</span>'}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn btn-glass btn-sm" onclick="AutomationsPage.edit('${r.id}')">Edit</button>
          <button class="btn btn-glass btn-sm" onclick="AutomationsPage.remove('${r.id}')">Delete</button>
        </td>
      </tr>`).join('');

    const missed = this._missed ? `
      <div class="card" style="padding:16px;max-width:720px;margin-bottom:16px">
        <label class="cash-check" style="align-items:flex-start;gap:12px">
          <input type="checkbox" id="am-missed" ${this._missed.enabled ? 'checked' : ''} onchange="AutomationsPage.saveMissed()">
          <span><b>Task from a missed call</b><br>
          <span style="font-size:12.5px;color:var(--tx-3)">A missed inbound call becomes a “call back” task — to the caller's account manager if known, else the fallback below.</span></span>
        </label>
        <div style="display:flex;gap:12px;margin-top:10px;flex-wrap:wrap">
          <div class="field" style="margin:0"><label class="field-label">Fallback assignee</label>
            <select class="field-select" id="am-assign" onchange="AutomationsPage.saveMissed()"><option value="">Unassigned</option>
              ${this._employees.map(e => `<option value="${e.id}" ${(this._missed.config || {}).assign_to === e.id ? 'selected' : ''}>${e.name}</option>`).join('')}</select></div>
          <div class="field" style="margin:0"><label class="field-label">Priority</label>
            <select class="field-select" id="am-priority" onchange="AutomationsPage.saveMissed()">
              ${['normal', 'high', 'urgent'].map(p => `<option value="${p}" ${((this._missed.config || {}).priority ?? 'high') === p ? 'selected' : ''}>${p[0].toUpperCase() + p.slice(1)}</option>`).join('')}</select></div>
        </div>
      </div>` : '';

    const inlineActions = this._embedBody ? `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap">
        <div style="font-size:14px;font-weight:800;color:var(--tx-1)">Automations</div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-glass btn-sm" onclick="AutomationsPage.runSweep()">Run sweep</button>
          <button class="btn btn-primary btn-sm" onclick="AutomationsPage.edit()">+ New rule</button>
        </div>
      </div>` : '';

    this._setBody(`
      <div id="auto-body">
        ${inlineActions}
        <div style="font-size:13px;color:var(--tx-3);max-width:680px;margin-bottom:14px">
          Automations run as work orders move through their lifecycle. Messaging uses the
          templates above; these rules do the rest — tasks, assignment, tagging, and webhooks.
        </div>
        ${missed}
        <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Rule</th><th>When</th><th>Conditions</th><th>Then</th><th>Fired</th><th>Status</th><th></th></tr></thead>
          <tbody>${rows || `<tr><td colspan="7" style="text-align:center;color:var(--tx-4);padding:24px">
            No automations yet — create one with <b>+ New rule</b></td></tr>`}</tbody>
        </table></div></div>
      </div>`);
  },

  edit(id = null) {
    const r = id ? this._rules.find(x => x.id === id) : null;
    const cfg = (r && r.action_config) || {};
    const cond = (r && r.conditions && r.conditions[0]) || {};
    const opt = (arr, sel) => arr.map(([k, l]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${l}</option>`).join('');
    const evOpts = this._meta.events.map(e => [e.key, e.label]);
    const acOpts = this._meta.actions.map(a => [a.key, a.label]);

    Modal.open(`
      <div class="modal-header"><span class="modal-title">${r ? 'Edit' : 'New'} automation</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name</label>
          <input class="field-input" id="au-name" value="${(r?.name || '').replace(/"/g, '&quot;')}" placeholder="e.g. Notify on Ready"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">When (event)</label>
            <select class="field-select" id="au-event" onchange="AutomationsPage._toggle()">${opt(evOpts, r?.event || 'status_changed')}</select></div>
          <div class="field" id="au-status-wrap"><label class="field-label">Status is</label>
            <select class="field-select" id="au-status">
              <option value="">(any)</option>
              ${this._statuses.map(s => `<option value="${s.key}" ${r?.trigger_status === s.key ? 'selected' : ''}>${s.name}</option>`).join('')}
            </select></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Then (action)</label>
            <select class="field-select" id="au-action" onchange="AutomationsPage._toggle()">${opt(acOpts, r?.action_type || 'create_task')}</select></div>
          <div class="field" id="au-actcfg"></div>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:12px 0 6px">Condition (optional)</div>
        <div class="field-row" style="align-items:end">
          <div class="field"><label class="field-label">Field</label>
            <select class="field-select" id="au-cfield"><option value="">(none)</option>
              ${this._meta.condition_fields.map(f => `<option value="${f}" ${cond.field === f ? 'selected' : ''}>${f}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Op</label>
            <select class="field-select" id="au-cop">${this._meta.ops.map(o => `<option value="${o}" ${cond.op === o ? 'selected' : ''}>${o}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Value</label>
            <input class="field-input" id="au-cval" value="${cond.value != null ? String(cond.value).replace(/"/g, '&quot;') : ''}"></div>
        </div>
        <label class="cash-check" style="margin-top:10px"><input type="checkbox" id="au-active" ${r ? (r.active ? 'checked' : '') : 'checked'}> Active</label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="AutomationsPage.save(${id ? `'${id}'` : 'null'})">Save</button>
      </div>`);
    this._toggle(cfg);
  },

  _toggle(cfg = {}) {
    const ev = document.getElementById('au-event').value;
    document.getElementById('au-status-wrap').style.display = ev === 'status_changed' ? '' : 'none';
    const act = document.getElementById('au-action').value;
    const wrap = document.getElementById('au-actcfg');
    if (act === 'create_task') wrap.innerHTML = `<label class="field-label">Task title</label><input class="field-input" id="au-cfg" value="${(cfg.title || '').replace(/"/g, '&quot;')}" placeholder="Follow up">`;
    else if (act === 'assign') wrap.innerHTML = `<label class="field-label">Technician</label><select class="field-select" id="au-cfg">${this._employees.map(e => `<option value="${e.id}" ${cfg.employee_id === e.id ? 'selected' : ''}>${e.name}</option>`).join('')}</select>`;
    else if (act === 'add_tag') wrap.innerHTML = `<label class="field-label">Tag</label><input class="field-input" id="au-cfg" value="${(cfg.tag || '').replace(/"/g, '&quot;')}" placeholder="vip">`;
    else if (act === 'notify') wrap.innerHTML = `<label class="field-label">Message</label><input class="field-input" id="au-cfg" value="${(cfg.message || '').replace(/"/g, '&quot;')}" placeholder="Order is ready">`;
    else if (act === 'webhook') wrap.innerHTML = `<label class="field-label">Webhook URL</label><input class="field-input" id="au-cfg" value="${(cfg.url || '').replace(/"/g, '&quot;')}" placeholder="https://…">`;
    else wrap.innerHTML = '';
  },

  _collectConfig() {
    const act = document.getElementById('au-action').value;
    const v = (document.getElementById('au-cfg') || {}).value || '';
    if (act === 'create_task') return { title: v };
    if (act === 'assign') return { employee_id: v };
    if (act === 'add_tag') return { tag: v };
    if (act === 'notify') return { message: v };
    if (act === 'webhook') return { url: v };
    return {};
  },

  async save(id) {
    const name = document.getElementById('au-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const ev = document.getElementById('au-event').value;
    const conds = [];
    const cf = document.getElementById('au-cfield').value;
    if (cf) conds.push({ field: cf, op: document.getElementById('au-cop').value, value: document.getElementById('au-cval').value });
    const body = {
      name, event: ev,
      trigger_status: ev === 'status_changed' ? (document.getElementById('au-status').value || null) : null,
      conditions: conds,
      action_type: document.getElementById('au-action').value,
      action_config: this._collectConfig(),
      active: document.getElementById('au-active').checked,
    };
    try {
      if (id) await put(`/api/automations/${id}`, body); else await post('/api/automations', body);
      Modal.close(); Toast.success(id ? 'Rule updated' : 'Rule created'); await this.load();
    } catch (e) { Toast.error(e.message || 'Save failed'); }
  },

  async remove(id) {
    if (!await confirm2('Delete this automation?')) return;
    try { await del(`/api/automations/${id}`); Toast.success('Deleted'); await this.load(); }
    catch (e) { Toast.error(e.message); }
  },

  async runSweep() {
    try { const r = await post('/api/automations/sweep', {}); Toast.success(`Sweep fired ${r.fired} rule(s)`); }
    catch (e) { Toast.error(e.message || 'Sweep failed'); }
  },

  async saveMissed() {
    const body = {
      enabled: document.getElementById('am-missed').checked,
      config: {
        assign_to: document.getElementById('am-assign').value || null,
        priority: document.getElementById('am-priority').value,
      },
    };
    try { await put('/api/comms/automations/task_on_missed_call', body); Toast.success('Saved'); }
    catch (e) { Toast.error(e.message || 'Could not save'); }
  },
};
