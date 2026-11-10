"""Vercel Serverless Function entry point for SynapseRouter."""

import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Union
import uuid

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from synapse_router.server import SynapseMCPServer

mcp_engine = SynapseMCPServer()

app = FastAPI(
    title="SynapseRouter MCP Serverless",
    version="1.1.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "server": "synapse-router",
        "version": "1.1.0",
        "protocol": "mcp-jsonrpc-2.0",
        "transport": ["http", "sse"],
        "runtime": "vercel-serverless",
    }


@app.get("/sse")
async def sse_handshake(request: Request):
    session_id = str(uuid.uuid4())

    async def event_generator():
        endpoint_url = f"/messages?session_id={session_id}"
        yield f"event: endpoint\ndata: {endpoint_url}\n\n"
        yield f"event: ping\ndata: {json.dumps({'sessionId': session_id})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/messages")
@app.post("/mcp")
@app.post("/")
async def handle_jsonrpc(request: Request):
    try:
        body: Union[Dict[str, Any], List[Dict[str, Any]]] = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {str(exc)}"}, "id": None},
        )

    if isinstance(body, list):
        responses = []
        for req in body:
            res = mcp_engine.handle_request(req)
            if res is not None:
                responses.append(res)
        return JSONResponse(content=responses)

    res = mcp_engine.handle_request(body)
    if res is None:
        return Response(status_code=204)

    return JSONResponse(content=res)
