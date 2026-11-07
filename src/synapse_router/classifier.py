"""Prompt & Agent Step Complexity Analyzer for Multi-Model Routing."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional


class Tier(IntEnum):
    """Model capability tiers ordered from fastest/cheapest to frontier reasoning."""

    FAST_LOCAL = 1
    BALANCED = 2
    FRONTIER_REASONING = 3

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class AgentStepRequest:
    """Incoming multi-agent workflow step routing request."""

    prompt: str
    system_prompt: str = ""
    tools_schema: List[Dict[str, Any]] = field(default_factory=list)
    require_json_schema: Optional[Dict[str, Any]] = None
    latency_sla_ms: Optional[float] = None
    max_cost_usd: Optional[float] = None
    min_confidence: float = 0.78
    estimated_output_tokens: int = 256
    tenant_id: str = "default-tenant"
    workflow_id: str = "default-workflow"
    step_id: str = "step-0"
    bypass_cache: bool = False


@dataclass(frozen=True)
class ComplexityAnalysis:
    """Detailed feature breakdown produced by TaskComplexityClassifier."""

    complexity_score: float
    recommended_tier: Tier
    estimated_input_tokens: int
    estimated_output_tokens: int
    reasoning_marker_score: float
    code_density_score: float
    tool_complexity_score: float
    token_volume_score: float
    matched_markers: List[str]


_REASONING_PATTERNS: List[tuple[str, re.Pattern[str], float]] = [
    ("formal_proof", re.compile(r"\b(prove|theorem|lemma|induction|contradiction|invariant)\b", re.I), 0.34),
    ("system_architecture", re.compile(r"\b(architect|architecture|trade-?offs?|distributed|consensus|raft|paxos|byzantine)\b", re.I), 0.30),
    ("multi_step_reasoning", re.compile(r"\b(step-by-step|chain of thought|decompose|root cause|synthesize|counterexample|formalize)\b", re.I), 0.28),
    ("optimization_math", re.compile(r"\b(optimize|dynamic programming|np-hard|complexity|asymptotic|hessian|jacobian|eigenvalue|convex|pareto)\b", re.I), 0.30),
    ("security_audit", re.compile(r"\b(vulnerability|exploit|race condition|deadlock|cryptographic|zero-day|threat model)\b", re.I), 0.28),
    ("analytical_comparison", re.compile(r"\b(compare|evaluate|analyze|refactor|debug|diagnose|investigate)\b", re.I), 0.28),
]

_TRIVIAL_PATTERNS: List[re.Pattern[str]] = [
    re.compile(r"^\s*(classify|extract|format|summarize|translate|normalize|tag|route|parse)\b", re.I),
    re.compile(r"\b(yes or no|true or false|sentiment|intent classification|one word|json only)\b", re.I),
]

_CODE_SYNTAX_RE = re.compile(
    r"(```|\bdef\s+\w+|\bclass\s+\w+|\basync\s+def\b|\bimport\s+\w+|\bfunction\s*\(|\bSELECT\s+.+\bFROM\b|=>|\{\s*\"\w+\"\s*:)"
)


def _compute_schema_depth(obj: Any, current_depth: int = 0) -> int:
    """Recursively measure maximum nesting depth of a JSON/tool schema."""
    if not isinstance(obj, (dict, list)) or current_depth > 16:
        return current_depth
    if isinstance(obj, dict):
        if not obj:
            return current_depth + 1
        return max(_compute_schema_depth(v, current_depth + 1) for v in obj.values())
    if not obj:
        return current_depth + 1
    return max(_compute_schema_depth(item, current_depth + 1) for item in obj)


class TaskComplexityClassifier:
    """Deterministic <0.05ms analyzer that scores agent step complexity in [0.0, 1.0]."""

    def __init__(
        self,
        fast_tier_threshold: float = 0.34,
        frontier_tier_threshold: float = 0.66,
    ) -> None:
        if not (0.0 < fast_tier_threshold < frontier_tier_threshold < 1.0):
            raise ValueError("Thresholds must satisfy 0 < fast_tier_threshold < frontier_tier_threshold < 1")
        self.fast_tier_threshold = fast_tier_threshold
        self.frontier_tier_threshold = frontier_tier_threshold

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Deterministic token estimation (~4 chars/token plus punctuation/whitespace weighting)."""
        if not text:
            return 0
        words = len(text.split())
        chars = len(text)
        return max(1, int(round(0.6 * (chars / 4.0) + 0.4 * (words * 1.3))))

    def analyze(self, request: AgentStepRequest) -> ComplexityAnalysis:
        """Compute normalized complexity score and assign a recommended model Tier."""
        combined_text = f"{request.system_prompt}\n{request.prompt}".strip()
        input_tokens = self.estimate_tokens(combined_text)
        if request.tools_schema:
            input_tokens += sum(self.estimate_tokens(str(t)) for t in request.tools_schema)
        if request.require_json_schema:
            input_tokens += self.estimate_tokens(str(request.require_json_schema))

        # 1. Reasoning & multi-step planning markers
        matched_markers: List[str] = []
        raw_reasoning = 0.0
        for name, pattern, weight in _REASONING_PATTERNS:
            matches = pattern.findall(combined_text)
            if matches:
                matched_markers.append(name)
                # Diminishing returns for repeated matches of the same category
                raw_reasoning += weight * (1.0 + 0.25 * min(3, len(matches) - 1))

        # Check trivial task dampening when no heavy reasoning markers exist
        is_trivial_pattern = any(p.search(request.prompt) for p in _TRIVIAL_PATTERNS)
        if is_trivial_pattern and raw_reasoning < 0.20:
            raw_reasoning *= 0.35

        reasoning_score = min(1.0, raw_reasoning)

        # 2. Code & AST syntax density
        code_matches = len(_CODE_SYNTAX_RE.findall(combined_text))
        symbol_count = sum(1 for ch in combined_text if ch in "{}[]();=<>+-*/&|^~%")
        char_len = max(1, len(combined_text))
        symbol_ratio = symbol_count / char_len
        code_density_score = min(
            1.0,
            (0.18 * code_matches) + min(0.55, symbol_ratio * 4.5),
        )

        # 3. Tool schema count and nesting depth
        num_tools = len(request.tools_schema)
        max_depth = 0
        if request.tools_schema:
            max_depth = max(_compute_schema_depth(t) for t in request.tools_schema)
        if request.require_json_schema:
            max_depth = max(max_depth, _compute_schema_depth(request.require_json_schema))

        tool_complexity_score = min(
            1.0,
            (0.24 * num_tools) + (0.08 * max(0, max_depth - 2)),
        )

        # 4. Token volume score (log-scaled)
        total_estimated_tokens = input_tokens + max(64, request.estimated_output_tokens)
        token_volume_score = min(
            1.0,
            max(0.0, (math.log2(max(32, total_estimated_tokens)) - 5.0) / 7.5),
        )

        # Weighted composite score
        composite = (
            0.46 * reasoning_score
            + 0.22 * code_density_score
            + 0.20 * tool_complexity_score
            + 0.12 * token_volume_score
        )
        # Boost when analytical reasoning + tools or code co-occur
        if reasoning_score >= 0.25 and (code_density_score >= 0.15 or tool_complexity_score >= 0.20):
            composite = min(1.0, composite + 0.14)
        if reasoning_score >= 0.55:
            composite = min(1.0, composite + 0.14)

        complexity_score = round(min(1.0, max(0.0, composite)), 4)

        if complexity_score < self.fast_tier_threshold:
            recommended_tier = Tier.FAST_LOCAL
        elif complexity_score < self.frontier_tier_threshold:
            recommended_tier = Tier.BALANCED
        else:
            recommended_tier = Tier.FRONTIER_REASONING

        # Hard constraint upgrade: if latency SLA is extremely tight (<280ms) and not explicitly frontier-only
        if (
            request.latency_sla_ms is not None
            and request.latency_sla_ms < 280
            and recommended_tier == Tier.FRONTIER_REASONING
            and complexity_score < 0.85
        ):
            recommended_tier = Tier.BALANCED

        return ComplexityAnalysis(
            complexity_score=complexity_score,
            recommended_tier=recommended_tier,
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=request.estimated_output_tokens,
            reasoning_marker_score=round(reasoning_score, 4),
            code_density_score=round(code_density_score, 4),
            tool_complexity_score=round(tool_complexity_score, 4),
            token_volume_score=round(token_volume_score, 4),
            matched_markers=matched_markers,
        )
