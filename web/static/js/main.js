/**
 * main.js
 * 録音ページのメインスクリプト。
 *
 * 【追加機能】
 *   B. リアルタイム音量バー
 *      AnalyserNode から RMS を計算して #volumeBar に反映する。
 *
 *   C. 無音自動停止
 *      録音開始後、MIN_SPEECH_MS 以上の発話を検出した後に
 *      SILENCE_THRESHOLD 以下が SILENCE_DURATION ms 続くと
 *      自動的に録音を停止する。
 *      停止前に #autoStopCountdown でカウントダウンを表示する。
 */

// ── 自動停止のパラメータ ─────────────────────────────
const SILENCE_THRESHOLD  = 0.03;   // この RMS 以下を「無音」と判定
const SILENCE_DURATION   = 1500;   // ms：この期間無音が続いたら自動停止
const MIN_SPEECH_MS      = 500;    // ms：この時間以上発話してから自動停止を有効にする

async function main() {
  try {
    const canvas      = document.querySelector('#visualizer');
    const canvasCtx   = canvas.getContext('2d');
    const btnStart    = document.querySelector('#buttonStart');
    const btnStop     = document.querySelector('#buttonStop');
    const btnGraph    = document.querySelector('#buttongraph');
    const audio       = document.querySelector('#audio');
    const lipPreview  = document.querySelector('#lipPreview');
    const btnLipRef   = document.querySelector('#buttonLipRef');
    const btnLipTest  = document.querySelector('#buttonLipTest');
    const lipStatus   = document.querySelector('#lipStatus');
    const volumeBar   = document.querySelector('#volumeBar');       // B
    const volumeFill  = document.querySelector('#volumeFill');      // B
    const autoStop    = document.querySelector('#autoStopCountdown'); // C

    const stream = await navigator.mediaDevices.getUserMedia({ video:true, audio:true });
    const [track] = stream.getAudioTracks();
    const settings = track.getSettings();
    const videoStream = new MediaStream(stream.getVideoTracks());

    if (lipPreview) {
      lipPreview.srcObject = videoStream;
    }

    const audioContext = new AudioContext({ sampleRate:16000 });
    await audioContext.audioWorklet.addModule('/static/js/audio_recorder.js');

    const mediaStreamSource = audioContext.createMediaStreamSource(stream);
    const audioRecorder     = new AudioWorkletNode(audioContext, 'audio-recorder');
    const buffers           = [];

    audioRecorder.port.addEventListener('message', event => {
      buffers.push(event.data.buffer);
    });
    audioRecorder.port.start();
    mediaStreamSource.connect(audioRecorder);
    audioRecorder.connect(audioContext.destination);

    // Analyser（波形 + 音量両用）
    const analyser  = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    const bufLen    = analyser.frequencyBinCount;
    const timeData  = new Uint8Array(bufLen);
    mediaStreamSource.connect(analyser);
    window._spAnalyser    = analyser;
    window._spSampleRate  = audioContext.sampleRate;

    // ── 状態管理 ───────────────────────────────────────
    let isRecording    = false;
    let hasRecording   = false;   // ③ 録音済みかどうか
    let speechStart    = null;
    let silenceStart   = null;
    let autoStopTimer  = null;
    let animFrame      = null;

    // ── RMS 計算 ────────────────────────────────────────
    function getRMS(data) {
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = data[i] / 128 - 1;
        sum += v * v;
      }
      return Math.sqrt(sum / data.length);
    }

    // ── メインループ（波形 + 音量 + 自動停止） ─────────
    function drawLoop() {
      animFrame = requestAnimationFrame(drawLoop);
      analyser.getByteTimeDomainData(timeData);

      // --- 波形描画 ---
      const W = canvas.width, H = canvas.height;
      canvasCtx.clearRect(0, 0, W, H);

      // 背景
      canvasCtx.fillStyle = 'rgba(14,30,69,.04)';
      canvasCtx.fillRect(0, 0, W, H);

      // 波形線
      canvasCtx.lineWidth   = 2;
      canvasCtx.strokeStyle = isRecording ? '#C03D20' : '#9E9890';
      canvasCtx.beginPath();
      const sliceW = W / bufLen;
      let x = 0;
      for (let i = 0; i < bufLen; i++) {
        const v = timeData[i] / 128;
        const y = (v * H) / 2;
        i === 0 ? canvasCtx.moveTo(x, y) : canvasCtx.lineTo(x, y);
        x += sliceW;
      }
      canvasCtx.lineTo(W, H / 2);
      canvasCtx.stroke();

      // --- 音量バー (B) ---
      const rms = getRMS(timeData);
      const pct = Math.min(100, rms * 400);  // 0〜100%
      if (volumeFill) volumeFill.style.width = pct + '%';

      // 音量に応じてバー色を変える
      if (volumeFill) {
        if (pct > 70) volumeFill.style.background = '#C03D20';
        else if (pct > 30) volumeFill.style.background = '#2D6A4F';
        else volumeFill.style.background = '#9E9890';
      }

      // --- 無音自動停止 (C) ---
      if (!isRecording) return;

      const isSilent = rms < SILENCE_THRESHOLD;
      const now      = Date.now();

      // 発話検出
      if (!isSilent && speechStart === null) {
        speechStart = now;
        silenceStart = null;
      }

      // 最低発話時間を経過した後のみ自動停止を有効にする
      const hasSpeech = speechStart !== null && (now - speechStart) >= MIN_SPEECH_MS;

      if (hasSpeech) {
        if (isSilent) {
          if (silenceStart === null) silenceStart = now;
          const silenceMs = now - silenceStart;
          const remaining = Math.ceil((SILENCE_DURATION - silenceMs) / 1000);

          // カウントダウン表示
          if (autoStop && silenceMs > SILENCE_DURATION * 0.5) {
            autoStop.style.display = 'block';
            autoStop.textContent = `無音検知 — ${remaining}秒後に自動停止`;
          }

          // 自動停止
          if (silenceMs >= SILENCE_DURATION) {
            triggerAutoStop();
          }
        } else {
          // 発話再開 → リセット
          silenceStart = null;
          if (autoStop) autoStop.style.display = 'none';
        }
      }
    }

    // ── 自動停止トリガー (C) ────────────────────────────
    function triggerAutoStop() {
      if (!isRecording) return;
      if (autoStop) { autoStop.textContent = '自動停止しました'; }
      setTimeout(() => {
        btnStop.click();
        if (autoStop) autoStop.style.display = 'none';
      }, 200);
    }

    let lipRecorder       = null;
    let lipChunks         = [];
    let lipMode           = null;
    let lipRefUploaded    = false;
    let lipTestUploaded   = false;
    let hasSavedRef       = false;
    let lipUploadPromise  = Promise.resolve();
    const currentWordId   = window.CURRENT_WORD_ID || '';

    function updateLipButtons() {
      if (btnLipTest) {
        btnLipTest.disabled = !(lipRefUploaded || hasSavedRef) || !!lipRecorder;
      }
      if (btnLipRef) {
        btnLipRef.disabled = !!lipRecorder;
      }
    }

    async function checkSavedRef() {
      if (!currentWordId) return;
      try {
        const response = await fetch(`/api/lip_refs/${encodeURIComponent(currentWordId)}/ratios`);
        if (response.ok) {
          const result = await response.json();
          if (Array.isArray(result.ratios) && result.ratios.length > 0) {
            hasSavedRef = true;
            setLipStatus('');
            // お手本登録済みバッジを表示し、録画ボタンを隠す
            const tag = document.getElementById('lipRefTag');
            if (tag) tag.classList.add('show');
            if (btnLipRef) btnLipRef.style.display = 'none';
          } else {
            setLipStatus('お手本未登録 — 右の「お手本を録画」で先に3秒間登録してください');
          }
        }
      } catch (err) {
        console.warn('Saved ref check failed', err);
      }
      updateLipButtons();
    }

    function setLipStatus(message) {
      if (lipStatus) lipStatus.textContent = message;
    }

    // durationMs: 指定ありは自動停止あり（お手本用）、null は手動停止（テスト用）
    function startLipRecording(mode, durationMs = null) {
      // ref は Julius アライメント用に音声も必要なので full stream を使う
      // test は映像のみで十分（音声は別途 AudioWorklet で録音済み）
      const recordStream = mode === 'ref' ? stream : videoStream;
      if (!recordStream) return;
      if (lipRecorder) return;
      lipMode = mode;
      lipChunks.length = 0;
      const mimeType = mode === 'ref' ? 'video/webm' : 'video/webm;codecs=vp8';
      lipRecorder = new MediaRecorder(recordStream, { mimeType });

      lipRecorder.addEventListener('dataavailable', event => {
        if (event.data && event.data.size > 0) {
          lipChunks.push(event.data);
        }
      });
      lipRecorder.addEventListener('stop', () => {
        const blob = new Blob(lipChunks, { type: 'video/webm' });
        lipUploadPromise = sendLipVideo(mode, blob);
        lipRecorder = null;
        lipMode = null;
        updateLipButtons();
      });

      lipRecorder.start();
      setLipStatus(mode === 'ref' ? 'お手本動画を録画中…' : 'テスト動画を録画中…');
      updateLipButtons();
      if (durationMs) {
        setTimeout(() => {
          if (lipRecorder && lipRecorder.state === 'recording') {
            lipRecorder.stop();
          }
        }, durationMs);
      }
    }

    async function sendLipVideo(mode, blob) {
      try {
        const formData = new FormData();
        formData.append('mode', mode);
        formData.append('file', blob, `${mode}.webm`);
        const response = await fetch('/upload_lip_video', {
          method: 'POST',
          body: formData,
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || '唇動画のアップロードに失敗しました');
        }
        if (mode === 'ref') {
          lipRefUploaded = true;
          const tag = document.getElementById('lipRefTag');
          if (tag) tag.classList.add('show');
          if (btnLipRef) btnLipRef.style.display = 'none';
          // アライメント成否をユーザーに通知
          if (data.alignment_ok) {
            setLipStatus(`お手本登録完了（${data.mora_count}モーラ分析済み）`);
            setTimeout(() => setLipStatus(''), 3000);
          } else {
            setLipStatus('⚠ 音声認識に失敗しました。はっきり発音して録画し直してください。');
          }
        } else {
          lipTestUploaded = true;
          setLipStatus('');
        }
      } catch (err) {
        console.error(err);
        setLipStatus('唇動画のアップロードに失敗しました。再度お試しください。');
      }
    }

    const vizCard = document.querySelector('.viz-card');

    // ── ボタンイベント ──────────────────────────────────
    btnStart.addEventListener('click', () => {
      // ③ 録音済みの場合は確認ダイアログを表示
      if (hasRecording) {
        const ok = confirm('前の録音を破棄して録音し直しますか？');
        if (!ok) return;
      }

      btnStart.setAttribute('disabled', 'disabled');
      btnStop.removeAttribute('disabled');
      btnGraph.style.display = 'none';
      if (autoStop) autoStop.style.display = 'none';
      if (vizCard) vizCard.classList.add('recording');

      // 状態リセット
      isRecording  = true;
      window._spRecording = true;
      hasRecording = false;
      speechStart  = null;
      silenceStart = null;
      buffers.splice(0, buffers.length);

      // 録音開始と同時に唇のテスト動画を自動録画（保存済みお手本がある場合）
      try {
        if ((lipRefUploaded || hasSavedRef) && !lipRecorder) {
          startLipRecording('test');
        }
      } catch (e) { console.warn('auto lip record failed', e); }

      const param = audioRecorder.parameters.get('isRecording');
      param.setValueAtTime(1, audioContext.currentTime);
    });

    btnStop.addEventListener('click', () => {
      btnStop.setAttribute('disabled', 'disabled');
      btnStart.removeAttribute('disabled');
      if (vizCard) vizCard.classList.remove('recording');
      isRecording  = false;
      window._spRecording = false;
      hasRecording = true;   // ③ 録音完了フラグ
      speechStart  = null;
      silenceStart = null;

      audio.setAttribute('controlsList', 'nodownload');
      const param = audioRecorder.parameters.get('isRecording');
      param.setValueAtTime(0, audioContext.currentTime);

      // 音声停止と同時に唇テスト録画も停止（音素タイムスタンプとの同期を保つため）
      if (lipRecorder && lipRecorder.state === 'recording') {
        lipRecorder.stop();
      }

      const blob = encodeAudio(buffers, settings);
      const audioPromise = sendAudio(blob);
      audio.src = URL.createObjectURL(blob);

      // 音声・唇動画のアップロードが両方完了したら解析ボタンを表示
      // 200ms 待つことで lipRecorder の 'stop' イベントが先に発火して
      // lipUploadPromise が更新されるのを確実にする
      setTimeout(() => {
        Promise.all([audioPromise, lipUploadPromise])
          .finally(() => { btnGraph.style.display = 'block'; });
      }, 200);
    });

    if (btnLipRef) {
      // お手本は手動ボタン操作 → 3秒で自動停止
      btnLipRef.addEventListener('click', () => startLipRecording('ref', 3000));
    }
    if (btnLipTest) {
      btnLipTest.addEventListener('click', () => startLipRecording('test'));
    }

    updateLipButtons();
    checkSavedRef();

    // ── マイク品質チェック（録音前の背景ノイズ測定） ────────────
    const qualityBanner = document.getElementById('micQualityBanner');
    (async function checkMicQuality() {
      const SAMPLE_MS = 1500;
      const samples   = [];
      const start     = Date.now();
      while (Date.now() - start < SAMPLE_MS) {
        analyser.getByteTimeDomainData(timeData);
        samples.push(getRMS(timeData));
        await new Promise(r => setTimeout(r, 80));
      }
      if (!qualityBanner) return;
      const avg = samples.reduce((a, b) => a + b, 0) / samples.length;
      const max = Math.max(...samples);
      if (avg < 0.002) {
        qualityBanner.textContent = '⚠ マイクの音量が非常に小さいです。マイクが正しく接続されているか確認してください。';
        qualityBanner.className = 'mic-quality-warn warn-low';
        qualityBanner.style.display = 'flex';
      } else if (avg > 0.05) {
        qualityBanner.textContent = '⚠ 背景ノイズが大きいです。静かな場所で録音するとスコアが上がります。';
        qualityBanner.className = 'mic-quality-warn warn-noise';
        qualityBanner.style.display = 'flex';
      } else {
        qualityBanner.style.display = 'none';
      }
    })();

    drawLoop();

  } catch (err) {
    console.error(err);
    const msg = document.getElementById('micError');
    if (msg) { msg.style.display = 'block'; msg.textContent = 'マイクにアクセスできませんでした。ブラウザの権限を確認してください。'; }
  }
}

// ── WAV エンコード ──────────────────────────────────────
function encodeAudio(buffers, settings) {
  const sampleCount = buffers.reduce((acc, buf) => acc + buf.length, 0);
  const bytesPerSample = (settings.sampleSize || 16) / 8;
  const dataLength     = sampleCount * bytesPerSample;
  const sampleRate     = 16000;
  const ab  = new ArrayBuffer(44 + dataLength);
  const dv  = new DataView(ab);

  const set8  = (o, s) => [...s].forEach((c, i) => dv.setUint8(o + i, c.charCodeAt(0)));
  set8(0, 'RIFF'); dv.setUint32(4, 36 + dataLength, true);
  set8(8, 'WAVE'); set8(12, 'fmt ');
  dv.setUint32(16, 16, true);
  dv.setUint16(20, 1, true);
  dv.setUint16(22, 1, true);
  dv.setUint32(24, sampleRate, true);
  dv.setUint32(28, sampleRate * 2, true);
  dv.setUint16(32, bytesPerSample, true);
  dv.setUint16(34, 8 * bytesPerSample, true);
  set8(36, 'data'); dv.setUint32(40, dataLength, true);

  let idx = 44;
  for (const buf of buffers) {
    for (const v of buf) {
      dv.setInt16(idx, v * 0x7fff, true);
      idx += 2;
    }
  }
  return new Blob([dv], { type:'audio/wav' });
}

// ── 送信 ────────────────────────────────────────────────
function sendAudio(blob) {
  const fd = new FormData();
  fd.append('file', blob, 'test.wav');
  return fetch('/audio', { method:'POST', body:fd }).catch(console.error);
}

main();

// ── Service Worker 登録 ──────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/js/sw.js').catch(() => {});
  });
}