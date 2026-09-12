import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from tests import test_windows_lifecycle_transaction as lifecycle_tests
from tests.test_windows_lifecycle_transaction import FakeRunner
import install_codex_autosync_windows as windows
from platform_process import windows_command_argv, wait_windows_process_exit, wait_windows_startup_exit, windows_startup_processes


@unittest.skipUnless(os.name == 'nt', 'Windows command and process APIs')
class WindowsStartupBindingTest(unittest.TestCase):
    def setUp(self):
        self.h = lifecycle_tests.WindowsLifecycleTransactionTests()
        self.h.setUp()
        self.addCleanup(self.h.tearDown)
        self.wrapper = self.h.base / '日志 capture.py'
        self.launch = self.h.base / 'launch.json'
        self.pythonw = self.h.base / 'pythonw.exe'
        self.wrapper.write_bytes(b'# fixture wrapper, never executed')
        self.pythonw.write_bytes(b'fixture pythonw, never executed')
        sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        self.launch.write_text(json.dumps({'executable': self.h.command[0], 'arguments': subprocess.list2cmdline(self.h.command[1:]), 'sha256': sha(self.h.collector)}), encoding='utf-8')
        root = ET.fromstring(windows.task_xml([str(self.pythonw), '-B', str(self.wrapper)]))
        ns = windows.TASK_NAMESPACE
        trigger = ET.SubElement(root.find(f'{{{ns}}}Triggers'), f'{{{ns}}}TimeTrigger')
        repeat = ET.SubElement(trigger, f'{{{ns}}}Repetition')
        ET.SubElement(repeat, f'{{{ns}}}Interval').text = 'PT5M'
        self.xml = ET.tostring(root, encoding='utf-16', xml_declaration=True)
        self.binding = {'task_xml': base64.b64encode(self.xml).decode('ascii'), 'task_xml_sha256': hashlib.sha256(self.xml).hexdigest(),
            'wrapper': {'path': str(self.wrapper), 'sha256': sha(self.wrapper)}, 'launch': {'path': str(self.launch), 'sha256': sha(self.launch)},
            'pythonw': {'path': str(self.pythonw), 'sha256': sha(self.pythonw)}, 'collector_sha256': sha(self.h.collector), 'collector_pid': 99999999}
        telemetry = self.h.archive / 'imports/codex/collector-telemetry.json'
        telemetry.parent.mkdir(parents=True)
        telemetry.write_text(json.dumps({'pid': 99999999}), encoding='utf-8')

    def invoke(self, runner, **kwargs):
        return windows.install_transaction(task_name=windows.DEFAULT_TASK_NAME, command=self.h.command, archive_root=self.h.archive,
            command_manifest=self.h.manifest, pointer=self.h.pointer, journal_path=self.h.journal, runner=runner,
            startup_binding=self.binding, quiescence_probe=lambda *_: None, readiness_probe=self.h.ready_probe, **kwargs)

    def test_task_policy_and_wrapper_survive_transaction_with_native_lifecycle(self):
        runner = FakeRunner(self.xml)
        order = []
        def stopped(binding, command):
            self.assertIsNone(runner.task_xml)
            self.assertEqual(self.binding, binding)
            self.assertEqual(self.h.command, command)
            order.append('stopped')
        result = windows.install_transaction(task_name=windows.DEFAULT_TASK_NAME, command=self.h.command, archive_root=self.h.archive,
            command_manifest=self.h.manifest, pointer=self.h.pointer, journal_path=self.h.journal, runner=runner,
            startup_binding=self.binding, quiescence_probe=stopped, readiness_probe=self.h.ready_probe,
            prepare_mutation=lambda _: order.append('switched'))
        self.assertEqual(['stopped', 'switched'], order)
        self.assertEqual('commit', result['phase'])
        self.assertEqual(windows._xml_structure(self.xml), windows._xml_structure(runner.task_xml))
        self.assertEqual(['PT5M'], result['verification']['scheduled_task']['repeat_intervals'])
        self.assertEqual('PT1M', result['verification']['scheduled_task']['restart_interval'])
        self.assertEqual(self.h.command, json.loads(self.h.manifest.read_text('utf-8'))['command'])

    def test_unknown_wrapper_cannot_be_silently_replaced_by_default_task(self):
        with self.assertRaisesRegex(RuntimeError, 'preserved startup binding'):
            self.h.invoke(FakeRunner(self.xml))
        self.assertFalse(self.h.journal.exists())

    def test_binding_drift_fails_before_any_task_mutation(self):
        for item in ('wrapper', 'launch', 'pythonw'):
            binding = copy.deepcopy(self.binding)
            binding[item]['sha256'] = '0' * 64
            runner = FakeRunner(self.xml)
            with self.subTest(item=item), self.assertRaisesRegex(RuntimeError, 'Bound startup file changed'):
                windows.install_transaction(task_name=windows.DEFAULT_TASK_NAME, command=self.h.command, archive_root=self.h.archive,
                    command_manifest=self.h.manifest, pointer=self.h.pointer, journal_path=self.h.journal, runner=runner, startup_binding=binding)
            self.assertFalse(any(c[:2] in (['schtasks.exe', '/Delete'], ['schtasks.exe', '/End'], ['schtasks.exe', '/Change']) for c in runner.calls))

    def test_changed_live_task_policy_is_rejected(self):
        root = ET.fromstring(self.xml)
        ns = {'t': windows.TASK_NAMESPACE}
        root.find('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval', ns).text = 'PT6M'
        with self.assertRaisesRegex(RuntimeError, 'Bound startup task changed'):
            windows.verify_startup_binding(ET.tostring(root, encoding='utf-8'), self.h.command, self.binding)

    def test_rollback_restores_control_files_before_restarting_wrapper(self):
        self.h.pointer.parent.mkdir(parents=True, exist_ok=True)
        self.h.pointer.write_bytes(b'original-pointer')
        self.h.manifest.write_bytes(b'original-manifest')
        outer = self
        class CheckRestore(FakeRunner):
            runs = 0
            def __call__(self, command, **kwargs):
                if list(command)[:2] == ['schtasks.exe', '/Run']:
                    self.runs += 1
                    if self.runs == 2:
                        outer.assertEqual(b'original-pointer', outer.h.pointer.read_bytes())
                        outer.assertEqual(b'original-manifest', outer.h.manifest.read_bytes())
                return super().__call__(command, **kwargs)
        runner = CheckRestore(self.xml)
        with self.assertRaisesRegex(RuntimeError, 'injected effect failure'):
            windows.install_transaction(task_name=windows.DEFAULT_TASK_NAME, command=self.h.command, archive_root=self.h.archive,
                command_manifest=self.h.manifest, pointer=self.h.pointer, journal_path=self.h.journal, runner=runner,
                startup_binding=self.binding, quiescence_probe=lambda *_: None,
                readiness_probe=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('injected effect failure')))
        self.assertEqual(windows._xml_structure(self.xml), windows._xml_structure(runner.task_xml))
        self.assertEqual('rollback', json.loads(self.h.journal.read_text('utf-8'))['phase'])

    def test_windows_parser_and_process_exit_use_real_platform_apis(self):
        argv = [sys.executable, '-c', 'print("中文 \\ quoted")', 'C:\\空 白\\']
        self.assertEqual(argv, windows_command_argv(subprocess.list2cmdline(argv)))
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.2)'], creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(lambda: process.poll() is None and process.terminate())
        wait_windows_process_exit(process.pid, [sys.executable], timeout_seconds=5)
        self.assertEqual(0, process.wait(timeout=1))

    def test_quiescence_failure_does_not_restart_a_peer_or_claim_rollback(self):
        runner = FakeRunner(self.xml)
        with self.assertRaisesRegex(RuntimeError, 'still running'):
            windows.install_transaction(task_name=windows.DEFAULT_TASK_NAME, command=self.h.command, archive_root=self.h.archive,
                command_manifest=self.h.manifest, pointer=self.h.pointer, journal_path=self.h.journal, runner=runner,
                startup_binding=self.binding, quiescence_probe=lambda *_: (_ for _ in ()).throw(RuntimeError('still running')))
        self.assertFalse(any(c[:2] == ['schtasks.exe', '/Run'] for c in runner.calls))
        self.assertEqual('false', ET.fromstring(runner.task_xml).findtext(f'{{{windows.TASK_NAMESPACE}}}Settings/{{{windows.TASK_NAMESPACE}}}Enabled'))
        journal = json.loads(self.h.journal.read_text('utf-8'))
        self.assertEqual('blocked-before-restoration', journal['rollback_recovery']['status'])
        self.assertNotEqual('rollback', journal['phase'])

    def test_control_files_exist_when_periodic_task_is_registered(self):
        outer = self
        class CheckCreate(FakeRunner):
            def __call__(self, command, **kwargs):
                if list(command)[:2] == ['schtasks.exe', '/Create']:
                    outer.assertEqual(outer.h.command, json.loads(outer.h.manifest.read_text('utf-8'))['command'])
                    outer.assertEqual(str(outer.h.archive), outer.h.pointer.read_text('utf-8').strip())
                return super().__call__(command, **kwargs)
        self.invoke(CheckCreate(self.xml))

    def generation_fixture(self):
        skill = self.h.base / 'installed'
        candidate = self.h.base / 'candidate'
        for root, value in ((skill, 'old'), (candidate, 'new')):
            (root / 'bin').mkdir(parents=True)
            (root / 'SKILL.md').write_text(value, encoding='utf-8')
            (root / 'config.yaml').write_text('backup: false\n', encoding='utf-8')
            (root / 'bin/memory-wuxian-collector.exe').write_bytes(self.h.collector.read_bytes())
        self.h.command[0] = str(skill / 'bin/memory-wuxian-collector.exe')
        self.h.command[self.h.command.index('--config') + 1] = str(skill / 'config.yaml')
        self.launch.write_text(json.dumps({'executable': self.h.command[0], 'arguments': subprocess.list2cmdline(self.h.command[1:]),
            'sha256': self.binding['collector_sha256']}), encoding='utf-8')
        self.binding['launch']['sha256'] = hashlib.sha256(self.launch.read_bytes()).hexdigest()
        candidate_command = list(self.h.command)
        candidate_command[0] = str(candidate / 'bin/memory-wuxian-collector.exe')
        return skill, dict(candidate_root=candidate, skill_root=skill, runtime_directory=self.h.base / 'runtime',
            task_name=windows.DEFAULT_TASK_NAME, command=self.h.command, candidate_command=candidate_command,
            archive_root=self.h.archive, command_manifest=self.h.manifest, pointer=self.h.pointer,
            runner=FakeRunner(self.xml), startup_binding=self.binding, quiescence_probe=lambda *_: None,
            readiness_probe=self.h.ready_probe, defer_commit=True)

    def test_first_rename_failure_keeps_the_original_generation(self):
        skill, kwargs = self.generation_fixture()
        with patch.object(windows, '_move_directory', side_effect=OSError('first rename failed')):
            with self.assertRaisesRegex(OSError, 'first rename failed'):
                windows.install_generation_transaction(**kwargs)
        self.assertEqual('old', (skill / 'SKILL.md').read_text('utf-8'))
        self.assertEqual([], list((self.h.base / 'runtime').glob('transactions/*/failed-generation')))

    def test_durable_first_rename_intent_recovers_after_process_interruption(self):
        skill, kwargs = self.generation_fixture()
        move = windows._move_directory
        def interrupt(source, destination):
            move(source, destination)
            journal_path = next((self.h.base / 'runtime').glob('transactions/*/journal.json'))
            self.assertTrue(json.loads(journal_path.read_text('utf-8'))['generation']['switched'])
            raise SystemExit('simulated process interruption')
        with patch.object(windows, '_move_directory', side_effect=interrupt), patch.object(windows, '_restore_and_verify_previous', side_effect=SystemExit('simulated process interruption')):
            with self.assertRaises(SystemExit):
                windows.install_generation_transaction(**kwargs)
        self.assertFalse(skill.exists())
        journal_path = next((self.h.base / 'runtime').glob('transactions/*/journal.json'))
        windows.rollback_transaction(journal_path, runner=kwargs['runner'], readiness_probe=self.h.ready_probe, quiescence_probe=lambda *_: None)
        self.assertEqual('old', (skill / 'SKILL.md').read_text('utf-8'))

    def test_generation_rollback_uses_startup_inventory_even_without_telemetry(self):
        skill, kwargs = self.generation_fixture()
        journal, path = windows.install_generation_transaction(**kwargs)
        (self.h.archive / 'imports/codex/collector-telemetry.json').unlink()
        inspected = []
        def inventory(binding, command):
            self.assertEqual(self.binding, binding)
            self.assertEqual('new', (skill / 'SKILL.md').read_text('utf-8'))
            inspected.append(command)
        windows.rollback_transaction(path, runner=kwargs['runner'], readiness_probe=self.h.ready_probe, quiescence_probe=inventory)
        self.assertEqual([self.h.command], inspected)
        self.assertEqual('old', (skill / 'SKILL.md').read_text('utf-8'))

    def test_rollback_requires_disabled_scheduler_even_when_no_process_is_running(self):
        skill, kwargs = self.generation_fixture()
        journal, path = windows.install_generation_transaction(**kwargs)
        class RefuseStop(FakeRunner):
            def __call__(self, command, **kw):
                if list(command)[:2] in (['schtasks.exe', '/Change'], ['schtasks.exe', '/Delete'], ['schtasks.exe', '/End']):
                    self.calls.append(list(command))
                    return subprocess.CompletedProcess(command, 1, b'', b'access denied')
                return super().__call__(command, **kw)
        runner = RefuseStop(self.xml)
        with self.assertRaisesRegex(RuntimeError, 'could not be disabled'):
            windows.rollback_transaction(path, runner=runner, readiness_probe=self.h.ready_probe, quiescence_probe=lambda *_: None)
        self.assertEqual('new', (skill / 'SKILL.md').read_text('utf-8'))
        self.assertFalse(any(c[:2] == ['schtasks.exe', '/Run'] for c in runner.calls))

    def test_startup_inventory_waits_for_child_without_telemetry_or_started_receipt(self):
        observations = [[{'kind': 'wrapper', 'pid': 10}], [{'kind': 'collector', 'pid': 11}], [], []]
        def inspect(*_):
            return observations.pop(0)
        result = wait_windows_startup_exit(self.binding, self.h.command, inspect=inspect, sleep=lambda _: None)
        self.assertEqual('quiescent', result['status'])
        self.assertEqual([], observations)
        with self.assertRaisesRegex(RuntimeError, 'identity unavailable'):
            wait_windows_startup_exit(self.binding, self.h.command,
                inspect=lambda *_: (_ for _ in ()).throw(RuntimeError('identity unavailable')))

    def test_startup_process_inventory_filters_and_fails_closed(self):
        command = [str(self.h.base / 'memory-wuxian-collector.exe')]
        rows = [{'ProcessId': 20, 'ExecutablePath': command[0], 'CommandLine': subprocess.list2cmdline(command)},
                {'ProcessId': 21, 'ExecutablePath': str(self.pythonw), 'CommandLine': subprocess.list2cmdline([str(self.pythonw), '-B', str(self.wrapper)])},
                {'ProcessId': 22, 'ExecutablePath': str(self.pythonw), 'CommandLine': subprocess.list2cmdline([str(self.pythonw), 'unrelated.py'])}]
        def runner(*_a, **_k):
            return subprocess.CompletedProcess([], 0, json.dumps(rows).encode('utf-8'), b'')
        self.assertEqual([{'kind': 'collector', 'pid': 20}, {'kind': 'wrapper', 'pid': 21}],
                         windows_startup_processes(self.binding, command, runner=runner))
        for field in ('ExecutablePath', 'CommandLine'):
            original = rows[0][field]
            rows[0][field] = None
            with self.assertRaisesRegex(RuntimeError, 'identity is unavailable'):
                windows_startup_processes(self.binding, command, runner=runner)
            rows[0][field] = original
        with self.assertRaises(subprocess.CalledProcessError):
            windows_startup_processes(self.binding, command,
                runner=lambda *_a, **_k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, 'CIM')))

    def test_startup_inventory_matches_native_short_path_aliases(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetShortPathNameW.restype = wintypes.DWORD
        def short_path(path):
            buffer = ctypes.create_unicode_buffer(32768)
            count = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
            if not count or count >= len(buffer):
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer.value
        collector = self.h.base / 'memory-wuxian-collector.exe'
        collector.write_bytes(b'fixture, never executed')
        short_collector = short_path(collector)
        if os.path.normcase(short_collector) == os.path.normcase(str(collector.resolve())):
            self.skipTest('8.3 aliases are disabled on this volume')
        rows = [{'ProcessId': 20, 'ExecutablePath': short_collector, 'CommandLine': subprocess.list2cmdline([short_collector])},
                {'ProcessId': 21, 'ExecutablePath': short_path(self.pythonw), 'CommandLine': subprocess.list2cmdline([short_path(self.pythonw), '-B', short_path(self.wrapper)])}]
        runner = lambda *_a, **_k: subprocess.CompletedProcess([], 0, json.dumps(rows).encode('utf-8'), b'')
        self.assertEqual([{'kind': 'collector', 'pid': 20}, {'kind': 'wrapper', 'pid': 21}],
                         windows_startup_processes(self.binding, [str(collector.resolve())], runner=runner))
