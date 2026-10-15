import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import usercontext, vocab


class WordLifecycleTests(unittest.TestCase):
    """同じ単語の練習記録が1つの ID にまとまること（削除は一覧から外すだけ・同じ単語は作らない）。"""

    def setUp(self):
        # 実際の参加者フォルダ（data/users/）に書き込まない
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        for target in ("core.library.DATA_DIR", "core.history.DATA_DIR"):
            patcher = mock.patch(target, self.data_dir)
            patcher.start()
            self.addCleanup(patcher.stop)
        # MeCab が無くても動くよう、アクセント型の取得は止めておく
        patcher = mock.patch("core.vocab.get_accent", return_value=(None, "unknown"))
        patcher.start()
        self.addCleanup(patcher.stop)
        usercontext.set_current_user("u1")
        self.history: list[dict] = []
        patcher = mock.patch("core.vocab.load_history", side_effect=lambda: list(self.history))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        usercontext.clear()

    def practice(self, word_id, display, reading, times=1):
        for _ in range(times):
            self.history.insert(0, {"word_id": word_id, "display": display, "reading": reading, "total": 70})

    def test_delete_hides_the_word_but_keeps_it(self):
        wid = vocab.register_word("温度", "おんど")["word_id"]
        self.practice(wid, "温度", "おんど", 3)
        vocab.archive_word(wid)
        self.assertEqual(vocab.list_words(), [])
        self.assertIsNone(vocab.get_word(wid))
        self.assertIn(wid, vocab.load_db())   # ID と単語の対応は残る
        archived = vocab.list_archived_words()
        self.assertEqual([(w["word_id"], w["records"]) for w in archived], [(wid, 3)])
        with self.assertRaises(ValueError):
            vocab.get_reading_for_julius(wid)   # 削除した単語は練習できない

    def test_same_word_is_not_created_again(self):
        wid = vocab.register_word("温度", "おんど")["word_id"]
        with self.assertRaises(vocab.WordExists):
            vocab.register_word("温度", "おんど")
        # 全角・半角、空白、カタカナの読みの違いは同じ単語とみなす
        with self.assertRaises(vocab.WordExists):
            vocab.register_word(" 温度 ", "オンド")
        vocab.archive_word(wid)
        with self.assertRaises(vocab.WordExists) as ctx:
            vocab.register_word("温度", "おんど")
        self.assertTrue(ctx.exception.word["archived"])
        self.assertEqual(len(vocab.load_db()), 1)

    def test_restore_brings_back_the_same_id(self):
        wid = vocab.register_word("温度", "おんど")["word_id"]
        vocab.archive_word(wid)
        vocab.restore_word(wid)
        self.assertEqual([w["word_id"] for w in vocab.list_words()], [wid])
        self.assertNotIn("archived", vocab.load_db()[wid])

    def test_restore_refuses_to_make_a_duplicate(self):
        # 以前の版では、削除した単語と同じ単語を新しい ID で登録できた。そのデータが残っていても重複させない
        vocab.save_db({
            "word3": {"display": "温度", "reading": "おんど", "archived": True},
            "word7": {"display": "温度", "reading": "おんど"},
        })
        with self.assertRaises(ValueError):
            vocab.restore_word("word3")

    def test_reuses_the_id_of_a_word_deleted_by_an_older_version(self):
        # 以前の版で完全に消した単語は、練習記録にだけ残っている。同じ単語を登録したら、その ID で記録をつなげる
        self.practice("word3", "温度", "おんど", 4)
        result = vocab.register_word("温度", "おんど")
        self.assertEqual(result["word_id"], "word3")
        self.assertTrue(result["reused"])
        # 別の単語は、記録に残る ID を避けて新しい ID になる
        self.assertEqual(vocab.register_word("電話", "でんわ")["word_id"], "word4")

    def test_similar_words_are_reported_but_allowed(self):
        vocab.register_word("橋", "はし")
        same = vocab.find_same_words("箸", "はし")   # 同じ読みの別の語
        self.assertEqual(same["exact"], [])
        self.assertEqual([w["display"] for w in same["similar"]], ["橋"])
        self.assertEqual(vocab.register_word("箸", "はし")["word_id"], "word2")

    def test_purge_only_unpracticed_archived_words(self):
        typo = vocab.register_word("温度", "おんどう")["word_id"]
        used = vocab.register_word("電話", "でんわ")["word_id"]
        self.practice(used, "電話", "でんわ")
        with self.assertRaises(ValueError):
            vocab.purge_word(typo)          # 先に削除が必要
        vocab.archive_word(typo)
        vocab.archive_word(used)
        with self.assertRaises(ValueError):
            vocab.purge_word(used)          # 練習記録がある単語は消さない
        vocab.purge_word(typo)
        self.assertNotIn(typo, vocab.load_db())
        self.assertIn(used, vocab.load_db())

    def test_edit_cannot_make_two_words_the_same(self):
        vocab.register_word("温度", "おんど")
        other = vocab.register_word("音読", "おんどく")["word_id"]
        with self.assertRaises(vocab.WordExists):
            vocab.update_word(other, "温度", "おんど", None)
        vocab.update_word(other, "音読", "おんどく", 0)   # 自分自身と同じなのは問題ない

    def test_archived_words_are_left_out_of_lists(self):
        a = vocab.register_word("温度", "おんど")["word_id"]
        b = vocab.register_word("電話", "でんわ")["word_id"]
        vocab.update_word_tags(a, ["名詞"])
        vocab.archive_word(a)
        self.assertEqual(list(vocab.active_words_db()), [b])
        self.assertEqual(vocab.get_all_tags(), [])
        lib_dir = self.data_dir / "users" / "u1" / "library"
        self.assertEqual((lib_dir / "words.txt").read_text(encoding="utf-8").split(), ["電話"])
        with self.assertRaises(ValueError):
            vocab.update_word_note(a, "メモ")


class MergeSplitWordsTests(unittest.TestCase):
    """以前の版で分かれた同じ単語の記録を、scripts/merge_split_words.py で1つの ID にまとめる。"""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = Path(__file__).resolve().parent.parent / "scripts" / "merge_split_words.py"
        spec = importlib.util.spec_from_file_location("merge_split_words", path)
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_finds_and_merges_split_records(self):
        words_db = {"word7": {"display": "温度", "reading": "おんど"},
                    "word2": {"display": "電話", "reading": "でんわ"}}
        history = [
            {"word_id": "word7", "display": "温度", "reading": "おんど"},
            {"word_id": "word3", "display": "温度", "reading": "オンド"},   # 以前の版で消した同じ単語
            {"word_id": "word3", "display": "温度", "reading": "おんど"},
            {"word_id": "word2", "display": "電話", "reading": "でんわ"},
        ]
        splits = self.mod.find_splits(words_db, history)
        self.assertEqual([(s["display"], s["target"]) for s in splits], [("温度", "word7")])
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp)
            (user_dir / "library").mkdir()
            (user_dir / "history.json").write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
            (user_dir / "library" / "words_db.json").write_text(json.dumps(words_db, ensure_ascii=False), encoding="utf-8")
            moved = self.mod.merge(user_dir, splits, words_db, history)
            self.assertEqual(moved, 2)
            saved = json.loads((user_dir / "history.json").read_text(encoding="utf-8"))
            self.assertEqual([r["word_id"] for r in saved], ["word7", "word7", "word7", "word2"])
            self.assertEqual([r.get("merged_from") for r in saved], [None, "word3", "word3", None])
            # 書きかえる前のファイルを残す
            backup = list(user_dir.glob("history.json.bak-*"))
            self.assertEqual(len(backup), 1)
            self.assertEqual(json.loads(backup[0].read_text(encoding="utf-8"))[1]["word_id"], "word3")
            self.assertEqual(len(list((user_dir / "library").glob("words_db.json.bak-*"))), 1)
            self.assertEqual(self.mod.find_splits(words_db, saved), [])

    def test_keeps_merged_words_archived_and_points_to_target(self):
        words_db = {"word3": {"display": "温度", "reading": "おんど"},
                    "word7": {"display": "温度", "reading": "おんど"}}
        history = [{"word_id": "word7", "display": "温度", "reading": "おんど"}] * 2 + \
                  [{"word_id": "word3", "display": "温度", "reading": "おんど"}]
        history = [dict(r) for r in history]
        splits = self.mod.find_splits(words_db, history)
        self.assertEqual(splits[0]["target"], "word7")   # 使っている単語の中で記録が多い ID
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "library").mkdir()
            self.mod.merge(Path(tmp), splits, words_db, history)
        self.assertTrue(words_db["word3"]["archived"])
        self.assertEqual(words_db["word3"]["merged_into"], "word7")
        self.assertNotIn("archived", words_db["word7"])


if __name__ == "__main__":
    unittest.main()
