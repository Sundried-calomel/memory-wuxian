import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_PATHS = (
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "README.ja.md",
)


class ReadmeLocalizationTest(unittest.TestCase):
    def test_localized_readmes_share_structure_and_navigation(self):
        texts = [path.read_text(encoding="utf-8") for path in README_PATHS]
        heading_counts = [len(re.findall(r"^#{1,3} ", text, re.MULTILINE)) for text in texts]
        fence_counts = [len(re.findall(r"^```", text, re.MULTILINE)) for text in texts]

        self.assertEqual(heading_counts, [18, 18, 18])
        self.assertEqual(fence_counts, [30, 30, 30])
        for text in texts:
            self.assertIn("[English](README.md)", text)
            self.assertIn("[简体中文](README.zh-CN.md)", text)
            self.assertIn("[日本語](README.ja.md)", text)
            self.assertIn("memory_cli.py", text)
            self.assertIn("MemoryWuxian-<version>-macOS-universal.pkg", text)
            self.assertIn("MemoryWuxian-<version>-Windows-x64-Setup.exe", text)
            self.assertIn("README.zh-CN.md", text)
            self.assertIn("README.ja.md", text)


if __name__ == "__main__":
    unittest.main()
