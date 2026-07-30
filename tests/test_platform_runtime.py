import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from platform_runtime import executable_entry_path


class PlatformRuntimeTest(unittest.TestCase):
    def test_macos_preserves_stable_symlink_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            versioned = root / "Cellar" / "python3.14"
            versioned.parent.mkdir()
            versioned.write_text("", encoding="utf-8")
            stable = root / "bin" / "python3"
            stable.parent.mkdir()
            stable.symlink_to(versioned)
            self.assertEqual(
                executable_entry_path(stable, platform_name="darwin"),
                stable,
            )

    def test_non_macos_resolves_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "python.exe"
            target.write_text("", encoding="utf-8")
            self.assertEqual(
                executable_entry_path(target, platform_name="win32"),
                target.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
