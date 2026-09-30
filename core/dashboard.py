"""Loopback adapter for the copied legacy dashboard page; no legacy runtime imports."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from runtime import MemoryRuntime
from dashboard_data import DashboardData, thread_metadata, process_observation, archive_bytes

MAX_PATH_CHARS = 8192
MAX_BODY_BYTES = 65_536


def status_payload(runtime: MemoryRuntime, config: dict | None = None) -> dict:
    """Observe current sources without collecting, summarizing or modifying history."""
    if not hasattr(runtime, '_dashboard_data'):
        runtime._dashboard_data = DashboardData()
    data = runtime._dashboard_data
    config = dict(config or {}, root=str(runtime.store.root))
    with data.lock:
        cached = getattr(data, 'payload', None)
        if cached and time.monotonic() - data.built_at < 5:
            return cached
        excluded = runtime.store.excluded_conversations()
        metrics = data.message_metrics(runtime.store, excluded)
        levels, conversation_levels = data.summary_metrics(runtime.store, excluded)
        metadata, metadata_source = thread_metadata(config)
        usage, daily_usage = data.usage(config)
        with runtime.store.connection() as db:
            rounds = {row[0]: (row[1] - 1 - int(row[2] is not None), row[2])
                      for row in db.execute('SELECT conversation,next_round,pending FROM rounds')}
        conversations, totals, days = [], Counter(), {}
        for identifier, item in metrics.items():
            info = metadata.get(identifier, {})
            telemetry = dict(usage[identifier]) if identifier in usage else None
            if telemetry:
                telemetry['historical'] = not telemetry['updated_at'] or dt.datetime.fromisoformat(telemetry['updated_at'].replace('Z', '+00:00')) < dt.datetime.fromisoformat(item['latest'])
            conversations.append(dict(conversation_id=identifier, title=info.get('title') or identifier,
                project=info.get('project'), archived=info.get('archived'), origin_node='local',
                source_kind='codex' if identifier.startswith('codex:') else 'imported',
                message_count=item['messages'], tool_activity_count=item['tools'],
                last_message_at=item['latest'], estimated_archive_tokens=item['estimated_tokens'],
                completed_rounds=len(data.completed[identifier]), telemetry=telemetry,
                summary_counts=dict(conversation_levels.get(identifier, {}))))
            for name in ('messages', 'tools', 'characters', 'estimated_tokens', 'message_tokens'):
                totals[name] += item[name]
        for (identifier, day), item in data.daily.items():
            if identifier not in excluded:
                days.setdefault(day, Counter()).update(item)
        for (identifier, day), count in daily_usage.items():
            if identifier in metrics:
                days.setdefault(day, Counter())['reported_tokens'] += count
        daily = [dict(date=day, **item, all_devices=dict(item), local=dict(item),
                      devices=[dict(display_name='local', local=True, **item)])
                 for day, item in sorted(days.items())]
        conversations.sort(key=lambda item: item['last_message_at'], reverse=True)
        active = [item for item in conversations if item['archived'] is not True]
        archived = [item for item in conversations if item['archived'] is True]
        live = _live_status(runtime.store.root)
        process = process_observation(live.get('pid')) if live.get('status') == 'running' else {'process_running': False}
        mode = ('active' if process.get('process_running') else 'idle') if live.get('attempt_at') else 'not-started'
        if mode == 'idle':
            process.update(cpu_percent=0, memory_bytes=0)
        collection = live.get('collection') or {}
        errors = len(collection.get('errors') or {})
        summary_errors = len(live.get('summary_errors') or {})
        backup_pending = live.get('backup_pending')
        summary_progress = live.get('summary_progress') or {}
        debts = {
            'coverage_debt': dict(state='attention' if errors else 'ok' if collection else 'unknown',
                count=collection.get('pending_files'), quarantined=errors),
            'semantic_debt': dict(state='attention' if summary_errors else 'running' if live.get('phase') == 'summaries' and mode == 'active' else 'ok' if live.get('completed_at') else 'unknown',
                count=summary_progress.get('remaining'), in_progress=int(mode == 'active' and live.get('phase') == 'summaries'),
                retry=summary_errors, unit='conversations-to-check'),
            'backup_debt': dict(state='pending' if backup_pending else 'ok' if live.get('backup') else 'unknown',
                count=int(backup_pending) if backup_pending is not None else None),
        }
        sync = live.get('sync') or {}
        health = ('error' if live.get('status') == 'error' else
                  'attention' if errors or summary_errors or sync.get('status') == 'error' or
                  (live.get('environment') or {}).get('state') in ('error', 'partial') else
                  'catching-up' if mode == 'active' or collection.get('pending_files') or backup_pending else
                  'ok' if live.get('completed_at') else 'unavailable')
        observed = [item['telemetry'] for item in conversations if item['telemetry']]
        payload = dict(schema_version=1, health=health, conversations=conversations,
            active_conversations=active, archived_conversations=archived,
            conversation_lifecycle_known=all(item['archived'] is not None for item in conversations),
            metadata_source=metadata_source, generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            totals=dict(active_conversations=len(active), archived_conversations=len(archived),
                messages=totals['messages'], tool_activities=totals['tools'], summary_counts=levels,
                reported_total_tokens=sum(item['reported_total_tokens'] for item in observed) if observed else None,
                archived_days=len(days), characters=totals['characters'], storage_bytes=archive_bytes(runtime.store.root),
                estimated_tokens=totals['estimated_tokens'], message_estimated_tokens=totals['message_tokens']),
            token_usage=dict(source='persisted-token-count-ledgers', historical=any(item['historical'] for item in observed),
                covered_conversations=len(observed), total_conversations=len(conversations),
                updated_at=max((item['updated_at'] for item in observed), default=None)),
            daily=daily, daily_metrics=dict(complete_token_coverage=bool(observed) and len(observed) == len(conversations)
                and not any(item['historical'] for item in observed), devices_included=1, stale_devices=[]),
            collector=dict(mode=mode, live_status=live, **process,
                fallback_interval_seconds=config.get('interval_seconds'),
                last_file_event=max((item['last_message_at'] for item in conversations), default=None),
                last_archive_update=live.get('collection_completed_at') or live.get('completed_at'),
                wakeups_last_hour=sum((dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(stamp)).total_seconds()<3600
                    for stamp in live['attempts_last_hour']) if isinstance(live.get('attempts_last_hour'), list) else None),
            debt_status=dict(debts=debts), pending_rounds=sum(value[1] is not None for key, value in rounds.items() if key not in excluded),
            summary_total=sum(levels.values()), sync=sync,
            capabilities=dict(collector='direct-codex' if config.get('sessions_root') else 'unconfigured',
                federation='core-v1' if config.get('sync') else 'unconfigured',
                environment_registry='configured' if config.get('environment') else 'unconfigured',
                backup_health=(live.get('backup') or {}).get('status', 'not-yet-observed')))
        data.payload, data.built_at = payload, time.monotonic()
        return payload


def _live_status(root: Path) -> dict:
    """Read the one tick's published status file; never initiate collection or health checks."""
    path = root / "live-status.json"
    try:
        if not path.is_file() or path.stat().st_size > 65_536:
            return {"status": None, "completed_at": None, "collection": None,
                    "backup": None, "sync": None, "auto_summary": None}
        value = json.loads(path.read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status must be an object")
        # Allowlist operational facts only. Do not expose source paths, config, or error text.
        return {key: value.get(key) for key in
                ("status", "attempt_at", "completed_at", "collection_completed_at", "pid", "phase",
                 "collection", "backup", "backup_pending", "sync", "auto_summary", "summary_errors",
                 "summary_progress", "attempts_last_hour", "environment", "error")}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {"status": None, "completed_at": None, "collection": None,
                "backup": None, "sync": None, "auto_summary": None}


def search_payload(runtime: MemoryRuntime, query: str, mode: str, limit: int) -> dict:
    if not query or len(query) > 500 or mode not in {"keyword", "semantic", "hybrid"} or not 1 <= limit <= 50:
        raise ValueError("invalid query, mode or limit")
    found = runtime.query.query(query, limit=limit, mode=mode, include_peers=True)
    rows = []
    for result in found["results"]:
        provenance = result.get("provenance") or {}
        rows.append({**result,
                     "raw_path": provenance.get("path"),
                     "raw_line_start": None, "raw_line_end": None,
                     "record_sha256": provenance.get("sha256", result.get("summary_sha256"))})
    verified = all(item.get("record_type") != "summary" and item.get("origin") == "local" for item in rows)
    return {"query": query, "mode": mode, "count": len(rows), "results": rows,
            "verified_against_raw": verified, "semantic_provider": "unconfigured",
            "warnings": found.get("warnings", [])}


def _devices_payload(runtime, config) -> dict:
    sync=(config or {}).get('sync')
    if not sync:
        return {'enabled':False,'devices':[],'cloud':{'enabled':False,'configured':False}}
    from core_sync import CoreSyncService
    facts=CoreSyncService(runtime.store,**sync).status()
    pending = bool(facts.get('pending_ack') or facts.get('export_remaining')
                   or facts.get('pending_file_ack_count'))
    # The legacy page tests pending_since for truthiness, but core status has
    # no onset timestamp. True is a compatibility flag, never a fabricated date.
    schedule = {'pending_since': True if pending else None,
                'pending_since_known': False, 'last_attempt_at': None,
                'delivery': facts.get('delivery'), 'pending_ack': facts.get('pending_ack')}
    peer={'node_id':sync['peer_id'],'display_name':(config or {}).get('peer_display_name',sync['peer_id']),
          'trusted':True,'cloud_ready':True,'read_only_replica':True,
          'last_event_sequence':facts.get('peer_cursor')}
    return {'node':{'node_id':sync['local_node_id'],'display_name':(config or {}).get('display_name',sync['local_node_id'])},
            'enabled':True,'protocol_version':'core-v1','devices':[peer],
            'cloud':{'enabled':True,'configured':True,'encrypted':True,'identity_ready':True,
                     'exchange_root':sync['exchange_root'],'schedule':schedule,
                     'peers':[dict(peer,acknowledged={'last_event_sequence':facts.get('acknowledged_sequence')},
                                   outstanding=facts.get('pending_ack'))],
                     'streams':{},'core_status':facts},'recent_sync':[]}


def _environment_payload(runtime, config) -> dict:
    path=runtime.store.root/'environment-status.json'
    if path.exists():
        facts=json.loads(path.read_text('utf-8'))
        published=facts.get('published',[]);received=facts.get('received',[])
        return {'initialized':True,'validation_status':facts.get('state'),'object_classes':{
                      'global-rule':{'count':sum(x.get('selection')=='global-codex-agents' for x in published)},
                      'global-skill':{'count':sum(x.get('selection')=='memory-wuxian-core' for x in published)},
                      'project-rule':{'count':0},'project-skill':{'count':0}},
                'projects':[],'artifacts':[{'artifact_id':x.get('selection'),
                      'display_name':x.get('selection'),'revision_id':x.get('revision_id'),
                      'object_class':'global-rule' if x.get('selection')=='global-codex-agents' else 'global-skill',
                    'state':x.get('state')} for x in published],
                'conflicts':[x for x in received if x.get('state')=='error'],
                'installations':[x for x in received if x.get('acknowledged')],
                'recent_results':received,'incoming':{'staged_events':sum(not x.get('acknowledged',False) for x in received),
                    'effect':'explicit-bindings-only'},'core_status':facts}
    return {'initialized':False,'object_classes':{},'projects':[],'artifacts':[],
            'conflicts':[],'installations':[],'recent_results':[],
            'incoming':{'staged_events':0,'effect':'explicit-bindings-only'}}


def _read_live_config(path: Path) -> dict:
    if path.stat().st_size > 65_536:
        raise ValueError("live config exceeds size limit")
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("live config must be a JSON object")
    return value


def make_server(runtime: MemoryRuntime, *, host="127.0.0.1", port=8765, html_path=None,
                config: dict | None = None):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard must bind to loopback")
    page = Path(html_path) if html_path else Path(__file__).with_name("dashboard.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _host_ok(self):
            return self.headers.get("Host", "") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def _json(self, status, value):
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._host_ok() or len(self.path) > MAX_PATH_CHARS:
                self._json(400, {"error": "invalid loopback host or request path"})
                return
            request = urlsplit(self.path)
            params = parse_qs(request.query, keep_blank_values=True)
            if any(len(values) != 1 for values in params.values()):
                self._json(400, {"error": "parameters must occur once"})
                return
            try:
                if request.path in {"/", "/index.html", "/dashboard.html"}:
                    body = page.read_bytes()
                    # Keep the legacy layout, but make unsupported write actions inert.
                    disable_script = (b'<script>(()=>{const lock=()=>{document.querySelectorAll("[data-cloud-toggle],[data-chatgpt-import],[data-chatgpt-file],#environment-profile-compare,#evidence-owners-refresh").forEach(e=>{e.disabled=true;e.title="Not available in this candidate"});document.querySelectorAll(".attachment-metric .attachment-stage").forEach(e=>{const u=typeof unavailable==="function"?unavailable():"Unavailable";const b=e.querySelector("b"),s=e.querySelector("small");if(b&&b.textContent!==u)b.textContent=u;if(s&&s.textContent!==u)s.textContent=u})};document.addEventListener("DOMContentLoaded",lock);new MutationObserver(lock).observe(document.documentElement,{childList:true,subtree:true})})();</script>')
                    body = body.replace(b"</body>", disable_script + b"</body>")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if request.path == "/api/status":
                    result = status_payload(runtime, config)
                elif request.path == "/api/memory-search":
                    result = search_payload(runtime, params.get("q", [""])[0],
                                            params.get("mode", ["hybrid"])[0],
                                            int(params.get("limit", ["20"])[0]))
                elif request.path == "/api/devices":
                    result = _devices_payload(runtime, config)
                elif request.path == "/api/environment":
                    result = _environment_payload(runtime, config)
                elif request.path == "/api/system":
                    result = {"schema_version": 1, "version": Path(__file__).resolve().parent.parent.joinpath('VERSION').read_text().strip(),
                              "platform": platform.system() or None, "python": sys.version.split()[0],
                              "archive_root": str(runtime.store.root), "health_scan": "not-performed"}
                elif request.path == "/api/environment-profile":
                    self._json(503, {"error": "Environment profile comparison is not configured"})
                    return
                else:
                    self._json(404, {"error": "not found"})
                    return
                self._json(200, result)
            except (ValueError, RuntimeError, OSError) as exc:
                self._json(400, {"error": str(exc)})

        def do_POST(self):
            if not self._host_ok() or len(self.path) > MAX_PATH_CHARS:
                self._json(400, {"error": "invalid loopback host or request path"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = MAX_BODY_BYTES + 1
            if length < 0 or length > MAX_BODY_BYTES:
                self._json(413, {"error": "request body exceeds limit"})
                return
            body=self.rfile.read(length)
            path = urlsplit(self.path).path
            if path=='/api/cloud':
                origin=self.headers.get('Origin')
                if origin!='http://'+self.headers.get('Host','') or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    self._json(403,{'error':'same-origin JSON required'}); return
                try:
                    if json.loads(body)!={'action':'sync'} or not (config or {}).get('sync'):
                        raise ValueError('only configured core synchronization is supported')
                    from core_sync import CoreSyncService
                    from storage import exclusive_lock
                    with exclusive_lock(runtime.store.root/'.live-tick.lock'):
                        result=CoreSyncService(runtime.store,**config['sync']).sync_once()
                    self._json(200,{'result':result,'devices':_devices_payload(runtime,config)})
                except (ValueError,RuntimeError,OSError) as exc:
                    self._json(400,{'error':str(exc)})
                return
            if path=='/api/environment':
                if self.headers.get('Origin')!='http://'+self.headers.get('Host','') or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    self._json(403,{'error':'same-origin JSON required'}); return
                try:
                    if json.loads(body)!={'action':'process-incoming'} or not (config or {}).get('sync'):
                        raise ValueError('only configured incoming processing is supported')
                    from core_sync import CoreSyncService
                    from live import sync_environment
                    from storage import exclusive_lock
                    with exclusive_lock(runtime.store.root/'.live-tick.lock'):
                        result=sync_environment(runtime,CoreSyncService(runtime.store,**config['sync']),config)
                    self._json(200,{'result':result,'environment':_environment_payload(runtime,config)})
                except (ValueError,RuntimeError,OSError) as exc:
                    self._json(400,{'error':str(exc)})
                return
            self._json(503, {"error": "this candidate exposes no dashboard write actions" if path.startswith("/api/") else "not found"})

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Loopback dashboard adapter for an assembly archive")
    parser.add_argument("--root", required=True)
    parser.add_argument("--config", required=True, help="device-local core configuration")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    config = _read_live_config(Path(args.config).expanduser().resolve())
    product_root=Path(__file__).resolve().parent.parent
    adapter=product_root/'installer/dashboard.py'
    if adapter.is_file():
        from configure import register_bundled_installation
        register_bundled_installation(product_root)
        import importlib.util
        spec=importlib.util.spec_from_file_location('bundled_dashboard',adapter)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        server=module.integrated_server(product_root,args.root,config,port=args.port)
    else:
        server=make_server(MemoryRuntime(args.root),host=args.host,port=args.port,config=config)
    with server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
