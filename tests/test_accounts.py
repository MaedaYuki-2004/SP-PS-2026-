import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import accounts as acc
from core import storage


class TempDataTestCase(unittest.TestCase):
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


class AccountDeletionTests(TempDataTestCase):
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


class SetupCodeTests(TempDataTestCase):
    """初回ログインの「はじめのコード」。利用者 ID を知っているだけの人が先にパスワードを決められないこと。"""

    def _raw(self, uid):
        return acc.load_accounts()[uid]

    def test_code_is_six_digits_and_stored_only_as_hash(self):
        code = acc.create_account("A01")["setup_code"]
        self.assertRegex(code, r"^\d{6}$")
        text = (self.data_dir / "config" / "accounts.json").read_text(encoding="utf-8")
        self.assertNotIn(code, text)
        listed = acc.list_accounts()[0]
        self.assertTrue(listed["setup_code_issued"])
        self.assertNotIn("setup_code_hash", listed)
        self.assertTrue(acc.setup_code_issued("A01"))

    def test_wrong_code_does_not_set_password(self):
        code = acc.create_account("A01")["setup_code"]
        wrong = "000000" if code != "000000" else "111111"
        with self.assertRaises(acc.SetupCodeError):
            acc.complete_first_login("A01", wrong, "abcdef1")
        self.assertTrue(acc.needs_password_setup("A01"))
        self.assertEqual(self._raw("A01")["setup_code_failures"], 1)

    def test_right_code_sets_password_and_is_used_up(self):
        code = acc.create_account("A01")["setup_code"]
        acc.complete_first_login("A01", code, "abcdef1")
        self.assertIsNotNone(acc.verify_login("A01", "abcdef1"))
        self.assertNotIn("setup_code_hash", self._raw("A01"))
        self.assertFalse(acc.setup_code_issued("A01"))

    def test_full_width_and_spaces_are_accepted(self):
        code = acc.create_account("A01")["setup_code"]
        typed = " ".join(chr(ord(c) + 0xFEE0) for c in code)   # 全角の数字を空白で区切って入れた
        acc.complete_first_login("A01", typed, "abcdef1")
        self.assertFalse(acc.needs_password_setup("A01"))

    def test_too_many_failures_make_the_code_unusable(self):
        code = acc.create_account("A01")["setup_code"]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(acc.SETUP_CODE_MAX_FAILURES):
            with self.assertRaises(acc.SetupCodeError):
                acc.complete_first_login("A01", wrong, "abcdef1")
        with self.assertRaises(acc.SetupCodeError):
            acc.complete_first_login("A01", code, "abcdef1")   # 正しいコードでも、もう使えない
        self.assertTrue(acc.needs_password_setup("A01"))
        new = acc.issue_setup_code("A01")["setup_code"]
        acc.complete_first_login("A01", new, "abcdef1")
        self.assertFalse(acc.needs_password_setup("A01"))

    def test_reissue_invalidates_the_old_code(self):
        old = acc.create_account("A01")["setup_code"]
        new = acc.issue_setup_code("A01")["setup_code"]
        if old != new:
            with self.assertRaises(acc.SetupCodeError):
                acc.complete_first_login("A01", old, "abcdef1")
        acc.complete_first_login("A01", new, "abcdef1")
        self.assertFalse(acc.needs_password_setup("A01"))

    def test_clear_password_issues_a_new_code(self):
        code = acc.create_account("A01")["setup_code"]
        acc.complete_first_login("A01", code, "abcdef1")
        with self.assertRaises(ValueError):
            acc.issue_setup_code("A01")   # 設定済みのときは「パスワード初期化」を使う
        new = acc.clear_password("A01")["setup_code"]
        self.assertIsNone(acc.verify_login("A01", "abcdef1"))
        acc.complete_first_login("A01", new, "ghijkl2")
        self.assertIsNotNone(acc.verify_login("A01", "ghijkl2"))

    def test_account_without_code_cannot_complete_first_login(self):
        # はじめのコードを入れる前に作った、パスワード未設定のアカウント
        acc.create_account("A01")
        accounts = acc.load_accounts()
        for k in ("setup_code_hash", "setup_code_issued_at", "setup_code_failures"):
            accounts["A01"].pop(k)
        acc.save_accounts(accounts)
        self.assertFalse(acc.setup_code_issued("A01"))
        with self.assertRaises(acc.SetupCodeError):
            acc.complete_first_login("A01", "123456", "abcdef1")
        self.assertTrue(acc.needs_password_setup("A01"))

    def test_code_reissued_while_checking(self):
        # コードを照合している間に先生が発行し直したら、前のコードでは決められない
        old = acc.create_account("A01")["setup_code"]
        real = acc.check_password_hash
        new = []

        def reissue_during_check(pwhash, password):
            ok = real(pwhash, password)
            while not new or new[0] == old:
                new[:] = [acc.issue_setup_code("A01")["setup_code"]]
            return ok
        with mock.patch("core.accounts.check_password_hash", reissue_during_check):
            with self.assertRaises(acc.SetupCodeError):
                acc.complete_first_login("A01", old, "abcdef1")
        self.assertTrue(acc.needs_password_setup("A01"))
        acc.complete_first_login("A01", new[0], "abcdef1")
        self.assertIsNotNone(acc.verify_login("A01", "abcdef1"))


class LockScopeTests(TempDataTestCase):
    """パスワード・コードの照合（scrypt、0.1秒ほど）は、アカウントのロックの外で行う。

    ロックの中で行うと、授業の始めに全員が同時にログインしたとき、1人ずつ順番に待たされる。
    """

    def _spy_lock_held_while_checking(self):
        held = []
        real = acc.check_password_hash

        def spy(pwhash, password):
            try:
                with storage.lock("accounts", timeout=0):
                    held.append(False)
            except storage.LockTimeout:
                held.append(True)
            return real(pwhash, password)
        patcher = mock.patch("core.accounts.check_password_hash", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return held

    def test_login_checks_password_outside_lock(self):
        code = acc.create_account("A01")["setup_code"]
        acc.complete_first_login("A01", code, "abcdef1")
        held = self._spy_lock_held_while_checking()
        self.assertIsNotNone(acc.verify_login("A01", "abcdef1"))
        self.assertIsNone(acc.verify_login("A01", "wrong-pass"))
        self.assertEqual(held, [False, False])

    def test_first_login_checks_code_outside_lock(self):
        code = acc.create_account("A01")["setup_code"]
        held = self._spy_lock_held_while_checking()
        with self.assertRaises(acc.SetupCodeError):
            acc.complete_first_login("A01", f"{(int(code) + 1) % 10 ** 6:06d}", "abcdef1")
        acc.complete_first_login("A01", code, "abcdef1")
        self.assertEqual(held, [False, False])


if __name__ == "__main__":
    unittest.main()
