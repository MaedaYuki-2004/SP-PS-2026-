// カメラ・マイクが使えなかったときに、原因ごとに「どうすればいいか」がわかる文を返す。
// 以前はどの原因でも「カメラの起動に失敗しました」としか出ず、先生も生徒も直し方がわからなかった。
// 文の最後に（原因: NotAllowedError など）をつけておくと、先生から問い合わせを受けたときに原因を絞り込める。
(function () {
  const ua = navigator.userAgent || '';
  const isIOS = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const isAndroid = /Android/.test(ua);
  // LINE・Instagram・Facebook・WeChat・KakaoTalk の中のブラウザはカメラを使わせないことが多い
  const inAppName = /\bLine\//i.test(ua) ? 'LINE'
    : /Instagram/i.test(ua) ? 'Instagram'
    : /FBAN|FBAV/.test(ua) ? 'Facebook'
    : /MicroMessenger/i.test(ua) ? 'WeChat'
    : /KAKAOTALK/i.test(ua) ? 'KakaoTalk'
    : null;
  const browserName = isIOS ? 'Safari' : 'Chrome';

  function permissionHint() {
    if (isIOS) return 'アドレスバーの左の「ぁあ」→「Webサイトの設定」で、カメラとマイクを「許可」にして、ページを読み込み直してください。それでもだめなときは、iPhoneの「設定」→「Safari」→「カメラ」「マイク」を「許可」にしてください。';
    if (isAndroid) return 'アドレスバーの左のマークを押して「権限」を開き、カメラとマイクを許可して、ページを読み込み直してください。';
    return 'アドレスバーの左のマークを押して、カメラとマイクを許可して、ページを読み込み直してください。Macでは「システム設定」→「プライバシーとセキュリティ」の「カメラ」「マイク」で、使っているブラウザをオンにしてください。';
  }

  // getUserMedia を呼ぶ前に、そもそも使えない状態かを調べる。使えるなら null。
  function cameraUnavailableMessage() {
    if (!window.isSecureContext) {
      return 'このアドレスではカメラを使えません。「https://」で始まるアドレス（配布用のQRコードのアドレス）で開き直してください。（原因: not-https）';
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      if (inAppName) return `${inAppName}の中ではカメラを使えません。右上（または右下）のメニューから「${browserName}で開く」を選んで開き直してください。（原因: in-app-${inAppName}）`;
      return `このブラウザではカメラを使えません。${browserName}で開き直してください。（原因: no-mediaDevices）`;
    }
    return null;
  }

  function cameraErrorMessage(err) {
    const pre = cameraUnavailableMessage();
    if (pre) return pre;
    const name = (err && err.name) || 'Error';
    const tag = `（原因: ${name}）`;
    if (inAppName) {
      return `${inAppName}の中ではカメラを使えないことがあります。右上（または右下）のメニューから「${browserName}で開く」を選んで開き直してください。${tag}`;
    }
    switch (name) {
      case 'NotAllowedError':
      case 'PermissionDeniedError':
      case 'SecurityError':
        return 'カメラかマイクの使用が許可されていません。' + permissionHint() + tag;
      case 'NotFoundError':
      case 'DevicesNotFoundError':
      case 'OverconstrainedError':
        return 'カメラかマイクが見つかりません。カメラとマイクのついた端末で開くか、つながっているか確かめてください。' + tag;
      case 'NotReadableError':
      case 'TrackStartError':
      case 'AbortError':
        return 'カメラかマイクがほかで使われています。Zoom・カメラアプリや、このアプリのほかのタブを閉じてから、もう一度ためしてください。' + tag;
      default:
        return 'カメラの起動に失敗しました。ページを読み込み直して、もう一度ためしてください。' + tag;
    }
  }

  window.cameraUnavailableMessage = cameraUnavailableMessage;
  window.cameraErrorMessage = cameraErrorMessage;
})();
