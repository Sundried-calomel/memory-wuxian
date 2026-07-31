#!/usr/bin/env python3
"""Minimal allow-listed MCP-compatible JSON-RPC adapter for read-only memory."""

from __future__ import annotations

import json
from typing import Any, Optional

from memory_readonly_service import ReadOnlyMemoryService, ReadRequestError, error_payload


ALLOWED_METHODS = {"initialize", "notifications/initialized", "tools/list", "tools/call"}
MAX_FRAME_BYTES = 65_536
MAX_ID_CHARACTERS = 128
PROTOCOL_VERSION = "2025-03-26"


def bounded_response(response: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= MAX_FRAME_BYTES:
        return response
    return {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": "bounded response exceeds MCP frame limit"}}


def dispatch(
    service: ReadOnlyMemoryService,
    request: Any,
    session: Optional[dict[str, bool]] = None,
) -> Optional[dict[str, Any]]:
    request_id = request.get("id") if isinstance(request, dict) else None
    valid_id = (
        request_id is None
        or (isinstance(request_id, int) and not isinstance(request_id, bool))
        or (isinstance(request_id, str) and len(request_id) <= MAX_ID_CHARACTERS)
    )
    if not isinstance(request, dict) or set(request) - {"jsonrpc", "id", "method", "params"}:
        return bounded_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
    if not valid_id:
        return bounded_response({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request id"}})
    if request.get("jsonrpc") != "2.0" or request.get("method") not in ALLOWED_METHODS:
        return bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
    method = request["method"]
    if method == "initialize":
        if session is not None:
            params = request.get("params")
            if (
                request_id is None
                or not isinstance(params, dict)
                or params.get("protocolVersion") != PROTOCOL_VERSION
            ):
                return bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "unsupported initialization"}})
            session["initialize_seen"] = True
        return bounded_response({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "memory-wuxian-readonly", "version": "1"},
        }})
    if method == "notifications/initialized":
        if session is not None and session.get("initialize_seen") and request_id is None:
            session["initialized"] = True
        return None
    if session is not None and not session.get("initialized"):
        return bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32002, "message": "server is not initialized"}})
    if method == "tools/list":
        return bounded_response({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{
            "name": "memory.query",
            "description": "Bounded provenance-aware read-only local memory query",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "mode": {"enum": ["keyword", "semantic", "hybrid"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
            },
        }]}})
    params = request.get("params", {})
    if not isinstance(params, dict) or params.get("name") != "memory.query" or set(params) - {"name", "arguments"}:
        return bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "invalid tool call"}})
    try:
        result = service.query(params.get("arguments", {}))
    except ReadRequestError as exc:
        return bounded_response({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc), "data": error_payload(exc)}})
    response = {"jsonrpc": "2.0", "id": request_id, "result": {
        "content": [{"type": "text", "text": f"{result['count']} bounded read-only results"}],
        "structuredContent": result,
        "isError": False,
    }}
    return bounded_response(response)


def serve_lines(service: ReadOnlyMemoryService, input_stream, output_stream) -> None:
    session = {"initialize_seen": False, "initialized": False}
    while True:
        line = input_stream.readline(MAX_FRAME_BYTES + 1)
        if not line:
            break
        if len(line.encode("utf-8")) > MAX_FRAME_BYTES or not line.endswith("\n"):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "frame too large"}}
            output_stream.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            output_stream.flush()
            break
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            response = dispatch(service, request, session)
        if response is None:
            continue
        response = bounded_response(response)
        output_stream.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        output_stream.flush()
