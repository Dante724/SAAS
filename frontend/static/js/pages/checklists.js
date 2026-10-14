/* ════════════════════════════════════════════════════
   Build 144dp — Checklist templates (Orderry-style)
   Template → Steps → Sections → typed Fields.
   Settings builder (ChecklistTemplatesPage) + a work-order
   fill helper (WOChecklist) used from the order drawer.
════════════════════════════════════════════════════ */

const CL_KINDS = [
  ['check_result', 'Check result (Pass/Fail/Caution/Unknown)'],
  ['yes_no', 'Yes / No'],
  ['text', 'Short text'],
  ['textarea', 'Long text'],
  ['dropdown', 'Dropdown'],
  ['multiselect', 'Multi-select'],
  ['date', 'Date'],
  ['file', 'File / photo'],
];
const _clEsc = (s) => (s == null ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

const ChecklistTemplatesPage = {
  _tpls: [],
  _draft: null,   // template being edited

  async load() {
    if (typeof App !== 'undefined' && App.can && !App.can('can_manage_checklist_templates')) {
      setContent(`<div class="card" style="text-align:center;color:var(--tx-3);padding:26px">You don't have permission to manage checklist templates.</div>`);
      return;
    }
    try { this._tpls = (await get('/api/checklist-templates')).items || []; } catch (e) { this._tpls = []; }
    setContent(`
      <div class="page-head">
        <div><div class="page-h1">Checklists</div>
          <div class="page-sub">Reusable inspections run the same way in every work order — steps, sections and typed fields.</div></div>
        <button class="btn btn-primary" onclick="ChecklistTemplatesPage.edit()">${icon('plus',15)} Template</button>
      </div>
      <div class="card-sm" style="padding:2px">
        <table style="width:100%;font-size:13px;border-collapse:collapse">
          <thead><tr style="color:var(--tx-4);font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.06em">
            <th style="text-align:left;padding:11px 14px">Name</th>
            <th style="text-align:left;padding:11px 14px">Applies to</th>
            <th style="text-align:left;padding:11px 14px">Steps</th>
            <th style="padding:11px 14px"></th></tr></thead>
          <tbody>
          ${this._tpls.length ? this._tpls.map(t => `
            <tr class="cl-row" style="border-top:1px solid var(--line,#eef2f9);cursor:pointer" ondblclick="ChecklistTemplatesPage.edit('${t.id}')">
              <td style="padding:11px 14px;font-weight:700">${_clEsc(t.name)}</td>
              <td style="padding:11px 14px;color:var(--tx-3)">${t.scope === 'selected' ? (t.type_ids.length + ' order type(s)') : 'All order types'}</td>
              <td style="padding:11px 14px;color:var(--tx-3)">${(t.steps || []).length}</td>
              <td style="padding:11px 14px;text-align:right">
                <button class="btn btn-glass btn-sm" onclick="event.stopPropagation();ChecklistTemplatesPage.edit('${t.id}')">Edit</button>
                <button class="btn btn-glass btn-sm" onclick="event.stopPropagation();ChecklistTemplatesPage.remove('${t.id}','${_clEsc(t.name)}')">Delete</button>
              </td></tr>`).join('')
            : `<tr><td colspan="4" style="padding:22px;text-align:center;color:var(--tx-3)">No checklist templates yet. Create one to standardize your checks.</td></tr>`}
          </tbody>
        </table>
      </div>`);
  },

  async remove(id, name) {
    if (!await Modal.confirm(`Delete checklist template "${name}"?`, {danger:true})) return;
    try { await del('/api/checklist-templates/' + id); Toast.success('Template deleted'); this.load(); }
    catch (e) { Toast.error(e.message); }
  },

  async edit(id) {
    let d = { name: '', scope: 'all', type_ids: [], steps: [] };
    if (id) { try { d = await get('/api/checklist-templates/' + id); } catch (e) { return Toast.error(e.message); } }
    if (!d.steps || !d.steps.length) d.steps = [{ title: 'Step 1', sections: [{ title: 'Section 1', items: [] }] }];
    this._draft = { id, ...d };
    this._openDrawer();
  },

  _openDrawer() {
    const d = this._draft;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${d.id ? 'Edit' : 'New'} checklist template</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body" id="cl-body">${this._formHtml()}</div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ChecklistTemplatesPage.save()">${d.id ? 'Save' : 'Create'}</button>
      </div>`, 'modal-side-wide');
  },

  _rerender() { const b = document.getElementById('cl-body'); if (b) b.innerHTML = this._formHtml(); },

  _formHtml() {
    const d = this._draft;
    return `
      <div class="field"><label class="field-label">Name</label>
        <input class="field-input" id="cl-name" value="${_clEsc(d.name)}" placeholder="Repair quality control"
          oninput="ChecklistTemplatesPage._draft.name=this.value"></div>
      <div class="field"><label class="field-label">Applies to</label>
        <div class="seg">
          <button class="seg-btn ${d.scope === 'all' ? 'on' : ''}" onclick="ChecklistTemplatesPage._setScope('all')">All order types</button>
          <button class="seg-btn ${d.scope === 'selected' ? 'on' : ''}" onclick="ChecklistTemplatesPage._setScope('selected')">Selected</button>
        </div></div>
      <div class="cl-steps">
        ${d.steps.map((st, si) => `
          <div class="cl-step">
            <div class="cl-step-head">
              <span class="cl-badge">STEP ${si + 1}</span>
              <input class="field-input cl-inline" value="${_clEsc(st.title)}" placeholder="Step name"
                oninput="ChecklistTemplatesPage._draft.steps[${si}].title=this.value">
              <button class="btn btn-glass btn-sm" onclick="ChecklistTemplatesPage._delStep(${si})">${icon('trash',13)}</button>
            </div>
            ${(st.sections || []).map((sec, ci) => `
              <div class="cl-section">
                <div class="cl-sec-head">
                  <input class="field-input cl-inline" value="${_clEsc(sec.title)}" placeholder="Section name"
                    oninput="ChecklistTemplatesPage._draft.steps[${si}].sections[${ci}].title=this.value">
                  <label class="cl-mini"><input type="checkbox" ${sec.allow_notes ? 'checked' : ''}
                    onchange="ChecklistTemplatesPage._draft.steps[${si}].sections[${ci}].allow_notes=this.checked"> notes & photos</label>
                  <button class="btn btn-glass btn-sm" onclick="ChecklistTemplatesPage._delSection(${si},${ci})">${icon('trash',13)}</button>
                </div>
                ${(sec.items || []).map((it, ii) => this._itemHtml(si, ci, ii, it)).join('')}
                <button class="cl-add" onclick="ChecklistTemplatesPage._addItem(${si},${ci})">${icon('plus',13)} Add item</button>
              </div>`).join('')}
            <button class="cl-add" onclick="ChecklistTemplatesPage._addSection(${si})">${icon('plus',13)} Add section</button>
          </div>`).join('')}
        <button class="btn btn-glass btn-sm" style="margin-top:8px" onclick="ChecklistTemplatesPage._addStep()">${icon('plus',13)} Add step</button>
      </div>`;
  },

  _itemHtml(si, ci, ii, it) {
    const path = `ChecklistTemplatesPage._draft.steps[${si}].sections[${ci}].items[${ii}]`;
    const showOpts = it.field_kind === 'dropdown' || it.field_kind === 'multiselect';
    return `
      <div class="cl-item">
        <input class="field-input cl-inline" value="${_clEsc(it.label)}" placeholder="Check title"
          oninput="${path}.label=this.value">
        <select class="field-select cl-kind" onchange="${path}.field_kind=this.value;ChecklistTemplatesPage._rerender()">
          ${CL_KINDS.map(([k, l]) => `<option value="${k}" ${it.field_kind === k ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
        <button class="btn btn-glass btn-sm" onclick="ChecklistTemplatesPage._delItem(${si},${ci},${ii})">${icon('trash',13)}</button>
        ${showOpts ? `<input class="field-input cl-opts" value="${_clEsc((it.options || []).join(', '))}" placeholder="Option A, Option B, …"
          oninput="${path}.options=this.value.split(',').map(s=>s.trim()).filter(Boolean)">` : ''}
        <div class="cl-flags">
          <label class="cl-mini"><input type="checkbox" ${it.required !== false ? 'checked' : ''} onchange="${path}.required=this.checked"> required</label>
          <label class="cl-mini"><input type="checkbox" ${it.client_visible ? 'checked' : ''} onchange="${path}.client_visible=this.checked"> client-visible</label>
          ${it.field_kind === 'check_result' ? `<label class="cl-mini"><input type="checkbox" ${it.affects_result !== false ? 'checked' : ''} onchange="${path}.affects_result=this.checked"> affects result</label>` : ''}
        </div>
      </div>`;
  },

  _setScope(s) { this._draft.scope = s; this._rerender(); },
  _addStep() { this._draft.steps.push({ title: 'Step ' + (this._draft.steps.length + 1), sections: [{ title: 'Section 1', items: [] }] }); this._rerender(); },
  _delStep(si) { this._draft.steps.splice(si, 1); this._rerender(); },
  _addSection(si) { this._draft.steps[si].sections.push({ title: 'Section', items: [] }); this._rerender(); },
  _delSection(si, ci) { this._draft.steps[si].sections.splice(ci, 1); this._rerender(); },
  _addItem(si, ci) { this._draft.steps[si].sections[ci].items.push({ label: '', field_kind: 'check_result', options: [], required: true, client_visible: false, affects_result: true }); this._rerender(); },
  _delItem(si, ci, ii) { this._draft.steps[si].sections[ci].items.splice(ii, 1); this._rerender(); },

  async save() {
    const d = this._draft;
    if (!d.name.trim()) return Toast.error('Give the template a name');
    const payload = {
      name: d.name.trim(), scope: d.scope, type_ids: d.type_ids || [],
      steps: d.steps.map(st => ({
        title: st.title, description: st.description || null,
        sections: (st.sections || []).map(sec => ({
          title: sec.title, allow_notes: !!sec.allow_notes,
          items: (sec.items || []).filter(i => i.label && i.label.trim()).map(i => ({
            label: i.label, field_kind: i.field_kind, options: i.options || [],
            required: i.required !== false, client_visible: !!i.client_visible,
            affects_result: i.affects_result !== false,
          })),
        })),
      })),
    };
    try {
      if (d.id) await put('/api/checklist-templates/' + d.id, payload);
      else await post('/api/checklist-templates', payload);
      Toast.success('Template saved'); Modal.close(); this.load();
    } catch (e) { Toast.error(e.message); }
  },
};


/* ── Work-order Checklists tab (fill) ─────────────────────────────── */
const WOChecklist = {
  _order: null, _type: null,

  async load(orderId, typeId) {
    this._order = orderId; this._type = typeId || '';
    const host = document.getElementById('wo-cl-host');
    if (!host) return;
    let cls = [];
    try { cls = (await get('/api/orders/' + orderId + '/checklists')).items || []; } catch (e) {}
    const canAdd = typeof App === 'undefined' || !App.can || App.can('can_create_checklist');
    host.innerHTML = `
      <div class="wocl-list">
        ${cls.length ? cls.map(c => {
          const done = (c.steps_done || 0) + (c.items_done || 0);
          const total = (c.steps_total || 0) + (c.items_total || 0);
          const pct = total ? Math.round(done / total * 100) : 0;
          return `
          <div class="wocl-card wocl-card-ring" ondblclick="WOChecklist.open('${c.id}')">
            <div class="cl-ring" style="--p:${pct}"><i>${pct}%</i></div>
            <div style="flex:1;min-width:0">
              <div class="wocl-row">
                <span style="font-weight:700">${_clEsc(c.title)}</span>
                <span class="cl-res ${c.status === 'completed' ? (c.result || 'passed') : 'in_progress'}">${c.status === 'completed' ? (c.result === 'failed' ? 'Failed' : 'Passed') : 'In progress'}</span>
              </div>
              <div class="wocl-meta">${c.author ? _clEsc(c.author) + ' · ' : ''}${c.steps_done}/${c.steps_total} steps · ${c.items_done}/${c.items_total} fields</div>
            </div>
          </div>`; }).join('') : `<div class="wocl-empty">
            <div class="wocl-empty-t">No checklists on this work order yet</div>
            <div class="wocl-empty-s">Attach an intake or QC checklist to track the job — passed / failed steps, photos, and fields.</div>
            ${canAdd ? `<button class="btn btn-primary btn-sm" style="margin-top:12px" onclick="WOChecklist.pickTemplate()">${icon('plus',14)} Add checklist</button>` : ''}
          </div>`}
      </div>
      ${canAdd && cls.length ? `<button class="btn btn-glass btn-sm" style="margin-top:10px" onclick="WOChecklist.pickTemplate()">${icon('plus',14)} Add checklist</button>` : ''}`;
  },

  async pickTemplate() {
    let tpls = [];
    try { tpls = (await get('/api/checklist-templates' + (this._type ? '?type_id=' + this._type : ''))).items || []; } catch (e) {}
    tpls = tpls.filter(t => (t.steps || []).length);
    if (!tpls.length) return Toast.error('No checklist templates for this order type. Create one in Settings → Checklists.');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add checklist</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="wocl-list">
          ${tpls.map(t => `<div class="wocl-card" onclick="WOChecklist.attach('${t.id}')">
            <div style="font-weight:700">${_clEsc(t.name)}</div>
            <div class="wocl-meta">${(t.steps || []).length} step(s)</div></div>`).join('')}
        </div>
      </div>`, '', true);
  },

  async attach(tid) {
    try { await post('/api/orders/' + this._order + '/checklists', { template_id: tid }); Modal.close(); this.load(this._order, this._type); }
    catch (e) { Toast.error(e.message); }
  },

  async open(cid) {
    let cl;
    try { cl = await get('/api/orders/' + this._order + '/checklists/' + cid); } catch (e) { return Toast.error(e.message); }
    const canEdit = typeof App === 'undefined' || !App.can || App.can('can_edit_checklist');
    const canDel = typeof App === 'undefined' || !App.can || App.can('can_delete_checklist');
    let lastStep = null;
    const body = cl.items.map(it => {
      const stepHdr = it.step_title !== lastStep ? (lastStep = it.step_title, `<div class="cl-fill-sec">${_clEsc(it.step_title)}${it.section_title ? ' · ' + _clEsc(it.section_title) : ''}</div>`) : '';
      return stepHdr + `<div class="cl-fill-item">
        <div style="font-size:13px;font-weight:600">${_clEsc(it.label)}${it.required ? '' : ' <span style="color:var(--tx-4);font-weight:400">(optional)</span>'}${it.client_visible ? ' <span class="wo-cl-chip">client</span>' : ''}</div>
        ${this._control(cid, it, canEdit)}
      </div>`;
    }).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${_clEsc(cl.title)}</span>
        <span class="cl-res ${cl.summary.status === 'completed' ? (cl.summary.result || 'passed') : 'in_progress'}" style="margin-left:8px">${cl.summary.status === 'completed' ? (cl.summary.result === 'failed' ? 'Failed' : 'Passed') : 'In progress'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body" id="cl-fill-body">${body}</div>
      <div class="modal-footer">
        ${canDel ? `<button class="btn btn-glass" onclick="WOChecklist.remove('${cid}')">Delete</button>` : ''}
        <button class="btn btn-primary" onclick="Modal.close();WOChecklist.load(WOChecklist._order,WOChecklist._type)">Done</button>
      </div>`, 'modal-side-wide', true);
  },

  _control(cid, it, canEdit) {
    const dis = canEdit ? '' : 'disabled';
    const v = it.value;
    if (it.field_kind === 'check_result') {
      const canRec = typeof App === 'undefined' || !App.can || App.can('can_recommend');
      return `<div class="cl-cr" data-item="${it.id}">
        ${['pass', 'caution', 'fail', 'unknown'].map(s => `<button class="${s} ${v === s ? 'on' : ''}" ${dis} onclick="WOChecklist.fill('${cid}','${it.id}','${s}')">${s[0].toUpperCase() + s.slice(1)}</button>`).join('')}</div>
        ${canRec ? `<div id="rec-${it.id}" class="cl-recwrap" style="${v === 'fail' ? '' : 'display:none'}">
          <button class="cl-add" style="margin-top:6px" onclick="WOChecklist.recommend('${cid}','${it.id}')">${icon('bolt',12)} Create recommendation from this fail</button></div>` : ''}`;
    }
    if (it.field_kind === 'yes_no') {
      return `<div class="cl-cr" data-item="${it.id}">
        ${['yes', 'no'].map(s => `<button class="${s === 'yes' ? 'pass' : 'fail'} ${v === s ? 'on' : ''}" ${dis} onclick="WOChecklist.fill('${cid}','${it.id}','${s}')">${s[0].toUpperCase() + s.slice(1)}</button>`).join('')}</div>`;
    }
    if (it.field_kind === 'textarea') return `<textarea class="field-textarea" ${dis} style="margin-top:4px" onchange="WOChecklist.fill('${cid}','${it.id}',this.value)">${_clEsc(v || '')}</textarea>`;
    if (it.field_kind === 'date') return `<input class="field-input" type="date" ${dis} style="margin-top:4px;max-width:200px" value="${_clEsc(v || '')}" onchange="WOChecklist.fill('${cid}','${it.id}',this.value)">`;
    if (it.field_kind === 'dropdown') return `<select class="field-select" ${dis} style="margin-top:4px;max-width:260px" onchange="WOChecklist.fill('${cid}','${it.id}',this.value)"><option value="">—</option>${(it.options || []).map(o => `<option ${v === o ? 'selected' : ''}>${_clEsc(o)}</option>`).join('')}</select>`;
    if (it.field_kind === 'multiselect') return `<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:4px">${(it.options || []).map(o => `<label class="cl-mini"><input type="checkbox" ${dis} ${(v || []).includes(o) ? 'checked' : ''} onchange="WOChecklist.toggleMulti('${cid}','${it.id}','${_clEsc(o)}',this.checked)"> ${_clEsc(o)}</label>`).join('')}</div>`;
    if (it.field_kind === 'file') return `<div style="font-size:12px;color:var(--tx-3);margin-top:4px">${(it.file_ids || []).length ? (it.file_ids.length + ' file(s) attached') : 'Upload from the Files tab and it links here.'}</div>`;
    return `<input class="field-input" ${dis} style="margin-top:4px" value="${_clEsc(v || '')}" onchange="WOChecklist.fill('${cid}','${it.id}',this.value)">`;
  },

  _multiVals: {},
  async toggleMulti(cid, iid, opt, on) {
    const el = document.querySelector(`[data-item="${iid}"]`);
    const box = document.getElementById('cl-fill-body');
    const cur = this._multiVals[iid] || [];
    const next = on ? [...new Set([...cur, opt])] : cur.filter(x => x !== opt);
    this._multiVals[iid] = next;
    this.fill(cid, iid, next);
  },

  async fill(cid, iid, value) {
    try {
      const r = await patch('/api/orders/' + this._order + '/checklists/' + cid + '/items/' + iid, { value });
      // reflect check-result selection instantly
      const grp = document.querySelector(`.cl-cr[data-item="${iid}"]`);
      if (grp) grp.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.textContent.trim().toLowerCase() === String(value).toLowerCase()));
      const rec = document.getElementById('rec-' + iid);
      if (rec) rec.style.display = (String(value).toLowerCase() === 'fail') ? '' : 'none';
    } catch (e) { Toast.error(e.message); }
  },

  async recommend(cid, iid) {
    try {
      await post('/api/orders/' + this._order + '/checklists/' + cid + '/items/' + iid + '/recommend', {});
      Toast.success('Recommendation drafted — see the Services & Products tab');
    } catch (e) { Toast.error(e.message); }
  },

  async remove(cid) {
    if (!await Modal.confirm('Delete this checklist from the work order?', {danger:true})) return;
    try { await del('/api/orders/' + this._order + '/checklists/' + cid); Modal.close(); this.load(this._order, this._type); }
    catch (e) { Toast.error(e.message); }
  },
};
