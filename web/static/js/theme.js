/* SP-PS 共通の土台（全ページの <head> で最初に読み込む）
 *
 * ・テーマ（明るい／暗い）
 *     以前は各ページに同じテーマ切り替えスクリプトとボタンが複製されていて、
 *     保存が無いと必ずダークになっていた。ここに一本化し、既定は端末の設定に合わせる。
 *     保存値 sp-ps-theme : 'light' | 'dark' | 'auto'（未保存は auto 扱い）
 * ・先生モード
 *     研究用の数値・管理用の操作は、先生モードのときだけ出す。
 *     先生モードかどうかはサーバーのセッションで決まり（先生用パスワードで ON）、
 *     サーバーが <html class="teacher-mode"> を付けて返す。ここではそれを読むだけ。
 *     以前は端末の保存値（sp-ps-teacher-mode）で決めていたため、生徒が設定から ON にできた。
 * ・振動（SPHaptics）
 *     音が聞こえにくい人にとって「触ってわかる合図」は、画面を見ていなくても伝わる手がかり。
 *     録音の開始・停止やカウントダウンで短く震わせる（対応端末のみ。iPhone の Safari は非対応）。
 *     保存値 sp-ps-haptics : 'on' | 'off'（未保存は on）
 */
(function () {
  'use strict';
  var root = document.documentElement;

  function read(key, fallback) {
    try { var v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) {}
  }

  // ── テーマ ──
  var KEY = 'sp-ps-theme';
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function saved() { return read(KEY, 'auto'); }
  function apply(mode) {
    var dark = mode === 'dark' || (mode === 'auto' && mq && mq.matches);
    if (dark) root.setAttribute('data-theme', 'dark');
    else root.removeAttribute('data-theme');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', dark ? '#0B0B12' : '#F4F4F9');
  }
  apply(saved());
  if (mq) {
    var onChange = function () { if (saved() === 'auto') apply('auto'); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
  window.SPTheme = {
    get: saved,
    set: function (mode) { write(KEY, mode); apply(mode); }
  };

  // ── 先生モード ──
  // 以前の保存値は使わないので消しておく（残っていても意味はないが、紛らわしいため）
  try { localStorage.removeItem('sp-ps-teacher-mode'); } catch (e) {}
  var teacherOn = root.classList.contains('teacher-mode');
  if (teacherOn) {
    if (document.body) document.body.classList.add('teacher-mode');
    else document.addEventListener('DOMContentLoaded', function () { document.body.classList.add('teacher-mode'); });
  }
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        data.ok = res.ok && data.ok !== false;
        return data;
      });
    });
  }
  window.SPTeacher = {
    get: function () { return teacherOn; },
    // 先生用パスワードで ON にする。結果は { ok, error } で返す（ON にできたら画面を読み込み直す）
    unlock: function (password) { return postJSON('/teacher/unlock', { password: password }); },
    lock: function () { return postJSON('/teacher/lock'); }
  };

  // ── 振動 ──
  var HKEY = 'sp-ps-haptics';
  window.SPHaptics = {
    supported: function () { return typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function'; },
    enabled: function () { return read(HKEY, 'on') !== 'off'; },
    set: function (on) { write(HKEY, on ? 'on' : 'off'); },
    buzz: function (pattern) {
      if (!this.enabled() || !this.supported()) return;
      try { navigator.vibrate(pattern); } catch (e) {}
    },
    tick:    function () { this.buzz(35); },              // カウントダウンの 3・2・1
    start:   function () { this.buzz(140); },             // 録音がはじまった
    stop:    function () { this.buzz([50, 70, 50]); },    // 録音がおわった
    success: function () { this.buzz([40, 60, 40, 60, 90]); },
    nope:    function () { this.buzz([90, 60, 90]); }
  };

  // ── 数字のカウントアップ・リングの伸び ──
  // 点数が「0 から増えて止まる」動きは、音の効果音の代わりに「結果が出た」ことを伝える。
  //   <b data-count-up="72">72</b>        … 0 から 72 まで数える（HTML には最終値を書いておく）
  //   <span class="ring" data-ring="72">  … リングを 0 から 72% まで伸ばす
  // 視差効果を減らす設定の端末では、最初から最終値で表示する。
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function countUp(el) {
    var to = parseFloat(el.getAttribute('data-count-up'));
    if (isNaN(to) || reduce) return;
    var dur = 900, t0 = null;
    el.textContent = '0';
    function step(ts) {
      if (t0 === null) t0 = ts;
      var k = Math.min(1, (ts - t0) / dur);
      var eased = 1 - Math.pow(1 - k, 3);
      el.textContent = String(Math.round(to * eased));
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  function growRing(el) {
    var to = el.getAttribute('data-ring');
    if (to === null) return;
    if (reduce) { el.style.setProperty('--p', to); return; }
    el.style.setProperty('--p', '0');
    // 1フレーム待ってから最終値を入れると、CSS の transition で伸びる
    requestAnimationFrame(function () { requestAnimationFrame(function () { el.style.setProperty('--p', to); }); });
  }
  window.SPMotion = { countUp: countUp, growRing: growRing };
  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(document.querySelectorAll('[data-count-up]'), countUp);
    Array.prototype.forEach.call(document.querySelectorAll('[data-ring]'), growRing);
  });
})();
