from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_environment import EnvironmentRegistry
from semantic_runtime_contract import (
    ARTIFACT_ID,
    contract_sha256,
    environment_manifest,
    load_contract,
    local_status,
    realize,
    registered_contract,
)


class SemanticRuntimeContractTests(unittest.TestCase):
    def test_bundled_contract_is_closed_and_pinned(self):
        contract = load_contract()
        self.assertEqual("multilingual-e5-small", contract["provider"])
        self.assertEqual(384, contract["model"]["dimension"])
        self.assertFalse(contract["runtime"]["remote_model_code"])
        self.assertTrue(contract["runtime"]["offline_inference"])
        self.assertEqual("query: ", contract["embedding"]["query_prefix"])
        self.assertEqual("passage: ", contract["embedding"]["passage_prefix"])
        self.assertRegex(contract_sha256(contract), r"^[0-9a-f]{64}$")

    def test_environment_manifest_is_reproducible_and_registers_runtime_class(self):
        first = environment_manifest("mac-mini-lab")
        second = environment_manifest("mac-mini-lab")
        self.assertEqual(first, second)
        artifact = first["artifacts"][0]["artifact"]
        self.assertEqual(ARTIFACT_ID, artifact["artifact_id"])
        self.assertEqual("global-runtime-contract", artifact["object_class"])
        with tempfile.TemporaryDirectory() as directory:
            registry = EnvironmentRegistry(Path(directory))
            registered = registry.register(first, apply=True)
            self.assertEqual("registered", registered["status"])
            self.assertEqual(load_contract(), registered_contract(registry))
            self.assertEqual(
                "no-change",
                registry.register(second, apply=True)["status"],
            )

    def test_status_verifies_exact_artifacts_and_runtime(self):
        contract = load_contract()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            model_root = base / "model"
            runtime_root = base / "runtime"
            model_dir = model_root / contract["model"]["revision"]
            model_dir.mkdir(parents=True)
            for artifact in contract["model"]["artifacts"]:
                artifact["size"] = len(artifact["path"].encode())
                import hashlib
                artifact["sha256"] = hashlib.sha256(
                    artifact["path"].encode()
                ).hexdigest()
                (model_dir / artifact["path"]).write_bytes(
                    artifact["path"].encode()
                )
            (model_dir / "model-manifest.json").write_text("{}\n")
            python = runtime_root / (
                "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            )
            python.parent.mkdir(parents=True)
            python.write_text("")
            status = local_status(
                contract,
                model_root=model_root,
                runtime_root=runtime_root,
            )
            self.assertTrue(status["realized"])

    def test_realization_is_preview_only_until_explicit_apply(self):
        contract = load_contract()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with patch("semantic_runtime_contract.subprocess.run") as run:
                result = realize(
                    contract,
                    model_root=base / "model",
                    runtime_root=base / "runtime",
                    apply=False,
                )
            self.assertEqual("preview", result["status"])
            self.assertFalse(result["automatic"])
            self.assertTrue(result["supported_by_installed_skill"])
            run.assert_not_called()

    def test_realization_fails_closed_for_a_newer_unimplemented_contract(self):
        contract = load_contract()
        contract["model"]["dimension"] = 768
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaisesRegex(ValueError, "not supported"):
                realize(
                    contract,
                    model_root=base / "model",
                    runtime_root=base / "runtime",
                    apply=True,
                )

    def test_installer_and_worker_read_the_shared_contract(self):
        installer = (ROOT / "scripts" / "install_multilingual_e5.py").read_text()
        worker = (ROOT / "scripts" / "semantic_e5_worker.py").read_text()
        guarded = (ROOT / "scripts" / "memory_guarded_features.py").read_text()
        self.assertIn("load_contract", installer)
        self.assertIn("--contract", installer)
        self.assertIn("load_contract", worker)
        self.assertIn("query_prefix", json.dumps(load_contract()))
        self.assertIn("load_contract", guarded)


if __name__ == "__main__":
    unittest.main()
