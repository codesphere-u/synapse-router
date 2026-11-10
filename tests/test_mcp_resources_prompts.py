"""Tests for SynapseRouter MCP Resources and Prompts primitives."""

from synapse_router.server import SynapseMCPServer


def test_synapse_mcp_initialize_capabilities():
    server = SynapseMCPServer()
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    caps = resp["result"]["capabilities"]
    assert "tools" in caps
    assert "resources" in caps
    assert "prompts" in caps


def test_synapse_mcp_resources_list_and_read():
    server = SynapseMCPServer()
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    resources = resp["result"]["resources"]
    assert len(resources) >= 2
    uris = [r["uri"] for r in resources]
    assert "synapse://endpoints/catalog" in uris
    assert "synapse://health/summary" in uris

    # Read endpoints catalog
    read_resp = server.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "synapse://endpoints/catalog"}}
    )
    content = read_resp["result"]["contents"][0]
    assert content["mimeType"] == "application/json"
    assert "ollama/qwen2.5-coder-7b" in content["text"]

    # Read health summary
    read_resp2 = server.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "synapse://health/summary"}}
    )
    content2 = read_resp2["result"]["contents"][0]
    assert "health" in content2["text"]


def test_synapse_mcp_prompts_list_and_get():
    server = SynapseMCPServer()
    resp = server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
    prompts = resp["result"]["prompts"]
    assert len(prompts) >= 2
    names = [p["name"] for p in prompts]
    assert "route-task-advisor" in names
    assert "budget-allocation-planner" in names

    # Get prompt
    get_resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "prompts/get",
            "params": {"name": "route-task-advisor", "arguments": {"task_prompt": "Sort 10 items", "latency_sla_ms": 100}},
        }
    )
    res = get_resp["result"]
    assert len(res["messages"]) == 1
    assert "FAST_LOCAL, BALANCED" in res["messages"][0]["content"]["text"]
