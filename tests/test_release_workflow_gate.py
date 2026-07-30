from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TEST_WORKFLOW = ROOT / ".github" / "workflows" / "test.yml"
PYPROJECT = ROOT / "pyproject.toml"
WINDOWS_BOOTSTRAP = ROOT / "scripts" / "bootstrap_windows.ps1"
MACOS_POSTINSTALL = ROOT / "packaging" / "macos" / "scripts" / "postinstall"


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
        cls.test_source = TEST_WORKFLOW.read_text(encoding="utf-8")

    def test_stable_ci_pins_the_supported_windows_runner(self) -> None:
        self.assertIn('python-version: "3.14"', self.test_source)
        self.assertNotIn("windows-latest", self.test_source)
        self.assertEqual(self.test_source.count("runs-on: windows-2022"), 1)
        self.assertIn("runs-on: ubuntu-latest", self.test_source)
        self.assertIn("runs-on: macos-latest", self.test_source)

    def test_ci_does_not_repeat_the_suite_across_unsupported_python_versions(self) -> None:
        self.assertNotIn("python-compatibility:", self.test_source)
        for version in ("3.9", "3.10", "3.11", "3.12", "3.13"):
            self.assertNotIn(f'python-version: "{version}"', self.test_source)

    def test_main_runtime_contract_is_python_314_only(self) -> None:
        self.assertIn(
            'requires-python = ">=3.14,<3.15"',
            PYPROJECT.read_text(encoding="utf-8"),
        )
        bootstrap = WINDOWS_BOOTSTRAP.read_text(encoding="utf-8")
        self.assertIn('$MinimumPython = [version]"3.14"', bootstrap)
        self.assertIn('$MaximumPython = [version]"3.15"', bootstrap)
        self.assertIn("Python.Python.3.14", bootstrap)
        self.assertNotIn("Python.Python.3.13", bootstrap)
        postinstall = MACOS_POSTINSTALL.read_text(encoding="utf-8")
        self.assertIn("(3, 14) <= sys.version_info < (3, 15)", postinstall)

    def test_ci_eliminates_duplicate_triggers_and_serial_windows_shards(self) -> None:
        self.assertIn("branches: [main]", self.test_source)
        self.assertIn("pull_request:", self.test_source)
        self.assertIn("cancel-in-progress: true", self.test_source)
        self.assertIn("windows-candidate:", self.test_source)
        self.assertNotIn("windows-python:", self.test_source)
        self.assertNotIn("windows-rehearsal:", self.test_source)
        self.assertNotIn("max-parallel:", self.test_source)
        self.assertNotIn("scenario-shard-count", self.test_source)

    def test_candidate_jobs_reuse_full_unittest_evidence(self) -> None:
        self.assertEqual(
            self.test_source.count("python -m unittest discover -s tests -v"),
            3,
        )
        self.assertEqual(
            self.test_source.count("--reuse-unittest-evidence"),
            3,
        )
        self.assertIn("--exclude-baseline", self.test_source)
        self.assertIn("if: github.event_name == 'pull_request'", self.test_source)
        self.assertIn("if: github.event_name == 'push'", self.test_source)

    def test_release_is_manual_and_serialized(self) -> None:
        self.assertIn("workflow_dispatch:", self.source)
        self.assertNotIn("pull_request:", self.source)
        self.assertNotIn("tags:", self.source)
        self.assertIn("group: memory-wuxian-release", self.source)
        self.assertIn("cancel-in-progress: false", self.source)

    def test_candidate_proof_requires_successful_push_ci_for_same_sha(self) -> None:
        block = job_block(self.source, "candidate-proof")
        self.assertIn('workflow_id: "test.yml"', block)
        self.assertIn("head_sha: context.sha", block)
        self.assertIn('run.event === "push"', block)
        self.assertIn('run.conclusion === "success"', block)
        self.assertIn("has no successful push run of test.yml", block)

    def test_installer_workflow_does_not_repeat_candidate_suites(self) -> None:
        self.assertNotIn("unittest discover", self.source)
        self.assertNotIn("run_release_rehearsal.py", self.source)

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
        self.assertIn("Release tag already exists", metadata)
        self.assertIn("tag_name: ${{ needs.metadata.outputs.tag }}", publish)
        self.assertIn("target_commitish: ${{ github.sha }}", publish)
        self.assertNotIn("startsWith(github.ref", publish)

    def test_beta_versions_are_published_only_as_prereleases(self) -> None:
        metadata = job_block(self.source, "metadata")
        publish = job_block(self.source, "publish")
        self.assertIn('^([0-9]+\\.[0-9]+\\.[0-9]+)b([0-9]+)$', metadata)
        self.assertIn('-beta.${BASH_REMATCH[2]}', metadata)
        self.assertIn('prerelease="true"', metadata)
        self.assertIn(
            "prerelease: ${{ needs.metadata.outputs.prerelease }}",
            publish,
        )

    def test_installer_checksums_are_retained(self) -> None:
        self.assertIn("Get-FileHash", job_block(self.source, "windows-installer"))
        self.assertIn("sha256", job_block(self.source, "windows-installer"))
        self.assertIn("dist/*", job_block(self.source, "publish"))

    def test_release_rebuilds_native_binaries_before_each_installer(self) -> None:
        mac = job_block(self.source, "macos-installer")
        windows = job_block(self.source, "windows-installer")
        self.assertIn("cargo build --release --locked --bins", mac)
        self.assertIn("lipo -create", mac)
        self.assertIn("cargo build --release --locked --bins", windows)
        self.assertIn(
            "Copy-Item native-collector/target/release/memory-wuxian-collector.exe",
            windows,
        )
        self.assertIn("Unexpected native version", mac)
        self.assertIn("Unexpected native version", windows)
        self.assertIn('(& "bin\\$binary.exe" --version).Trim()', windows)

    def test_windows_installer_proves_all_native_executables_are_packaged(self) -> None:
        windows = job_block(self.source, "windows-installer")
        for executable in (
            "bin\\memory-wuxian-collector.exe",
            "bin\\memory-wuxian-envelope.exe",
            "bin\\memory-wuxian-dashboard-launcher.exe",
        ):
            self.assertIn(executable, windows)

    def test_both_installers_retain_the_architecture_hard_gate(self) -> None:
        required = [
            "SKILL.md",
            "AGENTS.md",
            "PRODUCT_ARCHITECTURE.md",
            "module-architecture.json",
            "check_architecture_contract.py",
        ]
        for installer in ("macos-installer", "windows-installer"):
            block = job_block(self.source, installer)
            for filename in required:
                self.assertIn(filename, block)


if __name__ == "__main__":
    unittest.main()
