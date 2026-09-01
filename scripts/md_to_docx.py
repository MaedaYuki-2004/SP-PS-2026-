"""
scripts/md_to_docx.py
倫理審査資料の Markdown を Word（.docx）に変換するスクリプト。

Markdown を正として Word を生成するため、資料を修正したい場合は
docs/倫理審査資料_SP-PS.md を編集してから本スクリプトを再実行する。

使い方:
    python scripts/md_to_docx.py
    python scripts/md_to_docx.py <入力.md> <出力.docx>

対応する記法: 見出し(#/##/###/####)・表・箇条書き・引用(>)・コードブロック・
              **太字**・段落。それ以外は素のテキストとして出力する。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

# ── 体裁の定数 ────────────────────────────────────────────────────────
FONT_JP    = "游ゴシック"        # 本文（日本語）
FONT_MONO  = "ＭＳ ゴシック"      # コードブロック
NAVY       = RGBColor(0x0E, 0x1E, 0x45)
GRAY       = RGBColor(0x55, 0x5D, 0x6E)
AMBER      = RGBColor(0x8A, 0x5D, 0x00)

# 途中でページ分割せず、まるごと次ページへ送る表の上限。
# 行数だけで判定すると、行数は少なくても文字量が多く縦に長い表まで
# ページ送りされ、前のページに大きな空白が残る。文字量でも判定する。
SMALL_TABLE_ROWS = 5
SMALL_TABLE_CHARS = 220

# 表の幅（本文幅と揃える）と、狭くなりすぎる列を防ぐための下限の重み
TABLE_WIDTH_CM = 17.2
MIN_COL_WEIGHT = 4.0
CHAR_W_CM   = 0.32   # 9pt の全角1文字ぶんの幅
CELL_PAD_CM = 0.42   # セル左右の余白
MAX_NEED_CM = 3.2    # 折り返しを避けるために確保する幅の上限


def _set_jp_font(run, name: str) -> None:
    """日本語フォントは w:eastAsia も指定しないと反映されない。"""
    run.font.name = name
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    rFonts.set(qn("w:eastAsia"), name)
    rFonts.set(qn("w:ascii"), name)
    rFonts.set(qn("w:hAnsi"), name)


def _shade(cell, hex_color: str) -> None:
    """表のセルに背景色を付ける。"""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _next_starts_block(lines: list[str], i: int) -> bool:
    """i 以降の最初の非空行が、表またはコードブロックの開始かを判定する。

    表や JSON 例の直前にある「〜は以下のとおり。」といったリード文が、
    単独でページ末に取り残されるのを防ぐために使う。
    """
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return False
    ln = lines[i].strip()
    return ln.startswith("|") or ln.startswith("```")


def _repeat_header(row) -> None:
    """表の見出し行を、ページをまたいだ各ページの先頭で繰り返す。"""
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _no_row_split(row) -> None:
    """1つの行が途中でページ分割されないようにする。"""
    trPr = row._tr.get_or_add_trPr()
    trPr.append(OxmlElement("w:cantSplit"))


def _keep_with_next(cell_or_par, flag: bool = True) -> None:
    """次の内容と同じページに保つ（表のセル・段落のどちらでも可）。"""
    pars = cell_or_par.paragraphs if hasattr(cell_or_par, "paragraphs") else [cell_or_par]
    for par in pars:
        par.paragraph_format.keep_with_next = flag


def _set_col_widths(table, header: list[str], rows: list[list[str]]) -> None:
    """列ごとの文字量に応じて幅を配分する。

    Word の自動調整に任せると、短い語しか入らない列が広く取られ、
    説明文の列が窮屈になって不自然な折り返しが起きる。列の中身の
    長さから幅を決めることで、どの列も同じくらいの行数に収まるようにする。
    """
    n = len(header)
    weights, needs = [], []
    for c in range(n):
        texts = [header[c]] + [r[c] for r in rows if c < len(r)]
        lens = sorted(len(t) for t in texts)
        # 1行だけ極端に長い列に引きずられないよう、中央値と最大値の折衷をとる
        med = lens[len(lens) // 2]
        weights.append(max(med * 0.6 + lens[-1] * 0.4, MIN_COL_WEIGHT))
        # 「表示場所」のような短い語だけの列が折り返さずに済む幅。
        # 説明文の列まで広げないよう上限を設ける。
        needs.append(min(lens[-1] * CHAR_W_CM + CELL_PAD_CM, MAX_NEED_CM))

    total = sum(weights)
    widths = [max(TABLE_WIDTH_CM * w / total, nd) for w, nd in zip(weights, needs)]

    # 下限を満たすために広げた分、余裕のある列から比例して削る
    over = sum(widths) - TABLE_WIDTH_CM
    if over > 0:
        slack = [max(w - nd, 0) for w, nd in zip(widths, needs)]
        if sum(slack) > 0:
            widths = [w - over * sl / sum(slack) for w, sl in zip(widths, slack)]
        else:
            widths = [w * TABLE_WIDTH_CM / sum(widths) for w in widths]

    table.autofit = False
    for c, w in enumerate(widths):
        width = Cm(w)
        for row in table.rows:
            row.cells[c].width = width


def _add_runs(par, text: str, size: float, *, bold=False, color=None, mono=False) -> None:
    """**太字** を解釈しながらテキストを流し込む。"""
    font = FONT_MONO if mono else FONT_JP
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        run = par.add_run(part)
        run.font.size = Pt(size)
        run.bold = bold or (i % 2 == 1)   # 奇数番目が ** で囲まれた部分
        if color is not None:
            run.font.color.rgb = color
        _set_jp_font(run, font)


def _clean(text: str) -> str:
    """Word では表現できない記法を落とす。"""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # リンク → テキスト
    text = re.sub(r"<[^>]+>", "", text)                     # 生 HTML タグ
    text = text.replace("<br>", " ").replace("`", "")
    return text.strip()


def _split_row(line: str) -> list[str]:
    return [_clean(c) for c in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|[\s:|-]+\|\s*", line))


def convert(md_path: Path, out_path: Path) -> None:
    lines = md_path.read_text(encoding="utf-8").split("\n")
    doc = Document()

    # 余白（A4・やや広め）
    for section in doc.sections:
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)

    # 標準スタイル（日本語は w:eastAsia の指定が必要）
    style = doc.styles["Normal"]
    style.font.size = Pt(10.5)
    style.font.name = FONT_JP
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        rFonts.set(qn(attr), FONT_JP)

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ── 水平線（--- は無視。表紙区切りにのみ改ページを入れない） ──
        if stripped == "---":
            i += 1
            continue

        # ── コードブロック ────────────────────────────────────────
        if stripped.startswith("```"):
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            par = doc.add_paragraph()
            par.paragraph_format.left_indent = Cm(0.5)
            par.paragraph_format.space_before = Pt(4)
            par.paragraph_format.space_after = Pt(10)
            run = par.add_run("\n".join(block))
            run.font.size = Pt(8.5)
            _set_jp_font(run, FONT_MONO)
            continue

        # ── 表 ────────────────────────────────────────────────────
        if stripped.startswith("|") and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            header = _split_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1

            table = doc.add_table(rows=1, cols=len(header))
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for c, text in enumerate(header):
                cell = table.rows[0].cells[c]
                cell.text = ""
                _add_runs(cell.paragraphs[0], text, 9, bold=True, color=NAVY)
                _shade(cell, "EEF1F7")
            for row in rows:
                cells = table.add_row().cells
                for c, text in enumerate(row[: len(header)]):
                    cells[c].text = ""
                    _add_runs(cells[c].paragraphs[0], text, 9)

            # ── ページ送りの制御 ──────────────────────────────
            # 表がページをまたぐと見出し行が消えて列の意味が分からなくなるため、
            # 各ページの先頭で見出し行を繰り返す。行の途中での分割も禁止する。
            _set_col_widths(table, header, rows)
            _repeat_header(table.rows[0])
            for row in table.rows:
                _no_row_split(row)
            # 見出し行だけがページ末に取り残されないよう、常に次の行と同じ
            # ページに置く。長い表はここで分割されるが、見出しは次ページの
            # 先頭で繰り返されるので列の意味は失われない。
            for cell in table.rows[0].cells:
                _keep_with_next(cell)
            # 小さい表だけ、途中で切らずまるごと次ページへ送る
            total_chars = sum(len(c.text) for r in table.rows for c in r.cells)
            if len(table.rows) <= SMALL_TABLE_ROWS and total_chars <= SMALL_TABLE_CHARS:
                for row in table.rows[:-1]:
                    for cell in row.cells:
                        _keep_with_next(cell)

            doc.add_paragraph().paragraph_format.space_after = Pt(6)
            continue

        # ── 見出し ────────────────────────────────────────────────
        # Word の見出しスタイルを使う。これによりナビゲーションウィンドウで
        # 節を移動でき、[参考資料]→[目次]で目次を自動生成できる。
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level, text = len(m.group(1)), _clean(m.group(2))
            spec = {1: (17, NAVY, 0, 14), 2: (13.5, NAVY, 20, 8),
                    3: (11.5, None, 14, 6), 4: (10.5, GRAY, 10, 4)}[level]
            size, color, before, after = spec
            par = doc.add_paragraph(style=f"Heading {min(level, 4)}")
            par.paragraph_format.space_before = Pt(before)
            par.paragraph_format.space_after = Pt(after)
            if level == 1:
                par.alignment = WD_ALIGN_PARAGRAPH.CENTER
            # 見出しだけがページ末に取り残されないよう、次の内容と同じページに保つ
            par.paragraph_format.keep_with_next = True
            par.paragraph_format.keep_together = True
            _add_runs(par, text, size, bold=True, color=color)
            i += 1
            continue

        # ── 引用（注記） ──────────────────────────────────────────
        if stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            text = _clean(" ".join(x for x in block if x))
            if text:
                par = doc.add_paragraph()
                par.paragraph_format.left_indent = Cm(0.5)
                par.paragraph_format.space_after = Pt(10)
                par.paragraph_format.keep_together = True
                # 直後が表なら、見出し＋注記だけがページ末に取り残されて
                # 表だけ次ページへ送られるのを防ぐ。
                if _next_starts_block(lines, i):
                    par.paragraph_format.keep_with_next = True
                is_action = "要確定" in text or "注意" in text or "最重要" in text
                _add_runs(par, text, 9.5, color=AMBER if is_action else GRAY)
            continue

        # ── 箇条書き / チェックリスト ─────────────────────────────
        m = re.match(r"^[-*]\s+(\[[ x]\]\s+)?(.*)$", stripped)
        if m:
            is_check = bool(m.group(1))
            parts = [m.group(2)]
            i += 1
            # インデントされた続きの行は、同じ項目の本文として結合する。
            # 別段落にすると2行目だけ左端に戻り、箇条書きの体裁が崩れるため。
            while i < len(lines) and lines[i].startswith("  ") and lines[i].strip()                     and not re.match(r"^\s*[-*]\s", lines[i]):
                parts.append(lines[i].strip())
                i += 1
            text = _clean("".join(parts))
            if text:
                if is_check:
                    # チェックリストは記入できるよう空欄の四角を頭に付ける。
                    # 箇条書きスタイルにすると中黒と四角が二重に出るため使わない。
                    par = doc.add_paragraph()
                    par.paragraph_format.left_indent = Cm(0.6)
                    text = "☐　" + text
                else:
                    par = doc.add_paragraph(style="List Bullet")
                par.paragraph_format.space_after = Pt(2)
                _add_runs(par, text, 10)
            continue

        # ── 番号付きリスト ────────────────────────────────────────
        # 目次のように連続する「1. 〜」を1段落にまとめてしまわないよう、
        # 箇条書きと同様に1行ずつ独立した段落として出力する。
        m = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if m:
            text = _clean(m.group(2))
            if text:
                par = doc.add_paragraph()
                par.paragraph_format.left_indent = Cm(0.6)
                par.paragraph_format.space_after = Pt(2)
                _add_runs(par, f"{m.group(1)}. {text}", 10)
            i += 1
            continue

        # ── 空行 ──────────────────────────────────────────────────
        if not stripped:
            i += 1
            continue

        # ── 本文 ──────────────────────────────────────────────────
        block = []
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*(#{1,4}\s|[-*]\s|\d+\.\s|>|\||```|---\s*$)", lines[i]
        ):
            block.append(lines[i].strip())
            i += 1
        # 全体が太字で複数行の段落（研究課題など見出し的な一文）は、
        # Word の自動折り返しに任せるとカタカナの途中で改行されて読みにくい。
        # 原稿の改行位置をそのまま使う。
        joined = "".join(block)
        keep_breaks = (len(block) > 1 and joined.startswith("**") and joined.endswith("**"))

        text = _clean(joined)
        if text:
            par = doc.add_paragraph()
            par.paragraph_format.space_after = Pt(8)
            par.paragraph_format.line_spacing = 1.4
            par.paragraph_format.widow_control = True   # 1行だけ取り残さない
            if keep_breaks:
                par.paragraph_format.keep_together = True
                for n, src in enumerate(block):
                    if n:
                        par.add_run().add_break()
                    _add_runs(par, _clean(src.strip("* ")), 10.5, bold=True)
            else:
                # 直後が表やコードブロックなら、リード文だけがページ末に残らないようにする
                if _next_starts_block(lines, i):
                    par.paragraph_format.keep_with_next = True
                    par.paragraph_format.keep_together = True
                _add_runs(par, text, 10.5)

    doc.save(str(out_path))


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    md  = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "docs" / "倫理審査資料_SP-PS.md"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else md.with_suffix(".docx")
    if not md.exists():
        raise SystemExit(f"入力が見つかりません: {md}")
    convert(md, out)
    print(f"作成しました: {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
