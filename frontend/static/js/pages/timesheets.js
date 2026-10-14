/* ════════════════════════════════════════════════════════════
   Time clock — a global timer widget (start/stop/switch from
   anywhere) + a Timesheets page (review, approve, manual entry).
════════════════════════════════════════════════════════════ */

function _fmtDur(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${s}s` : `${s}s`;
}
function _clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const p = n => String(n).padStart(2, '0');
  return `${p(h)}:${p(m)}:${p(s)}`;
}

/* ── Global timer widget ──────────────────────────────────── */
const TimeClock = {
  _active: null, _tick: null, _baseSec: 0, _baseAt: 0, _orders: [],
  _shift: null, _shiftTick: null, _shiftBaseAt: 0,

  _open: false,

  async mount() {
    if (document.getElementById('tc-widget')) return;
    // Build 144dm — the clock now lives as a compact chip in the topbar (was a
    // fixed bottom-right pod that covered page action buttons). It mounts into
    // the persistent #tc-slot and expands to a popover with both clocks.
    const slot = document.getElementById('tc-slot') || document.body;
    const el = document.createElement('div');
    el.id = 'tc-widget';
    el.className = 'tc-widget';
    slot.appendChild(el);
    await this.refresh();
    // close the popover on outside click
    if (!this._outside) {
      this._outside = (ev) => {
        if (!this._open) return;
        if (ev.target.closest('#tc-widget')) return;
        this._open = false; this._render();
      };
      document.addEventListener('mousedown', this._outside, true);
    }
    // re-sync every 60s in case another device changed state
    setInterval(() => this.refresh(), 60000);
  },

  toggle() { this._open = !this._open; this._render(); },

  // Build 144j/144x: which clocks this employee sees. Effective = org master
  // switch ON (Settings → General) *and* the per-employee flag (Settings →
  // Employees). Default on if we don't know yet. Both off → the widget hides.
  _showJob() { return typeof App === 'undefined' || !App.me || (App.me.track_job_hours !== false && App.me.clock_wo_enabled !== false); },
  _showAttendance() { return typeof App === 'undefined' || !App.me || (App.me.track_attendance !== false && App.me.clock_attendance_enabled !== false); },

  async refresh() {
    if (this._showJob()) {
      try { this._active = (await get('/api/timeclock/active')).active; } catch (e) { this._active = null; }
    } else { this._active = null; }
    if (this._showAttendance()) {
      try { this._shift = (await get('/api/timeclock/shift/active')).shift; } catch (e) { this._shift = null; }
    } else { this._shift = null; }
    this._render();
  },

  _render() {
    const el = document.getElementById('tc-widget');
    if (!el) return;
    if (this._tick) { clearInterval(this._tick); this._tick = null; }
    if (this._shiftTick) { clearInterval(this._shiftTick); this._shiftTick = null; }
    const showJob = this._showJob(), showAtt = this._showAttendance();
    if (!showJob && !showAtt) { el.style.display = 'none'; return; }
    el.style.display = '';

    // ── collapsed chip: a dot (green when a timer runs) + the live time ──
    const running = showJob && this._active;
    if (running) { this._baseSec = this._active.duration_sec || 0; this._baseAt = Date.now(); }
    const chipTime = running ? _clock(this._baseSec)
      : (showAtt && this._shift ? _clock(this._shift.elapsed_sec || 0) : '00:00:00');
    const chipDot = (running || (showAtt && this._shift)) ? 'tc-dot-on' : 'tc-dot-off';
    const chip = `
      <button class="tc-chip${this._open ? ' open' : ''}" onclick="TimeClock.toggle()" title="Time clock" aria-label="Time clock">
        <span class="tc-dot ${chipDot}"></span>
        <span class="tc-chip-time" id="tc-chip-time">${chipTime}</span>
        <span class="tc-chip-caret" data-icon="chevron" data-size="13"></span>
      </button>`;

    // ── expanded popover: both clocks with full buttons ──
    let panel = '';
    if (this._open) {
      let jobBlock = '';
      if (showJob) {
        jobBlock = this._active ? `
          <div class="tc-p-row">
            <div class="tc-p-body">
              <div class="tc-p-lbl">Job timer <span class="tc-p-run">running</span></div>
              <div class="tc-p-job" title="${this._active.order_number ? `WO ${this._active.order_number}` : (this._active.category || 'Time')}">${this._active.order_number ? `WO ${this._active.order_number}` : (this._active.category || 'Time')}</div>
            </div>
            <div class="tc-p-time" id="tc-time">${_clock(this._baseSec)}</div>
          </div>
          <div class="tc-p-btns">
            <button class="tc-btn tc-stop" onclick="TimeClock.stop()">&#9632; Stop</button>
            <button class="tc-btn tc-switch" onclick="TimeClock.pickOrder(true)">&#8646; Switch</button>
          </div>` : `
          <div class="tc-p-row">
            <div class="tc-p-body"><div class="tc-p-lbl">Job timer</div><div class="tc-p-idle">No timer running</div></div>
            <button class="tc-btn tc-start" onclick="TimeClock.pickOrder(false)">&#9658; Start</button>
          </div>`;
      }
      const attBlock = showAtt ? `<div class="tc-p-att">${this._shiftHtml()}</div>` : '';
      panel = `<div class="tc-panel">${jobBlock}${attBlock}</div>`;
    }

    el.innerHTML = chip + panel;
    if (typeof hydrateIcons === 'function') { try { hydrateIcons(el); } catch (e) {} }

    // live ticking — chip always, panel time when open
    if (running) {
      this._tick = setInterval(() => {
        const v = _clock(this._baseSec + (Date.now() - this._baseAt) / 1000);
        const c = document.getElementById('tc-chip-time'); if (c) c.textContent = v;
        const t = document.getElementById('tc-time'); if (t) t.textContent = v;
      }, 1000);
    }
    if (showAtt && this._shift) {
      this._shiftBaseAt = Date.now();
      const base = this._shift.elapsed_sec || 0;
      this._shiftTick = setInterval(() => {
        const t = document.getElementById('tc-shift-time');
        if (t) t.textContent = _clock(base + (Date.now() - this._shiftBaseAt) / 1000);
        if (!running) { const c = document.getElementById('tc-chip-time'); if (c) c.textContent = _clock(base + (Date.now() - this._shiftBaseAt) / 1000); }
      }, 1000);
    }
  },

  // Workplace attendance shift — clock in/out of the workplace (b144).
  _shiftHtml() {
    if (this._shift) {
      return `
        <div class="tc-row tc-shift-row tc-shift-on">
          <div class="tc-body">
            <div class="tc-job tc-shift-lbl">On shift · at work</div>
            <div class="tc-time tc-shift-time" id="tc-shift-time">${_clock(this._shift.elapsed_sec || 0)}</div>
          </div>
          <button class="tc-btn tc-clockout" title="Clock out of workplace" onclick="TimeClock.clockOut()">Clock out</button>
        </div>`;
    }
    return `
      <div class="tc-row tc-shift-row">
        <div class="tc-body"><div class="tc-job tc-idle tc-shift-lbl">Not clocked in</div></div>
        <button class="tc-btn tc-clockin" title="Clock in to workplace" onclick="TimeClock.clockIn()">Clock in</button>
      </div>`;
  },

  async clockIn() {
    try {
      await post('/api/timeclock/shift/clock-in', {});
      Toast.success('Clocked in — welcome');
      await this.refresh();
    } catch (e) { Toast.error(e.message); }
  },

  async clockOut() {
    try {
      const r = await post('/api/timeclock/shift/clock-out', {});
      Toast.success(`Clocked out — ${(r.hours_worked || 0).toFixed(2)}h logged`);
      await this.refresh();
      if (typeof TimesheetsPage !== 'undefined' && Router.current === 'timesheets') TimesheetsPage.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async pickOrder(isSwitch) {
    if (!this._orders.length) {
      try { this._orders = (await get('/api/orders?limit=50')).items || []; } catch (e) { this._orders = []; }
    }
    const opts = this._orders.map(o => `<option value="${o.id}">WO ${o.order_number} — ${(o.complaint || '').slice(0, 40)}</option>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${isSwitch ? 'Switch to job' : 'Start timer'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Work order</label>
          <select class="field-select" id="tc-order">${opts || '<option value="">No open work orders</option>'}</select></div>
        ${!isSwitch ? `<label class="cash-check" style="margin-top:8px"><input type="checkbox" id="tc-nonjob"> Non-job time (break / admin) instead</label>` : ''}
        <div class="field" style="margin-top:8px"><label class="field-label">Note (optional)</label>
          <input class="field-input" id="tc-note" placeholder="What are you working on?"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="TimeClock.begin(${isSwitch})">${isSwitch ? 'Switch' : 'Start'}</button>
      </div>`);
  },

  async begin(isSwitch) {
    const orderId = document.getElementById('tc-order').value || null;
    const note = document.getElementById('tc-note').value || null;
    const nonjob = document.getElementById('tc-nonjob') && document.getElementById('tc-nonjob').checked;
    try {
      if (isSwitch) {
        if (!orderId) return Toast.error('Pick a work order');
        await post('/api/timeclock/switch', { order_id: orderId, note });
      } else if (nonjob) {
        await post('/api/timeclock/start?switch=true', { category: 'admin', note });
      } else {
        if (!orderId) return Toast.error('Pick a work order');
        await post('/api/timeclock/start?switch=true', { order_id: orderId, note });
      }
      Modal.close();
      Toast.success('Timer started');
      await this.refresh();
    } catch (e) { Toast.error(e.message); }
  },

  async stop() {
    try {
      await post('/api/timeclock/stop', {});
      Toast.success('Timer stopped');
      await this.refresh();
      if (typeof TimesheetsPage !== 'undefined' && Router.current === 'timesheets') TimesheetsPage.reload();
    } catch (e) { Toast.error(e.message); }
  },

  // Start a timer on a specific order (called from the work-order page).
  async startOn(orderId) {
    try {
      await post('/api/timeclock/start?switch=true', { order_id: orderId });
      Toast.success('Timer started on this order');
      await this.refresh();
    } catch (e) { Toast.error(e.message); }
  },
};

/* ── Timesheets page ──────────────────────────────────────── */
const TimesheetsPage = {
  _emps: [], _empId: null, _from: null, _to: null,

  async load() {
    setTopbarActions(`<button class="btn btn-glass btn-sm" onclick="TimesheetsPage.manualModal()">+ Manual entry</button>`);
    try { this._emps = await get('/api/staff/employees'); } catch (e) { this._emps = []; }
    const me = await get('/api/auth/me').catch(() => null);
    this._empId = this._empId || (me && me.id) || (this._emps[0] && this._emps[0].id);
    const today = new Date();
    const monday = new Date(today); monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
    this._from = this._from || monday.toISOString().slice(0, 10);
    this._to = this._to || today.toISOString().slice(0, 10);
    setContent(`
      ${(typeof App !== 'undefined' && App.can && App.can('can_manage_payroll')) ? '<div id="ts-roster"></div>' : ''}
      <div class="ts-filters">
        <div class="field"><label class="field-label">Employee</label>
          <select class="field-select" id="ts-emp" onchange="TimesheetsPage.setEmp(this.value)">
            ${this._emps.map(e => `<option value="${e.id}" ${e.id === this._empId ? 'selected' : ''}>${e.name}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">From</label>
          <input class="field-input" type="date" id="ts-from" value="${this._from}" onchange="TimesheetsPage.setFrom(this.value)"></div>
        <div class="field"><label class="field-label">To</label>
          <input class="field-input" type="date" id="ts-to" value="${this._to}" onchange="TimesheetsPage.setTo(this.value)"></div>
        <button class="btn btn-primary btn-sm" style="align-self:flex-end" onclick="TimesheetsPage.approve()">Approve timesheet</button>
      </div>
      <div id="ts-summary"></div>
      <div id="ts-body"></div>`);
    await this.reload();
    this.loadRoster();
  },

  // Build 144dn — manager-only live roster: who is on the clock right now + where.
  _rosterTick: null,
  async loadRoster() {
    const host = document.getElementById('ts-roster');
    if (!host) return;
    let r;
    try { r = await get('/api/timeclock/roster'); } catch (e) { host.innerHTML = ''; return; }
    if (this._rosterTick) { clearInterval(this._rosterTick); this._rosterTick = null; }
    const shifts = r.on_shift || [], timers = r.timers || [];
    const esc = (s) => (s || '').replace(/</g, '&lt;');
    const shiftRows = shifts.length ? shifts.map(s => `
      <div class="ts-ros-row">
        <span class="ts-ros-dot"></span>
        <span class="ts-ros-name">${esc(s.employee_name)}</span>
        <span class="ts-ros-loc">${esc(s.location_name)}</span>
        <span class="ts-ros-time" data-since="${s.started_at}">${_clock(s.elapsed_sec || 0)}</span>
      </div>`).join('') : '<div class="ts-ros-empty">No one is clocked in right now.</div>';
    const timerRows = timers.length ? `<div class="ts-ros-sub">Running job timers</div>` + timers.map(t => `
      <div class="ts-ros-row">
        <span class="ts-ros-dot ts-ros-dot-job"></span>
        <span class="ts-ros-name">${esc(t.employee_name)}</span>
        <span class="ts-ros-loc">${t.order_number ? 'WO ' + esc(t.order_number) : esc(t.category || 'time')}</span>
        <span class="ts-ros-time" data-since="${t.started_at}">${_clock(t.elapsed_sec || 0)}</span>
      </div>`).join('') : '';
    host.innerHTML = `
      <div class="ts-roster card-sm">
        <div class="ts-ros-head">
          <span class="ts-ros-title">On the clock now</span>
          <span class="ts-ros-count">${shifts.length} at work · ${timers.length} timing</span>
        </div>
        ${shiftRows}${timerRows}
      </div>`;
    // live tick from each row's start time
    this._rosterTick = setInterval(() => {
      host.querySelectorAll('.ts-ros-time[data-since]').forEach(el => {
        const t0 = Date.parse(el.getAttribute('data-since'));
        if (!isNaN(t0)) el.textContent = _clock((Date.now() - t0) / 1000);
      });
    }, 1000);
  },

  setEmp(v) { this._empId = v; this.reload(); },
  setFrom(v) { this._from = v; this.reload(); },
  setTo(v) { this._to = v; this.reload(); },

  async reload() {
    const body = document.getElementById('ts-body');
    if (!body) return;
    body.innerHTML = '<div class="card" style="text-align:center;color:var(--tx-4);padding:20px">Loading…</div>';
    let ts;
    try { ts = await get(`/api/timeclock/timesheet?employee_id=${this._empId}&date_from=${this._from}&date_to=${this._to}`); }
    catch (e) {
      const sum = document.getElementById('ts-summary'); if (sum) sum.innerHTML = '';
      body.innerHTML = `<div class="card" style="text-align:center;color:var(--tx-3);padding:22px">Couldn't load timesheet — ${e.message}</div>`;
      return Toast.error(e.message);
    }

    document.getElementById('ts-summary').innerHTML = `
      <div class="ts-cards">
        ${this._stat('Total', _fmtDur(ts.total_sec))}
        ${this._stat('Billable', _fmtDur(ts.billable_sec))}
        ${this._stat('Job vs non-job', `${_fmtDur(ts.job_sec)} / ${_fmtDur(ts.nonjob_sec)}`)}
        ${this._stat('Overtime', `${ts.overtime_hours}h`)}
        ${this._stat('Utilization', `${Math.round((ts.utilization || 0) * 100)}%`)}
        ${this._stat('Labor cost', fmt.currency(ts.labor_cost))}
        ${this._stat('Billable value', fmt.currency(ts.labor_billable))}
        ${this._stat('Approved', `${ts.approved_count}/${ts.entry_count}`)}
      </div>`;

    body.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Date</th><th>Job</th><th>Span</th><th style="text-align:right">Duration</th><th>Billable</th><th>State</th><th style="width:140px"></th></tr></thead>
      <tbody>${ts.entries.map(e => {
        const d = new Date(e.started_at);
        const span = e.ended_at ? `${d.toTimeString().slice(0, 5)}–${new Date(e.ended_at).toTimeString().slice(0, 5)}` : `${d.toTimeString().slice(0, 5)}–…`;
        const stCol = e.state === 'approved' ? 'var(--green)' : e.state === 'running' ? 'var(--amber)' : 'var(--tx-3)';
        return `<tr>
          <td>${d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}</td>
          <td>${e.order_number ? 'WO ' + e.order_number : '<span style="color:var(--tx-4)">' + (e.category || 'non-job') + '</span>'}${e.needs_review ? ' <span style="color:var(--amber);font-size:11px">needs review</span>' : ''}</td>
          <td style="color:var(--tx-3)">${span}</td>
          <td style="text-align:right;font-weight:600">${_fmtDur(e.duration_sec)}</td>
          <td>${e.billable ? 'Yes' : 'No'}</td>
          <td style="color:${stCol};font-weight:600">${e.state}</td>
          <td style="text-align:right">
            ${e.state === 'stopped' ? `<button class="btn btn-glass btn-sm" onclick="TimesheetsPage.editModal('${e.id}','${e.started_at}','${e.ended_at || ''}',${e.billable})">Edit</button>` : ''}
            ${e.state === 'approved' ? `<button class="btn btn-glass btn-sm" onclick="TimesheetsPage.reopen('${e.id}')">Reopen</button>` : ''}
            ${e.state !== 'approved' && e.state !== 'running' ? `<button class="btn btn-glass btn-sm" onclick="TimesheetsPage.void('${e.id}')">Void</button>` : ''}
          </td></tr>`;
      }).join('') || `<tr><td colspan="7" style="text-align:center;color:var(--tx-4);padding:22px">No time entries in this range</td></tr>`}
      </tbody></table></div></div>`;
  },

  _stat(label, val) {
    return `<div class="ts-card"><div class="ts-card-label">${label}</div><div class="ts-card-val">${val}</div></div>`;
  },

  async approve() {
    if (!(await confirm2('Approve and lock all stopped entries in this range, and post hourly wages to payroll?'))) return;
    try {
      const r = await post('/api/timeclock/timesheet/approve', { employee_id: this._empId, date_from: this._from, date_to: this._to });
      Toast.success(`Approved ${r.approved} entries · wages ${fmt.currency(r.wage_posted)} (${r.overtime_hours}h OT)`);
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async manualModal() {
    let orders = [];
    try { orders = (await get('/api/orders?limit=50')).items || []; } catch (e) {}
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Manual time entry</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Work order (optional)</label>
          <select class="field-select" id="me-order"><option value="">— non-job time —</option>
            ${orders.map(o => `<option value="${o.id}">WO ${o.order_number}</option>`).join('')}</select></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Start</label><input class="field-input" type="datetime-local" id="me-start"></div>
          <div class="field"><label class="field-label">Minutes</label><input class="field-input" type="number" id="me-min" value="60" min="1"></div>
        </div>
        <label class="cash-check"><input type="checkbox" id="me-bill" checked> Billable</label>
        <div class="field" style="margin-top:8px"><label class="field-label">Note</label><input class="field-input" id="me-note"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="TimesheetsPage.submitManual()">Add entry</button>
      </div>`);
  },

  async submitManual() {
    const start = document.getElementById('me-start').value;
    if (!start) return Toast.error('Pick a start time');
    const body = {
      employee_id: this._empId,
      order_id: document.getElementById('me-order').value || null,
      started_at: new Date(start).toISOString(),
      duration_min: parseFloat(document.getElementById('me-min').value) || 60,
      billable: document.getElementById('me-bill').checked,
      note: document.getElementById('me-note').value || null,
    };
    try {
      await post('/api/timeclock/entries', body);
      Modal.close(); Toast.success('Entry added'); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  editModal(id, startIso, endIso, billable) {
    const toLocal = iso => iso ? new Date(iso).toISOString().slice(0, 16) : '';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Edit time entry</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Start</label><input class="field-input" type="datetime-local" id="ed-start" value="${toLocal(startIso)}"></div>
          <div class="field"><label class="field-label">End</label><input class="field-input" type="datetime-local" id="ed-end" value="${toLocal(endIso)}"></div>
        </div>
        <label class="cash-check"><input type="checkbox" id="ed-bill" ${billable ? 'checked' : ''}> Billable</label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="TimesheetsPage.submitEdit('${id}')">Save</button>
      </div>`);
  },

  async submitEdit(id) {
    const body = { billable: document.getElementById('ed-bill').checked };
    const s = document.getElementById('ed-start').value, e = document.getElementById('ed-end').value;
    if (s) body.started_at = new Date(s).toISOString();
    if (e) body.ended_at = new Date(e).toISOString();
    try {
      await patch(`/api/timeclock/entries/${id}`, body);
      Modal.close(); Toast.success('Saved'); await this.reload();
    } catch (err) { Toast.error(err.message); }
  },

  async void(id) {
    if (!(await confirm2('Void this time entry?'))) return;
    try { await del(`/api/timeclock/entries/${id}`); Toast.success('Voided'); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  async reopen(id) {
    try { await post(`/api/timeclock/entries/${id}/reopen`, {}); Toast.success('Reopened'); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },
};

Router.register('timesheets', () => TimesheetsPage.load());
