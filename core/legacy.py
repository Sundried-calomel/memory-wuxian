"""Explicit offline conversion. Original inputs are immutable evidence, never edited."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from archive import ArchiveStore, record_hash
from storage import atomic_replace_bytes, atomic_write_json, bytes_sha256, safe_target

RAW_MARKER = '<!-- memory-wuxian-record -->'

def read_raw_markdown(data):
    lines = data.decode('utf-8-sig').splitlines()
    records = []
    for number, line in enumerate(lines):
        if line != RAW_MARKER:
            continue
        if number+3 >= len(lines) or lines[number+1] != '```json' or lines[number+3] != '```':
            raise ValueError('incomplete legacy raw block')
        record = json.loads(lines[number+2])
        validate_raw(record)
        records.append(record)
    if not records:
        raise ValueError('selected file has no legacy raw records')
    return records

def validate_raw(record):
    if not isinstance(record, dict) or record.get('record_type') != 'raw_message':
        raise ValueError('unsupported legacy raw record')
    if record.get('content_sha256') != record_hash(record):
        raise ValueError('legacy raw content hash mismatch')
    for key in ('message_id','conversation_id','timestamp'):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError('missing legacy '+key)
    dt.datetime.fromisoformat(record['timestamp'])
    if not isinstance(record.get('text'),str) or record.get('speaker') not in {'user','assistant','system','tool'}:
        raise ValueError('invalid legacy message')
    for key in ('sequence','round_number'):
        if type(record.get(key)) is not int or record[key] < (1 if key=='sequence' else 0):
            raise ValueError('invalid legacy '+key)
    if type(record.get('completes_round')) is not bool:
        raise ValueError('legacy round completion is missing')

def normalize_raw(record, sequence):
    validate_raw(record)
    original = {k:v for k,v in record.items() if k!='_path'}
    result = {**original, 'sequence':sequence, 'legacy_original':original,
              'legacy_sha256':original['content_sha256'],
              'complete_round':bool(original['completes_round']) if original['speaker']=='assistant' else True}
    result['content_sha256'] = record_hash(result)
    return result

def normalize_records(records):
    unique, sequences = {}, {}
    for record in records:
        validate_raw(record)
        identifier, sequence = record['message_id'], record['sequence']
        if identifier in unique and unique[identifier]['content_sha256'] != record['content_sha256']:
            raise ValueError('conflicting legacy message ID')
        if sequence in sequences and sequences[sequence] != identifier:
            raise ValueError('conflicting legacy sequence')
        unique[identifier], sequences[sequence] = record, identifier
    return [normalize_raw(item,index+1) for index,item in enumerate(sorted(unique.values(),key=lambda r:r['sequence']))]

def _write_records(store, records):
    # Only called for a private new staging archive; reuse normal append recovery.
    rounds = {}
    for record in records:
        conversation, number = record['conversation_id'], record['round_number']
        next_round,pending = rounds.get(conversation,(1,None))
        next_round = max(next_round,number+1)
        if record['speaker']=='user' and number:
            pending = number
        if record['completes_round']:
            pending = None
        rounds[conversation] = next_round,pending
        relative = 'raw/'+bytes_sha256(conversation.encode())+'.jsonl'
        path = safe_target(store.root,relative)
        atomic_write_json(store.journal,dict(record=record,path=relative,
            offset=path.stat().st_size if path.exists() else 0,
            next_round=next_round,pending_round=pending))
        store.recover()

def convert_archive(raw_files, destination, summary_files=()):
    """Publish a fresh converted archive only when all explicitly selected inputs convert."""
    destination = Path(destination).absolute()
    if destination.exists():
        raise ValueError('conversion destination must not exist')
    inputs, records = [], []
    for name in raw_files:
        path = Path(name).absolute()
        data = path.read_bytes()
        records.extend(read_raw_markdown(data))
        inputs.append(('raw',path,data))
    if not records:
        raise ValueError('select at least one raw archive file')
    normalized = normalize_records(records)
    for name in summary_files:
        path = Path(name).absolute()
        inputs.append(('summary',path,path.read_bytes()))
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.legacy-convert-',dir=destination.parent))
    try:
        store = ArchiveStore(staging)
        _write_records(store,normalized)
        manifest = []
        for kind,path,data in inputs:
            digest = bytes_sha256(data)
            atomic_replace_bytes(staging/'legacy'/ 'objects'/digest,data)
            manifest.append(dict(kind=kind,name=path.name,sha256=digest))
        summaries = []
        if summary_files:
            from legacy_summaries import convert_files
            summaries = convert_files([(p.as_posix(),d) for kind,p,d in inputs if kind=='summary'],normalized)
            for item in summaries:
                atomic_write_json(safe_target(staging,'summaries/'+item['id']+'.json'),item)
                store.index_summary(item)
        atomic_write_json(staging/'legacy'/'conversion.json',dict(
            format='assembly.legacy-conversion.v1',inputs=manifest,
            message_count=len(normalized),summary_count=len(summaries)))
        # rename publishes a complete archive; it never replaces an existing destination.
        os.rename(staging,destination)
        return dict(status='converted',path=str(destination),messages=len(normalized),summaries=len(summaries))
    except Exception as exc:
        # Keep a failed staging directory for recovery; never report it as a completed archive.
        raise RuntimeError(f'conversion failed; unpublished staging retained at {staging}: {exc}') from exc

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--raw',action='append',required=True,help='explicit old raw Markdown file; repeatable')
    parser.add_argument('--summary',action='append',default=[],help='explicit old summary file; repeatable')
    args = parser.parse_args(argv)
    print(json.dumps(convert_archive(args.raw,args.destination,args.summary),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
