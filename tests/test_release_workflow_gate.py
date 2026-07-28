from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def job_block(source: str, job_name: str) -> str:
    pattern = rf"(?ms)^  {re.escape(job_name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)"
    match = re.search(pattern, source)
    if match is None:
        raise AssertionError(f"missing workflow job: {job_name}")
    return match.group(1)


class ReleaseWorkflowGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    def test_platform_gate_covers_python_and_rust_on_all_three_platforms(self) -> None:
        block = job_block(self.source, "platform-tests")
        self.assertIn(
            "os: [ubuntu-latest, macos-latest, windows-latest]",
            block,
        )
        for command in (
            "cargo fmt --check",
            "cargo check --locked --all-targets",
            "cargo test --locked",
            "cargo build --locked --bins",
            "python -m unittest discover -s tests -v",
        ):
            self.assertIn(command, block)

    def test_release_gate_requires_platform_tests_and_documentation(self) -> None:
        block = job_block(self.source, "release-gate")
        self.assertRegex(
            block,
            r"needs:\s*\[platform-tests,\s*documentation\]",
        )
        self.assertIn('needs.platform-tests.result }}" = "success"', block)
        self.assertIn('needs.documentation.result }}" = "success"', block)

    def test_installers_and_publish_require_release_gate(self) -> None:
        for installer in ("macos-installer", "windows-installer"):
            self.assertRegex(
                job_block(self.source, installer),
                r"(?m)^\s{4}needs:\s*release-gate\s*$",
            )
        publish = job_block(self.source, "publish")
        self.assertRegex(
            publish,
            r"needs:\s*\[release-gate,\s*macos-installer,\s*windows-installer\]",
        )

    def test_every_source_checkout_is_pinned_to_event_commit(self) -> None:
        checkout_count = self.source.count("uses: actions/checkout@v7")
        pinned_count = self.source.count("ref: ${{ github.sha }}")
        self.assertGreaterEqual(checkout_count, 4)
        self.assertEqual(pinned_count, checkout_count)

    def test_only_tag_push_can_publish_and_version_checks_remain(self) -> None:
        publish = job_block(self.source, "publish")
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", publish)
        self.assertIn("workflow_dispatch:", self.source)
        self.assertIn("pull_request:", self.source)
        self.assertGreaterEqual(
            self.source.count("does not match project version"),
            2,
        )

    def test_installer_checksums_are_retained(self) -> None:
        self.assertIn("Get-FileHash", job_block(self.source, "windows-installer"))
        self.assertIn("sha256", job_block(self.source, "windows-installer"))
        self.assertIn("dist/*", job_block(self.source, "publish"))


if __name__ == "__main__":
    unittest.main()
