"""Unit tests for the developer portal and upgraded HTTP/SSE endpoints in Synapse Router."""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from portal import render_developer_portal


def test_portal_html_render():
    html = render_developer_portal(
        server_name="synapse-router",
        server_version="1.1.0",
        tagline="Cost-Aware Multi-Model AI Agent Router",
        endpoint_url="https://synapse-router.vercel.app/mcp",
        github_url="https://github.com/codesphere-u/synapse-router",
        tools=[{"name": "route_agent_step", "description": "Route agent step", "inputSchema": {"properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}],
        resources=[{"uri": "synapse://endpoints/catalog", "name": "Endpoint Catalog", "mimeType": "application/json", "description": "Catalog of model endpoints"}],
        prompts=[{"name": "route-task-advisor", "description": "Routing advisor prompt", "arguments": [{"name": "prompt", "required": True}]}],
        sample_tool_call={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "route_agent_step", "arguments": {"prompt": "test"}}},
    )
    assert "<!DOCTYPE html>" in html
    assert "synapse-router" in html
    assert "route_agent_step" in html
    assert "synapse://endpoints/catalog" in html
    assert "route-task-advisor" in html
    assert "Operational" in html
    assert "claude mcp add" in html
