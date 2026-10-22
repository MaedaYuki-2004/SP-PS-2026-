/* SP-PS はじめてのチュートリアル（ホーム・録音ページ・結果ページで読み込む）
 *
 * 説明の絵を見せるだけでなく、本物の操作で
 *   単語の登録（お手本の録画）→ その単語で録音 → 結果を見る
 * を1回通してもらう。次に押すボタンだけを明るくして、吹き出しで案内する。ほかの場所は押せない
 * （「やめる」でいつでも抜けられる）。
 *
 * ・今どの手順かは、画面の状態（設定が開いている・単語の追加のどの段階か・録音ページのどの段階か）から
 *   そのつど決める。途中で戻ったり読み込み直したりしても、案内がずれない。
 * ・進み具合は sessionStorage（このタブの中だけ）に持つ。共有の端末で、次の人に引き継がれない。
 * ・はじめての人に自動で出したかどうかは、利用者 ID ごとに localStorage に持つ。
 * ・見た目（CSS）もこのファイルに入れる。base.css は Service Worker が古い控えを返すことがあり、
 *   更新した直後に見た目だけ古くなるのを避けるため。
 */
(function () {
  'use strict';

  var KEY_STATE = 'sp-ps-tour';
  var KEY_SEEN = 'sp-ps-tour-seen:';
  var MAX_AGE_MS = 3 * 60 * 60 * 1000;
  var TOTAL = 12;
  var EXAMPLES = [['電話', 'でんわ'], ['学校', 'がっこう'], ['時計', 'とけい'], ['電車', 'でんしゃ'], ['仕事', 'しごと']];

  // ── 進み具合 ──
  function load() {
    try {
      var s = JSON.parse(sessionStorage.getItem(KEY_STATE) || 'null');
      if (s && Date.now() - (s.t || 0) < MAX_AGE_MS) return s;
    } catch (e) {}
    return null;
  }
  function save(s) { s.t = Date.now(); try { sessionStorage.setItem(KEY_STATE, JSON.stringify(s)); } catch (e) {} }
  function clearState() { try { sessionStorage.removeItem(KEY_STATE); } catch (e) {} }
  function seen(user) { try { return localStorage.getItem(KEY_SEEN + (user || '')) === '1'; } catch (e) { return true; } }
  function markSeen(user) { try { localStorage.setItem(KEY_SEEN + (user || ''), '1'); } catch (e) {} }

  function $(id) { return document.getElementById(id); }
  function shown(el) {
    if (!el || el.hidden) return false;
    var r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    var cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
  }
  function cssEscape(v) { return window.CSS && CSS.escape ? CSS.escape(v) : String(v).replace(/["\\]/g, '\\$&'); }

  // ── 見た目 ──
  var CSS_TEXT = [
    '.tour-block{position:fixed;z-index:2147483000;background:transparent;pointer-events:auto;touch-action:none;}',
    '.tour-spot{position:fixed;z-index:2147483001;pointer-events:none;border-radius:16px;',
    '  box-shadow:0 0 0 200vmax rgba(14,12,34,.62);transition:left .25s var(--ease,ease),top .25s var(--ease,ease),width .25s var(--ease,ease),height .25s var(--ease,ease);}',
    '.tour-spot.light{box-shadow:0 0 0 200vmax rgba(14,12,34,.28);}',
    '.tour-ring{position:fixed;z-index:2147483002;pointer-events:none;border-radius:16px;',
    '  box-shadow:0 0 0 3px #fff,0 0 0 6px var(--brand-500,#7264F3);animation:tour-pulse 1.6s ease-in-out infinite;}',
    '@keyframes tour-pulse{0%,100%{box-shadow:0 0 0 3px #fff,0 0 0 6px var(--brand-500,#7264F3),0 0 0 6px rgba(114,100,243,.45);}',
    '  50%{box-shadow:0 0 0 3px #fff,0 0 0 6px var(--brand-500,#7264F3),0 0 0 16px rgba(114,100,243,0);}}',
    '.tour-bubble{position:fixed;z-index:2147483003;width:min(340px,calc(100vw - 24px));box-sizing:border-box;',
    '  background:var(--bg-card,#fff);color:var(--text,#13121A);border-radius:var(--r-lg,24px);padding:16px 18px 14px;',
    '  box-shadow:0 18px 50px -12px rgba(14,12,34,.55);font-family:var(--font,system-ui);}',
    '.tour-bubble::before{content:"";position:absolute;left:var(--tail-x,50%);width:16px;height:16px;margin-left:-8px;',
    '  background:inherit;transform:rotate(45deg);border-radius:3px;}',
    '.tour-bubble.below::before{top:-7px;}',
    '.tour-bubble.above::before{bottom:-7px;}',
    '.tour-bubble.notail::before{display:none;}',
    '.tour-step{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:var(--navy,#5B4BEA);margin-bottom:6px;letter-spacing:.04em;}',
    '.tour-bar{flex:1;height:5px;border-radius:99px;background:var(--navy-soft,#E9E6FF);overflow:hidden;}',
    '.tour-bar i{display:block;height:100%;background:var(--grad-brand,#5B4BEA);border-radius:inherit;}',
    '.tour-title{font-size:17px;font-weight:800;line-height:1.45;margin:0 0 4px;}',
    '.tour-text{font-size:15px;line-height:1.75;margin:0;color:var(--text-2,#4A4959);}',
    '.tour-text b{color:var(--text,#13121A);}',
    '.tour-note{margin:10px 0 0;padding:9px 12px;border-radius:12px;background:var(--mid-bg,#FCF1DD);color:var(--mid-text,#945F0B);font-size:13px;line-height:1.65;}',
    '.tour-actions{display:flex;align-items:center;gap:10px;margin-top:12px;}',
    '.tour-btn{flex:1;min-height:46px;border:none;border-radius:14px;font-family:inherit;font-size:15.5px;font-weight:700;cursor:pointer;',
    '  background:var(--grad-brand,#5B4BEA);color:#fff;box-shadow:var(--shadow-brand,none);}',
    '.tour-btn.sub{flex:0 0 auto;background:var(--bg-muted,#EEEEF5);color:var(--text-2,#4A4959);box-shadow:none;padding:0 16px;}',
    '.tour-quit{flex:0 0 auto;margin-left:auto;min-height:40px;padding:0 6px;border:none;background:none;font-family:inherit;',
    '  font-size:13px;font-weight:700;color:var(--text-3,#6A697B);cursor:pointer;text-decoration:underline;text-underline-offset:3px;}',
    '.tour-card-wrap{position:fixed;inset:0;z-index:2147483004;display:grid;place-items:center;padding:16px;background:rgba(14,12,34,.62);}',
    '.tour-card{width:min(400px,100%);box-sizing:border-box;background:var(--bg-card,#fff);color:var(--text,#13121A);border-radius:var(--r-xl,30px);',
    '  padding:26px 22px 20px;box-shadow:0 30px 80px -20px rgba(14,12,34,.6);font-family:var(--font,system-ui);text-align:center;}',
    '.tour-card-icon{width:68px;height:68px;margin:0 auto 12px;border-radius:22px;display:grid;place-items:center;background:var(--grad-brand,#5B4BEA);color:#fff;box-shadow:var(--shadow-brand,none);}',
    '.tour-card h2{font-size:21px;font-weight:800;margin:0 0 8px;line-height:1.4;}',
    '.tour-card p{font-size:15px;line-height:1.75;margin:0;color:var(--text-2,#4A4959);}',
    '.tour-card .tour-note{text-align:left;}',
    '.tour-flow{display:flex;justify-content:center;align-items:center;gap:6px;margin:14px 0 4px;font-size:13px;font-weight:700;color:var(--navy,#5B4BEA);flex-wrap:wrap;}',
    '.tour-flow span{padding:5px 10px;border-radius:99px;background:var(--navy-soft,#E9E6FF);}',
    '.tour-card .tour-actions{flex-direction:column;gap:8px;margin-top:16px;}',
    '.tour-card .tour-btn{width:100%;flex:none;}',
    '.tour-card .tour-btn.sub{width:100%;min-height:44px;}',
    '.tour-card .tour-foot{margin-top:8px;font-size:12.5px;color:var(--text-3,#6A697B);}',
    '.tour-toast{position:fixed;left:50%;bottom:calc(84px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);z-index:2147483005;',
    '  width:min(360px,calc(100vw - 24px));box-sizing:border-box;padding:12px 16px;border-radius:14px;background:var(--text,#13121A);color:var(--bg-card,#fff);',
    '  font-family:var(--font,system-ui);font-size:14px;line-height:1.6;box-shadow:0 12px 30px -10px rgba(0,0,0,.4);}',
    'body.tour-active #awAddAnotherBtn{display:none !important;}',
    // 単語の追加の画面は上に寄せ、下にあけたところに吹き出しを出す（カメラや入力欄に重ねないため）
    'body.tour-active .aw-modal-overlay.open{align-items:flex-start;padding-top:calc(10px + env(safe-area-inset-top,0px));}',
    '.tour-step .tour-quit{margin-left:4px;min-height:32px;font-size:12px;}',
    '@media (prefers-reduced-motion: reduce){.tour-ring{animation:none;}.tour-spot{transition:none;}}'
  ].join('\n');

  function injectCss() {
    if ($('tourCss')) return;
    var st = document.createElement('style');
    st.id = 'tourCss';
    st.textContent = CSS_TEXT;
    document.head.appendChild(st);
  }

  function make(tag, cls, parent) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (parent) parent.appendChild(el);
    return el;
  }

  // 文の部品：文字列はそのまま、{b: '…'} は太字。利用者が入れた単語も textContent で入れる
  function fill(el, parts) {
    el.replaceChildren();
    (parts || []).forEach(function (p) {
      if (typeof p === 'string') el.appendChild(document.createTextNode(p));
      else if (p && p.b != null) { var b = make('b', '', el); b.textContent = p.b; }
    });
  }

  var ICON_PLAY = '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><path d="M12 18v4"/></svg>';
  var ICON_DONE = '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

  // ── 画面に出す部品 ──
  var ui = null;
  function buildUi() {
    if (ui) return ui;
    injectCss();
    ui = {
      blocks: [0, 1, 2, 3].map(function () { var b = make('div', 'tour-block', document.body); b.hidden = true; return b; }),
      spot: make('div', 'tour-spot', document.body),
      ring: make('div', 'tour-ring', document.body),
      bubble: make('div', 'tour-bubble', document.body)
    };
    ui.spot.hidden = ui.ring.hidden = ui.bubble.hidden = true;
    ui.bubble.setAttribute('role', 'dialog');
    ui.bubble.setAttribute('aria-live', 'polite');
    var head = make('div', 'tour-step', ui.bubble);
    ui.stepLabel = make('span', '', head);
    var bar = make('span', 'tour-bar', head);
    ui.barFill = make('i', '', bar);
    var q = make('button', 'tour-quit', head);
    q.type = 'button';
    q.textContent = 'やめる';
    q.setAttribute('aria-label', 'チュートリアルをやめる');
    q.addEventListener('click', function () { quit(false); });
    ui.title = make('p', 'tour-title', ui.bubble);
    ui.text = make('p', 'tour-text', ui.bubble);
    ui.note = make('p', 'tour-note', ui.bubble);
    ui.actions = make('div', 'tour-actions', ui.bubble);
    // ふれた操作がうしろの画面に届かないように（穴の外は押せない）
    ui.blocks.forEach(function (b) {
      ['click', 'pointerdown', 'touchstart', 'mousedown'].forEach(function (t) {
        b.addEventListener(t, function (e) { e.preventDefault(); e.stopPropagation(); }, { passive: false });
      });
    });
    return ui;
  }

  function hideUi() {
    if (!ui) return;
    ui.blocks.forEach(function (b) { b.hidden = true; });
    ui.spot.hidden = ui.ring.hidden = ui.bubble.hidden = true;
    document.body.classList.remove('tour-active');
  }

  // ── 手順 ──
  // hole：押せる場所（ここ以外は押せない）。ring：光らせる場所（無ければ hole）。
  // dim：'light' は画面を少しだけ暗くする（お手本の動画やカメラが見えるように）。'none' は暗くせず、押すのも止めない。
  function stepDefs(st) {
    var word = st.display || '単語';
    var reading = st.reading || '';
    var awReading = ($('awReading') && $('awReading').value.trim()) || reading || 'たんご';
    return {
      homeSettings: { n: 1, hole: function () { return $('btnSettings'); },
        title: '設定をひらこう', text: ['右上の ', { b: '⚙（設定）' }, ' を押します。'] },
      homeAddWord: { n: 2, hole: function () { return $('btnAddWord'); }, scroll: true,
        title: '単語を追加しよう', text: ['「', { b: '単語を追加' }, '」を押します。'] },
      awInfo: { n: 3, hole: function () { return document.querySelector('#awModalOverlay .aw-modal-card'); },
        ring: function () { return $('awRegisterBtn'); }, pad: 0, outside: true, onEnter: prefillWord,
        title: '練習する単語を入れよう',
        text: ['れんしゅうに「', { b: ($('awDisplay') && $('awDisplay').value.trim()) || '電話' }, '」を入れておいたよ。べつの単語にしてもOK。「', { b: '次へ（お手本録画）' }, '」を押します。'],
        note: '単語とお手本の録画は、ふだんは、おうちの人や先生が登録します。今回は使い方を知るために、自分で1つ登録してみましょう。' },
      awRecord: { n: 4, hole: function () { return document.querySelector('#awModalOverlay .aw-modal-card'); },
        ring: function () { return $('awRecordBtn'); }, pad: 0, outside: true,
        title: 'お手本を録画しよう',
        text: ['顔を点線の枠に合わせて「', { b: '録画開始' }, '」を押し、3秒のあいだに「', { b: awReading }, '」と1回はっきり言います。終わると、自動で登録されます。'] },
      awDone: { n: 5, hole: function () { return document.querySelector('#awModalOverlay .aw-modal-card'); },
        ring: function () { return $('awFinishBtn'); }, pad: 0, outside: true,
        title: '登録できた！', text: ['「', { b: '完了して一覧に戻る' }, '」を押します。'] },
      homeCard: { n: 6, hole: function () { return homeCardButton(st); }, scroll: true,
        onTargetClick: function () { var s = load(); if (s) { s.stage = 'practice'; save(s); } },
        title: '「' + word + '」を練習しよう', text: ['登録した単語の「', { b: '録音する' }, '」を押します。'] },
      pStart: { n: 7, hole: function () { return $('refWatchBtn') || $('buttonStart'); },
        title: 'まず、お手本を見よう', text: ['「', { b: $('refWatchBtn') ? 'お手本を見る' : '録音する' }, '」を押します。'] },
      // お手本の動画が流れている間は吹き出しを出さない（動画と音の高さ・長さの図をかくさない）
      pRef: { n: 8, dim: 'light', hole: function () { return $('refStageRec'); },
        bubbleWhen: function () { return $('refStageReplay') && !$('refStageReplay').hidden; },
        title: 'お手本を見よう', text: ['先生の口の動きと、音の高さ・長さを見ます。見おわったら「', { b: '録音する' }, '」を押します。'] },
      pCount: { n: 9, dim: 'none', anchor: 'bottom',
        title: 'かまえよう', text: ['3・2・1 のあとに、「', { b: reading || word }, '」と1回言います。お手本に合わせなくてだいじょうぶ。'] },
      pRec: { n: 9, dim: 'light', hole: function () { return $('stageStop'); },
        title: '自分のペースで言おう', text: ['言い終わったら「', { b: '録音停止' }, '」を押します。しずかにしていると、自動でも止まります。'] },
      pDone: { n: 10, dim: 'light', hole: function () { return $('buttongraph'); }, scroll: true,
        onTargetClick: function () { var s = load(); if (s) { s.stage = 'result'; save(s); } },
        title: '口の動きを見くらべよう', text: ['上で、お手本とあなたの口がならんで再生されます。見たら「', { b: '結果を見る' }, '」を押します。'] },
      pPerm: { n: 7, hole: function () { return $('btnPermHelp'); },
        title: 'マイクかカメラが使えません', text: ['「', { b: '直し方を見る' }, '」で直してから、もう一度ためしてね。チュートリアルはここで終わります。'],
        buttons: [{ label: 'チュートリアルをおわる', onClick: function () { quit(true); } }] },
      rScore: { n: 11, dim: 'light', hole: function () { return document.querySelector('.result-card'); }, scroll: true, pad: 4,
        title: 'これがあなたの点数', text: ['100点に近いほど、お手本に近い発音です。下の ◎○△ で、高さ・長さ・口の形のどこが良くて、どこがまだかが分かります。'],
        buttons: [{ label: 'つぎへ', onClick: function () { var s = load(); if (s) { s.rstep = 'next'; save(s); tick(); } } }] },
      rNext: { n: 12, dim: 'light', scroll: true, pad: 4,
        hole: function () { return document.querySelector('.next-one-card') || document.querySelector('.all-good-card'); },
        title: document.querySelector('.next-one-card') ? 'つぎは、ここだけ' : 'ぜんぶ、きれいに言えたね',
        text: document.querySelector('.next-one-card')
          ? ['つぎの練習で直すのは、ここだけです。1つずつ直していくと、点数がのびていきます。']
          : ['この単語はマスターです。ほかの単語も練習してみよう。'],
        buttons: [{ label: 'おわり', onClick: finish }] }
    };
  }

  function homeCardButton(st) {
    var card = null;
    if (st.wordId) card = document.querySelector('.word-card[data-word-id="' + cssEscape(st.wordId) + '"]');
    if (!card && st.display) card = document.querySelector('.word-card[data-word="' + cssEscape(st.display) + '"]');
    return card ? card.querySelector('.wc-rec') : null;
  }

  function prefillWord() {
    var d = $('awDisplay'), r = $('awReading');
    if (!d || !r || d.value.trim() || r.value.trim()) return;
    var have = Array.prototype.map.call(document.querySelectorAll('.word-card'), function (c) { return c.dataset.word; });
    var ex = EXAMPLES.filter(function (e) { return have.indexOf(e[0]) < 0; })[0] || EXAMPLES[0];
    d.value = ex[0];
    r.value = ex[1];
    d.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // ── いまの手順をページの状態から決める ──
  function anySheetOpen(except) {
    return Array.prototype.some.call(document.querySelectorAll('.sheet'), function (s) { return !s.hidden && s.id !== except; });
  }

  function resolve(st) {
    var defs = stepDefs(st);
    if ($('awModalOverlay')) {                       // ホーム
      var aw = $('awModalOverlay');
      if (aw.classList.contains('open')) {
        if (shown($('awPane3'))) return ['awDone', defs.awDone];
        if (shown($('awPane2'))) return ['awRecord', defs.awRecord];
        return ['awInfo', defs.awInfo];
      }
      if (anySheetOpen('settingsSheet')) return null;
      if (st.wordId || st.stage === 'practice' || st.stage === 'result') {
        if (!$('settingsSheet').hidden) $('settingsSheet').hidden = true;
        return ['homeCard', defs.homeCard];
      }
      if (!$('settingsSheet').hidden) return ['homeAddWord', defs.homeAddWord];
      return ['homeSettings', defs.homeSettings];
    }
    if ($('recStage')) {                             // 録音ページ
      if ($('loadingOverlay') && $('loadingOverlay').classList.contains('show')) return null;
      if (anySheetOpen()) return null;
      if ($('refStage') && $('refStage').classList.contains('show')) return ['pRef', defs.pRef];
      if ($('guideCountdown') && $('guideCountdown').classList.contains('show')) return ['pCount', defs.pCount];
      var phase = document.body.dataset.phase;
      if (phase === 'recording') return ['pRec', defs.pRec];
      if (phase === 'done') return ['pDone', defs.pDone];
      if ($('btnPermHelp') && !$('btnPermHelp').hidden) return ['pPerm', defs.pPerm];
      return ['pStart', defs.pStart];
    }
    if ($('rsTabs')) {                               // 結果ページ
      if (st.rstep === 'next') return ['rNext', defs.rNext];
      return ['rScore', defs.rScore];
    }
    return null;
  }

  // ── 表示 ──
  var current = null;      // いまの手順の名前
  var currentDef = null;

  function enter(name, def) {
    current = name;
    currentDef = def;
    var u = buildUi();
    u.stepLabel.textContent = 'チュートリアル ' + def.n + ' / ' + TOTAL;
    u.barFill.style.width = Math.round(def.n / TOTAL * 100) + '%';
    u.title.textContent = def.title || '';
    fill(u.text, def.text);
    u.note.hidden = !def.note;
    u.note.textContent = def.note || '';
    u.actions.replaceChildren();
    (def.buttons || []).forEach(function (b) {
      var btn = make('button', 'tour-btn', u.actions);
      btn.type = 'button';
      btn.textContent = b.label;
      btn.addEventListener('click', b.onClick);
    });
    u.actions.hidden = !(def.buttons && def.buttons.length);
    if (def.onEnter) setTimeout(def.onEnter, 60);
    if (def.scroll && def.hole) {
      var t = def.hole();
      if (t && t.scrollIntoView) t.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  function rectOf(el, pad) {
    var r = el.getBoundingClientRect();
    return { x: r.left - pad, y: r.top - pad, w: r.width + pad * 2, h: r.height + pad * 2 };
  }

  function place(def) {
    var u = ui;
    var W = window.innerWidth, H = window.innerHeight;
    var dim = def.dim || 'full';
    var holeEl = def.hole ? def.hole() : null;
    if (def.hole && !shown(holeEl)) { hideUi(); return; }
    document.body.classList.add('tour-active');
    var pad = def.pad != null ? def.pad : 6;
    var hole = holeEl ? rectOf(holeEl, pad) : null;
    var ringEl = def.ring ? def.ring() : holeEl;
    var ring = ringEl && shown(ringEl) ? rectOf(ringEl, def.ring ? 3 : pad) : hole;

    // 暗くする面（穴の形）と、穴の外を押せなくする板
    if (hole && dim !== 'none') {
      u.spot.hidden = false;
      u.spot.className = 'tour-spot' + (dim === 'light' ? ' light' : '');
      u.spot.style.left = hole.x + 'px'; u.spot.style.top = hole.y + 'px';
      u.spot.style.width = hole.w + 'px'; u.spot.style.height = hole.h + 'px';
      var b = u.blocks;
      setBox(b[0], 0, 0, W, Math.max(0, hole.y));
      setBox(b[1], 0, hole.y + hole.h, W, Math.max(0, H - hole.y - hole.h));
      setBox(b[2], 0, hole.y, Math.max(0, hole.x), hole.h);
      setBox(b[3], hole.x + hole.w, hole.y, Math.max(0, W - hole.x - hole.w), hole.h);
    } else {
      u.spot.hidden = true;
      u.blocks.forEach(function (x) { x.hidden = true; });
    }
    if (ring && dim !== 'none' || (ring && def.ring)) {
      u.ring.hidden = false;
      u.ring.style.left = ring.x + 'px'; u.ring.style.top = ring.y + 'px';
      u.ring.style.width = ring.w + 'px'; u.ring.style.height = ring.h + 'px';
    } else {
      u.ring.hidden = true;
    }

    // 吹き出し：光らせた場所の下（入らなければ上）。どちらにも入らなければ画面の下
    if (def.bubbleWhen && !def.bubbleWhen()) { u.bubble.hidden = true; return; }
    u.bubble.hidden = false;
    var bw = u.bubble.offsetWidth, bh = u.bubble.offsetHeight, gap = 14, margin = 12;
    var top, cls = 'tour-bubble';
    // outside：大きな画面（単語の追加）の中に重ねず、その外に出す。しっぽは押すボタンを指す
    var anchor = def.outside ? hole : (ring || hole);
    var pointAt = ring || hole;
    if (!anchor || def.anchor === 'bottom') {
      top = H - bh - margin - 70;
      cls += ' notail';
      anchor = null;
    } else if (anchor.y + anchor.h + gap + bh + margin <= H) {
      top = anchor.y + anchor.h + gap; cls += ' below';
    } else if (anchor.y - gap - bh >= margin) {
      top = anchor.y - gap - bh; cls += ' above';
    } else {
      top = H - bh - margin; cls += ' notail';
    }
    var cx = anchor ? (pointAt || anchor).x + (pointAt || anchor).w / 2 : W / 2;
    var left = Math.min(Math.max(margin, cx - bw / 2), W - bw - margin);
    u.bubble.className = cls;
    u.bubble.style.left = left + 'px';
    u.bubble.style.top = Math.max(margin, top) + 'px';
    u.bubble.style.setProperty('--tail-x', Math.min(Math.max(22, cx - left), bw - 22) + 'px');
  }

  function setBox(el, x, y, w, h) {
    el.hidden = !(w > 0 && h > 0);
    el.style.left = x + 'px'; el.style.top = y + 'px';
    el.style.width = w + 'px'; el.style.height = h + 'px';
  }

  var timer = null;
  function tick() {
    var st = load();
    if (!st) { stop(); return; }
    var r = resolve(st);
    if (!r) { hideUi(); current = null; return; }
    if (r[0] !== current) enter(r[0], r[1]);
    else currentDef = r[1];   // 文（入れた単語など）を新しくする
    if (current === 'awRecord' || current === 'awInfo') {
      fill(ui.text, r[1].text);
    }
    place(currentDef);
  }
  function run() {
    if (timer) return;
    buildUi();
    timer = setInterval(tick, 150);
    tick();
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    hideUi();
    current = null;
  }

  // 穴の中を押したとき（ページを移る前に進み具合を残す）
  document.addEventListener('click', function (e) {
    if (!currentDef || !currentDef.onTargetClick || !currentDef.hole) return;
    var t = currentDef.hole();
    if (t && t.contains(e.target)) currentDef.onTargetClick();
  }, true);

  window.addEventListener('resize', function () { if (timer) tick(); });
  window.addEventListener('scroll', function () { if (timer && currentDef) place(currentDef); }, { passive: true });

  // 単語を登録した（select.html が知らせる）→ その単語で練習へ
  document.addEventListener('sp:wordadded', function (e) {
    var s = load();
    if (!s || !e.detail) return;
    s.wordId = e.detail.wordId;
    s.display = e.detail.display;
    s.reading = e.detail.reading;
    save(s);
  });

  // ── はじめ・おわりのカード ──
  function card(opts) {
    injectCss();
    var wrap = make('div', 'tour-card-wrap', document.body);
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    var c = make('div', 'tour-card', wrap);
    var icon = make('div', 'tour-card-icon', c);
    icon.innerHTML = opts.icon;
    var h = make('h2', '', c); h.textContent = opts.title;
    var p = make('p', '', c); p.textContent = opts.text;
    if (opts.flow) {
      var f = make('div', 'tour-flow', c);
      opts.flow.forEach(function (s, i) {
        if (i) { var a = make('em', '', f); a.textContent = '→'; a.style.fontStyle = 'normal'; }
        var sp = make('span', '', f); sp.textContent = s;
      });
    }
    if (opts.note) { var n = make('p', 'tour-note', c); n.textContent = opts.note; }
    var act = make('div', 'tour-actions', c);
    opts.buttons.forEach(function (b) {
      var btn = make('button', 'tour-btn' + (b.sub ? ' sub' : ''), act);
      btn.type = 'button';
      btn.textContent = b.label;
      btn.addEventListener('click', function () { wrap.remove(); if (b.onClick) b.onClick(); });
    });
    if (opts.footnote) { var fn = make('p', 'tour-foot', c); fn.textContent = opts.footnote; }
    setTimeout(function () { var first = act.querySelector('button'); if (first) first.focus(); }, 30);
    return wrap;
  }

  function toast(text) {
    injectCss();
    var t = make('div', 'tour-toast', document.body);
    t.setAttribute('role', 'status');
    t.textContent = text;
    setTimeout(function () { t.remove(); }, 4500);
  }

  function showStart() {
    document.querySelectorAll('.sheet').forEach(function (s) { s.hidden = true; });
    card({
      icon: ICON_PLAY,
      title: 'いっしょに1回やってみよう',
      text: '画面の案内にそって、単語を1つ登録して、録音して、点数を見るところまで進みます。3分くらいです。',
      flow: ['単語の登録', '録音', '結果を見る'],
      note: '単語とお手本の録画は、ふだんは、おうちの人や先生が登録します。今回は使い方を知るために、自分で1つ登録してみましょう。',
      buttons: [
        { label: 'はじめる', onClick: function () { save({ stage: 'register' }); run(); } },
        { label: 'あとで', sub: true }
      ],
      footnote: '設定の「使い方を見る」から、いつでもできます。'
    });
  }

  function quit(silent) {
    clearState();
    stop();
    if (!silent) toast('チュートリアルをやめました。設定の「使い方を見る」から、いつでもやり直せます。');
  }

  function finish() {
    clearState();
    stop();
    card({
      icon: ICON_DONE,
      title: 'できた！',
      text: '単語の登録から、録音して点数を見るまで、ひととおりできました。あとは、ホームの単語をえらんで練習しよう。',
      buttons: [
        { label: 'ホームへ', onClick: function () { location.href = '/select'; } },
        { label: 'この結果をもう少し見る', sub: true }
      ]
    });
  }

  // ── はじめに ──
  function init() {
    var cfg = window.SP_TOUR_HOME;
    var showIntro = $('btnShowIntro');
    if (showIntro) showIntro.addEventListener('click', function () { clearState(); stop(); showStart(); });
    if (load()) { run(); return; }
    if (cfg && cfg.auto && !seen(cfg.user)) {
      markSeen(cfg.user);   // 一度出したら、次からは自動では出さない（設定からはいつでも）
      showStart();
    }
  }

  window.SPTour = { start: showStart, quit: quit };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
