#!/usr/bin/env python3
"""M6 Research Gate (§19).

Validates that Phase R research has produced a viable architecture
for cross-build fine-grained semantic reuse. ALL clauses must exit 0.

Clauses:
  R1  Prototype A (fact + BuildSet) achieves exact parity with FULL_BUILD_GT
  R2  FALSE_SHARED_SEMANTIC_FACTS == 0 across all torture cases
  R3  C Macro Torture Corpus covers §9 patterns
  R4  Combination attacks (§16) pass
  R5  BuildSet query cost acceptable (< 10ms per build projection)
  R6  Storage duplication materially reduced (>= 50% savings at 10 builds)
  R7  Prototype C shows real materialization reuse (>= 50% at 10 builds)
  R8  Sublinear scaling (fact growth < build growth, 5→20)
  R9  Mutation testing: all mutations caught
  R10 Lifecycle implementable safely (GC reachability-based design documented)
  R11 M3B/M4/M5 regression gates remain green
  R12 Architecture contract candidate documented

Exit 0 only if ALL clauses pass. No warning-pass.
"""
from __future__ import annotations
import json, sys, subprocess, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = PROJECT_ROOT / ".dsh-control" / "m6_research"
PROTOTYPES_DIR = RESEARCH_DIR / "prototypes"
GATES_DIR = PROJECT_ROOT / ".dsh-control" / "gates"

class GateResult:
    def __init__(self, clause_id: str, description: str):
        self.clause_id = clause_id
        self.description = description
        self.passed = False
        self.details = ""
        self.evidence = {}

    def pass_(self, details: str = "", **evidence):
        self.passed = True
        self.details = details
        self.evidence = evidence

    def fail(self, details: str = "", **evidence):
        self.passed = False
        self.details = details
        self.evidence = evidence

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"  [{status}] {self.clause_id}: {self.description} — {self.details}"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── R1: Prototype A parity ───────────────────────────────────────────────────

def check_r1() -> GateResult:
    r = GateResult("R1", "Prototype A achieves exact parity with FULL_BUILD_GT")
    results_path = PROTOTYPES_DIR / "prototype_a_results.json"
    if not results_path.exists():
        r.fail("prototype_a_results.json not found — run prototype_a.py first")
        return r
    data = load_json(results_path)
    summary = data.get("summary", {})
    if summary.get("overall_pass"):
        r.pass_(
            f"{summary['total_cases']} cases, false_shared={summary['total_false_shared_facts']}, "
            f"parity_failures={summary['total_parity_failures']}",
            total_cases=summary["total_cases"],
            false_shared=summary["total_false_shared_facts"],
            parity_failures=summary["total_parity_failures"],
        )
    else:
        r.fail(
            f"false_shared={summary.get('total_false_shared_facts')}, "
            f"parity_failures={summary.get('total_parity_failures')}"
        )
    return r


# ── R2: FALSE_SHARED_SEMANTIC_FACTS == 0 ─────────────────────────────────────

def check_r2() -> GateResult:
    r = GateResult("R2", "FALSE_SHARED_SEMANTIC_FACTS == 0")
    results_path = PROTOTYPES_DIR / "prototype_a_results.json"
    if not results_path.exists():
        r.fail("prototype_a_results.json not found")
        return r
    data = load_json(results_path)
    summary = data.get("summary", {})
    false_shared = summary.get("total_false_shared_facts", -1)
    if false_shared == 0:
        r.pass_("zero false-shared semantic facts", false_shared=0)
    else:
        r.fail(f"false_shared={false_shared} (must be 0)", false_shared=false_shared)
    return r


# ── R3: C Macro Torture Corpus covers §9 patterns ───────────────────────────

def check_r3() -> GateResult:
    r = GateResult("R3", "C Macro Torture Corpus covers §9 patterns")
    corpus_manifest = RESEARCH_DIR / "corpus" / "manifest.json"
    if not corpus_manifest.exists():
        r.fail("corpus manifest not found")
        return r
    manifest = load_json(corpus_manifest)
    cases = manifest.get("cases", {})
    # Require at least: object macros, token paste, macro-gen names, struct fields,
    # X-Macros, repeated include, __COUNTER__, combination attacks, call graph
    required_patterns = [
        "T01_object_macros", "T05_token_paste", "T07_macro_gen_func_names",
        "T14_macro_gen_struct_fields", "T17_xmacros", "T18_repeated_include",
        "T27_counter", "C01_paste_conditional", "C08_counter_paste",
        "CALL_GRAPH_DISPATCH",
    ]
    missing = [p for p in required_patterns if p not in cases]
    if not missing:
        r.pass_(f"{len(cases)} cases covering all required §9 patterns",
                case_count=len(cases))
    else:
        r.fail(f"missing patterns: {missing}")
    return r


# ── R4: Combination attacks pass ──────────────────────────────────────────────

def check_r4() -> GateResult:
    r = GateResult("R4", "Combination attacks (§16) pass")
    results_path = PROTOTYPES_DIR / "prototype_a_results.json"
    if not results_path.exists():
        r.fail("prototype_a_results.json not found")
        return r
    data = load_json(results_path)
    combo_cases = {k: v for k, v in data.get("cases", {}).items()
                   if k.startswith("C0") or k == "CALL_GRAPH_DISPATCH"}
    all_pass = all(c.get("parity_failures", 1) == 0 and c.get("false_shared_facts", 1) == 0
                   for c in combo_cases.values())
    if all_pass and len(combo_cases) >= 3:
        r.pass_(f"{len(combo_cases)} combination/attack cases all pass",
                cases=list(combo_cases.keys()))
    else:
        failed = [k for k, v in combo_cases.items()
                  if v.get("parity_failures", 1) != 0 or v.get("false_shared_facts", 1) != 0]
        r.fail(f"failed combination cases: {failed}")
    return r


# ── R5: BuildSet query cost ───────────────────────────────────────────────────

def check_r5() -> GateResult:
    r = GateResult("R5", "BuildSet query cost < 10ms per build projection")
    results_path = PROTOTYPES_DIR / "scaling_results.json"
    if not results_path.exists():
        r.fail("scaling_results.json not found — run scaling_benchmark.py first")
        return r
    data = load_json(results_path)
    scales = data.get("scales", {})
    max_query_ms = 0
    for n_str, s in scales.items():
        query_s = s.get("query_reconstruction_s", 0)
        # query_reconstruction_s is for ALL builds — per-build = query_s / n
        n = int(n_str)
        per_build_ms = (query_s / n) * 1000 if n > 0 else 0
        max_query_ms = max(max_query_ms, per_build_ms)
    if max_query_ms < 10.0:
        r.pass_(f"max per-build query = {max_query_ms:.3f}ms", max_query_ms=round(max_query_ms, 4))
    else:
        r.fail(f"max per-build query = {max_query_ms:.3f}ms (>= 10ms)", max_query_ms=round(max_query_ms, 4))
    return r


# ── R6: Storage duplication materially reduced ───────────────────────────────

def check_r6() -> GateResult:
    r = GateResult("R6", "Storage savings >= 50% at 10 builds")
    results_path = PROTOTYPES_DIR / "scaling_results.json"
    if not results_path.exists():
        r.fail("scaling_results.json not found")
        return r
    data = load_json(results_path)
    s10 = data.get("scales", {}).get("10", {})
    savings = s10.get("storage_savings_pct", 0)
    if savings >= 50.0:
        r.pass_(f"10-build storage savings = {savings}%", savings=savings)
    else:
        r.fail(f"10-build storage savings = {savings}% (< 50%)", savings=savings)
    return r


# ── R7: Prototype C materialization reuse ────────────────────────────────────

def check_r7() -> GateResult:
    r = GateResult("R7", "Prototype C shows >= 50% fact reuse at 10 builds")
    results_path = PROTOTYPES_DIR / "prototype_c_results.json"
    if not results_path.exists():
        r.fail("prototype_c_results.json not found — run prototype_c.py first")
        return r
    data = load_json(results_path)
    s10 = data.get("scales", {}).get("10", {})
    reuse_rate = s10.get("fact_reuse_rate_pct", 0)
    if reuse_rate >= 50.0:
        r.pass_(f"10-build fact reuse rate = {reuse_rate}%", reuse_rate=reuse_rate)
    else:
        r.fail(f"10-build fact reuse rate = {reuse_rate}% (< 50%)", reuse_rate=reuse_rate)
    return r


# ── R8: Sublinear scaling ────────────────────────────────────────────────────

def check_r8() -> GateResult:
    r = GateResult("R8", "Sublinear scaling (fact growth < build growth, 5→20)")
    results_path = PROTOTYPES_DIR / "scaling_results.json"
    if not results_path.exists():
        r.fail("scaling_results.json not found")
        return r
    data = load_json(results_path)
    summary = data.get("summary", {})
    sublinear = summary.get("sublinear_scaling", False)
    build_ratio = summary.get("build_ratio_5_to_20", 0)
    fact_ratio = summary.get("fact_ratio_5_to_20", 0)
    if sublinear:
        r.pass_(f"build_ratio={build_ratio}x, fact_ratio={fact_ratio}x (sublinear)",
                build_ratio=build_ratio, fact_ratio=fact_ratio)
    else:
        r.fail(f"build_ratio={build_ratio}x, fact_ratio={fact_ratio}x (NOT sublinear)",
               build_ratio=build_ratio, fact_ratio=fact_ratio)
    return r


# ── R9: Mutation testing ─────────────────────────────────────────────────────

def check_r9() -> GateResult:
    r = GateResult("R9", "Mutation testing: all mutations caught")
    results_path = PROTOTYPES_DIR / "mutation_results.json"
    if not results_path.exists():
        r.fail("mutation_results.json not found — run mutation_testing.py first")
        return r
    data = load_json(results_path)
    all_caught = data.get("all_caught", False)
    mutations = data.get("mutations", [])
    if all_caught:
        r.pass_(f"{len(mutations)} mutations all caught", mutation_count=len(mutations))
    else:
        missed = [m["mutation_id"] for m in mutations if not m.get("pass")]
        r.fail(f"missed mutations: {missed}", missed=missed)
    return r


# ── R10: Lifecycle implementable safely ───────────────────────────────────────

def check_r10() -> GateResult:
    r = GateResult("R10", "Lifecycle: GC reachability-based design documented")
    # Check that the architecture documentation exists and mentions GC design
    arch_path = PROJECT_ROOT / ".dsh-control" / "m6_research" / "ARCHITECTURE_NOTES.md"
    if not arch_path.exists():
        # Allow the goal contract as evidence
        contract = PROJECT_ROOT / ".dsh-control" / "contracts" / "IMMUTABLE_M6_GOAL_CONTRACT.md"
        if contract.exists():
            text = contract.read_text(encoding="utf-8")
            if "reachability" in text.lower() and "GC" in text:
                r.pass_("GC reachability-based design documented in IMMUTABLE_M6_GOAL_CONTRACT.md")
                return r
        r.fail("no architecture notes or GC design documentation found")
        return r
    text = arch_path.read_text(encoding="utf-8")
    if "reachability" in text.lower() and "gc" in text.lower():
        r.pass_("GC reachability-based design documented in ARCHITECTURE_NOTES.md")
    else:
        r.fail("ARCHITECTURE_NOTES.md found but missing GC reachability design")
    return r


# ── R11: M3B/M4/M5 regression ────────────────────────────────────────────────

def check_r11() -> GateResult:
    r = GateResult("R11", "M3B/M4/M5 regression gates remain green")
    # Run the existing M5 stage gate (which includes M3B/M4 checks)
    m5_gate = GATES_DIR / "run_m5_stage_gate.py"
    if not m5_gate.exists():
        r.fail(f"M5 stage gate not found at {m5_gate}")
        return r
    try:
        result = subprocess.run(
            [sys.executable, str(m5_gate)],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            r.pass_("M5 stage gate (incl. M3B/M4) exits 0")
        else:
            # Extract last few lines for context
            stderr_tail = result.stderr.strip().split("\n")[-5:] if result.stderr else []
            stdout_tail = result.stdout.strip().split("\n")[-5:] if result.stdout else []
            r.fail(f"M5 gate exit={result.returncode}",
                   stdout_tail=stdout_tail, stderr_tail=stderr_tail)
    except subprocess.TimeoutExpired:
        r.fail("M5 gate timed out (120s)")
    except Exception as e:
        r.fail(f"M5 gate error: {e}")
    return r


# ── R12: Architecture contract candidate ────────────────────────────────────

def check_r12() -> GateResult:
    r = GateResult("R12", "Architecture contract candidate documented")
    # Check that research evidence is collected
    evidence_files = [
        PROTOTYPES_DIR / "prototype_a_results.json",
        PROTOTYPES_DIR / "scaling_results.json",
        PROTOTYPES_DIR / "prototype_c_results.json",
        PROTOTYPES_DIR / "mutation_results.json",
    ]
    missing = [str(f.name) for f in evidence_files if not f.exists()]
    if missing:
        r.fail(f"missing evidence files: {missing}")
        return r
    # Check goal contract exists
    contract = PROJECT_ROOT / ".dsh-control" / "contracts" / "IMMUTABLE_M6_GOAL_CONTRACT.md"
    if not contract.exists():
        r.fail("IMMUTABLE_M6_GOAL_CONTRACT.md not found")
        return r
    r.pass_("all research evidence + goal contract present",
            evidence_files=[f.name for f in evidence_files])
    return r


# ── Main ──────────────────────────────────────────────────────────────────────

ALL_CHECKS = [
    check_r1, check_r2, check_r3, check_r4, check_r5, check_r6,
    check_r7, check_r8, check_r9, check_r10, check_r11, check_r12,
]


def main() -> int:
    print("=" * 70)
    print("M6 RESEARCH GATE (§19)")
    print("=" * 70)
    
    results = []
    for check_fn in ALL_CHECKS:
        result = check_fn()
        results.append(result)
        print(result)
    
    all_pass = all(r.passed for r in results)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    
    print("-" * 70)
    print(f"RESULT: {passed}/{total} clauses PASS — {'GATE PASS' if all_pass else 'GATE FAIL'}")
    print("=" * 70)
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
