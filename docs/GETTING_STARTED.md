# Getting Started with Synapse-Router

`synapse-router` (`v1.2.0`) routes multi-step AI agent workflows across `FAST_LOCAL`, `BALANCED`, and `FRONTIER_REASONING` tiers with deterministic SimHash semantic caching, circuit-breaker failover, and Nexus Aegis G2 attestations.

## Installation & Benchmark

```bash
git clone https://github.com/codesphere-u/synapse-router.git
cd synapse-router
pip install -e ".[dev]"
synapse-router benchmark --requests 500
```

## Connect via MCP

```bash
claude mcp add --transport http synapse-router https://synapse-router.vercel.app/mcp
```
