/* RepairOS Technician — Service Worker
   Strategy:
   - App shell: precache, cache-first (instant launch, offline-capable).
   - Static assets (/static): stale-while-revalidate.
   - API GET (/api): network-first with cache fallback (fresh when online,
     last-known when offline — this is how assigned jobs open offline).
   - API writes: never cached; the page queues them in IndexedDB and replays.
   - Navigation: try network, fall back to cached shell, then offline.html.
   - Push + notificationclick: technician notifications with deep links.
   - Background Sync: on 'sync', tell open pages to flush their offline queue.
   The worker is registered at "/" (push needs it for the whole site), but the caching above is
   for the /tech app only: requests from the main app's pages go straight to the network, so
   its data (clients, prices, costs) never lands in a cache that outlives sign-out.
*/
const VERSION = 'ros-tech-v4';   // v4: caching limited to /tech (activating clears v3's caches)
const SHELL_CACHE = `shell-${VERSION}`;
const STATIC_CACHE = `static-${VERSION}`;
const API_CACHE = `api-${VERSION}`;

const SHELL = [
  '/tech',
  '/offline.html',
  '/manifest.webmanifest',
  '/static/css/design.css',
  '/static/js/core.js',
  '/static/js/icons.js',
  '/static/js/pwa.js',
  '/static/js/tech/app.js',
  '/static/js/tech/dashboard.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL_CACHE);
    await Promise.all(SHELL.map((u) => c.add(u).catch(() => null)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const keep = new Set([SHELL_CACHE, STATIC_CACHE, API_CACHE]);
    for (const k of await caches.keys()) if (!keep.has(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

// Allow the page to trigger an immediate update.
self.addEventListener('message', (e) => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});

function isApiGet(req, url) {
  return req.method === 'GET' && url.pathname.startsWith('/api/');
}

const isTechPath = (path) => path === '/tech' || path.startsWith('/tech/') || path === '/offline.html';

// Was this request made by a /tech page? (Main-app pages are left to the network.)
async function fromTechApp(e) {
  const id = e.clientId || e.resultingClientId;
  const c = id ? await self.clients.get(id) : null;
  return !!c && isTechPath(new URL(c.url).pathname);
}

// Technician-app links ("/tech#/order/<id>") opened from the main app go to the same job there.
function mainAppPath(link) {
  const m = /^\/tech\/?#\/order\/([^/?#]+)/.exec(link || '');
  if (m) return '/orders?open=' + encodeURIComponent(m[1]);
  return /^\/tech\/?(#.*)?$/.test(link || '') ? '/my-work' : (link || '/');
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;          // pass-through cross-origin
  if (req.method !== 'GET') return;                          // writes handled by the page queue

  // Navigations -> network, then cached shell, then offline page (technician app only).
  if (req.mode === 'navigate') {
    if (!isTechPath(url.pathname)) return;
    e.respondWith((async () => {
      try { return await fetch(req); }
      catch {
        return (await caches.match('/tech')) || (await caches.match('/offline.html'));
      }
    })());
    return;
  }

  // API GET -> network-first, fall back to cache.
  if (isApiGet(req, url)) {
    e.respondWith((async () => {
      if (!(await fromTechApp(e))) return fetch(req);
      try {
        const res = await fetch(req);
        const c = await caches.open(API_CACHE);
        c.put(req, res.clone());
        return res;
      } catch {
        const cached = await caches.match(req);
        if (cached) return cached;
        return new Response(JSON.stringify({ offline: true, detail: 'No cached data' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } });
      }
    })());
    return;
  }

  // Static -> stale-while-revalidate.
  if (url.pathname.startsWith('/static/')) {
    e.respondWith((async () => {
      if (!(await fromTechApp(e))) return fetch(req);
      const cached = await caches.match(req);
      const fetching = fetch(req).then((res) => {
        caches.open(STATIC_CACHE).then((c) => c.put(req, res.clone()));
        return res;
      }).catch(() => cached);
      return cached || fetching;
    })());
  }
});

// ── Background Sync: ask any open client to flush its offline queue ──
self.addEventListener('sync', (e) => {
  if (e.tag === 'ros-sync') {
    e.waitUntil((async () => {
      const cs = await self.clients.matchAll({ includeUncontrolled: true });
      for (const client of cs) client.postMessage({ type: 'FLUSH_QUEUE' });
    })());
  }
});

// ── Push notifications ──
self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { title: 'RepairOS', body: e.data && e.data.text() }; }
  const title = data.title || 'RepairOS';
  const options = {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: data.kind || 'ros',
    data: { deep_link: data.deep_link || '/tech', id: data.id || null },
    vibrate: [60, 30, 60],
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.deep_link) || '/tech';
  e.waitUntil((async () => {
    const cs = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of cs) {
      if (c.url.includes('/tech') && 'focus' in c) { c.focus(); c.postMessage({ type: 'NAVIGATE', to: target }); return; }
    }
    // No technician app open: use the main app if it's open, at the same job.
    for (const c of cs) {
      if ('focus' in c && 'navigate' in c) { await c.focus(); return c.navigate(mainAppPath(target)); }
    }
    if (self.clients.openWindow) return self.clients.openWindow(target);
  })());
});
