from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

from scripts.windows_installer_broker import (
    AllowlistedUacBroker,
    BrokerError,
    BrokerExit,
    NonceLedger,
    launch,
    classify_elevation_failure,
)


class WindowsInstallerBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manifests = self.root / "manifests"
        self.controllers = self.root / "controllers"
        self.manifests.mkdir()
        self.controllers.mkdir()
        self.manifest = self.manifests / "request.json"
        self.controller = self.controllers / "controller.py"
        self.manifest.write_text('{"schema_version":1}\n', encoding="utf-8")
        self.controller.write_text("# controller\n", encoding="utf-8")
        self.transaction_id = str(uuid.uuid4())
        self.target_sid = "S-1-5-21-100-200-300-1001"
        self.ledger = NonceLedger(self.root / "nonces")
        self.dispatched = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def payload(self) -> dict[str, str]:
        nonce = self.ledger.issue(self.transaction_id, self.target_sid)
        return {
            "transaction_id": self.transaction_id,
            "operation": "install",
            "target_sid": self.target_sid,
            "manifest_path": str(self.manifest),
            "manifest_sha256": self.digest(self.manifest),
            "controller_path": str(self.controller),
            "controller_sha256": self.digest(self.controller),
            "nonce": nonce,
        }

    def broker(self) -> AllowlistedUacBroker:
        return AllowlistedUacBroker(
            manifest_roots=[self.manifests],
            controller_roots=[self.controllers],
            nonce_ledger=self.ledger,
            dispatcher=lambda request: self.dispatched.append(request) or 0,
        )

    def assert_exit(self, expected: BrokerExit, payload: dict[str, str]) -> None:
        with self.assertRaises(BrokerError) as raised:
            self.broker().dispatch(payload)
        self.assertEqual(raised.exception.exit_code, expected)

    def test_allowlisted_request_dispatches_once_and_nonce_cannot_replay(self) -> None:
        payload = self.payload()
        self.assertEqual(self.broker().dispatch(payload), BrokerExit.SUCCESS)
        self.assertEqual(len(self.dispatched), 1)
        self.assert_exit(BrokerExit.NONCE_REJECTED, payload)

    def test_unknown_field_and_unknown_operation_fail_closed(self) -> None:
        payload = self.payload()
        payload["command"] = "powershell.exe"
        self.assert_exit(BrokerExit.INVALID_REQUEST, payload)
        payload = self.payload()
        payload["operation"] = "run-command"
        self.assert_exit(BrokerExit.INVALID_REQUEST, payload)

    def test_hash_drift_does_not_consume_nonce(self) -> None:
        payload = self.payload()
        payload["manifest_sha256"] = "0" * 64
        self.assert_exit(BrokerExit.HASH_MISMATCH, payload)
        payload["manifest_sha256"] = self.digest(self.manifest)
        self.assertEqual(self.broker().dispatch(payload), BrokerExit.SUCCESS)

    def test_path_escape_and_sid_mismatch_are_distinct(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        payload = self.payload()
        payload["manifest_path"] = str(outside)
        payload["manifest_sha256"] = self.digest(outside)
        self.assert_exit(BrokerExit.PATH_ESCAPE, payload)

        payload = self.payload()
        payload["target_sid"] = "S-1-5-21-100-200-300-2002"
        self.assert_exit(BrokerExit.SID_MISMATCH, payload)

    def test_expired_nonce_is_rejected(self) -> None:
        ledger = NonceLedger(self.root / "short-nonces", lifetime=timedelta(seconds=1))
        issued = datetime(2026, 8, 22, tzinfo=timezone.utc)
        nonce = ledger.issue(self.transaction_id, self.target_sid, at=issued)
        with self.assertRaises(BrokerError) as raised:
            ledger.consume(nonce, self.transaction_id, self.target_sid, at=issued + timedelta(seconds=2))
        self.assertEqual(raised.exception.exit_code, BrokerExit.NONCE_REJECTED)

    def test_cancel_and_denial_have_stable_exit_codes(self) -> None:
        cancelled = OSError("cancelled")
        cancelled.winerror = 1223
        denied = OSError("denied")
        denied.winerror = 5
        self.assertEqual(classify_elevation_failure(cancelled), BrokerExit.ELEVATION_CANCELLED)
        self.assertEqual(classify_elevation_failure(denied), BrokerExit.ELEVATION_DENIED)
        self.assertEqual(classify_elevation_failure(740), BrokerExit.ELEVATION_DENIED)

    def test_controller_exit_is_not_collapsed(self) -> None:
        payload = self.payload()
        broker = AllowlistedUacBroker(
            manifest_roots=[self.manifests],
            controller_roots=[self.controllers],
            nonce_ledger=self.ledger,
            dispatcher=lambda _request: 35,
        )
        self.assertEqual(broker.dispatch(payload), 35)

    def test_launch_hash_binds_request_before_elevation(self) -> None:
        manifest = self.manifests / "install.json"
        manifest.write_text('{"operation":"install"}\n', encoding="utf-8")
        request = self.root / "request.json"
        observed: list[str] = []

        def elevate(_executable, arguments):
            observed.extend(str(item) for item in arguments)
            return 32

        with mock.patch("scripts.windows_installer_broker.current_user_sid", return_value=self.target_sid):
            self.assertEqual(
                launch(manifest, self.controller, request, self.root / "nonces", elevater=elevate),
                32,
            )
        self.assertIn("--dispatch-request", observed)
        self.assertIn("--request-sha256", observed)
        digest = observed[observed.index("--request-sha256") + 1]
        self.assertEqual(digest, hashlib.sha256(request.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
