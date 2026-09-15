/* お手本とあなたの口を「ならべて再生」する部品
 *
 * 聞こえ方に頼らず発音を直すいちばんの手がかりは、先生の口と自分の口を
 * 同時に見くらべること。録音のあとの画面と、結果画面の「くらべる → 口の形」で使う。
 *
 * ・SPLipStore
 *     自分の口の動画は解析のためにサーバーへ送るが、解析が終わるとサーバーからは消える。
 *     結果画面でも見返せるよう、直前の1本だけをこの端末のブラウザ（IndexedDB）に置いておく。
 *     録音を始めた時点で消し（clear）、その録音で撮れたときだけ保存する。古いもの（2時間以上前）は使わない。
 *     消さずにおくと、口の動画を撮れなかった録音（カメラが使えない等）の結果画面に、
 *     前の録音（共有の端末ならほかの生徒）の動画が「あなた」として出てしまうため。
 * ・SPMouthCompare.mount(el, { refSrc, userSrc })
 *     el の中に「お手本｜あなた」の2つの動画と、ならべて再生・速さ切り替えを作る。
 */
(function () {
  'use strict';

  // ── 直前の自分の口の動画（この端末だけに保存） ──
  var DB = 'sp-ps-local', STORE = 'lip', MAX_AGE = 2 * 60 * 60 * 1000;
  function openDb() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) { reject(new Error('no indexedDB')); return; }
      var req = indexedDB.open(DB, 1);
      req.onupgradeneeded = function () { req.result.createObjectStore(STORE); };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }
  window.SPLipStore = {
    save: function (wordId, blob) {
      return openDb().then(function (db) {
        return new Promise(function (resolve) {
          var tx = db.transaction(STORE, 'readwrite');
          tx.objectStore(STORE).put({ wordId: String(wordId || ''), blob: blob, ts: Date.now() }, 'last');
          tx.oncomplete = function () { resolve(true); };
          tx.onerror = function () { resolve(false); };
        });
      }).catch(function () { return false; });
    },
    load: function (wordId) {
      return openDb().then(function (db) {
        return new Promise(function (resolve) {
          var req = db.transaction(STORE, 'readonly').objectStore(STORE).get('last');
          req.onsuccess = function () {
            var v = req.result;
            if (!v || !v.blob || v.wordId !== String(wordId || '') || Date.now() - v.ts > MAX_AGE) resolve(null);
            else resolve(v.blob);
          };
          req.onerror = function () { resolve(null); };
        });
      }).catch(function () { return null; });
    },
    clear: function () {
      return openDb().then(function (db) {
        return new Promise(function (resolve) {
          var tx = db.transaction(STORE, 'readwrite');
          tx.objectStore(STORE).delete('last');
          tx.oncomplete = function () { resolve(true); };
          tx.onerror = function () { resolve(false); };
        });
      }).catch(function () { return false; });
    }
  };

  // ── ならべて再生 ──
  var PLAY = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>';
  var STOP = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';

  function cell(src, label, cls) {
    var fig = document.createElement('figure');
    fig.className = 'mcmp-cell ' + cls;
    var v = document.createElement('video');
    v.className = 'mcmp-video';
    v.muted = true; v.playsInline = true; v.setAttribute('playsinline', '');
    v.preload = 'auto';
    v.src = src;
    var cap = document.createElement('figcaption');
    cap.className = 'mcmp-cap';
    cap.textContent = label;
    fig.appendChild(v); fig.appendChild(cap);
    return fig;
  }

  window.SPMouthCompare = {
    mount: function (el, opts) {
      if (!el || !opts || !opts.userSrc) return null;
      el.innerHTML = '';
      var root = document.createElement('div');
      root.className = 'mcmp';
      var pair = document.createElement('div');
      pair.className = 'mcmp-pair';
      var refFig = opts.refSrc ? cell(opts.refSrc, 'お手本', 'is-ref') : null;
      var youFig = cell(opts.userSrc, 'あなた', 'is-you');
      if (refFig) pair.appendChild(refFig);
      pair.appendChild(youFig);
      root.appendChild(pair);

      var ctrl = document.createElement('div');
      ctrl.className = 'mcmp-ctrl';
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'mcmp-play';
      var seg = document.createElement('div');
      seg.className = 'seg mcmp-rate';
      seg.innerHTML = '<button type="button" data-rate="1" class="active">ふつう</button><button type="button" data-rate="0.5">ゆっくり</button>';
      ctrl.appendChild(btn); ctrl.appendChild(seg);
      root.appendChild(ctrl);
      el.appendChild(root);

      var vids = function () { return Array.prototype.slice.call(root.querySelectorAll('video')); };
      var rate = 1, playing = false, played = false;
      function label() {
        var both = !!root.querySelector('.is-ref');
        btn.innerHTML = playing ? STOP + '<span>とめる</span>'
          : PLAY + '<span>' + (played ? 'もう一度' : (both ? 'ならべて再生' : '口の動きを再生')) + '</span>';
        root.classList.toggle('is-playing', playing);
      }
      function stopAll() {
        vids().forEach(function (v) { try { v.pause(); } catch (e) {} });
        playing = false; label();
      }
      function playAll() {
        var list = vids();
        list.forEach(function (v) { try { v.currentTime = 0; } catch (e) {} v.playbackRate = rate; });
        playing = true; played = true; label();
        list.forEach(function (v) { var p = v.play(); if (p && p.catch) p.catch(function () {}); });
      }
      btn.addEventListener('click', function () { if (playing) stopAll(); else playAll(); });
      seg.addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        seg.querySelectorAll('button').forEach(function (x) { x.classList.toggle('active', x === b); });
        rate = parseFloat(b.dataset.rate) || 1;
        vids().forEach(function (v) { v.playbackRate = rate; });
      });
      vids().forEach(function (v) {
        v.addEventListener('ended', function () {
          if (vids().every(function (x) { return x.ended || x.paused; })) { playing = false; label(); }
        });
      });
      // お手本の動画が無い・読めないときは、自分の口だけにする
      if (refFig) refFig.querySelector('video').addEventListener('error', function () { refFig.remove(); root.classList.add('is-single'); label(); });
      if (!refFig) root.classList.add('is-single');
      label();
      return { play: playAll, stop: stopAll };
    }
  };
})();
