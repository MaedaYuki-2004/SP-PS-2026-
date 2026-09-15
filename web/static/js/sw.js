// 見た目や共通スクリプトを大きく変えたときは番号を上げる（古い CSS・JS のキャッシュを捨てさせるため）
const CACHE = 'sp-ps-v3';
const PRECACHE = [
  '/static/css/base.css',
  '/static/js/theme.js',
  '/static/js/tabbar.js',
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

// キャッシュするのは /static/ の CSS・JS・画像だけ。
// 以前は画面（HTML）やお手本の音声・動画もキャッシュを先に返していたため、
//   ・練習した直後なのにホームの点数が古い
//   ・同じ端末で別の生徒がログインすると、前の人のホームが一瞬見える
//   ・先生モードを ON / OFF して読み込み直しても、ボタンの表示が変わらない
//   ・先生がお手本を録り直しても、古いお手本が流れる
// ということが起きうる作りだった。画面・音声・動画・API は毎回サーバーから取る。
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || !url.pathname.startsWith('/static/')) return;

  e.respondWith(
    caches.match(e.request).then(cached => {
      const net = fetch(e.request).then(res => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      });
      return cached || net;
    })
  );
});
