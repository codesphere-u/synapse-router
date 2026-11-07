"""SLA/Cost-Aware Model Router, Pareto Selector & Confidence Cascade Engine."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from synapse_router.budget import BudgetExceededError, TokenBudgetGovernor
from synapse_router.cache import SemanticPromptCache
from synapse_router.classifier import AgentStepRequest, ComplexityAnalysis, TaskComplexityClassifier, Tier
from synapse_router.health import EndpointHealthRegistry


class NoHealthyEndpointError(RuntimeError):
    """Raised when no healthy model endpoint satisfies hard routing constraints."""


@dataclass(frozen=True)
class ModelEndpoint:
    """Registered LLM model endpoint profile and pricing/SLA characteristics."""

    id: str
    provider: str
    tier: Tier
    cost_per_1k_input: float
    cost_per_1k_output: float
    p50_latency_ms: float
    p95_latency_ms: float
    context_window: int = 128_000
    supports_tools: bool = True
    supports_json_schema: bool = True
    quality_prior: float = 0.85

    def estimate_cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        """Compute exact USD cost for given input and output token counts."""
        in_cost = (max(0, input_tokens) / 1000.0) * self.cost_per_1k_input
        out_cost = (max(0, output_tokens) / 1000.0) * self.cost_per_1k_output
        return round(in_cost + out_cost, 8)


@dataclass(frozen=True)
class EndpointCandidateScore:
    """Pareto utility breakdown for a candidate endpoint."""

    endpoint_id: str
    tier: Tier
    utility_score: float
    quality_score: float
    normalized_cost: float
    normalized_latency: float
    estimated_cost_usd: float
    effective_latency_ms: float


@dataclass(frozen=True)
class ModelInvocationResult:
    """Result returned by an endpoint executor callback during cascade execution."""

    endpoint_id: str
    output_text: str
    confidence: float
    latency_ms: float
    input_tokens: int
    output_tokens: int
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass(frozen=True)
class ValidationOutcome:
    """Result of deterministic response quality, schema, and confidence validation."""

    passed: bool
    confidence: float
    reasons: List[str]


@dataclass(frozen=True)
class CascadeHop:
    """Telemetry record for a single execution attempt within a cascade."""

    hop_index: int
    tier: str
    endpoint_id: str
    status: str
    confidence: float
    latency_ms: float
    cost_usd: float
    validation_passed: bool
    escalation_reason: Optional[str] = None


@dataclass(frozen=True)
class RoutingDecisionReceipt:
    """Cryptographically signed audit receipt for a routed agent workflow step."""

    receipt_id: str
    tenant_id: str
    workflow_id: str
    step_id: str
    complexity_score: float
    initial_tier: str
    final_tier: str
    selected_endpoint_id: str
    cache_hit: bool
    cache_hamming_distance: Optional[int]
    soft_cap_downgraded: bool
    cascade_hops: List[CascadeHop]
    total_cost_usd: float
    frontier_baseline_cost_usd: float
    cost_saved_usd: float
    total_latency_ms: float
    routing_overhead_ms: float
    output_text: str
    confidence: float
    hmac_sha256_signature: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_REFUSAL_OR_TRUNCATION_MARKERS = (
    "i cannot fulfill this request",
    "as an ai language model, i cannot",
    "[truncated]",
    "<incomplete_generation>",
)


class ResponseValidator:
    """Deterministic validator for JSON schemas, tool call arguments, refusals, and confidence."""

    @staticmethod
    def validate(
        request: AgentStepRequest,
        result: ModelInvocationResult,
    ) -> ValidationOutcome:
        reasons: List[str] = []

        if result.error:
            return ValidationOutcome(
                passed=False,
                confidence=0.0,
                reasons=[f"provider_error:{result.error}"],
            )

        text = (result.output_text or "").strip()
        if not text and not result.tool_calls:
            reasons.append("empty_response")

        lower_text = text.lower()
        for marker in _REFUSAL_OR_TRUNCATION_MARKERS:
            if marker in lower_text:
                reasons.append(f"refusal_or_truncation:{marker}")
                break

        # Validate required JSON schema if requested
        if request.require_json_schema:
            try:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    reasons.append("json_schema_not_object")
                else:
                    required_keys = request.require_json_schema.get("required", [])
                    for req_key in required_keys:
                        if req_key not in parsed or parsed[req_key] is None or parsed[req_key] == "":
                            reasons.append(f"json_missing_required_key:{req_key}")
                    properties = request.require_json_schema.get("properties", {})
                    for prop_name, prop_spec in properties.items():
                        if prop_name in parsed and isinstance(prop_spec, dict):
                            expected_type = prop_spec.get("type")
                            val = parsed[prop_name]
                            if expected_type == "string" and not isinstance(val, str):
                                reasons.append(f"json_type_mismatch:{prop_name}:expected_string")
                            elif expected_type == "number" and not isinstance(val, (int, float)):
                                reasons.append(f"json_type_mismatch:{prop_name}:expected_number")
                            elif expected_type == "boolean" and not isinstance(val, bool):
                                reasons.append(f"json_type_mismatch:{prop_name}:expected_boolean")
                            elif expected_type == "array" and not isinstance(val, list):
                                reasons.append(f"json_type_mismatch:{prop_name}:expected_array")
                            elif expected_type == "object" and not isinstance(val, dict):
                                reasons.append(f"json_type_mismatch:{prop_name}:expected_object")
            except Exception:
                reasons.append("invalid_json_payload")

        # Validate tool call arguments if tool_calls are present
        if result.tool_calls:
            for tc in result.tool_calls:
                fn_name = tc.get("name") or tc.get("function", {}).get("name")
                args = tc.get("arguments", tc.get("function", {}).get("arguments"))
                if not fn_name:
                    reasons.append("tool_call_missing_name")
                if args in (None, "", {}):
                    reasons.append(f"tool_call_empty_arguments:{fn_name or 'unknown'}")

        # Validate confidence threshold
        if result.confidence < request.min_confidence:
            reasons.append(
                f"confidence_below_threshold:{result.confidence:.3f}<{request.min_confidence:.3f}"
            )

        return ValidationOutcome(
            passed=len(reasons) == 0,
            confidence=result.confidence,
            reasons=reasons,
        )


def default_endpoint_catalog() -> List[ModelEndpoint]:
    """Return the default 6-endpoint multi-provider catalog spanning all 3 capability tiers."""
    return [
        ModelEndpoint(
            id="ollama/qwen2.5-coder-7b",
            provider="local-ollama",
            tier=Tier.FAST_LOCAL,
            cost_per_1k_input=0.00005,
            cost_per_1k_output=0.00010,
            p50_latency_ms=85.0,
            p95_latency_ms=160.0,
            context_window=32_768,
            supports_tools=True,
            supports_json_schema=True,
            quality_prior=0.74,
        ),
        ModelEndpoint(
            id="google/gemini-2.5-flash-lite",
            provider="google",
            tier=Tier.FAST_LOCAL,
            cost_per_1k_input=0.00008,
            cost_per_1k_output=0.00030,
            p50_latency_ms=115.0,
            p95_latency_ms=210.0,
            context_window=256_000,
            supports_tools=True,
            supports_json_schema=True,
            quality_prior=0.80,
        ),
        ModelEndpoint(
            id="google/gemini-2.5-flash",
            provider="google",
            tier=Tier.BALANCED,
            cost_per_1k_input=0.00030,
            cost_per_1k_output=0.00120,
            p50_latency_ms=240.0,
            p95_latency_ms=460.0,
            context_window=1_000_000,
            supports_tools=True,
            supports_json_schema=True,
            quality_prior=0.89,
        ),
        ModelEndpoint(
            id="anthropic/claude-3-5-haiku",
            provider="anthropic",
            tier=Tier.BALANCED,
            cost_per_1k_input=0.00080,
            cost_per_1k_output=0.00400,
            p50_latency_ms=290.0,
            p95_latency_ms=540.0,
            context_window=200_000,
            supports_tools=True,
            supports_json_schema=True,
            quality_prior=0.91,
        ),
        ModelEndpoint(
            id="anthropic/claude-3-7-sonnet",
            provider="anthropic",
            tier=Tier.FRONTIER_REASONING,
            cost_per_1k_input=0.00300,
            cost_per_1k_output=0.01500,
            p50_latency_ms=680.0,
            p95_latency_ms=1350.0,
            context_window=200_000,
            supports_tools=True,
            supports_json_schema=True,
            quality_prior=0.97,
        ),
        ModelEndpoint(
            id="openai/o3-reasoning",
            provider="openai",
            tier=Tier.FRONTIER_REASONING,
            cost_per_1k_input=0.01000,
            cost_per_1k_output=0.04000,
            p50_latency_ms=950.0,
            p95_latency_ms=1950.0,
            context_window=200_000,
            supports_tools=True,
            supports_json_schema=True,
            quality_prior=0.99,
        ),
    ]


ExecutorCallback = Callable[[ModelEndpoint, AgentStepRequest, int], ModelInvocationResult]


class SynapseRouter:
    """Cost-aware multi-model router with Pareto utility scoring, circuit breaking, and confidence cascades."""

    def __init__(
        self,
        endpoints: Optional[List[ModelEndpoint]] = None,
        classifier: Optional[TaskComplexityClassifier] = None,
        health_registry: Optional[EndpointHealthRegistry] = None,
        cache: Optional[SemanticPromptCache] = None,
        budget_governor: Optional[TokenBudgetGovernor] = None,
        cost_weight: float = 0.35,
        latency_weight: float = 0.25,
        signing_secret: str = "synapse-router-deterministic-hmac-key",
    ) -> None:
        self.classifier = classifier or TaskComplexityClassifier()
        self.health = health_registry or EndpointHealthRegistry()
        self.cache = cache or SemanticPromptCache()
        self.budget = budget_governor or TokenBudgetGovernor()
        self.cost_weight = cost_weight
        self.latency_weight = latency_weight
        self._signing_secret = signing_secret.encode("utf-8")

        self._endpoints: Dict[str, ModelEndpoint] = {}
        for ep in (endpoints if endpoints is not None else default_endpoint_catalog()):
            self.register_endpoint(ep)

        self._receipt_counter = 0

    def register_endpoint(self, endpoint: ModelEndpoint) -> None:
        """Register or update a model endpoint profile."""
        self._endpoints[endpoint.id] = endpoint

    @property
    def endpoints(self) -> Dict[str, ModelEndpoint]:
        return dict(self._endpoints)

    def frontier_baseline_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Compute baseline cost if the request were sent to the highest-cost frontier endpoint."""
        frontier_eps = [
            ep for ep in self._endpoints.values() if ep.tier == Tier.FRONTIER_REASONING
        ]
        if not frontier_eps:
            frontier_eps = list(self._endpoints.values())
        if not frontier_eps:
            return 0.0
        return max(ep.estimate_cost_usd(input_tokens, output_tokens) for ep in frontier_eps)

    def rank_candidates(
        self,
        request: AgentStepRequest,
        target_tier: Tier,
        analysis: Optional[ComplexityAnalysis] = None,
        allow_tier_fallback: bool = True,
        excluded_endpoints: Optional[set[str]] = None,
    ) -> List[EndpointCandidateScore]:
        """Score and rank healthy endpoints using Pareto utility maximization."""
        if analysis is None:
            analysis = self.classifier.analyze(request)

        excluded = excluded_endpoints or set()
        needs_tools = bool(request.tools_schema)
        needs_json = request.require_json_schema is not None
        total_tokens = analysis.estimated_input_tokens + analysis.estimated_output_tokens

        def _filter_candidates(tier_filter: Optional[Tier], enforce_sla_and_cost: bool) -> List[ModelEndpoint]:
            matched: List[ModelEndpoint] = []
            for ep in self._endpoints.values():
                if ep.id in excluded:
                    continue
                if tier_filter is not None and ep.tier != tier_filter:
                    continue
                if not self.health.is_available(ep.id):
                    continue
                if needs_tools and not ep.supports_tools:
                    continue
                if needs_json and not ep.supports_json_schema:
                    continue
                if ep.context_window < total_tokens:
                    continue
                if enforce_sla_and_cost:
                    eff_lat = self.health.effective_latency_ms(ep.id, ep.p50_latency_ms)
                    if request.latency_sla_ms is not None and eff_lat > request.latency_sla_ms:
                        continue
                    est_cost = ep.estimate_cost_usd(
                        analysis.estimated_input_tokens, analysis.estimated_output_tokens
                    )
                    if request.max_cost_usd is not None and est_cost > request.max_cost_usd:
                        continue
                matched.append(ep)
            return matched

        candidates = _filter_candidates(target_tier, enforce_sla_and_cost=True)

        # Fallback 1: If no endpoint in target_tier meets strict SLA/cost, try adjacent healthy tiers that meet SLA/cost
        if not candidates and allow_tier_fallback:
            for fallback_tier in sorted(
                [t for t in Tier if t != target_tier],
                key=lambda t: (abs(int(t) - int(target_tier)), int(t)),
            ):
                candidates = _filter_candidates(fallback_tier, enforce_sla_and_cost=True)
                if candidates:
                    break

        # Fallback 2: Relax soft SLA/cost constraints on healthy endpoints if all strict matches failed
        if not candidates and allow_tier_fallback:
            candidates = _filter_candidates(target_tier, enforce_sla_and_cost=False)
            if not candidates:
                candidates = _filter_candidates(None, enforce_sla_and_cost=False)

        if not candidates:
            raise NoHealthyEndpointError(
                f"No healthy model endpoint available for tier={target_tier.name} (excluded={sorted(excluded)})"
            )

        # Compute normalization bounds across all registered endpoints for stable Pareto scoring
        all_costs = [
            ep.estimate_cost_usd(analysis.estimated_input_tokens, analysis.estimated_output_tokens)
            for ep in self._endpoints.values()
        ]
        all_lats = [
            self.health.effective_latency_ms(ep.id, ep.p50_latency_ms)
            for ep in self._endpoints.values()
        ]
        max_cost = max(all_costs) if all_costs else 1e-6
        min_cost = min(all_costs) if all_costs else 0.0
        cost_span = max(1e-9, max_cost - min_cost)

        max_lat = max(all_lats) if all_lats else 1000.0
        min_lat = min(all_lats) if all_lats else 1.0
        lat_span = max(1.0, max_lat - min_lat)

        scored: List[EndpointCandidateScore] = []
        for ep in candidates:
            est_cost = ep.estimate_cost_usd(
                analysis.estimated_input_tokens, analysis.estimated_output_tokens
            )
            eff_lat = self.health.effective_latency_ms(ep.id, ep.p50_latency_ms)
            norm_cost = (est_cost - min_cost) / cost_span
            norm_lat = (eff_lat - min_lat) / lat_span

            utility = (
                ep.quality_prior
                - (self.cost_weight * norm_cost)
                - (self.latency_weight * norm_lat)
            )
            scored.append(
                EndpointCandidateScore(
                    endpoint_id=ep.id,
                    tier=ep.tier,
                    utility_score=round(utility, 6),
                    quality_score=ep.quality_prior,
                    normalized_cost=round(norm_cost, 6),
                    normalized_latency=round(norm_lat, 6),
                    estimated_cost_usd=est_cost,
                    effective_latency_ms=eff_lat,
                )
            )

        scored.sort(key=lambda s: s.utility_score, reverse=True)
        return scored

    def select_endpoint(
        self,
        request: AgentStepRequest,
        target_tier: Optional[Tier] = None,
        analysis: Optional[ComplexityAnalysis] = None,
    ) -> Tuple[ModelEndpoint, EndpointCandidateScore, ComplexityAnalysis]:
        """Select the highest-utility healthy endpoint for the given request."""
        if analysis is None:
            analysis = self.classifier.analyze(request)
        effective_tier = target_tier if target_tier is not None else analysis.recommended_tier

        budget_eval = self.budget.evaluate(
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            requested_tier=effective_tier,
        )
        if not budget_eval.allowed:
            raise BudgetExceededError(budget_eval.reason)

        if budget_eval.soft_cap_downgrade and effective_tier > budget_eval.effective_max_tier:
            effective_tier = budget_eval.effective_max_tier

        ranked = self.rank_candidates(request, effective_tier, analysis=analysis)
        best = ranked[0]
        return self._endpoints[best.endpoint_id], best, analysis

    def _default_deterministic_executor(
        self,
        endpoint: ModelEndpoint,
        request: AgentStepRequest,
        hop_index: int,
    ) -> ModelInvocationResult:
        """Built-in zero-API-key deterministic execution simulator for offline routing & benchmarks."""
        analysis = self.classifier.analyze(request)
        # Confidence scales with endpoint quality prior relative to task complexity
        gap = endpoint.quality_prior - (0.55 * analysis.complexity_score)
        confidence = round(min(0.99, max(0.45, gap + 0.32)), 3)

        if request.require_json_schema:
            props = request.require_json_schema.get("properties", {})
            required = request.require_json_schema.get("required", list(props.keys()))
            payload: Dict[str, Any] = {}
            for key in required:
                spec = props.get(key, {})
                t = spec.get("type", "string") if isinstance(spec, dict) else "string"
                if t == "number":
                    payload[key] = round(confidence, 3)
                elif t == "boolean":
                    payload[key] = True
                elif t == "array":
                    payload[key] = ["routed", endpoint.id]
                elif t == "object":
                    payload[key] = {"tier": endpoint.tier.name}
                else:
                    payload[key] = f"synthesized_by_{endpoint.id}"
            output_text = json.dumps(payload, sort_keys=True)
        else:
            output_text = (
                f"[{endpoint.id} | tier={endpoint.tier.name}] Completed step '{request.step_id}' "
                f"(complexity={analysis.complexity_score:.2f}, confidence={confidence:.2f})."
            )

        tool_calls: List[Dict[str, Any]] = []
        if request.tools_schema:
            first_tool = request.tools_schema[0].get("name", "default_tool")
            tool_calls.append({"name": first_tool, "arguments": {"query": request.prompt[:48]}})

        return ModelInvocationResult(
            endpoint_id=endpoint.id,
            output_text=output_text,
            confidence=confidence,
            latency_ms=endpoint.p50_latency_ms,
            input_tokens=analysis.estimated_input_tokens,
            output_tokens=analysis.estimated_output_tokens,
            tool_calls=tool_calls,
            error=None,
        )

    def _sign_receipt(self, payload: Dict[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._signing_secret, serialized.encode("utf-8"), hashlib.sha256).hexdigest()

    def execute_cascade(
        self,
        request: AgentStepRequest,
        executor: Optional[ExecutorCallback] = None,
    ) -> RoutingDecisionReceipt:
        """Route request with semantic caching, circuit-breaker failover, and confidence cascade escalation."""
        t0 = time.perf_counter()
        self._receipt_counter += 1
        receipt_id = f"rcpt-{self._receipt_counter:06d}"

        analysis = self.classifier.analyze(request)
        initial_tier = analysis.recommended_tier

        # 1. Evaluate tenant/workflow budget before execution
        budget_eval = self.budget.evaluate(
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            requested_tier=initial_tier,
        )
        if not budget_eval.allowed:
            raise BudgetExceededError(budget_eval.reason)

        soft_cap_downgraded = False
        starting_tier = initial_tier
        if budget_eval.soft_cap_downgrade and starting_tier > budget_eval.effective_max_tier:
            starting_tier = budget_eval.effective_max_tier
            soft_cap_downgraded = True

        baseline_cost = self.frontier_baseline_cost(
            analysis.estimated_input_tokens,
            analysis.estimated_output_tokens,
        )

        # 2. Check Semantic Prompt Cache (unless bypassed)
        if not request.bypass_cache:
            cache_res = self.cache.lookup(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                tools_schema=request.tools_schema,
                require_json_schema=request.require_json_schema,
            )
            if cache_res.hit and cache_res.entry is not None:
                overhead_ms = round((time.perf_counter() - t0) * 1000.0, 4)
                unsigned = {
                    "receipt_id": receipt_id,
                    "tenant_id": request.tenant_id,
                    "workflow_id": request.workflow_id,
                    "step_id": request.step_id,
                    "selected_endpoint_id": cache_res.entry.endpoint_id,
                    "cache_hit": True,
                    "total_cost_usd": 0.0,
                }
                return RoutingDecisionReceipt(
                    receipt_id=receipt_id,
                    tenant_id=request.tenant_id,
                    workflow_id=request.workflow_id,
                    step_id=request.step_id,
                    complexity_score=analysis.complexity_score,
                    initial_tier=starting_tier.name,
                    final_tier=cache_res.entry.tier_name,
                    selected_endpoint_id=cache_res.entry.endpoint_id,
                    cache_hit=True,
                    cache_hamming_distance=cache_res.hamming_distance,
                    soft_cap_downgraded=soft_cap_downgraded,
                    cascade_hops=[],
                    total_cost_usd=0.0,
                    frontier_baseline_cost_usd=baseline_cost,
                    cost_saved_usd=round(baseline_cost, 8),
                    total_latency_ms=overhead_ms,
                    routing_overhead_ms=overhead_ms,
                    output_text=cache_res.entry.response_text,
                    confidence=cache_res.entry.confidence,
                    hmac_sha256_signature=self._sign_receipt(unsigned),
                )

        # 3. Confidence-Gated Cascade Execution across tiers
        run_fn = executor or self._default_deterministic_executor
        max_allowed_tier = (
            budget_eval.effective_max_tier
            if budget_eval.soft_cap_downgrade
            else Tier.FRONTIER_REASONING
        )
        tier_sequence = [
            t for t in (Tier.FAST_LOCAL, Tier.BALANCED, Tier.FRONTIER_REASONING)
            if int(starting_tier) <= int(t) <= int(max_allowed_tier)
        ]
        if not tier_sequence:
            tier_sequence = [starting_tier]

        hops: List[CascadeHop] = []
        attempted_endpoints: set[str] = set()
        total_cost_usd = 0.0
        total_model_latency_ms = 0.0
        final_result: Optional[ModelInvocationResult] = None
        final_endpoint: Optional[ModelEndpoint] = None

        tier_idx = 0
        while tier_idx < len(tier_sequence):
            current_tier = tier_sequence[tier_idx]
            try:
                ranked = self.rank_candidates(
                    request=request,
                    target_tier=current_tier,
                    analysis=analysis,
                    allow_tier_fallback=True,
                    excluded_endpoints=attempted_endpoints,
                )
            except NoHealthyEndpointError:
                tier_idx += 1
                continue

            candidate_ep = self._endpoints[ranked[0].endpoint_id]
            attempted_endpoints.add(candidate_ep.id)

            hop_idx = len(hops)
            inv_result = run_fn(candidate_ep, request, hop_idx)

            if inv_result.error:
                # Provider error (e.g., HTTP 429 or 503): update circuit breaker and fail over within same tier first
                self.health.record_failure(
                    candidate_ep.id,
                    error_type=inv_result.error,
                    latency_ms=inv_result.latency_ms,
                )
                total_model_latency_ms += inv_result.latency_ms
                hops.append(
                    CascadeHop(
                        hop_index=hop_idx,
                        tier=candidate_ep.tier.name,
                        endpoint_id=candidate_ep.id,
                        status="provider_error",
                        confidence=0.0,
                        latency_ms=inv_result.latency_ms,
                        cost_usd=0.0,
                        validation_passed=False,
                        escalation_reason=f"provider_error:{inv_result.error}",
                    )
                )
                # Stay on same tier_idx if another healthy endpoint exists in this tier
                remaining_same_tier = [
                    ep
                    for ep in self._endpoints.values()
                    if ep.tier == current_tier
                    and ep.id not in attempted_endpoints
                    and self.health.is_available(ep.id)
                ]
                if not remaining_same_tier:
                    tier_idx += 1
                continue

            # Provider call succeeded at HTTP level
            self.health.record_success(candidate_ep.id, inv_result.latency_ms)
            hop_cost = candidate_ep.estimate_cost_usd(
                inv_result.input_tokens, inv_result.output_tokens
            )
            total_cost_usd = round(total_cost_usd + hop_cost, 8)
            total_model_latency_ms += inv_result.latency_ms
            self.budget.record_spend(
                tenant_id=request.tenant_id,
                workflow_id=request.workflow_id,
                cost_usd=hop_cost,
                tokens=inv_result.input_tokens + inv_result.output_tokens,
            )

            validation = ResponseValidator.validate(request, inv_result)
            final_result = inv_result
            final_endpoint = candidate_ep

            if validation.passed:
                hops.append(
                    CascadeHop(
                        hop_index=hop_idx,
                        tier=candidate_ep.tier.name,
                        endpoint_id=candidate_ep.id,
                        status="accepted",
                        confidence=inv_result.confidence,
                        latency_ms=inv_result.latency_ms,
                        cost_usd=hop_cost,
                        validation_passed=True,
                        escalation_reason=None,
                    )
                )
                break
            else:
                reason_str = ";".join(validation.reasons)
                hops.append(
                    CascadeHop(
                        hop_index=hop_idx,
                        tier=candidate_ep.tier.name,
                        endpoint_id=candidate_ep.id,
                        status="escalated" if (tier_idx + 1) < len(tier_sequence) else "completed_max_tier",
                        confidence=inv_result.confidence,
                        latency_ms=inv_result.latency_ms,
                        cost_usd=hop_cost,
                        validation_passed=False,
                        escalation_reason=reason_str,
                    )
                )
                tier_idx += 1

        if final_result is None or final_endpoint is None:
            raise NoHealthyEndpointError("All candidate endpoints across cascade tiers failed or were unavailable.")

        # Cache valid responses
        if hops and hops[-1].validation_passed and not request.bypass_cache:
            self.cache.put(
                prompt=request.prompt,
                response_text=final_result.output_text,
                endpoint_id=final_endpoint.id,
                tier_name=final_endpoint.tier.name,
                confidence=final_result.confidence,
                system_prompt=request.system_prompt,
                tools_schema=request.tools_schema,
                require_json_schema=request.require_json_schema,
            )

        routing_overhead_ms = round((time.perf_counter() - t0) * 1000.0, 4)
        unsigned = {
            "receipt_id": receipt_id,
            "tenant_id": request.tenant_id,
            "workflow_id": request.workflow_id,
            "step_id": request.step_id,
            "selected_endpoint_id": final_endpoint.id,
            "cache_hit": False,
            "total_cost_usd": total_cost_usd,
            "hops": len(hops),
        }
        return RoutingDecisionReceipt(
            receipt_id=receipt_id,
            tenant_id=request.tenant_id,
            workflow_id=request.workflow_id,
            step_id=request.step_id,
            complexity_score=analysis.complexity_score,
            initial_tier=starting_tier.name,
            final_tier=final_endpoint.tier.name,
            selected_endpoint_id=final_endpoint.id,
            cache_hit=False,
            cache_hamming_distance=None,
            soft_cap_downgraded=soft_cap_downgraded,
            cascade_hops=hops,
            total_cost_usd=total_cost_usd,
            frontier_baseline_cost_usd=baseline_cost,
            cost_saved_usd=round(baseline_cost - total_cost_usd, 8),
            total_latency_ms=round(total_model_latency_ms + routing_overhead_ms, 3),
            routing_overhead_ms=routing_overhead_ms,
            output_text=final_result.output_text,
            confidence=final_result.confidence,
            hmac_sha256_signature=self._sign_receipt(unsigned),
        )
