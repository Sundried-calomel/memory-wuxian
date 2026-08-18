import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POSTINSTALL = ROOT / "packaging/macos/scripts/postinstall"
BUILD_PKG = ROOT / "packaging/macos/build_pkg.sh"


class MacosPackagingContractTest(unittest.TestCase):
    def test_postinstall_uses_user_transaction_with_quoted_exact_roots(self):
        source = POSTINSTALL.read_text(encoding="utf-8")
        self.assertIn('"$source_root/scripts/install_macos_transaction.py"', source)
        for option, value in (
            ("--source-root", "$source_root"),
            ("--skill-root", "$skill_root"),
            ("--archive-root", "$archive_root"),
            ("--sessions-root", "$sessions_root"),
            ("--python-executable", "$python_executable"),
            ("--codex-cli", "$codex_cli"),
        ):
            self.assertIn(f'{option} "{value}"', source)

    def test_postinstall_rejects_ambiguous_archive_pointer(self):
        source = POSTINSTALL.read_text(encoding="utf-8")
        self.assertIn("active archive pointer must contain exactly one path", source)
        self.assertIn('archive_root="$(cd "$archive_root" && /bin/pwd -P)"', source)
        self.assertIn("transaction did not commit the requested archive root", source)

    def test_package_preserves_payload_bytes_with_rsync_archive_mode(self):
        source = BUILD_PKG.read_text(encoding="utf-8")
        self.assertIn("rsync -a", source)
        self.assertNotIn("dos2unix", source)
        self.assertNotIn("iconv", source)


if __name__ == "__main__":
    unittest.main()
