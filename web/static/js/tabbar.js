/* SP-PS 共通ボトムタブバー（base.css の .app-tabbar と対）
   使い方: <script src="/static/js/tabbar.js" defer></script> を1行入れるだけ

   「記録」と「分析」は同じ /history の中の2つの面だが、生徒がいちばん見たい
   「自分がどうなっているか」への入口なので、どちらもタブに直接置いて1タップで開けるようにする。
   /history#log と /history#analysis はページを読み込み直さずに切り替わる（history.html が hashchange を見る）。 */
(function () {
  'use strict';

  var SVG = function (paths) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>';
  };
  var ICONS = {
    home:     SVG('<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"/>'),
    log:      SVG('<rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
    insight:  SVG('<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>'),
    admin:    SVG('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>')
  };

  // 「先生」は参加者アカウントの管理（登録・パスワードのリセット・削除・配布用QR）を行う画面。
  // 単語とお手本は参加者ごとに本人が管理するので（core/library.py）、先生のタブはアカウント管理へつなぐ。
  // 先生モード（先生用パスワードで ON にしたセッション）のときだけ出す。
  // 判定はサーバーが <html class="teacher-mode"> で渡す。
  var isTeacher = document.documentElement.classList.contains('teacher-mode');

  var onHistory = function (p) { return /^\/history/.test(p); };
  var TABS = [
    { href: '/select', label: 'ホーム', icon: ICONS.home,
      active: function (p) { return /^\/$/.test(p) || /^\/select/.test(p) || /^\/practice/.test(p); } },
    { href: '/history#log', label: '記録', icon: ICONS.log,
      active: function (p, h) { return onHistory(p) && h !== '#analysis'; } },
    { href: '/history#analysis', label: '分析', icon: ICONS.insight,
      active: function (p, h) { return onHistory(p) && h === '#analysis'; } }
  ];
  if (isTeacher) {
    TABS.push({ href: '/admin/accounts', label: '先生', icon: ICONS.admin,
      active: function (p) { return /^\/admin\/accounts/.test(p); } });
  }

  var nav = null;

  function refresh() {
    if (!nav) return;
    var p = location.pathname, h = location.hash;
    Array.prototype.forEach.call(nav.children, function (a, i) {
      var on = TABS[i].active(p, h);
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }

  function build() {
    nav = document.createElement('nav');
    nav.className = 'app-tabbar';
    nav.setAttribute('aria-label', 'メインナビゲーション');
    TABS.forEach(function (tab) {
      var a = document.createElement('a');
      a.className = 'app-tab-item';
      a.href = tab.href;
      a.innerHTML = tab.icon + '<span>' + tab.label + '</span>';
      nav.appendChild(a);
    });
    document.body.appendChild(nav);
    document.body.classList.add('has-tabbar');
    refresh();
  }

  window.addEventListener('hashchange', refresh);
  window.SPTabbar = { refresh: refresh };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
