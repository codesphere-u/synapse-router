"""HTTP and Server-Sent Events (SSE) remote transport server for SynapseRouter.

Provides production-ready, zero-dependency HTTP and SSE transport conforming to
the Model Context Protocol remote specification (2024-11-05). Enables cloud deployment
on platforms such as Glama.ai, Fly.io, Cloudflare, and Docker containers.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from synapse_router.server import SynapseMCPServer

logger = logging.getLogger("synapse_router.http_server")


class SynapseHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler supporting JSON-RPC 2.0 POST, SSE stream, and health probes."""

    server_instance: SynapseMCPServer

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        if path in ("/health", "/", "/info"):
            body = json.dumps(
                {
                    "status": "healthy",
                    "server": "synapse-router",
                    "version": "1.1.0",
                    "transport": ["stdio", "http", "sse"],
                    "description": "Dynamic Semantic Router & Multi-Tier Cost Gateway MCP Server",
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/sse":
            session_id = str(uuid.uuid4())
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._send_cors_headers()
            self.end_headers()

            endpoint_msg = f"event: endpoint\ndata: /messages?session_id={session_id}\n\n".encode("utf-8")
            self.wfile.write(endpoint_msg)
            self.wfile.flush()
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_error(400, "Empty request body.")
            return

        try:
            raw_data = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(raw_data)
        except Exception as e:
            self._send_error(400, f"Malformed JSON: {e}")
            return

        if isinstance(payload, list):
            responses = []
            for item in payload:
                resp = self.server_instance.handle_request(item)
                if resp is not None:
                    responses.append(resp)
            response_body = json.dumps(responses).encode("utf-8")
        elif isinstance(payload, dict):
            resp = self.server_instance.handle_request(payload)
            response_body = json.dumps(resp).encode("utf-8") if resp is not None else b""
        else:
            self._send_error(400, "Invalid JSON-RPC payload.")
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_body)))
        self._send_cors_headers()
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_error(self, code: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def run_http_server(
    server_instance: SynapseMCPServer,
    host: str = "0.0.0.0",
    port: int = 8000,
) -> ThreadingHTTPServer:
    handler = SynapseHTTPHandler
    handler.server_instance = server_instance
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd
