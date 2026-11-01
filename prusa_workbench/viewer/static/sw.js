// Service worker: makes the viewer installable as an app (PWA) in Chrome/Edge. Everything comes from the local
// viewer server (always fresh); the page shell is cached only so the app window can say the server isn't running.
const SHELL = 'workbench-shell-v1';
const OFFLINE = ['/', 'style.css', 'icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(OFFLINE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;   // CDN, Connect: browser default
  e.respondWith(fetch(e.request).catch(async () => {
    const hit = await caches.match(e.request, { ignoreSearch: true });
    if (hit) return hit;
    return new Response(JSON.stringify({ error: 'viewer server not running' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } });
  }));
});
