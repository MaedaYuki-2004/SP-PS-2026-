import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config import AUDIO_WAV_DIR, WORD_ID_MEMO_PATH
from core import usercontext
from core.workspace import clear_lip_dir, current_workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        # 実際の参加者フォルダ（data/users/）に作業フォルダを作らない
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        patcher = mock.patch("core.workspace.DATA_DIR", self.data_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        usercontext.clear()

    def test_logged_in_users_get_separate_files(self):
        usercontext.set_current_user("u1")
        a = current_workspace()
        usercontext.set_current_user("u2")
        b = current_workspace()
        self.assertEqual(a.wav, self.data_dir / "users" / "u1" / "work" / "align" / "test.wav")
        for name in ("wav", "lab", "log", "txt", "segment_wav", "word_id_path", "lip_dir"):
            self.assertNotEqual(getattr(a, name), getattr(b, name), name)

    def test_segment_wav_is_outside_alignment_dir(self):
        # segment_julius.pl は align_dir の *.wav をすべて処理する
        usercontext.set_current_user("u1")
        ws = current_workspace()
        self.assertNotEqual(ws.segment_wav.parent, ws.align_dir)

    def test_without_login_uses_shared_paths(self):
        usercontext.set_current_user(None)
        ws = current_workspace()
        self.assertEqual(ws.wav, AUDIO_WAV_DIR / "test.wav")
        self.assertEqual(ws.word_id_path, WORD_ID_MEMO_PATH)

    def test_rejects_path_traversal(self):
        usercontext.set_current_user("..")
        with self.assertRaises(ValueError):
            current_workspace()

    def test_clear_lip_dir_removes_only_that_users_videos(self):
        # 結果を見ないまま残った口の動画は、ログインのときに消す（ほかの参加者の分は消さない）
        usercontext.set_current_user("u1")
        a = current_workspace().lip_dir
        usercontext.set_current_user("u2")
        b = current_workspace().lip_dir
        (a / "left.webm").write_bytes(b"x")
        (b / "other.webm").write_bytes(b"x")
        self.assertEqual(clear_lip_dir("u1"), 1)
        self.assertEqual(list(a.iterdir()), [])
        self.assertTrue((b / "other.webm").exists())
        self.assertEqual(clear_lip_dir("nobody"), 0)


if __name__ == "__main__":
    unittest.main()
