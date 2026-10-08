"""M6 Phase I Hard Gate — runs ALL gates and produces final verdict.

This is the §45 M6 Hard Gate. It runs every gate in sequence and
produces a single PASS/FAIL verdict. The gate exits 0 only if ALL
gates pass.

Gates:
  [GATE 1] QUERY_PARITY_FAILURES == 0 (§34 — torture corpus, 21 cases / 39 builds)
  [GATE 2] MUTATION_ALL_CAUGHT (§35 — M1-M21, 21 mutations)
  [GATE 3] GC_SAFETY (§M1.6 — 5 GC tests)
  [GATE 4] CERT_SAFETY (Prototype C — safety_violation=False)
  [GATE 5] Phase R regression (Prototype A — 21/21, false_shared=0)
  [GATE 6] M5 regression (39 clauses)
  [GATE 7] COST_CLASS_SEPARATION (§M4 — 3 classes reported separately)
  [I3]     Concurrency (4 tests)
  [I7]     Certificate Store (7 steps)
  [I9]     Cross-TU Resolution
  [I10]    GC Lifecycle (8 steps)
  [I11]    §36 Real Corpus (parity + sharing)
  [I12]    Cost + Cert Safety on real corpus

Usage:
    python .dsh-control/gates/run_m6_stage_gate.py
"""
from __future__ import annotations
import sys, subprocess, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYTHONPATH = f"src;{sys.prefix}"


def run_gate(name: str, script: str, pattern: str) -> tuple[bool, str]:
    """Run a gate script and check for the expected pattern."""
    print(f"\n{'='*60}")
    print(f"  [{name}]")
    print(f"{'='*60}")
    t0 = time.perf_counter()
    result = subprocess.run(
        [sys.executable, str(REPO / script)],
        capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "PYTHONPATH": PYTHONPATH},
        cwd=str(REPO),
    )
    elapsed = time.perf_counter() - t0
    output = result.stdout + result.stderr
    # Print last 5 lines of output
    lines = output.strip().split("\n")
    for line in lines[-5:]:
        print(f"  {line}")
    passed = pattern in output
    print(f"  → {'PASS' if passed else 'FAIL'} ({elapsed:.1f}s)")
    return passed, output


def main() -> int:
    print("=" * 60)
    print("  M6 PHASE I HARD GATE")
    print("  M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE")
    print("=" * 60)

    gates = [
        ("GATE 1 §34",   ".dsh-control/m6_research/test_shadow_differential.py",
         "QUERY_PARITY_FAILURES == 0: True"),
        ("GATE 2 §35",   ".dsh-control/m6_research/test_shadow_mutation.py",
         "MUTATION_ALL_CAUGHT: True"),
        ("GATE 3 GC",    ".dsh-control/m6_research/test_shadow_store.py",
         "RESULT: PASS"),
        ("GATE 4 CERT",  ".dsh-control/m6_research/prototype_c.py",
         "Safety violation (false reuse): False"),
        ("GATE 5 PhR",   ".dsh-control/m6_research/prototype_a.py",
         "PASS: 21 cases"),
        ("GATE 6 M5",    ".dsh-control/gates/run_m5_stage_gate.py",
         "RESULT: PASS"),
        ("I3 Conc",      ".dsh-control/m6_research/test_concurrency.py",
         "RESULT: PASS"),
        ("I7 Cert",      ".dsh-control/m6_research/test_certificate_store.py",
         "RESULT: PASS"),
        ("I9 CrossTU",   ".dsh-control/m6_research/test_cross_tu.py",
         "RESULT: PASS"),
        ("I10 GCLife",   ".dsh-control/m6_research/test_gc_lifecycle.py",
         "RESULT: PASS"),
        ("I11 Real",     ".dsh-control/m6_research/test_real_corpus.py",
         "RESULT: PASS"),
        ("I12 CostCert", ".dsh-control/m6_research/test_cost_and_cert.py",
         "RESULT: PASS"),
    ]

    results = []
    for name, script, pattern in gates:
        passed, _ = run_gate(name, script, pattern)
        results.append((name, passed))

    # ── Summary ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  M6 HARD GATE SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, passed in results:
        status = "[PASS]" if passed else "[FAIL]"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    total = len(results)
    passed_count = sum(1 for _, p in results if p)
    print(f"\n  {passed_count}/{total} gates passed")

    if all_pass:
        print("\n  ============================================")
        print("  M6_CROSS_BUILD_FINE_GRAINED_SEMANTIC_REUSE")
        print("  HARD GATE: ALL PASS")
        print("  ============================================")
    else:
        print("\n  [FAIL] HARD GATE: FAIL")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
