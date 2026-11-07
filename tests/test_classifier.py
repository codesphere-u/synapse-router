"""Unit tests for TaskComplexityClassifier and AgentStepRequest."""

from __future__ import annotations

import pytest

from synapse_router.classifier import AgentStepRequest, TaskComplexityClassifier, Tier


def test_classifier_threshold_validation() -> None:
    with pytest.raises(ValueError):
        TaskComplexityClassifier(fast_tier_threshold=0.7, frontier_tier_threshold=0.5)
    with pytest.raises(ValueError):
        TaskComplexityClassifier(fast_tier_threshold=0.0, frontier_tier_threshold=0.6)


def test_estimate_tokens_empty_and_nonempty() -> None:
    assert TaskComplexityClassifier.estimate_tokens("") == 0
    tokens = TaskComplexityClassifier.estimate_tokens("Classify customer support ticket sentiment.")
    assert 5 <= tokens <= 15


@pytest.mark.parametrize(
    "prompt",
    [
        "Classify user intent into billing or technical support.",
        "Extract email address and phone number from text.",
        "Format the date string as YYYY-MM-DD json only.",
        "Translate 'Service is healthy' into French.",
        "Tag ticket priority: yes or no.",
        "Summarize this 2-line status update.",
    ],
)
def test_trivial_prompts_route_to_fast_local(prompt: str) -> None:
    clf = TaskComplexityClassifier()
    req = AgentStepRequest(prompt=prompt, estimated_output_tokens=64)
    res = clf.analyze(req)
    assert res.recommended_tier == Tier.FAST_LOCAL
    assert 0.0 <= res.complexity_score < clf.fast_tier_threshold


def test_balanced_tier_for_analytical_and_tool_step() -> None:
    clf = TaskComplexityClassifier()
    tools = [
        {
            "name": "query_metrics",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "window": {"type": "object", "properties": {"start": {"type": "string"}}},
                },
            },
        }
    ]
    req = AgentStepRequest(
        prompt="Analyze p95 latency regression across API gateway pods and compare error rates.",
        tools_schema=tools,
        estimated_output_tokens=220,
    )
    res = clf.analyze(req)
    assert res.recommended_tier == Tier.BALANCED
    assert clf.fast_tier_threshold <= res.complexity_score < clf.frontier_tier_threshold
    assert "analytical_comparison" in res.matched_markers


def test_frontier_tier_for_formal_proof_and_architecture() -> None:
    clf = TaskComplexityClassifier()
    req = AgentStepRequest(
        prompt=(
            "Architect a distributed Byzantine fault-tolerant consensus protocol and formally prove "
            "safety and liveness invariants step-by-step using induction and trade-off analysis."
        ),
        estimated_output_tokens=600,
    )
    res = clf.analyze(req)
    assert res.recommended_tier == Tier.FRONTIER_REASONING
    assert res.complexity_score >= clf.frontier_tier_threshold
    assert "formal_proof" in res.matched_markers
    assert "system_architecture" in res.matched_markers


def test_frontier_tier_for_security_audit_with_code_ast() -> None:
    clf = TaskComplexityClassifier()
    req = AgentStepRequest(
        prompt=(
            "Analyze and debug this concurrent bank transfer implementation for race condition, "
            "deadlock, and cryptographic replay vulnerability step-by-step:\n"
            "```python\n"
            "async def transfer(db, src, dst, amount):\n"
            "    bal = await db.get(src)\n"
            "    if bal >= amount:\n"
            "        await db.set(src, bal - amount)\n"
            "        await db.set(dst, (await db.get(dst)) + amount)\n"
            "```"
        ),
        estimated_output_tokens=450,
    )
    res = clf.analyze(req)
    assert res.recommended_tier == Tier.FRONTIER_REASONING
    assert res.code_density_score > 0.25
    assert "security_audit" in res.matched_markers


def test_tool_schema_depth_increases_complexity() -> None:
    clf = TaskComplexityClassifier()
    shallow_req = AgentStepRequest(
        prompt="Evaluate workflow state.",
        tools_schema=[{"name": "ping"}],
    )
    deep_req = AgentStepRequest(
        prompt="Evaluate workflow state.",
        tools_schema=[
            {"name": f"tool_{i}", "schema": {"a": {"b": {"c": {"d": {"e": "val"}}}}}}
            for i in range(5)
        ],
    )
    shallow_res = clf.analyze(shallow_req)
    deep_res = clf.analyze(deep_req)
    assert deep_res.tool_complexity_score > shallow_res.tool_complexity_score
    assert deep_res.complexity_score > shallow_res.complexity_score


def test_tight_latency_sla_downgrades_moderate_frontier_to_balanced() -> None:
    clf = TaskComplexityClassifier()
    req = AgentStepRequest(
        prompt=(
            "Prove asymptotic complexity bounds and Pareto optimality of the dynamic programming "
            "scheduler step-by-step with invariant verification."
        ),
        latency_sla_ms=220.0,
        estimated_output_tokens=400,
    )
    res = clf.analyze(req)
    assert res.recommended_tier == Tier.BALANCED


def test_tier_enum_ordering_and_labels() -> None:
    assert Tier.FAST_LOCAL < Tier.BALANCED < Tier.FRONTIER_REASONING
    assert Tier.FAST_LOCAL.label == "FAST_LOCAL"
    assert Tier.BALANCED.label == "BALANCED"
    assert Tier.FRONTIER_REASONING.label == "FRONTIER_REASONING"
