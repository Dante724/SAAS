/* ════════════════════════════════════════════════════
   Settings → Notifications  (Build 144em — Orderry-style Workflows)

   Two rule sections (Orders, Bookings). Each shows a "+ Notification"
   button above a rules table, and a "+ Reminder" button above a
   reminders table — exactly like Orderry's External notifications ›
   Workflows. Rules open in a right-side slide-in panel. Templates
   (the message bodies the rules render) live in their own section
   below, with the workflow automation rules kept at the very bottom.
════════════════════════════════════════════════════ */

const NotificationsPage = {
  rules: [],
  templates: [],
  variables: [],
  variableGroups: {}, // grouped, resolvable variable catalog (Orderry-style)
  statuses: [],       // order statuses (incl. estimate) → key/name/color
  locations: [],

  // Section definitions: which module + which events it exposes.
  SECTIONS: [
    { module: 'order', title: 'Orders',
      sub: 'Keep your clients informed with updates on key order events.',
      events: [
        { key: 'created',        label: 'Order created' },
        { key: 'status_changed', label: 'Status changed to' },
        { key: 'closed',         label: 'Order closed' },
      ], hasStatus: true },
    { module: 'booking', title: 'Bookings',
      sub: 'Remind and update clients about their appointments.',
      events: [
        { key: 'created',        label: 'Booking created' },
        { key: 'status_changed', label: 'Status changed' },
        { key: 'changed',        label: 'Booking rescheduled' },
      ], hasStatus: false },
  ],
  CHANNELS: [{ v: 'sms', l: 'SMS' }, { v: 'email', l: 'Email' }, { v: 'whatsapp', l: 'WhatsApp' }],
  BOOKING_STATUSES: [
    { key: 'booked', name: 'Booked' }, { key: 'confirmed', name: 'Confirmed' },
    { key: 'arrived', name: 'Arrived' }, { key: 'done', name: 'Done' },
    { key: 'canceled', name: 'Canceled' }, { key: 'no_show', name: 'No-show' },
  ],

  // working state for the open panel
  _p: null,

  async load() {
    setTopbarActions('');
    setContent(`
      <div class="card" style="margin-bottom:16px;padding:14px 20px">
        <div style="font-size:13px;color:var(--tx-2);font-weight:600;line-height:1.6">
          Notification rules send a message to the client when something happens on an order or booking.
          Because this build has no live SMS/email gateway, each send is logged to the record's activity feed so you can see exactly what would go out.
        </div>
      </div>
      <div id="wf-sections"><div class="card"><div class="skeleton" style="height:220px"></div></div></div>
      <div style="height:22px"></div>
      <div id="notif-templates"></div>
      <div style="height:22px"></div>
      <div id="notif-automations"></div>`);
    await this.reload();
    try { if (window.AutomationsPage) await AutomationsPage.mountInto('notif-automations'); } catch (e) {}
  },

  async reload() {
    try {
      const [rules, tpls] = await Promise.all([
        get('/api/notifications/rules'),
        get('/api/notifications/templates'),
      ]);
      this.rules = rules.items || [];
      this.templates = tpls.items || [];
      this.variables = tpls.variables || [];
    } catch (e) { Toast.error(e.message); }
    // status + location lookups (best-effort; the panel still works without them)
    try {
      const [reg, est] = await Promise.all([
        get('/api/statuses?is_estimate=false'), get('/api/statuses?is_estimate=true'),
      ]);
      this.statuses = [...(reg.items || []), ...(est.items || [])];
    } catch { this.statuses = []; }
    try { this.locations = (await get('/api/staff/locations')).items || []; } catch { this.locations = []; }
    try { this.variableGroups = (await get('/api/notifications/variables')).groups || {}; } catch { this.variableGroups = {}; }
    this._draw();
    this._drawTemplates();
  },

  /* ── Sections (rules + reminders) ─────────────────────────────── */
  _draw() {
    const el = document.getElementById('wf-sections');
    if (!el) return;
    el.innerHTML = this.SECTIONS.map(s => this._sectionHTML(s)).join('<div style="height:18px"></div>');
  },

  _sectionHTML(s) {
    const mine = this.rules.filter(r => r.module === s.module);
    const notifs = mine.filter(r => r.event !== 'reminder');
    const reminders = mine.filter(r => r.event === 'reminder');
    return `<div class="card wf-section" style="padding:18px 20px">
      <div class="wf-section-head">
        <div>
          <div class="wf-section-title">${s.title}</div>
          <div class="wf-section-sub">${s.sub}</div>
        </div>
        <button class="btn btn-primary" onclick="NotificationsPage.newRule('${s.module}',false)">${icon('plus',14)} Notification</button>
      </div>
      ${notifs.length ? `<table class="wf-table"><thead><tr>
          <th>Event</th><th>Send via</th><th>Template</th><th>Location</th><th class="wf-th-right">Actions</th>
        </tr></thead><tbody>
        ${notifs.map(r => this._ruleRow(r, s)).join('')}
        </tbody></table>` : `<div class="wf-empty"><b>No notifications yet</b>Add one to message clients on ${s.title.toLowerCase()} events.</div>`}

      <div class="wf-sub-label">Reminders</div>
      <div style="display:flex;justify-content:flex-end;margin:-30px 0 8px">
        <button class="btn btn-glass btn-sm" onclick="NotificationsPage.newRule('${s.module}',true)">${icon('plus',13)} Reminder</button>
      </div>
      ${reminders.length ? `<table class="wf-table"><thead><tr>
          <th>Before appointment</th><th>Send via</th><th>Template</th><th>Location</th><th class="wf-th-right">Actions</th>
        </tr></thead><tbody>
        ${reminders.map(r => this._ruleRow(r, s, true)).join('')}
        </tbody></table>` : `<div class="wf-empty"><b>Nothing here yet</b>Remind your clients about scheduled jobs.</div>`}
    </div>`;
  },

  _ruleRow(r, s, isReminder = false) {
    const ev = isReminder
      ? `<span class="wf-event-strong">${this._offsetLabel(r.reminder_offset_min)}</span>`
      : this._eventCell(r, s);
    const chColors = { sms: 'wf-dot-sms', email: 'wf-dot-email', whatsapp: 'wf-dot-whatsapp' };
    const chLabel = { sms: 'SMS', email: 'Email', whatsapp: 'WhatsApp' }[r.channel] || r.channel;
    const via = r.chat_first
      ? `<span class="wf-via" title="Tries the client's latest chat first">${icon('message',12)} Chat-first</span>`
      : `<span class="wf-via"><span class="wf-dot ${chColors[r.channel] || ''}"></span>${chLabel}</span>`;
    const tpl = this.templates.find(t => t.id === r.template_id);
    const tplName = tpl ? this._escape(tpl.name || '(untitled)') : '<span style="color:var(--tx-4)">Default message</span>';
    const loc = r.location_id
      ? this._escape((this.locations.find(l => l.id === r.location_id) || {}).name || 'One location')
      : 'All';
    return `<tr class="wf-row">
      <td>${ev}${r.active ? '' : ' <span class="wf-status-chip" style="background:rgba(144,152,180,.16);color:var(--tx-3)">off</span>'}</td>
      <td>${via}</td>
      <td>${tplName}</td>
      <td><span class="wf-loc">${loc}</span></td>
      <td class="wf-cell-actions">
        <button class="forms-field-btn" title="Edit" onclick="NotificationsPage.editRule('${r.id}')">${icon('list',13)}</button>
        <button class="forms-field-btn forms-field-del" title="Delete" onclick="NotificationsPage.deleteRule('${r.id}')">${icon('x',13)}</button>
      </td></tr>`;
  },

  _eventCell(r, s) {
    const ev = (s.events.find(e => e.key === r.event) || {}).label || r.event;
    if (r.event === 'status_changed' && r.trigger_status_key) {
      const st = (s.hasStatus ? this.statuses : this.BOOKING_STATUSES).find(x => x.key === r.trigger_status_key);
      const color = st && st.color ? st.color : '#2855c8';
      const nm = st ? (st.name || st.key) : r.trigger_status_key;
      return `<span class="wf-event-strong">${ev}</span> <span class="wf-status-chip" style="background:${this._tint(color)};color:${color}">${this._escape(nm)}</span>`;
    }
    return `<span class="wf-event-strong">${ev}</span>`;
  },

  _offsetLabel(min) {
    min = min || 0;
    if (min % 1440 === 0 && min >= 1440) return `${min / 1440} day${min / 1440 > 1 ? 's' : ''}`;
    if (min % 60 === 0 && min >= 60) return `${min / 60} hour${min / 60 > 1 ? 's' : ''}`;
    return `${min} min`;
  },

  _tint(hex) {
    // soft translucent background from a hex colour
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m) return 'rgba(79,142,247,.14)';
    const n = parseInt(m[1], 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},.14)`;
  },

  /* ── Right-side panel: New / Edit ─────────────────────────────── */
  newRule(module, isReminder) { this._panel(null, module, isReminder); },
  editRule(id) {
    const r = this.rules.find(x => x.id === id);
    if (!r) return;
    this._panel(r, r.module, r.event === 'reminder');
  },

  _panel(rule, module, isReminder) {
    const section = this.SECTIONS.find(s => s.module === module) || this.SECTIONS[0];
    const isEdit = !!rule;
    this._p = { id: rule?.id || null, module, isReminder,
                event: rule?.event || (isReminder ? 'reminder' : section.events[0].key),
                trigger_status_key: rule?.trigger_status_key || '',
                channel: rule?.channel || 'sms',
                template_id: rule?.template_id || '',
                delay_min: rule?.delay_min || 0,
                working_hours_only: !!rule?.working_hours_only,
                chat_first: !!rule?.chat_first,
                reminder_offset_min: rule?.reminder_offset_min || (isReminder ? 60 : 0),
                location_id: rule?.location_id || '',
                active: rule?.active !== false };

    const title = isReminder ? (isEdit ? 'Edit reminder' : 'New reminder')
                             : (isEdit ? 'Edit notification' : 'New notification');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${title}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="wf-info">${icon('message', 16)}
          <div>Clients are messaged only if they've consented. Manage consent on the contact, or set a default in <a onclick="SettingsPage.go('general')">Settings › General</a>.</div>
        </div>
        <div id="wf-panel-fields">${this._panelFields(section)}</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="NotificationsPage.submitRule()">${isEdit ? 'Save' : 'Create'}</button>
      </div>`, 'modal-side');
  },

  _panelFields(section) {
    const p = this._p;
    const eventField = p.isReminder ? `
      <div class="field">
        <label class="field-label">Send this reminder</label>
        <div class="field-row">
          <input class="field-input" type="number" min="1" id="wf-off-val"
                 value="${this._offsetVal(p.reminder_offset_min).val}" style="max-width:120px">
          <select class="field-select" id="wf-off-unit">
            ${[['min', 'minutes'], ['hour', 'hours'], ['day', 'days']].map(([v, l]) =>
              `<option value="${v}" ${this._offsetVal(p.reminder_offset_min).unit === v ? 'selected' : ''}>${l} before the appointment</option>`).join('')}
          </select>
        </div>
      </div>`
      : `<div class="field">
        <label class="field-label">Event</label>
        <select class="field-select" id="wf-event" onchange="NotificationsPage._onEventChange()">
          ${section.events.map(e => `<option value="${e.key}" ${p.event === e.key ? 'selected' : ''}>${e.label}</option>`).join('')}
        </select>
      </div>
      <div class="field" id="wf-status-wrap" style="${(p.event === 'status_changed') ? '' : 'display:none'}">
        <label class="field-label">When status is</label>
        <select class="field-select" id="wf-status">
          <option value="">Any status</option>
          ${(section.hasStatus ? this.statuses : this.BOOKING_STATUSES).map(st =>
            `<option value="${st.key}" ${p.trigger_status_key === st.key ? 'selected' : ''}>${this._escape(st.name || st.key)}</option>`).join('')}
        </select>
      </div>`;

    const locOpts = this.locations.length ? `
      <div class="field" id="wf-loc-wrap" style="${p.location_id ? '' : 'display:none'}">
        <label class="field-label">Location</label>
        <select class="field-select" id="wf-loc">
          ${this.locations.map(l => `<option value="${l.id}" ${p.location_id === l.id ? 'selected' : ''}>${this._escape(l.name)}</option>`).join('')}
        </select>
      </div>` : '';

    return `
      ${eventField}

      <div class="field">
        <label class="field-label">Send via</label>
        <div class="wf-seg" id="wf-channel">
          ${this.CHANNELS.map(c => `<button type="button" class="wf-seg-btn ${p.channel === c.v ? 'active' : ''}" data-v="${c.v}" onclick="NotificationsPage._pick('channel','${c.v}')">${c.l}</button>`).join('')}
        </div>
      </div>

      <label class="wf-check"><input type="checkbox" id="wf-chatfirst" ${p.chat_first ? 'checked' : ''}>
        <span>Try the client's latest chat first</span></label>
      <label class="wf-check"><input type="checkbox" id="wf-delay-on" ${p.delay_min > 0 ? 'checked' : ''}
        onchange="document.getElementById('wf-delay-row').style.display=this.checked?'flex':'none'">
        <span>Delay sending</span></label>
      <div class="wf-inline-min" id="wf-delay-row" style="display:${p.delay_min > 0 ? 'flex' : 'none'}">
        <input class="field-input" type="number" min="1" id="wf-delay-min" value="${p.delay_min || 15}">
        <span class="wf-hint">minutes after the event</span>
      </div>
      <label class="wf-check"><input type="checkbox" id="wf-wh" ${p.working_hours_only ? 'checked' : ''}>
        <span>Only send during working hours</span></label>

      <div class="field" style="margin-top:12px">
        <label class="field-label">Template</label>
        <div class="wf-tpl-row">
          <select class="field-select" id="wf-template">
            <option value="">Default message</option>
            ${this.templates.map(t => `<option value="${t.id}" ${p.template_id === t.id ? 'selected' : ''}>${this._escape(t.name || '(untitled)')}</option>`).join('')}
          </select>
          <button type="button" class="wf-tpl-add" onclick="NotificationsPage.newTemplate()">${icon('plus', 12)} Template</button>
        </div>
      </div>

      <div class="field">
        <label class="field-label">Location</label>
        <div class="wf-seg" id="wf-locseg">
          <button type="button" class="wf-seg-btn ${p.location_id ? '' : 'active'}" onclick="NotificationsPage._pickLoc('')">All</button>
          <button type="button" class="wf-seg-btn ${p.location_id ? 'active' : ''}" onclick="NotificationsPage._pickLoc('one')">Selected</button>
        </div>
      </div>
      ${locOpts}

      <label class="wf-check" style="margin-top:6px"><input type="checkbox" id="wf-active" ${p.active ? 'checked' : ''}>
        <span>Active</span></label>`;
  },

  _offsetVal(min) {
    min = min || 60;
    if (min % 1440 === 0) return { val: min / 1440, unit: 'day' };
    if (min % 60 === 0) return { val: min / 60, unit: 'hour' };
    return { val: min, unit: 'min' };
  },

  _pick(key, val) {
    this._p[key] = val;
    document.querySelectorAll('#wf-channel .wf-seg-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.v === val));
  },
  _pickLoc(mode) {
    const segBtns = document.querySelectorAll('#wf-locseg .wf-seg-btn');
    segBtns[0].classList.toggle('active', mode === '');
    segBtns[1].classList.toggle('active', mode === 'one');
    const wrap = document.getElementById('wf-loc-wrap');
    if (mode === 'one' && !this.locations.length) { Toast.error('No locations to choose from'); return; }
    if (wrap) wrap.style.display = mode === 'one' ? '' : 'none';
    if (mode === '') this._p.location_id = '';
    else this._p.location_id = (document.getElementById('wf-loc')?.value) || (this.locations[0] && this.locations[0].id) || '';
  },
  _onEventChange() {
    const ev = document.getElementById('wf-event')?.value;
    this._p.event = ev;
    const wrap = document.getElementById('wf-status-wrap');
    if (wrap) wrap.style.display = ev === 'status_changed' ? '' : 'none';
  },

  async submitRule() {
    const p = this._p;
    const payload = {
      module: p.module,
      channel: (document.querySelector('#wf-channel .wf-seg-btn.active')?.dataset.v) || p.channel,
      template_id: document.getElementById('wf-template')?.value || null,
      chat_first: document.getElementById('wf-chatfirst')?.checked || false,
      working_hours_only: document.getElementById('wf-wh')?.checked || false,
      active: document.getElementById('wf-active')?.checked !== false,
      location_id: null,
    };
    // location
    const locActive = document.querySelectorAll('#wf-locseg .wf-seg-btn')[1]?.classList.contains('active');
    if (locActive) payload.location_id = document.getElementById('wf-loc')?.value || null;
    // delay
    const delayOn = document.getElementById('wf-delay-on')?.checked;
    payload.delay_min = delayOn ? Math.max(1, parseInt(document.getElementById('wf-delay-min')?.value, 10) || 0) : 0;

    if (p.isReminder) {
      payload.event = 'reminder';
      const val = Math.max(1, parseInt(document.getElementById('wf-off-val')?.value, 10) || 0);
      const unit = document.getElementById('wf-off-unit')?.value || 'hour';
      payload.reminder_offset_min = val * (unit === 'day' ? 1440 : unit === 'hour' ? 60 : 1);
      if (payload.reminder_offset_min <= 0) return Toast.error('Set how long before the appointment to send');
    } else {
      payload.event = document.getElementById('wf-event')?.value || p.event;
      payload.reminder_offset_min = 0;
      if (payload.event === 'status_changed')
        payload.trigger_status_key = document.getElementById('wf-status')?.value || null;
    }

    try {
      if (p.id) { await patch(`/api/notifications/rules/${p.id}`, payload); Toast.success('Notification updated'); }
      else { await post('/api/notifications/rules', payload); Toast.success('Notification created'); }
      Modal.close(); await this.reload();
    } catch (e) { Toast.error(e.message || 'Could not save'); }
  },

  async deleteRule(id) {
    if (!(await confirm2('Delete this rule?'))) return;
    try { await del(`/api/notifications/rules/${id}`); Toast.success('Deleted'); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  /* ── Templates section (message bodies) ───────────────────────── */
  _drawTemplates() {
    const el = document.getElementById('notif-templates');
    if (!el) return;
    const rows = this.templates.map(t => `
      <div class="notif-row">
        <div class="notif-channel" style="color:var(--blue)">${icon('message', 16)}</div>
        <div class="notif-info">
          <div class="notif-name">${this._escape(t.name || '(untitled)')}
            <span class="notif-tag">${t.channel}</span>
            ${!t.active ? '<span class="notif-tag notif-off">inactive</span>' : ''}</div>
          <div class="notif-body">${this._escape(t.body_template)}</div>
        </div>
        <div class="notif-actions">
          <button class="forms-field-btn" onclick="NotificationsPage.editTemplate('${t.id}')" title="Edit">${icon('list', 13)}</button>
          <button class="forms-field-btn forms-field-del" onclick="NotificationsPage.deleteTemplate('${t.id}')" title="Delete">${icon('x', 13)}</button>
        </div>
      </div>`).join('');
    el.innerHTML = `<div class="card" style="padding:18px 20px">
      <div class="wf-section-head">
        <div><div class="wf-section-title">Templates</div>
          <div class="wf-section-sub">The message bodies your notification rules render. Use {variables} to personalise.</div></div>
        <button class="btn btn-glass" onclick="NotificationsPage.newTemplate()">${icon('plus', 14)} Template</button>
      </div>
      ${this.templates.length ? `<div style="padding:2px">${rows}</div>`
        : `<div class="wf-empty"><b>No templates yet</b>Create one to auto-notify clients on status changes.</div>`}
    </div>`;
  },

  _escape(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; },

  newTemplate() { this._modal(); },
  editTemplate(id) { const t = this.templates.find(x => x.id === id); if (t) this._modal(t); },

  _modal(tpl = null) {
    const isEdit = !!tpl;
    // Grouped, collapsible variable picker (Orderry-style). Falls back to the flat
    // list if the grouped catalog didn't load.
    const groups = this.variableGroups || {};
    const groupNames = Object.keys(groups);
    const varsPicker = groupNames.length
      ? groupNames.map((g, i) => `
          <div class="var-group">
            <button type="button" class="var-group-head" onclick="NotificationsPage._toggleVarGroup(this)">
              <span>${this._escape(g)} <span class="var-group-count">${groups[g].length}</span></span>${icon('plus', 12)}
            </button>
            <div class="var-group-body" hidden>
              ${groups[g].map(v => `<span class="var-chip" onclick="NotificationsPage._insertVar('${v}')">${v}</span>`).join('')}
            </div>
          </div>`).join('')
      : this.variables.map(v =>
          `<span class="var-chip" onclick="NotificationsPage._insertVar('${v}')">${v}</span>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${isEdit ? 'Edit template' : 'New template'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Template name</label><input class="field-input" id="nt-name" value="${tpl?.name || ''}" placeholder="e.g. Ready for pickup"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Channel</label>
            <select class="field-select" id="nt-channel">
              ${['sms', 'email', 'whatsapp'].map(c => `<option value="${c}"${tpl?.channel === c ? ' selected' : ''}>${c.toUpperCase()}</option>`).join('')}
            </select>
          </div>
          <div class="field"><label class="field-label">Trigger</label>
            <select class="field-select" id="nt-event" onchange="NotificationsPage._toggleStatus()">
              <option value="status_change"${tpl?.trigger_event === 'status_change' ? ' selected' : ''}>On status change</option>
              <option value="order_created"${tpl?.trigger_event === 'order_created' ? ' selected' : ''}>On order created</option>
              <option value="comment_added"${tpl?.trigger_event === 'comment_added' ? ' selected' : ''}>On comment added</option>
            </select>
          </div>
        </div>
        <div class="field" id="nt-status-wrap">
          <label class="field-label">When status becomes</label>
          <select class="field-select" id="nt-status">
            <option value="">Any status change</option>
            ${this.statuses.map(s => `<option value="${s.key}"${tpl?.trigger_status === s.key ? ' selected' : ''}>${this._escape(s.name || s.key)}</option>`).join('')}
          </select>
        </div>
        <div class="field" id="nt-subject-wrap" style="${tpl?.channel === 'email' ? '' : 'display:none'}">
          <label class="field-label">Subject (email)</label><input class="field-input" id="nt-subject" value="${tpl?.subject || ''}" placeholder="Subject line">
        </div>
        <div class="field">
          <label class="field-label">Message body</label>
          <textarea class="field-textarea" id="nt-body" rows="4" placeholder="Hi {Client Name}, your order {Order Number}…">${tpl ? this._escape(tpl.body_template) : ''}</textarea>
        </div>
        <div class="field">
          <label class="field-label">Insert variable</label>
          <div class="var-picker">${varsPicker}</div>
        </div>
        <label class="wf-check"><input type="checkbox" id="nt-active" ${tpl?.active !== false ? 'checked' : ''}> <span>Active</span></label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="NotificationsPage.submitTemplate(${tpl ? `'${tpl.id}'` : 'null'})">${isEdit ? 'Save' : 'Create'}</button>
      </div>`, 'modal-side');
    document.getElementById('nt-channel').addEventListener('change', () => {
      const isEmail = document.getElementById('nt-channel').value === 'email';
      document.getElementById('nt-subject-wrap').style.display = isEmail ? '' : 'none';
    });
    this._toggleStatus();
  },

  _toggleStatus() {
    const ev = document.getElementById('nt-event')?.value;
    const wrap = document.getElementById('nt-status-wrap');
    if (wrap) wrap.style.display = ev === 'status_change' ? '' : 'none';
  },

  _toggleVarGroup(btn) {
    const body = btn.nextElementSibling;
    if (body) body.hidden = !body.hidden;
    btn.classList.toggle('open', body && !body.hidden);
  },

  _insertVar(v) {
    const ta = document.getElementById('nt-body');
    if (!ta) return;
    const s = ta.selectionStart || ta.value.length;
    ta.value = ta.value.slice(0, s) + v + ta.value.slice(s);
    ta.focus();
  },

  async submitTemplate(id = null) {
    const body = document.getElementById('nt-body').value.trim();
    if (!body) return Toast.error('Message body is required');
    const payload = {
      name: document.getElementById('nt-name').value || null,
      channel: document.getElementById('nt-channel').value,
      trigger_event: document.getElementById('nt-event').value,
      trigger_status: document.getElementById('nt-status')?.value || null,
      subject: document.getElementById('nt-subject')?.value || null,
      body_template: body,
      active: document.getElementById('nt-active').checked,
    };
    try {
      if (id) { await put(`/api/notifications/templates/${id}`, payload); Toast.success('Template updated'); }
      else { await post('/api/notifications/templates', payload); Toast.success('Template created'); }
      Modal.close(); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async deleteTemplate(id) {
    if (!(await confirm2('Delete this template?'))) return;
    try { await del(`/api/notifications/templates/${id}`); Toast.success('Deleted'); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },
};
