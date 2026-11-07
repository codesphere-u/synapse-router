"""Locality-Sensitive 64-Bit SimHash Semantic Prompt Cache with Tool Isolation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass(frozen=True)
class CachedResponseEntry:
    """Stored response artifact in the semantic cache."""

    cache_key: str
    simhash: int
    context_fingerprint: str
    endpoint_id: str
    tier_name: str
    response_text: str
    confidence: float
    created_at: float
    expires_at: float
    hit_count: int = 0


@dataclass(frozen=True)
class CacheLookupResult:
    """Result of a semantic cache query."""

    hit: bool
    hamming_distance: Optional[int]
    entry: Optional[CachedResponseEntry]


_TOKEN_NORM_RE = re.compile(r"[a-z0-9_]+")


def compute_simhash64(text: str) -> int:
    """Compute a 64-bit Locality-Sensitive SimHash over word unigrams, bigrams, and 4-char shingles."""
    normalized = " ".join(_TOKEN_NORM_RE.findall(text.lower()))
    if not normalized:
        return 0

    words = normalized.split()
    features: List[tuple[str, int]] = []

    # Word unigrams (weight 3)
    for w in words:
        features.append((f"w:{w}", 3))

    # Word bigrams (weight 2)
    for i in range(len(words) - 1):
        features.append((f"b:{words[i]}_{words[i + 1]}", 2))

    # Character 4-grams (weight 1) for sub-word resilience
    compact = normalized.replace(" ", "_")
    for i in range(max(1, len(compact) - 3)):
        features.append((f"c:{compact[i:i + 4]}", 1))

    v = [0] * 64
    for feat, weight in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        h = int.from_bytes(digest, byteorder="big", signed=False)
        for bit_idx in range(64):
            if (h >> bit_idx) & 1:
                v[bit_idx] += weight
            else:
                v[bit_idx] -= weight

    fingerprint = 0
    for bit_idx in range(64):
        if v[bit_idx] > 0:
            fingerprint |= 1 << bit_idx
    return fingerprint


def hamming_distance64(a: int, b: int) -> int:
    """Return exact bitwise Hamming distance between two 64-bit integers."""
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


def compute_context_fingerprint(
    system_prompt: str = "",
    tools_schema: Optional[List[Dict[str, Any]]] = None,
    require_json_schema: Optional[Dict[str, Any]] = None,
) -> str:
    """Compute canonical SHA-256 fingerprint for system prompt, tools, and output schema."""
    payload = {
        "system_prompt": " ".join(system_prompt.strip().lower().split()),
        "tools_schema": tools_schema or [],
        "require_json_schema": require_json_schema or {},
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


class SemanticPromptCache:
    """64-bit SimHash semantic cache with Hamming distance matching, TTL, and tool-schema isolation."""

    def __init__(
        self,
        max_hamming_distance: int = 3,
        ttl_seconds: float = 300.0,
        max_entries: int = 1024,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (0 <= max_hamming_distance <= 16):
            raise ValueError("max_hamming_distance must be between 0 and 16")
        self.max_hamming_distance = max_hamming_distance
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._time_fn = time_fn
        self._store: "OrderedDict[str, CachedResponseEntry]" = OrderedDict()
        self.hits: int = 0
        self.misses: int = 0

    def _purge_expired(self, now: float) -> None:
        expired_keys = [
            k for k, entry in self._store.items() if entry.expires_at <= now
        ]
        for k in expired_keys:
            self._store.pop(k, None)

    def lookup(
        self,
        prompt: str,
        system_prompt: str = "",
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        require_json_schema: Optional[Dict[str, Any]] = None,
    ) -> CacheLookupResult:
        """Find the closest non-expired cached entry within `max_hamming_distance` in the same tool context."""
        now = self._time_fn()
        self._purge_expired(now)

        ctx_fp = compute_context_fingerprint(
            system_prompt=system_prompt,
            tools_schema=tools_schema,
            require_json_schema=require_json_schema,
        )
        query_hash = compute_simhash64(prompt)

        best_entry: Optional[CachedResponseEntry] = None
        best_dist: Optional[int] = None

        for key, entry in list(self._store.items()):
            if entry.context_fingerprint != ctx_fp:
                continue
            dist = hamming_distance64(query_hash, entry.simhash)
            if dist <= self.max_hamming_distance:
                if best_dist is None or dist < best_dist:
                    best_dist = dist
                    best_entry = entry
                    if dist == 0:
                        break

        if best_entry is not None and best_dist is not None:
            self.hits += 1
            updated = CachedResponseEntry(
                cache_key=best_entry.cache_key,
                simhash=best_entry.simhash,
                context_fingerprint=best_entry.context_fingerprint,
                endpoint_id=best_entry.endpoint_id,
                tier_name=best_entry.tier_name,
                response_text=best_entry.response_text,
                confidence=best_entry.confidence,
                created_at=best_entry.created_at,
                expires_at=best_entry.expires_at,
                hit_count=best_entry.hit_count + 1,
            )
            self._store[best_entry.cache_key] = updated
            self._store.move_to_end(best_entry.cache_key)
            return CacheLookupResult(hit=True, hamming_distance=best_dist, entry=updated)

        self.misses += 1
        return CacheLookupResult(hit=False, hamming_distance=None, entry=None)

    def put(
        self,
        prompt: str,
        response_text: str,
        endpoint_id: str,
        tier_name: str,
        confidence: float = 0.95,
        system_prompt: str = "",
        tools_schema: Optional[List[Dict[str, Any]]] = None,
        require_json_schema: Optional[Dict[str, Any]] = None,
        ttl_seconds: Optional[float] = None,
    ) -> CachedResponseEntry:
        """Insert a validated response into the semantic cache."""
        now = self._time_fn()
        self._purge_expired(now)

        ctx_fp = compute_context_fingerprint(
            system_prompt=system_prompt,
            tools_schema=tools_schema,
            require_json_schema=require_json_schema,
        )
        simhash = compute_simhash64(prompt)
        cache_key = f"{ctx_fp}:{simhash:016x}"
        effective_ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds

        entry = CachedResponseEntry(
            cache_key=cache_key,
            simhash=simhash,
            context_fingerprint=ctx_fp,
            endpoint_id=endpoint_id,
            tier_name=tier_name,
            response_text=response_text,
            confidence=confidence,
            created_at=now,
            expires_at=now + effective_ttl,
            hit_count=0,
        )
        self._store[cache_key] = entry
        self._store.move_to_end(cache_key)

        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

        return entry

    def stats(self) -> Dict[str, Any]:
        """Return cache utilization and hit-rate metrics."""
        total = self.hits + self.misses
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total > 0 else 0.0,
            "max_hamming_distance": self.max_hamming_distance,
            "ttl_seconds": self.ttl_seconds,
        }
