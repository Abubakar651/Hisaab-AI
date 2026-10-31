/* HisaabAI service worker.
 * - App shell (HTML + CSS + JS + icons) is cached so the app opens instantly / offline.
 * - /api/* is NEVER cached: ledger numbers must always come live from SQLite.
 * Upgrades from hisaabai-shell-v1 to hisaabai-shell-v3 to ensure immediate script updates.
 */
const CACHE = 'hisaabai-shell-v4';
const SHELL = [
  '/',
  '/manifest.webmanifest',
  '/static/theme.css',
  '/static/auth.js',
  '/static/app.js',
  '/static/vendor/firebase/firebase-app-compat.js',
  '/static/vendor/firebase/firebase-auth-compat.js',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
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

  // Network-first for script and style assets so frontend updates apply immediately
  if (url.pathname.endsWith('.js') || url.pathname.endsWith('.css')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // Cache-first for images, fonts, and manifest icons.
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
