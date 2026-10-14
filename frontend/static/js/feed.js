/* ════════════════════════════════════════════════════
   Event Feed — reusable component for orders & clients
   Renders into a container, handles comment / photo /
   signature / file, client-visible toggle, delete.
════════════════════════════════════════════════════ */

const Feed = {
  entityType: null,
  entityId: null,
  containerId: null,
  visible: false,        // client-visible toggle state

  async mount(entityType, entityId, containerId) {
    this.entityType = entityType;
    this.entityId = entityId;
    this.containerId = containerId;
    this.visible = false;
    const c = document.getElementById(containerId);
    if (!c) return;
    this._filter = 'all';
    c.innerHTML = this._composerHtml()
      + `<div class="feed-filter" id="feed-filter-${containerId}">
           <button class="feed-fbtn on" data-f="all" onclick="Feed.setFilter('all',this)">All events</button>
           <button class="feed-fbtn" data-f="comments" onclick="Feed.setFilter('comments',this)">Comments</button>
         </div>`
      + `<div id="feed-list-${containerId}"></div>`;
    await this.reload();
  },

  // 144bh WS3: Activity filter — comments vs the full typed event stream.
  _filter: 'all',
  _COMMENT_TYPES: ['comment', 'client_message', 'staff_reply'],
  setFilter(f, el) {
    this._filter = f;
    if (el) { el.parentElement.querySelectorAll('.feed-fbtn').forEach(b => b.classList.remove('on')); el.classList.add('on'); }
    this._paint();
  },
  _paint() {
    const listEl = document.getElementById(`feed-list-${this.containerId}`);
    if (!listEl) return;
    let items = this._items || [];
    if (this._filter === 'comments') items = items.filter(it => this._COMMENT_TYPES.includes(it.item_type));
    if (!items.length) {
      listEl.innerHTML = `<div style="text-align:center;padding:24px;color:var(--tx-4);font-size:13px">${this._filter === 'comments' ? 'No comments yet' : 'No activity yet — add the first note above'}</div>`;
      return;
    }
    listEl.innerHTML = `<div class="feed-timeline">${items.map(it => this._itemHtml(it)).join('')}</div>`;
  },

  _composerHtml() {
    return `
      <div class="feed-composer card-sm" style="padding:12px;margin-bottom:16px">
        <textarea class="feed-text" id="feed-text-${this.containerId}" placeholder="Add a comment…" rows="2"
          style="width:100%;border:none;background:none;resize:none;font-family:var(--font);font-size:13.5px;color:var(--tx-1);outline:none"></textarea>
        <div id="feed-staged-${this.containerId}"></div>
        <div style="display:flex;align-items:center;gap:6px;margin-top:8px;flex-wrap:wrap">
          <button class="feed-tool" onclick="Feed.pickPhoto()" title="Attach photo">
            ${icon('camera',15)} Photo</button>
          <button class="feed-tool" onclick="Feed.pickFile()" title="Attach file">
            ${icon('paperclip',15)} File</button>
          <button class="feed-tool" onclick="Feed.openSignature()" title="Capture signature">
            ${icon('signature',15)} Signature</button>
          <label class="feed-vis" id="feed-vis-${this.containerId}" onclick="Feed.toggleVis()">
            <span class="feed-vis-dot"></span> <span class="feed-vis-label">Private</span>
          </label>
          <button class="btn btn-primary btn-sm" style="margin-left:auto" onclick="Feed.postComment()">Post</button>
        </div>
        <input type="file" id="feed-photo-input-${this.containerId}" accept="image/*" capture="environment" style="display:none" onchange="Feed.onPhoto(event)">
        <input type="file" id="feed-file-input-${this.containerId}" style="display:none" onchange="Feed.onFile(event)">
      </div>`;
  },

  toggleVis() {
    this.visible = !this.visible;
    const el = document.getElementById(`feed-vis-${this.containerId}`);
    el.classList.toggle('on', this.visible);
    el.querySelector('.feed-vis-label').textContent = this.visible ? 'Client-visible' : 'Private';
  },

  // Build 144aq — unified take-photo/choose picker (downscaled); falls back to the
  // native file input if the shared component isn't loaded.
  pickPhoto() {
    if (typeof PhotoInput !== 'undefined') {
      PhotoInput.capture({ title: 'Add photo', onPhoto: async (dataUrl) => { await this._post({ item_type: 'photo', media_data: dataUrl, media_name: 'photo.jpg', media_mime: 'image/jpeg' }); } });
    } else { document.getElementById(`feed-photo-input-${this.containerId}`).click(); }
  },
  pickFile()  { document.getElementById(`feed-file-input-${this.containerId}`).click(); },

  async onPhoto(e) {
    const f = e.target.files[0]; if (!f) return;
    const data = await this._toDataURL(f);
    await this._post({ item_type: 'photo', media_data: data, media_name: f.name, media_mime: f.type });
    e.target.value = '';
  },
  async onFile(e) {
    const f = e.target.files[0]; if (!f) return;
    if (f.size > 5_000_000) { Toast.error('File too large (max 5 MB)'); e.target.value = ''; return; }
    const data = await this._toDataURL(f);
    await this._post({ item_type: 'file', media_data: data, media_name: f.name, media_mime: f.type });
    e.target.value = '';
  },

  _toDataURL(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.onerror = rej;
      r.readAsDataURL(file);
    });
  },

  async postComment() {
    const ta = document.getElementById(`feed-text-${this.containerId}`);
    const body = ta.value.trim();
    if (!body) { Toast.error('Write something first'); return; }
    await this._post({ item_type: 'comment', body });
    ta.value = '';
  },

  async _post(extra) {
    try {
      await post(`/api/feed/${this.entityType}/${this.entityId}`, {
        visibility: this.visible ? 'client' : 'private',
        ...extra,
      });
      Toast.success('Added to feed');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async reload() {
    const listEl = document.getElementById(`feed-list-${this.containerId}`);
    if (!listEl) return;
    try {
      const data = await get(`/api/feed/${this.entityType}/${this.entityId}`);
      this._items = data.items || [];
      this._paint();
    } catch (e) { Toast.error(e.message); }
  },

  // Render a system-event body, styling any "before → after" diff (e.g. status,
  // amount changes) as muted-before → bold-after. User comments are left untouched.
  _sysBody(body) {
    const esc = this._escape(body || '');
    const m = esc.split(' → ');
    if (m.length !== 2) return esc;
    return `<span class="feed-diff-a">${m[0]}</span><span class="feed-diff-arrow">→</span><span class="feed-diff-b">${m[1]}</span>`;
  },

  _itemHtml(it) {
    const isMsg = it.item_type === 'client_message';   // 144ba — message from the customer
    const isReply = it.item_type === 'staff_reply';    // 144be — shop reply to the customer
    const icons = { comment:'message', photo:'camera', signature:'signature', file:'paperclip', system:'refresh', client_message:'message', staff_reply:'message' };
    const dotColors = {
      comment:'var(--blue)', photo:'var(--teal)', signature:'var(--green)',
      file:'var(--tx-3)', system:'var(--amber)', client_message:'var(--blue)', staff_reply:'var(--green)',
    };
    const visBadge = isMsg
      ? `<span class="feed-badge feed-badge-cust">Customer</span>`
      : (isReply
        ? `<span class="feed-badge feed-badge-pub">Reply to customer</span>`
        : it.visibility === 'client'
        ? `<span class="feed-badge feed-badge-pub">client-visible</span>`
        : (it.item_type !== 'system' ? `<span class="feed-badge">private</span>` : ''));

    let media = '';
    if (it.item_type === 'photo' && it.media_data) {
      media = `<img src="${it.media_data}" class="feed-photo" onclick="Feed.lightbox('${it.id}')" alt="${it.media_name||'photo'}">`;
    } else if (it.item_type === 'signature' && it.media_data) {
      media = `<div class="feed-sig"><img src="${it.media_data}" alt="signature"></div>`;
    } else if (it.item_type === 'file' && it.media_data) {
      media = `<a href="${it.media_data}" download="${it.media_name||'file'}" class="feed-file-chip">
        ${icon('file',14)} ${it.media_name || 'attachment'}</a>`;
    }

    const canDelete = it.item_type !== 'system' && !isMsg;
    return `
      <div class="feed-item${isMsg ? ' feed-item-msg' : ''}" data-id="${it.id}">
        <div class="feed-dot" style="background:${dotColors[it.item_type]}22;color:${dotColors[it.item_type]}">${icon(icons[it.item_type]||'message',15)}</div>
        <div class="feed-body">
          <div class="feed-head">
            <span class="feed-who">${it.author_name || 'Unknown'}</span>
            <span class="feed-time">${fmt.reltime(it.created_at)}</span>
            ${visBadge}
            ${canDelete ? `<button class="feed-del" onclick="Feed.del('${it.id}')" title="Delete">${icon('x',12)}</button>` : ''}
          </div>
          ${it.body ? (isMsg ? `<div class="feed-msg">${this._escape(it.body)}</div>` : `<div class="feed-txt">${it.item_type === 'system' ? this._sysBody(it.body) : this._escape(it.body)}</div>`) : ''}
          ${media}
        </div>
      </div>`;
  },

  _escape(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; },

  async del(id) {
    const ok = await confirm2('Delete this entry?');
    if (!ok) return;
    try { await del(`/api/feed/item/${id}`); Toast.success('Deleted'); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  lightbox(id) {
    const img = document.querySelector(`.feed-item[data-id="${id}"] .feed-photo`);
    if (!img) return;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Photo</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body" style="text-align:center"><img src="${img.src}" style="max-width:100%;border-radius:var(--r-md)"></div>`, 'modal-wide');
  },

  /* ── Signature capture ── */
  openSignature() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Capture signature</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:12px;color:var(--tx-3);margin-bottom:8px">Sign in the box below</div>
        <canvas id="sig-canvas" width="480" height="180"
          style="width:100%;background:rgba(255,255,255,.7);border:1.5px dashed rgba(79,142,247,.4);border-radius:var(--r-md);touch-action:none;cursor:crosshair"></canvas>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass btn-sm" onclick="Feed.clearSig()">Clear</button>
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="Feed.saveSig()">Save signature</button>
      </div>`, 'modal-narrow');
    setTimeout(() => this._initSigPad(), 50);
  },

  _sig: { drawing: false, ctx: null, canvas: null, dirty: false },

  _initSigPad() {
    const canvas = document.getElementById('sig-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.strokeStyle = '#18254a';
    ctx.lineWidth = 2.2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    this._sig = { drawing: false, ctx, canvas, dirty: false };

    const pos = (e) => {
      const r = canvas.getBoundingClientRect();
      const t = e.touches ? e.touches[0] : e;
      return { x: (t.clientX - r.left) * (canvas.width / r.width),
               y: (t.clientY - r.top) * (canvas.height / r.height) };
    };
    const start = (e) => { e.preventDefault(); this._sig.drawing = true; this._sig.dirty = true; const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); };
    const move  = (e) => { if (!this._sig.drawing) return; e.preventDefault(); const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); };
    const end   = () => { this._sig.drawing = false; };

    canvas.addEventListener('mousedown', start);
    canvas.addEventListener('mousemove', move);
    canvas.addEventListener('mouseup', end);
    canvas.addEventListener('mouseleave', end);
    canvas.addEventListener('touchstart', start, { passive: false });
    canvas.addEventListener('touchmove', move, { passive: false });
    canvas.addEventListener('touchend', end);
  },

  clearSig() {
    const s = this._sig;
    if (s.ctx) { s.ctx.clearRect(0, 0, s.canvas.width, s.canvas.height); s.dirty = false; }
  },

  async saveSig() {
    const s = this._sig;
    if (!s.canvas || !s.dirty) { Toast.error('Please sign first'); return; }
    const data = s.canvas.toDataURL('image/png');
    Modal.close();
    await this._post({ item_type: 'signature', media_data: data, media_name: 'signature.png', media_mime: 'image/png' });
  },
};
