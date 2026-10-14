/* ════════════════════════════════════════════════════
   CategoryTree — Build 144ax. Premium collapsible category rail
   (Orderry "Idea 2"): pinned "All …" root with a live count,
   chevron-collapsible parents, per-node counts, + Category / + sub,
   double-click to rename, drag-to-reparent. Shared by Products &
   Bundles via a single mounted instance.
   Usage:
     CategoryTree.mount({ el:'pr-tree', entity:'product',
       activeId:'', onSelect(id){…}, onChange(){…} });
════════════════════════════════════════════════════ */
const CategoryTree = {
  _o: null, _entity: 'product', _active: '', _tree: [], _collapsed: new Set(), _dragId: null,

  mount(opts = {}) {
    this._o = opts;
    this._entity = opts.entity || 'product';
    this._active = opts.activeId || '';
    this._collapsed = this._loadCollapsed();
    return this._fetch();
  },

  _key() { return 'ros_cattree_' + this._entity; },
  _loadCollapsed() { try { return new Set(JSON.parse(localStorage.getItem(this._key()) || '[]')); } catch (e) { return new Set(); } },
  _saveCollapsed() { try { localStorage.setItem(this._key(), JSON.stringify([...this._collapsed])); } catch (e) {} },
  _el() { const e = this._o && this._o.el; return typeof e === 'string' ? document.getElementById(e) : e; },

  async _fetch() {
    const tpl = this._o.templateKey ? '&template_key=' + encodeURIComponent(this._o.templateKey) : '';
    try { this._tree = (await get(`/api/catalog/categories?entity_type=${this._entity}${tpl}`)).tree || []; }
    catch (e) { this._tree = []; }
    this.render();
  },

  render() {
    const el = this._el(); if (!el) return;
    const label = this._entity === 'bundle' ? 'All bundles' : this._entity === 'service' ? 'All services' : 'All products';
    const sum = (this._tree || []).reduce((a, n) => a + (n.count || 0), 0);
    el.innerHTML =
      `<div class="ct-hd"><span>Categories</span><span class="ct-mng" onclick="CategoryTree.manage()">Manage</span></div>` +
      `<div class="ct-body">` +
      `<button class="ct-add" onclick="CategoryTree.add()">${icon('plus', 13)} Category</button>` +
      `<div class="ct-all ${this._active === '' ? 'on' : ''}" onclick="CategoryTree.select('')">` +
        `<span class="ct-all-ic">${icon('grid', 14)}</span><span class="ct-all-t">${label}</span><span class="ct-all-ct">${sum}</span></div>` +
      (this._tree || []).map(n => this._node(n, 0)).join('') +
      `</div>`;
  },

  _node(n, d) {
    const kids = n.children || [];
    const has = kids.length > 0;
    const col = this._collapsed.has(n.id);
    const chev = has
      ? `<span class="ct-chev ${col ? '' : 'open'}" onclick="event.stopPropagation();CategoryTree.toggle('${n.id}')"></span>`
      : `<span class="ct-chsp"></span>`;
    // 144bc security: never inline a category name (user-controlled) into HTML/handlers.
    // Escape it for display, and rename() looks the name up by id instead of taking it as an arg.
    let h = `<div class="ct-node ${this._active === n.id ? 'on' : ''}" draggable="true"` +
      ` style="padding-left:${8 + d * 15}px"` +
      ` onclick="CategoryTree.select('${n.id}')" ondblclick="CategoryTree.rename('${n.id}')"` +
      ` ondragstart="CategoryTree._ds(event,'${n.id}')" ondragover="CategoryTree._dover(event)"` +
      ` ondragleave="CategoryTree._dleave(event)" ondrop="CategoryTree._drop(event,'${n.id}')" ondragend="CategoryTree._dend(event)">` +
      chev + `<span class="ct-nm">${this._esc(n.name)}</span><span class="ct-count">${n.count}</span>` +
      `<button class="ct-sub" title="Add sub-category" onclick="event.stopPropagation();CategoryTree.addSub('${n.id}')">${icon('plus', 11)}</button>` +
      `</div>`;
    if (has && !col) h += kids.map(k => this._node(k, d + 1)).join('');
    return h;
  },

  toggle(id) { this._collapsed.has(id) ? this._collapsed.delete(id) : this._collapsed.add(id); this._saveCollapsed(); this.render(); },
  select(id) { this._active = id; this.render(); if (this._o.onSelect) this._o.onSelect(id); },

  add() { CategoryManager.addPanel({ entity: this._entity, templateKey: this._o.templateKey, onDone: (c) => this._after(c) }); },
  addSub(pid) { this._collapsed.delete(pid); CategoryManager.addPanel({ entity: this._entity, templateKey: this._o.templateKey, parentId: pid, onDone: (c) => this._after(c) }); },
  manage() { CategoryManager.open({ entity: this._entity, templateKey: this._o.templateKey, onChange: () => this._after() }); },
  _esc(s) { const d = document.createElement('div'); d.textContent = (s == null ? '' : String(s)); return d.innerHTML; },
  _find(nodes, id) { for (const n of (nodes || [])) { if (n.id === id) return n; const f = this._find(n.children, id); if (f) return f; } return null; },
  async rename(id) {
    const node = this._find(this._tree, id);
    const name = await Modal.prompt('Rename category', {default: node ? node.name : ''});
    if (name === null || !name.trim()) return;
    try { await patch(`/api/catalog/categories/${id}`, { name: name.trim() }); Toast.success('Renamed'); this._after(); }
    catch (e) { Toast.error(e.message); }
  },
  async _after(cat) { if (cat && cat.parent_id) this._collapsed.delete(cat.parent_id); await this._fetch(); if (this._o.onChange) this._o.onChange(); },

  /* ── drag to reparent ── */
  _ds(e, id) { this._dragId = id; e.dataTransfer.effectAllowed = 'move'; },
  _dover(e) { e.preventDefault(); e.currentTarget.classList.add('ct-drop'); },
  _dleave(e) { e.currentTarget.classList.remove('ct-drop'); },
  _dend() { document.querySelectorAll('.ct-node.ct-drop').forEach(n => n.classList.remove('ct-drop')); this._dragId = null; },
  async _drop(e, targetId) {
    e.preventDefault(); e.stopPropagation();
    e.currentTarget.classList.remove('ct-drop');
    const from = this._dragId; this._dragId = null;
    if (!from || from === targetId) return;
    try { await patch(`/api/catalog/categories/${from}`, { parent_id: targetId }); Toast.success('Moved'); this._collapsed.delete(targetId); this._after(); }
    catch (e2) { Toast.error(e2.message); }
  },
};
window.CategoryTree = CategoryTree;
