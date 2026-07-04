"""
core/sync.py
音映像クロスチェック（口の動きと声の同期分析）。

【アイデアの出典】
先生の提案：お手本とユーザーの「音声同士のDTW対応」と「映像同士の
DTW対応」という2本の独立した対応線が同じ場所を指していれば、
その発音は音・口の形・タイミングのすべてが合っていると判断できる。

【本実装での構成】
- 音声側の対応線: Julius 強制アライメント（各録音の .lab のモーラ境界）。
  同じモーラ同士が対応するという事実を使うため、生波形DTWより頑健。
- 映像側の対応線: 唇形状ベクトル列同士の fastdtw 経路。
- クロスチェック: お手本の各モーラ中心時刻を映像DTW経路で
  ユーザーの映像時刻に写し、音声アライメントが指すユーザーの
  モーラ中心時刻と比較する。差（offset）が小さければ
  「口の動きが声と同期して正しい位置にある」ことの証拠になる。

【指標の読み方】
- offset > 0 : 口の動きが声より遅れて現れている
- offset < 0 : 口の動きが声より先行している
- median_offset_ms : 発話全体の系統的なズレ。
  録画開始タイミングの機材差も混入しうるため、単体では参考値。
- spread_ms : モーラ間のズレのばらつき（MAD）。機材差の影響を
  受けないため、発話内での「口と声の不同期」の最も信頼できる指標。

【しきい値の校正結果（2026-07-03、実データ+合成ズレ注入）】
- 自己一致（測定ノイズ床）: spread 2〜9ms、median ±11ms
- 一様ズレ注入 50/100/200ms: median が -61/-111/-211ms とほぼ線形に回復
- テンポ1.5倍変化（ズレなし）: spread 8ms → 誤検知しない
- 同話者・同単語の別テイク: spread 27〜83ms
  ※旧データは fps 未保存（30fps仮定）のため経時ドリフトを含む上限値。
    times を保存する新録画では下がる見込み
- 発話後半のみ +150ms の局所ズレ: spread 75ms
→ spread < 70ms = ok / 70〜140ms = warn / 140ms以上 = ng

【重要な限界（校正で判明）】
1. median はユーザーの音声と映像の録画開始タイミング差（機材差）を
   含むため、単体では発音の良し悪しを断定できない。主指標は spread。
2. この指標は「タイミングの同期」だけを測る。全く別の単語を発音しても
   タイミングが揃っていれば ok になり得る（校正テスト6で確認）。
   口の形の正しさは唇スコア・母音スコアが担当するため、
   必ずそれらと並べて表示すること。
"""
from __future__ import annotations

import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

# 校正済みしきい値（ms）— 上記の校正結果に基づく
SYNC_OK_MS   = 70.0
SYNC_WARN_MS = 140.0

_SIL_LABELS = {"silB", "silE", "sp"}


def _mora_center(mora) -> float:
    return (float(mora[0]) + float(mora[1])) / 2.0


def _nearest_time(times: list[float], t: float) -> int:
    """時刻リストから t に最も近いインデックスを返す。"""
    arr = np.asarray(times, dtype=float)
    return int(np.argmin(np.abs(arr - t)))


def compute_av_sync(
    ref_vectors: list[list[float]],
    ref_times:   list[float] | None,
    ref_moras:   list,
    test_vectors: list[list[float]],
    test_times:   list[float] | None,
    test_moras:   list,
) -> dict | None:
    """お手本とユーザーの音映像同期を分析する。

    Parameters
    ----------
    ref_vectors / test_vectors : 唇形状ベクトル列（20次元×フレーム）
    ref_times / test_times     : 各ベクトルの時刻（秒）。None なら 30fps 相当で補完
    ref_moras / test_moras     : lab_load の mora_list（[[start, end, label], ...] 秒）

    Returns
    -------
    dict | None:
        per_mora    : [{label, offset_ms}]
        median_offset_ms : 系統的ズレ（録画開始差を含みうる）
        spread_ms   : ズレのばらつき（MAD）— 発話内不同期の主指標
        verdict     : "ok" / "warn" / "ng"
        n_moras     : 分析できたモーラ数
    データ不足時は None。
    """
    if not ref_vectors or not test_vectors or not ref_moras or not test_moras:
        return None
    if len(ref_vectors) < 5 or len(test_vectors) < 5:
        return None

    if ref_times is None or len(ref_times) != len(ref_vectors):
        ref_times = [i / 30.0 for i in range(len(ref_vectors))]
    if test_times is None or len(test_times) != len(test_vectors):
        test_times = [i / 30.0 for i in range(len(test_vectors))]

    # ── 映像同士の対応線（DTW経路） ──────────────────────────────
    _, path = fastdtw(
        np.asarray(ref_vectors, dtype=float),
        np.asarray(test_vectors, dtype=float),
        dist=euclidean,
    )

    # ref フレーム → 対応する test フレーム（中央値）のマップ
    ref_to_test: dict[int, list[int]] = {}
    for i, j in path:
        ref_to_test.setdefault(i, []).append(j)

    def map_ref_frame(fi: int) -> int:
        js = ref_to_test.get(fi)
        if not js:
            # 経路に無いフレーム（理論上ないが保険）: 最近傍
            fi = min(ref_to_test.keys(), key=lambda x: abs(x - fi))
            js = ref_to_test[fi]
        js = sorted(js)
        return js[len(js) // 2]

    # ── モーラごとのクロスチェック ────────────────────────────────
    per_mora: list[dict] = []
    offsets:  list[float] = []
    n = min(len(ref_moras), len(test_moras))

    for k in range(n):
        r, u = ref_moras[k], test_moras[k]
        label = str(r[2])
        if label in _SIL_LABELS:
            continue

        # お手本のモーラ中心 → お手本映像フレーム → (DTW) → ユーザー映像時刻
        rc = _mora_center(r)
        fi = _nearest_time(ref_times, rc)
        fj = map_ref_frame(fi)
        t_video = test_times[fj] if fj < len(test_times) else test_times[-1]

        # 音声アライメントが指すユーザーのモーラ中心
        t_audio = _mora_center(u)

        off_ms = (t_video - t_audio) * 1000.0
        offsets.append(off_ms)
        per_mora.append({"label": label, "offset_ms": round(off_ms)})

    if len(offsets) < 2:
        return None

    arr    = np.asarray(offsets, dtype=float)
    median = float(np.median(arr))
    spread = float(np.median(np.abs(arr - median)))  # MAD

    if spread < SYNC_OK_MS:
        verdict = "ok"
    elif spread < SYNC_WARN_MS:
        verdict = "warn"
    else:
        verdict = "ng"

    # ── グラフ用データ（2本の対応線を同じ座標系で描くため） ──────────
    # 座標系: X = ユーザーの時間(秒), Y = お手本の時間(秒)
    # 発話区間（無音を除いた最初〜最後のモーラ）に前後10%の余白を付けた範囲。
    # 映像DTW経路は録画全体（無音含む）を通っているため、これで絞らないと
    # グラフ面積の大半が無音区間の「意味のない直線」で埋まってしまう。
    speech_moras = [m for m in ref_moras if str(m[2]) not in _SIL_LABELS]
    if speech_moras:
        sp_start = float(speech_moras[0][0])
        sp_end   = float(speech_moras[-1][1])
        pad      = max(0.05, (sp_end - sp_start) * 0.1)
        y_lo, y_hi = sp_start - pad, sp_end + pad
    else:
        y_lo, y_hi = ref_times[0], ref_times[-1]

    # 映像の対応線: DTW経路を時刻に変換し、発話区間内だけを残して間引く（最大120点）
    video_line_full: list[list[float]] = []
    for i, j in path:
        if i < len(ref_times) and j < len(test_times):
            ry = ref_times[i]
            if y_lo <= ry <= y_hi:
                video_line_full.append([round(test_times[j], 3), round(ry, 3)])
    step = max(1, len(video_line_full) // 120)
    video_line = video_line_full[::step]

    # 音声の対応線: モーラ境界同士の対応（区分線形）
    audio_line: list[list[float]] = []
    for k in range(n):
        r, u = ref_moras[k], test_moras[k]
        if str(r[2]) in _SIL_LABELS:
            continue
        audio_line.append([round(float(u[0]), 3), round(float(r[0]), 3)])
        audio_line.append([round(float(u[1]), 3), round(float(r[1]), 3)])

    # モーラごとのクロス点（映像線が指す位置 vs 音声線が指す位置）
    mora_points: list[dict] = []
    for k in range(n):
        r, u = ref_moras[k], test_moras[k]
        label = str(r[2])
        if label in _SIL_LABELS:
            continue
        rc = _mora_center(r)
        fi = _nearest_time(ref_times, rc)
        fj = map_ref_frame(fi)
        t_video = test_times[fj] if fj < len(test_times) else test_times[-1]
        mora_points.append({
            "label":   label,
            "r":       round(rc, 3),
            "u_audio": round(_mora_center(u), 3),
            "u_video": round(t_video, 3),
        })

    return {
        "per_mora":         per_mora,
        "median_offset_ms": round(median),
        "spread_ms":        round(spread),
        "verdict":          verdict,
        "n_moras":          len(offsets),
        "plot": {
            "video_line":  video_line,
            "audio_line":  audio_line,
            "mora_points": mora_points,
        },
    }
