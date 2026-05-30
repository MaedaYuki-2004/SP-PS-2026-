"""
core/confidence.py
ブートストラップ法によるスコアの信頼区間推定モジュール。

【背景】
  SP-PS のスコアは1回の録音に基づいて計算される。
  しかし実際の発音能力は録音環境・マイクノイズ・
  その日のコンディションなどで数点程度ブレる。

  同じ単語を複数回録音したスコアがあれば、
  ブートストラップ法で「本当の実力がどの範囲にあるか」
  を統計的に推定できる。

【ブートストラップ法】
  1. N 回の録音スコアがある
  2. その N 個からランダムに N 個を復元抽出する（1試行）
  3. その試行の平均を記録する
  4. 1〜3 を B 回繰り返して B 個の平均の分布を作る
  5. 分布の 2.5〜97.5 パーセンタイルが 95% 信頼区間

【解釈】
  「68〜76点（95%CI）」の意味：
  → 同じ練習を繰り返した場合、スコアの平均が
    このの範囲に入る確率が95%
  → この区間内のスコアの変動は誤差の範囲内

【必要サンプル数】
  MIN_SAMPLES = 3（3回以上の録音で推定を開始）
  サンプルが多いほど区間が狭まり、推定の精度が上がる

  目安：
    3〜4回  → 幅±10点程度（粗い推定）
    5〜9回  → 幅±5点程度
    10回以上 → 幅±2〜3点程度（信頼性が高い）
"""
from __future__ import annotations

import numpy as np

# ── 定数 ────────────────────────────────────────────────────────
N_BOOTSTRAP = 2000    # ブートストラップの試行回数（多いほど安定、遅くなる）
CI_LEVEL    = 0.95    # 信頼水準（95%）
MIN_SAMPLES = 3       # 信頼区間を計算するのに必要な最低サンプル数


def bootstrap_ci(
    scores:      list[float],
    n_bootstrap: int   = N_BOOTSTRAP,
    ci_level:    float = CI_LEVEL,
) -> dict | None:
    """
    スコアリストにブートストラップ法を適用して信頼区間を計算する。

    Parameters
    ----------
    scores      : 同一単語の過去スコアのリスト（時系列順でなくてもよい）
    n_bootstrap : ブートストラップの試行回数
    ci_level    : 信頼水準（0.0〜1.0）

    Returns
    -------
    dict または None（サンプル数が MIN_SAMPLES 未満の場合）

    dict のキー：
      mean      : スコアの標本平均
      lower     : 信頼区間下限
      upper     : 信頼区間上限
      std       : 標本標準偏差（スコアのバラツキ）
      se        : 標準誤差（平均の推定精度）
      n         : サンプル数
      ci_level  : 信頼水準
      width     : 区間の幅（upper − lower）
      reliability : 信頼性の評価（'low' | 'medium' | 'high'）
      reliability_note : 信頼性の説明文
    """
    arr = np.array([s for s in scores if s is not None], dtype=float)
    n   = len(arr)

    if n < MIN_SAMPLES:
        return None

    # ── ブートストラップ ──────────────────────────────────────────
    rng              = np.random.default_rng(seed=42)   # 再現性のため固定シード
    bootstrap_means  = np.array([
        np.mean(rng.choice(arr, size=n, replace=True))
        for _ in range(n_bootstrap)
    ])

    alpha = 1.0 - ci_level
    lower = float(np.percentile(bootstrap_means, alpha / 2 * 100))
    upper = float(np.percentile(bootstrap_means, (1 - alpha / 2) * 100))
    mean  = float(np.mean(arr))
    std   = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    se    = float(np.std(bootstrap_means))

    # 信頼性の評価
    width = upper - lower
    if n >= 10:
        reliability      = "high"
        reliability_note = "推定精度が高い（10回以上の録音から計算）"
    elif n >= 5:
        reliability      = "medium"
        reliability_note = "推定精度は中程度（5〜9回の録音から計算）"
    else:
        reliability      = "low"
        reliability_note = "推定精度が低い（3〜4回の録音から計算）。録音を重ねると精度が上がる"

    return {
        "mean":             round(mean, 1),
        "lower":            round(max(0.0, lower), 1),
        "upper":            round(min(100.0, upper), 1),
        "std":              round(std, 1),
        "se":               round(se, 2),
        "n":                n,
        "ci_level":         ci_level,
        "ci_pct":           int(ci_level * 100),
        "width":            round(width, 1),
        "reliability":      reliability,
        "reliability_note": reliability_note,
    }


def needs_more_data(n_sessions: int) -> dict:
    """
    サンプル数が不足している場合に表示するメッセージを返す。

    Parameters
    ----------
    n_sessions : 現在の録音回数（現在の録音を含む）
    """
    remaining = max(0, MIN_SAMPLES - n_sessions)
    return {
        "n":        n_sessions,
        "required": MIN_SAMPLES,
        "remaining": remaining,
        "message":  f"あと{remaining}回録音すると信頼区間が計算できます"
                    if remaining > 0 else "計算中...",
    }