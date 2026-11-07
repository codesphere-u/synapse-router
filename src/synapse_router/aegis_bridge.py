"""Nexus Aegis Bridge for SynapseRouter.

Exports SynapseRouter routing decisions, Pareto selections, and budget evaluations
into canonical `Nexus Aegis` `G2_BUDGET_ROUTING` attestation payloads with RFC 6962
domain-separated SHA-256 leaf digests (0x00 prefix).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Union

from synapse_router.budget import BudgetEvaluation
from synapse_router.router import RoutingDecisionReceipt

_TRIVIAL_TASK_HINTS = re.compile(
    r"\b(ping|hello|hi|echo|format\s+json|lowercase|uppercase|capitalize|what\s+is\s+2\s*\+\s*2)\b",
    re.IGNORECASE,
)

_FRONTIER_MODELS = {
    "claude-3-5-sonnet",
    "claude-3-opus",
    "gpt-4o",
    "gpt-4-turbo",
    "o1-preview",
    "o1-mini",
    "gemini-1.5-pro",
}


def canonical_json_bytes(obj: Any) -> bytes:
    """Return deterministic UTF-8 bytes for an object using sorted keys and minimal whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def hash_leaf_bytes(data: bytes) -> str:
    """Compute RFC 6962 Certificate Transparency leaf hash: SHA-256(0x00 || payload)."""
    return hashlib.sha256(b"\x00" + data).hexdigest()


def create_aegis_g2_payload(
    receipt_or_eval: Optional[Union[RoutingDecisionReceipt, BudgetEvaluation, Dict[str, Any]]] = None,
    task_id: str = "synapse_routing",
    task_prompt: str = "",
    cost_usd: float = 0.0,
    budget_cap_usd: float = 1.0,
    tokens_used: int = 0,
    token_budget_cap: int = 120_000,
    model_id: str = "claude-3-5-sonnet",
    is_trivial_override: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Transform a SynapseRouter receipt or budget evaluation into the canonical
    Nexus Aegis G2_BUDGET_ROUTING payload format.
    """
    actual_cost = cost_usd
    actual_cap = budget_cap_usd
    actual_tokens = tokens_used
    actual_model = model_id
    effective_task_id = task_id
    is_trivial = False

    if isinstance(receipt_or_eval, RoutingDecisionReceipt):
        actual_cost = receipt_or_eval.total_cost_usd
        actual_model = receipt_or_eval.selected_endpoint_id
        effective_task_id = receipt_or_eval.workflow_id or receipt_or_eval.step_id or task_id
        # Estimate tokens from cost or default
        if actual_tokens == 0 and receipt_or_eval.cascade_hops:
            # Approximate total tokens from hops if not explicitly provided
            actual_tokens = 500 * len(receipt_or_eval.cascade_hops)
        if receipt_or_eval.complexity_score < 0.25:
            is_trivial = True

    elif isinstance(receipt_or_eval, BudgetEvaluation):
        effective_task_id = receipt_or_eval.workflow_id or receipt_or_eval.tenant_id or task_id
        actual_cost = round((1.0 - receipt_or_eval.workflow_utilization_ratio) * 1.0, 4)
        if not receipt_or_eval.allowed:
            actual_cost = actual_cap + 0.05

    elif isinstance(receipt_or_eval, dict):
        actual_cost = float(receipt_or_eval.get("cost_usd", actual_cost))
        actual_cap = float(receipt_or_eval.get("budget_cap_usd", actual_cap))
        actual_tokens = int(receipt_or_eval.get("tokens_used", actual_tokens))
        actual_model = str(receipt_or_eval.get("model_id", actual_model))
        effective_task_id = str(receipt_or_eval.get("task_id", effective_task_id))
        if "task_prompt" in receipt_or_eval and not task_prompt:
            task_prompt = str(receipt_or_eval["task_prompt"])

    if task_prompt and _TRIVIAL_TASK_HINTS.search(task_prompt):
        is_trivial = True

    if is_trivial_override is not None:
        is_trivial = is_trivial_override

    violations: List[str] = []
    utilization = round(actual_cost / max(0.0001, actual_cap), 4)

    if actual_cost > actual_cap:
        violations.append(
            f"G2:budget_cap_exceeded: cost_usd=${actual_cost:.4f} > budget_cap_usd=${actual_cap:.4f}"
        )

    if actual_tokens > token_budget_cap:
        violations.append(
            f"G2:token_budget_runaway: tokens_used={actual_tokens} exceeds {token_budget_cap} token ceiling"
        )

    is_frontier = actual_model.lower() in _FRONTIER_MODELS
    if is_trivial and is_frontier:
        violations.append(
            f"G2:frontier_model_overprovisioned: model='{actual_model}' used on trivial task"
        )

    passed = len(violations) == 0
    score = 1.0 if passed else max(0.0, round(1.0 - (len(violations) / 3.0), 4))

    metrics = {
        "cost_usd": round(actual_cost, 6),
        "budget_cap_usd": round(actual_cap, 6),
        "budget_utilization_ratio": utilization,
        "tokens_used": actual_tokens,
        "token_budget_cap": token_budget_cap,
        "model_id": actual_model,
        "task_id": effective_task_id,
        "trivial_task_detected": is_trivial,
        "frontier_model": is_frontier,
    }

    return {
        "gate_id": "G2_BUDGET_ROUTING",
        "gate_name": "SynapseRouter Cost & Tier Efficiency Gate",
        "passed": passed,
        "score": round(score, 6),
        "violations": violations,
        "metrics": metrics,
    }


def create_aegis_g2_leaf(
    receipt_or_eval: Optional[Union[RoutingDecisionReceipt, BudgetEvaluation, Dict[str, Any]]] = None,
    task_id: str = "synapse_routing",
    task_prompt: str = "",
    cost_usd: float = 0.0,
    budget_cap_usd: float = 1.0,
    tokens_used: int = 0,
    token_budget_cap: int = 120_000,
    model_id: str = "claude-3-5-sonnet",
    is_trivial_override: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Return the full G2 attestation package containing canonical payload,
    leaf hash, and RFC 6962 domain separation metadata.
    """
    payload = create_aegis_g2_payload(
        receipt_or_eval=receipt_or_eval,
        task_id=task_id,
        task_prompt=task_prompt,
        cost_usd=cost_usd,
        budget_cap_usd=budget_cap_usd,
        tokens_used=tokens_used,
        token_budget_cap=token_budget_cap,
        model_id=model_id,
        is_trivial_override=is_trivial_override,
    )
    leaf_bytes = canonical_json_bytes(payload)
    leaf_hash = hash_leaf_bytes(leaf_bytes)

    return {
        "payload": payload,
        "leaf_hash": leaf_hash,
        "rfc6962_prefix": "0x00",
        "canonical_bytes_len": len(leaf_bytes),
    }
