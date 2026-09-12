"""Cross-platform subprocess defaults for Memory Wuxian."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Sequence


def _unique_command_argument(command: Sequence[str], option: str) -> str | None:
    matches = [index for index, value in enumerate(command) if value == option]
    if len(matches) != 1 or matches[0] + 1 >= len(command):
        return None
    return command[matches[0] + 1]


def no_window_kwargs() -> dict[str, Any]:
    """Prevent console flashes for child processes on Windows."""
    if os.name != "nt":
        return {}
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


def windows_command_argv(command_line: str) -> list[str]:
    """Use the Windows parser for a saved native command; never invoke a shell."""
    if os.name != 'nt':
        raise RuntimeError('Windows command parsing requires Windows')
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    count = ctypes.c_int()
    result = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [result[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(result)


def wait_windows_process_exit(pid: int, command: Sequence[str], *, timeout_seconds: float = 15) -> None:
    """Confirm the exact previous process exited; access denial is not absence."""
    if os.name != 'nt' or not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        raise RuntimeError('Collector quiescence requires a known Windows PID')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: this PID no longer exists.
            return
        raise ctypes.WinError(error)
    try:
        length = wintypes.DWORD(32768)
        executable = ctypes.create_unicode_buffer(length.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        if os.path.normcase(os.path.abspath(executable.value)) != os.path.normcase(os.path.abspath(command[0])):
            raise RuntimeError('Collector PID was reused by a different executable')
        if kernel.WaitForSingleObject(handle, int(timeout_seconds * 1000)) != 0:
            raise RuntimeError('Previous collector did not exit before the generation switch')
    finally:
        kernel.CloseHandle(handle)


def windows_startup_processes(binding, command, *, runner=subprocess.run):
    """Read only the bound executable names/paths; unknown identity is not zero."""
    import base64
    import json
    import re
    from pathlib import Path
    if os.name != 'nt':
        raise RuntimeError('Startup process inspection requires Windows')
    paths = {'collector': str(Path(command[0]).resolve()), 'pythonw': str(Path(binding['pythonw']['path']).resolve())}
    names = [Path(p).name for p in paths.values()]
    if not all(re.fullmatch(r'[A-Za-z0-9_.-]+', name) for name in names):
        raise ValueError('Unsupported bound executable name')
    config = base64.b64encode(json.dumps(paths).encode('utf-8')).decode('ascii')
    script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
    script += "$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + config + "'))|ConvertFrom-Json; "
    script += "$r=@(Get-CimInstance Win32_Process -Filter \"Name='" + names[0] + "' OR Name='" + names[1] + "'\" | "
    script += "Where-Object { !$_.ExecutablePath -or $_.ExecutablePath -eq $p.collector -or $_.ExecutablePath -eq $p.pythonw } | "
    script += "Select-Object ProcessId,ExecutablePath,CommandLine); ConvertTo-Json -InputObject $r -Compress"
    result = runner(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                     base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                    check=True, capture_output=True, timeout=20, **no_window_kwargs())
    rows = json.loads(result.stdout.decode('utf-8-sig') if isinstance(result.stdout, bytes) else result.stdout)
    if not isinstance(rows, list):
        raise RuntimeError('Invalid startup process inventory')
    matched = []
    normalize = lambda p: os.path.normcase(os.path.abspath(p))
    for row in rows:
        if not row.get('ExecutablePath') or not row.get('CommandLine'):
            raise RuntimeError('Bound executable process identity is unavailable')
        path = normalize(row['ExecutablePath'])
        if path == normalize(paths['collector']):
            matched.append({'kind': 'collector', 'pid': int(row['ProcessId'])})
        elif path == normalize(paths['pythonw']):
            argv = windows_command_argv(row['CommandLine'])
            if any(normalize(value) == normalize(binding['wrapper']['path']) for value in argv[1:]):
                matched.append({'kind': 'wrapper', 'pid': int(row['ProcessId'])})
    return matched


def wait_windows_startup_exit(binding, command, *, timeout_seconds=20, inspect=None, sleep=None):
    """After disabling/removing the task, prove wrapper and collector are absent."""
    import time
    inspect = inspect or windows_startup_processes
    sleep = sleep or time.sleep
    deadline = time.monotonic() + timeout_seconds
    while True:
        # Confirm wrappers are gone before the final collector observation: a
        # wrapper stopped between Popen and its receipt can leave an unseen PID.
        first = inspect(binding, command)
        if not first and not inspect(binding, command):
            return {'status': 'quiescent', 'collector_processes': 0, 'wrapper_processes': 0}
        if time.monotonic() >= deadline:
            raise RuntimeError('Bound startup processes did not exit before generation switch')
        sleep(0.25)


def windows_task_present(task_name, *, runner=subprocess.run):
    """Distinguish an absent exact task from query or permission failure."""
    import base64
    import json
    encoded_name = base64.b64encode(str(task_name).encode('utf-8')).decode('ascii')
    script = "$ErrorActionPreference='Stop'; $name=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded_name + "')); "
    script += "$s=New-Object -ComObject Schedule.Service; $s.Connect(); $f=$s.GetFolder('\\'); "
    script += "try { $null=$f.GetTask($name); 'true' } catch { $e=$_.Exception; while($e.InnerException){$e=$e.InnerException}; if($e.HResult -eq -2147024894){'false'}else{throw} }"
    result = runner(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                     base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                    capture_output=True, check=True, timeout=20, **no_window_kwargs())
    value = json.loads(result.stdout.decode('utf-8-sig') if isinstance(result.stdout, bytes) else result.stdout)
    if not isinstance(value, bool):
        raise RuntimeError('Invalid exact task presence response')
    return value
