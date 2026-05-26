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
    const canvas        = document.querySelector('#visualizer');
    const canvasCtx     = canvas.getContext('2d');
    const btnStart      = document.querySelector('#buttonStart');
    const btnStop       = document.querySelector('#buttonStop');
    const btnGraph      = document.querySelector('#buttongraph');
    const audio         = document.querySelector('#audio');
    const videoPreview  = document.querySelector('#cameraPreview');
    const cameraPrediction = document.querySelector('#cameraPrediction');
    const cameraError   = document.querySelector('#cameraError');
    const cameraOverlay = document.querySelector('#cameraOverlay');
    const cameraOverlayCtx = cameraOverlay?.getContext('2d');
    const volumeBar     = document.querySelector('#volumeBar');       // B
    const volumeFill    = document.querySelector('#volumeFill');      // B
    const autoStop      = document.querySelector('#autoStopCountdown'); // C

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video:true, audio:true });
    } catch (err) {
      console.warn('カメラ付きのアクセスに失敗しました。音声のみで録音します。', err);
      stream = await navigator.mediaDevices.getUserMedia({ audio:true });
      if (cameraError) {
        cameraError.style.display = 'block';
        cameraError.textContent = 'カメラにアクセスできませんでした。音声録音のみ行います。';
      }
    }
    const [track] = stream.getAudioTracks();
    const settings = track.getSettings();
    let lastFaceMeshTimestamp = 0;
    const FACE_MESH_INTERVAL_MS = 100;

    const faceMesh = new FaceMesh({
      locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh@0.4/${file}`
    });
    faceMesh.setOptions({
      maxNumFaces: 1,
      refineLandmarks: true,
      minDetectionConfidence: 0.5,
      minTrackingConfidence: 0.5,
    });
    faceMesh.onResults(onFaceMeshResults);

    if (videoPreview && stream.getVideoTracks().length > 0) {
      videoPreview.srcObject = stream;
      videoPreview.muted = true;
      videoPreview.playsInline = true;
      videoPreview.style.display = 'block';
      try {
        await videoPreview.play();
      } catch (playError) {
        console.warn('カメラ再生開始に失敗しました', playError);
      }
      if (cameraPrediction) {
        cameraPrediction.textContent = '予測：準備中...';
      }
    }

    function onFaceMeshResults(results) {
      if (!cameraPrediction) return;
      if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
        cameraPrediction.textContent = '予測：顔または口が検出されません';
        if (cameraOverlayCtx) {
          cameraOverlayCtx.clearRect(0, 0, cameraOverlay.width, cameraOverlay.height);
        }
        return;
      }
      const vowel = predictVowelFromLandmarks(results.multiFaceLandmarks[0]);
      cameraPrediction.textContent = `予測：${vowel}`;
      drawLipPoints(results.multiFaceLandmarks[0]);
    }

    function updateCameraOverlaySize() {
      if (!videoPreview || !cameraOverlay) return;
      const width = videoPreview.videoWidth || videoPreview.clientWidth;
      const height = videoPreview.videoHeight || videoPreview.clientHeight;
      cameraOverlay.width = width;
      cameraOverlay.height = height;
      cameraOverlay.style.display = width && height ? 'block' : 'none';
    }

    function drawLipPoints(landmarks) {
      if (!cameraOverlayCtx || !videoPreview) return;
      updateCameraOverlaySize();
      cameraOverlayCtx.clearRect(0, 0, cameraOverlay.width, cameraOverlay.height);
      cameraOverlayCtx.fillStyle = 'rgba(0, 255, 0, 0.85)';
      cameraOverlayCtx.strokeStyle = 'rgba(0, 255, 0, 0.9)';
      cameraOverlayCtx.lineWidth = 2;
      const points = [13, 14, 61, 291];
      for (const index of points) {
        const pt = landmarks[index];
        const x = pt.x * cameraOverlay.width;
        const y = pt.y * cameraOverlay.height;
        cameraOverlayCtx.beginPath();
        cameraOverlayCtx.arc(x, y, 5, 0, Math.PI * 2);
        cameraOverlayCtx.fill();
        cameraOverlayCtx.stroke();
      }
    }

    function predictVowelTree(node, features) {
      if (node.leaf) return node.value;
      const branch = features[node.feature] <= node.threshold ? node.left : node.right;
      return predictVowelTree(branch, features);
    }

    function predictVowelFromLandmarks(landmarks) {
      if (!window.VOWEL_FOREST || !window.VOWEL_CLASSES) {
        return '準備中';
      }
      const w = videoPreview.videoWidth;
      const h = videoPreview.videoHeight;
      const top = landmarks[13];
      const bottom = landmarks[14];
      const left = landmarks[61];
      const right = landmarks[291];
      const vertical = Math.hypot((bottom.x - top.x) * w, (bottom.y - top.y) * h);
      const horizontal = Math.hypot((right.x - left.x) * w, (right.y - left.y) * h);
      const ratio = vertical / (horizontal + 1e-6);
      const features = [vertical, horizontal, ratio];
      const votes = new Array(window.VOWEL_CLASSES.length).fill(0);
      for (const tree of window.VOWEL_FOREST) {
        const leafValue = predictVowelTree(tree, features);
        const labelIndex = leafValue.indexOf(Math.max(...leafValue));
        if (labelIndex >= 0) votes[labelIndex]++;
      }
      const winner = votes.indexOf(Math.max(...votes));
      return window.VOWEL_CLASSES[winner] || '--';
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

    // ── 状態管理 ───────────────────────────────────────
    let isRecording    = false;
    let speechStart    = null;   // 最初の発話を検出した時刻
    let silenceStart   = null;   // 無音開始時刻
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
      if (videoPreview && videoPreview.readyState >= 2 && performance.now() - lastFaceMeshTimestamp > FACE_MESH_INTERVAL_MS) {
        faceMesh.send({ image: videoPreview }).catch(() => {});
        lastFaceMeshTimestamp = performance.now();
      }

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

    // ── ボタンイベント ──────────────────────────────────
    btnStart.addEventListener('click', () => {
      btnStart.setAttribute('disabled', 'disabled');
      btnStop.removeAttribute('disabled');
      btnGraph.style.display = 'none';
      if (autoStop) autoStop.style.display = 'none';

      // 状態リセット
      isRecording  = true;
      speechStart  = null;
      silenceStart = null;
      buffers.splice(0, buffers.length);
      if (cameraPrediction) {
        cameraPrediction.textContent = '予測：準備中...';
      }

      const param = audioRecorder.parameters.get('isRecording');
      param.setValueAtTime(1, audioContext.currentTime);
    });

    btnStop.addEventListener('click', () => {
      btnStop.setAttribute('disabled', 'disabled');
      btnStart.removeAttribute('disabled');
      isRecording  = false;
      speechStart  = null;
      silenceStart = null;
      if (cameraPrediction) {
        cameraPrediction.textContent = '予測：停止中';
      }

      audio.setAttribute('controlsList', 'nodownload');
      const param = audioRecorder.parameters.get('isRecording');
      param.setValueAtTime(0, audioContext.currentTime);

      const blob = encodeAudio(buffers, settings);
      sendAudio(blob);
      audio.src = URL.createObjectURL(blob);

      // 3秒後に解析ボタンを表示
      setTimeout(() => { btnGraph.style.display = 'block'; }, 3000);
    });

    drawLoop();

  } catch (err) {
    console.error(err);
    if (cameraError) {
      cameraError.style.display = 'block';
      cameraError.textContent = 'マイクまたはカメラにアクセスできませんでした。ブラウザの権限を確認してください。';
    }
    const msg = document.getElementById('micError');
    if (msg) { msg.style.display = 'block'; msg.textContent = 'マイクにアクセスできませんでした。ブラウザの権限を確認してください。'; }
  }
}

// ── WAV エンコード ──────────────────────────────────────
function encodeAudio(buffers, settings) {
  const sampleCount = buffers.reduce((acc, buf) => acc + buf.length, 0);
  const bytesPerSample = settings.sampleSize / 8;
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
  fetch('/audio', { method:'POST', body:fd }).catch(console.error);
}

main();