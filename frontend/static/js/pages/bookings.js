/* Bookings — appointments. Two views: a flat List, and a Timeline (Build 144bq)
   where technicians are rows and time runs across the top. Drag a block sideways
   to re-time it, up/down to move it to another tech; both go through the
   reschedule endpoint with the same no-double-booking guard as create. */
const BookingsPage = {
  _clients: [], _services: [], _employees: [], _date: null, _view: 'timeline',
  STATUS_COLORS: { booked: 'var(--blue)', done: 'var(--green)', canceled: 'var(--tx-4)', no_show: 'var(--red)' },
  // timeline window + geometry
  OPEN_H: 8, CLOSE_H: 20, COL_W: 88, SNAP_MIN: 15,

  async load() {
    this._date = this._date || new Date().toISOString().slice(0, 10);
    setTopbarActions(`<button class="btn btn-primary" onclick="BookingsPage.newModal()">+ New booking</button>`);
    setContent(`
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
        <input class="field-input" type="date" id="bk-date" value="${this._date}" style="width:170px"
               onchange="BookingsPage._date=this.value;BookingsPage.reload()">
        <div class="seg" id="bk-viewseg">
          <button class="seg-btn ${this._view === 'timeline' ? 'on' : ''}" onclick="BookingsPage.setView('timeline')">Timeline</button>
          <button class="seg-btn ${this._view === 'list' ? 'on' : ''}" onclick="BookingsPage.setView('list')">List</button>
        </div>
        <span style="font-size:12.5px;color:var(--tx-3)">Drag a block to re-time it; drop it on another row to reassign.</span>
      </div>
      <div id="bk-view"></div>`);
    await this.reload();
  },

  async setView(v) {
    this._view = v;
    document.querySelectorAll('#bk-viewseg .seg-btn').forEach(b =>
      b.classList.toggle('on', b.textContent.trim().toLowerCase() === v));
    await this.reload();
  },

  async reload() {
    const el = document.getElementById('bk-view');
    if (!el) return;
    try {
      const data = await get(`/api/bookings?date_str=${this._date}`);
      this._items = data.items || [];
      if (this._view === 'timeline') {
        if (!this._employees.length) {
          try { this._employees = await get('/api/staff/employees'); } catch (e) { this._employees = []; }
        }
        this.renderTimeline(el);
      } else {
        this.renderList(el);
      }
    } catch (e) { Toast.error(e.message); }
  },

  renderList(el) {
    const hm = d => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th style="width:130px">Time</th><th>Client</th><th>Service</th><th>With</th><th>Status</th><th style="width:190px"></th></tr></thead>
      <tbody>${this._items.map(b => {
        const t1 = new Date(b.scheduled_for), t2 = new Date(b.ends_at);
        return `<tr>
          <td class="tbl-strong">${hm(t1)} - ${hm(t2)}</td>
          <td>${b.client_name || ''}</td>
          <td style="color:var(--tx-3)">${b.service_name || b.note || ''}</td>
          <td>${b.employee_name || ''}</td>
          <td style="color:${this.STATUS_COLORS[b.status]};font-weight:600">${b.status.replace('_', ' ')}</td>
          <td style="text-align:right;white-space:nowrap">${b.status === 'booked' ? `
            <button class="btn btn-glass btn-sm" onclick="BookingsPage.toOrder('${b.id}')">→ Work order</button>
            <button class="btn btn-glass btn-sm" onclick="BookingsPage.setStatus('${b.id}','no_show')">No-show</button>
            <button class="btn btn-glass btn-sm" onclick="BookingsPage.setStatus('${b.id}','canceled')">Cancel</button>`
            : b.order_id ? '<span style="color:var(--tx-4);font-size:12px">work order created</span>' : ''}</td>
        </tr>`;
      }).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:22px">No bookings on this day</td></tr>`}
      </tbody></table></div></div>`;
  },

  renderTimeline(el) {
    const rows = this._employees.length ? this._employees : [{ id: '', name: 'Unassigned' }];
    const hours = [];
    for (let h = this.OPEN_H; h < this.CLOSE_H; h++) hours.push(h);
    const gridW = hours.length * this.COL_W;
    const label = h => `${((h + 11) % 12) + 1}${h < 12 ? 'a' : 'p'}`;
    const head = `<div class="bkt-head" style="width:${gridW}px">
      ${hours.map(h => `<div class="bkt-hcell" style="width:${this.COL_W}px">${label(h)}</div>`).join('')}</div>`;
    const lanes = rows.map(r => {
      const blocks = this._items.filter(b => (b.employee_id || '') === r.id).map(b => this._block(b)).join('');
      return `<div class="bkt-lane" data-emp="${r.id}" style="width:${gridW}px">
        <div class="bkt-lane-bg">${hours.map(() => `<span style="width:${this.COL_W}px"></span>`).join('')}</div>
        ${blocks}</div>`;
    }).join('');
    const names = rows.map(r => `<div class="bkt-rowname">${(r.name || '').replace(/</g, '&lt;')}</div>`).join('');
    el.innerHTML = `<div class="card" style="padding:0;overflow:hidden">
      <div class="bkt-wrap">
        <div class="bkt-names"><div class="bkt-corner"></div>${names}</div>
        <div class="bkt-scroll" id="bkt-scroll">${head}${lanes}</div>
      </div>
      ${this._items.length ? '' : '<div style="text-align:center;color:var(--tx-4);padding:22px">No bookings on this day — hit “+ New booking”.</div>'}
    </div>`;
    this._wireDrag();
  },

  _block(b) {
    const start = new Date(b.scheduled_for), end = new Date(b.ends_at);
    const openMin = this.OPEN_H * 60;
    const sMin = start.getHours() * 60 + start.getMinutes();
    const dur = Math.max(20, (end - start) / 60000);
    const left = ((sMin - openMin) / 60) * this.COL_W;
    const width = (dur / 60) * this.COL_W;
    const hm = d => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const c = this.STATUS_COLORS[b.status];
    return `<div class="bkt-block" data-id="${b.id}" data-status="${b.status}"
      style="left:${left}px;width:${Math.max(46, width)}px;--bc:${c}"
      title="${(b.client_name || '')} · ${hm(start)}">
      <div class="bkt-b-t">${(b.client_name || 'Walk-in').replace(/</g, '&lt;')}</div>
      <div class="bkt-b-s">${hm(start)} · ${(b.service_name || b.note || '').replace(/</g, '&lt;')}</div>
    </div>`;
  },

  _wireDrag() {
    const scroll = document.getElementById('bkt-scroll');
    if (!scroll) return;
    const self = this;
    scroll.querySelectorAll('.bkt-block').forEach(bl => {
      bl.addEventListener('pointerdown', function (e) {
        if (e.button !== 0) return;
        const startX = e.clientX, startY = e.clientY;
        const id = bl.dataset.id;
        let moved = false;
        bl.setPointerCapture(e.pointerId);
        bl.classList.add('drag');
        const origLeft = parseFloat(bl.style.left);
        const lanes = [...scroll.querySelectorAll('.bkt-lane')];

        function move(ev) {
          const dx = ev.clientX - startX, dy = ev.clientY - startY;
          if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
          bl.style.left = (origLeft + dx) + 'px';
          bl.style.transform = `translateY(${dy}px)`;
        }
        async function up(ev) {
          bl.releasePointerCapture(ev.pointerId);
          bl.classList.remove('drag');
          bl.removeEventListener('pointermove', move);
          bl.removeEventListener('pointerup', up);
          bl.style.transform = '';
          if (!moved) { self.blockMenu(id); bl.style.left = origLeft + 'px'; return; }
          // new time from x
          const newLeft = origLeft + (ev.clientX - startX);
          let mins = self.OPEN_H * 60 + Math.round((newLeft / self.COL_W) * 60 / self.SNAP_MIN) * self.SNAP_MIN;
          mins = Math.max(self.OPEN_H * 60, Math.min(self.CLOSE_H * 60 - 15, mins));
          const hh = String(Math.floor(mins / 60)).padStart(2, '0'), mm = String(mins % 60).padStart(2, '0');
          // new tech from which lane the pointer is over
          let emp = null;
          for (const ln of lanes) {
            const r = ln.getBoundingClientRect();
            if (ev.clientY >= r.top && ev.clientY <= r.bottom) { emp = ln.dataset.emp; break; }
          }
          const body = { scheduled_for: `${self._date}T${hh}:${mm}:00` };
          if (emp !== null && emp !== undefined) body.employee_id = emp;
          try {
            await patch(`/api/bookings/${id}`, body);
            Toast.success('Rescheduled');
          } catch (err) { Toast.error(err.message || 'Could not reschedule'); }
          self.reload();
        }
        bl.addEventListener('pointermove', move);
        bl.addEventListener('pointerup', up);
        e.preventDefault();
      });
    });
  },

  // Build 144bt: full booking drawer — reschedule inline, convert to WO, see status.
  blockMenu(id) {
    const b = (this._items || []).find(x => x.id === id);
    if (!b) return;
    const t1 = new Date(b.scheduled_for), t2 = new Date(b.ends_at);
    const hm = d => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const dur = Math.round((t2 - t1) / 60000) || 60;
    const dateVal = b.scheduled_for.slice(0, 10);
    const timeVal = `${String(t1.getHours()).padStart(2, '0')}:${String(t1.getMinutes()).padStart(2, '0')}`;
    const emps = this._employees || [];
    const empOpts = emps.map(e => `<option value="${e.id}" ${e.id === b.employee_id ? 'selected' : ''}>${(e.name || '').replace(/</g, '&lt;')}</option>`).join('');
    const locked = b.status !== 'booked';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${(b.client_name || 'Walk-in').replace(/</g, '&lt;')}
        <span style="font-size:11.5px;font-weight:700;color:${this.STATUS_COLORS[b.status]};margin-left:8px">${b.status.replace('_', ' ')}</span></span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:13px;color:var(--tx-3);margin-bottom:12px">${hm(t1)} – ${hm(t2)} · ${(b.service_name || b.note || 'General appointment')}</div>
        ${locked ? `<div style="font-size:12px;color:var(--tx-4);margin-bottom:12px">This booking is ${b.status.replace('_', ' ')} — reschedule is disabled.</div>` : `
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Reschedule</div>
        <div class="field-row">
          <div class="field"><label class="field-label">Date</label><input class="field-input" type="date" id="bd-date" value="${dateVal}"></div>
          <div class="field"><label class="field-label">Time</label><input class="field-input" type="time" id="bd-time" value="${timeVal}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Technician</label><select class="field-select" id="bd-emp">${empOpts}</select></div>
          <div class="field"><label class="field-label">Duration (min)</label><input class="field-input" type="number" id="bd-dur" value="${dur}"></div>
        </div>`}
        <div style="font-size:11.5px;color:var(--tx-4);margin-top:8px">${b.order_id ? 'A work order was already created from this booking.' : 'Converting creates a work order and marks this booking done.'}</div>
        <div style="font-size:11px;color:var(--tx-4);margin-top:6px">The client is notified automatically if a booking rule is set (Settings → Notifications).</div>
      </div>
      <div class="modal-footer" style="flex-wrap:wrap;gap:8px">
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
        ${b.status === 'booked' ? `
          <button class="btn btn-glass" onclick="BookingsPage.setStatus('${id}','no_show');Modal.close()">No-show</button>
          <button class="btn btn-glass" onclick="BookingsPage.setStatus('${id}','canceled');Modal.close()">Cancel</button>
          ${b.order_id ? '' : `<button class="btn btn-glass" onclick="BookingsPage.toOrder('${id}');Modal.close()">→ Work order</button>`}
          <button class="btn btn-primary" onclick="BookingsPage.saveReschedule('${id}')">Save changes</button>` : ''}
      </div>`, 'modal-side-wide');
  },

  async saveReschedule(id) {
    const d = document.getElementById('bd-date')?.value;
    const t = document.getElementById('bd-time')?.value;
    const emp = document.getElementById('bd-emp')?.value;
    const dur = parseInt(document.getElementById('bd-dur')?.value, 10) || null;
    if (!d || !t) return Toast.error('Pick a date and time');
    try {
      await patch(`/api/bookings/${id}`, { scheduled_for: `${d}T${t}:00`, employee_id: emp || null, duration_min: dur });
      Toast.success('Rescheduled'); Modal.close(); await this.reload();
    } catch (e) { Toast.error(e.message || 'Could not reschedule'); }
  },

  async setStatus(id, status) {
    try { await patch(`/api/bookings/${id}`, { status }); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  // Build 85: an appointment becomes the job. Client, time and note all carry over,
  // and the booking is marked done so it can't be converted twice.
  async toOrder(id) {
    try {
      const r = await post(`/api/bookings/${id}/to-order`, {});
      Toast.success(`Work order ${r.order_number} created`);
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Could not create the work order'); }
  },

  // Build 85: free slots come from the employee's work schedule, and existing
  // bookings *and* scheduled work orders both count as busy.
  async loadSlots() {
    const box = document.getElementById('bk-slots');
    if (!box) return;
    const day = document.getElementById('bk-d').value;
    const emp = document.getElementById('bk-emp').value;
    if (!day) { box.innerHTML = ''; return; }
    const dur = parseInt(document.getElementById('bk-dur')?.value, 10) || 60;
    box.innerHTML = '<span style="color:var(--tx-4);font-size:12px">Checking availability…</span>';
    try {
      const qs = `date_str=${day}&duration_min=${dur}${emp ? '&employee_id=' + emp : ''}`;
      const r = await get(`/api/bookings/availability?${qs}`);
      if (!r.slots.length) {
        box.innerHTML = `<span style="color:var(--red,#e5484d);font-size:12px">
          ${r.reason === 'employee off this day' ? 'This person is off on this day.' : 'No free slots that day.'}</span>`;
        return;
      }
      box.innerHTML = r.slots.map(s => `
        <button type="button" class="btn btn-glass btn-sm" style="margin:0 4px 4px 0"
          onclick="document.getElementById('bk-t').value='${s.start}'">${s.start}–${s.end}</button>`).join('');
    } catch (e) { box.innerHTML = ''; }
  },

  async newModal() {
    try {
      [this._clients, this._services, this._employees] = await Promise.all([
        get('/api/clients?limit=50').then(r => r.items),
        get('/api/catalog/search?type=service').then(r => r.items).catch(() => []),
        get('/api/staff/employees')]);
    } catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New booking</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Client</label>
            <select class="field-select" id="bk-client"><option value="">Walk-in / new</option>
              ${this._clients.map(c => `<option value="${c.id}">${c.name}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Or name</label>
            <input class="field-input" id="bk-cname" placeholder="If not in CRM"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Service</label>
            <select class="field-select" id="bk-svc"><option value="">— general appointment —</option>
              ${this._services.map(s => `<option value="${s.ref_id}">${s.name}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">With employee</label>
            <select class="field-select" id="bk-emp" onchange="BookingsPage.loadSlots()">${this._employees.map(e => `<option value="${e.id}">${e.name}</option>`).join('')}</select></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Date</label>
            <input class="field-input" id="bk-d" type="date" value="${this._date}"
                   onchange="BookingsPage.loadSlots()"></div>
          <div class="field"><label class="field-label">Time</label>
            <input class="field-input" id="bk-t" type="time" value="10:00"></div>
        </div>
        <div class="field"><label class="field-label">Duration (minutes — blank uses the service's)</label>
          <input class="field-input" id="bk-dur" type="number" placeholder="60"
                 onchange="BookingsPage.loadSlots()"></div>
        <div class="field">
          <label class="field-label">Free slots</label>
          <div id="bk-slots" style="min-height:26px"></div>
        </div>
        <div class="field"><label class="field-label">Note</label>
          <input class="field-input" id="bk-note" placeholder="What's the visit for?"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="BookingsPage.submit()">Book it</button>
      </div>`);
    this.loadSlots();   // show availability as soon as the form opens
  },

  async submit() {
    try {
      await post('/api/bookings', {
        client_id: document.getElementById('bk-client').value || null,
        client_name: document.getElementById('bk-cname').value || null,
        service_id: document.getElementById('bk-svc').value || null,
        employee_id: document.getElementById('bk-emp').value,
        scheduled_for: `${document.getElementById('bk-d').value}T${document.getElementById('bk-t').value}:00`,
        duration_min: parseInt(document.getElementById('bk-dur').value) || null,
        note: document.getElementById('bk-note').value || null,
      });
      Modal.close(); Toast.success('Booked');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('bookings', () => BookingsPage.load());
