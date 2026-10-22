/* SP-PS カメラと録画の設定（カメラを使うページで読み込む）
 *
 * ・カメラの大きさ
 *     getUserMedia に { video: true } だけを渡すと、どの大きさで撮るかは端末とブラウザ任せになり、
 *     スマホによっては 4K などの大きな映像になる。録画を送る量が増え、スマホの録画（圧縮）と、
 *     サーバーの口の解析（1コマずつほどいて顔を探す）も重くなる。
 *     口の解析には 640×480 で足りるので、正面のカメラに 640×480・毎秒 30 コマを頼む。
 *     どれも ideal（なるべく）なので、その大きさが無い端末でも、いちばん近い大きさで動く。
 * ・録画
 *     1 Mbps にする（ブラウザ任せだと Chrome で約 2.6 Mbps、Safari で約 8 Mbps になり、Mac の細い
 *     上り・下りで送信に数十秒かかった。口の開き具合の差は動く範囲の 2.5% ほど。README §5.4）。
 *     形式はその端末で録れるものを選ぶ（WebM を優先し、録れない端末では MP4）。
 *     決め打ちにすると、録れない端末では録画そのものが始まらない。
 *
 * 使い方：
 *   getUserMedia({ video: SPMedia.video(), audio: true })
 *   new MediaRecorder(stream, SPMedia.recorderOptions())
 * このファイルが読めなかったときに備え、使う側は window.SPMedia が無いときの代わりも書いておく。
 */
(function () {
  'use strict';

  var VIDEO_BPS = 1000000;
  var TYPES = ['video/webm;codecs=vp8', 'video/webm', 'video/mp4'];

  function video() {
    return {
      facingMode: 'user',
      width: { ideal: 640 },
      height: { ideal: 480 },
      frameRate: { ideal: 30 }
    };
  }

  function recorderOptions() {
    var opts = { videoBitsPerSecond: VIDEO_BPS };
    if (window.MediaRecorder && typeof MediaRecorder.isTypeSupported === 'function') {
      for (var i = 0; i < TYPES.length; i++) {
        if (MediaRecorder.isTypeSupported(TYPES[i])) { opts.mimeType = TYPES[i]; break; }
      }
    }
    return opts;
  }

  window.SPMedia = { video: video, recorderOptions: recorderOptions, VIDEO_BPS: VIDEO_BPS };
})();
