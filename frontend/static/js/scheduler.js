/* ════════════════════════════════════════════════════
   Scheduler — Day / Week / Month views.
   Day: technician rows × hour columns, drag + resize.
   Week: 7 day-columns with stacked orders.
   Month: calendar grid with order pills.
════════════════════════════════════════════════════ */

const Scheduler = {
  container: null,
  date: null,            // 'YYYY-MM-DD' anchor
  mode: 'day',           // day | week | month | timeline
  groupBy: 'employee',   // employee | resource (Orderry: By employee / By resource)
  startHour: 8,
  endHour: 19,
  employees: [],
  resources: [],

  async render(container) {
    this.container = container;
    if (!this.date) this.date = this._today();
    container.innerHTML = `<div class="card" style="padding:14px"><div class="skeleton" style="height:320px"></div></div>`;
    try { this.employees = await get('/api/staff/employees'); } catch { this.employees = []; }
    try { this.resources = (await get('/api/resources')).items; } catch { this.resources = []; }
    await this.load();
  },

  setMode(m) { this.mode = m; this.load(); },
  setGroupBy(g) { this.groupBy = g; this.load(); },

  _today() {
    const d = new Date();
    return this._fmt(d);
  },
  _fmt(d) { return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; },
  _parse(s) { const [y,m,d] = s.split('-').map(Number); return new Date(y, m-1, d); },

  _shift(delta) {
    const dt = this._parse(this.date);
    if (this.mode === 'day') dt.setDate(dt.getDate() + delta);
    else if (this.mode === 'week') dt.setDate(dt.getDate() + delta * 7);
    else dt.setMonth(dt.getMonth() + delta);
    this.date = this._fmt(dt);
    this.load();
  },
  _shiftDay(delta) { this._shift(delta); },  // kept for compatibility

  _weekStart(d) {
    // Monday-start week
    const dt = new Date(d);
    const wd = (dt.getDay() + 6) % 7;
    dt.setDate(dt.getDate() - wd);
    return dt;
  },

  _prettyDate() {
    const dt = this._parse(this.date);
    if (this.mode === 'day') return dt.toLocaleDateString('en-IN', { weekday:'long', day:'numeric', month:'long' });
    if (this.mode === 'week') {
      const s = this._weekStart(dt), e = new Date(s); e.setDate(e.getDate()+6);
      return `${s.toLocaleDateString('en-IN',{day:'numeric',month:'short'})} – ${e.toLocaleDateString('en-IN',{day:'numeric',month:'short'})}`;
    }
    return dt.toLocaleDateString('en-IN', { month:'long', year:'numeric' });
  },

  _techFilter: '',

  _toolbar(count) {
    const isToday = this.date === this._today();
    // technician filter — Orderry shows one specialist at a time in week/month
    const techFilter = (this.mode === 'week' || this.mode === 'month') ? `
        <select class="field-select sch-tech-filter" onchange="Scheduler.setTechFilter(this.value)">
          <option value="">All technicians</option>
          ${this.employees.map(e=>`<option value="${e.id}"${this._techFilter===e.id?' selected':''}>${e.name}</option>`).join('')}
        </select>` : '';
    return `
      <div class="sch-toolbar">
        <div class="sch-nav">
          <button class="btn btn-glass btn-icon" onclick="Scheduler._shift(-1)">${icon('chevronLeft',16)}</button>
          <button class="btn btn-glass btn-sm" onclick="Scheduler.date=Scheduler._today();Scheduler.load()">Today</button>
          <button class="btn btn-glass btn-icon" onclick="Scheduler._shift(1)">${icon('chevronRight',16)}</button>
        </div>
        <div class="sch-date">${this._prettyDate()}${isToday&&this.mode==='day'?' <span class="sch-today-tag">Today</span>':''}</div>
        ${techFilter}
        <div class="sch-mode-toggle">
          ${['day','week','month','timeline'].map(m=>`<button class="sch-mode-btn ${this.mode===m?'active':''}" onclick="Scheduler.setMode('${m}')">${m[0].toUpperCase()+m.slice(1)}</button>`).join('')}
        </div>
        ${(this.mode === 'day' || this.mode === 'timeline') && this.resources.length ? `
        <div class="sch-mode-toggle">
          <button class="sch-mode-btn ${this.groupBy==='employee'?'active':''}" onclick="Scheduler.setGroupBy('employee')">By employee</button>
          <button class="sch-mode-btn ${this.groupBy==='resource'?'active':''}" onclick="Scheduler.setGroupBy('resource')">By resource</button>
        </div>` : ''}
        <div style="font-size:12px;color:var(--tx-3);font-weight:600;min-width:80px;text-align:right">${count} scheduled</div>
      </div>`;
  },

  setTechFilter(v) { this._techFilter = v; this.load(); },

  _applyTechFilter(items) {
    if (!this._techFilter) return items;
    return items.filter(it => it.assigned_to_id === this._techFilter);
  },

  async load() {
    if (this.mode === 'timeline') return this._loadTimeline();
    if (this.mode === 'day') return this._loadDay();
    if (this.mode === 'week') return this._loadWeek();
    return this._loadMonth();
  },

  async _loadDay() {
    let items = [];
    try {
      const data = await get(`/api/orders/scheduler/day?date_str=${this.date}`);
      items = data.items;
    } catch (e) { Toast.error(e.message); }
    this._draw(items);
  },

  async _loadWeek() {
    const s = this._weekStart(this._parse(this.date));
    const e = new Date(s); e.setDate(e.getDate()+6);
    let items = [];
    try { items = (await get(`/api/orders/scheduler/range?from_str=${this._fmt(s)}&to_str=${this._fmt(e)}`)).items; }
    catch (err) { Toast.error(err.message); }
    this._lastItems = items;
    this._drawWeek(this._applyTechFilter(items), s);
  },

  async _loadMonth() {
    const dt = this._parse(this.date);
    const first = new Date(dt.getFullYear(), dt.getMonth(), 1);
    const last = new Date(dt.getFullYear(), dt.getMonth()+1, 0);
    let items = [];
    try { items = (await get(`/api/orders/scheduler/range?from_str=${this._fmt(first)}&to_str=${this._fmt(last)}`)).items; }
    catch (err) { Toast.error(err.message); }
    this._lastItems = items;
    this._drawMonth(this._applyTechFilter(items), dt);
  },

  _draw(items) {
    const hours = [];
    for (let h = this.startHour; h <= this.endHour; h++) hours.push(h);
    const colW = 88;          // px per hour
    this._colW = colW;
    const rowH = 64;          // px per technician row
    const labelW = 130;

    // group items by technician (null => Unassigned)
    const rows = this.employees.map(e => ({ id: e.id, name: e.name, items: [] }));
    rows.push({ id: null, name: 'Unassigned', items: [] });
    const rowById = {};
    rows.forEach(r => rowById[r.id || '_none'] = r);
    items.forEach(it => {
      const key = it.assigned_to_id || '_none';
      (rowById[key] || rowById['_none']).items.push(it);
    });
    // hide empty unassigned row if nothing there
    const visibleRows = rows.filter(r => r.id !== null || r.items.length);

    const totalW = labelW + hours.length * colW;
    const isToday = this.date === this._today();

    const statusColors = {
      new:'#4f8ef7', diagnosed:'#f5a623', waiting_parts:'#f06daa',
      in_repair:'#9b7bf4', done:'#3dca7a', delivered:'#2cd4c0',
    };

    const blockHtml = (it) => {
      const start = new Date(it.scheduled_for);
      const h = start.getHours() + start.getMinutes()/60;
      const left = (h - this.startHour) * colW;
      const w = Math.max((it.duration_min/60) * colW - 4, 56);
      const isBk = it.kind === 'booking';
      const color = isBk ? '#8b5cf6' : (statusColors[it.status] || '#4f8ef7');
      const tlabel = `${start.getHours()}:${String(start.getMinutes()).padStart(2,'0')}`;
      const heading = isBk ? 'Booking' : it.order_number;
      return `<div class="sch-block" draggable="true"
        data-id="${it.id}" data-dur="${it.duration_min}" data-kind="${it.kind || 'order'}"
        style="left:${left}px;width:${w}px;background:${color}1f;border-color:${color}66"
        ondragstart="Scheduler._dragStart(event,'${it.id}',${it.duration_min},'${it.kind || 'order'}')"
        ondragend="Scheduler._dragEnd(event)"
        onclick="Scheduler._clickBlock(event,'${it.id}','${it.kind || 'order'}','${it.order_id || ''}')"
        title="${heading} · ${it.client_name||'Walk-in'} — drag to move${isBk ? '' : ', drag right edge to resize'}">
        <div class="sch-block-top" style="color:${color}">
          <span class="sch-block-num">${heading}</span>
          <span class="sch-block-time">${tlabel}</span>
        </div>
        <div class="sch-block-client">${it.client_name || 'Walk-in'}${isBk ? ' · appt' : ''}</div>
        ${isBk ? '' : `<div class="sch-resize" onmousedown="Scheduler._resizeStart(event,'${it.id}',${it.duration_min})" title="Drag to change duration"></div>`}
      </div>`;
    };

    this.container.innerHTML = `
      ${this._toolbar(items.length)}
      <div class="card" style="padding:0;overflow:hidden">
        <div class="sch-scroll">
          <div class="sch-grid" style="width:${totalW}px">
            <!-- header -->
            <div class="sch-head-row" style="height:34px">
              <div class="sch-corner" style="width:${labelW}px">Technician</div>
              ${hours.map(h => `<div class="sch-hour" style="width:${colW}px">${h}:00</div>`).join('')}
            </div>
            <!-- rows -->
            ${visibleRows.map(r => `
              <div class="sch-row" style="height:${rowH}px">
                <div class="sch-tech" style="width:${labelW}px">
                  ${r.id ? avatarHtml(r.name,24) : `<div class="sch-noassign">${icon('user',14)}</div>`}
                  <span class="sch-tech-name">${r.name}</span>
                </div>
                <div class="sch-track" data-tech="${r.id||''}" style="width:${hours.length*colW}px"
                  ondragover="Scheduler._dragOver(event)"
                  ondragleave="Scheduler._dragLeave(event)"
                  ondrop="Scheduler._drop(event,'${r.id||''}')">
                  ${hours.map((h,i)=>`<div class="sch-cell" style="left:${i*colW}px;width:${colW}px" onclick="Scheduler.quickCreate('${r.id||''}',${h})"></div>`).join('')}
                  ${r.items.map(blockHtml).join('')}
                </div>
              </div>`).join('')}
          </div>
        </div>
      </div>
      <div class="sch-legend">
        ${Object.entries(statusColors).map(([s,c])=>`<span class="sch-leg"><span class="sch-leg-dot" style="background:${c}"></span>${s.replace(/_/g,' ')}</span>`).join('')}
        <span style="margin-left:auto;font-size:11px;color:var(--tx-4)">Drag to move · drag right edge to resize · click empty slot to add</span>
      </div>`;
  },

  _statusColors: { new:'#4f8ef7', diagnosed:'#f5a623', waiting_parts:'#f06daa', in_repair:'#9b7bf4', done:'#3dca7a', delivered:'#2cd4c0' },

  /* ── WEEK VIEW: 7 day columns ── */
  _drawWeek(items, weekStart) {
    const days = [];
    for (let i = 0; i < 7; i++) { const d = new Date(weekStart); d.setDate(d.getDate()+i); days.push(d); }
    const byDay = {};
    days.forEach(d => byDay[this._fmt(d)] = []);
    items.forEach(it => { const k = it.scheduled_for.slice(0,10); if (byDay[k]) byDay[k].push(it); });
    const todayStr = this._today();
    const sc = this._statusColors;

    this.container.innerHTML = `
      ${this._toolbar(items.length)}
      <div class="card" style="padding:0;overflow:hidden">
        <div class="sch-week">
          ${days.map(d => {
            const k = this._fmt(d);
            const list = (byDay[k]||[]).sort((a,b)=>a.scheduled_for.localeCompare(b.scheduled_for));
            const isToday = k === todayStr;
            return `<div class="sch-week-col ${isToday?'is-today':''}"
              ondragover="Scheduler._dayOver(event)" ondragleave="Scheduler._dayLeave(event)"
              ondrop="Scheduler._dayDrop(event,'${k}')">
              <div class="sch-week-head" onclick="Scheduler.date='${k}';Scheduler.setMode('day')">
                <div class="sch-week-dow">${d.toLocaleDateString('en-IN',{weekday:'short'})}</div>
                <div class="sch-week-day">${d.getDate()}</div>
              </div>
              <div class="sch-week-body">
                ${list.map(it=>{
                  const c = sc[it.status]||'#4f8ef7';
                  const t = new Date(it.scheduled_for);
                  return `<div class="sch-week-evt" draggable="true"
                    ondragstart="Scheduler._dragStart(event,'${it.id}',${it.duration_min})"
                    ondragend="Scheduler._dragEnd(event)"
                    style="background:${c}1f;border-left:3px solid ${c}" onclick="OrdersPage.viewOrder('${it.id}')" title="${it.order_number} · ${it.client_name||'Walk-in'}${it.assigned_to_name?' · '+it.assigned_to_name:''} — drag to another day">
                    <div class="sch-week-time" style="color:${c}">${t.getHours()}:${String(t.getMinutes()).padStart(2,'0')}</div>
                    <div class="sch-week-num">${it.order_number}</div>
                    <div class="sch-week-client">${it.client_name||'Walk-in'}</div>
                    ${!this._techFilter && it.assigned_to_name?`<div class="sch-week-tech">${it.assigned_to_name}</div>`:''}
                  </div>`;
                }).join('') || '<div class="sch-week-empty">—</div>'}
              </div>
            </div>`;
          }).join('')}
        </div>
      </div>
      <div class="sch-legend">
        ${Object.entries(sc).map(([s,c])=>`<span class="sch-leg"><span class="sch-leg-dot" style="background:${c}"></span>${s.replace(/_/g,' ')}</span>`).join('')}
        <span style="margin-left:auto;font-size:11px;color:var(--tx-4)">Click a day header to open its day view</span>
      </div>`;
  },

  /* ── MONTH VIEW: calendar grid ── */
  _drawMonth(items, anchor) {
    const year = anchor.getFullYear(), month = anchor.getMonth();
    const first = new Date(year, month, 1);
    const startPad = (first.getDay() + 6) % 7;  // Monday-start
    const daysInMonth = new Date(year, month+1, 0).getDate();
    const byDay = {};
    items.forEach(it => { const k = it.scheduled_for.slice(0,10); (byDay[k]=byDay[k]||[]).push(it); });
    const todayStr = this._today();
    const sc = this._statusColors;

    const cells = [];
    for (let i = 0; i < startPad; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push(d);
    while (cells.length % 7 !== 0) cells.push(null);

    const dows = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    this.container.innerHTML = `
      ${this._toolbar(items.length)}
      <div class="card" style="padding:14px">
        <div class="sch-month-dows">${dows.map(d=>`<div>${d}</div>`).join('')}</div>
        <div class="sch-month-grid">
          ${cells.map(d => {
            if (!d) return `<div class="sch-month-cell sch-month-empty"></div>`;
            const k = this._fmt(new Date(year, month, d));
            const list = (byDay[k]||[]).sort((a,b)=>a.scheduled_for.localeCompare(b.scheduled_for));
            const isToday = k === todayStr;
            const shown = list.slice(0,3);
            const more = list.length - shown.length;
            return `<div class="sch-month-cell ${isToday?'is-today':''}"
              ondragover="Scheduler._dayOver(event)" ondragleave="Scheduler._dayLeave(event)"
              ondrop="Scheduler._dayDrop(event,'${k}')">
              <div class="sch-month-daynum" onclick="Scheduler.date='${k}';Scheduler.setMode('day')">${d}</div>
              ${shown.map(it=>{const c=sc[it.status]||'#4f8ef7';return `<div class="sch-month-pill" draggable="true"
                ondragstart="Scheduler._dragStart(event,'${it.id}',${it.duration_min})"
                ondragend="Scheduler._dragEnd(event)"
                style="background:${c}22;color:${c}" title="${it.order_number} · ${it.client_name||'Walk-in'} — drag to another day" onclick="event.stopPropagation();OrdersPage.viewOrder('${it.id}')">${it.order_number}</div>`;}).join('')}
              ${more>0?`<div class="sch-month-more" onclick="Scheduler.date='${k}';Scheduler.setMode('day')">+${more} more</div>`:''}
            </div>`;
          }).join('')}
        </div>
      </div>
      <div class="sch-legend">
        ${Object.entries(sc).map(([s,c])=>`<span class="sch-leg"><span class="sch-leg-dot" style="background:${c}"></span>${s.replace(/_/g,' ')}</span>`).join('')}
        <span style="margin-left:auto;font-size:11px;color:var(--tx-4)">Click a day to open its day view</span>
      </div>`;
  },

  quickCreate(techId, hour) {
    const when = `${this.date}T${String(hour).padStart(2,'0')}:00`;
    OrdersPage.newModal({ scheduled_for: when, assigned_to_id: techId || '' });
  },

  /* ── drag to reschedule ── */
  _drag: { id: null, dur: 60, moved: false },

  _dragStart(e, id, dur, kind) {
    this._drag = { id, dur, kind: kind || 'order', moved: false };
    e.dataTransfer.effectAllowed = 'move';
    // REQUIRED for Firefox/Safari to actually start the drag + allow drop
    try { e.dataTransfer.setData('text/plain', id); } catch (_) {}
    // mark after a tick so the drag image is the full block
    setTimeout(() => e.target.classList.add('sch-dragging'), 0);
  },

  _dragEnd(e) {
    e.target.classList.remove('sch-dragging');
    document.querySelectorAll('.sch-track.drop-active').forEach(t => t.classList.remove('drop-active'));
  },

  _dragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    this._drag.moved = true;
    e.currentTarget.classList.add('drop-active');
  },

  _dragLeave(e) {
    e.currentTarget.classList.remove('drop-active');
  },

  async _drop(e, techId) {
    e.preventDefault();
    e.currentTarget.classList.remove('drop-active');
    if (!this._drag.id) return;
    // x within the track -> hour (snap to 30-min increments)
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left + e.currentTarget.scrollLeft;
    const hoursFloat = this.startHour + (x / this._colW);
    let snapped = Math.round(hoursFloat * 2) / 2;            // nearest half hour
    snapped = Math.max(this.startHour, Math.min(snapped, this.endHour));
    const hh = Math.floor(snapped);
    const mm = (snapped - hh) === 0.5 ? 30 : 0;
    const when = `${this.date}T${String(hh).padStart(2,'0')}:${String(mm).padStart(2,'0')}`;
    try {
      if (this._drag.kind === 'booking') {
        // bookings carry their own employee + time, not the order fields
        await patch(`/api/bookings/${this._drag.id}`, {
          scheduled_for: `${when}:00`, employee_id: techId || null });
        Toast.success('Appointment rescheduled');
      } else {
        await patch(`/api/orders/${this._drag.id}/schedule`, {
          scheduled_for: when, assigned_to_id: techId || null });
        Toast.success('Order rescheduled');
      }
      this.load();
    } catch (err) { Toast.error(err.message); }
    this._drag = { id: null, dur: 60, kind: 'order', moved: false };
  },

  /* ── week/month: drop onto a whole day (keeps the original time of day) ── */
  _dayOver(e) {
    if (!this._drag.id) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    e.currentTarget.classList.add('drop-active');
  },

  _dayLeave(e) {
    e.currentTarget.classList.remove('drop-active');
  },

  async _dayDrop(e, dayStr) {
    e.preventDefault();
    e.currentTarget.classList.remove('drop-active');
    if (!this._drag.id) return;
    // preserve the order's existing time-of-day; only the date changes
    let timePart = '09:00';
    const block = document.querySelector('.sch-dragging');
    const existing = this._lastItems?.find(i => i.id === this._drag.id);
    if (existing && existing.scheduled_for) timePart = existing.scheduled_for.slice(11, 16) || '09:00';
    const when = `${dayStr}T${timePart}`;
    try {
      await patch(`/api/orders/${this._drag.id}/schedule`, { scheduled_for: when });
      Toast.success('Order rescheduled');
      this.load();
    } catch (err) { Toast.error(err.message); }
    this._drag = { id: null, dur: 60, moved: false };
  },

  /* ── resize to change duration (drag right edge) ── */
  _resize: null,

  _resizeStart(e, id, dur) {
    e.preventDefault();
    e.stopPropagation();
    const block = e.target.closest('.sch-block');
    this._resize = { id, startX: e.clientX, startW: block.offsetWidth, block, dur };
    document.body.style.cursor = 'ew-resize';
    block.classList.add('sch-resizing');
    this._onMove = (ev) => this._resizeMove(ev);
    this._onUp = (ev) => this._resizeEnd(ev);
    window.addEventListener('mousemove', this._onMove);
    window.addEventListener('mouseup', this._onUp);
  },

  _resizeMove(e) {
    if (!this._resize) return;
    const dx = e.clientX - this._resize.startX;
    let w = Math.max(this._resize.startW + dx, 40);
    this._resize.block.style.width = w + 'px';
    // live duration label
    const mins = this._wToMinutes(w);
    const lbl = this._resize.block.querySelector('.sch-block-time');
    if (lbl) lbl.textContent = `${Math.round(mins)}m`;
  },

  _wToMinutes(w) {
    // width(px) -> hours -> minutes, snap to 15
    const hours = (w + 4) / this._colW;
    let mins = Math.round((hours * 60) / 15) * 15;
    return Math.max(15, mins);
  },

  async _resizeEnd(e) {
    window.removeEventListener('mousemove', this._onMove);
    window.removeEventListener('mouseup', this._onUp);
    document.body.style.cursor = '';
    if (!this._resize) return;
    const { id, block } = this._resize;
    const mins = this._wToMinutes(block.offsetWidth);
    block.classList.remove('sch-resizing');
    this._resize = null;
    try {
      await patch(`/api/orders/${id}/schedule`, { duration_min: mins });
      Toast.success(`Duration set to ${mins} min`);
      this.load();
    } catch (err) { Toast.error(err.message); this.load(); }
  },

  _clickBlock(e, id, kind, orderId) {
    // ignore click if it was a resize or the tail of a drag
    if (e.target.classList.contains('sch-resize')) return;
    if (this._drag.moved) { this._drag.moved = false; return; }
    if (kind === 'booking') { Router.go('bookings'); return; }
    OrdersPage.viewOrder(orderId || id);
  },

  /* ── Timeline (horizontal): rows = employees or resources, lanes = hours ── */
  async _loadTimeline() {
    let items = [];
    try { items = (await get(`/api/orders/scheduler/day?date_str=${this.date}`)).items; }
    catch (e) { Toast.error(e.message); }
    this._lastItems = items;
    this._drawTimeline(items);
  },

  _drawTimeline(items) {
    const hours = [];
    for (let h = this.startHour; h <= this.endHour; h++) hours.push(h);
    const laneMin = this.startHour * 60;
    const totalMin = (this.endHour - this.startHour) * 60;
    const PXMIN = 1.1;  // px per minute

    // rows depend on groupBy
    const rows = this.groupBy === 'resource'
      ? this.resources.map(r => ({ id: r.id, name: r.name, color: r.color }))
      : this.employees.map(e => ({ id: e.id, name: e.name, color: '#4f8ef7' }));
    if (this.groupBy === 'employee') rows.push({ id: '', name: 'Unassigned', color: '#9aa1bd' });

    const keyOf = (it) => this.groupBy === 'resource' ? (it.resource_id || '') : (it.assigned_to_id || '');

    const rowHtml = rows.map(row => {
      const mine = items.filter(it => keyOf(it) === row.id && it.scheduled_for);
      const blocks = mine.map(it => {
        const d = new Date(it.scheduled_for);
        const startMin = d.getHours() * 60 + d.getMinutes();
        const left = Math.max((startMin - laneMin) * PXMIN, 0);
        const width = Math.max((it.duration_min || 60) * PXMIN, 40);
        const c = this._statusColors[it.status] || row.color || '#4f8ef7';
        const t = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
        return `<div class="tl-block" draggable="true" style="left:${left}px;width:${width}px;background:${c}"
          ondragstart="Scheduler._dragStart(event,'${it.id}',${it.duration_min})" ondragend="Scheduler._dragEnd(event)"
          onclick="Scheduler._clickBlock(event,'${it.id}')"
          title="${it.order_number} · ${it.client_name||'Walk-in'} — drag to move, drag right edge to resize">
          <span class="tl-block-num">${it.order_number} · ${t}</span>
          <span>${it.client_name||'Walk-in'}</span>
          <div class="tl-resize-r" onmousedown="Scheduler._resizeStart(event,'${it.id}',${it.duration_min})"></div>
        </div>`;
      }).join('');
      return `<div class="tl-row">
        <div class="tl-rowlabel">${row.name}</div>
        <div class="tl-lane" data-row="${row.id}"
             ondragover="Scheduler._tlOver(event)" ondrop="Scheduler._tlDrop(event,'${row.id}')">
          <div class="tl-hours">${hours.map(h=>`<div class="tl-hour">${String(h).padStart(2,'0')}:00</div>`).join('')}</div>
          ${blocks}
        </div>
      </div>`;
    }).join('');

    this._colW = 60 * PXMIN;  // px per hour, for resize math
    this.container.innerHTML = `${this._toolbar(items.length)}
      <div class="card sch-timeline" style="padding:12px">
        <div class="tl-grid">${rowHtml || '<div style="padding:30px;text-align:center;color:var(--tx-4)">No rows</div>'}</div>
        <div style="font-size:11px;color:var(--tx-4);margin-top:8px">Drag a block to another row/time · drag its right edge to change duration · grouped <b>by ${this.groupBy}</b></div>
      </div>`;
  },

  _tlOver(e) { if (this._drag.id) { e.preventDefault(); } },
  async _tlDrop(e, rowId) {
    e.preventDefault();
    if (!this._drag.id) return;
    const lane = e.currentTarget;
    const rect = lane.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const PXMIN = 1.1;
    let mins = this.startHour * 60 + Math.round((x / PXMIN) / 15) * 15;
    const hh = String(Math.floor(mins / 60)).padStart(2, '0');
    const mm = String(mins % 60).padStart(2, '0');
    const when = `${this.date}T${hh}:${mm}`;
    const body = { scheduled_for: when };
    if (this.groupBy === 'resource') body.resource_id = rowId;
    else body.assigned_to_id = rowId;
    const id = this._drag.id;
    this._drag = { id: null, dur: 60, moved: false };
    try {
      await patch(`/api/orders/${id}/schedule`, body);
      Toast.success('Rescheduled');
      this.load();
    } catch (err) { Toast.error(err.message); this.load(); }
  },
};
