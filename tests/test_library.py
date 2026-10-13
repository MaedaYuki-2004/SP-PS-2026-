import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import usercontext
from core.library import SHARED_LIBRARY, current_library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        # 実際の参加者フォルダ（data/users/）に書き込まない
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        patcher = mock.patch("core.library.DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        usercontext.clear()

    def test_logged_in_users_get_separate_libraries(self):
        usercontext.set_current_user("u1")
        a = current_library()
        usercontext.set_current_user("u2")
        b = current_library()
        self.assertEqual(a.words_db, self.data_dir / "users" / "u1" / "library" / "words_db.json")
        self.assertFalse(a.shared)
        for name in ("words_db", "sound_root", "mfcc_dir", "lip_refs", "formant_cache"):
            self.assertNotEqual(getattr(a, name), getattr(b, name), name)
        self.assertNotEqual(a.sample_wav("word1"), b.sample_wav("word1"))

    def test_without_login_uses_shared_library(self):
        usercontext.set_current_user(None)
        self.assertIs(current_library(), SHARED_LIBRARY)
        self.assertTrue(SHARED_LIBRARY.shared)

    def test_rejects_path_traversal(self):
        usercontext.set_current_user("..")
        with self.assertRaises(ValueError):
            current_library()

    def test_words_are_per_user(self):
        from core import vocab
        usercontext.set_current_user("u1")
        vocab.save_db({"word1": {"display": "夕食", "reading": "ゆうしょく"}})
        self.assertEqual(vocab.get_next_word_id(vocab.load_db()), "word2")
        usercontext.set_current_user("u2")
        self.assertEqual(vocab.list_words(), [])
        self.assertEqual(vocab.get_next_word_id(vocab.load_db()), "word1")


if __name__ == "__main__":
    unittest.main()
