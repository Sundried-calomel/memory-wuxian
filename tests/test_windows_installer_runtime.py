from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

ISOLATED_RUNTIME_ENTRYPOINTS = (
    "windows_installer_broker.py",
    "install_windows_transaction.py",
    "install_codex_autosync_windows.py",
    "auto_update.py",
    "memory_dashboard.py",
    "maintenance_supervisor.py",
    "memory_cli.py",
    "install_cloud_sync.py",
    "install_governance_ai.py",
)

from install_windows_runtime import (
    RuntimeBundleError,
    activate_bundle,
    assemble_bundle,
    canonical_bytes,
    sha256_file,
    validate_bundle,
)


def make_bundle(root: Path) -> dict:
    (root / "python").mkdir(parents=True)
    (root / "python/python.exe").write_bytes(b"fixture-python")
    (root / "runtime-lock.json").write_bytes(canonical_bytes({"fixture": 1}))
    files = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    identity = {
        "lock_sha256": sha256_file(root / "runtime-lock.json"),
        "files": files,
    }
    manifest = {
        "schema_version": 1,
        "bundle_id": hashlib.sha256(canonical_bytes(identity)).hexdigest(),
        "lock_sha256": identity["lock_sha256"],
        "python_version": "3.14.7",
        "interpreter": "python/python.exe",
        "packages": [],
        "files": files,
    }
    (root / "runtime-manifest.json").write_bytes(canonical_bytes(manifest))
    return manifest


class WindowsInstallerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.manifest = make_bundle(self.bundle)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_offline_activation_uses_no_path_or_network(self) -> None:
        with mock.patch("install_windows_runtime.urllib.request.urlretrieve", side_effect=AssertionError("network")):
            with mock.patch.dict("os.environ", {"PATH": "C:\\untrusted"}):
                result = activate_bundle(self.bundle, self.root / "installed", probe=False)
        self.assertEqual(result["bundle_id"], self.manifest["bundle_id"])
        self.assertEqual(validate_bundle(Path(result["runtime_root"]))["bundle_id"], self.manifest["bundle_id"])

    def test_assembler_uses_only_supplied_verified_assets(self) -> None:
        assets = self.root / "assets"
        assets.mkdir()
        python_archive = assets / "python-fixture.zip"
        with zipfile.ZipFile(python_archive, "w") as archive:
            archive.writestr("python.exe", b"fixture-python")
            archive.writestr("python314._pth", "python314.zip\n.\n#import site\n")
        wheel = assets / "demo-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("demo.py", "VALUE = 1\n")
        lock = {
            "schema_version": 1,
            "python": {
                "version": "3.14.7",
                "filename": python_archive.name,
                "url": "https://example.invalid/python.zip",
                "sha256": sha256_file(python_archive),
            },
            "packages": [{"artifact": "wheel", "name": "demo", "version": "1.0"}],
        }
        lock_path = self.root / "fixture-lock.json"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")
        output = self.root / "assembled"
        def compile_guard(python_root: Path) -> None:
            source = python_root / "Lib/site-packages/sitecustomize.py"
            source.write_text("import sys\nsys.dont_write_bytecode = True\n", encoding="utf-8")
            cache = source.parent / "__pycache__"
            cache.mkdir()
            (cache / "sitecustomize.cpython-314.pyc").write_bytes(b"fixture-pyc")

        with (
            mock.patch("install_windows_runtime.urllib.request.urlretrieve", side_effect=AssertionError("network")),
            mock.patch("install_windows_runtime._install_no_bytecode_guard", side_effect=compile_guard),
        ):
            manifest = assemble_bundle(lock_path, assets, output)
        self.assertTrue((output / "python/Lib/site-packages/demo.py").is_file())
        self.assertTrue((output / "python/Lib/site-packages/sitecustomize.py").is_file())
        self.assertEqual(validate_bundle(output)["bundle_id"], manifest["bundle_id"])

    def test_real_bundle_guard_prevents_post_probe_file_drift(self) -> None:
        guard = (ROOT / "scripts/install_windows_runtime.py").read_text(encoding="utf-8")
        self.assertIn("sys.dont_write_bytecode = True", guard)
        self.assertIn("PYTHONDONTWRITEBYTECODE", guard)
        self.assertIn("validate_bundle(target)", guard)

    def test_bundle_hash_drift_and_extra_files_fail_closed(self) -> None:
        (self.bundle / "python/python.exe").write_bytes(b"drift")
        with self.assertRaisesRegex(RuntimeBundleError, "drift"):
            validate_bundle(self.bundle)
        self.bundle = self.root / "bundle-extra"
        self.bundle.mkdir()
        make_bundle(self.bundle)
        (self.bundle / "unexpected.txt").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeBundleError, "extra"):
            validate_bundle(self.bundle)

    def test_production_installer_has_no_path_python_or_online_pip(self) -> None:
        installer = (ROOT / "packaging/windows/install.ps1").read_text(encoding="utf-8").lower()
        self.assertIn('"runtime\\windows"', installer)
        self.assertIn('"python\\python.exe"', installer)
        self.assertIn("install_windows_runtime.py", installer)
        self.assertNotIn("bootstrap_windows.ps1", installer)
        self.assertNotIn("pip install", installer)
        self.assertNotIn("get-command python", installer)

    def test_inno_packages_only_the_manifest_bound_runtime_bytecode(self) -> None:
        installer = (ROOT / "packaging/windows/MemoryWuxian.iss").read_text(encoding="utf-8").lower()
        self.assertIn("__pycache__\\*,*.pyc", installer)
        self.assertIn(
            'source: "{#sourceroot}\\runtime\\windows\\python\\lib\\site-packages\\__pycache__\\sitecustomize.*.pyc"',
            installer,
        )
        self.assertIn(
            'destdir: "{tmp}\\memorywuxian\\candidate\\runtime\\windows\\python\\lib\\site-packages\\__pycache__"',
            installer,
        )

    def test_runtime_entrypoints_bootstrap_under_isolated_python(self) -> None:
        environment = os.environ.copy()
        environment["PATH"] = ""
        for entrypoint in ISOLATED_RUNTIME_ENTRYPOINTS:
            with self.subTest(entrypoint=entrypoint):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(ROOT / "scripts" / entrypoint),
                        "--help",
                    ],
                    cwd=self.root,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_broker_dispatch_resolves_sibling_manifest_under_isolated_python(self) -> None:
        driver = self.root / "isolated-broker-dispatch.py"
        driver.write_text(
            "from pathlib import Path\n"
            "from types import SimpleNamespace\n"
            "import importlib.util, sys, uuid\n"
            "broker_path = Path(sys.argv[1]).resolve()\n"
            "root = Path(sys.argv[2]).resolve()\n"
            "spec = importlib.util.spec_from_file_location('isolated_broker', broker_path)\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name] = module\n"
            "spec.loader.exec_module(module)\n"
            "import windows_install_manifest as manifest_module\n"
            "sid = 'S-1-5-21-100-200-300-1001'\n"
            "module.current_user_sid = lambda: sid\n"
            "manifest = root / 'request.json'\n"
            "manifest.write_text('{\\\"operation\\\":\\\"install\\\"}\\n', encoding='utf-8')\n"
            "controller = root / 'controller.py'\n"
            "controller.write_text('# child\\n', encoding='utf-8')\n"
            "transaction_id = str(uuid.uuid4())\n"
            "ledger = module.NonceLedger(root / 'nonces')\n"
            "payload = {'transaction_id': transaction_id, 'operation': 'install', "
            "'target_sid': sid, 'manifest_path': str(manifest), "
            "'manifest_sha256': module.sha256_file(manifest), "
            "'controller_path': str(controller), "
            "'controller_sha256': module.sha256_file(controller), "
            "'nonce': ledger.issue(transaction_id, sid)}\n"
            "request = root / 'broker-request.json'\n"
            "request.write_bytes(module._canonical_json(payload))\n"
            "reader_calls = []\n"
            "def read_manifest(path):\n"
            " reader_calls.append(Path(path).resolve())\n"
            " return SimpleNamespace(operation='install', "
            "runtime_bundle=SimpleNamespace(python_executable=Path(sys.executable)))\n"
            "manifest_module.read_manifest = read_manifest\n"
            "child_calls = []\n"
            "def run_child(command, check=False):\n"
            " child_calls.append([str(item) for item in command])\n"
            " return SimpleNamespace(returncode=37)\n"
            "module.subprocess.run = run_child\n"
            "result = module.dispatch_request(request, module.sha256_file(request), root / 'nonces')\n"
            "assert result == 37, result\n"
            "assert reader_calls == [manifest.resolve()], reader_calls\n"
            "assert len(child_calls) == 1, child_calls\n"
            "assert child_calls[0][1] == str(controller.resolve()), child_calls\n"
            "assert child_calls[0][2:] == ['--execute-manifest', str(manifest.resolve())], child_calls\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["PATH"] = ""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(driver),
                str(ROOT / "scripts" / "windows_installer_broker.py"),
                str(self.root),
            ],
            cwd=self.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_lock_pins_python_and_every_dependency_version(self) -> None:
        lock = json.loads((ROOT / "scripts/windows_runtime_lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["python"]["version"], "3.14.7")
        self.assertEqual(lock["python"]["sha256"], "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15")
        self.assertEqual(len(lock["packages"]), 10)
        self.assertTrue(all(item["version"] for item in lock["packages"]))


if __name__ == "__main__":
    unittest.main()
