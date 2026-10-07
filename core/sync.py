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

【合計点への反映（2026-09-29 追加、2026-10-03 文献にもとづいて決め直した）】
音ごとのずれ d（そのモーラの offset − 発話全体の中央値。正＝声が口より先）を、
人が気づく範囲・口の情報が聞き取りに使われる範囲と比べ、範囲を超えた音だけ
合計点から引く（calc_sync_penalty）。加点はしない（そろっていても正しい単語を
言えた証拠にはならない。限界2）。範囲と式の出典は下の定数のコメントと README §7.3。
最初の版（2026-09-29）は spread が 70〜210ms の間で直線的に引いていたが、
向き（声が先か口が先か）を見ておらず、70・210 に文献の根拠が無かった。
あわせて、唇のデータが無い時刻のモーラは比べないようにした。唇のデータは録画の
先頭から最大 50 フレームしか取っていないので、それより後ろのモーラを最後の
フレームに寄せると、発音と関係なくズレが大きく出ていた。
"""
from __future__ import annotations

import numpy as np
from fastdtw import fastdtw
from scipy.spatial.distance import euclidean

from core.utils import romaji_mora_to_kana

# 校正済みしきい値（ms）— 上記の校正結果に基づく
SYNC_OK_MS   = 70.0
SYNC_WARN_MS = 140.0

# ── 合計点からの減点（文献にもとづく範囲） ───────────────────────────
# 口と声のずれは向きで許される幅が大きく違う（声が先には敏感、口が先には寛容）。
# 次の範囲までは、人が気づかず、口が見えることによる聞き取りの上乗せも失われない:
#   声が先 45ms … Grant, van Wassenhove & Poeppel (2003) の弁別閾（約 −45ms）、
#                 ITU-R BT.1359-1 (1998) の検知限（+45ms）
#   口が先 200ms … Grant ほか (2003)（約 +200ms）、Grant & Greenberg (2001)（了解度は 160〜200ms まで下がらない）
#   （van Wassenhove ほか (2007) の統合の範囲 −30〜+170ms も同じ形）
SYNC_TOL_VOICE_FIRST_MS = 45.0
SYNC_TOL_MOUTH_FIRST_MS = 200.0
# ずれが 400ms になると、口が見えることによる上乗せがほぼ全部失われる:
#   Grant & Greenberg (2001) 表1 の平均: 声が先 400ms で音声だけと同じ（上乗せ 0%）、口が先 400ms で上乗せの 10%
# 範囲の端からここまでを直線でつなぐ（声が先の側は実測の下がり方より緩い。厳しくしすぎないため）。
SYNC_FULL_LOSS_MS = 400.0
# 上限（すべての音で上乗せが失われたときに引く点数）。文献からは決まらない値で、
# 「あまり厳しくしない」という方針で選んだ（README §7.3）。
SYNC_PENALTY_MAX = 5.0
# これより少ないモーラでは、ずれの基準にする中央値が安定しないので点数に入れない
SYNC_MIN_MORAS = 3

_SIL_LABELS = {"silB", "silE", "sp"}


def _mora_center(mora) -> float:
    return (float(mora[0]) + float(mora[1])) / 2.0


def _nearest_time(times: list[float], t: float) -> int:
    """時刻リストから t に最も近いインデックスを返す。"""
    arr = np.asarray(times, dtype=float)
    return int(np.argmin(np.abs(arr - t)))


def _frame_interval(times: list[float]) -> float:
    """唇のデータのコマの間隔（秒）。"""
    diffs = np.diff(np.asarray(times, dtype=float))
    pos   = diffs[diffs > 0]
    return float(np.median(pos)) if len(pos) else 1 / 30.0


def _mora_loss(rel_ms: float, margin_ms: float) -> float:
    """音ごとのずれ（正＝声が先）から、口が見えることによる上乗せが失われた割合（0〜1）。"""
    tol = SYNC_TOL_VOICE_FIRST_MS if rel_ms > 0 else SYNC_TOL_MOUTH_FIRST_MS
    over = abs(rel_ms) - tol - margin_ms
    return float(min(1.0, max(0.0, over / (SYNC_FULL_LOSS_MS - tol))))


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
        per_mora    : [{label, offset_ms, rel_ms, loss}]
                      rel_ms … offset_ms − median（正＝声が口より先）
                      loss   … 口が見えることによる上乗せが失われた割合（0〜1、_mora_loss）
        median_offset_ms : 系統的ズレ（録画開始差を含みうる）
        spread_ms   : ズレのばらつき（MAD）— 発話内不同期の主指標
        verdict     : "ok" / "warn" / "ng"
        n_moras     : 分析できたモーラ数
        n_outside   : 唇のデータが無い時刻にあったため比べなかったモーラ数
        times_assumed : どちらかの時刻が無く 30fps と仮定したか（点数には入れない）
        margin_ms   : 測定の誤差として範囲に足した幅（お手本と録画のコマの間隔の半分の和）
    データ不足時は None。
    """
    if not ref_vectors or not test_vectors or not ref_moras or not test_moras:
        return None
    if len(ref_vectors) < 5 or len(test_vectors) < 5:
        return None

    times_assumed = False
    if ref_times is None or len(ref_times) != len(ref_vectors):
        ref_times = [i / 30.0 for i in range(len(ref_vectors))]
        times_assumed = True
    if test_times is None or len(test_times) != len(test_vectors):
        test_times = [i / 30.0 for i in range(len(test_vectors))]
        times_assumed = True
    # 唇のデータがある時間の範囲（前後に1コマ分の余裕を付ける）
    ref_dt,  test_dt = _frame_interval(ref_times), _frame_interval(test_times)
    ref_lo,  ref_hi  = ref_times[0]  - ref_dt,  ref_times[-1]  + ref_dt
    test_lo, test_hi = test_times[0] - test_dt, test_times[-1] + test_dt
    # 口の動きの時刻はコマ単位でしか分からないので、お手本・録画それぞれ半コマずつ
    # ずれうる（ITU-R BT.1359-1 付録1が引く BR.265 の「±半コマ」と同じ考え方）
    margin_ms = (ref_dt + test_dt) / 2 * 1000.0

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
    per_mora:    list[dict] = []
    offsets:     list[float] = []
    mora_points: list[dict] = []   # グラフ用のクロス点（映像線が指す位置 vs 音声線が指す位置）
    n_outside = 0
    n = min(len(ref_moras), len(test_moras))

    for k in range(n):
        r, u = ref_moras[k], test_moras[k]
        label = str(r[2])
        if label in _SIL_LABELS:
            continue

        rc      = _mora_center(r)   # お手本のモーラ中心
        t_audio = _mora_center(u)   # 音声アライメントが指すユーザーのモーラ中心

        # 唇のデータが無い時刻のモーラは比べない。いちばん近い（端の）フレームに
        # 寄せると、発音と関係なくズレが大きく出てしまう。
        if not (ref_lo <= rc <= ref_hi and test_lo <= t_audio <= test_hi):
            n_outside += 1
            continue

        # お手本のモーラ中心 → お手本映像フレーム → (DTW) → ユーザー映像時刻
        fi = _nearest_time(ref_times, rc)
        fj = map_ref_frame(fi)
        t_video = test_times[fj] if fj < len(test_times) else test_times[-1]

        off_ms = (t_video - t_audio) * 1000.0
        kana   = romaji_mora_to_kana(label)
        offsets.append(off_ms)
        per_mora.append({"label": kana, "offset_ms": round(off_ms)})
        mora_points.append({
            "label":   kana,
            "r":       round(rc, 3),
            "u_audio": round(t_audio, 3),
            "u_video": round(t_video, 3),
        })

    if len(offsets) < 2:
        return None

    arr    = np.asarray(offsets, dtype=float)
    median = float(np.median(arr))
    spread = float(np.median(np.abs(arr - median)))  # MAD

    # 音ごとのずれは中央値からの差で見る（全体が一律にずれた分は録画開始の機材差と区別できない）
    for m, off in zip(per_mora, offsets):
        rel = off - median
        m["rel_ms"] = round(rel)
        m["loss"]   = round(_mora_loss(rel, margin_ms), 3)

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

    return {
        "per_mora":         per_mora,
        "median_offset_ms": round(median),
        "spread_ms":        round(spread),
        "verdict":          verdict,
        "n_moras":          len(offsets),
        "n_outside":        n_outside,
        "times_assumed":    times_assumed,
        "margin_ms":        round(margin_ms),
        "plot": {
            "video_line":  video_line,
            "audio_line":  audio_line,
            "mora_points": mora_points,
        },
    }


def calc_sync_penalty(av_sync: dict | None) -> dict:
    """合計点から引く点数（口と声のタイミング）を返す。

    減点 = SYNC_PENALTY_MAX × （音ごとの loss の平均）
    loss は、その音のずれが人の気づく範囲（声が先 45ms・口が先 200ms ＋ 測定の誤差）を
    超えた分を、400ms で 1 になるよう直線で表したもの（_mora_loss）。範囲内の音は 0。
    次のときは点数に入れない（penalty = None）:
      - 録画が無いなどで分析できなかった
      - 時刻の無い古い唇データで、30fps と仮定した（時間がたつほどズレが大きく出る）
      - 比べられたモーラが SYNC_MIN_MORAS 未満

    Returns
    -------
    dict:
        penalty   : 引く点数（0〜SYNC_PENALTY_MAX、0.1 点刻み）。点数に入れないときは None
        spread_ms : 分析できたときの spread_ms（点数に入れないときも入れる）
        note      : 点数に入れなかった理由（入れたときは None）
        over      : 範囲を超えた音 [{label, voice_first}]（voice_first＝声が口より先）
    """
    if not av_sync:
        return {"penalty": None, "spread_ms": None, "over": [],
                "note": "口と声のタイミングを調べられなかったので、点数には入れていません。"}

    spread = av_sync.get("spread_ms")
    if av_sync.get("times_assumed"):
        note = "お手本の口の動きのデータが古い形式なので、点数には入れていません。"
    elif int(av_sync.get("n_moras") or 0) < SYNC_MIN_MORAS:
        note = "くらべられる音が少ないので、点数には入れていません。"
    else:
        note = None
    per_mora = av_sync.get("per_mora") or []
    if note or not per_mora or any("loss" not in m for m in per_mora):
        return {"penalty": None, "spread_ms": spread, "over": [],
                "note": note or "口と声のタイミングを調べられなかったので、点数には入れていません。"}

    penalty = round(SYNC_PENALTY_MAX * float(np.mean([m["loss"] for m in per_mora])), 1)
    over    = [{"label": m["label"], "voice_first": m["rel_ms"] > 0} for m in per_mora if m["loss"] > 0]
    return {"penalty": penalty, "spread_ms": spread, "note": None, "over": over}
