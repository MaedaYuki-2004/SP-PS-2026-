import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import accounts as acc


class AccountDeletionTests(unittest.TestCase):
    def setUp(self):
        # 実際の data/ に触れないよう、アカウントとデータの置き場所を一時フォルダへ向ける
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        for target, value in [("core.accounts.DATA_DIR", self.data_dir),
                              ("core.accounts.ACCOUNTS_PATH", self.data_dir / "config" / "accounts.json"),
                              ("core.storage.LOCK_DIR", self.data_dir / "run" / "locks")]:
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_delete_removes_participant_data(self):
        # 参加をやめた人のデータ（練習記録・お手本の録画など）はアカウントと一緒に消す（倫理審査資料 §7.1）
        acc.create_account("A01")
        user_dir = self.data_dir / "users" / "A01"
        (user_dir / "library" / "sound" / "word1").mkdir(parents=True)
        (user_dir / "history.json").write_text("[]", encoding="utf-8")
        acc.delete_account("A01")
        self.assertFalse(acc.account_exists("A01"))
        self.assertFalse(user_dir.exists())

    def test_cannot_reuse_id_while_old_data_remains(self):
        # 前の人のデータが残る ID を作り直すと、新しい参加者に前の人の記録が見えてしまう
        (self.data_dir / "users" / "A02").mkdir(parents=True)
        with self.assertRaises(ValueError):
            acc.create_account("A02")

    def test_rejects_ids_differing_only_in_case(self):
        # Mac・Windows ではフォルダ名の大文字・小文字が区別されず、データが混ざる
        acc.create_account("A03")
        with self.assertRaises(ValueError):
            acc.create_account("a03")


if __name__ == "__main__":
    unittest.main()
