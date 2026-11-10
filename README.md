# synapse-router

[![CI](https://github.com/codesphere-u/synapse-router/actions/workflows/ci.yml/badge.svg)](https://github.com/codesphere-u/synapse-router/actions/workflows/ci.yml)
[![Python 3.10 | 3.11 | 3.12](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://github.com/codesphere-u/synapse-router)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Server](https://img.shields.io/badge/MCP-JSON--RPC%202.0-purple.svg)](src/synapse_router/server.py)
[![Version: 1.1.0](https://img.shields.io/badge/version-1.1.0-blue.svg)](CHANGELOG.md)
[![Tests: 77 Passed](https://img.shields.io/badge/tests-77%20passed-brightgreen.svg)](tests/)
[![Nexus Aegis Gate: G2](https://img.shields.io/badge/Nexus%20Aegis-G2__BUDGET__ROUTING-teal.svg)](https://github.com/codesphere-u/nexus-aegis)

**Cost-Aware Multi-Model AI Agent Router, Confidence-Gated Cascade Escalation, 3-State Circuit Breaker & 64-Bit SimHash Semantic Cache Gateway.**

---

## Why `synapse-router`? (The 2026 Multi-Agent Infrastructure Bottleneck)

Production multi-agent systems (**LangGraph**, **CrewAI**, **Pydantic AI**, and **MCP tool loops**) execute **10–50 LLM calls per user workflow**. Sending every trivial intent classification, entity extraction, JSON formatting, or tool-routing step to a frontier reasoning model (`o3-reasoning`, `claude-3-7-sonnet`) wastes **75–90% of token budgets** and violates sub-second latency SLAs—while relying on a single model provider causes total workflow failure during HTTP 429 rate-limit spikes or regional 5xx outages.

`synapse-router` is a zero-external-API-key, deterministic **Multi-Model Agent Routing, Confidence Cascade, Circuit-Breaking & Semantic Caching Engine** with a built-in **JSON-RPC 2.0 Model Context Protocol (MCP) Server** and reproducible CLI benchmark suite.

---

## System Architecture

```mermaid
flowchart TD
    Req["AgentStepRequest<br/>(prompt, tools_schema, require_json_schema, latency_sla_ms, max_cost_usd)"] --> Budget["TokenBudgetGovernor<br/>Hard Cap (100%) & Soft-Cap Tier Downgrade (80%)"]
    Budget -->|Allowed| Cache["SemanticPromptCache<br/>64-Bit Char/Word Shingle SimHash (Hamming <= 3)<br/>+ Tool/Schema Context Fingerprint Isolation"]
    Cache -->|Cache Hit (0.0 USD)| Receipt["Signed RoutingDecisionReceipt<br/>(HMAC-SHA256 Audit Trail)"]
    Cache -->|Cache Miss| Classifier["TaskComplexityClassifier<br/>Reasoning Markers + Code/AST Density + Tool Depth + Token Volume"]
    Classifier --> TierSelect["Pareto Utility Endpoint Selector<br/>U(m) = Q_prior - w_c * Cost_norm - w_l * Latency_EMA"]
    Health["EndpointHealthRegistry<br/>3-State Circuit Breaker (CLOSED / OPEN / HALF_OPEN)<br/>HTTP 429 Fast-Trip & EMA Latency Tracker"] <--> TierSelect
    TierSelect --> Fast["Tier 1: FAST_LOCAL<br/>(ollama/qwen2.5-coder-7b, gemini-2.5-flash-lite)"]
    Fast --> Validator["ResponseValidator<br/>JSON Schema + Non-Empty Tool Args + Refusal Check + Confidence >= min_confidence"]
    Validator -->|Valid & Confident| CacheStore["Update SimHash Cache & Budget Ledger"]
    Validator -->|Low Confidence / Invalid JSON / HTTP 429| Balanced["Tier 2: BALANCED<br/>(gemini-2.5-flash, claude-3-5-haiku)"]
    Balanced --> Validator2["ResponseValidator"]
    Validator2 -->|Valid & Confident| CacheStore
    Validator2 -->|Escalated| Frontier["Tier 3: FRONTIER_REASONING<br/>(claude-3-7-sonnet, openai/o3-reasoning)"]
    Frontier --> CacheStore
    CacheStore --> Receipt
```

---

## Key Capabilities

1. **Sub-Millisecond Prompt & Agent Step Complexity Analyzer (`classifier.py`)**:
   - Deterministic feature extraction across formal proof/architecture markers, code/AST syntax density, recursive JSON/tool schema depth, and log-scaled token volume.
   - Maps steps into `Tier.FAST_LOCAL`, `Tier.BALANCED`, or `Tier.FRONTIER_REASONING` with automatic tight-SLA adjustments (`latency_sla_ms < 280ms`).
2. **Pareto Utility Router & Confidence-Gated Cascade Engine (`router.py`)**:
   - Evaluates registered `ModelEndpoint` profiles against hard capability constraints (`supports_tools`, `supports_json_schema`, `context_window`, `latency_sla_ms`, `max_cost_usd`) and ranks healthy candidates by Pareto utility:
     $$\text{Utility}(m) = Q_{\text{prior}}(m) - w_c \cdot \widehat{C}(m) - w_\ell \cdot \widehat{L}_{\text{EMA}}(m)$$
   - Executes on the selected lower-cost tier first, validates output with `ResponseValidator` (strict JSON schema key/type validation, non-empty tool arguments, refusal/truncation detection, and `confidence >= min_confidence`), and escalates (`FAST_LOCAL -> BALANCED -> FRONTIER_REASONING`) **only** when validation or confidence fails.
   - Emits an HMAC-SHA256 signed `RoutingDecisionReceipt` recording every hop, latency, and exact USD saved vs. frontier-only routing.
3. **Rolling 3-State Provider Circuit Breaker (`health.py`)**:
   - Tracks per-endpoint rolling error rates, HTTP 429 rate-limit counters, 5xx errors, timeouts, and exponential moving average (EMA) latency.
   - Implements `CLOSED -> OPEN -> HALF_OPEN` state transitions with `< 0.05 ms` failover to healthy peer endpoints.
4. **64-Bit Locality-Sensitive SimHash Semantic Cache (`cache.py`)**:
   - Computes 64-bit SimHash fingerprints over weighted word unigrams, word bigrams, and 4-char shingles using `blake2b`.
   - Matches near-identical agent sub-prompts within a configurable bitwise Hamming distance (`<= 3` bits) while isolating cache entries by a canonical SHA-256 `context_fingerprint` of `system_prompt`, `tools_schema`, and `require_json_schema`.
5. **Tenant & Workflow Token/USD Budget Governor (`budget.py`)**:
   - Enforces per-workflow and per-tenant USD and token caps, automatically downgrading model tiers when utilization reaches `80%` (`soft_cap_downgrade`) and blocking execution at `100%`.
6. **Built-In JSON-RPC 2.0 MCP Server (`server.py`)**:
   - Exposes `route_agent_step`, `classify_task_complexity`, `inspect_endpoint_health`, and `check_budget_status` over standard MCP `tools/list` and `tools/call`.

---

## Reproducible Benchmark Results (500 Multi-Agent Requests across 6 Endpoints)

Run the benchmark suite directly via CLI:

```bash
python -m synapse_router.cli benchmark --requests 500
```

| Metric | Measured Value | Notes |
| :--- | :--- | :--- |
| **Workload Size** | `500` requests | Multi-step agent workflow across `6` model endpoints |
| **Routing Engine P50 Latency** | `742.56 µs` (`0.74 ms`) | End-to-end classification + SimHash lookup + Pareto scoring + receipt signing |
| **Routing Engine P95 Latency** | `1099.67 µs` (`1.10 ms`) | Includes multi-hop cascade escalation & failover paths |
| **Frontier-Only Baseline Cost** | `$3.7402 USD` | Baseline cost if all 500 steps routed to frontier reasoning tier |
| **Synapse-Router Actual Cost** | `$0.3520 USD` | Actual spend across tiered routing, cascades, and cache hits |
| **Total USD Cost Reduction** | **`90.59% saved`** (`$3.3881 USD` saved) | 10.6x reduction in multi-agent token spend |
| **Cascade Quality Retention** | **`100.00%`** | All 500 completed steps met `confidence >= 0.78` and schema validation |
| **Provider Failover Recovery Rate** | **`100.00%`** (`13/13` HTTP 429s) | Zero dropped workflow steps during injected HTTP 429 rate-limit spikes |
| **Semantic Cache Hit Rate** | `22.20%` | 64-bit SimHash near-duplicate detection (`Hamming distance <= 3`) |
| **Final Tier Distribution** | `57% FAST_LOCAL` / `23% BALANCED` / `20% FRONTIER` | `{"FAST_LOCAL": 285, "BALANCED": 115, "FRONTIER_REASONING": 100}` |

---

## 20-Step Multi-Agent Workflow Simulation (`demo`)

Run the 20-step workflow simulation (featuring injected HTTP 429 rate-limit spikes on `google/gemini-2.5-flash`, malformed JSON cascade escalation, low-confidence cascade escalation, and SimHash cache hits):

```bash
python -m synapse_router.cli demo
```

```text
==================================================================================================
SYNAPSE-ROUTER: 20-STEP MULTI-AGENT WORKFLOW DEMO (CASCADE + CIRCUIT BREAKER + SIMHASH CACHE)
==================================================================================================
STEP ID                    | SCORE | INIT TIER          | FINAL ENDPOINT               | HOPS |   COST ($)
--------------------------------------------------------------------------------------------------
step-01-intent             |  0.04 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000022
step-02-intent-near-dup    |  0.04 | FAST_LOCAL         | google/gemini-2.5-flash-lite [CACHE] |    0 |   0.000000
step-03-extract-entities   |  0.03 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000030
step-04-tool-lookup        |  0.45 | BALANCED           | google/gemini-2.5-flash      |    1 |   0.000232
step-05-cascade-json       |  0.05 | FAST_LOCAL         | google/gemini-2.5-flash      |    2 |   0.000121
step-06-rate-limit-429     |  0.43 | BALANCED           | anthropic/claude-3-5-haiku   |    2 |   0.001001
step-07-rate-limit-failover |  0.45 | BALANCED           | anthropic/claude-3-5-haiku   |    2 |   0.001001
step-08-frontier-arch      |  0.91 | FRONTIER_REASONING | anthropic/claude-3-7-sonnet  |    1 |   0.009240
step-09-tag-severity       |  0.02 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000011
step-10-tag-severity-cache |  0.02 | FAST_LOCAL         | google/gemini-2.5-flash-lite [CACHE] |    0 |   0.000000
step-11-code-audit         |  0.80 | FRONTIER_REASONING | anthropic/claude-3-7-sonnet  |    1 |   0.005367
step-12-summarize-slack    |  0.03 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000034
step-13-summarize-slack-dup |  0.03 | FAST_LOCAL         | google/gemini-2.5-flash-lite [CACHE] |    0 |   0.000000
step-14-sql-query-gen      |  0.49 | BALANCED           | anthropic/claude-3-5-haiku   |    1 |   0.000844
step-15-low-conf-cascade   |  0.03 | FAST_LOCAL         | anthropic/claude-3-5-haiku   |    2 |   0.000510
step-16-frontier-proof     |  0.68 | FRONTIER_REASONING | anthropic/claude-3-7-sonnet  |    1 |   0.008355
step-17-translate-note     |  0.03 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000028
step-18-extract-owners     |  0.04 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000020
step-19-postmortem-draft   |  0.70 | FRONTIER_REASONING | anthropic/claude-3-7-sonnet  |    1 |   0.004356
step-20-final-check        |  0.02 | FAST_LOCAL         | google/gemini-2.5-flash-lite |    1 |   0.000015
--------------------------------------------------------------------------------------------------
Frontier-Only Baseline Cost : $0.148660
Synapse-Router Actual Cost  : $0.031187
Total USD Cost Saved        : $0.117473 (79.02% reduction)
Semantic Cache Hits         : 3/20
Cascade / Failover Recoveries: 4 (100.0% workflow completion)
==================================================================================================
```

---

## Quickstart

### Installation

```bash
git clone https://github.com/codesphere-u/synapse-router.git
cd synapse-router
pip install -e ".[dev]"
```

### Python SDK Usage

```python
from synapse_router import AgentStepRequest, SynapseRouter

router = SynapseRouter()

# 1. Route a structured JSON extraction step (automatically selects Tier.FAST_LOCAL)
request = AgentStepRequest(
    prompt="Classify customer ticket intent into billing, outage, or security_audit.",
    require_json_schema={
        "type": "object",
        "required": ["intent", "priority"],
        "properties": {
            "intent": {"type": "string"},
            "priority": {"type": "string"},
        },
    },
    latency_sla_ms=300.0,
    min_confidence=0.80,
    tenant_id="acme-corp",
    workflow_id="ticket-triage-42",
    step_id="step-1",
)

receipt = router.execute_cascade(request)
print(f"Selected Endpoint : {receipt.selected_endpoint_id} ({receipt.final_tier})")
print(f"Actual Cost (USD) : ${receipt.total_cost_usd:.6f} (Saved ${receipt.cost_saved_usd:.6f})")
print(f"HMAC Receipt Sig  : {receipt.hmac_sha256_signature}")
```

### MCP Server Integration (Claude Desktop / Cursor / LangGraph MCP)

Start the stdio JSON-RPC 2.0 MCP server:

```bash
python -m synapse_router.cli mcp
```

Or configure in `mcp.json`:

```json
{
  "mcpServers": {
    "synapse-router": {
      "command": "python",
      "args": ["-m", "synapse_router.cli", "mcp"]
    }
  }
}
```

### Exposed MCP Tools (`tools/list`) & Detailed Usage Guide

| Tool Name | What is its use? (Purpose) | Key Arguments | Typical Output |
| :--- | :--- | :--- | :--- |
| `route_agent_step` | Route an AI agent workflow step across `FAST_LOCAL`, `BALANCED`, and `FRONTIER_REASONING` model tiers using Pareto cost/latency optimization, SimHash semantic caching, circuit-breaker failover, and confidence cascade escalation. | `prompt` (str), `system_prompt` (str), `latency_sla_ms` (float), `max_cost_usd` (float), `min_confidence` (float) | `{"selected_model": "...", "tier": "FAST_LOCAL", "cached": false, "estimated_cost_usd": 0.0001}` |
| `classify_task_complexity` | Compute deterministic task complexity score (`0.0–1.0`) and recommended model tier without executing model calls. | `prompt` (str), `system_prompt` (str), `tools_schema` (list) | `{"complexity_score": 0.35, "recommended_tier": "FAST_LOCAL", "feature_scores": {...}}` |
| `inspect_endpoint_health` | Inspect 3-state circuit breaker status (`CLOSED`, `OPEN`, `HALF_OPEN`), rolling error rates, HTTP 429 rate-limit counters, and EMA latency for model endpoints. | `endpoint_id` (optional str) | `{"endpoints": {"fast-llama": {"state": "CLOSED", "error_rate": 0.0, "p95_latency_ms": 42.1}}}` |
| `check_budget_status` | Query tenant and workflow token/USD budget utilization, remaining allowance, and 80% soft-cap tier downgrade state. | `tenant_id` (str), `workflow_id` (str) | `{"tenant_id": "...", "budget_usd": 100.0, "spent_usd": 14.20, "downgrade_active": false}` |
| `synapse_rate_limit_status` | Query sliding-window RPM and TPM consumption, ceiling limits, and saturation status for a model endpoint. | `endpoint_id` (str) | `{"endpoint_id": "...", "rpm_utilization": 0.12, "tpm_utilization": 0.08, "saturated": false}` |
| `synapse_export_aegis_g2` | Export canonical RFC 6962 G2_BUDGET_ROUTING attestation leaf for a routing receipt or simulation parameters. | `cost_usd` (float), `budget_cap_usd` (float), `model_id` (str), `task_prompt` (str) | `{"attestation_leaf": "...", "sha256": "...", "valid": true}` |

#### Tool Usage Examples

##### 1. `route_agent_step` — Intelligent Multi-Model Routing with Cost SLA
Route an agent's sub-task with strict latency and cost bounds:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "route_agent_step",
    "arguments": {
      "prompt": "Extract JSON fields from invoice: invoice_id, amount, due_date.",
      "latency_sla_ms": 250.0,
      "max_cost_usd": 0.002,
      "min_confidence": 0.85
    }
  }
}
```

##### 2. `classify_task_complexity` — Pre-Execution Task Triage
Determine whether a task needs an expensive frontier model (Claude 3.5 Sonnet / o1) or can be safely delegated to an ultra-fast local model (Llama-3.1-8B):
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "classify_task_complexity",
    "arguments": {
      "prompt": "Design a formal TLA+ distributed consensus spec with proof of non-blocking atomicity.",
      "tools_schema": []
    }
  }
}
```

---

## Architecture Decision Records (ADRs)

- [ADR-001: Pareto Utility Model Selection & Confidence-Gated Cascade Escalation](docs/adr/001-pareto-utility-and-confidence-cascade.md)
- [ADR-002: 64-Bit Locality-Sensitive SimHash Cache & 3-State Circuit Breaker](docs/adr/002-simhash-semantic-cache-and-circuit-breaker.md)

---

## Testing

Run the full 90-test suite:

```bash
pytest -v
```

All **90 tests** validate Pareto utility routing, SimHash semantic caching, confidence cascade escalation, 3-state circuit breakers, token/USD budget enforcement, rate limiters, RFC 6962 G2 attestation export, and Vercel serverless gateways.

---

## Hosted endpoint (Vercel)

A free, public, stateless deployment of Synapse Router runs over **MCP Streamable HTTP**:

| | |
|---|---|
| MCP endpoint | `https://synapse-router.vercel.app/mcp` (JSON-RPC 2.0 over `POST`) |
| Health probe | `https://synapse-router.vercel.app/health` |

```bash
curl -s -X POST https://synapse-router.vercel.app/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Client Configuration

#### 1. Claude Code CLI
```bash
# Add to current project:
claude mcp add --transport http synapse-router https://synapse-router.vercel.app/mcp

# Or add globally across all projects:
claude mcp add --scope user --transport http synapse-router https://synapse-router.vercel.app/mcp
```

#### 2. Claude Desktop / Cursor / Windsurf (`mcp.json`)
```json
{
  "mcpServers": {
    "synapse-router": {
      "type": "http",
      "url": "https://synapse-router.vercel.app/mcp"
    }
  }
}
```

**Hosted limitations:** All 6 tools are available. Router/cache/budget state is **in-memory and ephemeral** per serverless instance.

The hosted gateway is [`api/index.py`](api/index.py); it enforces a 1 MB body limit, strict JSON-RPC envelopes and the tool restrictions above. Deploy your own copy with `npx vercel deploy --prod` (entrypoint is configured in `pyproject.toml` under `[tool.vercel]`).

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Akandwanaho Ivan Alvin (`@codesphere-u`).
