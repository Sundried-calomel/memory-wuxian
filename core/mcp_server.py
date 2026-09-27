"""Small stdio MCP adapter for the assembly runtime's read-only query surface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import MemoryRuntime

PROTOCOL_VERSION = "2025-03-26"
MAX_FRAME_BYTES = 65_536
MAX_ID_CHARACTERS = 128
_TOOLS = [
    {
        "name": "memory.query",
        "description": "Bounded provenance-aware memory query, optionally including indexed peers",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "mode": {"enum": ["keyword", "semantic", "hybrid"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "include_peers": {"type": "boolean"},
            },
        },
    },
    {"name": "memory.status", "description": "Read local archive status",
     "inputSchema": {"type": "object", "additionalProperties": False}},
    {"name": "memory.context", "description": "Read a bounded conversation context",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["conversation_id"],
                     "properties": {"conversation_id": {"type": "string", "minLength": 1, "maxLength": 512},
                                    "max_characters": {"type": "integer", "minimum": 1, "maximum": 40000}}}},
    {"name": "memory.source", "description": "Read one verified local raw message or completed summary by ID",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["id"],
                     "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 512},
                                    "origin": {"type": "string", "minLength": 1, "maxLength": 128},
                                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                    "offset": {"type": "integer", "minimum": 0},
                                    "length": {"type": "integer", "minimum": 1, "maximum": 8000}}}},
]


def _response(value: dict) -> dict:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_FRAME_BYTES:
        return value
    return {"jsonrpc": "2.0", "id": value.get("id"),
            "error": {"code": -32603, "message": "bounded response exceeds MCP frame limit"}}


def dispatch(runtime: MemoryRuntime, request: Any, session: dict[str, bool] | None = None):
    request_id = request.get("id") if isinstance(request, dict) else None
    valid_id = request_id is None or (type(request_id) is int) or (isinstance(request_id, str) and len(request_id) <= MAX_ID_CHARACTERS)
    if not isinstance(request, dict) or set(request) - {"jsonrpc", "id", "method", "params"}:
        return _response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
    if not valid_id:
        return _response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request id"}})
    method = request.get("method")
    if request.get("jsonrpc") != "2.0" or method not in {"initialize", "notifications/initialized", "tools/list", "tools/call"}:
        return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
    if method == "initialize":
        params = request.get("params")
        if request_id is None or not isinstance(params, dict) or not isinstance(params.get("protocolVersion"), str):
            return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unsupported initialization"}})
        if session is not None:
            session["initialize_seen"] = True
        return _response({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "memory-wuxian-assembly-readonly", "version": "1"}}})
    if method == "notifications/initialized":
        if session is not None and session.get("initialize_seen") and request_id is None:
            session["initialized"] = True
        return None
    if session is not None and not session.get("initialized"):
        return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32002, "message": "server is not initialized"}})
    if method == "tools/list":
        return _response({"jsonrpc": "2.0", "id": request_id, "result": {"tools": _TOOLS}})
    params = request.get("params", {})
    if not isinstance(params, dict) or set(params) - {"name", "arguments"} or not isinstance(params.get("name"), str):
        return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "invalid tool call"}})
    name, args = params["name"], params.get("arguments", {})
    if not isinstance(args, dict):
        return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "tool arguments must be an object"}})
    try:
        if name == "memory.query":
            if set(args) - {"query", "mode", "limit", "include_peers"}:
                raise ValueError("unknown memory.query argument")
            result = runtime.query.query(args.get("query"), args.get("limit", 20), args.get("mode", "keyword"), args.get('include_peers', True))
            while len(json.dumps(result,ensure_ascii=False).encode('utf-8'))>50000 and result['results']:
                result['results'].pop()
                result['response_truncated']=True
        elif name == "memory.status":
            if args:
                raise ValueError("memory.status takes no arguments")
            result = runtime.status()
        elif name == "memory.context":
            if set(args) - {"conversation_id", "max_characters"}:
                raise ValueError("unknown memory.context argument")
            result = runtime.query.context(args.get("conversation_id"), args.get("max_characters", 12000))
        elif name == "memory.source":
            if set(args)-{'id','offset','length','origin','sha256'} or not isinstance(args.get('id'), str) or not args['id'] or len(args['id']) > 512:
                raise ValueError("memory.source requires one bounded id")
            identifier = args["id"]
            origin = args.get('origin', 'local')
            if not isinstance(origin, str) or not 1 <= len(origin) <= 128:
                raise ValueError('invalid source origin')
            if 'sha256' in args and (not isinstance(args['sha256'], str) or len(args['sha256']) != 64 or any(c not in '0123456789abcdef' for c in args['sha256'])):
                raise ValueError('invalid source hash')
            if origin != 'local':
                from peer_bridge import PeerIndex
                result = PeerIndex(runtime.store.root).source(origin, identifier, args.get('sha256'))
            else:
                result = runtime.store.message_by_id(identifier) or runtime.store.summary_by_id(identifier)
            if result is None:
                raise KeyError("source id was not found")
            if origin == 'local' and args.get('sha256') is not None and args['sha256'] not in {result.get('content_sha256'), result.get('summary_sha256')}:
                raise ValueError('local source hash mismatch')
            if origin == 'local' and result.get('conversation_id') in runtime.store.excluded_conversations():
                raise ValueError('internal task is excluded from user memory')
            offset,length=args.get('offset',0),args.get('length',8000)
            if type(offset) is not int or offset<0 or type(length) is not int or not 1<=length<=8000:
                raise ValueError('invalid source text range')
            result={key:value for key,value in result.items() if key in {
                'message_id','id','conversation_id','sequence','speaker','timestamp','round_number',
                'content_sha256','summary_sha256','level','text','origin','qualified_id',
                'peer_record_sha256','read_only_replica','provenance'}}
            full=result.get('text','')
            result.update(text=full[offset:offset+length],text_length=len(full),offset=offset,
                          next_offset=offset+length if offset+length<len(full) else None)
        else:
            return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unknown tool"}})
    except KeyError as exc:
        return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32004, "message": str(exc).strip("'")}})
    except (ValueError, RuntimeError, OSError, TypeError) as exc:
        return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})
    return _response({"jsonrpc": "2.0", "id": request_id, "result": {
        "content": [{"type": "text", "text": f"{len(result.get('results', []))} bounded read-only results" if name == "memory.query" else f"{name} completed"}],
        "structuredContent": result, "isError": False}})


def serve(root, input_stream=None, output_stream=None) -> None:
    """Serve one newline-delimited JSON-RPC stdio session over an existing archive root."""
    runtime = MemoryRuntime(root)
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    if input_stream is sys.stdin and hasattr(input_stream, "reconfigure"):
        input_stream.reconfigure(encoding="utf-8", errors="strict")
    if output_stream is sys.stdout and hasattr(output_stream, "reconfigure"):
        output_stream.reconfigure(encoding="utf-8", errors="strict")
    session = {"initialize_seen": False, "initialized": False}
    while True:
        line = input_stream.readline(MAX_FRAME_BYTES + 2)
        if not line:
            return
        if len(line.encode("utf-8")) > MAX_FRAME_BYTES or not line.endswith("\n"):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "frame too large or unterminated"}}
            output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            output_stream.flush()
            return
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = dispatch(runtime, request, session)
        if response is not None:
            output_stream.write(json.dumps(_response(response), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            output_stream.flush()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only stdio MCP adapter for an assembly archive")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    serve(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
