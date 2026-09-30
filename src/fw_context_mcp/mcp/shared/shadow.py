"""Shadow dual-read infrastructure for v2 authority migration (M1).

Implements the SHADOW DUAL READ mode (P11):

    production request
    → legacy authoritative response  (user sees this)
    +
    → v2 shadow response             (compared, logged)
    → semantic comparator / telemetry

The user ALWAYS receives the legacy result in M1. The v2 shadow runs in
parallel (or sequentially for simplicity in M1) and its result is
compared to the legacy result by a semantic comparator that classifies
mismatches (P12).

This is MIGRATION INFRASTRUCTURE (P38). It is gated by
``FW_CONTEXT_SHADOW_V2=1`` (the same env var that gates v2 write). When
the flag is off, NO shadow runs — production is unchanged.

MIGRATION TELEMETRY (P33): counters track legacy/v2/shadow query counts
and mismatch classes. Use telemetry to know when legacy can be retired.

Shadow comparator classes (P12):
    MATCH                 — semantic content agrees
    PRESENTATION_ONLY     — same symbols, different field formatting
    ORDER_ONLY            — same set, different order
    EXPECTED_DIFFERENCE   — v2 captures more/less by design (documented)
    LEGACY_BUG            — legacy result is wrong, v2 is correct
    V2_BUG                — v2 result is wrong, legacy is correct
    LEGACY_AMBIGUITY      — legacy couldn't resolve, v2 could (or vice versa)
    V2_UNSUPPORTED        — v2 has no implementation for this surface
    UNKNOWN               — comparator couldn't classify
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ── Shadow flag ────────────────────────────────────────────────────────────

def shadow_v2_enabled() -> bool:
    """Whether v2 shadow dual-read is enabled.

    Gated by ``FW_CONTEXT_SHADOW_V2=1`` env var (same as v2 write).
    When False, NO shadow runs — production is 100% legacy, unchanged.
    """
    return os.environ.get("FW_CONTEXT_SHADOW_V2") == "1"


# ── Telemetry (P33) ────────────────────────────────────────────────────────

@dataclass
class ShadowTelemetry:
    """Migration telemetry counters (P33).

    Thread-safe. Reset by ``reset_telemetry()`` (for tests).
    """
    legacy_authoritative_queries: int = 0
    v2_shadow_queries: int = 0
    v2_shadow_failures: int = 0
    v2_shadow_unsupported: int = 0
    mismatches: dict[str, int] = field(default_factory=dict)
    queryview_resolution_failures: int = 0
    fallback_attempts: int = 0  # should be 0 where forbidden (P3)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_mismatch(self, classification: str) -> None:
        with self._lock:
            self.mismatches[classification] = \
                self.mismatches.get(classification, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "legacy_authoritative_queries": self.legacy_authoritative_queries,
                "v2_shadow_queries": self.v2_shadow_queries,
                "v2_shadow_failures": self.v2_shadow_failures,
                "v2_shadow_unsupported": self.v2_shadow_unsupported,
                "mismatches": dict(self.mismatches),
                "queryview_resolution_failures": self.queryview_resolution_failures,
                "fallback_attempts": self.fallback_attempts,
            }

    def reset(self) -> None:
        with self._lock:
            self.legacy_authoritative_queries = 0
            self.v2_shadow_queries = 0
            self.v2_shadow_failures = 0
            self.v2_shadow_unsupported = 0
            self.mismatches.clear()
            self.queryview_resolution_failures = 0
            self.fallback_attempts = 0


_telemetry = ShadowTelemetry()


def get_telemetry() -> ShadowTelemetry:
    """Return the global shadow telemetry instance."""
    return _telemetry


def reset_telemetry() -> None:
    """Reset telemetry counters (for tests)."""
    _telemetry.reset()


# ── Shadow result ───────────────────────────────────────────────────────────

@dataclass
class ShadowResult:
    """Result of one shadow dual-read comparison.

    The ``legacy_result`` is what the user received. The ``v2_result`` is
    what the v2 shadow produced (or None if v2 failed/unsupported). The
    ``classification`` says how they compare.
    """
    tool_name: str
    legacy_result: Any
    v2_result: Any | None
    classification: str  # MATCH, PRESENTATION_ONLY, etc.
    detail: dict = field(default_factory=dict)
    legacy_latency_ms: float = 0.0
    v2_latency_ms: float = 0.0
    error: str | None = None  # v2 shadow error (if any)


# ── Comparators (P12) ──────────────────────────────────────────────────────

def _normalize_symbol_list(rows: list[dict]) -> frozenset[str]:
    """Extract symbol identity set from a list of symbol dicts.

    For CROSS-AUTHORITY comparison (legacy vs v2), we use ``name`` as the
    primary identity key because legacy results don't carry ``usr``.
    Within one authority, USR would be more precise, but for shadow
    comparison we need a key both sides share.

    Two results agree if they reference the same set of symbol names
    (qualified by kind when available, since a name can be both a class
    and a constructor).
    """
    ids = set()
    for r in rows:
        if isinstance(r, dict):
            name = r.get("name") or r.get("qualified_name")
            kind = r.get("kind", "")
            if name:
                # Include kind in the key to distinguish e.g. class ImuSensor
                # from constructor ImuSensor (same name, different kind)
                ids.add(f"{kind}:{name}" if kind else f":{name}")
    return frozenset(ids)


def classify_symbol_lists(
    tool_name: str,
    legacy: list[dict],
    v2: list[dict] | None,
) -> tuple[str, dict]:
    """Classify the comparison of two symbol-list results.

    Returns (classification, detail).
    """
    if v2 is None:
        return "V2_UNSUPPORTED", {"reason": "v2 returned None"}
    if not isinstance(v2, list):
        return "UNKNOWN", {"reason": f"v2 result type {type(v2).__name__} != list"}
    if not isinstance(legacy, list):
        return "UNKNOWN", {"reason": f"legacy result type {type(legacy).__name__} != list"}

    leg_set = _normalize_symbol_list(legacy)
    v2_set = _normalize_symbol_list(v2)

    if leg_set == v2_set:
        # Same symbol set — check order
        if len(legacy) == len(v2):
            # Check if order differs
            leg_order = [r.get("name", "") for r in legacy if isinstance(r, dict)]
            v2_order = [r.get("name", "") for r in v2 if isinstance(r, dict)]
            if leg_order != v2_order:
                return "ORDER_ONLY", {
                    "legacy_count": len(legacy),
                    "v2_count": len(v2),
                }
        return "MATCH", {
            "legacy_count": len(legacy),
            "v2_count": len(v2),
        }

    # Different sets — classify the difference
    only_legacy = leg_set - v2_set
    only_v2 = v2_set - leg_set
    detail = {
        "legacy_count": len(legacy),
        "v2_count": len(v2),
        "only_in_legacy": len(only_legacy),
        "only_in_v2": len(only_v2),
    }
    if only_legacy and not only_v2:
        # v2 has fewer — could be v2 bug or expected (v2 filters differently)
        return "EXPECTED_DIFFERENCE", detail
    if only_v2 and not only_legacy:
        # v2 has more — v2 captures more (documented for some surfaces)
        return "EXPECTED_DIFFERENCE", detail
    if only_legacy and only_v2:
        # Both have unique items — potential bug or ambiguity
        return "UNKNOWN", detail
    return "MATCH", detail


# ── Shadow runner (M5-2: migration scaffolding — compat-only) ───────────────
# run_shadow was the M1/M2 dual-read comparator. After M3B (v2 read
# authority) and M5 (v2-only default), it is unreachable in normal mode.
# Retained as compat-island diagnostic; gated behind _legacy_compat_on().

def run_shadow(
    tool_name: str,
    legacy_fn: Callable,
    v2_fn: Callable | None,
    comparator: Callable[[str, Any, Any], tuple[str, dict]] | None = None,
    *args, **kwargs,
) -> tuple[Any, ShadowResult | None]:
    """Run legacy authoritatively + v2 as shadow, return (legacy_result, shadow).

    M5-2: this function is migration scaffolding. It is only reachable when
    legacy compatibility mode is explicitly enabled. In normal v2-only mode,
    the v2 read path uses route_authority (v2 first, no dual-read comparison).

    Args:
        tool_name: Name of the MCP tool (for telemetry/logging).
        legacy_fn: The legacy handler function. Called with *args, **kwargs.
        v2_fn: The v2 shadow function, or None if v2 doesn't support this
            surface yet. Called with the SAME *args, **kwargs (the v2
            adapter is responsible for mapping them).
        comparator: Function ``(tool_name, legacy_result, v2_result) →
            (classification, detail)``. If None, uses ``classify_symbol_lists``
            for list results, else a default comparator.

    Returns:
        (legacy_result, shadow_result_or_None). The shadow_result is None
        when shadow is disabled or v2_fn is None.
    """
    # M5-2: this function is migration scaffolding (compat-only). In
    # v2-only mode it is never called. The entire body below is gated.
    try:
        from fw_context_mcp.indexer._unit_processor import _legacy_compat_on
        _m5_compat = _legacy_compat_on()
    except Exception:
        _m5_compat = False
    if _m5_compat:
        # Legacy is ALWAYS authoritative in M1
        t0 = time.perf_counter()
        legacy_result = legacy_fn(*args, **kwargs)
        legacy_ms = (time.perf_counter() - t0) * 1000

        _telemetry.legacy_authoritative_queries += 1

        if not shadow_v2_enabled() or v2_fn is None:
            if v2_fn is None:
                _telemetry.v2_shadow_unsupported += 1
            return legacy_result, None

        # Run v2 shadow
        _telemetry.v2_shadow_queries += 1
        t0 = time.perf_counter()
        try:
            v2_result = v2_fn(*args, **kwargs)
            v2_ms = (time.perf_counter() - t0) * 1000
        except Exception as e:
            v2_ms = (time.perf_counter() - t0) * 1000
            _telemetry.v2_shadow_failures += 1
            shadow = ShadowResult(
                tool_name=tool_name,
                legacy_result=legacy_result,
                v2_result=None,
                classification="V2_SHADOW_ERROR",
                detail={"error": str(e), "error_type": type(e).__name__},
                legacy_latency_ms=legacy_ms,
                v2_latency_ms=v2_ms,
                error=f"{type(e).__name__}: {e}",
            )
            log.debug("v2 shadow error for %s: %s", tool_name, e)
            return legacy_result, shadow

        # Classify
        if comparator is None:
            comparator = _default_comparator
        classification, detail = comparator(tool_name, legacy_result, v2_result)

        if classification != "MATCH":
            _telemetry.record_mismatch(classification)
            log.info("Shadow mismatch for %s: %s detail=%s",
                     tool_name, classification, detail)

        shadow = ShadowResult(
            tool_name=tool_name,
            legacy_result=legacy_result,
            v2_result=v2_result,
            classification=classification,
            detail=detail,
            legacy_latency_ms=legacy_ms,
            v2_latency_ms=v2_ms,
        )
        return legacy_result, shadow
    # v2-only mode: run_shadow should never be called. Fallback.
    return legacy_fn(*args, **kwargs), None


def _default_comparator(
    tool_name: str,
    legacy: Any,
    v2: Any,
) -> tuple[str, dict]:
    """Default comparator — dispatches by result type."""
    if isinstance(legacy, list) and isinstance(v2, list):
        return classify_symbol_lists(tool_name, legacy, v2)
    if isinstance(legacy, dict) and isinstance(v2, dict):
        # Check for error/info records
        if "error" in legacy and "error" not in v2:
            return "LEGACY_AMBIGUITY", {"legacy_error": legacy.get("error")}
        if "error" in v2 and "error" not in legacy:
            return "V2_BUG", {"v2_error": v2.get("error")}
        if "info" in legacy and "info" not in v2:
            return "EXPECTED_DIFFERENCE", {"legacy_info": legacy.get("info")}
        # Compare dict keys
        leg_keys = set(legacy.keys()) - {"_warning", "warning"}
        v2_keys = set(v2.keys()) - {"_warning", "warning"}
        if leg_keys == v2_keys:
            return "MATCH", {"key_count": len(leg_keys)}
        return "PRESENTATION_ONLY", {
            "legacy_keys": sorted(leg_keys),
            "v2_keys": sorted(v2_keys),
        }
    if legacy == v2:
        return "MATCH", {}
    return "UNKNOWN", {"type_legacy": type(legacy).__name__,
                        "type_v2": type(v2).__name__}
