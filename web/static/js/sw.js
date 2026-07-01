const CACHE = 'sp-ps-v1';
const PRECACHE = [
  '/select',
  '/static/css/base.css',
  '/static/js/main.js',
  '/static/js/audio_recorder.js',
  '/static/manifest.json',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  // API・音声ファイルはキャッシュしない
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/graph') ||
      url.pathname.endsWith('.wav') || url.pathname.endsWith('.webm')) return;

  e.respondWith(
    caches.match(e.request).then(cached => {
      const net = fetch(e.request).then(res => {
        if (res.ok && url.origin === location.origin) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      });
      return cached || net;
    })
  );
});
