from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from platform_paths import active_root_pointer


class PlatformPathsTests(unittest.TestCase):
    def test_active_root_pointer_preserves_unicode_spaces_and_long_paths(self) -> None:
        codex_home = Path("C:/") / ("设置 日本語 ￥ emoji-😀 " + "长" * 260)
        with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
            self.assertEqual(
                active_root_pointer(),
                codex_home / "memory-wuxian-active-root.txt",
            )


if __name__ == "__main__":
    unittest.main()
