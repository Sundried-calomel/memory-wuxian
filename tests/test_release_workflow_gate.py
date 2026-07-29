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

    def test_formal_release_is_manual_and_serialized(self) -> None:
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("pull_request:", self.source)
        self.assertNotIn("tags:", self.source)
        self.assertIn("group: memory-wuxian-formal-release", self.source)
        self.assertIn("cancel-in-progress: false", self.source)

    def test_candidate_proof_requires_successful_push_ci_for_same_sha(self) -> None:
        block = job_block(self.source, "candidate-proof")
        self.assertIn('workflow_id: "test.yml"', block)
        self.assertIn("head_sha: context.sha", block)
        self.assertIn('run.event === "push"', block)
        self.assertIn('run.conclusion === "success"', block)
        self.assertIn("has no successful push run of test.yml", block)

    def test_release_gate_requires_metadata_and_candidate_proof(self) -> None:
        block = job_block(self.source, "release-gate")
        self.assertRegex(
            block,
            r"needs:\s*\[metadata,\s*candidate-proof\]",
        )
        self.assertIn('needs.metadata.result }}" = "success"', block)
        self.assertIn('needs.candidate-proof.result }}" = "success"', block)

    def test_installers_and_publish_require_release_gate_and_metadata(self) -> None:
        for installer in ("macos-installer", "windows-installer"):
            self.assertRegex(
                job_block(self.source, installer),
                r"(?m)^\s{4}needs:\s*\[release-gate,\s*metadata\]\s*$",
            )
        publish = job_block(self.source, "publish")
        self.assertRegex(
            publish,
            r"needs:\s*\[metadata,\s*release-gate,\s*macos-installer,\s*windows-installer\]",
        )

    def test_every_source_checkout_is_pinned_to_event_commit(self) -> None:
        checkout_count = self.source.count("uses: actions/checkout@v7")
        pinned_count = self.source.count("ref: ${{ github.sha }}")
        self.assertGreaterEqual(checkout_count, 3)
        self.assertEqual(pinned_count, checkout_count)

    def test_publish_creates_single_version_tag_after_installers(self) -> None:
        publish = job_block(self.source, "publish")
        metadata = job_block(self.source, "metadata")
        self.assertIn("Formal release tag already exists", metadata)
        self.assertIn("tag_name: ${{ needs.metadata.outputs.tag }}", publish)
        self.assertIn("target_commitish: ${{ github.sha }}", publish)
        self.assertNotIn("startsWith(github.ref", publish)

    def test_installer_checksums_are_retained(self) -> None:
        self.assertIn("Get-FileHash", job_block(self.source, "windows-installer"))
        self.assertIn("sha256", job_block(self.source, "windows-installer"))
        self.assertIn("dist/*", job_block(self.source, "publish"))


if __name__ == "__main__":
    unittest.main()
