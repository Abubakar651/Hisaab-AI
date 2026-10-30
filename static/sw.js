/* HisaabAI service worker.
 * - App shell (HTML + icons) is cached so the app opens instantly / offline.
 * - /api/* is NEVER cached: ledger numbers must always come live from SQLite.
 */
const CACHE = 'hisaabai-shell-v1';
const SHELL = ['/', '/manifest.webmanifest', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) {
    return; // let the network handle it
  }

  // Network-first for the page so updates show up; fall back to cache when offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put('/', copy));
          return res;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  // Cache-first for static assets.
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});

