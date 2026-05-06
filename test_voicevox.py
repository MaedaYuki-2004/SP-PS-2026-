import urllib.request
import urllib.parse
import json
import os

BASE_URL = "http://127.0.0.1:50021"

def test_voicevox():
    print("=== VOICEVOX 動作確認 ===")

    # ① 接続確認
    try:
        req = urllib.request.urlopen(f"{BASE_URL}/version", timeout=5)
        version = req.read().decode()
        print(f"[OK] VOICEVOX接続成功 バージョン: {version}")
    except Exception as e:
        print(f"[NG] VOICEVOX に接続できません: {e}")
        print("     VOICEVOX が起動しているか確認してください")
        return

    # ② 音声生成テスト（「夕食」）
    text    = "夕食"
    speaker = 1  # ずんだもん

    print(f"\n音声生成テスト: 「{text}」")

    # audio_query は POST
    query_url = f"{BASE_URL}/audio_query?text={urllib.parse.quote(text)}&speaker={speaker}"
    req = urllib.request.Request(
        query_url,
        data=b"",  # POST として送る
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        query = json.loads(res.read().decode())
    print("[OK] audio_query 成功")

    # synthesis も POST
    data = json.dumps(query).encode("utf-8")
    req  = urllib.request.Request(
        f"{BASE_URL}/synthesis?speaker={speaker}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        wav_data = res.read()

    out_path = "test_voicevox_output.wav"
    with open(out_path, "wb") as f:
        f.write(wav_data)

    size = os.path.getsize(out_path)
    print(f"[OK] 音声生成成功: {out_path} ({size:,} bytes)")
    print("\ntest_voicevox_output.wav を再生して音声を確認してください")

test_voicevox()