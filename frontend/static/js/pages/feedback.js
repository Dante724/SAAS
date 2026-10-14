/* ════════════════════════════════════════════════════
   Phase E/F settings pages: Customer feedback + Document templates
════════════════════════════════════════════════════ */

const FeedbackPage = {
  _statuses: [],
  async load() {
    setTopbarActions('');
    setContent(`<div id="fb-wrap"><div class="card" style="padding:30px;text-align:center;color:var(--tx-4)">Loading…</div></div>`);
    let s = null;
    try {
      s = (await get('/api/feedback/settings')).settings;
      this._statuses = (await get('/api/statuses?is_estimate=false')).items;
    } catch (e) { return Toast.error(e.message); }
    s = s || { system:'five_point', active:false, heading:'How did we do?', question:'How would you rate your experience?', clarify_question:'Tell us more', bands:{}, trigger_status_id:null };
    const sys = s.system;
    document.getElementById('fb-wrap').innerHTML = `
      <div class="card" style="padding:22px;max-width:680px">
        <div class="dp-title" style="margin-bottom:4px">Customer feedback</div>
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:18px">Collect a rating when an order reaches a chosen status. The customer gets a link to a public form.</div>
        <label class="cash-check" style="margin-bottom:16px"><input type="checkbox" id="fb-active" ${s.active?'checked':''}> Enable customer feedback</label>
        <div class="field"><label class="field-label">Rating system</label>
          <div class="type-chips">
            ${[['five_point','5-point scale'],['like','Like / Dislike'],['nps','NPS (0–10)']].map(([k,l])=>`<div class="type-chip ${sys===k?'active':''}" onclick="FeedbackPage._pickSys('${k}',this)">${l}</div>`).join('')}
          </div>
        </div>
        <div class="field"><label class="field-label">Send when order reaches status</label>
          <select class="field-select" id="fb-trigger"><option value="">—</option>
            ${this._statuses.map(st=>`<option value="${st.id}" ${s.trigger_status_id===st.id?'selected':''}>${st.name}</option>`).join('')}
          </select>
        </div>
        <div class="field"><label class="field-label">Heading</label><input class="field-input" id="fb-heading" value="${(s.heading||'').replace(/"/g,'&quot;')}"></div>
        <div class="field"><label class="field-label">Question</label><input class="field-input" id="fb-question" value="${(s.question||'').replace(/"/g,'&quot;')}"></div>
        <div class="field"><label class="field-label">Clarifying question</label><input class="field-input" id="fb-clarify" value="${(s.clarify_question||'').replace(/"/g,'&quot;')}"></div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 8px">Answer by score band</div>
        <div class="field"><label class="field-label">Low score message</label><input class="field-input" id="fb-low" value="${(s.bands?.low||'').replace(/"/g,'&quot;')}" placeholder="e.g. We'll reach out to make it right"></div>
        <div class="field"><label class="field-label">Mid score message</label><input class="field-input" id="fb-mid" value="${(s.bands?.mid||'').replace(/"/g,'&quot;')}"></div>
        <div class="field"><label class="field-label">High score message</label><input class="field-input" id="fb-high" value="${(s.bands?.high||'').replace(/"/g,'&quot;')}" placeholder="e.g. Leave us a Google review"></div>
        <div style="display:flex;justify-content:flex-end;margin-top:12px"><button class="btn btn-primary" onclick="FeedbackPage.save()">Save settings</button></div>
      </div>
      <div class="card" style="padding:22px;max-width:680px;margin-top:16px">
        <div class="dp-title" style="margin-bottom:12px">Recent feedback</div>
        <div id="fb-list"></div>
      </div>`;
    this._sys = sys;
    this._loadList();
  },
  _sys: 'five_point',
  _pickSys(k, el) { this._sys = k; el.parentElement.querySelectorAll('.type-chip').forEach(c=>c.classList.remove('active')); el.classList.add('active'); },
  async _loadList() {
    try {
      const d = await get('/api/feedback/list');
      const el = document.getElementById('fb-list');
      const summary = `<div style="display:flex;gap:18px;margin-bottom:12px;font-size:13px">
        ${d.avg_five_point!=null?`<span><b>${d.avg_five_point}</b> avg</span>`:''}
        ${d.nps!=null?`<span><b>${d.nps}</b> NPS</span>`:''}
        <span><b>${d.response_rate}%</b> response rate</span></div>`;
      el.innerHTML = summary + (d.items.length
        ? d.items.slice(0,15).map(f=>`<div class="wo-pay-row"><span>${f.order_number} · ${f.pending?'<i style="color:var(--tx-4)">pending</i>':('Rated '+f.rating)}${f.comment?' · '+f.comment:''}</span><span style="color:var(--tx-4);font-size:11px">${f.submitted_at?fmt.date(f.submitted_at):''}</span></div>`).join('')
        : '<div style="color:var(--tx-4);font-size:13px">No feedback yet</div>');
    } catch {}
  },
  async save() {
    const body = {
      system: this._sys, active: document.getElementById('fb-active').checked,
      trigger_status_id: document.getElementById('fb-trigger').value || null,
      heading: document.getElementById('fb-heading').value,
      question: document.getElementById('fb-question').value,
      clarify_question: document.getElementById('fb-clarify').value,
      bands: { low: document.getElementById('fb-low').value, mid: document.getElementById('fb-mid').value, high: document.getElementById('fb-high').value },
    };
    try { await put('/api/feedback/settings', body); Toast.success('Feedback settings saved'); }
    catch (e) { Toast.error(e.message); }
  },
};
Router.register('feedback', () => FeedbackPage.load());


/* Build 144n — full-page document-template editor (Orderry-style):
   template picker + toolbar (font, size, colour, B/I/U, align, image, logo,
   table, HR) + contenteditable canvas + grouped Variables side-panel. */
const DOC_ASSIGN = [
  ['work_order', 'Work orders and estimates'], ['estimate', 'Estimates'],
  ['invoice', 'Invoices'], ['receipt', 'Receipt'], ['sale', 'Sales'],
  ['price_tag', 'Shelf price tags'],
  ['product_label', 'Product and service labels'], ['vehicle_label', 'Vehicle labels'],
];
// doc_types that are physical labels/price tags — mirrors backend LABEL_DOC_TYPES
const LABEL_DOC_TYPES = ['label', 'price_tag', 'product_label', 'vehicle_label'];
const DOC_FONTS = ['Sans-Serif', 'Serif', 'Monospace', 'Arial', 'Georgia', 'Times New Roman'];
const DOC_SIZES = [['2', '10pt'], ['3', '12pt'], ['4', '14pt'], ['5', '18pt'], ['6', '24pt'], ['7', '36pt']];

const DocsPage = {
  _items: [], _groups: [], _current: null, _company: null, _statuses: [],
  _embedBody: null,
  _setActions(html) { if (!this._embedBody) setTopbarActions(html); },
  _setBody(html) {
    if (this._embedBody) { const el = document.getElementById(this._embedBody); if (el) { el.innerHTML = html; return; } }
    setContent(html);
  },
  async mountInto(bodyId) { this._embedBody = bodyId; await this.load(); },

  async load() {
    this._setActions('');
    this._setBody(`<div id="docs-wrap"><div class="card" style="padding:30px;text-align:center;color:var(--tx-4)">Loading…</div></div>`);
    try {
      const d = await get('/api/documents/templates');
      this._items = d.items || [];
      this._groups = d.variable_groups || [{ group: 'Variables', items: d.variables || [] }];
    } catch (e) { return Toast.error(e.message); }
    try { this._company = await get('/api/staff/company'); } catch { this._company = null; }
    try { this._statuses = (await get('/api/statuses?is_estimate=false')).items || []; } catch { this._statuses = []; }
    if (!this._current || !this._items.some(t => t.id === this._current)) {
      this._current = this._items[0] ? this._items[0].id : null;
    }
    this._render();
  },

  _svg(name) {
    const S = (d) => `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
    return {
      undo: S('<path d="M4 7H10.5a3.5 3.5 0 010 7H7"/><path d="M6 4.5L3.5 7 6 9.5"/>'),
      redo: S('<path d="M12 7H5.5a3.5 3.5 0 000 7H9"/><path d="M10 4.5L12.5 7 10 9.5"/>'),
      alignLeft: S('<path d="M2.5 4h11M2.5 8h7M2.5 12h10"/>'),
      alignCenter: S('<path d="M2.5 4h11M4.5 8h7M3.5 12h9"/>'),
      alignRight: S('<path d="M2.5 4h11M6.5 8h7M3.5 12h10"/>'),
      alignJustify: S('<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>'),
      image: S('<rect x="2" y="3" width="12" height="10" rx="1.5"/><circle cx="5.5" cy="6.5" r="1"/><path d="M3 12l3-3 2.5 2.5L11 8l2 2"/>'),
      table: S('<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 7h12M2 10.5h12M6.5 3v10M10 3v10"/>'),
      rule: S('<path d="M2.5 8h11"/>'),
      code: S('<path d="M6 5L3 8l3 3M10 5l3 3-3 3"/>'),
    }[name] || '';
  },
  _render() {
    const t = this._items.find(x => x.id === this._current) || null;
    const btn = (cmd, label, title, arg) => `<button type="button" class="doc-tb" title="${title}" onclick="DocsPage.cmd('${cmd}'${arg !== undefined ? ",'" + arg + "'" : ''})">${label}</button>`;
    const groupsHtml = this._groups.map((g, gi) => `
      <div class="doc-var-group">
        <div class="doc-var-head" onclick="DocsPage._toggleGroup(${gi})">${g.group}<span>▾</span></div>
        <div class="doc-var-items" id="doc-vg-${gi}">
          ${g.items.map(v => `<button type="button" class="doc-var" onclick="DocsPage.insertVar('${v.replace(/'/g, "\\'")}')">${v}</button>`).join('')}
        </div>
      </div>`).join('');

    this._setBody(`
      <div class="doc-editor">
        <div class="doc-topbar">
          <button class="btn btn-primary btn-sm" onclick="DocsPage.newDoc()">+ New document</button>
          <select class="field-select" style="max-width:240px" onchange="DocsPage.pick(this.value)">
            ${this._items.length ? this._items.map(x => `<option value="${x.id}" ${x.id === this._current ? 'selected' : ''}>${(x.name || 'Untitled').replace(/</g, '&lt;')}${LABEL_DOC_TYPES.includes(x.doc_type) ? ' · label' : ''}</option>`).join('') : '<option>No templates yet</option>'}
          </select>
          ${t ? `<button class="btn btn-glass btn-sm" onclick="DocsPage.rename()" title="Rename / settings">${icon('signature',14)}</button>
                 <button class="btn btn-glass btn-sm" onclick="DocsPage.del('${t.id}')" title="Delete">${icon('ban',14)}</button>` : ''}
          <div style="flex:1"></div>
          ${t ? `<select class="field-select" style="max-width:150px" id="doc-pagesize" title="Page size">
                   ${[['a4', 'A4'], ['letter', 'Letter'], ['label', 'Label']].map(([k, l]) => `<option value="${k}" ${t.page_size === k ? 'selected' : ''}>${l}</option>`).join('')}
                 </select>
                 <button class="btn btn-glass btn-sm" onclick="DocsPage.printPreview()">Print</button>
                 <button class="btn btn-primary btn-sm" onclick="DocsPage.save()">Save</button>` : ''}
        </div>

        ${t ? `
        <div class="doc-main">
          <div class="doc-pane">
            <div class="doc-toolbar">
              ${btn('undo', this._svg('undo'), 'Undo')}${btn('redo', this._svg('redo'), 'Redo')}
              <span class="doc-tb-div"></span>
              <select class="doc-tb-sel" title="Font" onchange="DocsPage.cmd('fontName', this.value)">
                ${DOC_FONTS.map(f => `<option value="${f}" ${t.font_family === f ? 'selected' : ''}>${f}</option>`).join('')}
              </select>
              <select class="doc-tb-sel" title="Size" onchange="DocsPage.cmd('fontSize', this.value)">
                ${DOC_SIZES.map(([k, l]) => `<option value="${k}" ${k === '3' ? 'selected' : ''}>${l}</option>`).join('')}
              </select>
              <input type="color" class="doc-tb-color" title="Text colour" value="#111111" oninput="DocsPage.cmd('foreColor', this.value)">
              <span class="doc-tb-div"></span>
              ${btn('bold', '<b>B</b>', 'Bold')}${btn('italic', '<i>I</i>', 'Italic')}${btn('underline', '<u>U</u>', 'Underline')}${btn('strikeThrough', '<s>S</s>', 'Strikethrough')}
              <span class="doc-tb-div"></span>
              ${btn('justifyLeft', this._svg('alignLeft'), 'Align left')}${btn('justifyCenter', this._svg('alignCenter'), 'Centre')}${btn('justifyRight', this._svg('alignRight'), 'Align right')}${btn('justifyFull', this._svg('alignJustify'), 'Justify')}
              <span class="doc-tb-div"></span>
              <button type="button" class="doc-tb" title="Insert image" onclick="DocsPage.insertImage()">${this._svg('image')}</button>
              <button type="button" class="doc-tb doc-logo-btn" title="Insert company logo" onclick="DocsPage.insertLogo()">Logo</button>
              <button type="button" class="doc-tb" title="Insert table" onclick="DocsPage.insertTable()">${this._svg('table')}</button>
              <button type="button" class="doc-tb" title="Divider" onclick="DocsPage.cmd('insertHorizontalRule')">${this._svg('rule')}</button>
              <button type="button" class="doc-tb" title="Edit HTML" onclick="DocsPage.toggleHtml()">${this._svg('code')}</button>
            </div>
            <div class="doc-canvas-wrap doc-page-${t.page_size || 'a4'}">
              <div class="doc-canvas" id="doc-canvas" contenteditable="true" style="font-family:${t.font_family || 'Sans-Serif'}">${t.body_html || ''}</div>
              <textarea class="doc-html" id="doc-html" style="display:none">${(t.body_html || '').replace(/</g, '&lt;')}</textarea>
            </div>
          </div>
          <div class="doc-vars">
            <div class="doc-vars-title">Variables</div>
            ${groupsHtml}
          </div>
        </div>` : `<div class="card" style="padding:30px;text-align:center;color:var(--tx-4)">No templates yet — create one with <b>+ New document</b>.</div>`}
      </div>`);
  },

  _toggleGroup(gi) {
    const el = document.getElementById('doc-vg-' + gi);
    if (el) el.style.display = el.style.display === 'none' ? '' : 'none';
  },
  pick(id) { this._current = id; this._render(); },

  // execCommand-based rich editing on the contenteditable canvas.
  cmd(command, value) {
    const c = document.getElementById('doc-canvas');
    if (c) c.focus();
    try { document.execCommand(command, false, value); } catch (e) {}
  },
  insertVar(v) {
    const c = document.getElementById('doc-canvas');
    if (!c) return;
    c.focus();
    let ok = false;
    try { ok = document.execCommand('insertText', false, v); } catch (e) {}
    if (!ok) c.innerHTML += v;   // fallback (no live selection, e.g. tests)
  },
  async insertImage() {
    const url = await Modal.prompt('Image URL');
    if (url) this.cmd('insertImage', url);
  },
  insertLogo() {
    const logo = this._company && this._company.logo_data;
    if (!logo) return Toast.error('Upload a company logo in Settings → General first');
    const c = document.getElementById('doc-canvas');
    if (c) { c.focus(); try { document.execCommand('insertHTML', false, `<img src="${logo}" style="max-height:80px">`); } catch (e) { c.innerHTML += `<img src="${logo}" style="max-height:80px">`; } }
  },
  insertTable() {
    const c = document.getElementById('doc-canvas');
    const html = `<table style="width:100%;border-collapse:collapse" border="1" cellpadding="6"><tbody><tr><td>&nbsp;</td><td>&nbsp;</td></tr><tr><td>&nbsp;</td><td>&nbsp;</td></tr></tbody></table>`;
    if (c) { c.focus(); try { document.execCommand('insertHTML', false, html); } catch (e) { c.innerHTML += html; } }
  },
  toggleHtml() {
    const c = document.getElementById('doc-canvas'), h = document.getElementById('doc-html');
    if (!c || !h) return;
    if (h.style.display === 'none') { h.value = c.innerHTML; h.style.display = ''; c.style.display = 'none'; }
    else { c.innerHTML = h.value; h.style.display = 'none'; c.style.display = ''; }
  },
  _bodyHtml() {
    const h = document.getElementById('doc-html');
    if (h && h.style.display !== 'none') return h.value;   // HTML mode is the source of truth
    const c = document.getElementById('doc-canvas');
    return c ? c.innerHTML : '';
  },

  newDoc() {
    this._openSettings(null);
  },
  rename() {
    const t = this._items.find(x => x.id === this._current);
    if (t) this._openSettings(t);
  },
  _openSettings(t) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${t ? 'Document settings' : 'New document'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Document title *</label><input class="field-input" id="ds-name" value="${(t?.name || '').replace(/"/g, '&quot;')}" placeholder="Estimate"></div>
        <div class="field"><label class="field-label">Document assignment</label>
          <select class="field-select" id="ds-type">${DOC_ASSIGN.map(([k, l]) => `<option value="${k}" ${((t?.doc_type) || 'work_order') === k ? 'selected' : ''}>${l}</option>`).join('')}</select>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Page size</label>
            <select class="field-select" id="ds-page">${[['a4', 'A4'], ['letter', 'Letter'], ['label', 'Label']].map(([k, l]) => `<option value="${k}" ${((t?.page_size) || 'a4') === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Default font</label>
            <select class="field-select" id="ds-font">${DOC_FONTS.map(f => `<option value="${f}" ${((t?.font_family) || 'Sans-Serif') === f ? 'selected' : ''}>${f}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label class="field-label">Auto-print on</label>
          <select class="field-select" id="ds-auto"><option value="">Never</option>
            ${[['create', 'Order created'], ['close', 'Order closed']].map(([k, l]) => `<option value="${k}" ${t?.auto_print_on === k ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
        <label class="cash-check"><input type="checkbox" id="ds-default" ${t?.is_default ? 'checked' : ''}> Default template for this assignment</label>
        ${this._statuses.length ? `
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 6px">Allow printing in statuses</div>
        <div style="font-size:11.5px;color:var(--tx-4);margin-bottom:8px">Tick the statuses this document may be printed in. All ticked = printable in any status.</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 14px;max-height:220px;overflow:auto">
          ${this._statuses.map(s => {
            const on = !t || !(t.print_statuses || []).length || (t.print_statuses || []).includes(s.key);
            return `<label class="cash-check" style="margin:0"><input type="checkbox" data-pstatus="${s.key}" ${on ? 'checked' : ''}> ${s.name}</label>`;
          }).join('')}
        </div>` : ''}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="DocsPage._saveSettings(${t ? `'${t.id}'` : 'null'})">${t ? 'Save' : 'Create'}</button>
      </div>`);
  },
  async _saveSettings(id) {
    const name = document.getElementById('ds-name').value.trim();
    if (!name) return Toast.error('Document title is required');
    // Collect ticked statuses; all ticked (or none) == "any status" == send [].
    const boxes = [...document.querySelectorAll('[data-pstatus]')];
    const checked = boxes.filter(b => b.checked).map(b => b.dataset.pstatus);
    const print_statuses = (boxes.length && checked.length < boxes.length) ? checked : [];
    const body = {
      name, doc_type: document.getElementById('ds-type').value,
      page_size: document.getElementById('ds-page').value,
      font_family: document.getElementById('ds-font').value,
      auto_print_on: document.getElementById('ds-auto').value || null,
      is_default: document.getElementById('ds-default').checked,
      print_statuses,
      body_html: id ? (this._items.find(x => x.id === id)?.body_html || '') : '',
    };
    try {
      if (id) { await put(`/api/documents/templates/${id}`, body); }
      else { const r = await post('/api/documents/templates', body); this._current = r.id; }
      Toast.success('Saved'); Modal.close(); await this.load();
    } catch (e) { Toast.error(e.message); }
  },

  async save() {
    const t = this._items.find(x => x.id === this._current);
    if (!t) return;
    const body = {
      name: t.name, doc_type: t.doc_type, is_default: t.is_default, auto_print_on: t.auto_print_on,
      page_size: document.getElementById('doc-pagesize')?.value || t.page_size,
      font_family: t.font_family, print_statuses: t.print_statuses || [],
      body_html: this._bodyHtml(),
    };
    try { await put(`/api/documents/templates/${t.id}`, body); Toast.success('Template saved'); await this.load(); }
    catch (e) { Toast.error(e.message); }
  },
  async del(id) {
    if (!await confirm2('Delete this template?')) return;
    try { await del(`/api/documents/templates/${id}`); Toast.success('Deleted'); this._current = null; await this.load(); }
    catch (e) { Toast.error(e.message); }
  },
  printPreview() {
    const t = this._items.find(x => x.id === this._current);
    if (!t) return;
    const w = window.open('', '_blank');
    if (w) { w.document.write(`<html><head><title>${t.name}</title></head><body style="font-family:${t.font_family}">${this._bodyHtml()}</body></html>`); w.document.close(); w.print(); }
  },
};
/* Build 144v — unified Document templates: every printable — work orders,
   estimates, invoices, AND labels / price tags — is a single DocumentTemplate,
   edited with one rich editor (Orderry-style). The old separate Labels tab and
   its mm-based editor are retired; label sheets print via DocsPage → LabelPrint. */
const DocumentsHubPage = {
  async load() {
    setTopbarActions('');
    setContent(`<div id="dochub-body"><div class="card" style="padding:20px;color:var(--tx-4)">Loading…</div></div>`);
    await DocsPage.mountInto('dochub-body');
  },
};
Router.register('documents', () => DocumentsHubPage.load());


/* ════════════════════════════════════════════════════
   Phase G — Location resources settings (bays/rooms/equipment)
════════════════════════════════════════════════════ */
const ResourcesPage = {
  _items: [],
  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="ResourcesPage.editModal()">+ Add resource</button>`);
    setContent(`<div id="res-wrap"><div class="card" style="padding:30px;text-align:center;color:var(--tx-4)">Loading…</div></div>`);
    await this.reload();
  },
  async reload() {
    try {
      this._items = (await get('/api/resources')).items;
      const w = document.getElementById('res-wrap');
      w.innerHTML = `<div class="card" style="padding:0">
        <div style="padding:16px 18px;font-size:12.5px;color:var(--tx-3)">Resources are schedulable workplaces (bays, rooms, equipment). They appear as rows in the scheduler's <b>By resource</b> mode.</div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Resource</th><th>Kind</th><th>Color</th><th></th></tr></thead>
          <tbody>${this._items.length ? this._items.map(r=>`<tr>
            <td style="font-weight:800">${r.name}</td>
            <td style="color:var(--tx-3)">${r.kind}</td>
            <td><span style="display:inline-block;width:16px;height:16px;border-radius:5px;background:${r.color};vertical-align:middle"></span></td>
            <td><button class="btn btn-glass btn-sm" onclick="ResourcesPage.editModal('${r.id}')">Edit</button></td>
          </tr>`).join('') : emptyRow(4,'box','No resources','Add bays, rooms or equipment')}</tbody>
        </table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },
  editModal(id=null) {
    const r = id ? this._items.find(x=>x.id===id) : null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${r?'Edit resource':'New resource'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="rs-name" value="${(r?.name||'').replace(/"/g,'&quot;')}" placeholder="Bay 1, Workbench, Room A"></div>
        <div class="field"><label class="field-label">Kind</label>
          <select class="field-select" id="rs-kind">${[['bay','Bay / post'],['room','Room'],['equipment','Equipment'],['other','Other']].map(([k,l])=>`<option value="${k}" ${r?.kind===k?'selected':''}>${l}</option>`).join('')}</select>
        </div>
        <div class="field"><label class="field-label">Color</label><input class="field-input" type="color" id="rs-color" value="${r?.color||'#2cd4c0'}" style="height:42px;padding:4px"></div>
      </div>
      <div class="modal-footer">
        ${r?`<button class="btn btn-danger btn-sm" onclick="ResourcesPage.del('${r.id}')">Delete</button>`:''}
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ResourcesPage.save(${r?`'${r.id}'`:'null'})">${r?'Save':'Create'}</button>
      </div>`, 'modal-narrow');
  },
  async save(id) {
    const body = {
      name: document.getElementById('rs-name').value.trim(),
      kind: document.getElementById('rs-kind').value,
      color: document.getElementById('rs-color').value,
    };
    if (!body.name) return Toast.error('Name is required');
    try {
      if (id) await put(`/api/resources/${id}`, body);
      else await post('/api/resources', body);
      Toast.success('Resource saved'); Modal.close(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },
  async del(id) {
    if (!await confirm2('Delete this resource?')) return;
    try { await del(`/api/resources/${id}`); Toast.success('Deleted'); Modal.close(); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },
};
// Build 144e: Location resources are managed per-location inside
// Settings → Locations (open a location → "Location resources"). The standalone
// settings page is retired to avoid a duplicate; the old route now lands on the
// Locations settings so any deep link or bookmark still works. ResourcesPage is
// kept only so the scheduler and legacy callers don't break.
Router.register('resources', () => {
  if (typeof SettingsPage !== 'undefined') { SettingsPage._sub = 'locations'; Router.go('settings'); }
  else { ResourcesPage.load(); }
});


/* ════════════════════════════════════════════════════
   General — company profile (Settings > General)
════════════════════════════════════════════════════ */
// build 123: ISO 3166-1 alpha-2 code → display name (country picker)
const COUNTRY_LIST = [
  ['IN','India'],['US','United States'],['GB','United Kingdom'],['CA','Canada'],
  ['AU','Australia'],['NZ','New Zealand'],['IE','Ireland'],['AE','United Arab Emirates'],
  ['SA','Saudi Arabia'],['QA','Qatar'],['KW','Kuwait'],['BH','Bahrain'],['OM','Oman'],
  ['SG','Singapore'],['MY','Malaysia'],['ID','Indonesia'],['PH','Philippines'],
  ['TH','Thailand'],['VN','Vietnam'],['JP','Japan'],['KR','South Korea'],['CN','China'],
  ['HK','Hong Kong'],['TW','Taiwan'],['PK','Pakistan'],['BD','Bangladesh'],['LK','Sri Lanka'],
  ['NP','Nepal'],['DE','Germany'],['FR','France'],['ES','Spain'],['IT','Italy'],
  ['PT','Portugal'],['NL','Netherlands'],['BE','Belgium'],['CH','Switzerland'],
  ['AT','Austria'],['SE','Sweden'],['NO','Norway'],['DK','Denmark'],['FI','Finland'],
  ['PL','Poland'],['CZ','Czechia'],['RO','Romania'],['GR','Greece'],['TR','Turkey'],
  ['RU','Russia'],['UA','Ukraine'],['ZA','South Africa'],['NG','Nigeria'],['KE','Kenya'],
  ['EG','Egypt'],['MA','Morocco'],['GH','Ghana'],['BR','Brazil'],['MX','Mexico'],
  ['AR','Argentina'],['CL','Chile'],['CO','Colombia'],['PE','Peru'],
];

const GeneralPage = {
  async load() {
    setTopbarActions('');
    setContent(`<div id="gen-wrap"><div class="card" style="padding:30px;text-align:center;color:var(--tx-4)">Loading…</div></div>`);
    let c;
    try { c = await get('/api/staff/company'); } catch (e) { return Toast.error(e.message); }
    document.getElementById('gen-wrap').innerHTML = `
      <div class="card" style="padding:20px;max-width:720px;margin-bottom:16px">
        <div class="dp-title" style="margin-bottom:4px">Appearance</div>
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:14px">Switch between light and the premium matte dark theme. Your choice is saved on this device.</div>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div style="font-size:13.5px;font-weight:700">Dark mode</div>
          <div class="theme-switch">
            <span style="font-size:12px;color:var(--tx-3)">Light</span>
            <div class="theme-track" id="theme-track" onclick="GeneralPage.toggleTheme()"><div class="theme-knob"></div></div>
            <span style="font-size:12px;color:var(--tx-3)">Dark</span>
          </div>
        </div>
      </div>
      <div class="card" style="padding:24px;max-width:720px">
        <div class="dp-title" style="margin-bottom:4px">Company info</div>
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:20px">These details appear on documents, invoices, and customer notifications.</div>
        <div style="display:flex;gap:22px;align-items:flex-start;flex-wrap:wrap">
          <div style="text-align:center">
            <div id="gen-logo-box" class="gen-logo">${c.logo_data ? `<img src="${c.logo_data}" alt="logo">` : `<span style="color:var(--tx-4);font-size:11px">No logo</span>`}</div>
            <button class="btn btn-glass btn-sm" style="margin-top:8px" onclick="GeneralPage.pickLogo()">${icon('camera', 13)} Take photo / choose</button>
            ${c.logo_data ? `<button class="btn btn-glass btn-sm" style="margin-top:6px" onclick="GeneralPage._clearLogo()">Remove</button>` : ''}
          </div>
          <div style="flex:1;min-width:280px">
            <div class="field"><label class="field-label">Company name</label><input class="field-input" id="g-name" value="${(c.name||'').replace(/"/g,'&quot;')}"></div>
            <div class="field-row">
              <div class="field"><label class="field-label">Email</label><input class="field-input" id="g-email" type="email" value="${c.company_email||''}"></div>
              <div class="field"><label class="field-label">Phone</label><input class="field-input" id="g-phone" value="${c.company_phone||''}"></div>
            </div>
            <div class="field"><label class="field-label">Website</label><input class="field-input" id="g-website" value="${c.website||''}" placeholder="https://"></div>
          </div>
        </div>
        <div class="field"><label class="field-label">Address</label><textarea class="field-textarea" id="g-address">${c.company_address||''}</textarea></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Currency symbol</label><input class="field-input" id="g-currency" value="${c.currency||'₹'}" maxlength="4"></div>
          <div class="field"><label class="field-label">Currency code</label><input class="field-input" id="g-ccode" value="${c.currency_code||'INR'}" maxlength="6"></div>
        </div>
        <div class="field-row-3">
          <div class="field"><label class="field-label">Country</label>
            <select class="field-select" id="g-country" onchange="GeneralPage.countryChanged()">
              <option value="">— Select country —</option>
              ${COUNTRY_LIST.map(([k,l])=>`<option value="${k}" ${c.country===k?'selected':''}>${l}</option>`).join('')}
            </select>
          </div>
          <div class="field"><label class="field-label">Timezone</label>
            <select class="field-select" id="g-tz">
              ${['Asia/Kolkata','Asia/Dubai','Europe/London','America/New_York','America/Los_Angeles','Asia/Singapore','Australia/Sydney','Europe/Berlin','America/Chicago','Asia/Tokyo','Pacific/Auckland'].map(t=>`<option ${c.timezone===t?'selected':''}>${t}</option>`).join('')}
            </select>
          </div>
          <div class="field"><label class="field-label">Document language <span style="font-weight:600;color:var(--tx-4)">— printed docs</span></label>
            <select class="field-select" id="g-doclang">
              ${[['en','English'],['hi','हिन्दी (Hindi)'],['es','Español'],['fr','Français'],['de','Deutsch'],['pt','Português'],['ar','العربية (Arabic)'],['ru','Русский'],['zh','中文 (Chinese)'],['ja','日本語 (Japanese)']].map(([k,l])=>`<option value="${k}" ${(c.document_language||'en')===k?'selected':''}>${l}</option>`).join('')}
            </select>
          </div>
        </div>

        <div class="set-section" style="margin:8px 0 16px">
          <div class="ss-head">${icon('coin',16)} Additional currencies</div>
          <div class="ss-desc">Accept and quote in more than one currency. The rate is how many units of the currency equal one of your base currency (${c.currency_code||'INR'}).</div>
          <div id="g-currencies"></div>
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:8px;margin-top:10px;align-items:end">
            <div class="field" style="margin:0"><label class="field-label">Code</label><input class="field-input" id="cur-code" placeholder="USD" maxlength="6"></div>
            <div class="field" style="margin:0"><label class="field-label">Symbol</label><input class="field-input" id="cur-sym" placeholder="$" maxlength="4"></div>
            <div class="field" style="margin:0"><label class="field-label">Rate (per 1 ${c.currency_code||'INR'})</label><input class="field-input" id="cur-rate" type="number" step="any" placeholder="0.012"></div>
            <button class="btn btn-glass btn-sm" onclick="GeneralPage.addCurrency()">+ Add</button>
          </div>
        </div>
          <div class="field"><label class="field-label">Industry template <span style="font-weight:600;color:var(--tx-4)">— renames assets app-wide (Vehicles / Vessels / Devices…)</span></label>
            <select class="field-select" id="g-ind">
              ${[['generic','Generic — Assets'],['auto_repair','Auto repair — Vehicles (VIN)'],['powersports','Powersports — Vehicles (VIN)'],['marine','Marine — Vessels (HIN)'],['device_repair','Phone / computer repair — Devices (IMEI/Serial)'],['small_engine','Small engine — Equipment (Serial)'],['tool_repair','Tool repair — Tools (Serial)'],['appliance_repair','Appliance repair — Appliances (Serial)']].map(([k,l])=>`<option value="${k}" ${c.industry_template===k?'selected':''}>${l}</option>`).join('')}
            </select>
          </div>
        <div class="field"><label class="field-label">Company details (registration / tax ID)</label><textarea class="field-textarea" id="g-details" placeholder="GSTIN, registration no…">${c.company_details||''}</textarea></div>
        <div class="field"><label class="field-label">Company note (printed on documents)</label><textarea class="field-textarea" id="g-note">${c.company_note||''}</textarea></div>

        <div class="set-section" style="margin:8px 0 16px">
          <div class="ss-head">${icon('bolt',16)} Time clocks</div>
          <div class="ss-desc">Turn each clock on or off for the whole company. Individual employees can still be opted out under Settings → Employees; a clock counts toward payroll only when it's on here <b>and</b> enabled for that employee.</div>
          <label class="cash-check" style="margin-top:8px"><input type="checkbox" id="g-clock-wo" ${c.clock_wo_enabled!==false?'checked':''}> Work-order / job &amp; task timer <span style="color:var(--tx-4);font-weight:600">— time spent on orders and tasks</span></label>
          <label class="cash-check" style="margin-top:6px"><input type="checkbox" id="g-clock-att" ${c.clock_attendance_enabled!==false?'checked':''}> Shop attendance clock <span style="color:var(--tx-4);font-weight:600">— clock in / out of the workplace</span></label>
          <label class="cash-check" style="margin-top:6px"><input type="checkbox" id="g-clock-bill" ${c.clock_labour_billing===true?'checked':''}> <b>Bill labour from the job timer</b> <span style="color:var(--tx-4);font-weight:600">— optional: time clocked on a work order adds a labour line priced by tracked time × the employee's rate</span></label>
        </div>

        <div class="set-section" style="margin:8px 0 16px">
          <div class="ss-head">${icon('bolt',16)} Security</div>
          <div class="ss-desc">Two-factor authentication (2FA) adds a one-time code from an authenticator app on top of the password. Recommended for every team.</div>
          <label class="cash-check" style="margin-top:8px"><input type="checkbox" id="g-require-mfa" ${c.require_mfa!==false?'checked':''}> <b>Require 2FA for everyone</b> <span style="color:var(--tx-4);font-weight:600">— members must set up an authenticator before using the app, and can't turn their own 2FA off</span></label>
        </div>

        <div class="set-section" style="margin:8px 0 16px">
          <div class="ss-head">${icon('coin',16)} Contact balances</div>
          <div class="ss-desc">Rebuild every client and supplier balance from your orders, payments, postings and returns. Use this once after upgrading, or any time a balance looks off — it's safe to run repeatedly.</div>
          <button class="btn btn-glass btn-sm" onclick="GeneralPage.rebuildLedger(this)">Rebuild balances from history</button>
        </div>

        <div style="display:flex;justify-content:flex-end;margin-top:8px"><button class="btn btn-primary" onclick="GeneralPage.save()">Save company info</button></div>
      </div>`;
    this._logoData = c.logo_data || null;
    this._currencies = (c.currencies || []).slice();
    this._renderCurrencies();
  },

  _renderCurrencies() {
    const box = document.getElementById('g-currencies');
    if (!box) return;
    box.innerHTML = this._currencies.length
      ? this._currencies.map((cur, i) => `<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border:1px solid var(--g-border);border-radius:var(--r-sm);margin-bottom:6px">
          <b style="min-width:44px">${cur.code}</b>
          <span style="color:var(--tx-3)">${cur.symbol||''}</span>
          <span style="color:var(--tx-3);flex:1">1 base = ${cur.rate} ${cur.code}</span>
          <button class="btn btn-glass btn-sm" onclick="GeneralPage.removeCurrency(${i})">Remove</button>
        </div>`).join('')
      : '<div style="color:var(--tx-4);font-size:13px;padding:4px 0">No additional currencies. Add one below.</div>';
  },

  // Build 144da — choosing a country auto-fills base currency (code + symbol)
  // and timezone from the shared Geo table. All three stay editable afterward.
  countryChanged() {
    const sel = document.getElementById('g-country');
    const meta = (typeof Geo !== 'undefined') ? Geo.meta(sel && sel.value) : null;
    if (!meta) return;
    const code = document.getElementById('g-ccode');
    const sym = document.getElementById('g-currency');
    const tz = document.getElementById('g-tz');
    if (code) code.value = meta.currency_code;
    if (sym) sym.value = meta.symbol;
    if (tz) {
      if (![...tz.options].some(o => o.value === meta.timezone)) {
        tz.add(new Option(meta.timezone, meta.timezone));
      }
      tz.value = meta.timezone;
    }
    if (typeof Toast !== 'undefined') Toast.info(`Set currency to ${meta.currency_code} and timezone to ${meta.timezone}`);
  },

  addCurrency() {
    const code = (document.getElementById('cur-code').value || '').trim().toUpperCase();
    const symbol = (document.getElementById('cur-sym').value || '').trim();
    const rate = parseFloat(document.getElementById('cur-rate').value);
    if (!code) return Toast.error('Enter a currency code');
    if (!rate || rate <= 0) return Toast.error('Enter a valid rate');
    if (this._currencies.some(c => c.code === code)) return Toast.error(code + ' already added');
    this._currencies.push({ code, symbol, rate });
    document.getElementById('cur-code').value = '';
    document.getElementById('cur-sym').value = '';
    document.getElementById('cur-rate').value = '';
    this._renderCurrencies();
  },

  removeCurrency(i) {
    this._currencies.splice(i, 1);
    this._renderCurrencies();
  },
  _logoData: null,
  async rebuildLedger(btn) {
    if (!await confirm2('Rebuild all contact balances from your orders, payments and stock documents? This is safe to run more than once.')) return;
    const prev = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = 'Rebuilding…'; }
    try {
      const r = await post('/api/staff/ledger/rebuild', {});
      Toast.success(`Balances rebuilt — ${r.contacts_rebalanced} contact(s), ${r.orders_charged} charge(s), ${r.payments_posted} payment(s)`);
    } catch (e) { Toast.error(e.message || 'Rebuild failed'); }
    finally { if (btn) { btn.disabled = false; btn.textContent = prev; } }
  },
  toggleTheme() { Theme.toggle(); },
  // Build 144aq — company logo via the shared picker (take photo or choose file).
  pickLogo() {
    PhotoInput.capture({
      title: 'Company logo', max: 600,
      onPhoto: (dataUrl) => { this._logoData = dataUrl; const box = document.getElementById('gen-logo-box'); if (box) box.innerHTML = `<img src="${dataUrl}" alt="logo">`; },
    });
  },
  _clearLogo() { this._logoData = null; document.getElementById('gen-logo-box').innerHTML = '<span style="color:var(--tx-4);font-size:11px">No logo</span>'; },
  async save() {
    const body = {
      name: document.getElementById('g-name').value.trim(),
      company_email: document.getElementById('g-email').value || null,
      company_phone: document.getElementById('g-phone').value || null,
      website: document.getElementById('g-website').value || null,
      company_address: document.getElementById('g-address').value || null,
      country: document.getElementById('g-country').value || null,
      company_details: document.getElementById('g-details').value || null,
      company_note: document.getElementById('g-note').value || null,
      currency: document.getElementById('g-currency').value || '₹',
      currency_code: document.getElementById('g-ccode').value || 'INR',
      timezone: document.getElementById('g-tz').value,
      document_language: document.getElementById('g-doclang').value,
      currencies: this._currencies || [],
      industry_template: document.getElementById('g-ind').value,
      logo_data: this._logoData,
      clock_wo_enabled: document.getElementById('g-clock-wo').checked,
      clock_attendance_enabled: document.getElementById('g-clock-att').checked,
      clock_labour_billing: document.getElementById('g-clock-bill').checked,
      require_mfa: document.getElementById('g-require-mfa').checked,
    };
    if (!body.name) return Toast.error('Company name is required');
    try {
      await put('/api/staff/company', body);
      // Build 125: reflect a currency change immediately across the app (no reload needed).
      fmt.setBase(body.currency);
      if (typeof App !== 'undefined' && App.me) App.me.currency = body.currency;
      // Build 144ah: refresh the cached profile so the clock master switches take effect
      // right away — the time-clock widget hides/shows without a page reload.
      // (Top-level `const App`/`TimeClock` aren't on window, so guard with typeof.)
      try { if (typeof App !== 'undefined' && App.refreshPerms) await App.refreshPerms(); } catch (e) {}
      try { if (typeof TimeClock !== 'undefined' && TimeClock.refresh) await TimeClock.refresh(); } catch (e) {}
      Toast.success('Company info saved');
    } catch (e) { Toast.error(e.message); }
  },
};
Router.register('general', () => GeneralPage.load());
