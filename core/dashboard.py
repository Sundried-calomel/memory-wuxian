"""Loopback adapter for the copied legacy dashboard page; no legacy runtime imports."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from runtime import MemoryRuntime

MAX_PATH_CHARS = 8192
MAX_BODY_BYTES = 65_536


def status_payload(runtime: MemoryRuntime, config: dict | None = None) -> dict:
    """Return only SQL-backed archive counts; unsupported observations stay null/unknown."""
    excluded=runtime.store.excluded_conversations()
    scope='conversation NOT IN ('+','.join('?' for _ in excluded)+')' if excluded else '1=1'
    with runtime.store.connection() as db:
        row = db.execute("SELECT COUNT(*), COUNT(DISTINCT conversation), COALESCE(SUM(CASE WHEN speaker='tool' THEN 1 ELSE 0 END),0), COALESCE(SUM(length(text)),0), MAX(timestamp) FROM messages WHERE "+scope,list(excluded)).fetchone()
        pending = db.execute("SELECT COUNT(*) FROM rounds WHERE pending IS NOT NULL AND "+scope,list(excluded)).fetchone()[0]
        summary_total = db.execute("SELECT COUNT(*) FROM summary_index WHERE "+scope,list(excluded)).fetchone()[0]
        daily_rows = db.execute("SELECT substr(timestamp,1,10), COUNT(*), COALESCE(SUM(length(text)),0) FROM messages WHERE timestamp IS NOT NULL AND length(timestamp)>=10 AND "+scope+" GROUP BY substr(timestamp,1,10) ORDER BY substr(timestamp,1,10)",list(excluded)).fetchall()
        conversation_rows = db.execute(
            "SELECT conversation,COUNT(*),SUM(CASE WHEN speaker='tool' THEN 1 ELSE 0 END),MAX(timestamp) "
            "FROM messages WHERE "+scope+" GROUP BY conversation ORDER BY MAX(timestamp) DESC",
            list(excluded)).fetchall()
    live = _live_status(runtime.store.root)
    daily = [{"date": day, "messages": count, "all_devices": {"messages": count},
              "local": {"messages": count}, "characters": characters,
              "devices": [{"display_name": "local", "local": True, "messages": count,
                           "characters": characters}]} for day, count, characters in daily_rows]
    unknown_levels = {str(level): None for level in range(1, 9)}
    # This index contains no title, lifecycle, telemetry, or per-level summary
    # metadata. List available conversations without inventing those facts.
    conversations = [{"conversation_id": identifier, "title": identifier,
                      "project": None, "source_kind": None, "origin_node": "local",
                      "message_count": count, "tool_activity_count": tools,
                      "last_message_at": latest, "telemetry": None,
                      "estimated_archive_tokens": None, "completed_rounds": None,
                      "summary_counts": dict(unknown_levels), "archived": None}
                     for identifier, count, tools, latest in conversation_rows]
    unknown_debts = {name: {"state": "unavailable", "count": None, "in_progress": None, "retry": None,
                            "quarantined": None, "permanent_failures": None}
                     for name in ("coverage_debt", "mechanical_debt", "semantic_debt", "backup_debt")}
    return {
        "schema_version": 1, "health": "unavailable",
        "conversations": conversations, "active_conversations": conversations,
        "archived_conversations": [], "conversation_lifecycle_known": False,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "totals": {
            "active_conversations": row[1], "archived_conversations": None,
            "messages": row[0], "tool_activities": row[2],
            "summary_counts": unknown_levels, "reported_total_tokens": None,
            "archived_days": len(daily_rows), "characters": row[3], "storage_bytes": None,
            "estimated_tokens": None, "message_estimated_tokens": None,
        },
        "daily": daily, "daily_metrics": {"complete_token_coverage": False, "devices_included": 1, "stale_devices": []},
        "collector": {"mode": "unavailable", "live_status": live},
        "debt_status": {"debts": unknown_debts},
        "pending_rounds": pending,
        "summary_total": summary_total,
        "sync": live.get("sync"),
        "capabilities": {"collector": "unconfigured", "federation": "unconfigured",
                         "environment_registry": "unavailable", "backup_health": "unavailable"},
    }


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
                ("status", "completed_at", "collection", "backup", "sync", "auto_summary")}
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
                    result = {"schema_version": 1, "version": "assembly-six-paths candidate (unreleased)",
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
    runtime = MemoryRuntime(args.root)
    with make_server(runtime, host=args.host, port=args.port,
                     config=config) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
