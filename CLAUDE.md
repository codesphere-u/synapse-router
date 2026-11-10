# CLAUDE.md — Synapse Router Engineering Guide

## Project Overview
`synapse-router` is a zero-dependency, pure Python 3.10+ **Cost-Aware Multi-Model AI Agent Router, Confidence-Gated Cascade Escalation, 3-State Circuit Breaker & 64-Bit SimHash Semantic Cache Gateway**. It slashes token costs by 75–90% across multi-agent workflows by delegating simple steps to fast local endpoints while reserving frontier reasoning models for complex tasks.

* **Package Name (PyPI)**: `synapse-router` (CLI entry point: `synapse-router`, module: `synapse_router`)
* **MCP Specification**: Tripartite support for **Tools**, **Resources**, and **Prompts** (Protocol version `2024-11-05`).
* **Hosted Production**: `https://synapse-router.vercel.app/mcp` (Vercel Serverless Streamable HTTP + SSE).
* **Interactive Portal**: `https://synapse-router.vercel.app/` (Developer Playground).

---

## Build, Test & Lint Commands

```bash
# Run the complete test suite
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run the built-in reproducible benchmark suite
uv run python -m synapse_router.cli benchmark

# Run the end-to-end routing demonstration
uv run python -m synapse_router.cli demo

# Check token and USD rate limiter status
uv run python -m synapse_router.cli rate-status

# Build sdist and wheel distributions
uv build

# Run local stdio MCP server
uv run python -m synapse_router.cli mcp
```

---

## Architecture & Code Structure

```text
src/synapse_router/
├── __init__.py         # Package exports
├── router.py           # ParetoUtilityRouter: cost/latency/quality utility optimization
├── classifier.py       # TaskComplexityClassifier: AST density, reasoning markers, depth
├── cache.py            # SemanticPromptCache: 64-bit character/word shingle SimHash (Hamming <= 3)
├── circuit_breaker.py  # 3-State circuit breaker (CLOSED, OPEN, HALF_OPEN) with HTTP 429 fast-trip
├── budget.py           # TokenBudgetGovernor: hard cap (100%) & soft-cap tier downgrade (80%)
├── rate_limiter.py     # Sliding-window token and USD rate limiters per tenant
├── aegis_bridge.py     # Nexus Aegis G2 budget routing attestation exporter (RFC 6962)
├── server.py           # SynapseMCPServer: Model Context Protocol JSON-RPC 2.0 engine
└── cli.py              # CLI commands (demo, benchmark, rate-status, export-aegis, mcp)

api/
├── index.py            # Vercel Serverless FastAPI gateway (rate-limited, sandboxed)
└── portal.py           # Zero-dependency interactive Developer Portal & Playground
```

---

## Model Context Protocol (MCP) Primitives

### 1. Tools (`tools/list`, `tools/call`)
* `route_agent_step`: Route a single agent turn with Pareto utility selection, semantic caching, and SLA enforcement.
* `classify_task_complexity`: Score prompt complexity, reasoning intensity, and code density.
* `inspect_endpoint_health`: Query real-time circuit breaker states and EMA latency across all endpoints.
* `check_budget_status`: Query token spend and USD budget utilization against soft/hard caps.
* `synapse_rate_limit_status`: Query sliding-window rate limiter counters per tenant.
* `synapse_export_aegis_g2`: Export cryptographic HMAC-SHA256 routing decision receipts for Nexus Aegis G2 audits.

### 2. Resources (`resources/list`, `resources/read`)
* `synapse://endpoints/catalog`: Registry of model endpoints across tiers (`FAST_LOCAL`, `BALANCED`, `FRONTIER_REASONING`).
* `synapse://health/summary`: Real-time circuit breaker states, sliding-window rate limit counters, and budget spend.

### 3. Prompts (`prompts/list`, `prompts/get`)
* `route-task-advisor`: Analyze task complexity and recommend optimal model tiers and fallback cascades.
* `budget-allocation-planner`: Configure sliding-window token and USD budgets with 80% soft-cap degradation.

---

## Claude Code Quick-Connect

```bash
# Add to Claude Code project scope:
claude mcp add --transport http synapse-router https://synapse-router.vercel.app/mcp

# Or user scope (globally across all projects):
claude mcp add --scope user --transport http synapse-router https://synapse-router.vercel.app/mcp
```
