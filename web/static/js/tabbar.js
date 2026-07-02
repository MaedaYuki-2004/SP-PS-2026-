/* SP-PS 共通ボトムタブバー（モバイル用・base.css の .app-tabbar と対）
   使い方: <script src="/static/js/tabbar.js" defer></script> を1行入れるだけ */
(function () {
  'use strict';

  var ICONS = {
    practice: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>',
    vowel:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 13.5c1 1.6 2.4 2.5 4 2.5s3-.9 4-2.5"/><circle cx="9" cy="9.5" r=".8" fill="currentColor" stroke="none"/><circle cx="15" cy="9.5" r=".8" fill="currentColor" stroke="none"/></svg>',
    history:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/></svg>',
    admin:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>'
  };

  var TABS = [
    { href: '/select',  label: '練習',  icon: ICONS.practice, match: [/^\/$/, /^\/select/, /^\/practice/] },
    { href: '/history', label: '記録',  icon: ICONS.history,  match: [/^\/history/, /^\/analysis/] },
    { href: '/admin',   label: '単語',  icon: ICONS.admin,    match: [/^\/admin/] }
  ];

  function isActive(tab, path) {
    return tab.match.some(function (re) { return re.test(path); });
  }

  function build() {
    var path = location.pathname;
    var nav = document.createElement('nav');
    nav.className = 'app-tabbar';
    nav.setAttribute('aria-label', 'メインナビゲーション');

    TABS.forEach(function (tab) {
      var a = document.createElement('a');
      a.className = 'app-tab-item' + (isActive(tab, path) ? ' active' : '');
      a.href = tab.href;
      a.innerHTML = tab.icon + '<span>' + tab.label + '</span>';
      nav.appendChild(a);
    });

    document.body.appendChild(nav);
    document.body.classList.add('has-tabbar');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
