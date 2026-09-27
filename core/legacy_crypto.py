"""Open legacy cloud envelopes with the existing trusted native helper.

The caller supplies the trusted executable and the locally selected peer key;
neither is taken from the envelope. Authentication is the helper's age decrypt
and Ed25519 verification, not the payload hash or a caller-provided trust flag.
This module does not discover identities, install binaries, or publish plaintext.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile


_KINDS = {"bundle", "ack", "environment-v1-bundle", "environment-v1-ack",
          "project-evidence-v1-bundle", "project-evidence-v1-ack",
          "project-attachment-v1-bundle", "project-attachment-v1-ack"}


def open_legacy_envelope(source, *, binary, identity, signing_public_key,
                         origin, target, kind, timeout=600) -> bytes:
    """Return authenticated plaintext; any helper/binding failure raises.

    ``binary`` and ``identity`` must be explicit absolute local paths. The
    existing memory-wuxian-envelope executable remains a runtime dependency.
    ``origin``, ``target`` and the signing key come from trusted local selection,
    not unverified network metadata. Plaintext exists only in a temporary file
    until verification completes, then is returned for the caller's importer.
    No public runner/trusted-result injection is provided.
    """
    executable, private_identity = Path(binary), Path(identity)
    if not executable.is_absolute() or not private_identity.is_absolute():
        raise ValueError("explicit absolute binary and identity paths required")
    if os.name == "nt" and executable.suffix.lower() != ".exe":
        executable = Path(str(executable) + ".exe")
    for value in (origin, target):
        if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", value):
            raise ValueError("invalid expected node ID")
    if kind not in _KINDS:
        raise ValueError("unsupported legacy envelope kind")
    if not isinstance(signing_public_key, str) or not signing_public_key.strip():
        raise ValueError("explicit trusted peer signing public key required")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("positive helper timeout required")
    with tempfile.TemporaryDirectory(prefix="memory-legacy-open-") as temporary:
        output = Path(temporary) / "plaintext"
        arguments = [str(executable), "open", "--identity", str(private_identity),
                     "--signing-public-key=" + signing_public_key,
                     "--input", str(Path(source).absolute()), "--output", str(output),
                     "--expected-kind", kind, "--expected-origin-node-id", origin,
                     "--expected-target-node-id", target]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        completed = subprocess.run(arguments, text=True, encoding="utf-8",
                                   capture_output=True, check=False, timeout=timeout,
                                   shell=False, **options)
        if completed.returncode != 0:
            raise ValueError("legacy envelope decrypt/signature verification failed "
                             f"(helper exit {completed.returncode})")
        try:
            metadata = json.loads(completed.stdout)
        except (ValueError, TypeError) as error:
            raise ValueError("legacy envelope helper returned invalid metadata") from error
        expected = {"format": "memory-wuxian-envelope-v1", "kind": kind,
                    "origin_node_id": origin, "target_node_id": target,
                    "output": str(output)}
        if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in expected.items()):
            raise ValueError("legacy envelope helper identity/kind/output binding mismatch")
        if not output.exists() or not stat.S_ISREG(output.lstat().st_mode):
            raise ValueError("legacy envelope helper did not produce a regular plaintext file")
        if output.stat().st_size > 128 * 1024 * 1024:
            raise ValueError("legacy envelope plaintext exceeds migration limit")
        payload = output.read_bytes()
        if (type(metadata.get("payload_length")) is not int
                or metadata["payload_length"] != len(payload)
                or metadata.get("payload_sha256") != hashlib.sha256(payload).hexdigest()):
            raise ValueError("legacy envelope helper plaintext binding mismatch")
        return payload
