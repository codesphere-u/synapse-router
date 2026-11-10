"""Tests for SynapseRouter HTTP and SSE remote transport server."""

import json
import threading
import time
import urllib.request

import pytest

from synapse_router.http_server import run_http_server
from synapse_router.server import SynapseMCPServer


@pytest.fixture
def http_server():
    server = SynapseMCPServer()
    httpd = run_http_server(server, host="127.0.0.1", port=0)
    port = httpd.server_port
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def test_health_check(http_server):
    req = urllib.request.Request(f"{http_server}/health")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert data["status"] == "healthy"
        assert data["server"] == "synapse-router"
        assert "http" in data["transport"]


def test_json_rpc_tools_list_over_http(http_server):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    req = urllib.request.Request(
        f"{http_server}/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        tools = data["result"]["tools"]
        assert len(tools) >= 3
        tool_names = [t["name"] for t in tools]
        assert "route_agent_step" in tool_names


def test_cors_options(http_server):
    req = urllib.request.Request(f"{http_server}/messages", method="OPTIONS")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
