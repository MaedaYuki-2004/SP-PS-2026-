"""
setup_mfa.py
MFA（Montreal Forced Aligner）のインストール状況を確認し、
必要なモデルをダウンロードするスクリプト。

【実行前の前提条件】
    conda が使えること（Miniconda / Anaconda がインストール済みであること）

【実行手順】
    1. conda 環境を作る（初回のみ）
       conda create -n mfa -c conda-forge montreal-forced-aligner python=3.10
       conda activate mfa

    2. このスクリプトを実行する
       python scripts/setup_mfa.py

    3. config.py の USE_MFA を True に変更する
"""
import shutil
import subprocess
import sys


def run(cmd: list[str]) -> tuple[int, str, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def main() -> None:
    print("=" * 60)
    print("  MFA セットアップ確認スクリプト")
    print("=" * 60)

    # ── 1. MFA が使えるか確認 ─────────────────────────────────────
    mfa_path = shutil.which("mfa")
    if mfa_path is None:
        print("\n[NG] mfa コマンドが見つかりません。")
        print("     以下のコマンドで MFA をインストールしてください：")
        print()
        print("     conda create -n mfa -c conda-forge montreal-forced-aligner python=3.10")
        print("     conda activate mfa")
        sys.exit(1)

    print(f"\n[OK] mfa が見つかりました: {mfa_path}")

    # バージョン確認
    code, out, _ = run(["mfa", "version"])
    if code == 0:
        print(f"     バージョン: {out.strip()}")

    # ── 2. 日本語音響モデルのダウンロード ────────────────────────
    print("\n  日本語音響モデル（japanese_mfa）を確認中...")
    code, out, err = run(["mfa", "model", "inspect", "acoustic", "japanese_mfa"])
    if code == 0:
        print("[OK] japanese_mfa 音響モデルはインストール済みです")
    else:
        print("[--] japanese_mfa 音響モデルが見つかりません。ダウンロードします...")
        code, out, err = run(["mfa", "model", "download", "acoustic", "japanese_mfa"])
        if code == 0:
            print("[OK] 音響モデルのダウンロード完了")
        else:
            print(f"[NG] ダウンロード失敗: {err.strip()}")
            sys.exit(1)

    # ── 3. 日本語発音辞書のダウンロード ──────────────────────────
    print("\n  日本語発音辞書（japanese_mfa）を確認中...")
    code, out, err = run(["mfa", "model", "inspect", "dictionary", "japanese_mfa"])
    if code == 0:
        print("[OK] japanese_mfa 辞書はインストール済みです")
    else:
        print("[--] japanese_mfa 辞書が見つかりません。ダウンロードします...")
        code, out, err = run(["mfa", "model", "download", "dictionary", "japanese_mfa"])
        if code == 0:
            print("[OK] 辞書のダウンロード完了")
        else:
            print(f"[NG] ダウンロード失敗: {err.strip()}")
            sys.exit(1)

    # ── 4. 設定の案内 ────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  セットアップ完了！")
    print()
    print("  次のステップ：")
    print("  config.py を開いて USE_MFA = True に変更してください。")
    print()
    print("  変更箇所：")
    print("    USE_MFA = False   ← True に変更")
    print("=" * 60)


if __name__ == "__main__":
    main()