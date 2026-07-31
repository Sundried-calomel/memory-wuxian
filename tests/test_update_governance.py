from __future__ import annotations

import hashlib
import base64
import json
import shutil
import subprocess
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from memory_update_governance import (
    SIGNING_IDENTITY,
    SIGNING_NAMESPACE,
    apply_delta_bundle,
    load_signed_metadata,
    select_release,
    stage_update,
)


def descriptor(url, data):
    return {"url": url, "sha256": hashlib.sha256(data).hexdigest(), "filename": f"{url}.bin"}


class UpdateGovernanceTests(unittest.TestCase):
    def metadata(self):
        full = b"full-2.9"
        return full, {"schema_version": 1, "releases": [
            {"version": "2.9.0", "channel": "stable", "full": descriptor("full", full), "deltas": []},
            {"version": "2.10.0-beta.1", "channel": "beta", "full": descriptor("beta", b"beta"), "deltas": []},
            {"version": "2.11.0", "channel": "development", "full": descriptor("dev", b"dev"), "deltas": []},
        ]}

    def test_mw29_channel_001_selection_is_explicit(self):
        _, metadata = self.metadata()
        self.assertEqual("2.9.0", select_release(metadata, "stable", "2.8.0")["release"]["version"])
        self.assertEqual("2.10.0-beta.1", select_release(metadata, "beta", "2.8.0")["release"]["version"])
        self.assertEqual("2.11.0", select_release(metadata, "development", "2.8.0")["release"]["version"])

    def test_mw29_delta_fallback_001_stages_full_without_execution(self):
        full = b"verified-full"
        release = {
            "version": "2.9.0", "channel": "stable", "full": descriptor("full", full),
            "deltas": [{"from_version": "2.8.0", "artifact": descriptor("delta", b"delta")}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate.bin"
            result = stage_update(release, "2.8.0", destination, lambda url: {"delta": b"delta", "full": full}[url], lambda _patch: (_ for _ in ()).throw(ValueError("patch rejected")))
            self.assertEqual("full", result["artifact_kind"])
            self.assertEqual("failed", result["attempts"][0]["status"])
            self.assertEqual(full, destination.read_bytes())
            self.assertFalse(result["installed"])
            self.assertFalse(result["executed"])

    def test_mw29_hash_001_corruption_does_not_replace_prior_stage(self):
        release = {"version": "2.9.0", "channel": "stable", "full": descriptor("full", b"expected"), "deltas": []}
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate.bin"
            destination.write_bytes(b"prior")
            with self.assertRaises(ValueError):
                stage_update(release, "2.8.0", destination, lambda _url: b"corrupt", lambda value: value)
            self.assertEqual(b"prior", destination.read_bytes())

    def test_mw29_delta_001_reconstructs_and_verifies_target(self):
        base = b"old-package"
        target = b"old-package-new"
        patch = json.dumps({
            "format": "memory-wuxian-binary-delta-v1",
            "base_sha256": hashlib.sha256(base).hexdigest(),
            "target_sha256": hashlib.sha256(target).hexdigest(),
            "operations": [
                {"copy": [0, len(base)]},
                {"data": base64.b64encode(b"-new").decode("ascii")},
            ],
        }, separators=(",", ":")).encode("utf-8")
        release = {
            "version": "2.9.0", "channel": "stable",
            "full": descriptor("full", target),
            "deltas": [{"from_version": "2.8.0", "artifact": descriptor("delta", patch)}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "candidate.bin"
            result = stage_update(
                release,
                "2.8.0",
                destination,
                lambda url: {"delta": patch, "full": target}[url],
                lambda payload: apply_delta_bundle(base, payload),
            )
            self.assertEqual("delta", result["artifact_kind"])
            self.assertEqual(target, destination.read_bytes())

    def test_mw29_signature_001_metadata_authenticity_fails_closed(self):
        self.assertIsNotNone(shutil.which("ssh-keygen"), "OpenSSH signature verifier is a release prerequisite")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "signing-key"
            subprocess.run([shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            metadata = root / "metadata.json"
            _, payload = self.metadata()
            metadata.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            subprocess.run([shutil.which("ssh-keygen"), "-Y", "sign", "-f", str(key), "-n", SIGNING_NAMESPACE, str(metadata)], check=True, capture_output=True)
            allowed = root / "allowed_signers"
            public_output = subprocess.check_output(
                [shutil.which("ssh-keygen"), "-y", "-f", str(key)]
            )
            match = re.search(rb"ssh-ed25519 [A-Za-z0-9+/=]+", public_output)
            self.assertIsNotNone(match)
            public_key = match.group(0).decode("ascii")
            allowed.write_text(f"{SIGNING_IDENTITY} {public_key}\n", encoding="utf-8")
            loaded = load_signed_metadata(metadata, metadata.with_suffix(".json.sig"), allowed)
            self.assertEqual(payload, loaded)
            metadata.write_text(metadata.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_signed_metadata(metadata, metadata.with_suffix(".json.sig"), allowed)


if __name__ == "__main__":
    unittest.main()
