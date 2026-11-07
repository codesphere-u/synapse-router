"""Unit tests for TokenBudgetGovernor, SynapseRouter Pareto selection, and Confidence Cascades."""

from __future__ import annotations

import pytest

from synapse_router.budget import BudgetExceededError, TokenBudgetGovernor
from synapse_router.classifier import AgentStepRequest, Tier
from synapse_router.router import (
    ModelEndpoint,
    ModelInvocationResult,
    NoHealthyEndpointError,
    ResponseValidator,
    SynapseRouter,
)


def test_budget_governor_soft_cap_downgrade_and_hard_cap_block() -> None:
    gov = TokenBudgetGovernor(
        default_workflow_max_usd=1.00,
        default_tenant_max_usd=5.00,
        soft_cap_ratio=0.80,
    )

    # Initial state: 0% spent -> full FRONTIER_REASONING allowed
    ev0 = gov.evaluate("tenant-a", "wf-1", Tier.FRONTIER_REASONING)
    assert ev0.allowed is True
    assert ev0.soft_cap_downgrade is False
    assert ev0.effective_max_tier == Tier.FRONTIER_REASONING

    # Spend $0.82 (82% of $1.00 workflow budget) -> triggers soft_cap_downgrade (FRONTIER -> BALANCED)
    gov.record_spend("tenant-a", "wf-1", cost_usd=0.82, tokens=4000)
    ev1 = gov.evaluate("tenant-a", "wf-1", Tier.FRONTIER_REASONING)
    assert ev1.allowed is True
    assert ev1.soft_cap_downgrade is True
    assert ev1.effective_max_tier == Tier.BALANCED

    # Spend another $0.12 -> $0.94 (94% utilization) -> downgrades to FAST_LOCAL
    gov.record_spend("tenant-a", "wf-1", cost_usd=0.12, tokens=1000)
    ev2 = gov.evaluate("tenant-a", "wf-1", Tier.FRONTIER_REASONING)
    assert ev2.allowed is True
    assert ev2.soft_cap_downgrade is True
    assert ev2.effective_max_tier == Tier.FAST_LOCAL

    # Spend another $0.07 -> $1.01 (>= 100% hard cap) -> blocked!
    gov.record_spend("tenant-a", "wf-1", cost_usd=0.07, tokens=500)
    ev3 = gov.evaluate("tenant-a", "wf-1", Tier.FAST_LOCAL)
    assert ev3.allowed is False
    assert "workflow_hard_cap_exceeded" in ev3.reason


def test_budget_governor_tenant_cap_and_custom_limits() -> None:
    with pytest.raises(ValueError):
        TokenBudgetGovernor(soft_cap_ratio=1.05)

    gov = TokenBudgetGovernor(default_workflow_max_usd=10.0, default_tenant_max_usd=1.0)
    gov.set_tenant_budget("tenant-x", max_usd=0.50, max_tokens=10_000)
    gov.set_workflow_budget("wf-custom", max_usd=2.00, max_tokens=50_000)

    gov.record_spend("tenant-x", "wf-custom", cost_usd=0.50, tokens=1000)
    status = gov.get_status("tenant-x", "wf-custom")
    assert status["allowed"] is False
    assert "tenant_hard_cap_exceeded" in str(status["reason"])


def test_router_pareto_selection_with_sla_and_cost_constraints() -> None:
    router = SynapseRouter()
    # Request a frontier task, but constrain latency_sla_ms to 750ms so o3-reasoning (950ms) is filtered out
    req = AgentStepRequest(
        prompt=(
            "Architect a distributed Raft consensus engine and formally prove safety invariants "
            "step-by-step under network partition."
        ),
        latency_sla_ms=750.0,
        max_cost_usd=0.02,
        estimated_output_tokens=500,
    )
    ep, score, analysis = router.select_endpoint(req)
    assert analysis.recommended_tier == Tier.FRONTIER_REASONING
    assert ep.id == "anthropic/claude-3-7-sonnet"
    assert score.effective_latency_ms <= 750.0
    assert score.estimated_cost_usd <= 0.02


def test_response_validator_catches_invalid_json_missing_keys_and_type_mismatches() -> None:
    schema = {
        "type": "object",
        "required": ["action", "score"],
        "properties": {
            "action": {"type": "string"},
            "score": {"type": "number"},
        },
    }
    req = AgentStepRequest(prompt="Return JSON", require_json_schema=schema, min_confidence=0.80)

    # 1. Malformed JSON
    r_bad_json = ModelInvocationResult("ep", "{not_json", 0.90, 50.0, 20, 20)
    v1 = ResponseValidator.validate(req, r_bad_json)
    assert not v1.passed and "invalid_json_payload" in v1.reasons

    # 2. Missing required key
    r_missing = ModelInvocationResult("ep", '{"action": "route"}', 0.90, 50.0, 20, 20)
    v2 = ResponseValidator.validate(req, r_missing)
    assert not v2.passed and "json_missing_required_key:score" in v2.reasons

    # 3. Wrong property type
    r_type = ModelInvocationResult("ep", '{"action": "route", "score": "high"}', 0.90, 50.0, 20, 20)
    v3 = ResponseValidator.validate(req, r_type)
    assert not v3.passed and "json_type_mismatch:score:expected_number" in v3.reasons

    # 4. Valid JSON + high confidence
    r_ok = ModelInvocationResult("ep", '{"action": "route", "score": 0.95}', 0.92, 50.0, 20, 20)
    v4 = ResponseValidator.validate(req, r_ok)
    assert v4.passed is True


def test_response_validator_catches_refusal_and_empty_tool_args() -> None:
    req = AgentStepRequest(prompt="Call search tool", min_confidence=0.75)
    r_refusal = ModelInvocationResult(
        "ep",
        "As an AI language model, I cannot assist with this.",
        0.95,
        40.0,
        10,
        10,
    )
    v_ref = ResponseValidator.validate(req, r_refusal)
    assert not v_ref.passed
    assert any("refusal_or_truncation" in r for r in v_ref.reasons)

    r_empty_tool = ModelInvocationResult(
        "ep",
        "Calling tool",
        0.95,
        40.0,
        10,
        10,
        tool_calls=[{"name": "search", "arguments": {}}],
    )
    v_tool = ResponseValidator.validate(req, r_empty_tool)
    assert not v_tool.passed
    assert "tool_call_empty_arguments:search" in v_tool.reasons


def test_confidence_cascade_escalates_across_all_three_tiers() -> None:
    router = SynapseRouter()
    req = AgentStepRequest(
        prompt="Classify ticket sentiment into positive or negative.",
        min_confidence=0.88,
        bypass_cache=True,
    )

    def escalating_executor(ep: ModelEndpoint, r: AgentStepRequest, hop: int) -> ModelInvocationResult:
        if ep.tier == Tier.FAST_LOCAL:
            return ModelInvocationResult(ep.id, "maybe positive", 0.55, 80.0, 20, 10)
        if ep.tier == Tier.BALANCED:
            return ModelInvocationResult(ep.id, "likely positive", 0.72, 200.0, 20, 10)
        return ModelInvocationResult(ep.id, "positive", 0.96, 600.0, 20, 10)

    receipt = router.execute_cascade(req, executor=escalating_executor)
    assert receipt.initial_tier == "FAST_LOCAL"
    assert receipt.final_tier == "FRONTIER_REASONING"
    assert len(receipt.cascade_hops) == 3
    assert receipt.cascade_hops[0].status == "escalated"
    assert receipt.cascade_hops[1].status == "escalated"
    assert receipt.cascade_hops[2].status == "accepted"
    assert receipt.confidence == 0.96
    assert len(receipt.hmac_sha256_signature) == 64


def test_cascade_handles_provider_429_and_fails_over_within_same_tier() -> None:
    router = SynapseRouter()
    req = AgentStepRequest(
        prompt="Extract user ID from webhook event.",
        bypass_cache=True,
    )

    def failover_executor(ep: ModelEndpoint, r: AgentStepRequest, hop: int) -> ModelInvocationResult:
        if ep.id == "google/gemini-2.5-flash-lite":
            return ModelInvocationResult(ep.id, "", 0.0, 15.0, 0, 0, error="http_429_rate_limit")
        return ModelInvocationResult(ep.id, "user_id=9812", 0.91, ep.p50_latency_ms, 20, 10)

    receipt = router.execute_cascade(req, executor=failover_executor)
    assert len(receipt.cascade_hops) == 2
    assert receipt.cascade_hops[0].endpoint_id == "google/gemini-2.5-flash-lite"
    assert receipt.cascade_hops[0].status == "provider_error"
    assert receipt.cascade_hops[1].endpoint_id == "ollama/qwen2.5-coder-7b"
    assert receipt.cascade_hops[1].status == "accepted"
    assert receipt.final_tier == "FAST_LOCAL"


def test_cascade_uses_semantic_cache_on_second_identical_call() -> None:
    router = SynapseRouter()
    req1 = AgentStepRequest(
        prompt="Classify deployment risk for service auth-api in production.",
        step_id="s1",
    )
    req2 = AgentStepRequest(
        prompt="Classify deployment risk for service auth-api in production!",
        step_id="s2",
    )

    rcpt1 = router.execute_cascade(req1)
    assert rcpt1.cache_hit is False
    assert rcpt1.total_cost_usd > 0.0

    rcpt2 = router.execute_cascade(req2)
    assert rcpt2.cache_hit is True
    assert rcpt2.total_cost_usd == 0.0
    assert rcpt2.cache_hamming_distance == 0
    assert rcpt2.output_text == rcpt1.output_text


def test_router_raises_when_budget_hard_cap_exceeded_or_no_endpoints_healthy() -> None:
    router = SynapseRouter()
    router.budget.set_workflow_budget("wf-broke", max_usd=0.0001)
    router.budget.record_spend("default-tenant", "wf-broke", cost_usd=0.0002, tokens=500)

    with pytest.raises(BudgetExceededError):
        router.execute_cascade(AgentStepRequest(prompt="Classify text", workflow_id="wf-broke"))

    # Trip all endpoints to OPEN
    for ep_id in router.endpoints:
        for _ in range(4):
            router.health.record_failure(ep_id, "http_503")

    with pytest.raises(NoHealthyEndpointError):
        router.execute_cascade(AgentStepRequest(prompt="Classify text", workflow_id="wf-healthy"))
