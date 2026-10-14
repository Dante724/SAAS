/* Settings → Industry template.
   Pick the shop's industry (adapts labels, work-order wording, asset fields and
   scanning), preview what changes, and curate the per-template asset directories
   (groups → brands → models) that never mix across industries. */
const IndustryTemplatePage = {
  _data: null,      // {current, describe, templates}
  _active: null,    // org's live template
  _tk: null,        // template currently being previewed / managed
  _desc: null,      // descriptor for _tk
  _grp: null,       // selected group id (null = template-wide)
  _brand: null,     // selected brand id

  async load() {
    setTopbarActions('');
    setContent('<section class="settings-pane"><div id="ind-root">Loading…</div></section>');
    await this.boot();
  },

  async boot() {
    try { this._data = await get('/api/settings/industry-template'); }
    catch (e) { document.getElementById('ind-root').innerHTML = '<div class="empty">Failed to load industry templates.</div>'; return; }
    this._active = this._data.current;
    if (!this._tk) this._tk = this._active;
    await this.render();
  },

  async _descFor(tk) {
    if (tk === this._active) return this._data.describe;
    try { return await get(`/api/settings/industry-template/describe?template_key=${tk}`); }
    catch (e) { return this._data.describe; }
  },

  async render() {
    const root = document.getElementById('ind-root');
    this._desc = await this._descFor(this._tk);
    const d = this._data;
    const isActive = this._tk === this._active;

    root.innerHTML = `
      <h2 style="margin:0 0 4px">Industry template</h2>
      <p class="muted" style="margin:0 0 18px;max-width:640px">Choose your industry to adapt work-order wording, asset fields, scanning and the asset directories. Switching only affects <em>new</em> records — existing work orders and assets keep the template they were created under.</p>

      <div class="ind-picker">
        ${d.templates.map(t => `
          <button class="ind-tpl ${t.key === this._active ? 'live' : ''} ${t.key === this._tk ? 'sel' : ''}" onclick="IndustryTemplatePage.pick('${t.key}')">
            ${t.key === this._active ? '<span class="ind-live-badge">Active</span>' : ''}
            <div class="ind-tpl-name">${t.label}</div>
            <div class="ind-tpl-meta">${t.asset_label} · ${t.group_count} groups</div>
            <div class="ind-tpl-wo">${t.wo_button}</div>
          </button>`).join('')}
      </div>

      <div class="card glass-liquid ind-preview">
        <div class="ind-preview-head">
          <div>
            <div class="ind-eyebrow">Preview</div>
            <div class="ind-preview-title">${this._desc.label}</div>
          </div>
          ${isActive
            ? '<span class="ind-active-pill">✓ Active template</span>'
            : `<button class="btn btn-primary btn-sm" onclick="IndustryTemplatePage.confirmSwitch('${this._tk}')">Switch to this template</button>`}
        </div>
        <div class="ind-preview-grid">
          <div class="ind-facet"><div class="ind-eyebrow">Asset is called</div><div class="ind-facet-val">${this._desc.asset_label}</div></div>
          <div class="ind-facet"><div class="ind-eyebrow">New work order</div><div class="ind-facet-val">${this._desc.wo_button}</div></div>
          <div class="ind-facet"><div class="ind-eyebrow">Scan / identifier</div><div class="ind-facet-val">${(this._desc.identifier.primary || '').toUpperCase()} <span class="muted" style="font-weight:600;font-size:11px">(${this._desc.identifier.allowed.join(', ')})</span></div></div>
        </div>
        <div class="ind-eyebrow" style="margin-top:14px">Asset fields</div>
        <div class="chip-row" style="margin-top:6px">${this._desc.fields.map(f => `<span class="chip ind-chip-static">${f.label}</span>`).join('')}</div>
        <div class="ind-eyebrow" style="margin-top:14px">Asset groups</div>
        <div class="chip-row" style="margin-top:6px">${this._desc.groups.map(g => `<span class="chip ind-chip-static">${g.label}</span>`).join('')}</div>
      </div>

      <div class="ind-mgmt-head">
        <div>
          <h3 style="margin:0">Asset directories <span class="muted" style="font-weight:600;font-size:13px">— ${this._desc.label}</span></h3>
          <p class="muted" style="margin:2px 0 0;font-size:12.5px">Groups, brands and models for this template. Nothing here is shared with other industries.</p>
        </div>
        <button class="btn btn-glass btn-sm" onclick="IndustryTemplatePage.importCsv()">${icon('box',14)} Import CSV</button>
      </div>

      <div class="ind-cols">
        <div class="card ind-col"><div class="ind-col-head">Groups</div><div id="ind-groups"></div>
          <div class="ind-add"><input class="field-input" id="ind-grp-new" placeholder="New group…" onkeydown="if(event.key==='Enter')IndustryTemplatePage.addGroup()"><button class="btn btn-primary btn-sm" onclick="IndustryTemplatePage.addGroup()">Add</button></div>
        </div>
        <div class="card ind-col"><div class="ind-col-head">Brands</div><div id="ind-brands"><div class="ind-hint">Select a group, or add a template-wide brand.</div></div>
          <div class="ind-add"><input class="field-input" id="ind-brand-new" placeholder="New brand…" onkeydown="if(event.key==='Enter')IndustryTemplatePage.addBrand()"><button class="btn btn-primary btn-sm" onclick="IndustryTemplatePage.addBrand()">Add</button></div>
        </div>
        <div class="card ind-col"><div class="ind-col-head">Models</div><div id="ind-models"><div class="ind-hint">Select a brand.</div></div>
          <div class="ind-add"><input class="field-input" id="ind-model-new" placeholder="New model…" onkeydown="if(event.key==='Enter')IndustryTemplatePage.addModel()"><button class="btn btn-primary btn-sm" onclick="IndustryTemplatePage.addModel()">Add</button></div>
        </div>
      </div>`;

    await this._loadGroups();
  },

  pick(tk) { this._tk = tk; this._grp = null; this._brand = null; this.render(); },

  confirmSwitch(tk) {
    const t = this._data.templates.find(x => x.key === tk);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Switch industry template</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="margin:0 0 10px">Switch this shop to <strong>${t.label}</strong>?</p>
        <p class="muted" style="font-size:13px;margin:0">New work orders and assets will use the ${t.label} wording, fields and scanning. <strong>Existing records keep their current template</strong> and still display with their original labels — nothing is migrated or mixed.</p>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="ind-switch-go" onclick="IndustryTemplatePage.doSwitch('${tk}')">Switch template</button>
      </div>`, 'modal-narrow');
  },

  async doSwitch(tk) {
    const btn = document.getElementById('ind-switch-go'); btn.disabled = true; btn.textContent = 'Switching…';
    try {
      await put('/api/settings/industry-template', { template_key: tk });
      Toast.success('Industry template switched');
      Modal.close();
      this._data = await get('/api/settings/industry-template');
      this._active = this._data.current; this._tk = this._active;
      this.render();
    } catch (e) { Toast.error(e.message || 'Failed'); btn.disabled = false; btn.textContent = 'Switch template'; }
  },

  // ── groups ────────────────────────────────────────────────
  async _loadGroups() {
    const box = document.getElementById('ind-groups'); if (!box) return;
    let items = [];
    try { items = (await get(`/api/settings/asset-groups?template_key=${this._tk}`)).items || []; } catch (e) {}
    this._groups = items;
    box.innerHTML = items.length ? items.map(g => `
      <div class="ind-row ${g.id === this._grp ? 'on' : ''}" onclick="IndustryTemplatePage.selGroup('${g.id}')">
        <span>${g.label}${g.is_system ? ' <span class="ind-sys">system</span>' : ''}</span>
        ${g.is_system ? '' : `<button class="ind-x" onclick="event.stopPropagation();IndustryTemplatePage.delGroup('${g.id}')">×</button>`}
      </div>`).join('') : '<div class="ind-hint">No groups yet.</div>';
    await this._loadBrands();
  },

  async selGroup(id) { this._grp = (this._grp === id ? null : id); this._brand = null; await this._loadGroups(); },

  async addGroup() {
    const inp = document.getElementById('ind-grp-new'); const label = inp.value.trim(); if (!label) return;
    const key = label.toLowerCase().replace(/[^a-z0-9]+/g, '_');
    try { await post('/api/settings/asset-groups', { template_key: this._tk, key, label }); inp.value = ''; await this._loadGroups(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },
  async delGroup(id) {
    if (!(await confirm2('Delete this group?'))) return;
    try { await del(`/api/settings/asset-groups/${id}`); if (this._grp === id) this._grp = null; await this._loadGroups(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },

  // ── brands ────────────────────────────────────────────────
  async _loadBrands() {
    const box = document.getElementById('ind-brands'); if (!box) return;
    let url = `/api/settings/asset-brands?template_key=${this._tk}`;
    if (this._grp) url += `&asset_group_id=${this._grp}`;
    let items = [];
    try { items = (await get(url)).items || []; } catch (e) {}
    this._brands = items;
    box.innerHTML = items.length ? items.map(b => `
      <div class="ind-row ${b.id === this._brand ? 'on' : ''}" onclick="IndustryTemplatePage.selBrand('${b.id}')">
        <span>${b.name}${b.is_system ? ' <span class="ind-sys">system</span>' : ''}</span>
        <button class="ind-x" onclick="event.stopPropagation();IndustryTemplatePage.delBrand('${b.id}')">×</button>
      </div>`).join('') : `<div class="ind-hint">${this._grp ? 'No brands in this group.' : 'No brands yet.'}</div>`;
    await this._loadModels();
  },

  async selBrand(id) { this._brand = (this._brand === id ? null : id); await this._loadBrands(); },

  async addBrand() {
    const inp = document.getElementById('ind-brand-new'); const name = inp.value.trim(); if (!name) return;
    const body = { template_key: this._tk, name };
    if (this._grp) body.asset_group_id = this._grp;
    try { await post('/api/settings/asset-brands', body); inp.value = ''; await this._loadBrands(); }
    catch (e) { Toast.error(e.message || 'Brand already exists'); }
  },
  async delBrand(id) {
    if (!(await confirm2('Delete this brand and its models?'))) return;
    try { await del(`/api/settings/asset-brands/${id}`); if (this._brand === id) this._brand = null; await this._loadBrands(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },

  // ── models ────────────────────────────────────────────────
  async _loadModels() {
    const box = document.getElementById('ind-models'); if (!box) return;
    if (!this._brand) { box.innerHTML = '<div class="ind-hint">Select a brand.</div>'; return; }
    let items = [];
    try { items = (await get(`/api/settings/asset-models?template_key=${this._tk}&brand_id=${this._brand}`)).items || []; } catch (e) {}
    box.innerHTML = items.length ? items.map(m => `
      <div class="ind-row">
        <span>${m.name}${m.is_system ? ' <span class="ind-sys">system</span>' : ''}</span>
        <button class="ind-x" onclick="IndustryTemplatePage.delModel('${m.id}')">×</button>
      </div>`).join('') : '<div class="ind-hint">No models yet.</div>';
  },

  async addModel() {
    if (!this._brand) return Toast.error('Select a brand first');
    const inp = document.getElementById('ind-model-new'); const name = inp.value.trim(); if (!name) return;
    try { await post('/api/settings/asset-models', { template_key: this._tk, brand_id: this._brand, name }); inp.value = ''; await this._loadModels(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },
  async delModel(id) {
    try { await del(`/api/settings/asset-models/${id}`); await this._loadModels(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },

  // ── CSV import ────────────────────────────────────────────
  importCsv() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Import brands & models</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p class="muted" style="font-size:13px;margin:0 0 10px">Paste CSV with a <code>brand</code> column, and optionally <code>model</code> and <code>group</code>. Imported into <strong>${this._desc.label}</strong> only.</p>
        <textarea class="field-textarea" id="ind-csv" rows="7" placeholder="brand,model,group&#10;Apple,iPhone 15,phone&#10;Samsung,Galaxy S24,phone"></textarea>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="ind-csv-go" onclick="IndustryTemplatePage.doImport()">Import</button>
      </div>`, 'modal-narrow');
  },
  async doImport() {
    const csv = document.getElementById('ind-csv').value.trim();
    if (!csv) return Toast.error('Paste some CSV first');
    const btn = document.getElementById('ind-csv-go'); btn.disabled = true; btn.textContent = 'Importing…';
    try {
      const r = await post('/api/settings/asset-directory/import', { template_key: this._tk, csv });
      Toast.success(`Imported ${r.brands} brand(s), ${r.models} model(s)`);
      Modal.close(); await this._loadGroups();
    } catch (e) { Toast.error(e.message || 'Import failed'); btn.disabled = false; btn.textContent = 'Import'; }
  },
};
