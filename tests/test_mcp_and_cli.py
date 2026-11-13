"""Unit tests for SynapseMCPServer (JSON-RPC 2.0) and CLI commands."""

from __future__ import annotations

import pytest

from synapse_router.cli import main, run_benchmark, run_demo
from synapse_router.server import SynapseMCPServer


def test_mcp_server_initialize_ping_and_tools_list() -> None:
    server = SynapseMCPServer()

    init_resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp["result"]["serverInfo"]["name"] == "synapse-router-mcp"

    ping_resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping_resp["result"]["status"] == "pong"

    list_resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tool_names = {t["name"] for t in list_resp["result"]["tools"]}
    assert tool_names == {
        "route_agent_step",
        "classify_task_complexity",
        "inspect_endpoint_health",
        "check_budget_status",
        "synapse_rate_limit_status",
        "synapse_export_aegis_g2",
    }


def test_mcp_server_tools_call_all_four_tools() -> None:
    server = SynapseMCPServer()

    # 1. classify_task_complexity
    c_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "c1",
            "method": "tools/call",
            "params": {
                "name": "classify_task_complexity",
                "arguments": {
                    "prompt": "Architect a consensus state machine and prove safety invariants step-by-step."
                },
            },
        }
    )
    c_data = c_resp["result"]["structuredContent"]
    assert c_data["recommended_tier"] == "FRONTIER_REASONING"
    assert c_data["complexity_score"] >= 0.66

    # 2. route_agent_step
    r_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "r1",
            "method": "tools/call",
            "params": {
                "name": "route_agent_step",
                "arguments": {
                    "prompt": "Classify customer sentiment into positive or negative.",
                    "tenant_id": "mcp-tenant",
                    "workflow_id": "mcp-wf",
                    "step_id": "mcp-step-1",
                },
            },
        }
    )
    r_data = r_resp["result"]["structuredContent"]
    assert r_data["tenant_id"] == "mcp-tenant"
    assert r_data["final_tier"] == "FAST_LOCAL"
    assert r_data["cost_saved_usd"] > 0

    # 3. inspect_endpoint_health (all endpoints + single endpoint)
    h_all = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "h1",
            "method": "tools/call",
            "params": {"name": "inspect_endpoint_health", "arguments": {}},
        }
    )
    assert "google/gemini-2.5-flash-lite" in h_all["result"]["structuredContent"]["endpoints"]

    h_one = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "h2",
            "method": "inspect_endpoint_health",
            "params": {"endpoint_id": "google/gemini-2.5-flash-lite"},
        }
    )
    assert h_one["result"]["endpoint_id"] == "google/gemini-2.5-flash-lite"
    assert h_one["result"]["total_calls"] >= 1

    # 4. check_budget_status
    b_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "b1",
            "method": "tools/call",
            "params": {
                "name": "check_budget_status",
                "arguments": {"tenant_id": "mcp-tenant", "workflow_id": "mcp-wf"},
            },
        }
    )
    b_data = b_resp["result"]["structuredContent"]
    assert b_data["tenant_id"] == "mcp-tenant"
    assert b_data["workflow_spent_tokens"] > 0


def test_mcp_server_error_handling() -> None:
    server = SynapseMCPServer()

    bad_rpc = server.handle_request({"id": 1})
    assert bad_rpc["error"]["code"] == -32600

    not_found = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "nonexistent_method"})
    assert not_found["error"]["code"] == -32601

    missing_arg = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "route_agent_step", "arguments": {}},
        }
    )
    assert missing_arg["error"]["code"] == -32000


def test_cli_demo_and_benchmark_execution(capsys: pytest.CaptureFixture[str]) -> None:
    demo_res = run_demo(as_json=True)
    assert demo_res["workflow_steps"] == 20
    assert demo_res["cost_reduction_pct"] > 65.0
    assert demo_res["semantic_cache_hits"] >= 3

    bench_res = run_benchmark(num_requests=60, as_json=True)
    assert bench_res["requests_evaluated"] == 60
    assert bench_res["cost_reduction_pct"] > 70.0
    assert bench_res["failover_recovery_rate_pct"] == 100.0

    rc1 = main(["demo", "--json"])
    assert rc1 == 0
    rc2 = main(["benchmark", "--requests", "40", "--json"])
    assert rc2 == 0
    captured = capsys.readouterr().out
    assert "workflow_steps" in captured
