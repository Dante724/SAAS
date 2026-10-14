/* ════════════════════════════════════════════════════════════════
   Build 144bv — List extras: custom-field columns + sort + saved views.
   Shared across Contacts / Products / Assets. Works by *augmenting* the
   table the page already rendered, so each page only:
     • gives each <tr> a data-rid="<item.id>"
     • calls ListExtras.attach(tableId, entity, itemsById) after rendering
     • adds a "Columns" button that calls ListExtras.openMenu(entity, reload)
   Nothing about the page's existing columns changes.

   entity: 'client' | 'product' | 'device'
   Persistence: server (Build 144ca, /api/prefs/listview:<entity>) with
   localStorage as an instant-render offline cache. Choices follow the user
   across devices; localStorage keeps the UI snappy and works while offline.
════════════════════════════════════════════════════════════════ */
window.ListExtras = {
  _fields: {},           // entity -> [{id,label,type}]
  _cur: {},              // entity -> {tableId, itemsById, reload}
  _pulled: {},           // entity -> true once server prefs merged this session

  // ---- server sync (Build 144ca) --------------------------------
  _pk(entity) { return 'listview:' + entity; },
  async _pull(entity) {
    if (this._pulled[entity]) return;
    this._pulled[entity] = true;
    try {
      const r = await get('/api/prefs/' + this._pk(entity));
      const v = r && r.value ? r.value : null;
      if (v && (v.cols || v.sort)) this._save(entity, { cols: v.cols || [], sort: v.sort || null }, true);
      if (v && Array.isArray(v.views)) this._saveViews(entity, v.views, true);
    } catch (e) { /* offline → keep local cache */ }
  },
  _push(entity) {
    const st = this._state(entity);
    const body = { value: { cols: st.cols || [], sort: st.sort || null, views: this._views(entity) } };
    try { put('/api/prefs/' + this._pk(entity), body); } catch (e) {}   // fire-and-forget
  },

  async _load(entity) {
    if (this._fields[entity]) return this._fields[entity];
    try {
      const r = await get(`/api/forms/entity-fields?entity=${entity}`);
      this._fields[entity] = (r.items || []).map(f => ({ id: f.id, label: f.field_label, type: f.field_type }));
    } catch (e) { this._fields[entity] = []; }
    return this._fields[entity];
  },

  _sk(entity) { return 'ros_lx_' + entity; },
  _state(entity) { try { return JSON.parse(localStorage.getItem(this._sk(entity)) || '{}'); } catch (e) { return {}; } },
  _save(entity, st, fromServer) { try { localStorage.setItem(this._sk(entity), JSON.stringify(st)); } catch (e) {} if (!fromServer) this._push(entity); },
  _vk(entity) { return 'ros_lxviews_' + entity; },
  _views(entity) { try { return JSON.parse(localStorage.getItem(this._vk(entity)) || '[]'); } catch (e) { return []; } },
  _saveViews(entity, v, fromServer) { try { localStorage.setItem(this._vk(entity), JSON.stringify(v)); } catch (e) {} if (!fromServer) this._push(entity); },

  _fmt(v) {
    if (v === null || v === undefined || v === '') return '';
    if (Array.isArray(v)) return v.join(', ');
    if (v === true) return 'Yes'; if (v === false) return 'No';
    return String(v);
  },

  async attach(tableId, entity, itemsById) {
    const table = document.getElementById(tableId);
    if (!table) return;
    await this._pull(entity);   // merge server prefs (once per session), then render
    const fields = await this._load(entity);
    this._cur[entity] = { tableId, itemsById, reload: this._cur[entity]?.reload };
    const st = this._state(entity);
    const cols = (st.cols || []).filter(id => fields.some(f => f.id === id));

    const headRow = table.tHead && table.tHead.rows[0];
    const body = table.tBodies[0];
    if (!headRow || !body) return;

    // clean any previously-injected cells (idempotent on re-render)
    [...table.querySelectorAll('.lx-col')].forEach(el => el.remove());

    // inject custom-field columns just before a trailing "actions" header (if any)
    const trailing = headRow.lastElementChild && !headRow.lastElementChild.textContent.trim()
      ? headRow.lastElementChild : null;
    cols.forEach(id => {
      const f = fields.find(x => x.id === id); if (!f) return;
      const th = document.createElement('th');
      th.className = 'lx-col'; th.dataset.lxkey = 'cf:' + id;
      th.innerHTML = `${this._esc(f.label)} <span class="lx-caret"></span>`;
      th.style.cursor = 'pointer';
      th.onclick = () => this._toggleSort(entity, 'cf:' + id);
      headRow.insertBefore(th, trailing);
      [...body.rows].forEach(tr => {
        const rid = tr.dataset.rid || tr.dataset.id;
        const item = rid && itemsById ? itemsById[rid] : null;
        const val = item && item.custom_data ? item.custom_data[id] : '';
        const td = document.createElement('td');
        td.className = 'lx-col'; td.dataset.lxkey = 'cf:' + id;
        td.style.color = 'var(--tx-2)';
        td.textContent = this._fmt(val);
        // place the custom cell before the row's trailing actions cell, if any
        const anchor = trailing ? tr.lastElementChild : null;
        tr.insertBefore(td, anchor);
      });
    });

    // make every text header sortable (built-in + custom)
    [...headRow.cells].forEach((th, i) => {
      if (th.classList.contains('lx-col')) return;   // custom already wired
      const label = th.textContent.replace(/\s+/g, ' ').trim();
      if (!label) return;                            // skip checkbox / actions
      if (!th.dataset.lxkey) th.dataset.lxkey = 'c:' + label;
      th.style.cursor = 'pointer';
      if (!th._lxSort) { th._lxSort = true; th.addEventListener('click', () => this._toggleSort(entity, th.dataset.lxkey)); }
    });

    this._applySort(entity, table);
  },

  _toggleSort(entity, key) {
    const st = this._state(entity);
    if (st.sort && st.sort.key === key) st.sort.dir = st.sort.dir === 'asc' ? 'desc' : 'asc';
    else st.sort = { key, dir: 'asc' };
    this._save(entity, st);
    const c = this._cur[entity];
    if (c) this._applySort(entity, document.getElementById(c.tableId));
  },

  _applySort(entity, table) {
    if (!table) return;
    const st = this._state(entity); const sort = st.sort;
    const headRow = table.tHead.rows[0]; const body = table.tBodies[0];
    // clear carets
    headRow.querySelectorAll('.lx-caret').forEach(c => c.textContent = '');
    headRow.querySelectorAll('th').forEach(th => th.removeAttribute('data-lxdir'));
    if (!sort) return;
    let idx = -1;
    [...headRow.cells].forEach((th, i) => { if (th.dataset.lxkey === sort.key) idx = i; });
    if (idx < 0) return;
    const th = headRow.cells[idx];
    th.setAttribute('data-lxdir', sort.dir);
    let caret = th.querySelector('.lx-caret');
    if (!caret) { caret = document.createElement('span'); caret.className = 'lx-caret'; th.appendChild(caret); }
    caret.textContent = sort.dir === 'asc' ? ' ▲' : ' ▼';

    const rows = [...body.rows];
    const val = tr => (tr.cells[idx] ? tr.cells[idx].textContent.trim() : '');
    const num = s => { const n = parseFloat(String(s).replace(/[^0-9.\-]/g, '')); return isNaN(n) ? null : n; };
    rows.sort((a, b) => {
      const av = val(a), bv = val(b), an = num(av), bn = num(bv);
      let r;
      if (an !== null && bn !== null) r = an - bn;
      else r = av.localeCompare(bv, undefined, { numeric: true, sensitivity: 'base' });
      return sort.dir === 'asc' ? r : -r;
    });
    rows.forEach(tr => body.appendChild(tr));
  },

  async openMenu(entity, reload) {
    if (this._cur[entity]) this._cur[entity].reload = reload;
    const fields = await this._load(entity);
    const st = this._state(entity);
    const cols = st.cols || [];
    const views = this._views(entity);
    const fieldRows = fields.length ? fields.map(f => `
      <label class="lx-check"><input type="checkbox" value="${f.id}" ${cols.includes(f.id) ? 'checked' : ''}
        onchange="ListExtras._toggleCol('${entity}','${f.id}',this.checked)"> ${this._esc(f.label)}
        <span style="color:var(--tx-4);font-size:11px">· ${f.type}</span></label>`).join('')
      : `<div style="color:var(--tx-4);font-size:13px">No custom fields for this entity yet. Add them in Settings → Forms.</div>`;
    const viewRows = views.length ? views.map((v, i) => `
      <div class="lx-view"><button class="btn btn-glass btn-sm" onclick="ListExtras._applyView('${entity}',${i})">${this._esc(v.name)}</button>
        <button class="cat-line-del" title="Delete" onclick="ListExtras._delView('${entity}',${i})">${typeof icon === 'function' ? icon('x', 12) : '×'}</button></div>`).join('')
      : `<div style="color:var(--tx-4);font-size:12px">No saved views yet.</div>`;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Columns &amp; views</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="lx-sec-t">Custom-field columns</div>
        <div class="lx-fields">${fieldRows}</div>
        <div class="lx-sec-t" style="margin-top:16px">Saved views</div>
        <div class="lx-views">${viewRows}</div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <input class="field-input" id="lx-vn" placeholder="Name this view (columns + sort)" style="flex:1">
          <button class="btn btn-primary btn-sm" onclick="ListExtras._saveView('${entity}')">Save view</button>
        </div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Done</button></div>`, 'modal-narrow');
  },

  _toggleCol(entity, id, on) {
    const st = this._state(entity); const cols = new Set(st.cols || []);
    if (on) cols.add(id); else cols.delete(id);
    st.cols = [...cols]; this._save(entity, st);
    const c = this._cur[entity]; if (c && c.reload) c.reload();
  },
  _saveView(entity) {
    const name = (document.getElementById('lx-vn').value || '').trim();
    if (!name) return Toast.error('Name the view');
    const st = this._state(entity); const views = this._views(entity);
    views.push({ name, cols: st.cols || [], sort: st.sort || null });
    this._saveViews(entity, views); Toast.success('View saved');
    this.openMenu(entity, this._cur[entity]?.reload);
  },
  _applyView(entity, i) {
    const v = this._views(entity)[i]; if (!v) return;
    const st = this._state(entity); st.cols = v.cols || []; st.sort = v.sort || null;
    this._save(entity, st); Modal.close();
    const c = this._cur[entity]; if (c && c.reload) c.reload();
  },
  _delView(entity, i) {
    const views = this._views(entity); views.splice(i, 1); this._saveViews(entity, views);
    this.openMenu(entity, this._cur[entity]?.reload);
  },
  _esc(s) { return String(s == null ? '' : s).replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c])); },
};
