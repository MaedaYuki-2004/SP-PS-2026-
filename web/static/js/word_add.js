/* SP-PS 単語の追加・お手本の録り直し（web/templates/_word_add.html の画面を動かす）
 *
 * 流れ：①単語と読みがな → ②お手本を録画（3・2・1 のあと 3 秒）→ ③録画を見て確かめる → 登録
 *
 * ・読みがなは、単語を入れると候補を自動で入れる（/api/reading_hint。かなだけの単語はその場でひらがなに）。
 *   自分で読みを直したあとは、上書きしない。
 * ・録画が終わってもすぐには登録しない。声が入っていない・顔が切れている録画に、その場で気づけるように、
 *   見て確かめてから「これで登録する」を押してもらう。
 * ・3・2・1 の間・録画中・送信中は閉じない（とじるボタンも出さない）。
 * ・カメラは録画の画面に入ったときに起こし、閉じたら必ず止める。起動の途中で閉じたときも止める。
 *
 * 呼び方：
 *   SPWordAdd.open()                                  … 新しい単語を追加
 *   SPWordAdd.rerecord(wordId, display, reading[, {reloadOnClose}]) … 登録した単語のお手本を録り直す
 *   [data-word-add] を押す → open()／.word-ref-btn を押す → rerecord()（data-word-id・data-word-display・data-word-reading）
 * 知らせ：sp:wordadded {wordId, display, reading}（はじめてのチュートリアル tour.js が使う）、sp:refsaved {wordId}
 * 登録したあとで閉じると、ページを読み込み直して一覧を新しくする。
 * 見た目は _word_add.html に置く（このファイルは Service Worker が古い控えを返すことがあるため、
 * 見た目と動きの食い違いが出にくいよう、ID とクラスの名前は変えない）。
 */
(function () {
  'use strict';

  var REC_SECONDS = 3;
  var COUNT_FROM = 3;
  // main.js の無音判定（0.03）よりやや緩め（誤って止めない側に倒す）
  var MIC_SILENCE_THRESHOLD = 0.02;
  var HINT_DEFAULT = 'のばす音は「ー」で書きます（例：すーぱー）';
  var HINT_AUTO = '自動で入れました。ちがうときは直してください。';

  function $(id) { return document.getElementById(id); }

  var overlay = $('awModalOverlay');
  if (!overlay) return;

  var el = {
    card: overlay.querySelector('.aw-modal-card'),
    closeX: $('awCloseX'),
    ind: [$('awInd1'), $('awInd2'), $('awInd3')],
    steps: $('awSteps'),
    pane: { info: $('awPane1'), record: $('awPane2'), check: $('awPaneCheck'), done: $('awPane3') },
    display: $('awDisplay'), reading: $('awReading'), hint: $('awReadingHint'),
    status1: $('awStatus1'), matchBox: $('awMatch'), matchText: $('awMatchText'), matchActions: $('awMatchActions'),
    registerBtn: $('awRegisterBtn'),
    recTitle: $('awPane2Title'), sayWord: $('awSayWord'), sayReading: $('awSayReading'),
    video: $('awVideo'), count: $('awCount'), countNum: $('awCountNum'), countText: $('awCountText'),
    recBadge: $('awRecBadge'), recBar: $('awRecBar'), recBarFill: $('awRecBarFill'),
    volFill: $('awVolFill'), volWarn: $('awVolWarn'), status2: $('awStatus2'),
    recordBtn: $('awRecordBtn'), backBtn: $('awBackBtn'),
    checkTitle: $('awCheckTitle'), playback: $('awPlayback'), replay: $('awReplayBtn'), checkVoice: $('awCheckVoice'),
    status3: $('awStatus3'), confirmBtn: $('awConfirmBtn'), retakeBtn: $('awRetakeBtn'),
    doneTitle: $('awDoneTitle'), doneCount: $('awDoneCount'),
    practiceBtn: $('awPracticeBtn'), addAnotherBtn: $('awAddAnotherBtn'), finishBtn: $('awFinishBtn')
  };

  var st = {
    mode: 'add',          // 'add'：新しい単語／'rerecord'：お手本の録り直し
    step: 'info',
    wordId: null, display: '', reading: '',
    stream: null, camToken: 0, meter: null,
    recorder: null, chunks: [], blob: null, blobUrl: null,
    countTimer: null, recTimer: null, barRaf: null,
    busy: false,          // 3・2・1・録画中・送信中
    added: 0, changed: false,
    readingTouched: false, hintTimer: null, hintSeq: 0,
    opener: null
  };

  // ── 文字 ──
  function toHiragana(s) {
    return String(s || '').replace(/[ァ-ヶ]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0x60); });
  }
  function normReading(s) { return toHiragana(String(s || '').normalize('NFKC').replace(/\s+/g, '')); }
  // サーバー（core/vocab.py の julius_reading）と同じ決まり：ひらがな（ゔ を除く）と「ー」だけ
  function readingError(r) {
    if (!r) return '読みがなを、ひらがなで入れてください。';
    var bad = [];
    Array.prototype.forEach.call(r, function (c) { if (!/[ぁ-んー]/.test(c) && bad.indexOf(c) < 0) bad.push(c); });
    return bad.length ? '読みがなは、ひらがなで入れてください（使えない文字：' + bad.join('・') + '）。' : null;
  }

  function setStatus(node, msg, kind) {
    node.textContent = msg || '';
    node.className = 'aw-status' + (kind ? ' ' + kind : '');
  }
  function setHint(kind, msg) {
    el.hint.className = 'aw-hint' + (kind ? ' ' + kind : '');
    el.hint.textContent = msg || (kind === 'auto' ? HINT_AUTO : HINT_DEFAULT);
    el.reading.classList.toggle('is-err', kind === 'err');
  }

  // ── 画面の切りかえ ──
  function setStep(step) {
    st.step = step;
    Object.keys(el.pane).forEach(function (k) { el.pane[k].hidden = k !== step; });
    var idx = { info: 0, record: 1, check: 2, done: 3 }[step];
    el.ind.forEach(function (node, i) {
      node.className = 'aw-step' + (i < idx ? ' done' : i === idx ? ' active' : '');
    });
    // お手本の録り直しでは ①（単語）が無い
    var showWord = st.mode === 'add';
    el.ind[0].hidden = !showWord;
    if (el.ind[0].nextElementSibling) el.ind[0].nextElementSibling.hidden = !showWord;
    el.steps.hidden = step === 'done';
    if (el.card) el.card.scrollTop = 0;
    updateClose();
  }
  function setBusy(b) { st.busy = b; updateClose(); }
  function updateClose() { el.closeX.hidden = st.busy; }

  function show() {
    if (!overlay.classList.contains('open')) {
      st.opener = document.activeElement;
      overlay.classList.add('open');
      document.documentElement.style.overflow = 'hidden';
    }
  }

  // ── 開く ──
  function openAdd(keepCount) {
    if (st.busy) return;
    if (!keepCount) st.added = 0;
    st.mode = 'add';
    st.wordId = null; st.display = ''; st.reading = '';
    el.display.value = ''; el.reading.value = '';
    st.readingTouched = false;
    setHint('', '');
    setStatus(el.status1, '', '');
    hideMatch();
    el.registerBtn.disabled = false;
    show();
    setStep('info');
    setTimeout(function () { try { el.display.focus(); } catch (e) {} }, 60);
  }

  // opts.reloadOnClose：録り直さずに閉じても、ページを読み込み直す（単語の読みを変えた直後など）
  function openRerecord(wordId, display, reading, opts) {
    if (st.busy || !wordId) return;
    if (opts && opts.reloadOnClose) st.changed = true;
    st.mode = 'rerecord';
    st.wordId = wordId; st.display = display || ''; st.reading = reading || '';
    show();
    goRecord();
  }

  function close() {
    if (st.busy) return;
    overlay.classList.remove('open');
    document.documentElement.style.overflow = '';
    stopCamera();
    clearPreview();
    hideCount();
    if (st.changed) { st.changed = false; location.reload(); return; }
    if (st.opener && st.opener.focus) { try { st.opener.focus(); } catch (e) {} }
  }

  // ── ① 単語と読みがな ──
  function hideMatch() { el.matchBox.hidden = true; el.matchActions.replaceChildren(); }
  function showMatch(text, buttons) {
    el.matchText.textContent = text;
    el.matchActions.replaceChildren.apply(el.matchActions, buttons.map(function (b) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = b.primary ? 'aw-btn-primary' : 'aw-btn-secondary';
      btn.textContent = b.label;
      btn.addEventListener('click', b.onClick);
      return btn;
    }));
    el.matchBox.hidden = false;
  }
  function wordName(w) { return '「' + w.display + '」（' + w.reading + '）'; }

  function scheduleHint() {
    clearTimeout(st.hintTimer);
    if (st.readingTouched) return;
    var text = el.display.value.trim();
    if (!text) { el.reading.value = ''; setHint('', ''); return; }
    var kana = normReading(text);
    if (/^[ぁ-んー]+$/.test(kana)) { el.reading.value = kana; setHint('auto'); return; }
    st.hintTimer = setTimeout(function () {
      var seq = ++st.hintSeq;
      fetch('/api/reading_hint?display=' + encodeURIComponent(text), { headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (seq !== st.hintSeq || st.readingTouched || el.display.value.trim() !== text) return;
          if (d && d.reading) { el.reading.value = d.reading; setHint('auto'); }
          else { el.reading.value = ''; setHint('', '読みがなを、ひらがなで入れてください。'); }
        })
        .catch(function () {});
    }, 300);
  }

  el.display.addEventListener('input', function () { hideMatch(); setStatus(el.status1, '', ''); scheduleHint(); });
  el.reading.addEventListener('input', function () {
    hideMatch(); setStatus(el.status1, '', '');
    st.readingTouched = el.reading.value.trim() !== '';
    st.hintSeq++;
    setHint('', '');
  });
  el.reading.addEventListener('blur', function () {
    var v = normReading(el.reading.value);
    if (v !== el.reading.value) el.reading.value = v;
    if (v && readingError(v)) setHint('err', readingError(v));
  });
  el.display.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); el.reading.focus(); }
  });
  el.reading.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.isComposing && !el.registerBtn.disabled) { e.preventDefault(); next(); }
  });

  async function restoreWord(w) {
    setStatus(el.status1, '元に戻しています…', 'loading');
    try {
      var res = await fetch('/admin/restore_word', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ word_id: w.word_id })
      });
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || '元に戻せませんでした');
      hideMatch();
      setStatus(el.status1, '', '');
      st.wordId = w.word_id; st.display = w.display; st.reading = w.reading;
      document.dispatchEvent(new CustomEvent('sp:wordadded', { detail: { wordId: w.word_id, display: w.display, reading: w.reading } }));
      goDone('「' + w.display + '」を元に戻しました（前の練習の記録とお手本が、そのまま使えます）');
    } catch (err) {
      setStatus(el.status1, 'エラー：' + err.message, 'err');
    }
  }

  // 同じ単語を2つ作ると練習の記録が分かれるので、先に同じ単語・似ている単語（削除した単語を含む）を確かめる
  async function next() {
    var display = el.display.value.trim();
    var reading = normReading(el.reading.value);
    el.reading.value = reading;
    hideMatch();
    if (!display) { setStatus(el.status1, '単語を入れてください。', 'err'); el.display.focus(); return; }
    var bad = readingError(reading);
    if (bad) { setHint('err', bad); el.reading.focus(); return; }
    setStatus(el.status1, '確かめています…', 'loading');
    el.registerBtn.disabled = true;
    var same;
    try {
      var res = await fetch('/admin/check_word', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ display: display, reading: reading })
      });
      same = await res.json();
      if (!res.ok) throw new Error(same.error || '確かめられませんでした');
    } catch (err) {
      setStatus(el.status1, 'エラー：' + err.message, 'err');
      return;
    } finally {
      el.registerBtn.disabled = false;
    }
    setStatus(el.status1, '', '');
    st.display = display; st.reading = reading;
    var exact = same.exact[0];
    if (exact && !exact.archived) {
      setStatus(el.status1, wordName(exact) + 'は、もう登録されています。', 'err');
      return;
    }
    if (exact) {
      showMatch('前に削除した' + wordName(exact) + 'があります（練習の記録 ' + exact.records + ' 回）。'
        + '新しく追加せずに元に戻すと、前の記録とお手本の録画がそのまま使えます。'
        + 'お手本を録り直したいときは、戻したあとで「お手本を録り直す」を押してください。',
        [{ label: '元に戻す', primary: true, onClick: function () { restoreWord(exact); } }]);
      return;
    }
    if (same.similar.length) {
      var list = same.similar.map(function (w) { return wordName(w) + (w.archived ? '（削除した単語）' : ''); }).join('、');
      var buttons = same.similar.filter(function (w) { return w.archived; }).slice(0, 2).map(function (w) {
        return { label: wordName(w) + 'を元に戻す', onClick: function () { restoreWord(w); } };
      });
      buttons.push({ label: '別の単語として追加する', primary: true, onClick: function () { hideMatch(); goRecord(); } });
      showMatch('よく似た単語があります：' + list + '。同じ単語なら、新しく追加しないでください（練習の記録が分かれます）。', buttons);
      return;
    }
    goRecord();
  }
  el.registerBtn.addEventListener('click', next);

  // ── ② 録画 ──
  function goRecord() {
    var add = st.mode === 'add';
    el.recTitle.textContent = '「' + st.display + '」のお手本を' + (add ? '録画' : '録り直す');
    el.sayWord.textContent = st.display;
    el.sayReading.textContent = st.reading && st.reading !== st.display ? st.reading : '';
    el.backBtn.textContent = add ? 'もどる' : 'とじる';
    el.volWarn.classList.remove('show');
    el.recordBtn.disabled = !st.stream;
    el.backBtn.disabled = false;
    setStep('record');
    if (st.stream) cameraReady();
    else { setStatus(el.status2, 'カメラを起動しています…', 'loading'); startCamera(); }
    faceGuide.activate(el.video, $('awGuideCanvas'), $('awGuideStatus'));
  }

  function startCamera() {
    var token = ++st.camToken;
    var p;
    try {
      p = navigator.mediaDevices.getUserMedia({ video: window.SPMedia ? SPMedia.video() : true, audio: true });
    } catch (err) {
      p = Promise.reject(err);
    }
    p.then(function (stream) {
      if (token !== st.camToken || !overlay.classList.contains('open')) {
        stream.getTracks().forEach(function (t) { t.stop(); });
        return;
      }
      st.stream = stream;
      el.video.srcObject = stream;
      st.meter = createMicMeter(stream, el.volFill);
      cameraReady();
    }).catch(function (err) {
      if (token !== st.camToken) return;
      setStatus(el.status2, window.cameraErrorMessage ? cameraErrorMessage(err) : 'カメラを起動できませんでした。', 'err');
    });
  }

  function cameraReady() {
    if (st.step !== 'record') return;
    el.recordBtn.disabled = false;
    setStatus(el.status2, '顔を点線の枠に合わせて「録画をはじめる」を押します。', '');
  }

  function stopCamera() {
    st.camToken++;
    clearTimeout(st.recTimer); clearInterval(st.countTimer); cancelAnimationFrame(st.barRaf);
    if (st.recorder && st.recorder.state === 'recording') { st.cancelled = true; try { st.recorder.stop(); } catch (e) {} }
    st.recorder = null;
    if (st.stream) { st.stream.getTracks().forEach(function (t) { t.stop(); }); st.stream = null; }
    el.video.srcObject = null;
    if (st.meter) { st.meter.stop(); st.meter = null; }
    el.volFill.style.width = '0%';
    faceGuide.activate(null);
  }

  function showCount(text, sub, go) {
    el.count.hidden = false;
    el.count.classList.toggle('go', !!go);
    el.countNum.textContent = text;
    el.countText.textContent = sub || '';
  }
  function hideCount() { el.count.hidden = true; el.recBadge.hidden = true; el.recBar.hidden = true; }

  function startCountdown() {
    if (!st.stream || st.busy) return;
    if (st.meter) st.meter.resume();
    setBusy(true);
    st.cancelled = false;
    el.recordBtn.disabled = true;
    el.backBtn.disabled = true;
    el.volWarn.classList.remove('show');
    setStatus(el.status2, '', '');
    var n = COUNT_FROM;
    showCount(String(n), '言う準備をして…');
    st.countTimer = setInterval(function () {
      n--;
      if (n > 0) { showCount(String(n), '言う準備をして…'); return; }
      clearInterval(st.countTimer);
      beginRecording();
    }, 1000);
  }

  function beginRecording() {
    if (!st.stream) { setBusy(false); return; }
    var rec;
    try {
      rec = new MediaRecorder(st.stream, window.SPMedia ? SPMedia.recorderOptions() : { videoBitsPerSecond: 1000000 });
    } catch (err) {
      hideCount(); setBusy(false);
      el.backBtn.disabled = false; el.recordBtn.disabled = false;
      setStatus(el.status2, 'この端末のブラウザでは録画できませんでした。（原因: ' + (err && err.name || 'MediaRecorder') + '）', 'err');
      return;
    }
    var chunks = [];
    st.recorder = rec;
    if (st.meter) st.meter.resetMax();
    rec.addEventListener('dataavailable', function (e) { if (e.data && e.data.size > 0) chunks.push(e.data); });
    rec.addEventListener('stop', function () {
      // 形式は止まった録画（this）から読む。閉じたときは st.recorder を先に空にするため
      var blob = new Blob(chunks, { type: this.mimeType || 'video/webm' });
      afterRecord(blob);
    });
    rec.start();
    showCount('どうぞ！', '「' + (st.reading || st.display) + '」', true);
    setTimeout(function () { if (st.recorder === rec) el.count.hidden = true; }, 700);
    el.recBadge.hidden = false;
    el.recBar.hidden = false;
    var t0 = performance.now();
    (function bar() {
      var p = Math.min(1, (performance.now() - t0) / (REC_SECONDS * 1000));
      el.recBarFill.style.width = (p * 100) + '%';
      if (p < 1) st.barRaf = requestAnimationFrame(bar);
    })();
    st.recTimer = setTimeout(function () { if (rec.state === 'recording') rec.stop(); }, REC_SECONDS * 1000);
  }

  function afterRecord(blob) {
    hideCount();
    el.recBarFill.style.width = '0%';
    st.recorder = null;
    setBusy(false);
    if (st.cancelled || !overlay.classList.contains('open')) return;
    el.backBtn.disabled = false;
    // マイクが音を拾えていないと、送ってもサーバーで必ず失敗する（無音では音の区切りが取れない）。
    // 待たせずに、その場で原因が分かる形で知らせる
    if (st.meter && st.meter.measuring() && st.meter.getMaxRms() < MIC_SILENCE_THRESHOLD) {
      el.volWarn.classList.add('show');
      setStatus(el.status2, '', '');
      el.recordBtn.disabled = false;
      return;
    }
    if (!blob.size) {
      setStatus(el.status2, '録画できませんでした。もう一度「録画をはじめる」を押してください。', 'err');
      el.recordBtn.disabled = false;
      return;
    }
    st.blob = blob;
    goCheck();
  }

  el.recordBtn.addEventListener('click', startCountdown);
  el.backBtn.addEventListener('click', function () {
    if (st.busy) return;
    if (st.mode === 'rerecord') { close(); return; }
    stopCamera();
    setStep('info');
  });

  // ── ③ 確かめる ──
  function clearPreview() {
    try { el.playback.pause(); } catch (e) {}
    el.playback.removeAttribute('src');
    try { el.playback.load(); } catch (e) {}
    if (st.blobUrl) { URL.revokeObjectURL(st.blobUrl); st.blobUrl = null; }
  }

  function goCheck() {
    var add = st.mode === 'add';
    clearPreview();
    st.blobUrl = URL.createObjectURL(st.blob);
    el.playback.src = st.blobUrl;
    el.playback.muted = false;
    el.checkTitle.textContent = '録画を確かめる';
    el.checkVoice.textContent = '「' + (st.reading || st.display) + '」の声が聞こえる';
    $('awConfirmLabel').textContent = confirmLabel();
    el.confirmBtn.disabled = false;
    el.retakeBtn.disabled = false;
    setStatus(el.status3, '', '');
    setStep('check');
    playPreview();
  }

  function confirmLabel() { return st.mode === 'add' ? 'これで登録する' : 'このお手本にする'; }

  function playPreview() {
    el.replay.hidden = true;
    try { el.playback.currentTime = 0; } catch (e) {}
    var p = el.playback.play();
    if (p && p.catch) p.catch(function () { el.replay.hidden = false; });
  }
  el.playback.addEventListener('ended', function () { el.replay.hidden = false; });
  el.replay.addEventListener('click', playPreview);
  el.retakeBtn.addEventListener('click', function () { if (st.busy) return; clearPreview(); goRecord(); });

  async function confirm() {
    if (!st.blob || st.busy) return;
    var add = st.mode === 'add';
    try { el.playback.pause(); } catch (e) {}
    setBusy(true);
    el.confirmBtn.disabled = true;
    el.retakeBtn.disabled = true;
    setStatus(el.status3, add ? '登録しています…（10秒ほどかかります）' : '保存しています…（10秒ほどかかります）', 'loading');
    var fd = new FormData();
    if (add) { fd.append('display', st.display); fd.append('reading', st.reading); }
    else fd.append('word_id', st.wordId);
    fd.append('file', st.blob, 'ref.webm');
    var res = null, data = {};
    try {
      res = await fetch(add ? '/admin/add_word' : '/api/lip_refs/overwrite', { method: 'POST', body: fd });
      try { data = await res.json(); } catch (e) { data = {}; }
      if (!res.ok) throw new Error(data.error || (add ? '登録できませんでした' : '保存できませんでした'));
    } catch (err) {
      setBusy(false);
      el.retakeBtn.disabled = false;
      // 録画の中身が原因のとき（声が認識できない・顔が映っていない など）は、同じ録画を送り直しても通らない
      var resend = !res || res.status >= 500;
      el.confirmBtn.disabled = !resend;
      setStatus(el.status3, err.message + (resend ? '（もう一度「' + confirmLabel() + '」を押すと、送り直します）' : ''), 'err');
      return;
    }
    setBusy(false);
    if (add) {
      st.wordId = data.word_id;
      // はじめてのチュートリアル（tour.js）が、この単語で練習へ案内する
      document.dispatchEvent(new CustomEvent('sp:wordadded', { detail: { wordId: data.word_id, display: st.display, reading: st.reading } }));
      // 以前の版で完全に消した同じ単語なら、練習記録に残る前の ID で登録される（core/vocab.py）
      goDone(data.reused ? '「' + st.display + '」を追加しました（前に練習した記録とつなげました）' : '');
    } else {
      document.dispatchEvent(new CustomEvent('sp:refsaved', { detail: { wordId: st.wordId } }));
      goDone('');
    }
  }
  el.confirmBtn.addEventListener('click', confirm);

  // ── 登録できた ──
  function goDone(title) {
    var add = st.mode === 'add';
    stopCamera();
    clearPreview();
    st.blob = null;
    st.changed = true;
    if (add) {
      st.added++;
      el.doneTitle.textContent = title || '「' + st.display + '」を追加しました';
      el.doneCount.textContent = st.added > 1 ? 'この画面で ' + st.added + ' 語 追加しました' : 'ホームの単語の一覧に出ます。';
    } else {
      el.doneTitle.textContent = '「' + st.display + '」のお手本を録り直しました';
      el.doneCount.textContent = 'つぎの練習から、新しいお手本が流れます。';
    }
    el.practiceBtn.href = '/practice/word/' + encodeURIComponent(st.wordId || '');
    el.practiceBtn.hidden = !st.wordId;
    el.addAnotherBtn.hidden = !add;
    el.finishBtn.textContent = add ? 'おわる' : 'とじる';
    setStep('done');
  }
  el.addAnotherBtn.addEventListener('click', function () { openAdd(true); });
  el.finishBtn.addEventListener('click', close);
  // 練習へ進むときは、読み込み直さずにそのまま移る
  el.practiceBtn.addEventListener('click', function () { st.changed = false; stopCamera(); });

  // ── とじる ──
  el.closeX.addEventListener('click', close);
  overlay.addEventListener('click', function (e) {
    // 外を押して閉じるのは、録画を失わない画面だけ
    if (e.target === overlay && (st.step === 'info' || st.step === 'done')) close();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && overlay.classList.contains('open')) close();
  });

  // ── ページのボタンから開く ──
  document.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target.closest('[data-word-add], .word-ref-btn') : null;
    if (!t) return;
    e.preventDefault();
    if (t.matches('[data-word-add]')) openAdd(false);
    else openRerecord(t.dataset.wordId, t.dataset.wordDisplay, t.dataset.wordReading);
  });

  // ── マイクの音の大きさ ──
  // 録画の画面ではカメラの映像しか見えず、マイクが音を拾えているかに気づけなかった。
  // 無音のまま登録すると「発音が認識できませんでした」とだけ出て、原因が分からず同じ失敗をくり返していた
  function createMicMeter(stream, fillEl) {
    var track = stream.getAudioTracks()[0];
    var Ctx = window.AudioContext || window.webkitAudioContext;
    var NONE = { resetMax: function () {}, getMaxRms: function () { return 1; }, resume: function () {}, measuring: function () { return false; }, stop: function () {} };
    if (!track || !Ctx) return NONE;
    var ctx, source, analyser;
    try {
      ctx = new Ctx();
      source = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
    } catch (e) {
      return NONE;
    }
    var buf = new Uint8Array(analyser.frequencyBinCount);
    var maxRms = 0, raf = null;
    (function tick() {
      analyser.getByteTimeDomainData(buf);
      var sum = 0;
      for (var i = 0; i < buf.length; i++) { var v = buf[i] / 128 - 1; sum += v * v; }
      var rms = Math.sqrt(sum / buf.length);
      if (rms > maxRms) maxRms = rms;
      if (fillEl) {
        var pct = Math.min(100, rms * 400);
        fillEl.style.width = pct + '%';
        fillEl.style.background = pct > 30 ? 'var(--good)' : pct > 8 ? 'var(--ref-line)' : 'var(--danger)';
      }
      raf = requestAnimationFrame(tick);
    })();
    return {
      resetMax: function () { maxRms = 0; },
      getMaxRms: function () { return maxRms; },
      // ブラウザによっては、AudioContext が一時停止（suspended）のまま始まる。そのままだと声が入っていても
      // 大きさが 0 と測れてしまうので、ボタンを押したとき（ユーザーの操作の中）に動かす
      resume: function () { if (ctx.state === 'suspended' && ctx.resume) ctx.resume().catch(function () {}); },
      // 測れているか。測れていないときは「声が録れていない」とは決めつけない
      measuring: function () { return ctx.state === 'running'; },
      stop: function () {
        if (raf) cancelAnimationFrame(raf);
        try { source.disconnect(); } catch (e) {}
        try { ctx.close(); } catch (e) {}
      }
    };
  }

  // ── 顔の位置の案内（点線の枠と、近い・遠いの表示。MediaPipe Face Mesh） ──
  // MediaPipe は大きいので、録画の画面をはじめて開いたときに読み込む
  var faceGuide = (function () {
    var CAM = null, CNVS = null, STS = null, ctx = null;
    var W = 0, H = 0, fm = null, busy = false, lm = null, frm = 0, running = false, loading = false;
    var OVAL = [[10,338],[338,297],[297,332],[332,284],[284,251],[251,389],[389,356],[356,454],[454,323],[323,361],[361,288],[288,397],[397,365],[365,379],[379,378],[378,400],[400,377],[377,152],[152,148],[148,176],[176,149],[149,150],[150,136],[136,172],[172,58],[58,132],[132,93],[93,234],[234,127],[127,162],[162,21],[21,54],[54,103],[103,67],[67,109],[109,10]];
    var LIP_OUTER = [[61,185],[185,40],[40,39],[39,37],[37,0],[0,267],[267,269],[269,270],[270,409],[409,291],[291,375],[375,321],[321,405],[405,314],[314,17],[17,84],[84,181],[181,91],[91,146],[146,61]];
    var LIP_INNER = [[78,191],[191,80],[80,81],[81,82],[82,13],[13,312],[312,311],[311,310],[310,415],[415,308],[308,324],[324,318],[318,402],[402,317],[317,14],[14,87],[87,178],[178,88],[88,95],[95,78]];
    var NOSE = [[168,6],[6,5],[5,4],[4,1],[1,19],[19,94],[94,49],[94,279],[49,48],[279,278]];
    var MP = 'https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh@0.4.1633559619/';   // 版を固定（script と、あとから読む wasm・data の版をそろえる）

    function sz() {
      if (!CNVS) return;
      var p = CNVS.parentElement;
      var nw = (p && p.offsetWidth) || 0, nh = (p && p.offsetHeight) || 0;
      if (nw !== W || nh !== H) { CNVS.width = nw; CNVS.height = nh; W = nw; H = nh; }
    }
    function drawConn(conns, color, lw) {
      if (!lm || !ctx) return;
      ctx.save();
      ctx.strokeStyle = color; ctx.lineWidth = lw; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      ctx.beginPath();
      for (var i = 0; i < conns.length; i++) {
        var pa = lm[conns[i][0]], pb = lm[conns[i][1]];
        if (!pa || !pb) continue;
        ctx.moveTo(pa.x * W, pa.y * H); ctx.lineTo(pb.x * W, pb.y * H);
      }
      ctx.stroke(); ctx.restore();
    }
    function guide() {
      if (!ctx || W < 1 || H < 1) return;
      ctx.clearRect(0, 0, W, H);
      var cx = W * 0.5, cy = H * 0.44, rx = W * 0.32, ry = H * 0.44;
      ctx.save();
      ctx.strokeStyle = 'rgba(255,255,255,0.6)'; ctx.lineWidth = Math.max(1.5, W * 0.004);
      ctx.setLineDash([6, 5]);
      ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = 'rgba(181,173,255,0.9)'; ctx.lineWidth = Math.max(1.2, W * 0.003);
      ctx.beginPath(); ctx.ellipse(cx, cy + ry * 0.38, rx * 0.52, ry * 0.16, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
      if (STS) STS.textContent = '顔を点線の枠に合わせてください';
    }
    function draw() {
      if (!ctx || W < 1 || H < 1) return;
      ctx.clearRect(0, 0, W, H);
      if (!lm) { guide(); return; }
      var ratio = (lm[454] ? lm[454].x : 1) - (lm[234] ? lm[234].x : 0);
      var color, text;
      if (ratio > 0.82) { color = 'rgba(233,176,79,0.95)'; text = '近すぎます。少し離れてください'; }
      else if (ratio < 0.38) { color = 'rgba(233,176,79,0.95)'; text = '遠すぎます。もう少し近づいてください'; }
      else { color = 'rgba(76,197,150,0.95)'; text = 'ちょうどいい位置です'; }
      ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = Math.max(3, W * 0.008); ctx.strokeRect(2, 2, W - 4, H - 4); ctx.restore();
      drawConn(OVAL, 'rgba(255,255,255,0.30)', Math.max(1, W * 0.002));
      drawConn(NOSE, 'rgba(255,255,255,0.30)', Math.max(1, W * 0.002));
      drawConn(LIP_OUTER, 'rgba(181,173,255,0.9)', Math.max(1.5, W * 0.004));
      drawConn(LIP_INNER, 'rgba(181,173,255,0.55)', Math.max(1, W * 0.003));
      if (STS) STS.textContent = text;
    }
    function tick() {
      if (!CAM) { running = false; return; }
      requestAnimationFrame(tick);
      if (++frm % 3 !== 0) return;
      sz();
      if (W < 1 || H < 1) return;
      if (!fm || busy) { if (!lm) guide(); return; }
      if (!CAM.srcObject || CAM.readyState < 2 || CAM.paused) { guide(); return; }
      busy = true;
      try { fm.send({ image: CAM }).catch(function () { busy = false; }); } catch (e) { busy = false; }
    }
    function initFM() {
      if (fm || typeof FaceMesh === 'undefined') return;
      fm = new FaceMesh({ locateFile: function (f) { return MP + f; } });
      fm.setOptions({ maxNumFaces: 1, refineLandmarks: false, minDetectionConfidence: 0.5, minTrackingConfidence: 0.5 });
      fm.onResults(function (res) {
        busy = false;
        lm = (res.multiFaceLandmarks && res.multiFaceLandmarks[0]) || null;
        draw();
      });
    }
    function load() {
      if (loading || typeof FaceMesh !== 'undefined') { initFM(); return; }
      loading = true;
      var s = document.createElement('script');
      s.src = MP + 'face_mesh.js';
      s.crossOrigin = 'anonymous';
      s.onload = initFM;
      document.head.appendChild(s);
    }
    return {
      // video・canvas・文字の要素を渡すと案内をはじめ、null で止める
      activate: function (video, canvas, status) {
        CAM = video; CNVS = canvas; STS = status;
        ctx = CNVS ? CNVS.getContext('2d') : null;
        lm = null; busy = false; W = 0; H = 0;
        if (!CAM) { if (ctx) ctx.clearRect(0, 0, CNVS.width, CNVS.height); return; }
        load();
        if (!running) { running = true; tick(); }
      }
    };
  })();

  window.SPWordAdd = { open: function () { openAdd(false); }, rerecord: openRerecord, close: close };
})();
