import re
import subprocess
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CARGO_MANIFEST = ROOT / "native-collector" / "Cargo.toml"
RUST_ROOT = ROOT / "native-collector"
BINARIES = {
    "memory-wuxian-collector": ROOT / "bin" / "memory-wuxian-collector.exe",
    "memory-wuxian-envelope": ROOT / "bin" / "memory-wuxian-envelope.exe",
}


def normalize_cargo_version(version: str) -> str:
    return re.sub(r"(?<=\d)-(?=[A-Za-z])", "", version)


class VersionContractTest(unittest.TestCase):
    def test_source_versions_match_beta_product_version(self):
        product = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
        cargo = tomllib.loads(CARGO_MANIFEST.read_text(encoding="utf-8"))["package"]["version"]

        self.assertEqual(normalize_cargo_version(cargo), product)

    def test_rust_cli_names_and_versions_match_product(self):
        sources = {
            "memory-wuxian-collector": RUST_ROOT / "src" / "main.rs",
            "memory-wuxian-envelope": RUST_ROOT / "src" / "bin" / "memory-wuxian-envelope.rs",
        }
        for name, source in sources.items():
            text = source.read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn(f'name = "{name}"', text)
                self.assertIn("version,", text)
                self.assertNotIn("version = \"", text)

    def test_checked_in_binaries_are_current_or_require_rebuild(self):
        stale = []
        cargo = tomllib.loads(CARGO_MANIFEST.read_text(encoding="utf-8"))["package"]["version"]
        for name, executable in BINARIES.items():
            if not executable.exists():
                stale.append(f"{name}: missing")
                continue
            completed = subprocess.run(
                [str(executable), "--version"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            expected = f"{name} {cargo}"
            actual = completed.stdout.strip()
            if completed.returncode != 0 or actual != expected:
                stale.append(f"{name}: expected {expected!r}, got {actual!r}")

        self.assertFalse(
            stale,
            "release binaries must be rebuilt from the current Rust sources:\n"
            + "\n".join(stale),
        )


if __name__ == "__main__":
    unittest.main()
