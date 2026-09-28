"""Append-only messages with indexed reads and single-record recovery."""
from __future__ import annotations
import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from storage import atomic_write_json, bytes_sha256, canonical_json_bytes, exclusive_lock, safe_target

def record_hash(record):
    return bytes_sha256(canonical_json_bytes({k:v for k,v in record.items() if k not in {'content_sha256','_path'}}))

def source_identity(source):
    if isinstance(source,dict) and source.get('kind')=='codex-session':
        return {k:v for k,v in source.items() if k not in {'path','line'}}
    return source

def tool_description(payload):
    name = str(payload.get('name','tool'))
    value = payload.get('arguments',payload.get('input',''))
    try:
        fields = json.loads(value) if isinstance(value,str) else value
    except ValueError:
        fields = value
    command = ''
    if isinstance(fields,dict):
        command = str(next((fields[k] for k in ('cmd','command','code') if k in fields),''))
    elif isinstance(fields,str):
        command = fields
    nested = sorted(set(re.findall(r'\btools\.([A-Za-z_][A-Za-z_0-9]*)',command)))
    description = name + (' ['+', '.join(nested)+']' if nested else '') + (': '+command if command else '')
    return description[:4096] + (' [tool description truncated]' if len(description)>4096 else '')

class ArchiveStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = safe_target(self.root, '.assembly-format.json')
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError('use an empty working archive; existing archive migration is not implicit')
            atomic_write_json(marker, {'format':'assembly-six-paths.archive.v1','id':uuid.uuid4().hex})
        elif json.loads(marker.read_text('utf-8')).get('format') != 'assembly-six-paths.archive.v1':
            raise ValueError('unsupported archive format')
        self.archive_id = json.loads(marker.read_text('utf-8')).get('id',bytes_sha256(str(self.root).encode()))
        self.db_path = safe_target(self.root, 'index.sqlite')
        self.journal = safe_target(self.root, 'pending-append.json')
        with self.lock(), self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, sequence INTEGER UNIQUE NOT NULL,
                  conversation TEXT NOT NULL,path TEXT NOT NULL,offset INTEGER NOT NULL,length INTEGER NOT NULL,
                  digest TEXT NOT NULL,text TEXT NOT NULL,speaker TEXT NOT NULL,timestamp TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS conversation_sequence ON messages(conversation,sequence);
                CREATE TABLE IF NOT EXISTS rounds(conversation TEXT PRIMARY KEY,next_round INTEGER,pending INTEGER);
                CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY,vector TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS summary_index(id TEXT PRIMARY KEY,conversation TEXT NOT NULL,text TEXT NOT NULL);
            """)
        self.recover()
        # Repair only objects missing their derived search row after an interrupted commit.
        with self.lock(), self.connection() as db:
            indexed = {r[0] for r in db.execute('SELECT id FROM summary_index')}
        for path in (self.root/'summaries').glob('sum-*.json'):
            if path.stem not in indexed:
                self.index_summary(json.loads(safe_target(self.root,'summaries/'+path.name).read_text('utf-8')))

    def lock(self):
        return exclusive_lock(safe_target(self.root, '.archive.lock'))

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _recover(self):
        if not self.journal.exists():
            return
        pending = json.loads(self.journal.read_text('utf-8'))
        record = pending['record']
        if record_hash(record) != record['content_sha256']:
            raise ValueError('pending append is corrupt')
        payload = canonical_json_bytes(record) + b'\n'
        path = safe_target(self.root, pending['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        size = path.stat().st_size if path.exists() else 0
        offset = pending['offset']
        if not offset <= size <= offset + len(payload):
            raise ValueError('raw append position changed; refusing to rewrite history')
        existing = b''
        if path.exists():
            with path.open('rb') as handle:
                handle.seek(offset)
                existing = handle.read(len(payload))
        if not payload.startswith(existing):
            raise ValueError('raw bytes differ from pending append')
        if len(existing) < len(payload):
            with path.open('ab') as handle:
                handle.write(payload[len(existing):])
                handle.flush()
                os.fsync(handle.fileno())
        with self.connection() as db:
            found = db.execute('SELECT digest FROM messages WHERE id=?',(record['message_id'],)).fetchone()
            if found and found['digest'] != record['content_sha256']:
                raise ValueError('message ID conflict during recovery')
            if not found:
                db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)', (
                    record['message_id'],record['sequence'],record['conversation_id'],pending['path'],offset,
                    len(payload),record['content_sha256'],record['text'],record['speaker'],record['timestamp']))
                db.execute('INSERT OR REPLACE INTO rounds VALUES(?,?,?)',(
                    record['conversation_id'],pending['next_round'],pending['pending_round']))
        self.journal.unlink()

    def recover(self):
        with self.lock():
            self._recover()

    def append_message(self,speaker,text,timestamp=None,conversation_id=None,message_id=None,reply_to=None,
                       allow_secrets=False,complete_round=True,source=None):
        if speaker not in {'user','assistant','system','tool'} or not isinstance(text,str):
            raise ValueError('invalid speaker or text')
        if not isinstance(conversation_id,str) or not conversation_id or len(conversation_id)>512:
            raise ValueError('conversation_id is required')
        message_id = message_id or str(uuid.uuid4())
        if not isinstance(message_id,str) or not message_id or len(message_id)>512:
            raise ValueError('invalid message_id')
        if timestamp is not None:
            dt.datetime.fromisoformat(timestamp)
        stored_text = text if allow_secrets else re.sub(r'\bsk-[A-Za-z0-9_-]{20,}\b','[REDACTED]',text)
        with self.lock():
            self._recover()
            existing = self.message_by_id(message_id)
            if existing:
                expected = (speaker,stored_text,conversation_id,source_identity(source),bool(complete_round))
                actual = (existing['speaker'],existing['text'],existing['conversation_id'],source_identity(existing.get('source')),existing['complete_round'])
                if actual != expected or (timestamp is not None and timestamp != existing['timestamp']) or (reply_to is not None and reply_to != existing.get('reply_to')):
                    raise ValueError('message ID already belongs to different content')
                return {**existing,'status':'duplicate'}
            with self.connection() as db:
                sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM messages').fetchone()[0]
                state = db.execute('SELECT next_round,pending FROM rounds WHERE conversation=?',(conversation_id,)).fetchone()
            next_round,pending = tuple(state) if state else (1,None)
            if speaker == 'user' and pending is None:
                pending,next_round = next_round,next_round+1
            number = pending if speaker in {'user','assistant','tool'} and pending else 0
            completed = speaker == 'assistant' and complete_round and pending is not None
            record = dict(record_type='raw_message',sequence=sequence,message_id=message_id,
                conversation_id=conversation_id,speaker=speaker,text=stored_text,
                timestamp=timestamp or dt.datetime.now(dt.timezone.utc).isoformat(),
                round_number=number,completes_round=bool(completed),complete_round=bool(complete_round),
                reply_to=reply_to,source=source,redacted=stored_text != text)
            record['content_sha256'] = record_hash(record)
            relative = 'raw/'+bytes_sha256(conversation_id.encode())+'.jsonl'
            path = safe_target(self.root,relative)
            atomic_write_json(self.journal,dict(record=record,path=relative,offset=path.stat().st_size if path.exists() else 0,
                next_round=next_round,pending_round=None if completed else pending))
            self._recover()
            return {**record,'status':'appended'}

    def _read(self,row):
        with safe_target(self.root,row['path']).open('rb') as handle:
            handle.seek(row['offset'])
            record = json.loads(handle.read(row['length']))
        if record.get('message_id') != row['id'] or record_hash(record) != row['digest'] or record['content_sha256'] != row['digest']:
            raise ValueError('raw message does not match indexed identity')
        return record

    def message_by_id(self,message_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        return self._read(row) if row else None

    def records(self,conversation_id=None,after_sequence=0):
        sql,args = 'SELECT * FROM messages WHERE sequence>?',[int(after_sequence)]
        if conversation_id is not None:
            sql += ' AND conversation=?'
            args.append(conversation_id)
        with self.connection() as db:
            rows = db.execute(sql+' ORDER BY sequence',args).fetchall()
        return [self._read(row) for row in rows]

    def status(self):
        with self.connection() as db:
            row = db.execute('SELECT COUNT(*),COUNT(DISTINCT conversation),MAX(sequence),MAX(timestamp) FROM messages').fetchone()
        return dict(total_messages=row[0],conversations=row[1],last_sequence=row[2] or 0,last_timestamp=row[3])

    def excluded_conversations(self):
        path=safe_target(self.root,'excluded-conversations.json')
        return set(json.loads(path.read_text('utf-8')).get('conversations',[])) if path.exists() else set()

    def index_summary(self, item):
        from summary import _summary_hash, _summary_id
        if item.get('id') != _summary_id(item) or item.get('summary_sha256') != _summary_hash(item):
            raise ValueError('invalid completed summary')
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO summary_index VALUES(?,?,?)', (item['id'],item['conversation_id'],item['text']))

    def summary_by_id(self, identifier):
        from summary import _summary_hash, _summary_id
        with self.connection() as db:
            row = db.execute('SELECT id FROM summary_index WHERE id=?',(identifier,)).fetchone()
        if not row: return None
        item = json.loads(safe_target(self.root,'summaries/'+identifier+'.json').read_text('utf-8'))
        if item.get('id') != identifier or _summary_id(item) != identifier or item.get('summary_sha256') != _summary_hash(item):
            raise ValueError('summary source changed')
        return item

    def export_snapshot(self, after_sequence=0):
        from summary import SummaryService
        with self.lock():
            self._recover()
            result = {'records':self.records(after_sequence=after_sequence),'summaries':SummaryService(self).list()}
            legacy = safe_target(self.root,'legacy/artifacts.json')
            if legacy.exists():
                result['legacy_artifacts'] = json.loads(legacy.read_text('utf-8'))
            return result

    def import_peer(self,origin,payload):
        if not isinstance(origin,str) or not origin or not isinstance(payload,dict):
            raise ValueError('invalid peer payload')
        records,seen = payload.get('records',[]),set()
        for record in records:
            if record['message_id'] in seen or record_hash(record) != record.get('content_sha256'):
                raise ValueError('invalid peer message identity/content')
            seen.add(record['message_id'])
        path = safe_target(self.root,'replicas/'+bytes_sha256(origin.encode())+'.json')
        with self.lock():
            previous = json.loads(path.read_text('utf-8')) if path.exists() else {'records':[],'summaries':[]}
            merged = {r['message_id']:r for r in previous['records']}
            for record in records:
                if record['message_id'] in merged and merged[record['message_id']] != record:
                    raise ValueError('peer attempted to change an existing message')
                merged[record['message_id']] = record
            from summary import _summary_hash, _summary_id
            summaries = {s['id']:s for s in previous.get('summaries',[])}
            for item in payload.get('summaries',[]):
                if not set(item.get('raw_source_ids',[])) <= set(merged):
                    raise ValueError('peer summary references absent messages')
                if item.get('summary_sha256') != _summary_hash(item) or item.get('id') != _summary_id(item):
                    raise ValueError('peer summary content or identity mismatch')
                if any(merged[mid]['conversation_id'] != item['conversation_id'] for mid in item['raw_source_ids']):
                    raise ValueError('peer summary crosses conversations')
                if item['id'] in summaries and summaries[item['id']] != item:
                    raise ValueError('peer changed an existing summary')
                summaries[item['id']] = item
            for item in summaries.values():
                if type(item['level']) is not int or item['level'] < 1 or not item['source_refs']:
                    raise ValueError('invalid peer summary level or references')
                if item['level'] == 1:
                    if set(item['source_refs']) != set(item['raw_source_ids']):
                        raise ValueError('peer raw summary references disagree')
                else:
                    if any(ref not in summaries for ref in item['source_refs']):
                        raise ValueError('peer parent summary is missing child summaries')
                    children = [summaries[ref] for ref in item['source_refs']]
                    if any(c['conversation_id'] != item['conversation_id'] or c['level'] != item['level']-1 for c in children):
                        raise ValueError('peer summary children have wrong scope or level')
                    if set(item['raw_source_ids']) != {mid for c in children for mid in c['raw_source_ids']}:
                        raise ValueError('peer parent raw sources disagree with children')
            artifacts = dict(previous.get('legacy_artifacts',{}))
            incoming = payload.get('legacy_artifacts',{})
            if not isinstance(incoming,dict):
                raise ValueError('invalid legacy artifacts')
            for identifier,artifact in incoming.items():
                if identifier in artifacts and artifacts[identifier] != artifact:
                    raise ValueError('peer changed an existing legacy artifact')
                artifacts[identifier] = artifact
            result = dict(origin=origin,records=sorted(merged.values(),key=lambda r:r['sequence']),summaries=list(summaries.values()))
            if artifacts:
                result['legacy_artifacts'] = artifacts
            atomic_write_json(path,result)
        return {'status':'received','origin':origin,'records':len(records),'read_only_replica':True}

    def peer_snapshots(self):
        directory = self.root/'replicas'
        return [json.loads(safe_target(self.root,str(p.relative_to(self.root)).replace('\\','/')).read_text('utf-8'))
                for p in sorted(directory.glob('*.json'))] if directory.exists() else []

    def collect_session(self, source_path):
        from collector import DirectCollector
        return DirectCollector(self, Path(source_path).resolve().parent).collect(source_path)
