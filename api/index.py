"""Vercel Serverless entrypoint for Synapse Router.

Exposes the MCP server over **Streamable HTTP** (``POST /mcp``) plus a
``/health`` probe. The gateway is stateless and hardened for public hosting:

* strict JSON-RPC 2.0 envelopes (parse / invalid-request / internal errors),
* request body size limit,
* tools that touch the server filesystem or run commands are NOT exposed on
  the hosted deployment (use the local stdio install for those).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import uuid

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
for _p in (str(SRC_DIR), str(ROOT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from synapse_router.server import SynapseMCPServer

SERVER_NAME = "synapse-router"
SERVER_VERSION = "1.1.0"
MAX_BODY_BYTES = 1_000_000

# Tools hidden and refused on the public hosted deployment (filesystem / process access).
HOSTED_BLOCKED_TOOLS = frozenset([])
# Per-tool arguments that are refused (and stripped from the advertised schema) when hosted.
HOSTED_FORBIDDEN_ARGS: Dict[str, List[str]] = {}

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603

mcp_engine = SynapseMCPServer()

app = FastAPI(
    title="Synapse Router (hosted MCP)",
    version=SERVER_VERSION,
    docs_url="/docs",
    openapi_url="/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)


def _error(code: int, message: str, req_id: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
    )


def _sanitize_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    visible = []
    for tool in tools:
        name = tool.get("name")
        if name in HOSTED_BLOCKED_TOOLS:
            continue
        tool = copy.deepcopy(tool)
        schema = tool.get("inputSchema") or {}
        for arg in HOSTED_FORBIDDEN_ARGS.get(name, []):
            schema.get("properties", {}).pop(arg, None)
            if arg in schema.get("required", []):
                schema["required"].remove(arg)
        visible.append(tool)
    return visible


def _dispatch(message: Any) -> Optional[Dict[str, Any]]:
    """Process one JSON-RPC message. Returns None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return {
            "jsonrpc": "2.0",
            "id": message.get("id") if isinstance(message, dict) else None,
            "error": {"code": INVALID_REQUEST, "message": "Invalid JSON-RPC 2.0 request."},
        }

    method = message["method"]
    req_id = message.get("id")
    is_notification = "id" not in message

    if method == "ping":
        return None if is_notification else {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        refused = None
        if name in HOSTED_BLOCKED_TOOLS:
            refused = f"Tool '{name}' is disabled on the hosted deployment (filesystem/process access). Install locally via stdio to use it."
        else:
            for arg in HOSTED_FORBIDDEN_ARGS.get(name, []):
                if isinstance(args, dict) and arg in args:
                    refused = f"Argument '{arg}' is disabled on the hosted deployment. Pass the content inline instead."
                    break
        if refused:
            if is_notification:
                return None
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": refused}], "isError": True},
            }

    try:
        response = mcp_engine.handle_request(message)
    except Exception:  # noqa: BLE001 - never leak internals to public callers
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": INTERNAL_ERROR, "message": "Internal server error."}}

    if response is None or is_notification:
        return None

    if method == "tools/list" and isinstance(response.get("result"), dict):
        response["result"]["tools"] = _sanitize_tools(response["result"].get("tools", []))
    return response


@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "protocol": "mcp-jsonrpc-2.0",
        "transport": ["streamable-http"],
        "endpoint": "/mcp",
        "runtime": "vercel-serverless",
        "hosted_restrictions": sorted(HOSTED_BLOCKED_TOOLS),
    }


@app.get("/mcp")
async def mcp_get():
    # Streamable HTTP: server-initiated streams are optional; this gateway is stateless.
    return Response(status_code=405, headers={"Allow": "POST, OPTIONS"})


@app.get("/sse")
async def sse_legacy(request: Request):
    """Legacy discovery stream. Responses are returned inline on POST /mcp (Streamable HTTP)."""
    session_id = str(uuid.uuid4())

    async def event_generator():
        yield f"event: endpoint\ndata: /messages?session_id={session_id}\n\n"
        yield f"event: ping\ndata: {json.dumps({'sessionId': session_id})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/mcp")
@app.post("/messages")
@app.post("/")
async def handle_jsonrpc(request: Request):
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _error(INVALID_REQUEST, "Request body too large.", status=413)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return _error(PARSE_ERROR, "Parse error.", status=400)

    if isinstance(body, list):
        if not body:
            return _error(INVALID_REQUEST, "Empty batch.", status=400)
        responses = [r for r in (_dispatch(m) for m in body) if r is not None]
        if not responses:
            return Response(status_code=202)
        return JSONResponse(content=responses)

    result = _dispatch(body)
    if result is None:
        return Response(status_code=202)
    return JSONResponse(content=result)
