"""
check_voicevox_speakers.py
VOICEVOX で使える話者とスタイルの一覧を表示するスクリプト。

使い方：
    1. VOICEVOX を起動する
    2. python scripts/check_voicevox_speakers.py を実行する
    3. 表示された一覧から使いたい話者のIDをメモする
"""
import urllib.request
import json

VOICEVOX_URL = "http://127.0.0.1:50021"

def main():
    # 接続確認
    try:
        with urllib.request.urlopen(f"{VOICEVOX_URL}/version", timeout=5) as res:
            version = res.read().decode().strip()
        print(f"VOICEVOX バージョン: {version}\n")
    except Exception:
        print("[エラー] VOICEVOX に接続できません。VOICEVOXが起動しているか確認してください。")
        return

    # 話者一覧を取得
    try:
        with urllib.request.urlopen(f"{VOICEVOX_URL}/speakers", timeout=10) as res:
            speakers = json.loads(res.read().decode())
    except Exception as e:
        print(f"[エラー] 話者一覧の取得に失敗しました: {e}")
        return

    print(f"{'ID':>4}  {'話者名':<20}  スタイル")
    print("-" * 50)

    for speaker in speakers:
        name = speaker["name"]
        for style in speaker["styles"]:
            sid    = style["id"]
            sname  = style["name"]
            marker = "  ← 現在使用中" if sid == 11 else ""
            print(f"{sid:>4}  {name:<20}  {sname}{marker}")

    print("\n使いたい話者のIDをメモして、regenerate_all_tts.py の")
    print("SPEAKER_ID = の値を変更してから実行してください。")

if __name__ == "__main__":
    main()