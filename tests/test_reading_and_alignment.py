import tempfile
import unittest
from pathlib import Path

from core.accent import _get_tagger, get_accent, guess_reading
from core.alignment import lab_is_squeezed
from core.vocab import julius_reading


class JuliusReadingTests(unittest.TestCase):
    """読みがなを Julius に渡せる形にする（2026-10-20 の実機テストで「メダカ」「田んぼ」が必ず失敗した）。"""

    def test_hiragana_is_kept(self):
        self.assertEqual(julius_reading("らーめん"), "らーめん")
        self.assertEqual(julius_reading(" きゃべつ "), "きゃべつ")

    def test_katakana_becomes_hiragana(self):
        self.assertEqual(julius_reading("メダカ"), "めだか")
        self.assertEqual(julius_reading("ﾒﾀﾞｶ"), "めだか")   # 半角カタカナ

    def test_other_letters_are_rejected_with_the_letters_named(self):
        with self.assertRaises(ValueError) as cm:
            julius_reading("田んぼ")
        self.assertIn("田", str(cm.exception))
        with self.assertRaises(ValueError):
            julius_reading("ヴァイオリン")   # ゔ は Julius の変換表に無い
        with self.assertRaises(ValueError):
            julius_reading("   ")


class GuessReadingTests(unittest.TestCase):
    """単語を追加する画面で、読みの欄に先に入れておく候補（画面で確かめて直してもらう）。"""

    def test_kana_only_words_become_hiragana_without_mecab(self):
        self.assertEqual(guess_reading("スーパー"), "すーぱー")
        self.assertEqual(guess_reading(" ゆうしょく "), "ゆうしょく")

    def test_empty_or_too_long_gives_none(self):
        self.assertIsNone(guess_reading(""))
        self.assertIsNone(guess_reading("あ" * 41))

    @unittest.skipIf(_get_tagger() is None, "MeCab・UniDic が入っていない")
    def test_kanji_words_are_read_and_pass_the_julius_check(self):
        for display, reading in [("夕食", "ゆうしょく"), ("病院", "びょういん"), ("田んぼ", "たんぼ"), ("学校", "がっこう")]:
            self.assertEqual(guess_reading(display), reading)
            self.assertEqual(julius_reading(guess_reading(display)), reading)

    @unittest.skipIf(_get_tagger() is None, "MeCab・UniDic が入っていない")
    def test_quoted_commas_in_unidic_features_do_not_shift_the_fields(self):
        # 「台」の素性には "B,B4WB7G9G" のような引用符の中のカンマがあり、カンマで単純に区切ると
        # 後ろの項目がずれて、読みが取れず、アクセント型も取れなかった
        self.assertEqual(guess_reading("東京スカイツリー展望台"), "とうきょうすかいつりーてんぼうだい")
        self.assertEqual(get_accent("台"), (1, "mecab"))
        self.assertEqual(get_accent("温度"), (1, "mecab"))


class SqueezedAlignmentTests(unittest.TestCase):
    """認識に失敗して、すべての音が最短の長さに押し込まれた結果を見つける。"""

    def lab(self, text: str) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".lab", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return Path(tmp.name)

    def test_squeezed_result_is_found(self):
        # 2026-10-20 に「電話」で実際に出た結果
        self.assertTrue(lab_is_squeezed(self.lab(
            "0.0000000 2.8125000 silB\n2.8125000 2.8425000 d\n2.8425000 2.8725000 e\n"
            "2.8725000 2.9025000 N\n2.9025000 2.9325000 w\n2.9325000 2.9625000 a\n"
            "2.9625000 2.9925000 silE\n")))

    def test_normal_result_is_kept(self):
        self.assertFalse(lab_is_squeezed(self.lab(
            "0.0000000 1.1225000 silB\n1.1225000 1.4225000 h\n1.4225000 1.4525000 a\n"
            "1.4525000 1.5225000 r\n1.5225000 1.6525000 u\n1.6525000 1.7725000 m\n"
            "1.7725000 1.9325000 a\n1.9325000 2.0425000 k\n2.0425000 2.2425000 i\n"
            "2.2425000 3.0025000 silE\n")))

    def test_missing_file_is_not_squeezed(self):
        self.assertFalse(lab_is_squeezed(Path("/nonexistent/x.lab")))


if __name__ == "__main__":
    unittest.main()
