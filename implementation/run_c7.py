"""Wave-1.1 R4 — C7 planted case: fresh-context solving (deterministic).

Corpus: implementation/corpus_c7 (planted, lineage D). The solver sees only
the fixture tree + CASE_DESIGN.md; FAULT_ORACLE.json stays custodian-side.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.k3.e2e_runner import execute_refutation, solve_case  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "implementation" / "corpus_c7"
ROUND = Path(__file__).resolve().parent / "rounds" / "wave1_1" / "C7_planted"

# pinned build identity per CASE_DESIGN + CMakeLists target_compile_definitions
BUILD_DEFINES = {"CONFIG_SENSOR_ENABLE": 1}
LINEAGE = "D (planted-source-deterministic)"


def main() -> None:
    rnd = solve_case(
        "C7", CORPUS, ROUND,
        build_defines=BUILD_DEFINES, build_id="BUILD-C7-PINNED",
        lineage=LINEAGE,
        absence_probes=[{"api": "sensor_submit", "file": "fixture/sensor_module.c"}],
        clang_checks=[{"file": "sensor_module.c", "marker": "work_submit(&sensor_work);"}],
    )
    refutations = execute_refutation(rnd, {
        "build_defines": BUILD_DEFINES,
        "freeze_id": rnd.freeze_id,
        "guard_flips": [{"symbol": "CONFIG_SENSOR_IRQ_PATH"}],
        "clang_checks": [{"file": "sensor_module.c", "marker": "work_submit(&sensor_work);"}],
        "include_dirs": ["fixture"],
    })

    receipt_path = rnd.round_dir / "ROUND_RECEIPT.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    print(json.dumps({
        "case": "C7",
        "freeze_id": rnd.freeze_id,
        "terminal_class": receipt.get("terminal_class"),
        "accepted_fault_cards": [c["rulecard_id"] for c in receipt["accepted_fault_cards"]],
        "rejected_candidates": [r["rulecard_id"] for r in receipt["rejected_candidates"]],
        "refutations": refutations,
    }, indent=1))


if __name__ == "__main__":
    main()
