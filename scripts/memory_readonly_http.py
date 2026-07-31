#!/usr/bin/env python3
"""Loopback-only HTTP adapter for the shared read-only memory service."""

from __future__ import annotations

import json
import ipaddress
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from memory_readonly_service import ReadOnlyMemoryService, ReadRequestError, error_payload


MAX_CONCURRENT_REQUESTS = 8


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, maximum_requests: int = MAX_CONCURRENT_REQUESTS, **kwargs):
        self._request_slots = threading.BoundedSemaphore(maximum_requests)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        self._request_slots.acquire()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class BoundedThreadingHTTPServerV6(BoundedThreadingHTTPServer):
    address_family = socket.AF_INET6


def make_handler(service: ReadOnlyMemoryService):
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def do_GET(self) -> None:
            request = urlparse(self.path)
            if request.path != "/v1/memory/query":
                self.send_error(404)
                return
            values = parse_qs(request.query, keep_blank_values=True)
            try:
                payload = service.query(service.from_query_parameters(values))
                status = 200
            except ReadRequestError as exc:
                payload, status = error_payload(exc), 400
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self.send_error(405)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def create_server(host: str, port: int, service: ReadOnlyMemoryService, server_factory: Callable = BoundedThreadingHTTPServer):
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("read-only HTTP host must be a numeric loopback address") from exc
    if not address.is_loopback:
        raise ValueError("read-only HTTP must bind to a loopback address")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if address.version == 6 and server_factory is BoundedThreadingHTTPServer:
        server_factory = BoundedThreadingHTTPServerV6
    return server_factory((host, port), make_handler(service))
