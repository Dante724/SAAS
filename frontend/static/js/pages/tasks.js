/* Build 86 — Tasks.

   A task now carries the context its assignee actually needs: which job, which
   client, when it's due, and a feed of what's been said about it. The list leads
   with "My tasks" and sorts by due date, because that's the question someone
   opening this page is actually asking. */
const TasksPage = {
  _filter: { status: '', mine: false, overdue: false },
  _emps: {},

  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="TasksPage.edit()">+ New task</button>`);
    setContent(`
      <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">
        <div class="chip-row" id="task-filter">
          ${[['', 'All'], ['todo', 'To do'], ['in_progress', 'In progress'], ['done', 'Done']]
            .map(([s, l], i) => `<div class="chip${i === 0 ? ' active' : ''}" data-s="${s}"
              onclick="TasksPage.setStatus('${s}', this)">${l}</div>`).join('')}
        </div>
        <div class="chip-row">
          <div class="chip" id="chip-mine" onclick="TasksPage.toggle('mine', this)">My tasks</div>
          <div class="chip" id="chip-overdue" onclick="TasksPage.toggle('overdue', this)">Overdue</div>
        </div>
      </div>
      <div class="card"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Task</th><th>Status</th><th>Links</th><th>Assigned to</th>
          <th>Due</th></tr></thead>
        <tbody id="task-tbody">${skeletonRows(5)}</tbody>
      </table></div></div>`);
    await this.reload();
  },

  setStatus(s, el) {
    this._filter.status = s;
    document.querySelectorAll('#task-filter .chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    this.reload();
  },

  toggle(key, el) {
    this._filter[key] = !this._filter[key];
    el.classList.toggle('active', this._filter[key]);
    this.reload();
  },

  async reload() {
    const tbody = document.getElementById('task-tbody');
    if (!tbody) return;
    const qs = [];
    if (this._filter.status) qs.push(`status=${this._filter.status}`);
    if (this._filter.mine)   qs.push('mine=true');
    if (this._filter.overdue) qs.push('overdue=true');
    let tasks = [];
    try {
      const [r, emps] = await Promise.all([
        get(`/api/tasks${qs.length ? '?' + qs.join('&') : ''}`),
        get('/api/staff/employees'),
      ]);
      tasks = r.items || [];
      this._emps = {};
      (emps || []).forEach(e => { this._emps[e.id] = e.name; });
    } catch (e) { return Toast.error(e.message); }

    if (!tasks.length) {
      tbody.innerHTML = emptyRow(5, 'check-circle', 'No tasks', 'Nothing matches these filters');
      return;
    }
    tbody.innerHTML = tasks.map(t => {
      const late = t.overdue;
      const links = [
        t.order_id ? '<span class="badge">work order</span>' : '',
        t.client_id ? '<span class="badge">client</span>' : '',
        t.lead_id  ? '<span class="badge">lead</span>'   : '',
      ].filter(Boolean).join(' ') || '<span style="color:var(--tx-4)">—</span>';
      const onTime = t.status === 'done'
        ? (t.completed_on_time === false
            ? '<span class="badge" style="background:var(--red,#e5484d);color:#fff">late</span>'
            : '<span class="badge b-done">on time</span>')
        : badge(t.status);
      return `<tr style="cursor:pointer" ondblclick="TasksPage.open('${t.id}')">
        <td><span style="font-weight:800">${t.title}</span>
          ${t.description ? `<div style="font-size:11px;color:var(--tx-3);margin-top:1px">${t.description.slice(0, 60)}</div>` : ''}</td>
        <td>${onTime}</td>
        <td>${links}</td>
        <td style="font-size:12px">${t.assignee_name || '—'}</td>
        <td style="color:${late ? 'var(--red,#e5484d)' : 'var(--tx-3)'};font-weight:${late ? '800' : '500'}">
          ${t.due_at ? fmt.date(t.due_at) : '—'}${late ? ' · overdue' : ''}</td>
      </tr>`;
    }).join('');
  },

  async complete(id) {
    try {
      const r = await patch(`/api/tasks/${id}`, { status: 'done' });
      Toast.success(r.completed_on_time === false ? 'Completed (late)' : 'Completed on time');
      Modal.close();
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  _woClock() { return typeof App === 'undefined' || !App.me || App.me.clock_wo_enabled !== false; },
  async startTimer(id) {
    try {
      await post('/api/timeclock/start?switch=true', { task_id: id, category: 'task' });
      Toast.success('Timer started on this task');
    } catch (e) { Toast.error(e.message || 'Could not start timer'); }
  },

  async remove(id) {
    if (!await confirm2('Delete this task?')) return;
    try {
      await del(`/api/tasks/${id}`);
      Toast.success('Task deleted');
      Modal.close();
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── detail — opens as editable, with Delete bottom-right ── */
  async open(id) {
    let t, feed, emps = [];
    try {
      [t, feed, emps] = await Promise.all([
        get(`/api/tasks/${id}`),
        get(`/api/tasks/${id}/feed`),
        get('/api/staff/employees'),
      ]);
    } catch (e) { return Toast.error(e.message); }

    const empOpts = (emps || []).map(e =>
      `<option value="${e.id}" ${t.assigned_to_id === e.id ? 'selected' : ''}>${e.name}</option>`
    ).join('');

    const feedHtml = (feed.items || []).map(f =>
      `<div style="padding:8px 0;border-bottom:1px solid var(--g-border)">
        <div style="font-size:13px">${f.body}</div>
        <div style="font-size:11px;color:var(--tx-4);margin-top:2px">
          ${f.author_name || 'System'} · ${f.created_at ? fmt.datetime(f.created_at) : ''}
          ${f.kind === 'comment' ? '' : ' · system'}
        </div>
      </div>`
    ).join('') || '<div style="color:var(--tx-4);font-size:13px">Nothing yet</div>';

    const actionBtns = t.status !== 'done'
      ? `<div style="margin:12px 0">
          <button class="btn btn-primary btn-sm" onclick="TasksPage.complete('${id}')">Mark as done</button>
          ${this._woClock() ? ` <button class="btn btn-glass btn-sm" onclick="TasksPage.startTimer('${id}')">▶ Start timer</button>` : ''}
        </div>`
      : '';

    Modal.open(`
      <div class="modal-header"><span class="modal-title">${t.title}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        ${(t.order_number || t.client_name) ? `<div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--tx-3);margin-bottom:12px">
          ${t.order_number ? `<div><b>Order:</b> ${t.order_number}</div>` : ''}
          ${t.client_name ? `<div><b>Client:</b> ${t.client_name}</div>` : ''}
        </div>` : ''}
        <div class="field"><label class="field-label">Title</label>
          <input class="field-input" id="tk-title" value="${(t.title || '').replace(/"/g, '&quot;')}"></div>
        <div class="field"><label class="field-label">Description</label>
          <textarea class="field-input" id="tk-desc" rows="3">${t.description || ''}</textarea></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Assign to</label>
            <select class="field-select" id="tk-emp">
              <option value="">Unassigned</option>${empOpts}
            </select></div>
          <div class="field"><label class="field-label">Due</label>
            <input class="field-input" type="datetime-local" id="tk-due"
              value="${t.due_at ? t.due_at.slice(0, 16) : ''}"></div>
        </div>
        <div class="field"><label class="field-label">Link to work order <span style="color:var(--tx-4);font-weight:400">(optional)</span></label>
          <input class="field-input" id="tk-order" value="${t.order_id || ''}" placeholder="Work order id"></div>
        ${actionBtns}
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">Activity</div>
        <div style="display:flex;gap:8px;margin-bottom:12px">
          <input class="field-input" id="tk-comment" placeholder="Add a comment…" style="flex:1">
          <button class="btn btn-glass" onclick="TasksPage.comment('${id}')">Post</button>
        </div>
        <div style="max-height:260px;overflow:auto">${feedHtml}</div>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <button class="btn btn-primary" onclick="TasksPage.save('${id}')">Save</button>
          <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        </div>
        <div>
          <button class="btn btn-danger" onclick="TasksPage.remove('${id}')">Delete</button>
        </div>
      </div>`);
  },

  async comment(id) {
    const el = document.getElementById('tk-comment');
    const body = el.value.trim();
    if (!body) return;
    try {
      await post(`/api/tasks/${id}/comments`, { body });
      await this.open(id);
    } catch (e) { Toast.error(e.message); }
  },

  /* ── create only (new task) ── */
  async edit(id = null) {
    let t = null;
    if (id) { try { t = await get(`/api/tasks/${id}`); } catch (e) { return Toast.error(e.message); } }
    let emps = [];
    try { emps = await get('/api/staff/employees'); } catch (e) { /* non-fatal */ }

    Modal.open(`
      <div class="modal-header"><span class="modal-title">${t ? 'Edit' : 'New'} task</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Title</label>
          <input class="field-input" id="tk-title" value="${t?.title || ''}"></div>
        <div class="field"><label class="field-label">Description</label>
          <textarea class="field-input" id="tk-desc" rows="3">${t?.description || ''}</textarea></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Assign to</label>
            <select class="field-select" id="tk-emp">
              <option value="">Unassigned</option>
              ${emps.map(e => `<option value="${e.id}" ${t?.assigned_to_id === e.id ? 'selected' : ''}>${e.name}</option>`).join('')}
            </select></div>
          <div class="field"><label class="field-label">Due</label>
            <input class="field-input" type="datetime-local" id="tk-due"
              value="${t?.due_at ? t.due_at.slice(0, 16) : ''}"></div>
        </div>
        <div class="field"><label class="field-label">Link to work order <span style="color:var(--tx-4);font-weight:400">(optional)</span></label>
          <input class="field-input" id="tk-order" value="${t?.order_id || ''}" placeholder="Work order id"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="TasksPage.save(${id ? `'${id}'` : 'null'})">Save</button>
      </div>`);
  },

  async save(id) {
    const title = document.getElementById('tk-title').value.trim();
    if (!title) return Toast.error('Title is required');
    const due = document.getElementById('tk-due').value;
    const body = {
      title,
      description: document.getElementById('tk-desc').value || null,
      assigned_to_id: document.getElementById('tk-emp').value || null,
      due_at: due ? new Date(due).toISOString().slice(0, 19) : null,
      order_id: document.getElementById('tk-order').value || null,
    };
    try {
      if (id) await patch(`/api/tasks/${id}`, body);
      else    await post('/api/tasks', body);
      Modal.close();
      Toast.success(id ? 'Task updated' : 'Task created');
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Save failed'); }
  },
};

Router.register('tasks', () => TasksPage.load());
