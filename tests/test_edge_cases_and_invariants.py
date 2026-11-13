"""Edge-case, property, and invariant tests for synapse-router."""

from __future__ import annotations

import json

import pytest

from synapse_router.budget import TokenBudgetGovernor
from synapse_router.cache import (
    compute_simhash64,
    hamming_distance64,
)
from synapse_router.classifier import AgentStepRequest, Tier
from synapse_router.router import (
    ModelEndpoint,
    ModelInvocationResult,
    ResponseValidator,
    SynapseRouter,
)
from synapse_router.server import SynapseMCPServer


@pytest.mark.parametrize(
    ("json_text", "expected_reason_prefix"),
    [
        ("[1, 2, 3]", "json_schema_not_object"),
        ('{"str_f": 123, "num_f": 1, "bool_f": true, "arr_f": [], "obj_f": {}}', "json_type_mismatch:str_f:expected_string"),
        ('{"str_f": "ok", "num_f": "bad", "bool_f": true, "arr_f": [], "obj_f": {}}', "json_type_mismatch:num_f:expected_number"),
        ('{"str_f": "ok", "num_f": 1.5, "bool_f": "yes", "arr_f": [], "obj_f": {}}', "json_type_mismatch:bool_f:expected_boolean"),
        ('{"str_f": "ok", "num_f": 1.5, "bool_f": false, "arr_f": "not_list", "obj_f": {}}', "json_type_mismatch:arr_f:expected_array"),
        ('{"str_f": "ok", "num_f": 1.5, "bool_f": true, "arr_f": [1], "obj_f": []}', "json_type_mismatch:obj_f:expected_object"),
    ],
)
def test_validator_json_schema_all_field_types(json_text: str, expected_reason_prefix: str) -> None:
    schema = {
        "type": "object",
        "required": ["str_f", "num_f", "bool_f", "arr_f", "obj_f"],
        "properties": {
            "str_f": {"type": "string"},
            "num_f": {"type": "number"},
            "bool_f": {"type": "boolean"},
            "arr_f": {"type": "array"},
            "obj_f": {"type": "object"},
        },
    }
    req = AgentStepRequest(prompt="Validate all JSON types", require_json_schema=schema, min_confidence=0.70)
    res = ModelInvocationResult("ep-1", json_text, 0.95, 50.0, 20, 20)
    outcome = ResponseValidator.validate(req, res)
    assert outcome.passed is False
    assert any(r.startswith(expected_reason_prefix) for r in outcome.reasons)


@pytest.mark.parametrize(
    "marker_text",
    [
        "I cannot fulfill this request due to policy.",
        "As an AI language model, I cannot execute shell code.",
        "Partial output follow [TRUNCATED]",
        "Generating response... <incomplete_generation>",
    ],
)
def test_validator_all_refusal_and_truncation_markers(marker_text: str) -> None:
    req = AgentStepRequest(prompt="Generate report", min_confidence=0.70)
    res = ModelInvocationResult("ep-1", marker_text, 0.95, 50.0, 20, 20)
    outcome = ResponseValidator.validate(req, res)
    assert outcome.passed is False
    assert any(r.startswith("refusal_or_truncation:") for r in outcome.reasons)


def test_validator_empty_output_and_missing_tool_name() -> None:
    req = AgentStepRequest(prompt="Run step", min_confidence=0.70)
    empty_res = ModelInvocationResult("ep-1", "   ", 0.90, 20.0, 10, 5)
    v_empty = ResponseValidator.validate(req, empty_res)
    assert not v_empty.passed
    assert "empty_response" in v_empty.reasons

    anon_tool = ModelInvocationResult(
        "ep-1",
        "",
        0.90,
        20.0,
        10,
        5,
        tool_calls=[{"name": "", "arguments": {"a": 1}}],
    )
    v_anon = ResponseValidator.validate(req, anon_tool)
    assert not v_anon.passed
    assert "tool_call_missing_name" in v_anon.reasons


def test_router_capability_constraints_filter_endpoints() -> None:
    ep_no_tools = ModelEndpoint(
        id="local/no-tools",
        provider="local",
        tier=Tier.FAST_LOCAL,
        cost_per_1k_input=0.00001,
        cost_per_1k_output=0.00001,
        p50_latency_ms=40.0,
        p95_latency_ms=80.0,
        context_window=4096,
        supports_tools=False,
        supports_json_schema=False,
        quality_prior=0.82,
    )
    ep_full = ModelEndpoint(
        id="local/full-capable",
        provider="local",
        tier=Tier.FAST_LOCAL,
        cost_per_1k_input=0.00008,
        cost_per_1k_output=0.00020,
        p50_latency_ms=95.0,
        p95_latency_ms=170.0,
        context_window=64_000,
        supports_tools=True,
        supports_json_schema=True,
        quality_prior=0.79,
    )
    router = SynapseRouter(endpoints=[ep_no_tools, ep_full])

    # 1. Request requiring tools skips ep_no_tools
    req_tools = AgentStepRequest(
        prompt="Classify ticket",
        tools_schema=[{"name": "lookup_ticket"}],
    )
    selected, _, _ = router.select_endpoint(req_tools, target_tier=Tier.FAST_LOCAL)
    assert selected.id == "local/full-capable"

    # 2. Request requiring JSON schema skips ep_no_tools
    req_json = AgentStepRequest(
        prompt="Classify ticket",
        require_json_schema={"type": "object", "required": ["tag"]},
    )
    selected_j, _, _ = router.select_endpoint(req_json, target_tier=Tier.FAST_LOCAL)
    assert selected_j.id == "local/full-capable"

    # 3. Request exceeding 4096 context window skips ep_no_tools
    req_ctx = AgentStepRequest(
        prompt="Classify ticket",
        estimated_output_tokens=5000,
    )
    selected_c, _, _ = router.select_endpoint(req_ctx, target_tier=Tier.FAST_LOCAL)
    assert selected_c.id == "local/full-capable"


def test_default_deterministic_executor_synthesizes_all_json_schema_types() -> None:
    router = SynapseRouter()
    schema = {
        "type": "object",
        "required": ["title", "count", "active", "tags", "meta"],
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "number"},
            "active": {"type": "boolean"},
            "tags": {"type": "array"},
            "meta": {"type": "object"},
        },
    }
    req = AgentStepRequest(prompt="Classify item into full schema.", require_json_schema=schema)
    receipt = router.execute_cascade(req)
    parsed = json.loads(receipt.output_text)
    assert isinstance(parsed["title"], str)
    assert isinstance(parsed["count"], (int, float))
    assert parsed["active"] is True
    assert isinstance(parsed["tags"], list)
    assert isinstance(parsed["meta"], dict)


def test_soft_cap_downgrade_enforced_during_execute_cascade() -> None:
    router = SynapseRouter()
    router.budget.set_workflow_budget("wf-soft", max_usd=1.00)
    router.budget.record_spend("default-tenant", "wf-soft", cost_usd=0.85, tokens=1000)

    req = AgentStepRequest(
        prompt=(
            "Architect a Byzantine fault-tolerant consensus protocol and formally prove "
            "safety invariants step-by-step under network partition."
        ),
        workflow_id="wf-soft",
        bypass_cache=True,
    )
    receipt = router.execute_cascade(req)
    assert receipt.soft_cap_downgraded is True
    assert receipt.initial_tier == "BALANCED"
    assert receipt.final_tier == "BALANCED"


def test_budget_governor_token_hard_caps_for_workflow_and_tenant() -> None:
    gov = TokenBudgetGovernor(
        default_workflow_max_usd=100.0,
        default_workflow_max_tokens=500,
        default_tenant_max_usd=1000.0,
        default_tenant_max_tokens=1000,
    )
    gov.record_spend("t1", "wf1", cost_usd=0.01, tokens=500)
    ev_wf = gov.evaluate("t1", "wf1", Tier.FAST_LOCAL)
    assert ev_wf.allowed is False
    assert "workflow_hard_cap_exceeded" in ev_wf.reason

    gov.record_spend("t1", "wf2", cost_usd=0.01, tokens=500)
    ev_tn = gov.evaluate("t1", "wf3", Tier.FAST_LOCAL)
    assert ev_tn.allowed is False
    assert "tenant_hard_cap_exceeded" in ev_tn.reason


@pytest.mark.parametrize(
    ("text_a", "text_b", "max_expected_dist"),
    [
        ("Extract customer ID from invoice PDF", "Extract customer ID from invoice PDF.", 0),
        ("Route payment webhook to stripe handler", "Route payment webhook to stripe handler!", 0),
        ("Summarize weekly uptime metrics for SRE team", "Summarize weekly uptime metrics for the SRE team", 6),
        ("Parse JSON config and validate port numbers", "Parse JSON config, and validate port numbers!", 0),
        ("Check database replica lag in us_east_1", "Check database replica lag in us_east_1?", 0),
        ("Format response as JSON object only", "   Format response as JSON object only   ", 0),
    ],
)
def test_simhash_distance_invariants(text_a: str, text_b: str, max_expected_dist: int) -> None:
    ha = compute_simhash64(text_a)
    hb = compute_simhash64(text_b)
    assert hamming_distance64(ha, hb) <= max_expected_dist


@pytest.mark.parametrize(
    ("in_tokens", "out_tokens"),
    [
        (0, 0),
        (500, 250),
        (1000, 1000),
        (4000, 2000),
    ],
)
def test_endpoint_cost_estimation_scaling(in_tokens: int, out_tokens: int) -> None:
    router = SynapseRouter()
    ep = router.endpoints["anthropic/claude-3-7-sonnet"]
    cost = ep.estimate_cost_usd(in_tokens, out_tokens)
    expected = (in_tokens / 1000.0) * ep.cost_per_1k_input + (out_tokens / 1000.0) * ep.cost_per_1k_output
    assert cost == pytest.approx(expected, rel=1e-6)
    baseline = router.frontier_baseline_cost(in_tokens, out_tokens)
    assert baseline >= cost


def test_mcp_direct_method_calls_and_receipt_dict_conversion() -> None:
    server = SynapseMCPServer()
    resp = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "route_agent_step",
            "params": {
                "prompt": "Format user profile JSON.",
                "step_id": "direct-rpc-step",
            },
        }
    )
    assert resp["id"] == 99
    assert resp["result"]["step_id"] == "direct-rpc-step"
    assert "hmac_sha256_signature" in resp["result"]
