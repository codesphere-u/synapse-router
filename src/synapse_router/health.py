"""Rolling Provider Circuit Breaker & EMA Latency Tracker."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class CircuitState(str, Enum):
    """3-state circuit breaker狀態."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True)
class EndpointHealthSnapshot:
    """Point-in-time health and latency telemetry for a model endpoint."""

    endpoint_id: str
    state: CircuitState
    total_calls: int
    rolling_error_rate: float
    consecutive_failures: int
    rate_limit_429_count: int
    server_5xx_count: int
    timeout_count: int
    ema_latency_ms: float | None
    is_available: bool
    last_error_reason: str | None


class _EndpointState:
    def __init__(self, window_size: int) -> None:
        self.state: CircuitState = CircuitState.CLOSED
        self.total_calls: int = 0
        self.outcomes: deque[tuple[float, bool, str]] = deque(maxlen=window_size)
        self.consecutive_failures: int = 0
        self.half_open_successes: int = 0
        self.rate_limit_429_count: int = 0
        self.server_5xx_count: int = 0
        self.timeout_count: int = 0
        self.ema_latency_ms: float | None = None
        self.opened_at: float | None = None
        self.last_error_reason: str | None = None


class EndpointHealthRegistry:
    """Tracks rolling endpoint errors, EMA latency, and 3-state circuit breakers."""

    def __init__(
        self,
        failure_threshold: int = 3,
        error_rate_threshold: float = 0.50,
        min_calls_for_rate: int = 4,
        recovery_timeout_s: float = 15.0,
        half_open_max_successes: int = 2,
        window_size: int = 20,
        ema_alpha: float = 0.25,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.error_rate_threshold = error_rate_threshold
        self.min_calls_for_rate = min_calls_for_rate
        self.recovery_timeout_s = recovery_timeout_s
        self.half_open_max_successes = half_open_max_successes
        self.window_size = window_size
        self.ema_alpha = ema_alpha
        self._time_fn = time_fn
        self._endpoints: dict[str, _EndpointState] = {}

    def _get_or_create(self, endpoint_id: str) -> _EndpointState:
        if endpoint_id not in self._endpoints:
            self._endpoints[endpoint_id] = _EndpointState(self.window_size)
        return self._endpoints[endpoint_id]

    def _refresh_state(self, st: _EndpointState) -> None:
        if st.state == CircuitState.OPEN and st.opened_at is not None:
            now = self._time_fn()
            if (now - st.opened_at) >= self.recovery_timeout_s:
                st.state = CircuitState.HALF_OPEN
                st.half_open_successes = 0

    def is_available(self, endpoint_id: str) -> bool:
        """Check whether an endpoint is eligible to serve traffic (<0.01ms)."""
        st = self._get_or_create(endpoint_id)
        self._refresh_state(st)
        return st.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def get_state(self, endpoint_id: str) -> CircuitState:
        """Return current circuit state after checking cooldown timer."""
        st = self._get_or_create(endpoint_id)
        self._refresh_state(st)
        return st.state

    def record_success(self, endpoint_id: str, latency_ms: float) -> CircuitState:
        """Record a successful invocation and update EMA latency and circuit state."""
        st = self._get_or_create(endpoint_id)
        self._refresh_state(st)
        now = self._time_fn()

        st.total_calls += 1
        st.outcomes.append((now, True, "ok"))
        st.consecutive_failures = 0

        if st.ema_latency_ms is None:
            st.ema_latency_ms = float(latency_ms)
        else:
            st.ema_latency_ms = (
                self.ema_alpha * float(latency_ms)
                + (1.0 - self.ema_alpha) * st.ema_latency_ms
            )

        if st.state == CircuitState.HALF_OPEN:
            st.half_open_successes += 1
            if st.half_open_successes >= self.half_open_max_successes:
                st.state = CircuitState.CLOSED
                st.opened_at = None
                st.last_error_reason = None
                st.outcomes.clear()
                st.outcomes.append((now, True, "ok"))

        return st.state

    def record_failure(
        self,
        endpoint_id: str,
        error_type: str = "http_500",
        latency_ms: float | None = None,
    ) -> CircuitState:
        """Record an endpoint failure (e.g. 'http_429', 'http_503', 'timeout') and trip breaker if needed."""
        st = self._get_or_create(endpoint_id)
        self._refresh_state(st)
        now = self._time_fn()

        st.total_calls += 1
        st.outcomes.append((now, False, error_type))
        st.consecutive_failures += 1
        st.last_error_reason = error_type

        norm_type = error_type.lower()
        if "429" in norm_type or "rate_limit" in norm_type:
            st.rate_limit_429_count += 1
        elif "timeout" in norm_type:
            st.timeout_count += 1
        else:
            st.server_5xx_count += 1

        if latency_ms is not None:
            if st.ema_latency_ms is None:
                st.ema_latency_ms = float(latency_ms)
            else:
                st.ema_latency_ms = (
                    self.ema_alpha * float(latency_ms)
                    + (1.0 - self.ema_alpha) * st.ema_latency_ms
                )

        # Immediate trip if in HALF_OPEN or if consecutive failures / rolling error rate exceed threshold
        rolling_err = self._compute_error_rate(st)
        should_open = (
            st.state == CircuitState.HALF_OPEN
            or st.consecutive_failures >= self.failure_threshold
            or (len(st.outcomes) >= self.min_calls_for_rate and rolling_err >= self.error_rate_threshold)
            # Fast-trip on repeated HTTP 429 rate-limit spikes
            or ("429" in norm_type and st.consecutive_failures >= 2)
        )
        if should_open:
            st.state = CircuitState.OPEN
            st.opened_at = now
            st.half_open_successes = 0

        return st.state

    @staticmethod
    def _compute_error_rate(st: _EndpointState) -> float:
        if not st.outcomes:
            return 0.0
        failures = sum(1 for _, ok, _ in st.outcomes if not ok)
        return round(failures / len(st.outcomes), 4)

    def effective_latency_ms(self, endpoint_id: str, default_p50_ms: float) -> float:
        """Return observed EMA latency if available, blended with endpoint prior P50."""
        st = self._get_or_create(endpoint_id)
        if st.ema_latency_ms is None:
            return default_p50_ms
        return round(0.6 * st.ema_latency_ms + 0.4 * default_p50_ms, 2)

    def snapshot(self, endpoint_id: str) -> EndpointHealthSnapshot:
        """Return an immutable telemetry snapshot for an endpoint."""
        st = self._get_or_create(endpoint_id)
        self._refresh_state(st)
        return EndpointHealthSnapshot(
            endpoint_id=endpoint_id,
            state=st.state,
            total_calls=st.total_calls,
            rolling_error_rate=self._compute_error_rate(st),
            consecutive_failures=st.consecutive_failures,
            rate_limit_429_count=st.rate_limit_429_count,
            server_5xx_count=st.server_5xx_count,
            timeout_count=st.timeout_count,
            ema_latency_ms=round(st.ema_latency_ms, 2) if st.ema_latency_ms is not None else None,
            is_available=st.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN),
            last_error_reason=st.last_error_reason,
        )

    def all_snapshots(self) -> dict[str, EndpointHealthSnapshot]:
        """Return health snapshots for all tracked endpoints."""
        return {ep_id: self.snapshot(ep_id) for ep_id in sorted(self._endpoints.keys())}
