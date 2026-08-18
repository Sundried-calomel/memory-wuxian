import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CARGO_MANIFEST = ROOT / "native-collector" / "Cargo.toml"
RUST_ROOT = ROOT / "native-collector"


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
            "memory-wuxian-collector": RUST_ROOT / "src" / "lib.rs",
            "memory-wuxian-envelope": RUST_ROOT / "src" / "bin" / "memory-wuxian-envelope.rs",
        }
        for name, source in sources.items():
            text = source.read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn(f'name = "{name}"', text)
                self.assertIn("version,", text)
                self.assertNotIn("version = \"", text)

    def test_locally_built_native_binaries_report_the_current_version(self):
        cargo = manifest_version(CARGO_MANIFEST, "package")
        executable = (
            ROOT
            / "native-collector"
            / "target"
            / "debug"
            / ("memory-wuxian-collector.exe" if sys.platform == "win32" else "memory-wuxian-collector")
        )
        if not executable.is_file():
            self.skipTest("native candidate has not been built in this checkout")
        completed = subprocess.run(
            [str(executable), "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual(f"memory-wuxian-collector {cargo}", completed.stdout.strip())

    def test_bundled_native_binaries_report_the_current_version(self):
        cargo = manifest_version(CARGO_MANIFEST, "package")
        if sys.platform == "win32":
            binaries = ("memory-wuxian-collector.exe",)
        elif sys.platform == "darwin":
            binaries = ("memory-wuxian-collector", "memory-wuxian-envelope")
        else:
            self.skipTest("desktop bundled binaries are not shipped on this platform")

        for filename in binaries:
            executable = ROOT / "bin" / filename
            self.assertTrue(executable.is_file(), f"missing bundled executable: {executable}")
            completed = subprocess.run(
                [str(executable), "--version"],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            name = filename.removesuffix(".exe")
            with self.subTest(name=name):
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual(f"{name} {cargo}", completed.stdout.strip())


if __name__ == "__main__":
    unittest.main()
