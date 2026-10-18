/* Tekly Technician app controller. Hash-routed, offline-aware.
   Reuses core.js (Auth, apiLogin, get, Toast, icon) and RosPWA (offline queue). */
(function () {
  const view = () => document.getElementById('view');
  const banners = () => document.getElementById('banners');

  // ── Auth / boot ──
  document.getElementById('t-login').addEventListener('click', doLogin);
  document.getElementById('t-pass').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });
  document.getElementById('t-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') doLogin(); });

  // Second sign-in step: shown once the server says this account has 2-step verification on.
  let needCode = false;
  function askForCode() {
    needCode = true;
    document.getElementById('t-code-hint').classList.remove('hidden');
    const c = document.getElementById('t-code');
    c.classList.remove('hidden'); c.value = ''; c.focus();
  }

  async function doLogin() {
    const email = document.getElementById('t-email').value.trim();
    const pass = document.getElementById('t-pass').value;
    const code = needCode ? document.getElementById('t-code').value.replace(/\s+/g, '') : '';
    const btn = document.getElementById('t-login');
    if (!email || !pass) { Toast.error('Enter your email and password'); return; }
    if (needCode && !code) { Toast.error('Enter the code from your authenticator app'); return; }
    btn.textContent = 'Signing in…'; btn.disabled = true;
    try {
      const d = await apiLogin(email, pass, code);
      if (!d || !d.access_token) throw new Error('Could not sign in');
      Auth.token = d.access_token;
      showApp();
    } catch (e) {
      if (e.mfaRequired) { const retry = needCode; askForCode(); if (!retry) return; }
      Toast && Toast.error ? Toast.error(e.message || 'Could not sign in') : alert(e.message);
    }
    finally { btn.textContent = needCode ? 'Verify' : 'Sign in'; btn.disabled = false; }
  }

  async function showApp() {
    document.getElementById('login').classList.add('hidden');
    document.getElementById('app').classList.remove('hidden');
    // Sliding-session refresh (keeps the on-device session alive across launches).
    // POST — the endpoint never accepted GET, so this refresh silently 405'd on every launch.
    try { const r = await post('/api/auth/refresh'); if (r && r.access_token) Auth.token = r.access_token; } catch (e) {}
    // Best-effort push enrolment (no-op unless VAPID configured + permission granted).
    if (window.RosPWA) RosPWA.enablePush().catch(() => {});
    if (!location.hash) location.hash = '#/dashboard';
    route();
    refreshUnread();
  }

  // ── Shell wiring ──
  function setTab(tab) {
    document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.tab === tab));
  }
  function setTitle(t) { document.getElementById('t-title').textContent = t; }

  function paintConn(online) {
    const p = document.getElementById('t-conn');
    p.className = 'pill ' + (online ? 'on' : 'off');
    p.textContent = online ? '● Online' : '● Offline';
  }
  async function paintSync(st) {
    const b = banners();
    const existing = b.querySelector('.banner.sync');
    if (st && st.pending > 0) {
      const msg = st.syncing ? `Syncing ${st.pending} change(s)…` : `${st.pending} change(s) waiting to sync`;
      if (existing) existing.firstChild.textContent = msg;
      else b.insertAdjacentHTML('beforeend', `<div class="banner sync"><span>${msg}</span></div>`);
    } else if (existing) existing.remove();
    paintConflicts(st);
  }

  function labelFor(c) {
    const u = c.url || '';
    if (/\/status$/.test(u)) return 'Status change';
    if (/\/comments$/.test(u)) return 'Note';
    if (/\/signature$/.test(u)) return 'Signature';
    if (/\/checklist/.test(u)) return 'Checklist update';
    if (/\/lines$/.test(u)) return 'Part added';
    if (/timeclock/.test(u)) return 'Time clock';
    if (/\/complete$/.test(u)) return 'Completion';
    return c.method + ' ' + u.replace(/^\/api\//, '');
  }

  async function paintConflicts(st) {
    const b = banners();
    const existing = b.querySelector('.banner.conflict');
    const n = st ? st.conflicts : (await RosPWA.status()).conflicts;
    if (!n) { if (existing) existing.remove(); return; }
    const msg = `${n} change(s) conflicted with a newer update`;
    if (existing) { existing.firstChild.textContent = msg; return; }
    b.insertAdjacentHTML('beforeend',
      `<div class="banner conflict" style="background:rgba(251,146,60,.16);color:#fdba74"><span>${msg}</span><button id="cf-review">Review</button></div>`);
    document.getElementById('cf-review').addEventListener('click', reviewConflicts);
  }

  async function reviewConflicts() {
    const list = await RosPWA.getConflicts();
    if (!list.length) return;
    const rows = list.map((c) => `<div class="job" data-cid="${c.id}">
      <div class="row"><span class="num" style="font-size:14px">${labelFor(c)}</span></div>
      <div class="meta">${c.detail || ''} · ${new Date(c.at).toLocaleString()}</div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="ghost-btn cf-discard" data-cid="${c.id}" style="margin-top:0;padding:10px 14px;width:auto">Discard mine</button>
        <button class="big-btn cf-retry" data-cid="${c.id}" style="margin-top:0;padding:10px 14px;width:auto">Re-apply</button>
      </div></div>`).join('');
    const html = `<h3 style="margin:0 0 12px">Resolve conflicts</h3>
      <p class="meta" style="margin:0 0 12px">These changes were made offline but the server has since moved on. Re-apply to run yours against the latest, or discard.</p>${rows}`;
    const m = (window.Modal && Modal.open) ? Modal.open(html, 'modal-wide') : null;
    const root = document;
    root.querySelectorAll('.cf-discard').forEach((btn) => btn.addEventListener('click', async () => {
      await RosPWA.resolveConflict(btn.dataset.cid, 'discard'); closeAndRefresh(m);
    }));
    root.querySelectorAll('.cf-retry').forEach((btn) => btn.addEventListener('click', async () => {
      await RosPWA.resolveConflict(btn.dataset.cid, 'retry'); closeAndRefresh(m);
    }));
  }

  function closeAndRefresh(modal) {
    try { if (modal && modal.close) modal.close(); else if (window.Modal && Modal.close) Modal.close(); } catch (e) {}
    RosPWA.status().then(paintConflicts);
  }

  function showUpdateBanner() {
    if (banners().querySelector('.banner.update')) return;
    banners().insertAdjacentHTML('beforeend',
      `<div class="banner update"><span>A new version is ready.</span><button id="t-upd">Update</button></div>`);
    document.getElementById('t-upd').addEventListener('click', () => RosPWA.applyUpdate());
  }

  document.getElementById('t-bell').addEventListener('click', () => { location.hash = '#/notifications'; });

  async function refreshUnread() {
    try {
      const d = await get('/api/tech/notifications?only_unread=true');
      const n = (d.items || []).length;
      const dot = document.getElementById('t-unread');
      if (n > 0) { dot.textContent = n > 9 ? '9+' : n; dot.classList.remove('hidden'); }
      else dot.classList.add('hidden');
    } catch (e) {}
  }

  if (window.RosPWA) {
    paintConn(navigator.onLine);
    RosPWA.on('connectivity', (d) => paintConn(d.online));
    // Only refresh views when signed in: the boot-time flush runs on the sign-in screen too, and a
    // 401 there reloads the page (signedOut), which looped and wiped the form every couple of seconds.
    RosPWA.on('sync', (st) => { paintSync(st); if (st && st.justFlushed && Auth.token) { route(); refreshUnread(); } });
    RosPWA.on('conflict', () => Toast && Toast.error && Toast.error('A change conflicted with a newer update and was skipped.'));
    RosPWA.on('update-available', showUpdateBanner);
    RosPWA.on('installable', () => { const f = document.getElementById('install-fab'); f.classList.remove('hidden'); });
    document.getElementById('install-fab').addEventListener('click', async () => {
      const r = await RosPWA.promptInstall();
      if (r.outcome !== 'unavailable') document.getElementById('install-fab').classList.add('hidden');
    });
    RosPWA.status().then(paintSync);
  }

  // ── Router ──
  window.addEventListener('hashchange', route);
  function route() {
    const h = (location.hash || '#/dashboard').slice(2);
    const [path, arg] = h.split('/');
    if (path === 'dashboard' || path === '') { setTab('dashboard'); setTitle('My jobs'); TechDashboard.render(view()); }
    else if (path === 'clock') { setTab('clock'); setTitle('Time clock'); renderClock(); }
    else if (path === 'scan') { setTab('scan'); setTitle('Scan'); renderScan(); }
    else if (path === 'notifications') { setTab('notifications'); setTitle('Alerts'); renderNotifications(); }
    else if (path === 'preferences') { setTab('notifications'); setTitle('Notifications'); renderPreferences(); }
    else if (path === 'client') { setTitle('Customer'); renderClientHistory(arg); }
    else if (path === 'device') { setTitle('Device history'); renderDeviceHistory(arg); }
    else if (path === 'order') { setTitle('Work order'); renderOrder(arg); }
    else { location.hash = '#/dashboard'; }
  }

  // ── Time clock (offline-capable) ──
  async function renderClock() {
    const v = view();
    v.innerHTML = `<div class="empty">Loading…</div>`;
    let a = { active: null };
    try { a = await get('/api/timeclock/active'); } catch (e) {}
    const on = !!a.active;
    const cat = on ? a.active.category : null;
    v.innerHTML = `
      <div class="stat" style="margin:8px 0 16px"><div class="n" style="font-size:20px">${on ? 'Clocked in' : 'Clocked out'}</div>
        <div class="l">${on ? (cat + (a.active.order_no ? ' · ' + a.active.order_no : '')) : 'Tap below to start your shift'}</div></div>
      ${on ? '' : `<button class="big-btn" id="c-in">Clock in</button>`}
      ${on && cat === 'job' ? `<button class="ghost-btn" id="c-break">Start break</button>` : ''}
      ${on && cat === 'break' ? `<button class="big-btn" id="c-resume">Resume work</button>` : ''}
      ${on ? `<button class="big-btn stop" id="c-out" style="margin-top:10px">Clock out</button>` : ''}
      <p class="empty">Time is tracked per shift. Edits and manager approval happen on the desktop timesheet.</p>`;

    const gps = await getGps();
    const geo = gps ? { gps_lat: gps.lat, gps_lng: gps.lng } : {};
    const tc = (url, body) => RosPWA.mutate('POST', url, body).then((r) => { Toast && Toast.success && Toast.success(r.queued ? 'Saved offline — will sync' : 'Done'); renderClock(); });
    v.querySelector('#c-in') && v.querySelector('#c-in').addEventListener('click', () => tc('/api/timeclock/start', Object.assign({ category: 'job' }, geo)));
    v.querySelector('#c-break') && v.querySelector('#c-break').addEventListener('click', () => tc('/api/timeclock/start?switch=true', Object.assign({ category: 'break' }, geo)));
    v.querySelector('#c-resume') && v.querySelector('#c-resume').addEventListener('click', () => tc('/api/timeclock/start?switch=true', Object.assign({ category: 'job' }, geo)));
    v.querySelector('#c-out') && v.querySelector('#c-out').addEventListener('click', () => tc('/api/timeclock/stop', {}));
  }
  function getGps() {
    return new Promise((res) => {
      if (!navigator.geolocation) return res(null);
      navigator.geolocation.getCurrentPosition(
        (p) => res({ lat: p.coords.latitude, lng: p.coords.longitude }),
        () => res(null), { timeout: 4000, maximumAge: 60000 });
    });
  }

  // ── Scanner (BarcodeDetector with manual fallback) ──
  async function renderScan() {
    const v = view();
    const supported = ('BarcodeDetector' in window);
    v.innerHTML = `
      <div class="sec-h">Scan part / asset / QR</div>
      ${supported ? `<video id="sc-vid" playsinline style="width:100%;border-radius:16px;background:#000;aspect-ratio:3/4;object-fit:cover"></video>
        <button class="ghost-btn" id="sc-stop">Stop camera</button>` :
        `<p class="empty">Live camera scanning isn't supported in this browser (common on iOS). Use manual entry below.</p>`}
      <div class="sec-h">Manual entry</div>
      <input id="sc-code" inputmode="text" placeholder="Type or paste a code / serial"
        style="width:100%;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:14px;color:#fff;font-size:16px;min-height:52px">
      <button class="big-btn" id="sc-look" style="margin-top:10px">Look up</button>
      <div id="sc-result"></div>`;
    v.querySelector('#sc-look').addEventListener('click', () => lookup(v.querySelector('#sc-code').value.trim()));

    if (!supported) return;
    let stream, raf, stopped = false;
    const vid = v.querySelector('#sc-vid');
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      vid.srcObject = stream; await vid.play();
    } catch (e) {
      v.querySelector('#sc-result').innerHTML = `<p class="empty">Camera permission denied. Use manual entry.</p>`;
      return;
    }
    const det = new window.BarcodeDetector();
    const tick = async () => {
      if (stopped) return;
      try { const codes = await det.detect(vid); if (codes && codes.length) { stop(); lookup(codes[0].rawValue); return; } } catch (e) {}
      raf = requestAnimationFrame(tick);
    };
    const stop = () => { stopped = true; cancelAnimationFrame(raf); if (stream) stream.getTracks().forEach((t) => t.stop()); };
    v.querySelector('#sc-stop').addEventListener('click', stop);
    window.addEventListener('hashchange', stop, { once: true });
    tick();
  }
  // Scanned text is untrusted (anyone can print a QR), so it is escaped before it goes into HTML.
  const escHtml = (t) => String(t == null ? '' : t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function lookup(code) {
    code = (code || '').replace(/[\r\n\t]+/g, '').trim();
    if (!code) return;
    const box = document.getElementById('sc-result');
    box.innerHTML = `<div class="empty">Looking up “${escHtml(code)}”…</div>`;
    // One lookup for every label the shop prints: work-order barcode/number, a ticket's tracking QR,
    // a part's SKU/barcode, or a device serial/IMEI/VIN (POST /api/scan/resolve). This used to search
    // the catalogue only, so scanning a work-order label never opened the job.
    let r = null;
    try { r = await post('/api/scan/resolve', { code }); } catch (e) {}
    if (r && r.matched && r.type === 'order') { location.hash = '#/order/' + encodeURIComponent(r.id); return; }
    if (r && r.matched && r.type === 'asset') { location.hash = '#/device/' + encodeURIComponent(r.id); return; }
    if (r && r.matched && r.type === 'product') {
      let p = null;
      try { p = (await get('/api/catalog/products/' + encodeURIComponent(r.id))).product; } catch (e) {}
      box.innerHTML = `<div class="sec-h">Inventory match</div><div class="job"><div class="row"><span class="num">${escHtml((p && p.name) || r.label || code)}</span>
        <span class="st">${p && p.qty_available != null ? escHtml(p.qty_available) + ' in stock' : escHtml((p && (p.sku || p.code)) || '')}</span></div></div>`;
      return;
    }
    // No exact match: offer close catalogue matches (a partial SKU or name).
    let res = null;
    try { res = await get('/api/catalog/products?search=' + encodeURIComponent(code)); } catch (e) {}
    const items = (res && (res.items || res.products)) || [];
    if (items.length) {
      box.innerHTML = `<div class="sec-h">Close inventory matches</div>` + items.slice(0, 5).map((p) =>
        `<div class="job"><div class="row"><span class="num">${escHtml(p.name || p.code || code)}</span>
          <span class="st">${p.qty_available != null ? escHtml(p.qty_available) + ' in stock' : escHtml(p.code || '')}</span></div></div>`).join('');
    } else {
      box.innerHTML = `<div class="job"><div class="row"><span class="num">${escHtml(code)}</span><span class="st">No match</span></div>
        <div class="meta">${r ? 'Nothing in this shop uses this code.' : (navigator.onLine ? 'Lookup failed. Try again.' : "You're offline. Scanning needs a connection.")}</div></div>`;
    }
  }

  // ── Notifications ──
  async function renderNotifications() {
    const v = view();
    v.innerHTML = `<div class="empty">Loading…</div>`;
    let d = { items: [] };
    try { d = await get('/api/tech/notifications'); } catch (e) {}
    const items = d.items || [];
    v.innerHTML =
      `<a href="#/preferences" class="meta" style="display:inline-block;float:right;margin:4px 0 10px;color:#a5b4fc">Settings ›</a>` +
      (items.length ? `<button class="ghost-btn" id="n-all" style="margin:6px 0 12px">Mark all read</button>` : '') +
      (items.length ? items.map((n) => `<a class="job ${n.read ? '' : 'ov'}" href="${(n.deep_link || '#/dashboard').replace('/tech', '')}" data-id="${n.id}">
        <div class="row"><span class="num">${n.title}</span><span class="st">${n.read ? '' : 'new'}</span></div>
        <div class="meta">${n.body || ''} · ${new Date(n.created_at).toLocaleString()}</div></a>`).join('')
        : `<div class="empty">No alerts.</div>`);
    const all = v.querySelector('#n-all');
    if (all) all.addEventListener('click', async () => { await post('/api/tech/notifications/read-all', {}); refreshUnread(); renderNotifications(); });
    v.querySelectorAll('a.job[data-id]').forEach((a) => a.addEventListener('click', () => {
      post('/api/tech/notifications/' + a.dataset.id + '/read', {}).then(refreshUnread).catch(() => {});
    }));
  }

  // ── Work order (compact, one-hand, offline-capable) ──
  async function renderOrder(id) {
    const v = view();
    v.innerHTML = `<div class="empty">Loading work order…</div>`;
    let o;
    try { o = await get('/api/orders/' + id); }
    catch (e) { v.innerHTML = `<div class="empty">Can't open this order${navigator.onLine ? '' : ' offline (not cached yet)'}.</div>`; return; }
    let allowed = [];
    try { allowed = (await get('/api/statuses/allowed/' + id)).items || []; } catch (e) {}
    let checklist = { items: [], summary: { required: 0, required_done: 0, complete: true } };
    try { checklist = await get('/api/orders/' + id + '/checklist'); } catch (e) {}
    let labor = { total_seconds: 0, running: false };
    try { labor = await get('/api/orders/' + id + '/labor'); } catch (e) {}
    const dev = o.device ? `${o.device.brand || ''} ${o.device.model || ''}`.trim() : '';
    const hhmm = (s) => `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    const cl = checklist.summary;

    v.innerHTML = `
      <a href="#/dashboard" class="meta" style="display:inline-block;margin:4px 0 10px">← All jobs</a>
      <div class="stat" style="margin-bottom:14px">
        <div class="n" style="font-size:18px">${o.order_number} <span class="st">${o.status}</span></div>
        <div class="l" style="text-transform:none;font-size:14px;color:#cfcfe0;margin-top:8px">
          ${o.client && o.client.id ? `<a href="#/client/${o.client.id}" style="color:#a5b4fc;text-decoration:none">${o.client.name || 'Customer'} ›</a>` : ((o.client && o.client.name) || 'Walk-in')}${o.client && o.client.phone ? ' · ' + o.client.phone : ''}<br>
          ${dev ? (o.device && o.device.id ? `<a href="#/device/${o.device.id}" style="color:#a5b4fc;text-decoration:none">${dev} ›</a>` : dev) + '<br>' : ''}${o.complaint || ''}</div>
        ${o.status_sla_overdue ? `<div class="l" style="color:#fb923c;margin-top:8px">SLA overdue — ${o.minutes_in_status}m in status</div>` : ''}
      </div>

      <div class="sec-h">Labor timer</div>
      <div class="job"><div class="row"><span class="num">${hhmm(labor.total_seconds)}</span>
        <span class="st">${labor.running ? 'running' : 'logged'}</span></div></div>
      <button class="${labor.running ? 'big-btn stop' : 'big-btn'}" id="o-labor">${labor.running ? 'Stop labor' : 'Start labor on this job'}</button>

      <div class="sec-h">Checklist ${cl.required ? `(${cl.required_done}/${cl.required} required)` : ''}</div>
      <div id="o-checklist"></div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <input id="o-cl-add" placeholder="Add a step…" style="flex:1;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:12px;color:#fff;font-size:15px;min-height:48px">
        <button class="ghost-btn" id="o-cl-btn" style="margin-top:0;width:auto;padding:12px 16px">Add</button>
      </div>
      <button class="ghost-btn" id="o-cl-tpl">Apply a template</button>

      <div class="sec-h">Parts</div>
      <input id="o-part-search" placeholder="Search parts / products…" style="width:100%;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:12px;color:#fff;font-size:15px;min-height:48px">
      <div id="o-part-results"></div>

      <div class="sec-h">Change status</div>
      <select id="o-status" style="width:100%;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:14px;color:#fff;font-size:16px;min-height:52px">
        ${(allowed.length ? allowed : [o.status]).map((s) => `<option ${s === o.status ? 'selected' : ''}>${s}</option>`).join('')}
      </select>
      <button class="big-btn" id="o-save-status" style="margin-top:10px">Update status</button>

      <div class="sec-h">Add a note</div>
      <textarea id="o-note" rows="2" placeholder="What did you find / do?" style="width:100%;background:rgba(0,0,0,.25);border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:14px;color:#fff;font-size:16px"></textarea>
      <label class="meta" style="display:flex;gap:8px;align-items:center;margin:8px 0"><input type="checkbox" id="o-cust"> Visible to customer</label>
      <button class="ghost-btn" id="o-add-note">Save note</button>

      <div class="sec-h">Photo / video / PDF</div>
      <input type="file" accept="image/*" capture="environment" id="o-photo" style="width:100%;color:#cfcfe0;font-size:14px;margin-bottom:8px">
      <input type="file" accept="video/*,application/pdf,image/*" id="o-file" style="width:100%;color:#cfcfe0;font-size:14px">

      <div class="sec-h">Customer signature</div>
      <canvas id="o-sig" style="width:100%;height:160px;background:#fff;border-radius:14px;touch-action:none"></canvas>
      <div style="display:flex;gap:10px;margin-top:10px">
        <button class="ghost-btn" id="o-sig-clear" style="margin-top:0">Clear</button>
        <button class="big-btn" id="o-sig-save" style="margin-top:0">Save signature</button>
      </div>

      <div class="sec-h">Finish</div>
      <button class="big-btn" id="o-pay" style="background:linear-gradient(160deg,#10b981,#34d399)">Generate payment link</button>
      <div id="o-pay-out"></div>
      <button class="big-btn" id="o-complete" style="margin-top:10px">Complete repair</button>
      <div id="o-complete-out"></div>
      <div style="height:24px"></div>`;

    const queuedToast = (r) => Toast && Toast.success && Toast.success(r.queued ? 'Saved offline — will sync' : 'Saved');

    // Labor timer
    v.querySelector('#o-labor').addEventListener('click', async () => {
      const url = labor.running ? '/api/timeclock/stop' : '/api/timeclock/start?switch=true';
      const body = labor.running ? {} : { category: 'job', order_id: id };
      const r = await RosPWA.mutate('POST', url, body);
      if (r.ok) { queuedToast(r); if (!r.queued) renderOrder(id); }
    });

    // Checklist
    renderChecklist(v.querySelector('#o-checklist'), id, checklist.items);
    v.querySelector('#o-cl-btn').addEventListener('click', async () => {
      const label = v.querySelector('#o-cl-add').value.trim(); if (!label) return;
      const r = await RosPWA.mutate('POST', '/api/orders/' + id + '/checklist', { label, required: true });
      if (r.ok) { queuedToast(r); if (!r.queued) renderOrder(id); }
    });
    v.querySelector('#o-cl-tpl').addEventListener('click', async () => {
      let tpls = { items: [] };
      try { tpls = await get('/api/checklist-templates' + (o.type_id ? '?type_id=' + o.type_id : '')); } catch (e) {}
      if (!tpls.items.length) return Toast && Toast.error && Toast.error('No checklist templates yet (create them on desktop).');
      const t = tpls.items[0];
      const r = await RosPWA.mutate('POST', '/api/orders/' + id + '/checklist', { template_id: t.id });
      if (r.ok) { queuedToast(r); if (!r.queued) renderOrder(id); }
    });

    // Parts search + add
    let partTimer;
    v.querySelector('#o-part-search').addEventListener('input', (e) => {
      clearTimeout(partTimer);
      const q = e.target.value.trim();
      partTimer = setTimeout(() => searchParts(id, q, v.querySelector('#o-part-results'), queuedToast), 300);
    });

    // Status
    v.querySelector('#o-save-status').addEventListener('click', async () => {
      const s = v.querySelector('#o-status').value;
      const r = await RosPWA.mutate('PATCH', '/api/orders/' + id + '/status', { status: s });
      if (r.ok) { queuedToast(r); if (!r.queued) renderOrder(id); }
      else Toast.error('Status change rejected (' + (r.status || '') + ')');
    });
    // Note
    v.querySelector('#o-add-note').addEventListener('click', async () => {
      const body = v.querySelector('#o-note').value.trim(); if (!body) return;
      const r = await RosPWA.mutate('POST', '/api/orders/' + id + '/comments',
        { body, visible_to_client: v.querySelector('#o-cust').checked });
      if (r.ok) { v.querySelector('#o-note').value = ''; queuedToast(r); }
    });
    // Attachments
    const upload = (f, kind) => {
      const rd = new FileReader();
      rd.onload = async () => {
        const r = await RosPWA.mutate('POST', '/api/feed/order/' + id,
          { item_type: kind, media_data: rd.result, media_name: f.name, media_mime: f.type, body: f.name });
        if (r.ok) queuedToast(r);
      };
      rd.readAsDataURL(f);
    };
    v.querySelector('#o-photo').addEventListener('change', (e) => { const f = e.target.files[0]; if (f) upload(f, 'photo'); });
    v.querySelector('#o-file').addEventListener('change', (e) => {
      const f = e.target.files[0]; if (!f) return;
      if (f.size > 8 * 1024 * 1024) return Toast && Toast.error && Toast.error('File too large (max ~8MB for mobile upload).');
      upload(f, f.type.startsWith('image') ? 'photo' : 'file');
    });
    setupSignature(v.querySelector('#o-sig'), v.querySelector('#o-sig-clear'), v.querySelector('#o-sig-save'), id, queuedToast);

    // Payment link (online-only — financial)
    v.querySelector('#o-pay').addEventListener('click', async () => {
      const out = v.querySelector('#o-pay-out');
      if (!navigator.onLine) { out.innerHTML = `<div class="empty">Payment links need a connection.</div>`; return; }
      out.innerHTML = `<div class="empty">Creating link…</div>`;
      try {
        const d = await post('/api/orders/' + id + '/payment-link', {});
        out.innerHTML = `<div class="job" style="text-align:center">
          <img src="${d.qr_svg}" alt="QR" style="width:160px;height:160px;background:#fff;border-radius:12px;padding:8px">
          <div class="meta" style="margin-top:10px;word-break:break-all">${d.url}</div>
          <button class="ghost-btn" id="o-pay-copy">Copy link</button></div>`;
        out.querySelector('#o-pay-copy').addEventListener('click', () => {
          navigator.clipboard && navigator.clipboard.writeText(d.url);
          Toast && Toast.success && Toast.success('Link copied');
        });
      } catch (e) { out.innerHTML = `<div class="empty">${(e && e.message) || 'Could not create a payment link.'}</div>`; }
    });

    // Complete (runs the server completion gate)
    v.querySelector('#o-complete').addEventListener('click', async () => {
      const out = v.querySelector('#o-complete-out');
      const r = await RosPWA.mutate('POST', '/api/orders/' + id + '/complete', {});
      if (r.ok && r.queued) { out.innerHTML = `<div class="empty">Queued — will complete on sync.</div>`; return; }
      if (r.ok) { out.innerHTML = `<div class="empty" style="color:#34d399">✓ Completed.</div>`; setTimeout(() => renderOrder(id), 600); }
      else out.innerHTML = `<div class="empty" style="color:#fb7185">${(r.data && r.data.detail) || 'Could not complete (status ' + (r.status || '') + ').'}</div>`;
    });
  }

  function renderChecklist(el, id, items) {
    if (!items.length) { el.innerHTML = `<div class="empty">No checklist yet.</div>`; return; }
    el.innerHTML = items.map((c) => `<label class="job" style="display:flex;gap:10px;align-items:center">
      <input type="checkbox" data-cid="${c.id}" ${c.done ? 'checked' : ''} style="width:22px;height:22px">
      <span class="num" style="font-weight:600;font-size:14px${c.done ? ';text-decoration:line-through;color:#7a7a95' : ''}">${c.label}${c.required ? '' : ' (optional)'}</span></label>`).join('');
    el.querySelectorAll('input[data-cid]').forEach((cb) => cb.addEventListener('change', async () => {
      const r = await RosPWA.mutate('PATCH', '/api/orders/' + id + '/checklist/' + cb.dataset.cid, { done: cb.checked });
      if (r.ok && r.queued) Toast && Toast.success && Toast.success('Saved offline — will sync');
    }));
  }

  async function searchParts(id, q, box, queuedToast) {
    if (!q) { box.innerHTML = ''; return; }
    let res = { items: [] };
    try { res = await get('/api/catalog/products?search=' + encodeURIComponent(q)); } catch (e) {}
    const items = res.items || res.products || [];
    if (!items.length) { box.innerHTML = `<div class="empty">No matches.</div>`; return; }
    const priceOf = (p) => (p.sell_price != null ? p.sell_price : (p.price != null ? p.price : (p.unit_price || 0)));
    box.innerHTML = items.slice(0, 6).map((p, i) => {
      const stock = p.qty != null ? `${p.qty} in stock` : '';
      return `<div class="job" data-i="${i}"><div class="row"><span class="num">${p.name || p.code}</span>
        <span class="st">add</span></div><div class="meta">${priceOf(p) ? '$' + priceOf(p) : ''}${stock ? ' · ' + stock : ''}${p.sku ? ' · ' + p.sku : ''}</div></div>`;
    }).join('');
    box.querySelectorAll('[data-i]').forEach((row) => row.addEventListener('click', async () => {
      const p = items[+row.dataset.i];
      // Link the catalog part (ref_id + kind:product) so stock deducts and the
      // close-time pending-stock guard applies.
      const r = await RosPWA.mutate('POST', '/api/orders/' + id + '/lines',
        { line_type: 'part', kind: 'product', ref_id: p.id, description: p.name || p.code, qty: 1, unit_price: priceOf(p) });
      if (r.ok) { queuedToast(r); box.innerHTML = `<div class="empty" style="color:#34d399">Added ${p.name || p.code}.</div>`; }
      else box.innerHTML = `<div class="empty" style="color:#fb7185">${(r.data && r.data.detail) || 'Could not add part (stock?).'}</div>`;
    }));
  }


  function setupSignature(canvas, clearBtn, saveBtn, id, queuedToast) {
    const ratio = window.devicePixelRatio || 1;
    const resize = () => { canvas.width = canvas.clientWidth * ratio; canvas.height = canvas.clientHeight * ratio; };
    resize();
    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio); ctx.lineWidth = 2.4; ctx.lineCap = 'round'; ctx.strokeStyle = '#111';
    let drawing = false, dirty = false;
    const pos = (e) => { const r = canvas.getBoundingClientRect(); const t = e.touches ? e.touches[0] : e; return { x: t.clientX - r.left, y: t.clientY - r.top }; };
    const start = (e) => { drawing = true; dirty = true; const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); e.preventDefault(); };
    const move = (e) => { if (!drawing) return; const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke(); e.preventDefault(); };
    const end = () => { drawing = false; };
    canvas.addEventListener('pointerdown', start); canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', end); canvas.addEventListener('pointerleave', end);
    clearBtn.addEventListener('click', () => { ctx.clearRect(0, 0, canvas.width, canvas.height); dirty = false; });
    saveBtn.addEventListener('click', async () => {
      if (!dirty) return Toast && Toast.error && Toast.error('Please capture a signature first');
      const dataUrl = canvas.toDataURL('image/png');
      const r = await RosPWA.mutate('POST', '/api/orders/' + id + '/signature', { signature: dataUrl, note: 'Captured in field' });
      if (r.ok) queuedToast(r);
    });
  }

  // ── Customer & device history (Part 7) ──
  function moneyTxt(n) { return '$' + (Number(n || 0)).toFixed(2); }
  function orderRow(c) {
    const when = c.created_at ? new Date(c.created_at).toLocaleDateString() : '';
    return `<a class="job" href="#/order/${c.id}"><div class="row"><span class="num">${c.order_number}</span>
      <span class="st">${c.status}</span></div>
      <div class="meta">${when}${c.balance > 0 ? ' · ' + moneyTxt(c.balance) + ' due' : ''}${c.warranty_until ? ' · warranty to ' + c.warranty_until.slice(0, 10) : ''}</div></a>`;
  }

  async function renderClientHistory(cid) {
    const v = view();
    v.innerHTML = `<div class="empty">Loading history…</div>`;
    let h;
    try { h = await get('/api/tech/client/' + cid + '/history'); }
    catch (e) { v.innerHTML = `<div class="empty">Couldn't load customer history${navigator.onLine ? '' : ' (offline)'}.</div>`; return; }
    const s = h.stats;
    v.innerHTML = `
      <a href="javascript:history.back()" class="meta" style="display:inline-block;margin:4px 0 10px">← Back</a>
      <div class="stat" style="margin-bottom:8px"><div class="n" style="font-size:18px">${h.client.name}</div>
        <div class="l" style="text-transform:none;color:#cfcfe0;margin-top:6px">${h.client.phone || ''}${h.client.email ? ' · ' + h.client.email : ''}</div></div>
      <div class="stat-grid">
        <div class="stat"><div class="n">${s.orders}</div><div class="l">Repairs</div></div>
        <div class="stat ${s.outstanding_balance > 0 ? 'alert' : ''}"><div class="n" style="font-size:20px">${moneyTxt(s.outstanding_balance)}</div><div class="l">Outstanding</div></div>
      </div>
      ${h.recommendations.length ? `<div class="sec-h">Previous recommendations</div>` + h.recommendations.map((r) => `<div class="job"><div class="meta" style="color:#e9e9f2">${r.body}</div></div>`).join('') : ''}
      ${h.devices.length ? `<div class="sec-h">Devices</div>` + h.devices.map((d) => `<a class="job" href="#/device/${d.id}"><div class="row"><span class="num" style="font-size:14px">${d.label}</span><span class="st">${d.serial || d.imei || ''}</span></div></a>`).join('') : ''}
      <div class="sec-h">Repairs</div>${h.orders.length ? h.orders.map(orderRow).join('') : `<div class="empty">No repairs.</div>`}
      ${h.invoices.length ? `<div class="sec-h">Invoices</div>` + h.invoices.map((i) => `<div class="job"><div class="row"><span class="num" style="font-size:14px">${i.invoice_number}</span><span class="st">${i.status}</span></div><div class="meta">${moneyTxt(i.total)}${i.balance > 0 ? ' · ' + moneyTxt(i.balance) + ' due' : ' · paid'}</div></div>`).join('') : ''}
      ${h.recent_activity.length ? `<div class="sec-h">Recent activity</div>` + h.recent_activity.slice(0, 8).map((f) => `<div class="job"><div class="meta">${f.item_type}${f.body ? ' · ' + f.body.slice(0, 80) : ''}</div></div>`).join('') : ''}
      <div style="height:24px"></div>`;
  }

  async function renderDeviceHistory(did) {
    const v = view();
    v.innerHTML = `<div class="empty">Loading device…</div>`;
    let h;
    try { h = await get('/api/tech/device/' + did + '/history'); }
    catch (e) { v.innerHTML = `<div class="empty">Couldn't load device history${navigator.onLine ? '' : ' (offline)'}.</div>`; return; }
    v.innerHTML = `
      <a href="javascript:history.back()" class="meta" style="display:inline-block;margin:4px 0 10px">← Back</a>
      <div class="stat" style="margin-bottom:8px"><div class="n" style="font-size:18px">${h.device.label}</div>
        <div class="l" style="text-transform:none;color:#cfcfe0;margin-top:6px">${h.device.serial ? 'SN ' + h.device.serial : ''}${h.device.imei ? ' · IMEI ' + h.device.imei : ''}${h.device.condition ? ' · ' + h.device.condition : ''}</div></div>
      <div class="stat-grid">
        <div class="stat"><div class="n">${h.stats.repairs}</div><div class="l">Repairs</div></div>
        <div class="stat ${h.stats.outstanding_balance > 0 ? 'alert' : ''}"><div class="n" style="font-size:20px">${moneyTxt(h.stats.outstanding_balance)}</div><div class="l">Outstanding</div></div>
      </div>
      <div class="sec-h">Repair history</div>${h.orders.length ? h.orders.map(orderRow).join('') : `<div class="empty">No repairs.</div>`}
      <div style="height:24px"></div>`;
  }

  // ── Notification preferences (Part 9) ──
  const PREF_LABELS = {
    'job.assigned': 'New job assigned', 'schedule.changed': 'Schedule / status changes',
    'chat.message': 'New messages', 'estimate.approved': 'Customer approvals',
    'booking.cancelled': 'Cancelled bookings', 'payment.received': 'Payment received',
  };
  async function renderPreferences() {
    const v = view();
    v.innerHTML = `<div class="empty">Loading…</div>`;
    let prefs = { preferences: [] };
    try { prefs = await get('/api/tech/preferences'); } catch (e) {}
    v.innerHTML = `<a href="#/notifications" class="meta" style="display:inline-block;margin:4px 0 10px">← Alerts</a>
      <div class="sec-h">Push notifications</div>
      ${prefs.preferences.map((p) => `<label class="job" style="display:flex;align-items:center;gap:10px">
        <span class="num" style="font-size:14px;flex:1">${PREF_LABELS[p.kind] || p.kind}</span>
        <input type="checkbox" data-kind="${p.kind}" ${p.enabled ? 'checked' : ''} style="width:44px;height:26px"></label>`).join('')}
      <p class="empty">Turn a type off to stop both push and in-app alerts for it.</p>`;
    v.querySelectorAll('input[data-kind]').forEach((cb) => cb.addEventListener('change', async () => {
      try { await put('/api/tech/preferences', { kind: cb.dataset.kind, enabled: cb.checked }); Toast && Toast.success && Toast.success('Saved'); }
      catch (e) { cb.checked = !cb.checked; Toast && Toast.error && Toast.error('Could not save'); }
    }));
  }

  // ── Start ──
  if (Auth.token) showApp(); // already signed in on this device
})();
