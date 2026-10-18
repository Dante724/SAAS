/* Tekly Go — service worker, scoped to /go/.
   Strategy (same shape as the /tech worker in frontend/sw.js):
   - Go pages (navigations under /go/): network first, then the cached shell, so the app opens offline.
   - Built assets (/assets/*): cache first. Vite names carry a content hash, so they never go stale.
   - Icons: stale-while-revalidate. (Fonts are cross-origin and left to the browser's HTTP cache.)
   - A short list of API reads the phone needs offline (today's jobs, a job, statuses, who am I):
     network first with the last good answer as a fallback. Everything else goes to the network
     untouched, and the cache is wiped on sign-out (CLEAR_DATA) so shop data doesn't outlive a session.
   - Writes are never cached: the page queues them (offlineQueue.ts) and replays them.
   - Background Sync: on 'tekly-go-sync', tell open pages to flush their queue. */
const VERSION = 'tekly-go-v1';
const SHELL_CACHE = `go-shell-${VERSION}`;
const ASSET_CACHE = `go-assets-${VERSION}`;
const API_CACHE = `go-api-${VERSION}`;
const SHELL = ['/go/', '/go/manifest.webmanifest', '/static/icons/icon-192.png', '/static/icons/tekly-mark.svg'];

// API reads worth keeping for offline use. Anything not listed is never cached.
const API_ALLOW = [
  /^\/api\/auth\/me$/,
  /^\/api\/tech\/dashboard$/,
  /^\/api\/orders\/board$/,
  /^\/api\/orders\/[^/]+$/,
  /^\/api\/statuses(\/allowed\/[^/]+)?$/,
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
    const keep = new Set([SHELL_CACHE, ASSET_CACHE, API_CACHE]);
    for (const k of await caches.keys()) if (k.startsWith('go-') && !keep.has(k)) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('message', (e) => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
  if (e.data === 'CLEAR_DATA') e.waitUntil(caches.delete(API_CACHE));
});

async function networkFirst(req, cacheName, fallbackUrl) {
  try {
    const res = await fetch(req);
    if (res.ok) (await caches.open(cacheName)).put(fallbackUrl || req, res.clone());
    return res;
  } catch {
    const cached = await caches.match(fallbackUrl || req);
    if (cached) return cached;
    if (fallbackUrl) return new Response('<h1>Offline</h1>', { status: 503, headers: { 'Content-Type': 'text/html' } });
    return new Response(JSON.stringify({ offline: true, detail: "You're offline and this isn't saved on the phone yet." }),
      { status: 503, headers: { 'Content-Type': 'application/json' } });
  }
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || req.method !== 'GET') return;

  if (req.mode === 'navigate') {
    if (url.pathname === '/go' || url.pathname.startsWith('/go/')) e.respondWith(networkFirst(req, SHELL_CACHE, '/go/'));
    return;
  }

  if (url.pathname.startsWith('/assets/')) {
    e.respondWith((async () => {
      const cached = await caches.match(req);
      if (cached) return cached;
      const res = await fetch(req);
      if (res.ok) (await caches.open(ASSET_CACHE)).put(req, res.clone());
      return res;
    })());
    return;
  }

  if (url.pathname.startsWith('/static/icons/')) {
    e.respondWith((async () => {
      const cached = await caches.match(req);
      const fresh = fetch(req).then(async (res) => {
        if (res.ok) (await caches.open(ASSET_CACHE)).put(req, res.clone());
        return res;
      }).catch(() => cached);
      return cached || fresh;
    })());
    return;
  }

  if (url.pathname.startsWith('/api/') && API_ALLOW.some((re) => re.test(url.pathname))) {
    e.respondWith(networkFirst(req, API_CACHE));
  }
});

self.addEventListener('sync', (e) => {
  if (e.tag === 'tekly-go-sync') {
    e.waitUntil((async () => {
      for (const c of await self.clients.matchAll({ includeUncontrolled: true })) c.postMessage({ type: 'FLUSH_QUEUE' });
    })());
  }
});
