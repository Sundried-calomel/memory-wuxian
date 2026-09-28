"""Create a device-local core live config and run one explicit core-v1 sync.

No legacy cloud-sync script is imported or launched. All paths and private-key
references come from the local invocation/configuration and stay on-device.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from archive import ArchiveStore
from core_sync import CoreSyncService
from native_bridge import NativeArchiveBridge
from storage import atomic_write_json


_NODE = re.compile(r"[a-z0-9][a-z0-9-]{2,63}\Z")
EXCHANGE_DIRECTORY = "MemoryWuxianExchange"


def _read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("selected local federation configuration is missing or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("selected local federation configuration is malformed")
    return value


def _exchange_path(selected: str) -> Path:
    path = Path(selected).expanduser().absolute()
    if path.name.casefold() != EXCHANGE_DIRECTORY.casefold():
        path = path / EXCHANGE_DIRECTORY
    if not path.is_dir() or path.is_symlink():
        raise ValueError("selected OneDrive exchange directory is not available locally")
    return path


def build_live_config(*, new_root, native_source, old_archive_root, exchange_root,
                      identity, binary, peer_id, environment_root) -> dict:
    old_root = Path(old_archive_root).expanduser().absolute()
    federation = old_root / "federation"
    node = _read_json(federation / "node.json")
    cloud = _read_json(federation / "cloud.json")
    if node.get("format_version") != 1 or cloud.get("format_version") != 1:
        raise ValueError("unsupported local federation configuration version")
    local_id = node.get("node_id")
    if not isinstance(local_id, str) or not _NODE.fullmatch(local_id):
        raise ValueError("local federation node identity is malformed")
    if not isinstance(peer_id, str) or not _NODE.fullmatch(peer_id) or peer_id == local_id:
        raise ValueError("a distinct explicit peer node ID is required")
    peer = _read_json(federation / "peers" / f"{peer_id}.json")
    identity_data = peer.get("cloud_identity")
    if (peer.get("format_version") != 1 or peer.get("node_id") != peer_id
            or peer.get("trusted") is not True or not isinstance(identity_data, dict)):
        raise ValueError("selected peer is not a trusted supported federation peer")
    encryption_key = identity_data.get("encryption_public_key")
    signing_key = identity_data.get("signing_public_key")
    if not isinstance(encryption_key, str) or not encryption_key or not isinstance(signing_key, str) or not signing_key:
        raise ValueError("selected peer is missing its encryption or signing public key")

    archive_root = Path(new_root).expanduser().absolute()
    source_root = Path(native_source).expanduser().absolute()
    private_identity = Path(identity).expanduser().absolute()
    envelope_binary = Path(binary).expanduser().absolute()
    if not source_root.is_dir() or source_root.is_symlink():
        raise ValueError("explicit native source directory is unavailable")
    if not private_identity.is_file() or private_identity.is_symlink():
        raise ValueError("selected local native identity is unavailable")
    if not envelope_binary.is_file() or envelope_binary.is_symlink():
        raise ValueError("selected local envelope helper is unavailable")
    if archive_root.resolve().is_relative_to(source_root.resolve()) or source_root.resolve().is_relative_to(archive_root.resolve()):
        raise ValueError("new archive and native source must be separate")

    exchange = _exchange_path(str(exchange_root))
    codex_root = Path(environment_root).expanduser().absolute()
    skill_root = codex_root / "skills" / "memory-wuxian"
    # Legacy `transport.type=offline` is not reused as a new-core transport.
    # The selected OneDrive directory and the paired native identities form a
    # new, explicit core-v1 route; old cloud-sync scripts remain unused.
    return {
        "root": str(archive_root),
        "source": str(source_root),
        "sessions_root": str(codex_root / "sessions"),
        "auto_summary": False,
        "sync": {
            "exchange_root": str(exchange),
            "binary": str(envelope_binary),
            "identity": str(private_identity),
            "local_node_id": local_id,
            "peer_id": peer_id,
            "peer_encryption_public_key": encryption_key,
            "peer_signing_public_key": signing_key,
        },
        "environment": {
            "root": str(codex_root),
            "agents_file": str(codex_root / "AGENTS.md"),
            "core_directory": str(skill_root / "core"),
            "skill_entry": str(skill_root / "SKILL.md"),
            "outbound_selection_ids": ["global-codex-agents", "memory-wuxian-core"],
            "receive_limit": 32,
        },
    }


def configure_and_sync(args) -> dict:
    config = build_live_config(new_root=args.new_root, native_source=args.native_source,
        old_archive_root=args.old_archive_root, exchange_root=args.exchange_root,
        identity=args.identity, binary=args.binary, peer_id=args.peer_id,
        environment_root=args.environment_root)
    config_path = Path(args.live_config).expanduser().absolute()
    atomic_write_json(config_path, config)
    if os.name != "nt":
        config_path.chmod(0o600)
    result = CoreSyncService(ArchiveStore(config["root"]), **config["sync"]).sync_once()
    return {"status": result.get("status", "ok"),
            "sent_batches": result.get("sent_batches", 0),
            "received_batches": result.get("received_batches", 0),
            "acknowledged_batches": result.get("acknowledged_batches", 0),
            "live_config_written": True,
            "auto_summary": False}


def import_native_once(new_root, source) -> dict:
    """Explicit one-time raw import; never invoked by configure or scheduled ticks."""
    result = NativeArchiveBridge(new_root, source).run_once()
    return {key: result[key] for key in (
        "appended", "duplicates", "variants", "excluded", "files_processed",
        "files_unchanged", "pending_file")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("configure-and-sync", help="write local core live config and perform one core-v1 sync")
    setup.add_argument("--new-root", required=True)
    setup.add_argument("--native-source", required=True)
    setup.add_argument("--old-archive-root", required=True)
    setup.add_argument("--exchange-root", required=True,
                       help="selected OneDrive root or its MemoryWuxianExchange directory")
    setup.add_argument("--identity", required=True, help="local native identity file; never packaged")
    setup.add_argument("--binary", required=True, help="local native envelope helper; never packaged")
    setup.add_argument("--peer-id", required=True)
    setup.add_argument("--live-config", required=True, help="device-local live config output")
    setup.add_argument("--environment-root", required=True,
                       help="device-local Codex environment root; never packaged")
    importer = commands.add_parser("import-native", help="explicit one-time native raw import; no model call")
    importer.add_argument("--new-root", required=True)
    importer.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    try:
        result = configure_and_sync(args) if args.command == "configure-and-sync" \
            else import_native_once(args.new_root, args.source)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        # Do not echo configuration values, local paths, keys or helper output.
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
