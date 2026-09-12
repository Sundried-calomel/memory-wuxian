"""Check the real isolated command owner without invoking a cloud model."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor/summary_v2'
PROGRAM = '''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import summary_v2_worker as owner
config=json.loads(sys.argv[2])
values=[]
for kind in ('raw-records',owner.SOURCE_CHILDREN,owner.SOURCE_RESCUE_MAPS,owner.SOURCE_PARENT_RESCUE_MAPS):
    command,timeout,limit=owner.codex_command(config,{'source_kind':kind})
    values.append(command)
print(json.dumps(values))
'''


class SummaryV2ModelSelectionTest(unittest.TestCase):
    def commands(self, config):
        result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', PROGRAM,
                                 str(VENDOR / 'scripts'), json.dumps(config)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_empty_or_absent_model_is_terra_with_explicit_medium(self):
        for config in ({}, {'ai_summary': {'model': ''}}):
            for command in self.commands(config):
                self.assertEqual(command[command.index('--model') + 1], 'gpt-5.6-terra')
                self.assertEqual(command[command.index('-c') + 1], 'model_reasoning_effort="medium"')
                self.assertIn('--ignore-user-config', command)
                self.assertIn('--ephemeral', command)
                self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')

    def test_explicit_model_and_unicode_executable_remain_single_arguments(self):
        executable = '中文 日本語 tool/codex'
        config = {'ai_summary': {'model': 'explicit-user-model', 'codex_cli_path': executable,
                                'codex_cli_path_windows': executable}}
        for command in self.commands(config):
            self.assertEqual(command[0], str(Path(executable)))
            self.assertEqual(command[command.index('--model') + 1], 'explicit-user-model')
            self.assertEqual(command.count('--model'), 1)
            self.assertEqual(command.count('-c'), 1)


if __name__ == '__main__':
    unittest.main()
