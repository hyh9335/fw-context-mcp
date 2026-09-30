"""I6_C1_E2E — fresh-context solving of corpus case C1 (deterministic)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.k3.e2e_runner import execute_refutation, solve_case  # noqa: E402
from fault_discovery_agent.k3.scorer import score_rulecard  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "corpus" / "C1_PFC1_SOURCE_DETERMINISTIC"
ROUND = Path(__file__).resolve().parent / "rounds" / "I6_C1"

# pinned build identity per CASE_DESIGN §1 + CMakeLists target_compile_definitions
BUILD_DEFINES = {
    "CONFIG_ACCEL_SAMPLE": 1,
    "CONFIG_ACCEL_TRIGGER": 1,
    "CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD": 1,
}

rnd = solve_case("C1", CORPUS, ROUND, build_defines=BUILD_DEFINES, build_id="BUILD-C1-PINNED",
                 clang_checks=[{"file": "accel_trigger.c", "marker": "k_work_submit(&accel_work);"}])
refutations = execute_refutation(rnd, {
    "build_defines": BUILD_DEFINES,
    "freeze_id": rnd.freeze_id,
    "clang_checks": [{"file": "accel_trigger.c", "marker": "k_work_submit(&accel_work);"}],
    "include_dirs": ["fixture"],
})

# blind scoring (>=30% sample) — evidence-internal, no oracle
scores = []
for oc in rnd.outcomes:
    if oc.kind == "rulecard" and oc.rulecard is not None:
        comp = score_rulecard(oc.rulecard, fact_log_rows=rnd.gateway.log.all_facts(), sample_rate=0.30)
        scores.append({"rulecard_id": oc.rulecard["rulecard_id"], "verdict": comp.s5_verdict,
                       "s1": comp.s1_completeness, "s2": comp.s2_evidence, "s3": comp.s3_sample_agreement,
                       "s4": comp.s4_refutation_consistency, "sampled": len(comp.sampled),
                       "overclaims": comp.overclaims})

receipt = {
    "case": "C1",
    "freeze_id": rnd.freeze_id,
    "round_dir": str(ROUND),
    "targets": len(rnd.targets),
    "outcomes": [
        {"kind": o.kind,
         "hypothesis_id": (o.hypothesis or {}).get("hypothesis_id") if o.hypothesis else None,
         "rulecard_id": (o.rulecard or {}).get("rulecard_id") if o.rulecard else None,
         "missing_evidence": o.missing_evidence,
         "gate": o.gate_decisions}
        for o in rnd.outcomes
    ],
    "refutations": refutations,
    "scores": scores,
    "two_part_separation": {
        "source_level": "mechanism provable from T0/T1 surfaces (recorded in promoted RuleCards)",
        "runtime_occurrence": "required_but_unavailable — never asserted (CASE_DESIGN §2)",
    },
    "custody_log": str(rnd.round_dir / "custody_log.jsonl"),
    "fact_log": str(rnd.round_dir / "fact_log.jsonl"),
}
(ROUND / "E2E_RECEIPT.json").write_text(json.dumps(receipt, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")

# persist promoted rulecards
rc_dir = ROUND / "rulecards"
rc_dir.mkdir(exist_ok=True)
for oc in rnd.outcomes:
    if oc.kind == "rulecard" and oc.rulecard is not None:
        (rc_dir / f"{oc.rulecard['rulecard_id']}.json").write_text(
            json.dumps({"rulecard": oc.rulecard, "hypothesis": oc.hypothesis}, indent=1, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )

print(f"freeze_id: {rnd.freeze_id}")
print(f"targets: {len(rnd.targets)}, outcomes: {len(rnd.outcomes)}, rulecards: {len(scores)}")
for o in rnd.outcomes:
    hid = (o.hypothesis or {}).get("hypothesis_id") if o.hypothesis else None
    print(f"  {o.kind:22s} {hid or '-':24s} {(o.rulecard or {}).get('rulecard_id', '-')}")
for s in scores:
    print(f"  score {s['rulecard_id']}: {s['verdict']} s1={s['s1']} s2={s['s2']} s3={s['s3']} s4={s['s4']} sampled={s['sampled']} overclaims={s['overclaims']}")
for r in refutations:
    print(f"  refutation {r['rulecard_id']}: state={r['state']} steps={[ (s['step_id'], s['holds']) for s in r['steps'] ]}")
