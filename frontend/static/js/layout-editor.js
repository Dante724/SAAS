/* ════════════════════════════════════════════════════════════════
   Build 144cn — "Edit layout": free-anywhere drag editor in the real app.

   Enter edit mode from the floating button; then hold & drag any piece of
   chrome — sidebar nav items, the user pod, topbar buttons, and every page
   action button — to ANY x/y. Offsets persist per-user (localStorage +
   /api/prefs/layout) and re-apply after every page render, so a customised
   layout survives navigation and reloads. Reset restores defaults.

   Keying: elements are identified by a stable key (nav→data-page, user pod,
   or the button's own text within its bar), stored on data-lx so lookups stay
   stable across re-renders.
════════════════════════════════════════════════════════════════ */
window.LayoutEditor = {
  KEY: 'ros_layout_v1',
  editing: false,
  offsets: {},
  _pulled: false,

  // What can be grabbed in edit mode.
  SEL: '.nav-link, .user-pod, #topbar-actions > *, .app-toolbar > *',

  _key(el) {
    if (el.dataset.lx) return el.dataset.lx;
    let k;
    if (el.classList.contains('nav-link') && el.dataset.page) k = 'nav:' + el.dataset.page;
    else if (el.classList.contains('user-pod')) k = 'userpod';
    else {
      const bar = el.closest('#topbar-actions') ? 'tb' : (el.closest('.app-toolbar') ? 'at' : 'x');
      const txt = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40) || el.className.slice(0, 20);
      k = bar + ':' + txt;
    }
    el.dataset.lx = k;
    return k;
  },

  _load() {
    try { this.offsets = JSON.parse(localStorage.getItem(this.KEY) || '{}'); } catch (e) { this.offsets = {}; }
  },
  _save() {
    try { localStorage.setItem(this.KEY, JSON.stringify(this.offsets)); } catch (e) {}
    try { if (typeof put === 'function' && typeof Auth !== 'undefined' && Auth.token) put('/api/prefs/layout', { value: this.offsets }); } catch (e) {}
  },
  async _pull() {
    if (this._pulled) return; this._pulled = true;
    try {
      const r = await get('/api/prefs/layout');
      const v = r && r.value ? r.value : null;
      if (v && Object.keys(v).length) { this.offsets = v; this._save(); }
    } catch (e) {}
  },

  /** Re-apply saved offsets to whatever is currently on screen. Called after every render. */
  apply(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(this.SEL).forEach(el => {
      const o = this.offsets[this._key(el)];
      if (o) { el.style.transform = `translate(${o.x}px, ${o.y}px)`; el.style.position = el.style.position || 'relative'; el.style.zIndex = el.style.zIndex || 5; }
      else if (el.style.transform && el.dataset.lx) { el.style.transform = ''; }
    });
  },

  toggle() {
    this.editing = !this.editing;
    document.body.classList.toggle('lx-editing', this.editing);
    const btn = document.getElementById('lx-toggle');
    if (btn) btn.classList.toggle('on', this.editing);
    const lbl = document.getElementById('lx-label'); if (lbl) lbl.textContent = this.editing ? 'Done' : 'Edit layout';
    const rb = document.getElementById('lx-reset'); if (rb) rb.style.display = this.editing ? '' : 'none';
    const hint = document.getElementById('lx-hint'); if (hint) hint.style.display = this.editing ? '' : 'none';
    if (typeof Toast !== 'undefined' && this.editing) Toast.info('Hold and drag any button, icon, or nav item anywhere.');
  },
  reset() { this.offsets = {}; this._save(); document.querySelectorAll(this.SEL).forEach(el => { el.style.transform = ''; }); },

  _drag: null,
  _onDown(e) {
    if (!this.editing) return;
    const el = e.target.closest(this.SEL);
    if (!el) return;
    e.preventDefault(); e.stopPropagation();
    const cur = this.offsets[this._key(el)] || { x: 0, y: 0 };
    this._drag = { el, key: this._key(el), sx: e.clientX, sy: e.clientY, bx: cur.x, by: cur.y };
    el.classList.add('lx-drag'); el.style.position = 'relative';
    try { el.setPointerCapture(e.pointerId); } catch (_) {}
  },
  _onMove(e) {
    if (!this._drag) return;
    const dx = this._drag.bx + (e.clientX - this._drag.sx), dy = this._drag.by + (e.clientY - this._drag.sy);
    this._drag.el.style.transform = `translate(${dx}px, ${dy}px)`; this._drag.el.style.zIndex = 70;
  },
  _onUp(e) {
    if (!this._drag) return;
    const dx = this._drag.bx + (e.clientX - this._drag.sx), dy = this._drag.by + (e.clientY - this._drag.sy);
    this.offsets[this._drag.key] = { x: Math.round(dx), y: Math.round(dy) };
    this._drag.el.classList.remove('lx-drag'); this._drag.el.style.zIndex = 5;
    this._save(); this._drag = null;
  },

  boot() {
    // Build 144cp: editor retired. Saved layouts are still loaded and re-applied
    // on every render, so everything the user arranged persists — but the
    // drag/edit interaction and its floating bar are gone (no re-editing).
    this._load();
    this._pull().then(() => this.apply());
    this.apply();
  },
};
