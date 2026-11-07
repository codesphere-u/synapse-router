"""Synapse-Router: Cost-Aware Multi-Model AI Agent Router & Cascade Gateway."""

from synapse_router.aegis_bridge import create_aegis_g2_leaf, create_aegis_g2_payload
from synapse_router.budget import BudgetEvaluation, BudgetExceededError, TokenBudgetGovernor
from synapse_router.cache import (
    CachedResponseEntry,
    CacheLookupResult,
    SemanticPromptCache,
    compute_context_fingerprint,
    compute_simhash64,
    hamming_distance64,
)
from synapse_router.classifier import (
    AgentStepRequest,
    ComplexityAnalysis,
    TaskComplexityClassifier,
    Tier,
)
from synapse_router.health import CircuitState, EndpointHealthRegistry, EndpointHealthSnapshot
from synapse_router.rate_limiter import RateGovernor, RateLimitConfig, RateLimitDecision
from synapse_router.router import (
    CascadeHop,
    EndpointCandidateScore,
    ModelEndpoint,
    ModelInvocationResult,
    NoHealthyEndpointError,
    ResponseValidator,
    RoutingDecisionReceipt,
    SynapseRouter,
    ValidationOutcome,
    default_endpoint_catalog,
)
from synapse_router.server import MCP_TOOLS_SPEC, SynapseMCPServer

__version__ = "1.1.0"

__all__ = [
    "AgentStepRequest",
    "BudgetEvaluation",
    "BudgetExceededError",
    "CacheLookupResult",
    "CachedResponseEntry",
    "CascadeHop",
    "CircuitState",
    "ComplexityAnalysis",
    "EndpointCandidateScore",
    "EndpointHealthRegistry",
    "EndpointHealthSnapshot",
    "MCP_TOOLS_SPEC",
    "ModelEndpoint",
    "ModelInvocationResult",
    "NoHealthyEndpointError",
    "RateGovernor",
    "RateLimitConfig",
    "RateLimitDecision",
    "ResponseValidator",
    "RoutingDecisionReceipt",
    "SemanticPromptCache",
    "SynapseMCPServer",
    "SynapseRouter",
    "TaskComplexityClassifier",
    "Tier",
    "TokenBudgetGovernor",
    "ValidationOutcome",
    "compute_context_fingerprint",
    "compute_simhash64",
    "create_aegis_g2_leaf",
    "create_aegis_g2_payload",
    "default_endpoint_catalog",
    "hamming_distance64",
]
