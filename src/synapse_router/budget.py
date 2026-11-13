"""Tenant & Workflow Token/USD Budget Governor with Soft-Cap Tier Downgrade."""

from __future__ import annotations

from dataclasses import dataclass

from synapse_router.classifier import Tier


class BudgetExceededError(RuntimeError):
    """Raised when a tenant or workflow exceeds its hard token or USD budget cap."""


@dataclass
class _UsageLedger:
    spent_usd: float = 0.0
    spent_tokens: int = 0
    step_count: int = 0


@dataclass(frozen=True)
class BudgetEvaluation:
    """Budget check and tier governance result prior to model routing."""

    tenant_id: str
    workflow_id: str
    allowed: bool
    soft_cap_downgrade: bool
    effective_max_tier: Tier
    workflow_utilization_ratio: float
    tenant_utilization_ratio: float
    remaining_workflow_usd: float
    remaining_tenant_usd: float
    reason: str


class TokenBudgetGovernor:
    """Enforces per-workflow and per-tenant USD/token budgets with 80% soft-cap tier downgrade."""

    def __init__(
        self,
        default_workflow_max_usd: float = 0.50,
        default_workflow_max_tokens: int = 100_000,
        default_tenant_max_usd: float = 10.00,
        default_tenant_max_tokens: int = 2_000_000,
        soft_cap_ratio: float = 0.80,
    ) -> None:
        if not (0.10 <= soft_cap_ratio < 1.0):
            raise ValueError("soft_cap_ratio must be in [0.10, 1.0)")
        self.default_workflow_max_usd = default_workflow_max_usd
        self.default_workflow_max_tokens = default_workflow_max_tokens
        self.default_tenant_max_usd = default_tenant_max_usd
        self.default_tenant_max_tokens = default_tenant_max_tokens
        self.soft_cap_ratio = soft_cap_ratio

        self._workflow_limits_usd: dict[str, float] = {}
        self._workflow_limits_tokens: dict[str, int] = {}
        self._tenant_limits_usd: dict[str, float] = {}
        self._tenant_limits_tokens: dict[str, int] = {}

        self._workflow_usage: dict[str, _UsageLedger] = {}
        self._tenant_usage: dict[str, _UsageLedger] = {}

    def set_workflow_budget(self, workflow_id: str, max_usd: float, max_tokens: int | None = None) -> None:
        """Configure custom hard budget caps for a specific workflow."""
        self._workflow_limits_usd[workflow_id] = max_usd
        if max_tokens is not None:
            self._workflow_limits_tokens[workflow_id] = max_tokens

    def set_tenant_budget(self, tenant_id: str, max_usd: float, max_tokens: int | None = None) -> None:
        """Configure custom hard budget caps for a specific tenant."""
        self._tenant_limits_usd[tenant_id] = max_usd
        if max_tokens is not None:
            self._tenant_limits_tokens[tenant_id] = max_tokens

    def _get_workflow_ledger(self, workflow_id: str) -> _UsageLedger:
        if workflow_id not in self._workflow_usage:
            self._workflow_usage[workflow_id] = _UsageLedger()
        return self._workflow_usage[workflow_id]

    def _get_tenant_ledger(self, tenant_id: str) -> _UsageLedger:
        if tenant_id not in self._tenant_usage:
            self._tenant_usage[tenant_id] = _UsageLedger()
        return self._tenant_usage[tenant_id]

    def evaluate(
        self,
        tenant_id: str,
        workflow_id: str,
        requested_tier: Tier,
        estimated_cost_usd: float = 0.0,
        estimated_tokens: int = 0,
    ) -> BudgetEvaluation:
        """Evaluate budget state and apply soft-cap tier downgrade (>=80%) or hard-cap block (>=100%)."""
        wf_ledger = self._get_workflow_ledger(workflow_id)
        tn_ledger = self._get_tenant_ledger(tenant_id)

        wf_max_usd = self._workflow_limits_usd.get(workflow_id, self.default_workflow_max_usd)
        wf_max_tok = self._workflow_limits_tokens.get(workflow_id, self.default_workflow_max_tokens)
        tn_max_usd = self._tenant_limits_usd.get(tenant_id, self.default_tenant_max_usd)
        tn_max_tok = self._tenant_limits_tokens.get(tenant_id, self.default_tenant_max_tokens)

        projected_wf_usd = wf_ledger.spent_usd + estimated_cost_usd
        projected_wf_tok = wf_ledger.spent_tokens + estimated_tokens
        projected_tn_usd = tn_ledger.spent_usd + estimated_cost_usd
        projected_tn_tok = tn_ledger.spent_tokens + estimated_tokens

        wf_ratio = max(
            wf_ledger.spent_usd / max(1e-9, wf_max_usd),
            wf_ledger.spent_tokens / max(1, wf_max_tok),
        )
        tn_ratio = max(
            tn_ledger.spent_usd / max(1e-9, tn_max_usd),
            tn_ledger.spent_tokens / max(1, tn_max_tok),
        )

        rem_wf_usd = max(0.0, round(wf_max_usd - wf_ledger.spent_usd, 6))
        rem_tn_usd = max(0.0, round(tn_max_usd - tn_ledger.spent_usd, 6))

        # Check hard cap (100% reached or projected to exceed)
        if (
            wf_ledger.spent_usd >= wf_max_usd
            or wf_ledger.spent_tokens >= wf_max_tok
            or projected_wf_usd > wf_max_usd
            or projected_wf_tok > wf_max_tok
        ):
            return BudgetEvaluation(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                allowed=False,
                soft_cap_downgrade=False,
                effective_max_tier=Tier.FAST_LOCAL,
                workflow_utilization_ratio=round(wf_ratio, 4),
                tenant_utilization_ratio=round(tn_ratio, 4),
                remaining_workflow_usd=rem_wf_usd,
                remaining_tenant_usd=rem_tn_usd,
                reason=f"workflow_hard_cap_exceeded ({wf_ledger.spent_usd:.4f}/{wf_max_usd:.4f} USD)",
            )

        if (
            tn_ledger.spent_usd >= tn_max_usd
            or tn_ledger.spent_tokens >= tn_max_tok
            or projected_tn_usd > tn_max_usd
            or projected_tn_tok > tn_max_tok
        ):
            return BudgetEvaluation(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                allowed=False,
                soft_cap_downgrade=False,
                effective_max_tier=Tier.FAST_LOCAL,
                workflow_utilization_ratio=round(wf_ratio, 4),
                tenant_utilization_ratio=round(tn_ratio, 4),
                remaining_workflow_usd=rem_wf_usd,
                remaining_tenant_usd=rem_tn_usd,
                reason=f"tenant_hard_cap_exceeded ({tn_ledger.spent_usd:.4f}/{tn_max_usd:.4f} USD)",
            )

        # Check soft cap (>= 80% utilization) -> automatic tier downgrade
        max_ratio = max(wf_ratio, tn_ratio)
        if max_ratio >= self.soft_cap_ratio:
            if max_ratio >= 0.92:
                downgraded_tier = Tier.FAST_LOCAL
            else:
                downgraded_tier = Tier(max(int(Tier.FAST_LOCAL), int(requested_tier) - 1))
            return BudgetEvaluation(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                allowed=True,
                soft_cap_downgrade=True,
                effective_max_tier=downgraded_tier,
                workflow_utilization_ratio=round(wf_ratio, 4),
                tenant_utilization_ratio=round(tn_ratio, 4),
                remaining_workflow_usd=rem_wf_usd,
                remaining_tenant_usd=rem_tn_usd,
                reason=f"soft_cap_downgrade_triggered (utilization={max_ratio:.1%})",
            )

        return BudgetEvaluation(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            allowed=True,
            soft_cap_downgrade=False,
            effective_max_tier=Tier.FRONTIER_REASONING,
            workflow_utilization_ratio=round(wf_ratio, 4),
            tenant_utilization_ratio=round(tn_ratio, 4),
            remaining_workflow_usd=rem_wf_usd,
            remaining_tenant_usd=rem_tn_usd,
            reason="within_budget",
        )

    def record_spend(
        self,
        tenant_id: str,
        workflow_id: str,
        cost_usd: float,
        tokens: int,
    ) -> None:
        """Commit actual token and USD spend to workflow and tenant ledgers."""
        wf = self._get_workflow_ledger(workflow_id)
        tn = self._get_tenant_ledger(tenant_id)

        wf.spent_usd = round(wf.spent_usd + max(0.0, cost_usd), 8)
        wf.spent_tokens += max(0, int(tokens))
        wf.step_count += 1

        tn.spent_usd = round(tn.spent_usd + max(0.0, cost_usd), 8)
        tn.spent_tokens += max(0, int(tokens))
        tn.step_count += 1

    def get_status(self, tenant_id: str, workflow_id: str) -> dict[str, object]:
        """Return structured budget ledger status for inspection and MCP tools."""
        wf = self._get_workflow_ledger(workflow_id)
        tn = self._get_tenant_ledger(tenant_id)
        eval_res = self.evaluate(tenant_id=tenant_id, workflow_id=workflow_id, requested_tier=Tier.FRONTIER_REASONING)
        return {
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "workflow_spent_usd": round(wf.spent_usd, 6),
            "workflow_max_usd": self._workflow_limits_usd.get(workflow_id, self.default_workflow_max_usd),
            "workflow_spent_tokens": wf.spent_tokens,
            "workflow_max_tokens": self._workflow_limits_tokens.get(workflow_id, self.default_workflow_max_tokens),
            "workflow_utilization_ratio": eval_res.workflow_utilization_ratio,
            "tenant_spent_usd": round(tn.spent_usd, 6),
            "tenant_max_usd": self._tenant_limits_usd.get(tenant_id, self.default_tenant_max_usd),
            "tenant_spent_tokens": tn.spent_tokens,
            "tenant_max_tokens": self._tenant_limits_tokens.get(tenant_id, self.default_tenant_max_tokens),
            "tenant_utilization_ratio": eval_res.tenant_utilization_ratio,
            "soft_cap_downgrade_active": eval_res.soft_cap_downgrade,
            "allowed": eval_res.allowed,
            "reason": eval_res.reason,
        }
