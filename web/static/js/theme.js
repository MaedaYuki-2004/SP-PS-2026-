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
 * ・ページの移動のアニメーション（base.css の @view-transition）
 *     移動が速く続いたときなどにブラウザがアニメーションを飛ばすと、「Transition was skipped」という
 *     エラーが記録される。移動そのものは普通に終わるので、受け取って捨てる。
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

  // ── ページの移動のアニメーション ──
  // 飛ばされたときに失敗で終わる約束（ready など）を受け取っておく。受け取らないと、
  // だれも受け取らなかったエラーとして記録される（見た目には何も起きない）
  function quietSkippedTransition(e) {
    var vt = e.viewTransition;
    if (!vt) return;
    ['ready', 'finished', 'updateCallbackDone'].forEach(function (k) {
      if (vt[k] && vt[k].catch) vt[k].catch(function () {});
    });
  }
  window.addEventListener('pageswap', quietSkippedTransition);     // 古いページの側
  window.addEventListener('pagereveal', quietSkippedTransition);   // 新しいページの側
  // それでも、上で受け取る前に約束が失敗で終わることがまれにある（全画面の確認で 70 回に 1 回ほど）。
  // この知らせだけは、だれも受け取らなかったエラーとして記録しない
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    if (r && r.name === 'AbortError' && /transition was skipped/i.test(String(r.message || ''))) e.preventDefault();
  });

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

  // ── 短い知らせ（トースト） ──
  // 以前は通信のエラーなどをブラウザの alert() で出していた。alert() は画面全体を止め、
  // 押すまで何も操作できず、文も選べない。画面の下に数秒だけ出す。
  //   SPToast.show('削除できませんでした：…', { kind: 'error' })   kind: 'error' | 'info'（既定）
  // 見た目はここに入れる（base.css は Service Worker が古い控えを返すことがあるため）。
  var toastBox = null;
  function toastStyle() {
    if (document.getElementById('spToastCss')) return;
    var st = document.createElement('style');
    st.id = 'spToastCss';
    st.textContent =
      '.sp-toast-box{position:fixed;left:50%;transform:translateX(-50%);z-index:2147483100;width:min(420px,calc(100vw - 24px));' +
      'bottom:calc(88px + env(safe-area-inset-bottom,0px));display:flex;flex-direction:column;gap:8px;pointer-events:none;}' +
      '.sp-toast{pointer-events:auto;display:flex;align-items:flex-start;gap:10px;padding:13px 14px 13px 16px;border-radius:16px;' +
      'background:var(--text,#13121A);color:var(--bg-card,#fff);font-family:var(--font,system-ui);font-size:14.5px;line-height:1.6;' +
      'box-shadow:0 14px 34px -12px rgba(0,0,0,.45);animation:sp-toast-in .22s ease-out;}' +
      '.sp-toast.error{background:var(--danger,#B93D35);color:#fff;}' +
      '.sp-toast span{flex:1;min-width:0;overflow-wrap:anywhere;}' +
      '.sp-toast button{flex:none;min-width:40px;min-height:32px;margin:-4px -6px -4px 0;border:none;border-radius:10px;background:transparent;' +
      'color:inherit;font:inherit;font-weight:700;cursor:pointer;opacity:.85;}' +
      '@keyframes sp-toast-in{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:none;}}' +
      '@media (prefers-reduced-motion: reduce){.sp-toast{animation:none;}}';
    document.head.appendChild(st);
  }
  function showToast(text, opts) {
    opts = opts || {};
    toastStyle();
    if (!toastBox || !document.body.contains(toastBox)) {
      toastBox = document.createElement('div');
      toastBox.className = 'sp-toast-box';
      toastBox.setAttribute('role', 'status');
      toastBox.setAttribute('aria-live', 'polite');
      document.body.appendChild(toastBox);
    }
    var t = document.createElement('div');
    t.className = 'sp-toast' + (opts.kind === 'error' ? ' error' : '');
    var msg = document.createElement('span');
    msg.textContent = text;
    var close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.setAttribute('aria-label', 'とじる');
    t.appendChild(msg);
    t.appendChild(close);
    toastBox.appendChild(t);
    var timer = setTimeout(remove, opts.ms || (opts.kind === 'error' ? 7000 : 4500));
    function remove() { clearTimeout(timer); if (t.parentNode) t.parentNode.removeChild(t); }
    close.addEventListener('click', remove);
    return remove;
  }
  window.SPToast = { show: showToast };
})();
