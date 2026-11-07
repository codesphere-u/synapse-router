"""Unit tests for EndpointHealthRegistry (3-state circuit breaker) and SemanticPromptCache (64-bit SimHash)."""

from __future__ import annotations

import pytest

from synapse_router.cache import (
    SemanticPromptCache,
    compute_context_fingerprint,
    compute_simhash64,
    hamming_distance64,
)
from synapse_router.health import CircuitState, EndpointHealthRegistry


class FakeClock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_circuit_breaker_starts_closed_and_tracks_ema_latency() -> None:
    clock = FakeClock()
    reg = EndpointHealthRegistry(time_fn=clock)
    ep = "google/gemini-2.5-flash"

    assert reg.get_state(ep) == CircuitState.CLOSED
    assert reg.is_available(ep) is True

    reg.record_success(ep, latency_ms=200.0)
    reg.record_success(ep, latency_ms=280.0)
    snap = reg.snapshot(ep)
    assert snap.state == CircuitState.CLOSED
    assert snap.total_calls == 2
    assert snap.rolling_error_rate == 0.0
    assert snap.ema_latency_ms == pytest.approx(220.0, rel=1e-3)
    assert reg.effective_latency_ms(ep, default_p50_ms=240.0) == pytest.approx(228.0, rel=1e-3)


def test_circuit_breaker_closed_to_open_to_half_open_to_closed() -> None:
    clock = FakeClock()
    reg = EndpointHealthRegistry(
        failure_threshold=3,
        recovery_timeout_s=15.0,
        half_open_max_successes=2,
        time_fn=clock,
    )
    ep = "anthropic/claude-3-7-sonnet"

    reg.record_failure(ep, error_type="http_503", latency_ms=400.0)
    reg.record_failure(ep, error_type="timeout", latency_ms=1500.0)
    assert reg.get_state(ep) == CircuitState.CLOSED

    # 3rd consecutive failure trips circuit to OPEN
    reg.record_failure(ep, error_type="http_502", latency_ms=300.0)
    assert reg.get_state(ep) == CircuitState.OPEN
    assert reg.is_available(ep) is False

    # Before cooldown expires, still OPEN
    clock.advance(10.0)
    assert reg.get_state(ep) == CircuitState.OPEN
    assert reg.is_available(ep) is False

    # After 15s cooldown, transitions to HALF_OPEN and allows probe traffic
    clock.advance(6.0)
    assert reg.get_state(ep) == CircuitState.HALF_OPEN
    assert reg.is_available(ep) is True

    # First probe success keeps it in HALF_OPEN
    reg.record_success(ep, latency_ms=500.0)
    assert reg.get_state(ep) == CircuitState.HALF_OPEN

    # Second probe success closes the circuit
    reg.record_success(ep, latency_ms=480.0)
    assert reg.get_state(ep) == CircuitState.CLOSED
    snap = reg.snapshot(ep)
    assert snap.consecutive_failures == 0
    assert snap.rolling_error_rate == 0.0
    assert snap.server_5xx_count == 2
    assert snap.timeout_count == 1


def test_circuit_breaker_fast_trips_on_repeated_429_and_reopens_from_half_open() -> None:
    clock = FakeClock()
    reg = EndpointHealthRegistry(failure_threshold=4, recovery_timeout_s=10.0, time_fn=clock)
    ep = "openai/o3-reasoning"

    # Two consecutive HTTP 429 rate-limit errors fast-trip the circuit even when failure_threshold=4
    reg.record_failure(ep, error_type="http_429_rate_limit")
    assert reg.get_state(ep) == CircuitState.CLOSED
    reg.record_failure(ep, error_type="http_429_rate_limit")
    assert reg.get_state(ep) == CircuitState.OPEN
    assert reg.snapshot(ep).rate_limit_429_count == 2

    # Advance past recovery timeout -> HALF_OPEN
    clock.advance(11.0)
    assert reg.get_state(ep) == CircuitState.HALF_OPEN

    # Any failure in HALF_OPEN immediately trips back to OPEN
    reg.record_failure(ep, error_type="http_500")
    assert reg.get_state(ep) == CircuitState.OPEN


def test_circuit_breaker_trips_on_rolling_error_rate() -> None:
    clock = FakeClock()
    reg = EndpointHealthRegistry(
        failure_threshold=5,
        error_rate_threshold=0.50,
        min_calls_for_rate=4,
        time_fn=clock,
    )
    ep = "ollama/qwen2.5-coder-7b"
    reg.record_success(ep, 80.0)
    reg.record_failure(ep, "http_500")
    reg.record_success(ep, 85.0)
    assert reg.get_state(ep) == CircuitState.CLOSED
    # Now 2 failures out of 4 calls = 50% error rate -> trips to OPEN
    reg.record_failure(ep, "http_500")
    assert reg.get_state(ep) == CircuitState.OPEN
    assert ep in reg.all_snapshots()


def test_simhash64_near_duplicate_vs_distinct_prompts() -> None:
    p1 = "Classify customer support ticket intent into billing, outage, or security_audit."
    p2 = "Classify customer support ticket intent into billing, outage, or security_audit!"
    p3 = "Classify customer support ticket intent into: billing, outage, or security_audit"
    p_different = "Architect a distributed Raft consensus storage engine in Rust with zero-copy IO."

    h1 = compute_simhash64(p1)
    h2 = compute_simhash64(p2)
    h3 = compute_simhash64(p3)
    h_diff = compute_simhash64(p_different)

    assert hamming_distance64(h1, h2) == 0
    assert hamming_distance64(h1, h3) <= 2
    assert hamming_distance64(h1, h_diff) >= 15
    assert compute_simhash64("   ") == 0


def test_semantic_cache_hit_near_duplicate_and_tool_context_isolation() -> None:
    clock = FakeClock()
    cache = SemanticPromptCache(max_hamming_distance=3, ttl_seconds=60.0, time_fn=clock)

    prompt_a = (
        "Summarize the database connection timeout incident timeline in three concise bullet "
        "points for the engineering status channel."
    )
    prompt_a_near = (
        "Summarize the database connection timeout incident timeline in three concise bullet "
        "points for the engineering status channel!"
    )

    tools_v1 = [{"name": "post_slack", "parameters": {"channel": "eng"}}]
    tools_v2 = [{"name": "send_pagerduty", "parameters": {"severity": "critical"}}]

    cache.put(
        prompt=prompt_a,
        response_text="• DB pool exhausted\n• Mitigated via replica failover\n• Zero data loss",
        endpoint_id="google/gemini-2.5-flash-lite",
        tier_name="FAST_LOCAL",
        confidence=0.94,
        tools_schema=tools_v1,
    )

    # Same tool context + near-identical prompt -> HIT
    res_hit = cache.lookup(prompt=prompt_a_near, tools_schema=tools_v1)
    assert res_hit.hit is True
    assert res_hit.hamming_distance is not None and res_hit.hamming_distance <= 3
    assert res_hit.entry is not None
    assert res_hit.entry.hit_count == 1

    # Incompatible tool schema context -> MISS (prevents cross-tool cache poisoning)
    res_isolated = cache.lookup(prompt=prompt_a, tools_schema=tools_v2)
    assert res_isolated.hit is False
    assert compute_context_fingerprint(tools_schema=tools_v1) != compute_context_fingerprint(tools_schema=tools_v2)


def test_semantic_cache_ttl_expiration_and_lru_eviction() -> None:
    clock = FakeClock()
    cache = SemanticPromptCache(max_hamming_distance=2, ttl_seconds=30.0, max_entries=2, time_fn=clock)

    cache.put("First distinct prompt about Kubernetes pod scheduling", "resp-1", "ep-1", "FAST_LOCAL")
    cache.put("Second distinct prompt about PostgreSQL vacuum tuning", "resp-2", "ep-1", "FAST_LOCAL")
    cache.put("Third distinct prompt about BGP route reflector peering", "resp-3", "ep-1", "FAST_LOCAL")

    # Max entries is 2, so first entry was evicted via LRU
    assert cache.stats()["entries"] == 2
    assert cache.lookup("First distinct prompt about Kubernetes pod scheduling").hit is False
    assert cache.lookup("Third distinct prompt about BGP route reflector peering").hit is True

    # Advance past 30s TTL -> all entries expire
    clock.advance(31.0)
    assert cache.lookup("Third distinct prompt about BGP route reflector peering").hit is False
    assert cache.stats()["entries"] == 0


def test_semantic_cache_invalid_hamming_distance() -> None:
    with pytest.raises(ValueError):
        SemanticPromptCache(max_hamming_distance=20)
