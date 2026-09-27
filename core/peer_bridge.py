"""Indexed, origin-isolated peer records and a read-only legacy cache adapter."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from archive import record_hash
from legacy import validate_raw
from storage import canonical_json_bytes, bytes_sha256, exclusive_lock, safe_target


def _normalize(text):
    return unicodedata.normalize('NFKC', text).casefold().strip()


def _grams(word):
    return {'u' + c.encode().hex() for c in word} | {
        'b' + word[i:i+2].encode().hex() for i in range(len(word)-1)}


def _search_terms(text):
    return ' '.join(sorted({g for word in re.findall(r'\w+', _normalize(text)) for g in _grams(word)}))


def _identity(item, kind):
    if kind == 'raw':
        validate_raw(item)
        return item['message_id'], item['content_sha256'], item['conversation_id'], item['text'], item['sequence']
    if 'record' in item and 'content' in item:
        record, text = item['record'], item['content']
        if not isinstance(text, str) or bytes_sha256(text.encode()) != record.get('summary_sha256'):
            raise ValueError('legacy peer summary content hash mismatch')
        return record['summary_id'], bytes_sha256(canonical_json_bytes(item)), record['conversation_id'], text, 0
    from summary import _summary_hash, _summary_id
    if item.get('id') != _summary_id(item) or item.get('summary_sha256') != _summary_hash(item):
        raise ValueError('peer summary identity/hash mismatch')
    return item['id'], bytes_sha256(canonical_json_bytes(item)), item['conversation_id'], item['text'], 0


class PeerIndex:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.path = safe_target(self.root, 'peer-index.sqlite')

    def _connect(self, write=False):
        if write:
            self.root.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=60)
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records(origin TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,
                    kind TEXT NOT NULL,conversation TEXT NOT NULL,source_sequence INTEGER NOT NULL,
                    wire_sequence INTEGER,normalized TEXT NOT NULL,payload TEXT NOT NULL,
                    UNIQUE(origin,id,digest));
                CREATE INDEX IF NOT EXISTS peer_identity ON records(origin,id);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(grams,content='');
                CREATE TABLE IF NOT EXISTS cursors(origin TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS heads(origin TEXT PRIMARY KEY,sequence INTEGER NOT NULL,identity TEXT);
                CREATE TABLE IF NOT EXISTS batches(origin TEXT,key TEXT,digest TEXT,identity TEXT,PRIMARY KEY(origin,key));
            ''')
        else:
            db = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=60)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _batch_key(identity):
        return bytes_sha256(canonical_json_bytes({k:v for k,v in identity.items() if k != 'payload_sha256'}))

    def state(self, origin):
        if not self.path.exists():
            return {'last_wire_sequence': 0, 'last_batch_identity': None}
        db = self._connect()
        try:
            row = db.execute('SELECT sequence,identity FROM heads WHERE origin=?', (origin,)).fetchone()
            return {'last_wire_sequence': row['sequence'] if row else 0,
                    'last_batch_identity': json.loads(row['identity']) if row and row['identity'] else None}
        finally:
            db.close()

    def has_batch(self, origin, batch_identity):
        if not self.path.exists():
            return False
        db = self._connect()
        try:
            row = db.execute('SELECT identity FROM batches WHERE origin=? AND key=?',
                             (origin, self._batch_key(batch_identity))).fetchone()
            if row and json.loads(row['identity']) != batch_identity:
                raise ValueError('peer batch identity conflicts with committed payload')
            return row is not None
        finally:
            db.close()

    @staticmethod
    def _summary_sources(db, origin, item):
        # Old summaries retain their old source descriptors; modern wire summaries
        # must close over this origin's indexed raw records and children.
        if 'record' in item and 'content' in item:
            return
        conversation = item['conversation_id']
        raw_ids, references = item.get('raw_source_ids'), item.get('source_refs')
        if not isinstance(raw_ids, list) or not raw_ids or not isinstance(references, list) or not references:
            raise ValueError('peer summary has no source coverage')
        for identifier in raw_ids:
            scopes = {r[0] for r in db.execute('SELECT DISTINCT conversation FROM records WHERE origin=? AND id=? AND kind=?',
                                               (origin, identifier, 'raw'))}
            if scopes != {conversation}:
                raise ValueError('peer summary raw source is absent or crosses origin/conversation')
        if item['level'] == 1:
            if set(references) != set(raw_ids):
                raise ValueError('peer summary raw references disagree')
        else:
            covered = set()
            for identifier in references:
                children = db.execute('SELECT payload FROM records WHERE origin=? AND id=? AND kind=?',
                                      (origin, identifier, 'summary')).fetchall()
                if len(children) != 1:
                    raise ValueError('peer summary child is absent or ambiguous')
                child = json.loads(children[0][0])
                if child.get('conversation_id') != conversation or child.get('level') != item['level']-1:
                    raise ValueError('peer summary child scope/level mismatch')
                covered.update(child['raw_source_ids'])
            if covered != set(raw_ids):
                raise ValueError('peer summary parent raw coverage differs from children')

    def ingest(self, origin, records, summaries=(), *, batch_identity=None, wire_sequence=None, _cursor=None):
        if not isinstance(origin, str) or not origin or origin == 'local':
            raise ValueError('explicit non-local peer origin required')
        records = list(records)
        with exclusive_lock(safe_target(self.root, '.peer-index.lock')):
            db = self._connect(write=True)
            try:
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    if batch_identity is not None:
                        if batch_identity.get('origin') != origin:
                            raise ValueError('batch origin mismatch')
                        prior = db.execute('SELECT identity FROM batches WHERE origin=? AND key=?',
                                           (origin, self._batch_key(batch_identity))).fetchone()
                        if prior:
                            if json.loads(prior['identity']) != batch_identity:
                                raise ValueError('peer batch identity conflicts with committed payload')
                            return {'status': 'no-change', 'inserted': 0}
                        head = db.execute('SELECT sequence FROM heads WHERE origin=?', (origin,)).fetchone()
                        previous = head[0] if head else 0
                        summary_only = (not records and type(wire_sequence) is int
                            and 0 <= wire_sequence <= previous
                            and batch_identity.get('from_wire_sequence') == wire_sequence + 1
                            and batch_identity.get('to_wire_sequence') == wire_sequence)
                        if not summary_only and (type(wire_sequence) is not int or wire_sequence < previous
                                or batch_identity.get('from_wire_sequence') != previous + 1
                                or batch_identity.get('to_wire_sequence') != wire_sequence):
                            raise ValueError('peer wire sequence gap or overlap')
                    inserted, wires = 0, []
                    def summary_level(value):
                        item = value['record'] if 'wire_sequence' in value else value
                        return item.get('level', item.get('record', {}).get('level', 1))
                    for kind, values in [('raw', records), ('summary', sorted(summaries, key=summary_level))]:
                        for value in values:
                            transport_sequence = value.get('wire_sequence') if isinstance(value, dict) and 'wire_sequence' in value else None
                            item = value['record'] if transport_sequence is not None else value
                            identifier, digest, conversation, text, sequence = _identity(item, kind)
                            if kind == 'summary':
                                self._summary_sources(db, origin, item)
                            if transport_sequence is not None:
                                if type(transport_sequence) is not int or (kind == 'raw' and value.get('source_sequence') != sequence):
                                    raise ValueError('peer wire/source sequence mismatch')
                                wires.append(transport_sequence)
                            found = db.execute('SELECT rowid FROM records WHERE origin=? AND id=? AND digest=?',
                                               (origin, identifier, digest)).fetchone()
                            if found:
                                continue
                            cursor = db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)',
                                (origin, identifier, digest, kind, conversation, sequence, transport_sequence,
                                 _normalize(text), canonical_json_bytes(item).decode()))
                            db.execute('INSERT INTO search(rowid,grams) VALUES(?,?)', (cursor.lastrowid, _search_terms(text)))
                            inserted += 1
                    if batch_identity is not None:
                        if summary_only and wires:
                            raise ValueError('summary-only page must not claim raw wire events')
                        if not summary_only and (len(wires) != wire_sequence - previous or
                                any(value != previous + number + 1 for number, value in enumerate(sorted(wires)))):
                            raise ValueError('peer wire page is not contiguous')
                        encoded = canonical_json_bytes(batch_identity).decode()
                        db.execute('INSERT INTO batches VALUES(?,?,?,?)', (origin, self._batch_key(batch_identity),
                            batch_identity.get('payload_sha256'), encoded))
                        if not summary_only:
                            db.execute('INSERT OR REPLACE INTO heads VALUES(?,?,?)', (origin, wire_sequence, encoded))
                    if _cursor is not None:
                        db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)', (origin, json.dumps(_cursor)))
                return {'status': 'imported', 'inserted': inserted}
            finally:
                db.close()

    def _view(self, row):
        item = json.loads(row['payload'])
        identifier, digest, _, _, _ = _identity(item, row['kind'])
        if identifier != row['id'] or digest != row['digest']:
            raise ValueError('peer indexed source hash mismatch')
        if row['kind'] == 'summary' and 'record' in item:
            result = {**item['record'], 'id': identifier, 'text': item['content'], 'record_type': 'summary'}
        else:
            result = {k:v for k,v in item.items() if not k.startswith('legacy_')}
            if row['kind'] == 'summary':
                result['record_type'] = 'summary'
        return {**result, 'origin': row['origin'], 'peer_record_sha256': digest,
                'qualified_id': row['origin'] + ':' + identifier, 'read_only_replica': True,
                'provenance': {'path': 'peer-index.sqlite', 'sha256': digest}}

    def source(self, origin, identifier, sha256=None):
        if not self.path.exists():
            return None
        db = self._connect()
        try:
            rows = db.execute('SELECT * FROM records WHERE origin=? AND id=?' + (' AND digest=?' if sha256 else ''),
                              (origin, identifier, sha256) if sha256 else (origin, identifier)).fetchmany(2)
            if len(rows) > 1:
                raise ValueError('peer source has historical versions; specify sha256 from the query result')
            return self._view(rows[0]) if rows else None
        finally:
            db.close()

    def query(self, terms, limit=20):
        if not self.path.exists():
            return []
        terms = [_normalize(t) for t in terms if re.fullmatch(r'\w+', _normalize(t))]
        if not terms:
            return []
        expression = ' OR '.join('(' + ' AND '.join(sorted(_grams(t))) + ')' for t in terms)
        clauses = ['CASE WHEN instr(normalized,?)>0 THEN 1 ELSE 0 END' for _ in terms]
        sql = ('SELECT r.*,(' + '+'.join(clauses) + ') score FROM search JOIN records r ON r.rowid=search.rowid '
               'WHERE search MATCH ? AND (' + ' OR '.join('instr(normalized,?)>0' for _ in terms) +
               ') ORDER BY score DESC,source_sequence DESC LIMIT ?')
        db = self._connect()
        try:
            return [{**self._view(row), 'score': row['score']/len(terms)} for row in db.execute(sql, [*terms, expression, *terms, limit])]
        finally:
            db.close()


class PeerCacheBridge:
    def __init__(self, root, source, origin):
        self.index, self.source, self.origin = PeerIndex(root), Path(source).absolute(), origin

    def run_once(self, progress=None):
        with exclusive_lock(safe_target(self.index.root, '.peer-cache-' + bytes_sha256(self.origin.encode())[:16] + '.lock')):
            return self._run_once(progress)

    def _run_once(self, progress):
        source = self.source / 'raw-records.jsonl'
        cursor = None
        if self.index.path.exists():
            db = self.index._connect()
            try:
                row = db.execute('SELECT value FROM cursors WHERE origin=?', (self.origin,)).fetchone()
                cursor = json.loads(row[0]) if row else None
            finally:
                db.close()
        stat = source.stat()
        stamp = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
        if cursor and cursor['source'] != str(source):
            raise ValueError('peer cache origin is already bound to another source')
        result = {'inserted': 0, 'records_read': 0, 'summary_inserted': 0}
        if not cursor or cursor['stamp'] != stamp:
            offset = cursor['offset'] if cursor else 0
            digest = hashlib.sha256()
            with source.open('rb') as handle:
                remaining = offset
                while remaining:
                    chunk = handle.read(min(1024*1024, remaining))
                    if not chunk:
                        raise ValueError('peer cache source was truncated')
                    digest.update(chunk); remaining -= len(chunk)
                if cursor and digest.hexdigest() != cursor['prefix_sha256']:
                    raise ValueError('peer cache consumed prefix changed')
                batch = []
                while handle.tell() < stat.st_size:
                    line = handle.readline(stat.st_size - handle.tell())
                    if not line.endswith(b'\n'):
                        break
                    item = json.loads(line)
                    validate_raw(item)
                    batch.append(item); digest.update(line); offset = handle.tell()
                    if len(batch) >= 256:
                        current = dict(source=str(source), offset=offset, prefix_sha256=digest.hexdigest(), stamp=stamp)
                        result['inserted'] += self.index.ingest(self.origin, batch, _cursor=current)['inserted']
                        result['records_read'] += len(batch); batch = []
                        if progress:
                            progress(dict(result))
                current = dict(source=str(source), offset=offset, prefix_sha256=digest.hexdigest(), stamp=stamp)
                result['inserted'] += self.index.ingest(self.origin, batch, _cursor=current)['inserted']
                result['records_read'] += len(batch)
        # Legacy summary files are immutable identities; SQLite deduplicates them.
        for path in sorted((self.source/'summaries').glob('*.json')):
            item = json.loads(path.read_text('utf-8'))
            result['summary_inserted'] += self.index.ingest(self.origin, [], [item])['inserted']
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--origin', required=True)
    args = parser.parse_args()
    print(json.dumps(PeerCacheBridge(args.root, args.source, args.origin).run_once()))


if __name__ == '__main__':
    main()
