"""One-time old wire -> current wire conversion; all input pages are explicit."""
from __future__ import annotations
import argparse
import base64
import json
import os
import tempfile
from pathlib import Path
from exchange import ExchangeService
from legacy import normalize_records
from legacy_protocol import parse_local_bundle
from legacy_summaries import convert_files
from storage import bytes_sha256, canonical_json_bytes

class LegacyBridge:
    def __init__(self, key):
        self.key = key

    def convert_local_packages(self, paths, *, origin, target, selection=None, paginate=False):
        """Explicit offline migration of user-selected files; does not authenticate old authors."""
        return self._convert([Path(p).read_bytes() for p in paths],origin,target,selection,paginate=paginate)

    def convert_envelopes(self, paths, *, origin, target, kind, binary, identity,
                          signing_public_key, selection=None, paginate=False):
        from legacy_crypto import open_legacy_envelope
        kinds = {'bundle':'archive','environment-v1-bundle':'environment',
                 'project-attachment-v1-bundle':'project-attachment'}
        if kind not in kinds:
            raise ValueError('unsupported migration envelope kind')
        data = [open_legacy_envelope(p,binary=binary,identity=identity,
            signing_public_key=signing_public_key,origin=origin,target=target,kind=kind) for p in paths]
        return self._convert(data,origin,target,selection,kinds[kind],paginate)

    def _convert(self, data, origin, target, selection, expected_kind=None, paginate=False):
        if not data:
            raise ValueError('select a complete legacy bundle chain')
        pages = [(parse_local_bundle(blob,expected_kind),bytes_sha256(blob)) for blob in data]
        pages.sort(key=lambda pair:pair[0]['from_event_sequence'])
        cursor,predecessor,events = 0,None,[]
        kind = pages[0][0]['kind']
        for page,digest in pages:
            if page['kind'] != kind or page['origin_node_id'] != origin or page['target_node_id'] != target:
                raise ValueError('legacy stream/origin/target mismatch')
            if page['base_event_sequence'] != cursor or page['previous_bundle_sha256'] != predecessor:
                raise ValueError('legacy pages must be a complete chain beginning at event 1')
            cursor,predecessor = page['to_event_sequence'],digest
            events.extend(page['events'])
        metadata = {'stream':kind,'pages':[dict(bundle_id=p['bundle_id'],sha256=d,
            from_event_sequence=p['from_event_sequence'],to_event_sequence=p['to_event_sequence']) for p,d in pages],
            'events':events}
        exchange = ExchangeService(None,self.key,origin)
        if kind=='archive':
            artifacts = {}
            for event in events:
                identifier = event['artifact_id']
                if identifier in artifacts:
                    raise ValueError('duplicate artifact in legacy chain')
                artifacts[identifier] = event
            raw = normalize_records([e['payload'] for e in events if e['artifact_type']=='raw'])
            files = []
            for index,event in enumerate(events):
                item = event['payload']
                if event['artifact_type']=='summary':
                    files.append((f'v1-{index}.md',item['content'].encode('utf-8')))
                elif event['artifact_type']=='summary-v2':
                    files.extend([(f'v2-{index}/summary.json',base64.b64decode(item['summary_json_base64'],validate=True)),
                                  (f'v2-{index}/summary.md',base64.b64decode(item['summary_markdown_base64'],validate=True)),
                                  (f'v2-{index}/completion.json',canonical_json_bytes(item['completion']))])
            summaries = convert_files(files,raw) if files else []
            if paginate:
                return _archive_pages(exchange,raw,summaries,artifacts)
            return exchange._seal('archive',{'records':raw,'summaries':summaries,
                'legacy_artifacts':artifacts},1,len(raw))
        if not selection:
            raise ValueError('select an exact environment revision or attachment generation')
        if kind=='environment':
            matches = [e for e in events if e.get('revision_id')==selection]
            if len(matches)!=1:
                raise ValueError('selected legacy revision is absent or ambiguous')
            from legacy_protocol import environment_files
            files = environment_files(matches[0])
        elif kind=='project-attachment':
            from legacy_protocol import attachment_files
            files = attachment_files(events,selection)
        else:
            raise ValueError('unsupported legacy stream')
        package = exchange.export_files(files,legacy_artifacts=metadata)
        return [package] if paginate else package

def _archive_pages(exchange, records, summaries, artifacts, budget=32*1024*1024):
    """Raw pages first, then topologically ordered summaries and immutable old events."""
    pages, cursor = [],0
    payload = {'records':[],'summaries':[],'legacy_artifacts':{}}
    size = 0
    def flush():
        nonlocal payload,size,cursor
        if not size:
            return
        end = cursor+len(payload['records'])
        pages.append(exchange._seal('archive',payload,cursor+1,end))
        cursor=end
        payload={'records':[],'summaries':[],'legacy_artifacts':{}}
        size=0
    for field,values in (('records',enumerate(records)),('summaries',enumerate(summaries)),('legacy_artifacts',artifacts.items())):
        flush()
        for key,value in values:
            length=len(canonical_json_bytes(value))+len(str(key).encode('utf-8'))+16
            if size and size+length>budget:
                flush()
            if field=='legacy_artifacts': payload[field][key]=value
            else: payload[field].append(value)
            size+=length
        flush()
    return pages or [exchange._seal('archive',payload,1,0)]

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',action='append',required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument('--output',help='one new package')
    output.add_argument('--output-dir',help='new directory of ordered packages, for large archives')
    parser.add_argument('--origin',required=True); parser.add_argument('--target',required=True)
    parser.add_argument('--new-key-file',required=True,help='new shared HMAC key, raw bytes')
    parser.add_argument('--selection',help='exact old revision or generation ID')
    parser.add_argument('--encrypted-kind',choices=['bundle','environment-v1-bundle','project-attachment-v1-bundle'])
    parser.add_argument('--binary'); parser.add_argument('--identity'); parser.add_argument('--signing-public-key')
    args = parser.parse_args(argv)
    bridge = LegacyBridge(Path(args.new_key_file).read_bytes())
    options = dict(origin=args.origin,target=args.target,selection=args.selection,paginate=bool(args.output_dir))
    if args.encrypted_kind:
        if not all((args.binary,args.identity,args.signing_public_key)):
            parser.error('encrypted input requires binary, identity and trusted signing public key')
        package = bridge.convert_envelopes(args.input,kind=args.encrypted_kind,binary=args.binary,
            identity=args.identity,signing_public_key=args.signing_public_key,**options)
    else:
        package = bridge.convert_local_packages(args.input,**options)
    if args.output_dir:
        target=Path(args.output_dir).absolute()
        if target.exists(): raise ValueError('output directory must not exist')
        target.parent.mkdir(parents=True,exist_ok=True)
        staging=Path(tempfile.mkdtemp(prefix='.legacy-packages-',dir=target.parent))
        for index,data in enumerate(package):
            from storage import atomic_replace_bytes
            atomic_replace_bytes(staging/f'{index+1:06d}.json',data)
        os.rename(staging,target)
        result=dict(status='converted',output=str(target),packages=len(package))
    else:
        with Path(args.output).open('xb') as handle:
            handle.write(package)
            handle.flush(); os.fsync(handle.fileno())
        result=dict(status='converted',output=args.output,bytes=len(package))
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    main()
