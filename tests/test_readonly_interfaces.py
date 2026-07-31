from __future__ import annotations

import hashlib
import json
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from memory_cli import MemoryStore, load_simple_yaml
import memory_cli
from memory_readonly_http import create_server
from memory_readonly_mcp import dispatch
from memory_readonly_service import MAX_SERIALIZED_BYTES, ReadOnlyMemoryService, ReadRequestError
from memory_guarded_features import raw_record_sha256


class ReadOnlyInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        config = base / "config.yaml"
        config.write_text("memory:\n  root_directory: ./memory\nsummaries:\n  maximum_summary_depth: 4\n", encoding="utf-8")
        self.store = MemoryStore(base / "memory", load_simple_yaml(config))
        self.config = config
        self.store.init()
        self.store.append_message("user", "后台刷新导致输入窗口失去焦点", "2026-08-01T00:00:00+09:00", "codex:test", "message-1", None, False)
        self.service = ReadOnlyMemoryService(self.store)

    def raw_hash(self):
        digest = hashlib.sha256()
        for path in sorted((self.store.root / "raw").rglob("*")):
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_mw29_parity_001_cli_http_and_mcp_share_payload(self):
        before = self.raw_hash()
        expected = self.service.query({"query": "窗口失去焦点", "mode": "keyword", "limit": 5})
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/v1/memory/query?q={quote('窗口失去焦点')}&mode=keyword&limit=5", timeout=5) as response:
                http_payload = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        mcp = dispatch(self.service, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "memory.query", "arguments": {"query": "窗口失去焦点", "mode": "keyword", "limit": 5}}})
        self.assertEqual(expected, http_payload)
        self.assertEqual(expected, mcp["result"]["structuredContent"])
        self.assertEqual("verified", expected["confidence"])
        self.assertTrue(expected["results"][0]["provenance"]["verified_against_raw"])
        self.assertEqual(before, self.raw_hash())

        cli = subprocess.run(
            [sys.executable, str(ROOT / "scripts/memory_cli.py"), "--root", str(self.store.root), "--config", str(self.config), "readonly-query", "--query", "窗口失去焦点", "--mode", "keyword", "--limit", "5"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        self.assertEqual(expected, json.loads(cli.stdout))

    def test_mw29_malformed_001_all_surfaces_fail_closed(self):
        with self.assertRaises(ReadRequestError) as caught:
            self.service.query({"query": "x" * 501})
        self.assertEqual("over-broad-query", caught.exception.code)
        mcp = dispatch(self.service, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "memory.query", "arguments": {"query": "x", "limit": 51}}})
        self.assertEqual(-32602, mcp["error"]["code"])
        self.assertNotIn("write", json.dumps(dispatch(self.service, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})))
        cli = subprocess.run(
            [sys.executable, str(ROOT / "scripts/memory_cli.py"), "--root", str(self.store.root), "--config", str(self.config), "readonly-query", "--query", "x", "--limit", "51"],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(1, cli.returncode)
        self.assertEqual("over-broad-query", json.loads(cli.stderr)["error"]["code"])
        self.assertEqual("over-broad-query", mcp["error"]["data"]["error"]["code"])

        malformed_cases = (
            ([], "malformed-request"),
            (["--query", "x", "--mode", "invalid"], "malformed-request"),
            (["--query", "x", "--limit", "not-an-integer"], "malformed-request"),
            (["--query", "x", "--unknown", "value"], "malformed-request"),
        )
        for arguments, expected_code in malformed_cases:
            cli = subprocess.run(
                [sys.executable, str(ROOT / "scripts/memory_cli.py"), "--root", str(self.store.root), "--config", str(self.config), "readonly-query", *arguments],
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
            self.assertEqual(1, cli.returncode)
            self.assertEqual(expected_code, json.loads(cli.stderr)["error"]["code"])

    def test_mw29_stale_001_hybrid_falls_back_but_semantic_fails(self):
        hybrid = self.service.query({"query": "窗口", "mode": "hybrid", "limit": 5})
        self.assertIn("semantic-index-unavailable-keyword-fallback", hybrid["warnings"])
        self.assertEqual("verified", hybrid["confidence"])
        with self.assertRaises(ReadRequestError) as caught:
            self.service.query({"query": "窗口", "mode": "semantic", "limit": 5})
        self.assertEqual("index-unavailable", caught.exception.code)

    def test_mw29_stale_002_corrupt_semantic_index_is_normalized(self):
        from memory_guarded_features import GuardedFeatures
        GuardedFeatures(self.store).semantic_build("local-hash-v1")
        vectors = self.store.index_dir / "semantic" / "vectors.jsonl"
        vectors.write_text("{\n", encoding="utf-8")
        with self.assertRaises(ReadRequestError) as caught:
            self.service.query({"query": "anything", "mode": "semantic", "limit": 5})
        self.assertEqual("index-unavailable", caught.exception.code)

    def test_mw29_provenance_001_tamper_is_not_verified(self):
        raw_path = next((self.store.root / "raw").rglob("*.md"))
        lines = raw_path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[lines.index("```json") + 1])
        record["text"] = "窗口已经篡改"
        record["content_sha256"] = raw_record_sha256(record)
        lines[lines.index("```json") + 1] = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = self.service.query({"query": "窗口已经篡改", "mode": "keyword", "limit": 5})
        self.assertEqual("unverified", result["confidence"])
        self.assertFalse(result["results"][0]["provenance"]["verified_against_raw"])

    def test_mw29_bound_001_truncates_text_and_mcp_frames(self):
        self.store.append_message("user", "z" * 5000 + " needle", "2026-08-01T00:01:00+09:00", "codex:large", "message-large", None, False)
        result = self.service.query({"query": "needle", "mode": "keyword", "limit": 5})
        self.assertTrue(result["results"][0]["text_truncated"])
        self.assertLessEqual(len(result["results"][0]["text"]), 4000)
        self.assertEqual(MAX_SERIALIZED_BYTES, result["response_byte_limit"])
        self.assertLessEqual(
            len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            MAX_SERIALIZED_BYTES,
        )
        from memory_readonly_mcp import serve_lines
        output = io.StringIO()
        serve_lines(self.service, io.StringIO("x" * 70000 + "\n"), output)
        self.assertEqual("frame too large", json.loads(output.getvalue())["error"]["message"])
        oversized_id = dispatch(self.service, {"jsonrpc": "2.0", "id": "x" * 129, "method": "tools/list", "params": {}})
        self.assertEqual("invalid request id", oversized_id["error"]["message"])

    def test_mw29_mcp_001_complete_initialization_lifecycle(self):
        from memory_readonly_mcp import serve_lines
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        output = io.StringIO()
        serve_lines(
            self.service,
            io.StringIO("".join(json.dumps(item) + "\n" for item in requests)),
            output,
        )
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([1, 2], [item["id"] for item in responses])
        self.assertEqual("2025-03-26", responses[0]["result"]["protocolVersion"])
        self.assertEqual("memory.query", responses[1]["result"]["tools"][0]["name"])

    def test_mw29_source_001_malformed_raw_is_normalized(self):
        raw_path = next((self.store.root / "raw").rglob("*.md"))
        lines = raw_path.read_text(encoding="utf-8").splitlines()
        lines[lines.index("```json") + 1] = "{"
        raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(ReadRequestError) as caught:
            self.service.query({"query": "anything", "mode": "keyword", "limit": 5})
        self.assertEqual("source-invalid", caught.exception.code)

    def test_mw29_lock_001_long_lived_readers_bypass_archive_lock(self):
        with mock.patch.object(memory_cli, "exclusive_lock", side_effect=AssertionError("archive lock used")), \
             mock.patch.object(memory_cli, "dispatch_command", return_value=0) as dispatch_command:
            result = memory_cli.main(["--root", str(self.store.root), "--config", str(self.config), "readonly-http"])
        self.assertEqual(0, result)
        dispatch_command.assert_called_once()

    def test_mw29_loopback_001_rejects_remote_binding_and_post(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, self.service)
        with self.assertRaises(ValueError):
            create_server("localhost", 0, self.service)
        ipv6 = create_server("::1", 0, self.service)
        self.assertEqual("::1", ipv6.server_address[0])
        ipv6.server_close()
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from urllib.request import Request
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(f"http://127.0.0.1:{server.server_port}/v1/memory/query", method="POST"), timeout=5)
            self.assertEqual(405, caught.exception.code)
            caught.exception.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_mw29_source_002_file_enumeration_is_bounded(self):
        with mock.patch("memory_readonly_service.MAX_RAW_FILES", 0):
            with self.assertRaises(ReadRequestError) as caught:
                self.service.query({"query": "anything", "mode": "keyword", "limit": 5})
        self.assertEqual("source-too-large", caught.exception.code)

    def test_mw29_source_003_nonobject_state_has_adapter_parity(self):
        self.store.state_path.write_text("[]\n", encoding="utf-8")
        with self.assertRaises(ReadRequestError) as caught:
            self.service.query({"query": "anything", "mode": "keyword", "limit": 5})
        self.assertEqual("source-invalid", caught.exception.code)
        mcp = dispatch(self.service, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "memory.query", "arguments": {"query": "anything", "mode": "keyword", "limit": 5}}})
        self.assertEqual("source-invalid", mcp["error"]["data"]["error"]["code"])
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as http_error:
                urlopen(f"http://127.0.0.1:{server.server_port}/v1/memory/query?q=anything&mode=keyword&limit=5", timeout=5)
            payload = json.loads(http_error.exception.read().decode("utf-8"))
            http_error.exception.close()
            self.assertEqual("source-invalid", payload["error"]["code"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)
        cli = subprocess.run(
            [sys.executable, str(ROOT / "scripts/memory_cli.py"), "--root", str(self.store.root), "--config", str(self.config), "readonly-query", "--query", "anything", "--mode", "keyword", "--limit", "5"],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(1, cli.returncode)
        self.assertEqual("source-invalid", json.loads(cli.stderr)["error"]["code"])

    def test_mw29_source_004_linked_raw_root_is_rejected(self):
        linked_root = self.store.root.parent / "linked-memory"
        linked_store = MemoryStore(linked_root, load_simple_yaml(self.config))
        linked_store.init()
        external = self.store.root.parent / "external-raw"
        external.mkdir()
        linked_store.raw_dir.rmdir()
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(linked_store.raw_dir), str(external)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
        else:
            linked_store.raw_dir.symlink_to(external, target_is_directory=True)
        with self.assertRaises(ReadRequestError) as caught:
            ReadOnlyMemoryService(linked_store).query({"query": "anything", "mode": "keyword", "limit": 5})
        self.assertEqual("source-invalid", caught.exception.code)

    def test_mw29_http_001_unknown_and_duplicate_parameters_fail(self):
        server = create_server("127.0.0.1", 0, self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for suffix in ("q=x&unknown=1", "q=x&q=y"):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(f"http://127.0.0.1:{server.server_port}/v1/memory/query?{suffix}", timeout=5)
                payload = json.loads(caught.exception.read().decode("utf-8"))
                caught.exception.close()
                self.assertEqual("malformed-request", payload["error"]["code"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_mw29_missing_001_readers_do_not_initialize_archive(self):
        missing = self.store.root.parent / "missing-memory"
        service = ReadOnlyMemoryService(MemoryStore(missing, load_simple_yaml(self.config)))
        with self.assertRaises(ReadRequestError) as caught:
            service.query({"query": "anything"})
        self.assertEqual("source-unavailable", caught.exception.code)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
