async function main () {
  try {
    const canvas = document.querySelector('#visualizer');
    const canvasCtx = canvas.getContext("2d");
    const buttonStart = document.querySelector('#buttonStart')
    const buttonStop = document.querySelector('#buttonStop')
    const buttongraph = document.querySelector("#buttongraph")
    const audio = document.querySelector('#audio')

    const stream = await navigator.mediaDevices.getUserMedia({ // <1>
      vide: false,
      audio: true,
    })

    const [track] = stream.getAudioTracks()
    const settings = track.getSettings() // <2>


    const audioContext = new AudioContext({sampleRate: 16000}) 
    await audioContext.audioWorklet.addModule('/static/js/audio_recorder.js') // <3>

    const mediaStreamSource = audioContext.createMediaStreamSource(stream) // <4>
    const audioRecorder = new AudioWorkletNode(audioContext, 'audio-recorder') // <5>
    const buffers = []

    audioRecorder.port.addEventListener('message', event => { // <6>
      buffers.push(event.data.buffer)
    })
    audioRecorder.port.start() // <7>

    mediaStreamSource.connect(audioRecorder) // <8>
    audioRecorder.connect(audioContext.destination)

    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 2048;
    const bufferLength = analyser.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    mediaStreamSource.connect(analyser);

    draw()
    buttonStart.addEventListener('click', event => {
      buttonStart.setAttribute('disabled', 'disabled')
      buttonStop.removeAttribute('disabled')
      buttongraph.style.display = 'none'

      const parameter = audioRecorder.parameters.get('isRecording')
      parameter.setValueAtTime(1, audioContext.currentTime) // <9>

      buffers.splice(0, buffers.length)
    })

    buttonStop.addEventListener('click', event => {
      buttonStop.setAttribute('disabled', 'disabled')
      buttonStart.removeAttribute('disabled')
    
      audio.setAttribute('controlsList', 'nodownload');

      const parameter = audioRecorder.parameters.get('isRecording')
      parameter.setValueAtTime(0, audioContext.currentTime) // <10>

      const blob = encodeAudio(buffers, settings) // <11>
      sendaudio(blob);
      const url = URL.createObjectURL(blob)

      audio.src = url
      start(); //スリープ機能
    })


    function draw() {
      const WIDTH = canvas.width
      const HEIGHT = canvas.height;

      requestAnimationFrame(draw);

      analyser.getByteTimeDomainData(dataArray);

      canvasCtx.fillStyle = 'rgb(255, 255, 255)';
      canvasCtx.fillRect(0, 0, WIDTH, HEIGHT);

      canvasCtx.lineWidth = 2;
      canvasCtx.strokeStyle = 'rgb(0, 0, 0)';

      canvasCtx.beginPath();

      let sliceWidth = WIDTH * 1.0 / bufferLength;
      let x = 0;


      for(let i = 0; i < bufferLength; i++) {

        let v = dataArray[i] / 128.0;
        let y = (v * HEIGHT) / 2;

        if(i === 0) {
          canvasCtx.moveTo(x, y);
        } else {
          canvasCtx.lineTo(x, y);
        }

        x += sliceWidth;
      }

      canvasCtx.lineTo(canvas.width, canvas.height/2);
      canvasCtx.stroke();
    }
  } catch (err) {
    console.error(err)
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function start() {
  //3秒待機してボタンを表示させる（labファイル上書きのため）
  await sleep(3000);
  buttongraph.style.display = 'block'
}

function encodeAudio (buffers, settings) {
  const sampleCount = buffers.reduce((memo, buffer) => {
    return memo + buffer.length
  }, 0)

  const bytesPerSample = settings.sampleSize / 8
  const bitsPerByte = 8
  const dataLength = sampleCount * bytesPerSample
  const sampleRate = 16000

  const arrayBuffer = new ArrayBuffer(44 + dataLength)
  const dataView = new DataView(arrayBuffer)

  dataView.setUint8(0, 'R'.charCodeAt(0)) // <10>
  dataView.setUint8(1, 'I'.charCodeAt(0))
  dataView.setUint8(2, 'F'.charCodeAt(0))
  dataView.setUint8(3, 'F'.charCodeAt(0))
  dataView.setUint32(4, 36 + dataLength, true)
  dataView.setUint8(8, 'W'.charCodeAt(0))
  dataView.setUint8(9, 'A'.charCodeAt(0))
  dataView.setUint8(10, 'V'.charCodeAt(0))
  dataView.setUint8(11, 'E'.charCodeAt(0))
  dataView.setUint8(12, 'f'.charCodeAt(0))
  dataView.setUint8(13, 'm'.charCodeAt(0))
  dataView.setUint8(14, 't'.charCodeAt(0))
  dataView.setUint8(15, ' '.charCodeAt(0))
  dataView.setUint32(16, 16, true)
  dataView.setUint16(20, 1, true)
  dataView.setUint16(22, 1, true)
  dataView.setUint32(24, sampleRate, true)
  dataView.setUint32(28, sampleRate * 2, true)
  dataView.setUint16(32, bytesPerSample, true)
  dataView.setUint16(34, bitsPerByte * bytesPerSample, true)
  dataView.setUint8(36, 'd'.charCodeAt(0))
  dataView.setUint8(37, 'a'.charCodeAt(0))
  dataView.setUint8(38, 't'.charCodeAt(0))
  dataView.setUint8(39, 'a'.charCodeAt(0))
  dataView.setUint32(40, dataLength, true)

  let index = 44

  for (const buffer of buffers) {
    for (const value of buffer) {
      dataView.setInt16(index, value * 0x7fff, true)
      index += 2
    }
  }

  return new Blob([dataView], {type: 'audio/wav'})
}

function sendaudio(blob){
  const req = new XMLHttpRequest();
  let fd = new FormData();
  fd.append('file',blob, 'test.wav');
  req.open('POST', '/audio', true);
  req.send(fd);
  console.log('sendaudio');
}

main()