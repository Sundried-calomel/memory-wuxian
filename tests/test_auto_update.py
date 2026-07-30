import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
import auto_update


class AutoUpdateTest(unittest.TestCase):
    def test_versions_and_platform_assets_are_strict(self):
        self.assertGreater(auto_update.version_tuple("v1.3.0"), auto_update.version_tuple("1.2.1"))
        self.assertEqual(
            auto_update.asset_names("1.3.0", "Windows"),
            (
                "MemoryWuxian-1.3.0-Windows-x64-Setup.exe",
                "MemoryWuxian-1.3.0-Windows-x64-Setup.exe.sha256",
            ),
        )
        with self.assertRaises(ValueError):
            auto_update.version_tuple("1.3.0-beta.1")

    def test_checksum_requires_matching_filename_and_sha256(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "MemoryWuxian-1.3.0-Windows-x64-Setup.exe"
            checksum = root / f"{package.name}.sha256"
            package.write_bytes(b"verified package")
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            checksum.write_text(f"{digest}  {package.name}\n", encoding="utf-8")
            self.assertEqual(auto_update.verify_checksum(package, checksum), digest)
            checksum.write_text(f"{digest}  different.exe\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                auto_update.verify_checksum(package, checksum)

    def test_main_ignores_equal_release_and_rejects_prerelease(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skill"
            skill.mkdir()
            skill.joinpath("pyproject.toml").write_text(
                '[project]\nversion = "1.2.1"\n', encoding="utf-8"
            )
            state = root / "state.json"
            release = root / "release.json"
            release.write_text(json.dumps({
                "tag_name": "v1.2.1", "draft": False, "prerelease": False, "assets": []
            }), encoding="utf-8")
            result = auto_update.main([
                "--skill-root", str(skill), "--state-file", str(state),
                "--release-json", str(release), "--force",
            ])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "up-to-date")
            release.write_text(json.dumps({
                "tag_name": "v1.3.0", "draft": False, "prerelease": True, "assets": []
            }), encoding="utf-8")
            result = auto_update.main([
                "--skill-root", str(skill), "--state-file", str(state),
                "--release-json", str(release), "--force",
            ])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "failed")

    def test_verified_update_is_downloaded_then_staged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "skill"
            skill.mkdir()
            skill.joinpath("pyproject.toml").write_text(
                '[project]\nversion = "1.2.1"\n', encoding="utf-8"
            )
            package_name, checksum_name = auto_update.asset_names("1.3.0", "Windows")
            package_bytes = b"release package"
            digest = hashlib.sha256(package_bytes).hexdigest()
            release = {
                "tag_name": "v1.3.0", "draft": False, "prerelease": False,
                "assets": [
                    {"name": package_name, "browser_download_url": "package-url"},
                    {"name": checksum_name, "browser_download_url": "checksum-url"},
                ],
            }
            release_path = root / "release.json"
            release_path.write_text(json.dumps(release), encoding="utf-8")

            def fake_download(url, destination):
                if url == "package-url":
                    destination.write_bytes(package_bytes)
                else:
                    destination.write_text(f"{digest}  {package_name}\n", encoding="utf-8")

            state = root / "state.json"
            with patch.object(auto_update.platform, "system", return_value="Windows"), \
                 patch.object(auto_update, "download", side_effect=fake_download), \
                 patch.object(auto_update, "stage_install", return_value="staged-for-next-login") as stage:
                result = auto_update.main([
                    "--skill-root", str(skill), "--state-file", str(state),
                    "--download-directory", str(root / "downloads"),
                    "--release-json", str(release_path), "--force",
                ])
            self.assertEqual(result, 0)
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "staged-for-next-login")
            self.assertEqual(payload["sha256"], digest)
            stage.assert_called_once()
            self.assertEqual("Windows", stage.call_args.args[1])

    def test_macos_update_uses_user_transaction_without_installer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "MemoryWuxian-1.3.0-macOS-universal.pkg"
            package.write_bytes(b"package")
            skill = root / ".codex/skills/memory-wuxian"
            skill.mkdir(parents=True)
            python = root / "bin/python3"
            python.parent.mkdir()
            python.write_text("", encoding="utf-8")
            source = root / "source"
            source.joinpath("scripts").mkdir(parents=True)
            source.joinpath("scripts/install_macos_transaction.py").write_text(
                "", encoding="utf-8"
            )
            source.joinpath("SKILL.md").write_text("", encoding="utf-8")
            archive = root / "archive"
            archive.mkdir()
            pointer = skill.parent.parent / "memory-wuxian-active-root.txt"
            pointer.write_text(str(archive) + "\n", encoding="utf-8")
            codex = root / "codex"
            codex.write_text("", encoding="utf-8")
            codex.chmod(0o755)

            completed = type(
                "Completed",
                (),
                {"stdout": json.dumps({"status": "installed"})},
            )()
            with patch.object(
                auto_update,
                "_extract_macos_skill",
                return_value=source,
            ), patch.object(
                auto_update,
                "_codex_cli",
                return_value=codex,
            ), patch.object(
                auto_update.subprocess,
                "run",
                return_value=completed,
            ) as run:
                status = auto_update.stage_install(
                    package,
                    "Darwin",
                    skill_root=skill,
                    python_executable=python,
                    runner=run,
                )
            self.assertEqual("installed-user-transaction", status)
            command = run.call_args.args[0]
            self.assertIn("install_macos_transaction.py", command[1])
            self.assertNotIn("/usr/sbin/installer", command)

    def test_macos_scheduler_passes_the_stable_python_entry(self):
        installer = (
            SKILL_ROOT / "scripts" / "install_auto_update.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--python-executable"', installer)
        self.assertIn("executable_entry_path", installer)


if __name__ == "__main__":
    unittest.main()
