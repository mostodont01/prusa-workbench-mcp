// Service worker: makes the viewer installable as an app (PWA) in Chrome/Edge. Everything comes from the viewer
// server (always fresh), except:
// - the page shell, cached only so the app can say the server can't be reached;
// - G-code (/files/<job>/<bed>.gcode?v=<version>): kept on the device, so a bed opened once loads instantly,
//   even over slow mobile data. ?v= changes whenever the G-code does, so a cached copy is never stale.
const SHELL = 'workbench-shell-v2';
const GCODE = 'workbench-gcode-v1';
const GCODE_KEEP = 12;   // beds kept on the device (the biggest are ~20 MB each)
const OFFLINE = ['/', 'style.css', 'icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(OFFLINE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== GCODE)
    .map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

async function gcode(request, url) {
  const cache = await caches.open(GCODE);
  const hit = await cache.match(request, { ignoreVary: true });
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) {
    const keep = res.clone();
    (async () => {
      // drop older versions of this bed, then the oldest beds beyond GCODE_KEEP
      for (const k of await cache.keys()) {
        if (new URL(k.url).pathname === url.pathname) await cache.delete(k);
      }
      await cache.put(request, keep);
      const keys = await cache.keys();
      for (const k of keys.slice(0, Math.max(0, keys.length - GCODE_KEEP))) await cache.delete(k);
    })().catch(() => {});   // storage full etc.: just not cached
  }
  return res;
}

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;   // CDN, Connect: browser default
  if (url.pathname.startsWith('/files/') && url.pathname.endsWith('.gcode') && url.searchParams.has('v')) {
    e.respondWith(gcode(e.request, url));
    return;
  }
  e.respondWith(fetch(e.request).catch(async () => {
    const hit = await caches.match(e.request, { ignoreSearch: true });
    if (hit) return hit;
    return new Response(JSON.stringify({ error: 'viewer server not running' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } });
  }));
});
