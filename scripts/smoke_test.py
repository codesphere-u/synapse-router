#!/usr/bin/env python3
"""Smoke-test a deployed MCP Streamable HTTP endpoint (stdlib only).

Usage: python scripts/smoke_test.py https://your-deployment.vercel.app [min_tools] [min_resources] [min_prompts]
"""

import json
import sys
import urllib.error
import urllib.request


def call(url, payload=None, raw=None, method="POST", headers=None):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    base = sys.argv[1].rstrip("/")
    min_tools = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    min_resources = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    min_prompts = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    failures = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    # 1. JSON Health Probe
    status, body = call(f"{base}/health", method="GET")
    check("GET /health is healthy", status == 200 and json.loads(body).get("status") == "healthy")

    # 2. HTML Developer Portal on GET /
    status, body = call(f"{base}/", method="GET", headers={"Accept": "text/html"})
    check("GET / with Accept: text/html returns developer portal", status == 200 and "<!DOCTYPE html>" in body)

    # 3. Streamable SSE on GET /mcp
    status, body = call(f"{base}/mcp", method="GET", headers={"Accept": "text/event-stream"})
    check("GET /mcp with Accept: text/event-stream returns SSE endpoint", status == 200 and "event: endpoint" in body)

    # 4. JSON-RPC 2.0 initialize
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1.0"},
        },
    }
    status, body = call(f"{base}/mcp", init)
    res_data = json.loads(body).get("result", {}) if status == 200 else {}
    caps = res_data.get("capabilities", {})
    check("initialize returns serverInfo", status == 200 and "serverInfo" in res_data)
    check("initialize advertises tools, resources, and prompts", "tools" in caps and "resources" in caps and "prompts" in caps)

    # 5. JSON-RPC notifications
    status, _ = call(f"{base}/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    check("notification returns 202", status == 202)

    # 6. JSON-RPC ping
    status, body = call(f"{base}/mcp", {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    check("ping returns {}", status == 200 and json.loads(body).get("result") == {})

    # 7. tools/list
    status, body = call(f"{base}/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = json.loads(body).get("result", {}).get("tools", []) if status == 200 else []
    check(f"tools/list returns >= {min_tools} tools (got {len(tools)})", len(tools) >= min_tools)

    # 8. resources/list
    status, body = call(f"{base}/mcp", {"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    resources = json.loads(body).get("result", {}).get("resources", []) if status == 200 else []
    check(f"resources/list returns >= {min_resources} resources (got {len(resources)})", len(resources) >= min_resources)

    # 9. prompts/list
    status, body = call(f"{base}/mcp", {"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
    prompts = json.loads(body).get("result", {}).get("prompts", []) if status == 200 else []
    check(f"prompts/list returns >= {min_prompts} prompts (got {len(prompts)})", len(prompts) >= min_prompts)

    # 10. Malformed JSON-RPC handling
    status, body = call(f"{base}/mcp", raw=b"{bad")
    check("malformed JSON returns -32700", status == 400 and json.loads(body)["error"]["code"] == -32700)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
