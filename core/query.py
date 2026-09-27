"""One query service for CLI/HTTP, indexed keyword and optional vector search."""
from __future__ import annotations
import json
import math
import re
import unicodedata
from summary import SummaryService

def normalize(text):
    return unicodedata.normalize('NFKC',text).casefold().strip()

def _view(record):
    # Migration evidence remains on disk and in source lookup, not repeated in every prompt.
    return {key:value for key,value in record.items() if not key.startswith('legacy_')}

def _vector(values):
    values = [float(v) for v in values]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError('invalid embedding')
    size = math.sqrt(sum(v*v for v in values))
    if not size:
        raise ValueError('zero embedding')
    return [v/size for v in values]

class QueryService:
    def __init__(self,store,encoder=None,encoder_id=None):
        self.store,self.encoder,self.encoder_id = store,encoder,encoder_id

    def index_vectors(self):
        if self.encoder is None or not self.encoder_id:
            raise ValueError('configure an encoder and stable encoder_id')
        with self.store.lock(), self.store.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS embedding_meta(name TEXT PRIMARY KEY,value TEXT)')
            prior = db.execute("SELECT value FROM embedding_meta WHERE name='model'").fetchone()
            if prior and prior[0] != self.encoder_id:
                raise ValueError('embedding model changed; use a separate index or explicitly rebuild')
            rows = db.execute('SELECT id,text FROM messages WHERE id NOT IN (SELECT id FROM vectors)').fetchall()
            dimension = db.execute("SELECT value FROM embedding_meta WHERE name='dimension'").fetchone()
            for start in range(0,len(rows),64):
                batch = rows[start:start+64]
                vectors = self.encoder([r['text'] for r in batch])
                if len(vectors) != len(batch):
                    raise ValueError('encoder result count mismatch')
                for row,vector in zip(batch,vectors):
                    vector = _vector(vector)
                    if dimension and len(vector) != int(dimension[0]):
                        raise ValueError('embedding dimension changed')
                    dimension = (str(len(vector)),)
                    db.execute('INSERT INTO vectors VALUES(?,?)',(row['id'],json.dumps(vector)))
            if dimension:
                db.execute('INSERT OR REPLACE INTO embedding_meta VALUES(?,?)',('dimension',dimension[0]))
            db.execute('INSERT OR REPLACE INTO embedding_meta VALUES(?,?)',('model',self.encoder_id))
        return {'indexed':len(rows),'model':self.encoder_id}

    def query(self,text,limit=20,mode='keyword',include_peers=False):
        if not isinstance(text,str) or not text.strip() or len(text)>2048 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('invalid query or limit')
        if mode not in {'keyword','semantic','hybrid'}:
            raise ValueError('invalid query mode')
        if type(include_peers) is not bool:
            raise ValueError('include_peers must be boolean')
        terms = list(dict.fromkeys(re.findall(r'\w+',normalize(text))))[:16] or [normalize(text)]
        excluded=self.store.excluded_conversations()
        scope=' AND conversation NOT IN ('+','.join('?' for _ in excluded)+')' if excluded else ''
        scores,rows,warnings = {},{},[]
        with self.store.connection() as db:
            if mode in {'keyword','hybrid'}:
                clauses = ['CASE WHEN instr(lower(text),?)>0 THEN 1 ELSE 0 END' for _ in terms]
                sql = 'SELECT *,('+'+'.join(clauses)+') AS score FROM messages WHERE ('+' OR '.join('instr(lower(text),?)>0' for _ in terms)+')'+scope+' ORDER BY score DESC,sequence DESC LIMIT ?'
                for row in db.execute(sql,[*terms,*terms,*excluded,min(500,limit*5)]):
                    rows[row['id']] = row
                    scores[row['id']] = row['score']/len(terms)
            if mode in {'semantic','hybrid'}:
                if self.encoder is None or not self.encoder_id:
                    if mode == 'semantic':
                        raise ValueError('semantic encoder is not configured')
                    warnings.append('semantic encoder not configured; keyword results returned')
                else:
                    try:
                        model = db.execute("SELECT value FROM embedding_meta WHERE name='model'").fetchone()
                    except Exception:
                        model = None
                    if not model or model[0] != self.encoder_id:
                        raise ValueError('build the index with the selected encoder first')
                    encoded = self.encoder([text])
                    if len(encoded) != 1:
                        raise ValueError('encoder result count mismatch')
                    q = _vector(encoded[0])
                    ranked = []
                    for entry in db.execute('SELECT id,vector FROM vectors'):
                        v = json.loads(entry['vector'])
                        if len(v)!=len(q):
                            raise ValueError('query embedding dimension mismatch')
                        ranked.append((sum(a*b for a,b in zip(q,v)),entry['id']))
                    for score,identifier in sorted(ranked,reverse=True)[:limit*5]:
                        row = db.execute('SELECT * FROM messages WHERE id=?',(identifier,)).fetchone()
                        if row and row['conversation'] not in excluded:
                            rows[identifier] = row
                            scores[identifier] = max(scores.get(identifier,0),score)
                    missing = db.execute('SELECT COUNT(*) FROM messages WHERE id NOT IN (SELECT id FROM vectors)').fetchone()[0]
                    if missing:
                        warnings.append(f'{missing} messages are not embedded')
        results = []
        # Only result records are read from raw storage and verified.
        for identifier in sorted(scores,key=lambda i:(scores[i],rows[i]['sequence']),reverse=True)[:limit]:
            row = rows[identifier]
            record = self.store._read(row)
            results.append({**_view(record),'score':scores[identifier],'origin':'local',
                            'provenance':{'path':row['path'],'offset':row['offset'],'sha256':row['digest']}})
        if mode in {'keyword','hybrid'}:
            with self.store.connection() as db:
                hits = db.execute('SELECT id,text FROM summary_index WHERE ('+ ' OR '.join('instr(lower(text),?)>0' for _ in terms) + ')'+scope+' LIMIT ?', [*terms,*excluded,limit]).fetchall()
            for row in hits:
                item = self.store.summary_by_id(row['id'])
                results.append({**_view(item),'record_type':'summary','origin':'local',
                                'score':sum(t in normalize(row['text']) for t in terms)/len(terms),
                                'confidence':'summary-supported'})
            results = sorted(results,key=lambda r:r['score'],reverse=True)[:limit]
        if include_peers:
            from peer_bridge import PeerIndex
            indexed_peers = PeerIndex(self.store.root).query(terms, limit)
            results.extend(indexed_peers)
            if indexed_peers and mode != 'keyword':
                warnings.append('peer replicas use indexed keyword search')
            for snapshot in self.store.peer_snapshots():
                for record in snapshot['records']:
                    score = sum(t in normalize(record['text']) for t in terms)/len(terms)
                    if score:
                        results.append({**_view(record),'score':score,'origin':snapshot['origin'],'read_only_replica':True})
            unique = {}
            for result in results:
                key = (result['origin'], result.get('message_id', result.get('id')),
                       result.get('peer_record_sha256', result.get('content_sha256', result.get('summary_sha256'))))
                unique.setdefault(key, result)
            results = sorted(unique.values(),key=lambda r:r['score'],reverse=True)[:limit]
        for result in results:
            if len(result.get('text',''))>2000:
                result['text_length']=len(result['text'])
                result['text']=result['text'][:2000]
                result['text_truncated']=True
        return {'query':text,'mode':mode,'results':results,'warnings':warnings}

    def context(self,conversation_id,max_characters=12000,*,context_window=None,max_tokens=3000):
        if conversation_id in self.store.excluded_conversations():
            raise ValueError('internal task is excluded from user memory')
        if not isinstance(conversation_id,str) or not conversation_id or not 1<=max_characters<=40000:
            raise ValueError('invalid context request')
        summaries = SummaryService(self.store).list(conversation_id)
        child_ids = {child for item in summaries for child in item['source_refs'] if item['level'] > 1}
        roots = [item for item in summaries if item['id'] not in child_ids]
        roots.sort(key=lambda s:s.get('level',1),reverse=True)
        selected,covered,used = [],set(),0
        for item in roots:
            text = item['text']
            if used+len(text)>max_characters:
                continue
            selected.append(_view(item))
            covered.update(item['raw_source_ids'])
            used += len(text)
        recent = []
        for record in reversed(self.store.records(conversation_id)):
            if record['message_id'] in covered:
                continue
            if used+len(record['text'])>max_characters:
                continue
            recent.append(_view(record))
            used += len(record['text'])
        if type(max_tokens) is not int or max_tokens < 1 or (context_window is not None and (type(context_window) is not int or context_window < 1)):
            raise ValueError('invalid context token budget')
        token_budget = min(3000,max_tokens,10000,context_window//100 if context_window is not None else 10000)
        # A UTF-8 byte cap is deliberately more conservative than a token estimate.
        result = {'conversation_id':conversation_id,'summaries':selected,'recent':list(reversed(recent)),
                  'confidence':'summary-supported; use raw sources for exact claims'}
        cap = max(0,token_budget-64)
        while len(json.dumps(result,ensure_ascii=False).encode('utf-8')) > cap:
            if result['recent']: result['recent'].pop(0)
            elif result['summaries']: result['summaries'].pop()
            else: raise ValueError('context budget too small for metadata')
        return result


