"""Sliding-Window TPM (Tokens Per Minute) & RPM (Requests Per Minute) Rate Governor.

Prevents HTTP 429 provider rate-limit storms and manages per-endpoint, per-tenant,
and per-tier token throughput and request concurrency across AI agent workloads.
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limit parameters for a model endpoint or tenant pool."""

    max_rpm: int = 60
    max_tpm: int = 60_000
    window_seconds: float = 60.0
    burst_multiplier: float = 1.0


@dataclass(frozen=True)
class RateLimitDecision:
    """Result of rate limit evaluation prior to dispatching an LLM request."""

    allowed: bool
    endpoint_id: str
    current_rpm: int
    current_tpm: int
    max_rpm: int
    max_tpm: int
    utilization_rpm: float
    utilization_tpm: float
    retry_after_seconds: float
    reason: str


class RateGovernor:
    """Sliding-window RPM and TPM token bucket / rolling-window rate governor.

    Tracks timestamped consumption events (timestamp, token_count) in memory,
    eagerly prunes records older than `window_seconds`, and calculates exact
    `retry_after_seconds` wait times when quotas are saturated.
    """

    def __init__(
        self,
        default_rpm: int = 60,
        default_tpm: int = 60_000,
        window_seconds: float = 60.0,
    ) -> None:
        if default_rpm <= 0 or default_tpm <= 0 or window_seconds <= 0:
            raise ValueError("default_rpm, default_tpm, and window_seconds must be positive")

        self.default_config = RateLimitConfig(
            max_rpm=default_rpm,
            max_tpm=default_tpm,
            window_seconds=window_seconds,
        )
        self._configs: Dict[str, RateLimitConfig] = {}
        # endpoint_id -> deque of (timestamp: float, tokens: int)
        self._history: Dict[str, Deque[Tuple[float, int]]] = {}

    def configure_endpoint(
        self,
        endpoint_id: str,
        max_rpm: int,
        max_tpm: int,
        window_seconds: float = 60.0,
        burst_multiplier: float = 1.0,
    ) -> None:
        """Register custom rate limit constraints for a specific model endpoint."""
        if max_rpm <= 0 or max_tpm <= 0 or window_seconds <= 0:
            raise ValueError("Rate limits and window_seconds must be positive numbers")
        self._configs[endpoint_id] = RateLimitConfig(
            max_rpm=max_rpm,
            max_tpm=max_tpm,
            window_seconds=window_seconds,
            burst_multiplier=max(1.0, burst_multiplier),
        )

    def get_config(self, endpoint_id: str) -> RateLimitConfig:
        """Return the effective RateLimitConfig for an endpoint."""
        return self._configs.get(endpoint_id, self.default_config)

    def _get_history(self, endpoint_id: str) -> Deque[Tuple[float, int]]:
        if endpoint_id not in self._history:
            self._history[endpoint_id] = collections.deque()
        return self._history[endpoint_id]

    def _prune(self, endpoint_id: str, now: float, window_seconds: float) -> Deque[Tuple[float, int]]:
        q = self._get_history(endpoint_id)
        cutoff = now - window_seconds
        while q and q[0][0] <= cutoff:
            q.popleft()
        return q

    def check_rate_limit(
        self,
        endpoint_id: str,
        estimated_tokens: int = 0,
        now: Optional[float] = None,
    ) -> RateLimitDecision:
        """Evaluate whether an incoming request can proceed under RPM/TPM limits."""
        current_time = time.time() if now is None else float(now)
        cfg = self.get_config(endpoint_id)
        window = cfg.window_seconds
        effective_max_rpm = int(cfg.max_rpm * cfg.burst_multiplier)
        effective_max_tpm = int(cfg.max_tpm * cfg.burst_multiplier)

        history = self._prune(endpoint_id, current_time, window)
        current_rpm = len(history)
        current_tpm = sum(item[1] for item in history)

        util_rpm = round(current_rpm / effective_max_rpm, 4) if effective_max_rpm > 0 else 1.0
        util_tpm = round(current_tpm / effective_max_tpm, 4) if effective_max_tpm > 0 else 1.0

        # Check RPM breach
        if current_rpm >= effective_max_rpm:
            oldest_ts = history[0][0]
            retry_after = max(0.001, round((oldest_ts + window) - current_time, 3))
            return RateLimitDecision(
                allowed=False,
                endpoint_id=endpoint_id,
                current_rpm=current_rpm,
                current_tpm=current_tpm,
                max_rpm=effective_max_rpm,
                max_tpm=effective_max_tpm,
                utilization_rpm=util_rpm,
                utilization_tpm=util_tpm,
                retry_after_seconds=retry_after,
                reason=f"RPM limit exceeded ({current_rpm}/{effective_max_rpm} req/{window:.0f}s)",
            )

        # Check TPM breach with proposed estimated tokens
        if current_tpm + estimated_tokens > effective_max_tpm:
            # Find earliest timestamp when enough tokens will roll off
            needed_reduction = (current_tpm + estimated_tokens) - effective_max_tpm
            freed = 0
            oldest_ts = current_time
            for ts, tok in history:
                freed += tok
                oldest_ts = ts
                if freed >= needed_reduction:
                    break
            retry_after = max(0.001, round((oldest_ts + window) - current_time, 3))
            return RateLimitDecision(
                allowed=False,
                endpoint_id=endpoint_id,
                current_rpm=current_rpm,
                current_tpm=current_tpm,
                max_rpm=effective_max_rpm,
                max_tpm=effective_max_tpm,
                utilization_rpm=util_rpm,
                utilization_tpm=util_tpm,
                retry_after_seconds=retry_after,
                reason=(
                    f"TPM limit exceeded ({current_tpm + estimated_tokens} projected > "
                    f"{effective_max_tpm} tok/{window:.0f}s)"
                ),
            )

        return RateLimitDecision(
            allowed=True,
            endpoint_id=endpoint_id,
            current_rpm=current_rpm,
            current_tpm=current_tpm,
            max_rpm=effective_max_rpm,
            max_tpm=effective_max_tpm,
            utilization_rpm=util_rpm,
            utilization_tpm=util_tpm,
            retry_after_seconds=0.0,
            reason="Within sliding-window RPM and TPM limits",
        )

    def record_consumption(
        self,
        endpoint_id: str,
        tokens: int = 0,
        now: Optional[float] = None,
    ) -> RateLimitDecision:
        """Record an executed request and token consumption in the sliding window."""
        current_time = time.time() if now is None else float(now)
        cfg = self.get_config(endpoint_id)
        window = cfg.window_seconds
        effective_max_rpm = int(cfg.max_rpm * cfg.burst_multiplier)
        effective_max_tpm = int(cfg.max_tpm * cfg.burst_multiplier)

        history = self._prune(endpoint_id, current_time, window)
        history.append((current_time, max(0, int(tokens))))

        current_rpm = len(history)
        current_tpm = sum(item[1] for item in history)
        util_rpm = round(current_rpm / effective_max_rpm, 4) if effective_max_rpm > 0 else 1.0
        util_tpm = round(current_tpm / effective_max_tpm, 4) if effective_max_tpm > 0 else 1.0

        if current_rpm > effective_max_rpm or current_tpm > effective_max_tpm:
            oldest_ts = history[0][0]
            retry_after = max(0.001, round((oldest_ts + window) - current_time, 3))
            return RateLimitDecision(
                allowed=False,
                endpoint_id=endpoint_id,
                current_rpm=current_rpm,
                current_tpm=current_tpm,
                max_rpm=effective_max_rpm,
                max_tpm=effective_max_tpm,
                utilization_rpm=util_rpm,
                utilization_tpm=util_tpm,
                retry_after_seconds=retry_after,
                reason="Capacity exceeded upon recording consumption",
            )

        return RateLimitDecision(
            allowed=True,
            endpoint_id=endpoint_id,
            current_rpm=current_rpm,
            current_tpm=current_tpm,
            max_rpm=effective_max_rpm,
            max_tpm=effective_max_tpm,
            utilization_rpm=util_rpm,
            utilization_tpm=util_tpm,
            retry_after_seconds=0.0,
            reason="Recorded within sliding-window capacity",
        )

    def get_status(self, endpoint_id: str, now: Optional[float] = None) -> Dict[str, Any]:
        """Return a structured dictionary snapshot of an endpoint's rate utilization."""
        current_time = time.time() if now is None else float(now)
        cfg = self.get_config(endpoint_id)
        history = self._prune(endpoint_id, current_time, cfg.window_seconds)

        current_rpm = len(history)
        current_tpm = sum(item[1] for item in history)
        effective_max_rpm = int(cfg.max_rpm * cfg.burst_multiplier)
        effective_max_tpm = int(cfg.max_tpm * cfg.burst_multiplier)

        return {
            "endpoint_id": endpoint_id,
            "window_seconds": cfg.window_seconds,
            "current_rpm": current_rpm,
            "max_rpm": effective_max_rpm,
            "rpm_utilization_pct": round((current_rpm / effective_max_rpm) * 100.0, 2) if effective_max_rpm else 0.0,
            "current_tpm": current_tpm,
            "max_tpm": effective_max_tpm,
            "tpm_utilization_pct": round((current_tpm / effective_max_tpm) * 100.0, 2) if effective_max_tpm else 0.0,
            "saturated": current_rpm >= effective_max_rpm or current_tpm >= effective_max_tpm,
        }

    def reset(self, endpoint_id: Optional[str] = None) -> None:
        """Reset historical sliding window consumption records."""
        if endpoint_id is not None:
            self._history.pop(endpoint_id, None)
        else:
            self._history.clear()
