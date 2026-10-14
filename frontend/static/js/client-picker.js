/* ════════════════════════════════════════════════════
   ClientPicker — live-search a client or create one inline.
   Renders into a container; exposes the chosen client id.
════════════════════════════════════════════════════ */

const ClientPicker = {
  selected: null,        // { id, name, phone }
  containerId: null,
  _debounce: null,

  mount(containerId) {
    this.containerId = containerId;
    this.selected = null;
    const c = document.getElementById(containerId);
    if (!c) return;
    c.innerHTML = `
      <div class="cp-wrap">
        <div class="search-box">
          <span class="search-icon">${icon('search',14)}</span>
          <input class="field-input search-input" id="cp-input-${containerId}"
            placeholder="Search client by name or phone…" autocomplete="off"
            oninput="ClientPicker.onType('${containerId}')"
            onfocus="ClientPicker.onType('${containerId}')">
        </div>
        <div class="cp-dropdown" id="cp-drop-${containerId}"></div>
        <div class="cp-chosen" id="cp-chosen-${containerId}"></div>
      </div>`;
  },

  get value() { return this.selected?.id || null; },

  onType(cid) {
    const input = document.getElementById(`cp-input-${cid}`);
    const q = input.value.trim();
    clearTimeout(this._debounce);
    this._debounce = setTimeout(() => this._search(cid, q), 220);
  },

  async _search(cid, q) {
    const drop = document.getElementById(`cp-drop-${cid}`);
    if (!drop) return;
    let items = [];
    try {
      const data = await get(`/api/clients?limit=8${q ? '&search=' + encodeURIComponent(q) : ''}`);
      items = data.items;
    } catch { /* ignore */ }

    let html = '';
    // existing matches
    html += items.map(c => `
      <div class="cp-opt" onclick="ClientPicker.choose('${cid}','${c.id}','${this._esc(c.name)}','${this._esc(c.phone||'')}')">
        ${avatarHtml(c.name, 26)}
        <div class="cp-opt-info">
          <div class="cp-opt-name">${this._escHtml(c.name)}</div>
          <div class="cp-opt-sub">${c.phone || 'no phone'} · ${c.order_count || 0} orders</div>
        </div>
        ${c.loyalty_tier === 'vip' ? '<span class="cp-tier">VIP</span>' : ''}
      </div>`).join('');

    // create-new option (always shown)
    const label = q ? `Create "${this._escHtml(q)}"` : 'Create new client';
    html += `
      <div class="cp-opt cp-create" onclick="ClientPicker.createInline('${cid}')">
        <div class="cp-plus">+</div>
        <div class="cp-opt-info"><div class="cp-opt-name" style="color:var(--blue-dark)">${label}</div>
        <div class="cp-opt-sub">Add a brand-new client</div></div>
      </div>`;

    drop.innerHTML = html;
    drop.classList.add('open');
  },

  choose(cid, id, name, phone) {
    this.selected = { id, name, phone };
    document.getElementById(`cp-drop-${cid}`).classList.remove('open');
    document.getElementById(`cp-input-${cid}`).value = '';
    this._renderChosen(cid);
  },

  clear(cid) {
    this.selected = null;
    this._renderChosen(cid);
  },

  _renderChosen(cid) {
    const el = document.getElementById(`cp-chosen-${cid}`);
    if (!el) return;
    if (!this.selected) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <div class="cp-pill">
        ${avatarHtml(this.selected.name, 26)}
        <div class="cp-opt-info">
          <div class="cp-opt-name">${this._escHtml(this.selected.name)}</div>
          ${this.selected.phone ? `<div class="cp-opt-sub">${this.selected.phone}</div>` : ''}
        </div>
        <button class="cp-remove" onclick="ClientPicker.clear('${cid}')" title="Remove">${icon('x',13)}</button>
      </div>`;
  },

  /* Inline create — opens a compact form, saves, then selects it */
  createInline(cid) {
    const prefill = document.getElementById(`cp-input-${cid}`)?.value.trim() || '';
    // Guess: if the typed text is all digits, treat as phone, else name
    const isPhone = /^[+\d][\d\s-]{4,}$/.test(prefill);
    document.getElementById(`cp-drop-${cid}`).classList.remove('open');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New client</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Full name *</label><input class="field-input" id="cpi-name" value="${isPhone ? '' : this._esc(prefill)}" placeholder="Client name"></div>
          <div class="field"><label class="field-label">Phone</label><input class="field-input" id="cpi-phone" value="${isPhone ? this._esc(prefill) : ''}" placeholder="+91-..."></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Email</label><input class="field-input" id="cpi-email" type="email" placeholder="email@..."></div>
          <div class="field"><label class="field-label">Loyalty tier</label>
            <select class="field-select" id="cpi-tier"><option value="standard">Standard</option><option value="vip">VIP</option><option value="corporate">Corporate</option></select>
          </div>
        </div>
        <div class="field"><label class="field-label">Source</label>
          <select class="field-select" id="cpi-source"><option value="">—</option><option>walk_in</option><option>referral</option><option>google_ads</option><option>instagram</option><option>whatsapp</option></select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ClientPicker._saveInline('${cid}')">Create &amp; select</button>
      </div>`, 'modal-narrow', true);
    setTimeout(() => document.getElementById('cpi-name')?.focus(), 60);
  },

  async _saveInline(cid) {
    const name = document.getElementById('cpi-name').value.trim();
    if (!name) { Toast.error('Name is required'); return; }
    try {
      const res = await post('/api/clients', {
        name,
        phone: document.getElementById('cpi-phone').value || null,
        email: document.getElementById('cpi-email').value || null,
        loyalty_tier: document.getElementById('cpi-tier').value,
        source: document.getElementById('cpi-source').value || null,
      });
      const phone = document.getElementById('cpi-phone')?.value || '';
      Modal.close();  // pop only the inline-create modal
      this.choose(cid, res.id, name, phone);
      Toast.success(`Client "${name}" created & linked`);
    } catch (e) { Toast.error(e.message); }
  },

  _esc(s) { return (s || '').replace(/'/g, "\\'").replace(/"/g, '&quot;'); },
  _escHtml(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; },
};

/* close dropdown on outside click */
document.addEventListener('click', (e) => {
  if (!e.target.closest('.cp-wrap')) {
    document.querySelectorAll('.cp-dropdown.open').forEach(d => d.classList.remove('open'));
  }
});
