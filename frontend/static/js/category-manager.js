/* ════════════════════════════════════════════════════
   CategoryManager — Build 144ao. Create / rename / delete product
   categories (template-scoped). Shared by Products, Inventory and POS.
════════════════════════════════════════════════════ */
const CategoryManager = {
  _tpl: null, _onChange: null, _tree: [],

  // opts: { entity, template_key, onChange, stack }
  open(opts = {}) {
    this._entity = opts.entity || 'product';
    this._tpl = opts.template_key || (typeof window !== 'undefined' && window.__tpl && window.__tpl.current) || null;
    this._onChange = opts.onChange || null;
    const noun = this._entity === 'bundle' ? 'Bundle' : this._entity === 'service' ? 'Service' : 'Product';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${noun} categories</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:12px;color:var(--tx-3);margin-bottom:12px">Create the categories your products live in. Counts update from Inventory. ${this._tpl ? 'These belong to the current template.' : ''}</div>
        <div style="display:flex;gap:8px;margin-bottom:14px">
          <input class="field-input" id="cm-name" placeholder="New category name" style="flex:1" onkeydown="if(event.key==='Enter')CategoryManager.create()">
          <button class="btn btn-primary" onclick="CategoryManager.create()">${icon('plus', 14)} Add</button>
        </div>
        <div id="cm-list"></div>
      </div>`, 'modal-wide', !!opts.stack);
    this.refresh();
  },

  async refresh() {
    try {
      const d = await get(`/api/catalog/categories?entity_type=${this._entity || 'product'}${this._tpl ? '&template_key=' + encodeURIComponent(this._tpl) : ''}`);
      this._tree = d.tree || [];
    } catch (e) { return Toast.error(e.message); }
    const el = document.getElementById('cm-list'); if (!el) return;
    const flat = [];
    const walk = (nodes, d) => nodes.forEach(n => { flat.push({ ...n, depth: d }); if (n.children?.length) walk(n.children, d + 1); });
    walk(this._tree, 0);
    el.innerHTML = flat.length ? `<div class="cm-rows">${flat.map(c => `
      <div class="cm-row">
        <div class="cm-nm" style="padding-left:${c.depth * 16}px">${icon('box', 15)}<span>${c.name}</span></div>
        <span class="cm-count">${c.count} product${c.count === 1 ? '' : 's'}</span>
        <div class="cm-acts">
          <button class="btn btn-glass btn-sm" title="Rename" onclick="CategoryManager.rename('${c.id}','${(c.name || '').replace(/'/g, "\\'")}')">${icon('wrench', 13)}</button>
          <button class="btn btn-glass btn-sm" title="Delete" onclick="CategoryManager.remove('${c.id}')">${icon('trash', 13)}</button>
        </div>
      </div>`).join('')}</div>`
      : `<div style="text-align:center;color:var(--tx-4);font-size:13px;padding:22px">No categories yet — add your first above.</div>`;
  },

  async create() {
    const inp = document.getElementById('cm-name');
    const name = (inp?.value || '').trim();
    if (!name) return Toast.error('Enter a category name');
    try {
      await post('/api/catalog/categories', { entity_type: this._entity || 'product', name, template_key: this._tpl || null });
      if (inp) inp.value = '';
      Toast.success('Category added');
      await this.refresh();
      if (this._onChange) this._onChange();
    } catch (e) { Toast.error(e.message); }
  },

  async rename(id, cur) {
    const name = await Modal.prompt('Rename category', {default: cur});
    if (name === null) return;
    if (!name.trim()) return Toast.error('Name is required');
    try {
      await patch(`/api/catalog/categories/${id}`, { name: name.trim() });
      Toast.success('Renamed');
      await this.refresh();
      if (this._onChange) this._onChange();
    } catch (e) { Toast.error(e.message); }
  },

  async remove(id) {
    if (!await Modal.confirm('Delete this category? Products must be moved out first.', {danger:true})) return;
    try {
      await del(`/api/catalog/categories/${id}`);
      Toast.success('Category deleted');
      await this.refresh();
      if (this._onChange) this._onChange();
    } catch (e) { Toast.error(e.message); }  // 409 when in use — message is shown
  },

  // quick inline create used by forms + the Products tree; resolves to {id,name} or null.
  async quickCreate(templateKey, parentId) {
    const name = await Modal.prompt(parentId ? 'New sub-category name' : 'New category name');
    if (name === null || !name.trim()) return null;
    try {
      const r = await post('/api/catalog/categories', { entity_type: 'product', name: name.trim(), template_key: templateKey || null, parent_id: parentId || null });
      Toast.success('Category added');
      return { id: r.id, name: r.name };
    } catch (e) { Toast.error(e.message); return null; }
  },

  // Build 144as — Orderry-style "Add category" side panel. Opens a right-side drawer
  // with a title and a placement choice: a new top-level category, or under a parent.
  // opts: { templateKey, parentId (preselected parent), onDone(cat) }
  async addPanel(opts = {}) {
    const tpl = opts.templateKey || (typeof window !== 'undefined' && window.__tpl && window.__tpl.current) || null;
    this._apEntity = opts.entity || 'product';
    // load the tree for the parent picker
    let tree = [];
    try { tree = (await get(`/api/catalog/categories?entity_type=${this._apEntity}${tpl ? '&template_key=' + encodeURIComponent(tpl) : ''}`)).tree || []; } catch (e) {}
    const flat = [];
    const walk = (nodes, d) => nodes.forEach(n => { flat.push({ id: n.id, name: `${'— '.repeat(d)}${n.name}` }); if (n.children?.length) walk(n.children, d + 1); });
    walk(tree, 0);
    const hasParents = flat.length > 0;
    const underDefault = !!opts.parentId && hasParents;
    this._apTpl = tpl; this._apOnDone = opts.onDone || null; this._apPlacement = underDefault ? 'under' : 'top';
    const parentOpts = flat.map(c => `<option value="${c.id}" ${opts.parentId === c.id ? 'selected' : ''}>${c.name}</option>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add category</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Category title *</label>
          <input class="field-input" id="cm-ap-name" placeholder="e.g. Screens" autofocus
                 onkeydown="if(event.key==='Enter')CategoryManager.addPanelSubmit()"></div>
        <div class="field">
          <label class="field-label">Placement</label>
          <div class="seg-pill" id="cm-ap-seg">
            <button type="button" data-place="top" class="${this._apPlacement === 'top' ? 'on' : ''}" onclick="CategoryManager.apPlace('top')">New top-level</button>
            <button type="button" data-place="under" class="${this._apPlacement === 'under' ? 'on' : ''}" ${hasParents ? '' : 'disabled style="opacity:.4;cursor:not-allowed"'} onclick="CategoryManager.apPlace('under')">Under a category</button>
          </div>
        </div>
        <div class="field" id="cm-ap-parent-wrap" style="${this._apPlacement === 'under' ? '' : 'display:none'}">
          <label class="field-label">Parent category *</label>
          <select class="field-select" id="cm-ap-parent">${parentOpts}</select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="CategoryManager.addPanelSubmit()">${icon('plus', 14)} Create</button>
      </div>`, 'modal-side', !!opts.stack);
  },

  apPlace(place) {
    this._apPlacement = place;
    document.querySelectorAll('#cm-ap-seg button').forEach(b => b.classList.toggle('on', b.dataset.place === place));
    const w = document.getElementById('cm-ap-parent-wrap');
    if (w) w.style.display = place === 'under' ? '' : 'none';
  },

  async addPanelSubmit() {
    const name = (document.getElementById('cm-ap-name')?.value || '').trim();
    if (!name) return Toast.error('Enter a category title');
    const parent_id = this._apPlacement === 'under' ? (document.getElementById('cm-ap-parent')?.value || null) : null;
    if (this._apPlacement === 'under' && !parent_id) return Toast.error('Pick a parent category');
    try {
      const r = await post('/api/catalog/categories', { entity_type: this._apEntity || 'product', name, template_key: this._apTpl || null, parent_id });
      Toast.success('Category added');
      Modal.close();
      if (this._apOnDone) this._apOnDone({ id: r.id, name: r.name, parent_id });
    } catch (e) { Toast.error(e.message); }
  },
};
