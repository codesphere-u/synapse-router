"""Conformance and hosted-safety tests for the Vercel gateway (api/index.py)."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

_spec = importlib.util.spec_from_file_location(
    "vercel_gateway", Path(__file__).resolve().parent.parent / "api" / "index.py"
)
gateway = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gateway)
client = TestClient(gateway.app)

BLOCKED = []
FORBIDDEN = {}


def rpc(method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body)


def test_health_lists_streamable_http():
    r = client.get("/health")
    data = r.json()
    assert data["status"] == "healthy"
    assert data["endpoint"] == "/mcp"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert r.headers.get("x-request-id")


def test_health_head_probe():
    for path in ("/", "/health", "/healthz", "/api/health"):
        r = client.head(path)
        assert r.status_code == 200


def test_request_id_echoed_and_sanitized():
    r = client.get("/health", headers={"X-Request-ID": "req-abc_123"})
    assert r.headers.get("x-request-id") == "req-abc_123"
    r_bad = client.get("/health", headers={"X-Request-ID": "bad value!"})
    assert r_bad.headers.get("x-request-id") != "bad value!"


def test_ping_returns_empty_result():
    assert rpc("ping").json()["result"] == {}


def test_notification_returns_202_no_body():
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202 and r.content == b""


def test_parse_error_is_valid_jsonrpc_envelope():
    r = client.post("/mcp", content=b"{bad", headers={"Content-Type": "application/json"})
    body = r.json()
    assert r.status_code == 400
    assert body["error"]["code"] == -32700 and "detail" not in body


def test_invalid_request_rejected():
    assert client.post("/mcp", json={"hello": "world"}).json()["error"]["code"] == -32600


def test_oversized_body_rejected():
    r = client.post("/mcp", content=b"x" * (gateway.MAX_BODY_BYTES + 1))
    assert r.status_code == 413


def test_get_mcp_not_allowed():
    assert client.get("/mcp").status_code == 405


def test_blocked_tools_hidden_and_refused():
    names = {t["name"] for t in rpc("tools/list").json()["result"]["tools"]}
    assert names, "hosted gateway must still expose tools"
    for tool in BLOCKED:
        assert tool not in names
        res = rpc("tools/call", {"name": tool, "arguments": {}}).json()["result"]
        assert res["isError"] is True


def test_forbidden_args_stripped_and_refused():
    tools = {t["name"]: t for t in rpc("tools/list").json()["result"]["tools"]}
    for tool, args in FORBIDDEN.items():
        for arg in args:
            assert arg not in tools[tool]["inputSchema"].get("properties", {})
            res = rpc("tools/call", {"name": tool, "arguments": {arg: "/etc"}}).json()["result"]
            assert res["isError"] is True


def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(gateway, "RATE_LIMIT_PER_MIN", 3)
    gateway._hits.clear()
    codes = [rpc("ping", id_=i).status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    r = rpc("ping")
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert r.json()["error"]["code"] == -32000
    gateway._hits.clear()
