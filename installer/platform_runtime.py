"""Fixed runtime integration; packages cannot supply execution hooks."""
import contextlib
import json
import os
import subprocess
import sys
from transaction import lock


class PlatformRuntime:
    def __init__(self, *, offline=False, python=None):
        self.offline = offline
        self.python = python or sys.executable
        self.stack = contextlib.ExitStack()
        self.locked = False

    def powershell(self, script, **values):
        env = dict(os.environ, **{'MW_' + k: str(v) for k,v in values.items()})
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 "$ErrorActionPreference='Stop'; " + script], env=env,
                                capture_output=True, text=True, timeout=40, check=True)
        return result.stdout.strip()

    def snapshot(self, root):
        if self.offline: return {'offline':True}
        if os.name != 'nt':
            raise ValueError('live service integration currently supports Windows; stop services and use --offline')
        result = self.powershell("""
        $items=@(); foreach($pair in @(@('MemoryWuxianCoreMaintenance','live.py'),@('MemoryWuxianCoreDashboard','dashboard.py'))) {
          $task=Get-ScheduledTask -TaskName $pair[0] -ErrorAction SilentlyContinue
          if($task) {
            $expected=Join-Path $env:MW_ROOT ('core\\'+$pair[1])
            if($task.Actions.Count -ne 1 -or -not $task.Actions[0].Arguments.Contains($expected)) { throw 'Task belongs to another installation' }
            $items+=@{name=$pair[0];enabled=[bool]$task.Settings.Enabled;running=($task.State -eq 'Running')}
          }
        }; ConvertTo-Json -Compress -InputObject @($items)
        """, ROOT=root)
        items = json.loads(result)
        if not items:
            raise ValueError('no known runtime tasks; stop all clients and use --offline explicitly')
        return {'offline':False,'tasks':items}

    def task(self, verb, name):
        if name not in {'MemoryWuxianCoreMaintenance','MemoryWuxianCoreDashboard'}:
            raise ValueError('unknown runtime task')
        self.powershell(f'{verb}-ScheduledTask -TaskName $env:MW_TASK | Out-Null', TASK=name)

    def pause(self, root, snapshot):
        if snapshot['offline']:
            if not self.offline: raise ValueError('offline recovery requires --offline')
            return
        if self.offline: raise ValueError('live transaction must recover with live integration')
        for item in snapshot['tasks']: self.task('Disable', item['name'])
        if not self.locked:
            config = json.loads((root/'core/live-config.json').read_text('utf-8-sig'))
            from pathlib import Path
            self.stack.enter_context(lock(Path(config['root'])/'.live-tick.lock'))
            self.locked = True
        for item in snapshot['tasks']:
            self.task('Stop', item['name'])
            self.powershell("""
            $deadline=(Get-Date).AddSeconds(20)
            while((Get-ScheduledTask -TaskName $env:MW_TASK).State -eq 'Running') {
              if((Get-Date) -gt $deadline) { throw 'Task did not stop' }
              Start-Sleep -Milliseconds 100
            }
            """, TASK=item['name'])

    def load_check(self, root):
        code = "import sys; sys.path.insert(0,sys.argv[1]); import runtime, live, mcp_server"
        subprocess.run([self.python, '-I', '-B', '-c', code, str(root/'core')],
                       check=True, capture_output=True, timeout=30)
        binary = root/'bin'/('memory-wuxian-envelope.exe' if os.name=='nt' else 'memory-wuxian-envelope')
        subprocess.run([str(binary), '--version'], check=True, capture_output=True, timeout=10)

    def resume(self, root, snapshot):
        if snapshot['offline']: return
        for item in snapshot['tasks']:
            if item['running']:
                self.task('Enable', item['name'])
                self.task('Start', item['name'])
            self.task('Enable' if item['enabled'] else 'Disable', item['name'])

    def close(self):
        self.stack.close()
        self.locked = False
