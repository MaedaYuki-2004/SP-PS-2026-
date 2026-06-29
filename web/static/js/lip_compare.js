const preview = document.getElementById('preview');
const btnRef = document.getElementById('buttonRef');
const btnTest = document.getElementById('buttonTest');
const btnCompare = document.getElementById('buttonCompare');
const previewStatus = document.getElementById('previewStatus');
const recordStatus = document.getElementById('recordStatus');
const resultPanel = document.getElementById('resultPanel');
const resultScore = document.getElementById('resultScore');
const resultLabel = document.getElementById('resultLabel');
const graphCanvas = document.getElementById('graphCanvas');
const graphCtx = graphCanvas.getContext('2d');

let mediaStream = null;
let recorder = null;
let chunks = [];
let refBlob = null;
let testBlob = null;
let currentMode = null;

const RECORD_DURATION_MS = 3000;

async function initCamera() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    preview.srcObject = mediaStream;
    previewStatus.textContent = 'カメラ接続済み';
  } catch (err) {
    console.error(err);
    previewStatus.textContent = 'カメラのアクセスに失敗しました。権限を確認してください。';
    btnRef.disabled = true;
    btnTest.disabled = true;
    btnCompare.disabled = true;
  }
}

function setStatus(message) {
  recordStatus.textContent = message;
}

function startRecording(mode) {
  if (!mediaStream || recorder) return;
  currentMode = mode;
  chunks = [];
  recorder = new MediaRecorder(mediaStream, { mimeType: 'video/webm;codecs=vp8' });
  recorder.addEventListener('dataavailable', event => {
    if (event.data && event.data.size > 0) {
      chunks.push(event.data);
    }
  });
  recorder.addEventListener('stop', () => {
    const blob = new Blob(chunks, { type: 'video/webm' });
    if (mode === 'ref') {
      refBlob = blob;
      btnTest.disabled = false;
      setStatus('お手本の録画が完了しました。次にテストを録画してください。');
    } else {
      testBlob = blob;
      btnCompare.disabled = false;
      setStatus('テストの録画が完了しました。比較ボタンを押してください。');
    }
    recorder = null;
  });

  recorder.start();
  setStatus(mode === 'ref' ? 'お手本録画中…' : 'テスト録画中…');
  btnRef.disabled = true;
  btnTest.disabled = true;
  btnCompare.disabled = true;
  previewStatus.textContent = mode === 'ref' ? 'お手本を録画しています' : 'テストを録画しています';

  setTimeout(() => {
    if (recorder && recorder.state === 'recording') {
      recorder.stop();
    }
  }, RECORD_DURATION_MS);
}

async function sendCompare() {
  if (!refBlob || !testBlob) {
    return;
  }
  btnCompare.disabled = true;
  setStatus('比較を実行しています…');

  const formData = new FormData();
  formData.append('ref_video', refBlob, 'ref.webm');
  formData.append('test_video', testBlob, 'test.webm');

  try {
    const response = await fetch('/lip_compare', {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || '比較に失敗しました');
    }
    displayResult(data);
  } catch (err) {
    console.error(err);
    setStatus('比較に失敗しました。再度お試しください。');
  } finally {
    btnCompare.disabled = false;
  }
}

function displayResult(data) {
  resultPanel.style.display = 'grid';
  resultScore.textContent = `${data.score} / 100`;
  resultLabel.textContent = `距離: ${data.distance.toFixed(2)} (低いほど一致)`;
  drawGraph(data.ref_ratios, data.test_ratios);
  setStatus('比較が完了しました。結果を確認してください。');
}

function drawGraph(refRatios, testRatios) {
  const width = graphCanvas.width;
  const height = graphCanvas.height;
  const maxLen = Math.max(refRatios.length, testRatios.length);
  const padding = 24;
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;
  const maxValue = Math.max(...refRatios, ...testRatios, 0.1);

  graphCtx.clearRect(0, 0, width, height);
  graphCtx.fillStyle = '#090b11';
  graphCtx.fillRect(0, 0, width, height);
  graphCtx.strokeStyle = 'rgba(255,255,255,0.08)';
  graphCtx.lineWidth = 1;
  graphCtx.beginPath();
  graphCtx.moveTo(padding, padding + innerHeight / 2);
  graphCtx.lineTo(width - padding, padding + innerHeight / 2);
  graphCtx.stroke();

  function drawLine(values, color) {
    graphCtx.strokeStyle = color;
    graphCtx.lineWidth = 3;
    graphCtx.beginPath();
    values.forEach((value, index) => {
      const x = padding + (innerWidth * index) / Math.max(maxLen - 1, 1);
      const y = padding + innerHeight - (value / maxValue) * innerHeight;
      if (index === 0) graphCtx.moveTo(x, y);
      else graphCtx.lineTo(x, y);
    });
    graphCtx.stroke();
  }

  drawLine(refRatios, 'rgba(255,255,255,0.95)');
  drawLine(testRatios, 'rgba(255,64,64,0.95)');
}

btnRef.addEventListener('click', () => startRecording('ref'));
btnTest.addEventListener('click', () => {
  if (!refBlob) {
    setStatus('先にお手本を録画してください。');
    return;
  }
  startRecording('test');
});
btnCompare.addEventListener('click', sendCompare);

initCamera();
