#!/usr/bin/env python3
"""Smoke-test a deployed MCP Streamable HTTP endpoint (stdlib only).

Usage: python scripts/smoke_test.py https://your-deployment.vercel.app [min_tools]
"""

import json
import sys
import urllib.error
import urllib.request


def call(url, payload=None, raw=None, method="POST"):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    base = sys.argv[1].rstrip("/")
    min_tools = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    failures = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    status, body = call(f"{base}/health", method="GET")
    check("GET /health is healthy", status == 200 and json.loads(body).get("status") == "healthy")

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}}
    status, body = call(f"{base}/mcp", init)
    check("initialize returns serverInfo", status == 200 and "serverInfo" in json.loads(body).get("result", {}))

    status, _ = call(f"{base}/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    check("notification returns 202", status == 202)

    status, body = call(f"{base}/mcp", {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    check("ping returns {}", status == 200 and json.loads(body).get("result") == {})

    status, body = call(f"{base}/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = json.loads(body).get("result", {}).get("tools", []) if status == 200 else []
    check(f"tools/list returns >= {min_tools} tools", len(tools) >= min_tools)

    status, body = call(f"{base}/mcp", raw=b"{bad")
    check("malformed JSON returns -32700", status == 400 and json.loads(body)["error"]["code"] == -32700)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
