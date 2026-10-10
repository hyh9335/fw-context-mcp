"""M7 scanner: economic metrics measurement.

Reports all §39 metrics SEPARATELY (never one combined "reuse %" metric).
Consumed by run_m7_economic_gate.py and run_m7_master_gate.py.

At BOOTSTRAP: captures the M7.0B FreeRTOS scaling table as the PRE-migration
baseline. The actual migrated measurements are taken at Economic Closure
(after M7.5), run against the SAME corpus + compiler + machine.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _git_head() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def load_freertos_scaling() -> list[dict]:
    """Load the M7.0B scaling table (PRE-migration baseline).

    Structure: {"test":..., "prefixes":[2,5,10,16], "data":[{prefix, builds, total_facts, unique_facts, shared_facts, private_facts, distinct_buildsets, frontend_cost_s, materialization_cost_s, projection_latency_ms, projection_latency_total_ms}, ...]}
    """
    p = REPO / ".dsh-control" / "corpora" / "reports" / "freertos_matrix_scaling.json"
    if not p.exists():
        p = REPO / ".dsh-control" / "evidence" / "freertos_matrix_scaling.json"
    if not p.exists():
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    rows = raw.get("data", raw.get("scaling", []))
    # Normalize field names for downstream consumers
    out = []
    for r in rows:
        out.append({
            "n_builds": r.get("prefix", r.get("n_builds")),
            "builds": r.get("builds", []),
            "total_facts": r.get("total_facts"),
            "unique_facts": r.get("unique_facts"),
            "shared_facts": r.get("shared_facts"),
            "build_private_facts": r.get("private_facts", r.get("build_private_facts")),
            "distinct_buildsets": r.get("distinct_buildsets"),
            "frontend_s": r.get("frontend_cost_s", r.get("frontend_s")),
            "materialize_s": r.get("materialization_cost_s", r.get("materialize_s")),
            "proj_per_build_ms": r.get("projection_latency_ms", r.get("proj_per_build_ms")),
        })
    return out


def scan() -> dict:
    scaling = load_freertos_scaling()
    return {
        "head": _git_head(),
        "stage_note": "BOOTSTRAP: PRE-migration baseline from M7.0B FreeRTOS scaling table. Migrated measurements taken at Economic Closure.",
        "metrics_measured_separately": [
            "frontend_preprocess_parse",
            "semantic_materialization",
            "cross_tu_resolution",
            "storage_write",
            "finalization",
            "total_wall_time",
            "semantic_storage_bytes",
            "unique_facts",
            "shared_facts",
            "build_private_facts",
            "distinct_buildsets",
            "query_p50",
            "query_p95",
            "query_p99",
        ],
        "baseline_scaling_table": scaling,
        "baseline_frontend_share_at_16_builds": (
            (scaling[-1].get("frontend_s", 0) or 0) / ((scaling[-1].get("frontend_s", 0) or 0) + (scaling[-1].get("materialize_s", 0) or 0))
            if scaling and len(scaling) >= 1 and (scaling[-1].get("frontend_s") or scaling[-1].get("materialize_s"))
            else None
        ),
        "note": "If frontend_preprocess_parse > 0.7 * total_wall_time at 16-build scale AND semantic_materialization < 0.3 * total_wall_time, classify ORIGINAL_GOAL_REMAINING_FRONTEND_BOTTLENECK.",
    }


def main() -> int:
    state = scan()
    out = REPO / ".dsh-control" / "m7" / "state" / "economic_metrics_state.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[scan_economic_metrics] HEAD={state['head']} baseline_rows={len(state['baseline_scaling_table'])}")
    if state["baseline_frontend_share_at_16_builds"] is not None:
        print(f"  baseline frontend share @ 16 builds: {state['baseline_frontend_share_at_16_builds']:.2%}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
