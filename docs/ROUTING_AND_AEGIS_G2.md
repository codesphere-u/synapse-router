# Routing Cascade, RateGovernor & Nexus Aegis G2

## 6 Exposed MCP Tools

| Tool | Purpose |
| :--- | :--- |
| `route_agent_step` | Route step across tiers with SimHash cache, 429 failover, and signed receipt |
| `classify_task_complexity` | Compute deterministic `[0.0, 1.0]` complexity score and recommended tier |
| `inspect_endpoint_health` | Inspect 3-state circuit breaker status, error rate, and EMA latency |
| `check_budget_status` | Evaluate 80% soft-cap tier downgrade and 100% hard USD/token caps |
| `synapse_rate_limit_status` | Report rolling 60s RPM/TPM saturation and `retry_after_seconds` |
| `synapse_export_aegis_g2` | Generate RFC 6962 `0x00`-prefixed SHA-256 `G2_BUDGET_ROUTING` leaf |

## CLI Rate & Aegis Inspection

```bash
synapse-router rate-status --json
synapse-router export-aegis --cost 0.003 --budget 0.05 --tokens 900
```
