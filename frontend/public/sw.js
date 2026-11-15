// NetWatch service worker: lets the app be installed and opens its shell without a connection.
// It never stores API answers (/api/), so nothing private or stale is kept on the phone.
const VERSION = 'v1';
const SHELL = `netwatch-shell-${VERSION}`;
const ASSETS = `netwatch-assets-${VERSION}`;
const MAX_ASSETS = 80; // old versions' files are dropped, oldest first

self.addEventListener('install', (event) => {
  // Keep the page that is open on the old version; the new one takes over on the next launch.
  event.waitUntil(caches.open(SHELL).then((c) => c.add(new Request('/', { cache: 'reload' }))));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL && k !== ASSETS).map((k) => caches.delete(k))),
    ),
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') return;

  if (req.mode === 'navigate') {
    // Network first, so a new version and fresh sign-in state always win; the saved shell is
    // only for when NetWatch cannot be reached.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL).then((c) => c.put('/', copy));
          }
          return res;
        })
        .catch(() => caches.match('/', { cacheName: SHELL }).then((r) => r || Response.error())),
    );
    return;
  }

  if (url.pathname.startsWith('/assets/')) {
    // File names carry a content hash, so a saved copy is always correct.
    event.respondWith(
      caches.open(ASSETS).then((c) =>
        c.match(req).then(
          (hit) =>
            hit ||
            fetch(req).then((res) => {
              if (res.ok) {
                c.put(req, res.clone()).then(() => trim(c));
              }
              return res;
            }),
        ),
      ),
    );
  }
});

async function trim(cache) {
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_ASSETS)).map((k) => cache.delete(k)));
}
