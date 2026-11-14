# Changelog

All notable changes to `synapse-router` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-11-14

### Added
- **Editorial Website & Interactive Multi-Model Routing Studio (`website/`)**:
  - Multi-page Warm Ivory (`#fffdf2`) editorial website with Electric Cerulean (`#0284c7`) and Sunburst Marigold (`#f59e0b`) accents across `index.html`, `studio.html`, `proof.html`, `rules.html`, and `docs.html`.
  - Interactive `studio.html` simulator for Pareto tier selection (`FAST_LOCAL`, `BALANCED`, `FRONTIER_REASONING`), 64-bit SimHash semantic cache matching, 3-state circuit breaker failover, sliding-window `RateGovernor` checks, and Nexus Aegis `G2_BUDGET_ROUTING` RFC 6962 leaf export.
  - Full SEO/GEO suite (`llms.txt`, `llms-full.txt`, `robots.txt`, `sitemap.xml`, `manifest.webmanifest`, Schema.org `@graph` JSON-LD, and `website/marketing/` playbooks).
- **Documentation, Pages & Verification**:
  - Added `docs/GETTING_STARTED.md`, `docs/ROUTING_AND_AEGIS_G2.md`, `.github/workflows/pages.yml`, and `tests/test_website_and_docs.py`.
  - Added strict type annotations to `scripts/smoke_test.py` and bumped package/MCP/gateway version to `1.2.0`.

## [1.1.0] - 2026-11-07

### Added
- **Sliding-Window TPM & RPM Rate Governor (`synapse_router.rate_limiter`)**:
  - `RateGovernor` engine with eager timestamp pruning across configurable rolling windows (default 60s).
  - Exact `retry_after_seconds` calculation on request (RPM) or token throughput (TPM) quota saturation.
  - Per-endpoint custom rate profiles with `burst_multiplier` support.
  - Preemptive candidate down-ranking to prevent HTTP 429 provider rate-limit storms.
- **Nexus Aegis G2 Bridge (`synapse_router.aegis_bridge`)**:
  - Canonical `G2_BUDGET_ROUTING` attestation payload generator.
  - RFC 6962 domain-separated (`0x00`) SHA-256 leaf digest calculation.
  - Comprehensive enforcement of USD budget caps, 120,000 token ceilings, and anti-overprovisioning rules (blocking frontier models on trivial agent prompts).
  - Direct integration method `SynapseRouter.export_aegis_g2(receipt)`.
- **MCP Server Expansion**:
  - Added `synapse_rate_limit_status` tool to inspect real-time RPM/TPM saturation metrics.
  - Added `synapse_export_aegis_g2` tool to generate verifiable RFC 6962 attestation leaves over JSON-RPC 2.0.
  - Bumped MCP server protocol version to 1.1.0.
- **CLI Commands**:
  - Added `synapse-router rate-status [--endpoint <id>] [--json]` for CLI rate inspection.
  - Added `synapse-router export-aegis` for instant G2 leaf generation.

### Changed
- Integrated automatic consumption recording into `SynapseRouter.execute_cascade()`.
- Bumped version to `1.1.0`.

---

## [1.0.0] - 2026-11-07

### Added
- Initial open-source release of `synapse-router`.
- SLA/Cost-aware Pareto endpoint scoring across Fast Local, Balanced, and Frontier Reasoning tiers.
- 64-bit SimHash locality-sensitive semantic prompt caching with Hamming distance matching.
- 3-State circuit breaker (`CLOSED`, `OPEN`, `HALF_OPEN`) with exponential backoff on HTTP 429/5xx errors.
- Confidence-gated cascade escalation with HMAC-SHA256 signed receipts.
- Model Context Protocol (MCP) JSON-RPC 2.0 stdio server.
