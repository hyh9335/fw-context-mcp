"""M9 Workload Probe — decides whether to enable certificate-gated reuse.

Per `.dsh-control/m9_frontend_reuse/WORKLOAD_PROBE_SPEC.md`:

> Do not blindly enable certificate work for every project.
> Build a cheap probe using safe signals such as number of Builds, per-TU
> flag/config variation, candidate TU overlap, estimated certificate-
> equivalent TU ratio, certificate cost, and historical hit rate.
> Allowed outcomes: ENABLE, DISABLE, SAMPLE/LEARN.
> Wrong ENABLE is tolerable only if safety remains intact and overhead
> remains under threshold. Wrong HIT is never acceptable.

Design
------
The probe runs in two phases:

1. **Pre-index probe** (cheap, no parsing): examines compile_commands.json
   and prior index state to estimate whether the workload will benefit.
   Signals: number of builds, per-TU argv variation, candidate TU overlap
   (same source compiled by multiple builds), per-build define overlap.

2. **In-index adaptive probe** (during indexing): for the first N builds,
   measures the actual certificate hit rate and overhead. If hit rate
   exceeds threshold, switches from SAMPLE to ENABLE. If below, switches
   to DISABLE (no further cert work for the rest of the run).

The probe NEVER compromises safety — it only decides whether the cert
computation is worth its cost. A DISABLE decision means full parsing
for every TU, which is the current behavior.

Outcomes
--------
- ``ENABLE``: cert layer active for all TUs.
- ``DISABLE``: cert layer skipped; full parse for every TU (status quo).
- ``SAMPLE/LEARN``: cert layer active for a sample of TUs; decision
  deferred until enough data is collected.

This file is the SKELETON. Real thresholds come from
``state/baseline_freeze_thresholds.json`` (produced by BASELINE_FREEZE).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ProbeSignals:
    """Inputs the probe uses to decide ENABLE/DISABLE/SAMPLE."""

    # Pre-index (cheap — no parse)
    num_builds: int = 0
    num_tus_total: int = 0
    num_tus_compiled_by_multiple_builds: int = 0  # candidate overlap
    per_tu_argv_variation_avg: float = 0.0  # mean distinct argv per source path
    per_build_define_overlap_ratio: float = 0.0  # Jaccard of -D sets across builds

    # In-index (measured during first N builds)
    certificate_cost_per_tu_ms: float = 0.0
    avoided_frontend_cost_per_tu_ms: float = 0.0
    measured_hit_rate: float = 0.0  # hits / (hits + misses) after first N builds
    builds_measured: int = 0

    # Historical (from prior runs, if available)
    historical_hit_rate: float | None = None


@dataclass
class ProbeDecision:
    policy: str  # "ENABLE" | "DISABLE" | "SAMPLE/LEARN"
    reason: str
    signals: ProbeSignals
    thresholds_applied: dict[str, float] = field(default_factory=dict)


# Default thresholds — overridden by state/baseline_freeze_thresholds.json
DEFAULT_THRESHOLDS = {
    "min_builds_to_consider": 2,         # 1 build = no reuse possible
    "min_candidate_overlap_ratio": 0.20,  # <20% overlap → likely no benefit
    "min_pre_estimate_enable_ratio": 0.30,  # pre-index estimate > 30% → ENABLE
    "max_pre_estimate_disable_ratio": 0.05,  # pre-index estimate < 5% → DISABLE
    "min_measured_hit_rate_to_enable": 0.20,  # measured > 20% → ENABLE
    "max_measured_hit_rate_to_disable": 0.05,  # measured < 5% → DISABLE
    "min_builds_measured_for_decision": 2,   # need >= 2 builds to decide
    "max_certificate_cost_per_tu_ms": 1.0,   # cert must be cheap
    "max_overhead_when_disabled_pct": 1.0,    # even DISABLED path must be cheap
}


def load_thresholds(state_dir: Path) -> dict[str, float]:
    """Load frozen thresholds; fall back to defaults."""
    p = state_dir / "baseline_freeze_thresholds.json"
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return {**DEFAULT_THRESHOLDS, **data}
        except Exception:
            pass
    return dict(DEFAULT_THRESHOLDS)


def pre_index_probe(
    signals: ProbeSignals,
    thresholds: dict[str, float],
) -> ProbeDecision:
    """Pre-index probe: estimate benefit from compile_commands.json signals.

    Conservative: when uncertain, return SAMPLE/LEARN (let the in-index
    probe decide with real data). Never ENABLE on estimate alone unless
    the estimate is strong.
    """
    # 1 build → no reuse possible
    if signals.num_builds < int(thresholds.get("min_builds_to_consider", 2)):
        return ProbeDecision(
            policy="DISABLE",
            reason=f"only {signals.num_builds} build(s) — no cross-build reuse possible",
            signals=signals,
            thresholds_applied=thresholds,
        )

    # Candidate overlap ratio
    overlap_ratio = (
        signals.num_tus_compiled_by_multiple_builds / signals.num_tus_total
        if signals.num_tus_total > 0 else 0.0
    )

    # Pre-index estimate: weighted combination of overlap + define overlap
    # (Both must be present for cert reuse to pay off: same source compiled
    #  by multiple builds WITH similar (but not identical) defines.)
    estimate = (
        overlap_ratio * 0.6 + signals.per_build_define_overlap_ratio * 0.4
    )

    if estimate >= thresholds.get("min_pre_estimate_enable_ratio", 0.30):
        return ProbeDecision(
            policy="ENABLE",
            reason=f"pre-index estimate {estimate:.2f} >= "
                   f"{thresholds.get('min_pre_estimate_enable_ratio', 0.30)}",
            signals=signals,
            thresholds_applied=thresholds,
        )

    if estimate <= thresholds.get("max_pre_estimate_disable_ratio", 0.05):
        return ProbeDecision(
            policy="DISABLE",
            reason=f"pre-index estimate {estimate:.2f} <= "
                   f"{thresholds.get('max_pre_estimate_disable_ratio', 0.05)}",
            signals=signals,
            thresholds_applied=thresholds,
        )

    return ProbeDecision(
        policy="SAMPLE/LEARN",
        reason=f"pre-index estimate {estimate:.2f} ambiguous — defer to in-index",
        signals=signals,
        thresholds_applied=thresholds,
    )


def in_index_adaptive_probe(
    signals: ProbeSignals,
    thresholds: dict[str, float],
    current_policy: str,
) -> ProbeDecision:
    """In-index adaptive probe: re-decide based on measured hit rate.

    Called after each build completes. Switches policy based on real
    measurements. Once ENABLED, stays ENABLED (no flip-flop). Once
    DISABLED on measurement, stays DISABLED.
    """
    if signals.builds_measured < int(thresholds.get("min_builds_measured_for_decision", 2)):
        return ProbeDecision(
            policy=current_policy,
            reason=f"only {signals.builds_measured} builds measured — keep current policy",
            signals=signals,
            thresholds_applied=thresholds,
        )

    # Cert cost sanity: if cert is too expensive, DISABLE regardless of hit rate
    if signals.certificate_cost_per_tu_ms > thresholds.get("max_certificate_cost_per_tu_ms", 1.0):
        return ProbeDecision(
            policy="DISABLE",
            reason=f"cert cost {signals.certificate_cost_per_tu_ms:.2f}ms > "
                   f"threshold — too expensive",
            signals=signals,
            thresholds_applied=thresholds,
        )

    if current_policy == "DISABLE":
        return ProbeDecision(
            policy="DISABLE",
            reason="already disabled — staying disabled",
            signals=signals,
            thresholds_applied=thresholds,
        )

    if signals.measured_hit_rate >= thresholds.get("min_measured_hit_rate_to_enable", 0.20):
        return ProbeDecision(
            policy="ENABLE",
            reason=f"measured hit rate {signals.measured_hit_rate:.2f} >= "
                   f"{thresholds.get('min_measured_hit_rate_to_enable', 0.20)}",
            signals=signals,
            thresholds_applied=thresholds,
        )

    if signals.measured_hit_rate <= thresholds.get("max_measured_hit_rate_to_disable", 0.05):
        return ProbeDecision(
            policy="DISABLE",
            reason=f"measured hit rate {signals.measured_hit_rate:.2f} <= "
                   f"{thresholds.get('max_measured_hit_rate_to_disable', 0.05)}",
            signals=signals,
            thresholds_applied=thresholds,
        )

    return ProbeDecision(
        policy="SAMPLE/LEARN",
        reason=f"measured hit rate {signals.measured_hit_rate:.2f} ambiguous — keep sampling",
        signals=signals,
        thresholds_applied=thresholds,
    )


def collect_pre_index_signals(
    compile_commands: list[dict],
    project_root: Path,
) -> ProbeSignals:
    """Collect cheap pre-index signals from compile_commands.json.

    No parsing — just argv analysis.
    """
    from fw_context_mcp.indexer.context_identity import (
        canonical_argv, defines_of_argv, raw_argv_of_entry,
    )

    # Group by source path (basename) to find candidate overlap
    by_source: dict[str, list[dict]] = {}
    per_build_defines: dict[str, set[str]] = {}
    builds_seen: set[str] = set()

    for entry in compile_commands:
        raw = raw_argv_of_entry(entry)
        src = entry.get("file", "")
        by_source.setdefault(src, []).append(entry)

        # Crude build id: directory or first -D flag set — caller should
        # pass build_id if known. For pre-index estimate, use the directory.
        build_id = entry.get("directory", "")
        builds_seen.add(build_id)
        defs = set(defines_of_argv(raw).keys())
        per_build_defines.setdefault(build_id, set()).update(defs)

    # Candidate overlap: sources compiled by more than one build
    multi_build_sources = sum(1 for src, entries in by_source.items()
                              if len(entries) > 1)

    # Per-TU argv variation: mean distinct argv per source
    total_variation = 0.0
    for src, entries in by_source.items():
        argvs = {tuple(canonical_argv(raw_argv_of_entry(e))) for e in entries}
        total_variation += len(argvs)
    avg_variation = total_variation / len(by_source) if by_source else 0.0

    # Per-build define overlap: mean pairwise Jaccard of -D sets
    build_list = list(per_build_defines.values())
    overlap_sum = 0.0
    pairs = 0
    for i in range(len(build_list)):
        for j in range(i + 1, len(build_list)):
            if not build_list[i] and not build_list[j]:
                continue
            union = build_list[i] | build_list[j]
            if not union:
                continue
            overlap = len(build_list[i] & build_list[j]) / len(union)
            overlap_sum += overlap
            pairs += 1
    avg_overlap = overlap_sum / pairs if pairs > 0 else 0.0

    return ProbeSignals(
        num_builds=len(builds_seen),
        num_tus_total=len(by_source),
        num_tus_compiled_by_multiple_builds=multi_build_sources,
        per_tu_argv_variation_avg=avg_variation,
        per_build_define_overlap_ratio=avg_overlap,
    )


def write_probe_result(
    state_dir: Path,
    decision: ProbeDecision,
    freertos_result: dict | None = None,
    per_file_result: dict | None = None,
) -> None:
    """Persist probe evaluation to state/workload_probe.json."""
    out = {
        "status": "EVALUATED",
        "policy": decision.policy,
        "reason": decision.reason,
        "signals": decision.signals.__dict__,
        "thresholds_applied": decision.thresholds_applied,
        "freertos_result": freertos_result,
        "per_file_result": per_file_result,
    }
    (state_dir / "workload_probe.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
