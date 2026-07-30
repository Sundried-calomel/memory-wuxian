import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CARGO_MANIFEST = ROOT / "native-collector" / "Cargo.toml"
RUST_ROOT = ROOT / "native-collector"
CHECKED_IN_BINARIES = {
    "memory-wuxian-collector": ROOT / "bin" / "memory-wuxian-collector.exe",
}


def normalize_cargo_version(version: str) -> str:
    return re.sub(r"(?<=\d)-(?=[A-Za-z])", "", version)


def manifest_version(path: Path, section: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^\[{re.escape(section)}\]\s*$.*?^version\s*=\s*\"([^\"]+)\"",
        text,
    )
    if not match:
        raise AssertionError(f"version missing from [{section}] in {path}")
    return match.group(1)


class VersionContractTest(unittest.TestCase):
    def test_source_versions_match_beta_product_version(self):
        product = manifest_version(PYPROJECT, "project")
        cargo = manifest_version(CARGO_MANIFEST, "package")

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
        cargo = manifest_version(CARGO_MANIFEST, "package")
        for name, executable in CHECKED_IN_BINARIES.items():
            if not executable.exists():
                stale.append(f"{name}: missing")
                continue
            expected = f"{name} {cargo}"
            if os.name == "nt":
                completed = subprocess.run(
                    [str(executable), "--version"],
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    check=False,
                )
                actual = completed.stdout.strip()
                if completed.returncode != 0 or actual != expected:
                    stale.append(
                        f"{name}: expected {expected!r}, got {actual!r}"
                    )
            elif expected.encode("ascii") not in executable.read_bytes():
                stale.append(f"{name}: embedded version {expected!r} missing")

        self.assertFalse(
            stale,
            "release binaries must be rebuilt from the current Rust sources:\n"
            + "\n".join(stale),
        )


if __name__ == "__main__":
    unittest.main()
