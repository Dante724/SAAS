/* Tekly PWA runtime: SW registration + update handling, install prompt,
   connectivity, and an encrypted offline mutation queue with auto-sync.
   Exposes window.RosPWA. Depends on nothing; core.js reads Auth.token. */
(function () {
  const DB_NAME = 'ros-tech';
  const STORE = 'queue';
  const META = 'meta';
  const TOKEN_KEY = 'ros_tok';

  const listeners = {};
  function emit(evt, detail) {
    (listeners[evt] || []).forEach((fn) => { try { fn(detail); } catch (e) {} });
    window.dispatchEvent(new CustomEvent('ros:' + evt, { detail }));
  }

  // ── IndexedDB ──
  function idb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => {
        const db = r.result;
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
        if (!db.objectStoreNames.contains(META)) db.createObjectStore(META, { keyPath: 'k' });
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  function tx(db, store, mode) { return db.transaction(store, mode).objectStore(store); }
  function reqP(req) { return new Promise((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); }); }

  // ── Encryption key (AES-GCM, non-extractable, persisted in IndexedDB) ──
  async function getKey() {
    const db = await idb();
    let rec = await reqP(tx(db, META, 'readonly').get('cryptoKey'));
    if (rec && rec.key) return rec.key;
    const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    await reqP(tx(db, META, 'readwrite').put({ k: 'cryptoKey', key }));
    return key;
  }
  async function enc(obj) {
    const key = await getKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = new TextEncoder().encode(JSON.stringify(obj));
    const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, data);
    return { iv: Array.from(iv), ct: Array.from(new Uint8Array(ct)) };
  }
  async function dec(blob) {
    const key = await getKey();
    const iv = new Uint8Array(blob.iv);
    const ct = new Uint8Array(blob.ct);
    const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);
    return JSON.parse(new TextDecoder().decode(pt));
  }

  // ── Queue ──
  async function enqueue(item) {
    const db = await idb();
    const payload = await enc(item);
    await reqP(tx(db, STORE, 'readwrite').add({ payload, enqueued_at: Date.now() }));
    registerSync();
    emit('sync', await status());
  }
  async function allRows() {
    const db = await idb();
    return await reqP(tx(db, STORE, 'readonly').getAll());
  }
  async function remove(id) {
    const db = await idb();
    await reqP(tx(db, STORE, 'readwrite').delete(id));
  }
  async function pendingCount() { return (await allRows()).length; }
  async function status() {
    return { pending: await pendingCount(), online: navigator.onLine, syncing: RosPWA._syncing, conflicts: (await _conflicts()).length };
  }

  function registerSync() {
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      navigator.serviceWorker.ready.then((reg) => reg.sync.register('ros-sync').catch(() => {}));
    }
  }

  // ── Conflict store (persisted so it survives reloads until resolved) ──
  async function _conflicts() {
    const db = await idb();
    let rec; try { rec = await reqP(tx(db, META, 'readonly').get('conflicts')); } catch (e) { rec = null; }
    return (rec && rec.list) || [];
  }
  async function _saveConflicts(list) {
    const db = await idb();
    await reqP(tx(db, META, 'readwrite').put({ k: 'conflicts', list }));
  }
  async function recordConflict(c) {
    const list = await _conflicts(); c.id = 'c' + Date.now() + Math.random().toString(36).slice(2, 6);
    list.push(c); await _saveConflicts(list);
  }
  async function getConflicts() { return await _conflicts(); }
  async function resolveConflict(id, action) {
    const list = await _conflicts();
    const c = list.find((x) => x.id === id);
    const rest = list.filter((x) => x.id !== id);
    await _saveConflicts(rest);
    if (c && action === 'retry') {         // re-queue the mutation to run against latest server state
      await enqueue({ method: c.method, url: c.url, body: c.body });
      if (navigator.onLine) flush();
    }
    emit('sync', await status());
  }

  // ── Replay ──
  async function flush() {
    if (RosPWA._syncing || !navigator.onLine) return;
    RosPWA._syncing = true; emit('sync', await status());
    let conflicts = 0, failed = 0;
    try {
      const rows = await allRows();
      for (const row of rows) {
        let item; try { item = await dec(row.payload); } catch { await remove(row.id); continue; }
        const headers = Object.assign({ 'Content-Type': 'application/json' }, item.headers || {});
        const tok = localStorage.getItem(TOKEN_KEY);
        if (tok) headers['Authorization'] = 'Bearer ' + tok;
        try {
          const res = await fetch(item.url, { method: item.method, headers, body: item.body ? JSON.stringify(item.body) : undefined });
          if (res.ok) { await remove(row.id); }
          else if (res.status === 409) {
            conflicts++; await remove(row.id);
            let server = null;
            try { server = await res.clone().json(); } catch (e) {}
            await recordConflict({ method: item.method, url: item.url, body: item.body,
                                   detail: (server && (server.detail || server.message)) || 'Conflicts with a newer change',
                                   at: Date.now() });
            emit('conflict', { item, status: 409 });
          }
          else if (res.status === 401) { failed++; break; }                 // need re-auth; stop
          else if (res.status >= 400 && res.status < 500) { await remove(row.id); emit('dropped', { item, status: res.status }); }
          else { failed++; }                                                 // 5xx -> keep, retry later
        } catch { failed++; break; }                                         // offline mid-flush -> stop
      }
    } finally {
      RosPWA._syncing = false;
      const st = await status();
      emit('sync', Object.assign(st, { conflicts, failed, justFlushed: true }));
    }
  }

  // ── Public mutation helper: online-first, queue on failure/offline ──
  async function mutate(method, url, body, opts) {
    opts = opts || {};
    const tok = localStorage.getItem(TOKEN_KEY);
    const headers = { 'Content-Type': 'application/json' };
    if (tok) headers['Authorization'] = 'Bearer ' + tok;
    if (navigator.onLine) {
      try {
        const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
        if (res.ok) return { ok: true, queued: false, data: await res.json().catch(() => ({})) };
        if (res.status >= 500) throw new Error('server');         // transient -> queue
        return { ok: false, queued: false, status: res.status, data: await res.json().catch(() => ({})) };
      } catch (e) { /* fall through to queue */ }
    }
    await enqueue({ method, url, body });
    return { ok: true, queued: true };
  }

  // ── Service worker registration + update handling ──
  function register() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').then((reg) => {
      reg.addEventListener('updatefound', () => {
        const nw = reg.installing;
        if (!nw) return;
        nw.addEventListener('statechange', () => {
          if (nw.state === 'installed' && navigator.serviceWorker.controller) emit('update-available', { reg });
        });
      });
    }).catch(() => {});
    // Reload only when an existing worker is replaced by an update. On the very first install the
    // new worker claims the page too (clients.claim) — reloading then wiped the sign-in form while
    // a technician was typing, so their first sign-in failed with an empty password.
    const hadController = !!navigator.serviceWorker.controller;
    let reloaded = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!hadController || reloaded) return; reloaded = true; location.reload();
    });
    navigator.serviceWorker.addEventListener('message', (e) => {
      const m = e.data || {};
      if (m.type === 'FLUSH_QUEUE') flush();
      if (m.type === 'NAVIGATE' && m.to) location.hash = m.to.replace(/^\/tech/, '') || '/dashboard';
    });
  }
  function applyUpdate() {
    navigator.serviceWorker.getRegistration().then((reg) => {
      if (reg && reg.waiting) reg.waiting.postMessage('SKIP_WAITING');
    });
  }

  // ── Install prompt ──
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferredPrompt = e; emit('installable', {}); });
  window.addEventListener('appinstalled', () => { deferredPrompt = null; emit('installed', {}); });
  async function promptInstall() {
    if (!deferredPrompt) return { outcome: 'unavailable' };
    deferredPrompt.prompt();
    const res = await deferredPrompt.userChoice;
    deferredPrompt = null;
    return res;
  }
  function canInstall() { return !!deferredPrompt; }

  // ── Push subscription ──
  function urlB64ToUint8(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const base = (b64 + pad).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base); return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }
  async function enablePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return { ok: false, reason: 'unsupported' };
    const tok = localStorage.getItem(TOKEN_KEY); if (!tok) return { ok: false, reason: 'no-auth' };
    const keyRes = await fetch('/api/push/vapid-public-key', { headers: { Authorization: 'Bearer ' + tok } }).then((r) => r.json()).catch(() => ({}));
    if (!keyRes.enabled || !keyRes.public_key) return { ok: false, reason: 'not-configured' };
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') return { ok: false, reason: 'denied' };
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8(keyRes.public_key) });
    const j = sub.toJSON();
    await fetch('/api/push/subscribe', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok },
      body: JSON.stringify({ endpoint: j.endpoint, keys: j.keys, user_agent: navigator.userAgent }),
    });
    return { ok: true };
  }

  window.addEventListener('online', () => { emit('connectivity', { online: true }); flush(); });
  window.addEventListener('offline', () => emit('connectivity', { online: false }));

  const RosPWA = {
    _syncing: false,
    on(evt, fn) { (listeners[evt] = listeners[evt] || []).push(fn); },
    register, applyUpdate, promptInstall, canInstall, enablePush,
    mutate, flush, status, pendingCount,
    getConflicts, resolveConflict,
  };
  window.RosPWA = RosPWA;
  register();
  if (navigator.onLine) setTimeout(flush, 1500);
})();
