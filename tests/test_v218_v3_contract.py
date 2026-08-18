import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "019fb8f2-9a67-7b03-9474-6f92cd6b21a7"


def rollout_lines(message_count: int) -> str:
    lines = [
        {
            "timestamp": "2026-08-18T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": SESSION_ID, "source": "user"},
        }
    ]
    for index in range(message_count):
        speaker = "user_message" if index % 2 == 0 else "agent_message"
        payload = {
            "type": speaker,
            "message": f"第{index}条 円¥ emoji🙂 leading-value-{index}",
        }
        if speaker == "agent_message":
            payload["phase"] = "final_answer"
        lines.append(
            {
                "timestamp": f"2026-08-18T00:{index // 60:02}:{index % 60:02}Z",
                "type": "event_msg",
                "payload": payload,
            }
        )
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)


def run_collector(executable: Path, sessions: Path, archive: Path, rollout: Path) -> str:
    archive.mkdir(parents=True)
    config = archive / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")
    state = {
        "format_version": 1,
        "total_messages": 0,
        "completed_rounds": 0,
        "last_summarized_round": 0,
        "last_summarized_rounds": {},
        "last_raw_message_id": None,
        "pending_round": None,
        "pending_rounds": {},
        "next_round_number": 1,
        "completed_rounds_out_of_order": [],
        "next_job_id": 1,
        "next_summary_ids": {"1": 1, "2": 1, "3": 1, "4": 1},
        "last_successful_memory_update": None,
    }
    (archive / "state.json").write_text(
        json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = subprocess.run(
        [
            str(executable),
            "--archive-root",
            str(archive),
            "--config",
            str(config),
            "--sessions-root",
            str(sessions),
            "--once",
            "--session-file",
            str(rollout),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise AssertionError(
            f"collector failed with {result.returncode}:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    json.loads(result.stdout)
    return result.stdout


class V218V3ContractTests(unittest.TestCase):
    def test_declared_internal_refactor_keeps_public_entrypoints(self):
        source = (ROOT / "native-collector/src/lib.rs").read_text(encoding="utf-8")
        production = source.split("#[cfg(test)]", 1)[0]
        self.assertIn("fn sync_batch(&self, paths: Vec<PathBuf>) -> Result<Value>", source)
        self.assertIn("fn sync_startup_batch(&self, paths: Vec<PathBuf>) -> Result<Value>", source)
        self.assertEqual(source.count("fn run_event_loop("), 2)
        self.assertEqual(production.count("fn process_rollout_cycle("), 1)
        self.assertEqual(production.count("process_rollout_cycle("), 3)
        macos_loop, non_macos_loop = production.split("fn run_event_loop(")[1:]
        self.assertIn("KqueueWatcher", macos_loop)
        self.assertIn("watcher.wait(interval)?", macos_loop)
        self.assertIn("KqueueWatcher::new", macos_loop)
        self.assertIn("PreparedWatcher", non_macos_loop)
        self.assertIn("receiver.recv_timeout(interval)", non_macos_loop)
        self.assertIn("event_rollouts", non_macos_loop)

    @unittest.skipUnless(
        os.environ.get("MEMORY_WUXIAN_V3_BASELINE_EXE")
        and os.environ.get("MEMORY_WUXIAN_V3_CANDIDATE_EXE"),
        "V3 binary parity executables are not configured",
    )
    def test_baseline_and_candidate_emit_identical_json_bytes(self):
        baseline = Path(os.environ["MEMORY_WUXIAN_V3_BASELINE_EXE"])
        candidate = Path(os.environ["MEMORY_WUXIAN_V3_CANDIDATE_EXE"])
        with tempfile.TemporaryDirectory(prefix="memory-v3-parity-") as temporary:
            root = Path(temporary)
            sessions = root / "会話 円¥ emoji🙂 sessions"
            sessions.mkdir()
            rollout = sessions / f"rollout-2026-08-18T00-00-00-{SESSION_ID}.jsonl"
            for case, message_count in (("single", 2), ("bounded", 521)):
                with self.subTest(case=case):
                    rollout.write_text(rollout_lines(message_count), encoding="utf-8")
                    baseline_stdout = run_collector(
                        baseline, sessions, root / f"baseline-{case}", rollout
                    )
                    candidate_stdout = run_collector(
                        candidate, sessions, root / f"candidate-{case}", rollout
                    )
                    self.assertEqual(candidate_stdout, baseline_stdout)


if __name__ == "__main__":
    unittest.main()
