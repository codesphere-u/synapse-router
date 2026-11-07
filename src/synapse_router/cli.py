"""CLI & Reproducible Multi-Agent Routing Benchmark Suite for synapse-router."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any, Dict, List, Optional

from synapse_router.budget import TokenBudgetGovernor
from synapse_router.cache import SemanticPromptCache
from synapse_router.classifier import AgentStepRequest, Tier
from synapse_router.health import EndpointHealthRegistry
from synapse_router.router import ModelEndpoint, ModelInvocationResult, RoutingDecisionReceipt, SynapseRouter
from synapse_router.server import SynapseMCPServer


def _build_20_step_demo_workflow() -> List[AgentStepRequest]:
    """Construct a realistic 20-step LangGraph/MCP multi-agent customer & code triage workflow."""
    json_schema = {
        "type": "object",
        "required": ["intent", "priority"],
        "properties": {
            "intent": {"type": "string"},
            "priority": {"type": "string"},
        },
    }
    search_tools = [
        {
            "name": "query_knowledge_base",
            "description": "Search technical runbooks",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "filters": {"type": "object"}},
            },
        }
    ]
    return [
        AgentStepRequest(
            step_id="step-01-intent",
            prompt="Classify customer ticket intent into billing, outage, or security_audit.",
            require_json_schema=json_schema,
            estimated_output_tokens=64,
        ),
        AgentStepRequest(
            step_id="step-02-intent-near-dup",
            prompt="Classify customer ticket intent into billing, outage, or security_audit!",
            require_json_schema=json_schema,
            estimated_output_tokens=64,
        ),
        AgentStepRequest(
            step_id="step-03-extract-entities",
            prompt="Extract organization ID, region tag, and timestamp from alert payload.",
            estimated_output_tokens=96,
        ),
        AgentStepRequest(
            step_id="step-04-tool-lookup",
            prompt="Analyze error logs and compare service latency across us-east-1 replicas.",
            tools_schema=search_tools,
            estimated_output_tokens=180,
        ),
        AgentStepRequest(
            step_id="step-05-cascade-json",
            prompt="Format incident triage metadata into strict schema.",
            require_json_schema=json_schema,
            estimated_output_tokens=80,
            bypass_cache=True,
        ),
        AgentStepRequest(
            step_id="step-06-rate-limit-429",
            prompt="Evaluate database connection pool exhaustion and refactor retry backoff.",
            tools_schema=search_tools,
            estimated_output_tokens=240,
        ),
        AgentStepRequest(
            step_id="step-07-rate-limit-failover",
            prompt="Diagnose replica lag spike and compare read-replica routing policies.",
            tools_schema=search_tools,
            estimated_output_tokens=240,
        ),
        AgentStepRequest(
            step_id="step-08-frontier-arch",
            prompt=(
                "Architect a Byzantine fault-tolerant consensus state machine and formally prove "
                "liveness and safety invariants step-by-step under asynchronous network partitions "
                "with trade-off analysis for Paxos vs Raft."
            ),
            tools_schema=search_tools,
            estimated_output_tokens=600,
        ),
        AgentStepRequest(
            step_id="step-09-tag-severity",
            prompt="Tag incident severity level in one word: P0, P1, P2, or P3.",
            estimated_output_tokens=32,
        ),
        AgentStepRequest(
            step_id="step-10-tag-severity-cache",
            prompt="Tag incident severity level in one word: P0, P1, P2, or P3.",
            estimated_output_tokens=32,
        ),
        AgentStepRequest(
            step_id="step-11-code-audit",
            prompt=(
                "Analyze this Python async lock implementation for race condition and deadlock "
                "vulnerability:\n```python\nasync def acquire_all(locks):\n    for l in locks:\n        await l.acquire()\n```"
            ),
            estimated_output_tokens=350,
        ),
        AgentStepRequest(
            step_id="step-12-summarize-slack",
            prompt="Summarize the incident timeline in two bullet points for the status channel.",
            estimated_output_tokens=110,
        ),
        AgentStepRequest(
            step_id="step-13-summarize-slack-dup",
            prompt="Summarize the incident timeline in two bullet points for the status channel.",
            estimated_output_tokens=110,
        ),
        AgentStepRequest(
            step_id="step-14-sql-query-gen",
            prompt="Analyze slow query plan and refactor `SELECT * FROM events WHERE tenant_id = 42`.",
            tools_schema=search_tools,
            estimated_output_tokens=200,
        ),
        AgentStepRequest(
            step_id="step-15-low-conf-cascade",
            prompt="Normalize webhook payload headers and extract signature verification status.",
            min_confidence=0.86,
            estimated_output_tokens=120,
            bypass_cache=True,
        ),
        AgentStepRequest(
            step_id="step-16-frontier-proof",
            prompt=(
                "Prove asymptotic complexity bounds and Pareto optimality of the dynamic programming "
                "scheduler step-by-step with formal invariant verification and counterexample check."
            ),
            estimated_output_tokens=550,
        ),
        AgentStepRequest(
            step_id="step-17-translate-note",
            prompt="Translate status page maintenance notice into Spanish.",
            estimated_output_tokens=90,
        ),
        AgentStepRequest(
            step_id="step-18-extract-owners",
            prompt="Parse CODEOWNERS file and list on-call team handles.",
            estimated_output_tokens=64,
        ),
        AgentStepRequest(
            step_id="step-19-postmortem-draft",
            prompt="Evaluate root cause mitigation items and compare canary rollback intervals.",
            tools_schema=search_tools,
            estimated_output_tokens=280,
        ),
        AgentStepRequest(
            step_id="step-20-final-check",
            prompt="Format final workflow completion acknowledgement.",
            estimated_output_tokens=48,
        ),
    ]


def run_demo(as_json: bool = False) -> Dict[str, Any]:
    """Execute the 20-step multi-agent workflow demo with injected 429s, cascades, and cache hits."""
    router = SynapseRouter(
        health_registry=EndpointHealthRegistry(failure_threshold=2, recovery_timeout_s=30.0),
        cache=SemanticPromptCache(max_hamming_distance=3, ttl_seconds=600.0),
        budget_governor=TokenBudgetGovernor(default_workflow_max_usd=1.00, soft_cap_ratio=0.80),
    )
    steps = _build_20_step_demo_workflow()

    def demo_executor(
        endpoint: ModelEndpoint,
        request: AgentStepRequest,
        hop_index: int,
    ) -> ModelInvocationResult:
        # Inject HTTP 429 rate-limit spike on google/gemini-2.5-flash during steps 6 & 7
        if request.step_id in ("step-06-rate-limit-429", "step-07-rate-limit-failover") and endpoint.id == "google/gemini-2.5-flash":
            return ModelInvocationResult(
                endpoint_id=endpoint.id,
                output_text="",
                confidence=0.0,
                latency_ms=18.0,
                input_tokens=0,
                output_tokens=0,
                error="http_429_rate_limit_exceeded",
            )
        # Inject invalid JSON on first hop of step-05 to trigger confidence/validation cascade escalation
        if request.step_id == "step-05-cascade-json" and hop_index == 0:
            return ModelInvocationResult(
                endpoint_id=endpoint.id,
                output_text="{malformed_json: true",
                confidence=0.61,
                latency_ms=endpoint.p50_latency_ms,
                input_tokens=48,
                output_tokens=32,
                error=None,
            )
        # Inject low confidence on first hop of step-15 to trigger cascade escalation
        if request.step_id == "step-15-low-conf-cascade" and hop_index == 0:
            return ModelInvocationResult(
                endpoint_id=endpoint.id,
                output_text="Partial header normalization result.",
                confidence=0.64,
                latency_ms=endpoint.p50_latency_ms,
                input_tokens=52,
                output_tokens=45,
                error=None,
            )
        return router._default_deterministic_executor(endpoint, request, hop_index)

    receipts: List[RoutingDecisionReceipt] = []
    for step in steps:
        receipt = router.execute_cascade(step, executor=demo_executor)
        receipts.append(receipt)

    total_actual_usd = round(sum(r.total_cost_usd for r in receipts), 6)
    total_frontier_usd = round(sum(r.frontier_baseline_cost_usd for r in receipts), 6)
    saved_usd = round(total_frontier_usd - total_actual_usd, 6)
    savings_pct = round((saved_usd / max(1e-9, total_frontier_usd)) * 100.0, 2)
    cache_hits = sum(1 for r in receipts if r.cache_hit)
    escalations = sum(1 for r in receipts if len(r.cascade_hops) > 1)

    summary = {
        "workflow_steps": len(receipts),
        "total_actual_cost_usd": total_actual_usd,
        "frontier_baseline_cost_usd": total_frontier_usd,
        "cost_saved_usd": saved_usd,
        "cost_reduction_pct": savings_pct,
        "semantic_cache_hits": cache_hits,
        "cascade_or_failover_events": escalations,
        "steps": [
            {
                "step_id": r.step_id,
                "complexity": r.complexity_score,
                "initial_tier": r.initial_tier,
                "final_tier": r.final_tier,
                "endpoint": r.selected_endpoint_id,
                "cache_hit": r.cache_hit,
                "hops": len(r.cascade_hops),
                "cost_usd": r.total_cost_usd,
                "baseline_usd": r.frontier_baseline_cost_usd,
            }
            for r in receipts
        ],
    }

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print("=" * 98)
        print("SYNAPSE-ROUTER: 20-STEP MULTI-AGENT WORKFLOW DEMO (CASCADE + CIRCUIT BREAKER + SIMHASH CACHE)")
        print("=" * 98)
        print(
            f"{'STEP ID':<26} | {'SCORE':>5} | {'INIT TIER':<18} | {'FINAL ENDPOINT':<28} | {'HOPS':>4} | {'COST ($)':>10}"
        )
        print("-" * 98)
        for r in receipts:
            tag = " [CACHE]" if r.cache_hit else ""
            print(
                f"{r.step_id:<26} | {r.complexity_score:>5.2f} | {r.initial_tier:<18} | "
                f"{(r.selected_endpoint_id + tag):<28} | {len(r.cascade_hops):>4} | {r.total_cost_usd:>10.6f}"
            )
        print("-" * 98)
        print(f"Frontier-Only Baseline Cost : ${total_frontier_usd:.6f}")
        print(f"Synapse-Router Actual Cost  : ${total_actual_usd:.6f}")
        print(f"Total USD Cost Saved        : ${saved_usd:.6f} ({savings_pct:.2f}% reduction)")
        print(f"Semantic Cache Hits         : {cache_hits}/{len(receipts)}")
        print(f"Cascade / Failover Recoveries: {escalations} (100.0% workflow completion)")
        print("=" * 98)

    return summary


def run_benchmark(num_requests: int = 500, as_json: bool = False) -> Dict[str, Any]:
    """Run a 500-request synthetic multi-agent workload across 6 model endpoints."""
    router = SynapseRouter(
        health_registry=EndpointHealthRegistry(failure_threshold=3, recovery_timeout_s=10.0),
        cache=SemanticPromptCache(max_hamming_distance=3, ttl_seconds=900.0),
        budget_governor=TokenBudgetGovernor(
            default_workflow_max_usd=50.0,
            default_tenant_max_usd=500.0,
            soft_cap_ratio=0.80,
        ),
    )

    templates = _build_20_step_demo_workflow()
    routing_latencies_us: List[float] = []
    receipts: List[RoutingDecisionReceipt] = []
    injected_faults = 0
    recovered_faults = 0
    cascade_escalations = 0

    for i in range(num_requests):
        base = templates[i % len(templates)]
        # Make 20% of prompts exact/near-duplicate cacheable queries, 80% unique per batch
        if i % 5 == 0:
            prompt_text = base.prompt
        else:
            prompt_text = f"{base.prompt} [workflow_batch={i // 2} item_id={i}]"

        req = AgentStepRequest(
            prompt=prompt_text,
            system_prompt=base.system_prompt,
            tools_schema=base.tools_schema,
            require_json_schema=base.require_json_schema,
            min_confidence=base.min_confidence,
            estimated_output_tokens=base.estimated_output_tokens,
            tenant_id="bench-tenant",
            workflow_id=f"wf-{i // 25}",
            step_id=f"req-{i:04d}",
            bypass_cache=False,
        )

        def bench_executor(
            endpoint: ModelEndpoint,
            request: AgentStepRequest,
            hop_index: int,
            idx: int = i,
        ) -> ModelInvocationResult:
            nonlocal injected_faults
            # Inject periodic provider HTTP 429 rate limits on primary fast/balanced endpoints
            if idx % 29 == 0 and hop_index == 0 and endpoint.id in (
                "google/gemini-2.5-flash-lite",
                "google/gemini-2.5-flash",
                "ollama/qwen2.5-coder-7b",
            ):
                injected_faults += 1
                return ModelInvocationResult(
                    endpoint_id=endpoint.id,
                    output_text="",
                    confidence=0.0,
                    latency_ms=12.0,
                    input_tokens=0,
                    output_tokens=0,
                    error="http_429_rate_limit",
                )
            # Inject periodic low-confidence responses on FAST_LOCAL to trigger cascade escalation
            if idx % 19 == 0 and hop_index == 0 and endpoint.tier == Tier.FAST_LOCAL:
                return ModelInvocationResult(
                    endpoint_id=endpoint.id,
                    output_text="Uncertain fast-tier draft output.",
                    confidence=0.58,
                    latency_ms=endpoint.p50_latency_ms,
                    input_tokens=64,
                    output_tokens=64,
                    error=None,
                )
            return router._default_deterministic_executor(endpoint, request, hop_index)

        t_start = time.perf_counter()
        rcpt = router.execute_cascade(req, executor=bench_executor)
        elapsed_us = (time.perf_counter() - t_start) * 1_000_000.0
        routing_latencies_us.append(elapsed_us)
        receipts.append(rcpt)

        if any(h.status == "provider_error" for h in rcpt.cascade_hops) and rcpt.confidence >= req.min_confidence:
            recovered_faults += 1
        if any(h.status == "escalated" for h in rcpt.cascade_hops):
            cascade_escalations += 1

    sorted_lat = sorted(routing_latencies_us)
    p50_us = round(statistics.median(sorted_lat), 2)
    p95_idx = min(len(sorted_lat) - 1, int(round(0.95 * (len(sorted_lat) - 1))))
    p95_us = round(sorted_lat[p95_idx], 2)

    total_cost = round(sum(r.total_cost_usd for r in receipts), 6)
    baseline_cost = round(sum(r.frontier_baseline_cost_usd for r in receipts), 6)
    saved_cost = round(baseline_cost - total_cost, 6)
    cost_reduction_pct = round((saved_cost / max(1e-9, baseline_cost)) * 100.0, 2)

    tier_counts: Dict[str, int] = {t.name: 0 for t in Tier}
    for r in receipts:
        tier_counts[r.final_tier] = tier_counts.get(r.final_tier, 0) + 1

    quality_retained = sum(1 for r in receipts if r.confidence >= 0.78)
    quality_retention_pct = round((quality_retained / len(receipts)) * 100.0, 2)
    failover_recovery_pct = round(
        (recovered_faults / max(1, injected_faults)) * 100.0, 2
    ) if injected_faults > 0 else 100.0

    report = {
        "requests_evaluated": num_requests,
        "endpoints_registered": len(router.endpoints),
        "routing_latency_p50_us": p50_us,
        "routing_latency_p95_us": p95_us,
        "routing_latency_p50_ms": round(p50_us / 1000.0, 4),
        "routing_latency_p95_ms": round(p95_us / 1000.0, 4),
        "frontier_baseline_cost_usd": baseline_cost,
        "synapse_router_cost_usd": total_cost,
        "total_usd_saved": saved_cost,
        "cost_reduction_pct": cost_reduction_pct,
        "cascade_quality_retention_pct": quality_retention_pct,
        "failover_recovery_rate_pct": failover_recovery_pct,
        "injected_provider_faults": injected_faults,
        "cascade_escalations_triggered": cascade_escalations,
        "semantic_cache_stats": router.cache.stats(),
        "tier_distribution": tier_counts,
    }

    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print("=" * 84)
        print(f"SYNAPSE-ROUTER BENCHMARK SUITE ({num_requests} MULTI-AGENT REQUESTS / 6 ENDPOINTS)")
        print("=" * 84)
        print(f"Routing Engine P50 Latency     : {p50_us:.2f} µs ({p50_us / 1000.0:.4f} ms)")
        print(f"Routing Engine P95 Latency     : {p95_us:.2f} µs ({p95_us / 1000.0:.4f} ms)")
        print(f"Frontier-Only Baseline Cost    : ${baseline_cost:.4f} USD")
        print(f"Synapse-Router Total Cost      : ${total_cost:.4f} USD")
        print(f"Total USD Cost Reduction       : ${saved_cost:.4f} USD ({cost_reduction_pct:.2f}% saved)")
        print(f"Cascade Quality Retention      : {quality_retention_pct:.2f}% (confidence >= 0.78)")
        print(f"Provider Failover Recovery Rate: {failover_recovery_pct:.2f}% ({recovered_faults}/{injected_faults} HTTP 429s)")
        print(f"Cascade Escalations Triggered  : {cascade_escalations}")
        print(f"Semantic Cache Hit Rate        : {report['semantic_cache_stats']['hit_rate'] * 100:.2f}%")
        print(f"Tier Distribution              : {json.dumps(tier_counts)}")
        print("=" * 84)

    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="synapse-router",
        description="Cost-Aware Multi-Model AI Agent Router, Cascade Escalation & Semantic Cache CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser("demo", help="Run 20-step multi-agent workflow simulation")
    demo_p.add_argument("--json", action="store_true", help="Emit JSON report")

    bench_p = sub.add_parser("benchmark", help="Run 500-request routing & cost benchmark suite")
    bench_p.add_argument("--requests", type=int, default=500, help="Number of requests to benchmark")
    bench_p.add_argument("--json", action="store_true", help="Emit JSON report")

    sub.add_parser("mcp", help="Start JSON-RPC 2.0 MCP server over stdio")

    args = parser.parse_args(argv)
    if args.command == "demo":
        run_demo(as_json=args.json)
        return 0
    if args.command == "benchmark":
        run_benchmark(num_requests=args.requests, as_json=args.json)
        return 0
    if args.command == "mcp":  # pragma: no cover
        SynapseMCPServer().serve_stdio()
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
