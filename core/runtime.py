"""Directly wired library and local CLI; no installed-product imports."""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from archive import ArchiveStore
from backup import BackupService
from environment import EnvironmentService
from exchange import ExchangeService
from publish import PackageService
from query import QueryService
from summary import SummaryService, CodexCLIModel


class MemoryRuntime:
    def __init__(self, root, *, model=None, encoder=None, encoder_id=None, key=None, node_id=None):
        self.store = ArchiveStore(root)
        self.summary = SummaryService(self.store, model=model)
        self.query = QueryService(self.store, encoder=encoder, encoder_id=encoder_id)
        self.backup = BackupService(self.store)
        self.exchange = ExchangeService(self.store, key, node_id) if key is not None else None
        self.package = PackageService(self.exchange) if self.exchange else None

    def status(self):
        result = self.store.status()
        with self.store.connection() as db:
            result['pending_rounds'] = db.execute('SELECT COUNT(*) FROM rounds WHERE pending IS NOT NULL').fetchone()[0]
        result['summary_count'] = sum(1 for _ in (self.store.root/'summaries').glob('sum-*.json'))
        result['model_configured'] = self.summary.model is not None
        return result

    def summarize_due(self, conversation_id, minimum_rounds=20, after_sequence=0):
        if type(minimum_rounds) is not int or minimum_rounds < 1:
            raise ValueError('minimum_rounds must be positive')
        records = self.store.records(conversation_id,after_sequence=after_sequence)
        covered = {mid for item in self.summary.list(conversation_id)
                   for mid in (item.get('input_source_ids', item['raw_source_ids']) if item['level']==1 else item['raw_source_ids'])}
        completed = {r['round_number'] for r in records if r['completes_round']}
        selected = [r for r in records if r['message_id'] not in covered and r['round_number'] in completed]
        if len({r['round_number'] for r in selected}) < minimum_rounds:
            return {'status': 'below-threshold'}
        return self.summary.generate(conversation_id, source_ids=[r['message_id'] for r in selected])

    def receive_environment(self, package, environment_root, binding, *, expected_origin=None, dependency_check=None):
        if self.exchange is None:
            raise ValueError('configure exchange key and node identity')
        verified = self.exchange.receive(package, expected_origin=expected_origin, expected_kind='environment')
        if verified['status'] != 'verified-files':
            raise ValueError('expected files')
        service = EnvironmentService(environment_root)
        # A whole-file binding accepts its local name, not a remote destination.
        state = service._read()['bindings'][binding]
        files = verified['files']
        if state['strategy'] in {'whole-file', 'managed-block'}:
            if len(files) != 1:
                raise ValueError('this binding accepts one file')
            files = {'': next(iter(files.values()))}
        return service.apply(binding, files, dependency_check=dependency_check)

    def summarize_parents_due(self, conversation_id, minimum_children=10, maximum_level=8):
        made=[]
        for level in range(1, maximum_level):
            summaries=self.summary.list(conversation_id)
            consumed={ref for item in summaries for ref in item.get('input_source_ids',item['source_refs'])}
            ready=[item for item in summaries if item['level']==level and item['id'] not in consumed
                   and not item.get('legacy_format')]
            for start in range(0,len(ready)-minimum_children+1,minimum_children):
                made.append(self.summary.generate(conversation_id,children=ready[start:start+minimum_children]))
        return made

    def read_server(self, port=0):
        runtime = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                host = self.headers.get('Host', '')
                allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
                if host not in allowed or len(self.path) > 8192:
                    self.send_error(400); return
                request = urlsplit(self.path)
                args = parse_qs(request.query)
                try:
                    if any(len(v) != 1 for v in args.values()):
                        raise ValueError('parameters must occur once')
                    if request.path == '/api/status': result = runtime.status()
                    elif request.path == '/api/query':
                        result = runtime.query.query(args.get('q',[''])[0],int(args.get('limit',['20'])[0]))
                    elif request.path == '/api/context':
                        result = runtime.query.context(args.get('conversation',[''])[0])
                    elif request.path == '/api/source':
                        identifier = args.get('id',[''])[0]
                        result = runtime.store.message_by_id(identifier) or runtime.store.summary_by_id(identifier)
                        if result is None: self.send_error(404); return
                    else: self.send_error(404); return
                    status = 200
                except (ValueError, RuntimeError, OSError) as exc:
                    result, status = {'error':str(exc)}, 400
                payload = json.dumps(result,ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Content-Length',str(len(payload)))
                self.send_header('Cache-Control','no-store')
                self.end_headers(); self.wfile.write(payload)
        return ThreadingHTTPServer(('127.0.0.1',port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Assembly working runtime; use a new archive directory.')
    parser.add_argument('--root', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    append = sub.add_parser('append')
    append.add_argument('--conversation', required=True)
    append.add_argument('--speaker', choices=['user','assistant','tool','system'], required=True)
    append.add_argument('--text', required=True)
    append.add_argument('--id', required=True)
    sub.add_parser('status')
    query = sub.add_parser('query'); query.add_argument('text'); query.add_argument('--limit', type=int, default=20)
    context = sub.add_parser('context'); context.add_argument('conversation')
    summary = sub.add_parser('summarize'); summary.add_argument('conversation')
    summary.add_argument('--codex', required=True); summary.add_argument('--model', required=True)
    backup = sub.add_parser('backup'); backup.add_argument('destination')
    serve = sub.add_parser('serve'); serve.add_argument('--port',type=int,default=8765)
    collect = sub.add_parser('collect'); collect.add_argument('session_file')
    watch = sub.add_parser('watch'); watch.add_argument('session_file'); watch.add_argument('--interval',type=float,default=2)
    watch.add_argument('--codex'); watch.add_argument('--model'); watch.add_argument('--summary-rounds',type=int,default=20)
    args = parser.parse_args(argv)
    if args.command=='watch' and bool(args.codex)!=bool(args.model):
        raise ValueError('supply both --codex and --model to enable automatic summaries')
    model = CodexCLIModel(args.codex,args.model) if args.command=='summarize' or (args.command=='watch' and args.model) else None
    runtime = MemoryRuntime(args.root,model=model)
    if args.command=='serve':
        with runtime.read_server(args.port) as server:
            server.serve_forever()
        return 0
    if args.command=='watch':
        if args.interval<0.1:
            raise ValueError('interval must be at least 0.1 seconds')
        while True:
            result = runtime.store.collect_session(args.session_file)
            if result['appended']:
                print(json.dumps(result,ensure_ascii=False),flush=True)
                if model:
                    for conversation in result['conversations']:
                        runtime.summarize_due(conversation,args.summary_rounds)
            time.sleep(args.interval)
    if args.command=='append':
        result = runtime.store.append_message(args.speaker,args.text,conversation_id=args.conversation,message_id=args.id)
    elif args.command=='status': result = runtime.status()
    elif args.command=='query': result = runtime.query.query(args.text,args.limit)
    elif args.command=='context': result = runtime.query.context(args.conversation)
    elif args.command=='summarize': result = runtime.summary.generate(args.conversation)
    elif args.command=='collect': result = runtime.store.collect_session(args.session_file)
    else: result = runtime.backup.create(args.destination)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
