import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import default_words as dw
from core import usercontext, vocab
from core.library import library_for, reference_id


class DefaultWordsTests(unittest.TestCase):
    def setUp(self):
        # 実際の data/ に書き込まない
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        for target in ("core.library.DATA_DIR", "core.history.DATA_DIR", "core.default_words.DATA_DIR"):
            p = mock.patch(target, self.data_dir)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("core.storage.LOCK_DIR", self.data_dir / "run" / "locks")
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        usercontext.clear()

    def _add_word(self, user_id, word_id, display, reading, lip=True, archived=False):
        lib = library_for(user_id)
        d = lib.sound_dir(word_id)
        d.mkdir(parents=True, exist_ok=True)
        for suffix in (".wav", ".lab", ".log", ".txt", ".webm"):
            (d / f"{word_id}{suffix}").write_bytes(f"{display}{suffix}".encode())
        lib.mfcc_dir.mkdir(parents=True, exist_ok=True)
        lib.mfcc_bin(word_id).write_bytes(b"mfcc" + display.encode())
        db = json.loads(lib.words_db.read_text(encoding="utf-8")) if lib.words_db.exists() else {}
        db[word_id] = {"display": display, "reading": reading, "accent": 1,
                       "accent_source": "mecab", "source": "manual"}
        if archived:
            db[word_id].update(archived=True, archived_at="2026-10-01T00:00:00")
        lib.words_db.parent.mkdir(parents=True, exist_ok=True)
        lib.words_db.write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
        if lip:
            refs = json.loads(lib.lip_refs.read_text(encoding="utf-8")) if lib.lip_refs.exists() else {}
            refs[word_id] = {"schema_version": 2, "ratios": [display]}
            lib.lip_refs.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")

    def _make_model(self):
        self._add_word("T01", "word2", "夕食", "ゆうしょく")
        self._add_word("T01", "word3", "削除", "さくじょ", archived=True)
        self._add_word("T01", "word5", "温度", "おんど", lip=False)
        return dw.save_from_user("T01")

    def _db(self, user_id):
        return json.loads(library_for(user_id).words_db.read_text(encoding="utf-8"))

    def test_save_renumbers_active_words(self):
        plan = self._make_model()
        self.assertEqual([(p["word_id"], p["from"]) for p in plan], [("word1", "word2"), ("word2", "word5")])
        words = dw.list_default_words()
        self.assertEqual([w["display"] for w in words], ["夕食", "温度"])
        lib = dw.default_library()
        self.assertEqual(lib.sample_wav("word2").read_bytes(), "温度.wav".encode())
        self.assertEqual(json.loads(lib.lip_refs.read_text(encoding="utf-8")), {"word1": {"schema_version": 2, "ratios": ["夕食"]}})

    def test_save_replaces_previous_set(self):
        self._make_model()
        dw.save_from_user("T01", ["word5"])
        self.assertEqual([w["display"] for w in dw.list_default_words()], ["温度"])
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir() if p.name.startswith("default_words")),
                         ["default_words"])

    def test_save_rejects_word_without_reference(self):
        self._add_word("T01", "word1", "夕食", "ゆうしょく")
        library_for("T01").sample_video("word1").unlink()
        with self.assertRaises(ValueError):
            dw.save_from_user("T01")
        self.assertEqual(dw.list_default_words(), [])

    def test_install_to_new_user(self):
        self._make_model()
        result = dw.install_for_user("A01")
        self.assertEqual([(a["word_id"], a["display"]) for a in result["added"]], [("word1", "夕食"), ("word2", "温度")])
        db = self._db("A01")
        self.assertEqual(db["word1"]["source"], "default")
        self.assertEqual(db["word2"]["reading"], "おんど")
        lib = library_for("A01")
        # お手本の中身が同じなので、お手本ID も見本と同じになる
        src = library_for("T01")
        self.assertEqual(reference_id(lib.sample_wav("word2"), lib.sound_dir("word2") / "word2.lab", lib.sample_video("word2")),
                         reference_id(src.sample_wav("word5"), src.sound_dir("word5") / "word5.lab", src.sample_video("word5")))
        self.assertTrue(lib.mfcc_bin("word1").exists())
        self.assertEqual(set(json.loads(lib.lip_refs.read_text(encoding="utf-8"))), {"word1"})
        self.assertEqual(lib.words_txt.read_text(encoding="utf-8").split(), ["夕食", "温度"])
        self.assertIsNone(usercontext.current_user())

    def test_install_continues_numbering_and_skips_same_words(self):
        self._make_model()
        self._add_word("B01", "word1", "温度", "おんど")
        self._add_word("B01", "word4", "学校", "がっこう", lip=False)
        result = dw.install_for_user("B01")
        self.assertEqual([(a["word_id"], a["display"]) for a in result["added"]], [("word5", "夕食")])
        self.assertEqual([s["display"] for s in result["skipped"]], ["温度"])
        self.assertEqual(self._db("B01")["word1"]["source"], "manual")
        # 2回目は何も増えない
        again = dw.install_for_user("B01")
        self.assertEqual(again["added"], [])
        self.assertEqual(len(self._db("B01")), 3)

    def test_install_skips_archived_same_word(self):
        self._make_model()
        self._add_word("B01", "word1", "夕食", "ゆうしょく", archived=True)
        result = dw.install_for_user("B01")
        self.assertEqual([a["display"] for a in result["added"]], ["温度"])

    def test_install_reuses_id_from_history(self):
        self._make_model()
        # 以前の版で完全に消した「温度」の練習記録が word7 で残っている
        with mock.patch("core.vocab.load_history",
                        return_value=[{"word_id": "word7", "display": "温度", "reading": "おんど"}]):
            result = dw.install_for_user("B01")
        self.assertEqual([(a["word_id"], a["reused"]) for a in result["added"]], [("word8", False), ("word7", True)])

    def test_dry_run_writes_nothing(self):
        self._make_model()
        result = dw.install_for_user("A01", dry_run=True)
        self.assertEqual([a["word_id"] for a in result["added"]], ["word1", "word2"])
        self.assertFalse(library_for("A01").words_db.exists())

    def test_installed_word_can_be_archived(self):
        self._make_model()
        dw.install_for_user("A01")
        usercontext.set_current_user("A01")
        vocab.archive_word("word1")
        self.assertEqual([w["display"] for w in vocab.list_words()], ["温度"])

    def test_install_without_set_adds_nothing(self):
        self.assertEqual(dw.install_for_user("A01"), {"added": [], "skipped": []})


if __name__ == "__main__":
    unittest.main()
