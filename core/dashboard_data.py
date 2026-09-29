"""Read-only, incremental observations for the local dashboard."""
from collections import Counter, defaultdict
from contextlib import ExitStack
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
from storage import safe_target

CJK = re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]')


class DashboardData:
    def __init__(self):
        self.lock = threading.RLock()
        self.sequence = 0
        self.messages = {}
        self.daily = {}
        self.files = {}
        self.completed = defaultdict(set)

    def read_json(self, path):
        stat = path.stat()
        key = str(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if key not in self.files or self.files[key][0] != stamp:
            self.files[key] = (stamp, json.loads(path.read_text('utf-8')))
        return self.files[key][1]

    def message_metrics(self, store, excluded):
        # Only newly indexed messages are observed. Legacy round numbers are IDs,
        # not counts; read assistant completion flags at their indexed offsets.
        handles = {}
        with store.connection() as db, ExitStack() as stack:
            for row in db.execute('SELECT sequence,conversation,text,speaker,timestamp,path,offset,length FROM messages '
                                  'WHERE sequence>? ORDER BY sequence', (self.sequence,)):
                sequence, conversation, text, speaker, stamp, path, offset, length = row
                if speaker == 'assistant':
                    if path not in handles:
                        handles[path] = stack.enter_context(safe_target(store.root, path).open('rb'))
                    handle = handles[path]
                    handle.seek(offset)
                    record = json.loads(handle.read(length))
                    if record['completes_round']:
                        self.completed[conversation].add(record['round_number'])
                self.sequence = sequence
                cjk = len(CJK.findall(text))
                tokens = cjk + (len(text) - cjk + 3) // 4
                item = self.messages.setdefault(conversation, Counter())
                item.update(messages=1, tools=int(speaker == 'tool'), characters=len(text),
                            estimated_tokens=tokens,
                            message_tokens=tokens if speaker in ('user', 'assistant') else 0)
                instant = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                item['latest'] = max(item.get('latest', ''), instant.astimezone(dt.timezone.utc).isoformat())
                day = instant.astimezone().date().isoformat()
                self.daily.setdefault((conversation, day), Counter()).update(messages=1, characters=len(text))
        return {key: value for key, value in self.messages.items() if key not in excluded}

    def summary_metrics(self, store, excluded):
        levels, conversations = Counter(), defaultdict(Counter)
        for path in (store.root / 'summaries').glob('sum-*.json'):
            item = self.read_json(path)
            conversation = item['conversation_id']
            if conversation not in excluded:
                level = str(item['level'])
                levels[level] += 1
                conversations[conversation][level] += 1
        # L1 is always a main metric; nonexistent higher levels are omitted.
        levels.setdefault('1', 0)
        return dict(sorted(levels.items(), key=lambda pair: int(pair[0]))), conversations

    def usage(self, config):
        """Expose existing persisted billing telemetry without inventing later usage."""
        root = config.get('legacy_archive_root')
        if not root and config.get('source'):
            source = Path(config['source'])
            if source.name == 'raw':
                root = source.parent
        ledgers = {}
        directories = [Path(config['root']) / 'imports/codex/token-usage']
        if root:
            directories.insert(0, Path(root) / 'imports/codex/token-usage')
        for directory in directories:
            for path in directory.glob('*.json'):
                item = self.read_json(path)
                if item.get('measurement') != 'codex-reported-model-usage':
                    continue
                # A migrated ledger replaces its old copy; never add both.
                ledgers[(item.get('session_id'), item.get('segment_id'))] = item
        conversations, daily = {}, Counter()
        for item in ledgers.values():
            identifier = item.get('conversation_id') or 'codex:' + item['session_id']
            result = conversations.setdefault(identifier, dict(reported_total_tokens=0,
                reported_input_tokens=0, reported_cached_input_tokens=0, reported_output_tokens=0,
                model_request_count=0, counter_reset_count=0, updated_at=''))
            usage = item.get('reported_usage') or {}
            for name in ('total_tokens', 'input_tokens', 'cached_input_tokens', 'output_tokens'):
                result['reported_' + name] += int(usage.get(name) or 0)
            for name in ('model_request_count', 'counter_reset_count'):
                result[name] += int(item.get(name) or 0)
            if (item.get('updated_at') or '') >= result['updated_at']:
                latest = item.get('latest_request_usage') or {}
                request, window = latest.get('total_tokens'), item.get('model_context_window')
                result.update(updated_at=item.get('updated_at') or '', request_tokens=request,
                    context_window=window, window_ratio_percent=round(request * 100 / window, 2)
                    if request is not None and window else None)
            for day, usage in (item.get('daily_usage') or {}).items():
                daily[(identifier, day)] += int(usage.get('total_tokens') or 0)
        return conversations, daily


def thread_metadata(config):
    home = Path(config['sessions_root']).parent if config.get('sessions_root') else None
    if not home:
        return {}, 'not-configured'
    databases = sorted(home.glob('state_*.sqlite'),
                       key=lambda path: int(path.stem.split('_')[-1]) if path.stem.split('_')[-1].isdigit() else -1,
                       reverse=True)
    for path in databases:
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
                if not {'id', 'title', 'cwd', 'archived'} <= columns:
                    continue
                title = "COALESCE(NULLIF(name,''),title)" if 'name' in columns else 'title'
                rows = db.execute(f'SELECT id,{title},cwd,archived FROM threads').fetchall()
            return {'codex:' + row[0]: dict(title=row[1], project=row[2], archived=bool(row[3]))
                    for row in rows}, 'codex-state'
        except sqlite3.Error:
            continue
    return {}, 'unavailable'


def process_observation(pid):
    if type(pid) is not int or pid <= 0:
        return {}
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return {'process_running': False}
        try:
            code = wintypes.DWORD()
            if kernel.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code)):
                return {'process_running': code.value == 259}
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
        return {}
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', '%cpu=,rss=,command='],
                                capture_output=True, text=True, timeout=2)
        fields = result.stdout.strip().split(None, 2)
        if len(fields) != 3 or 'live.py' not in fields[2]:
            return {'process_running': False}
        return dict(process_running=True, cpu_percent=float(fields[0]), memory_bytes=int(fields[1]) * 1024)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def archive_bytes(root):
    total = 0
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if not (Path(folder) / name).is_symlink()]
        for name in files:
            path = Path(folder) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # An atomic status-file replacement may complete during a scan.
    return total
