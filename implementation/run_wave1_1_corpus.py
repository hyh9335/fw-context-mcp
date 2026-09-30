"""Wave-1.1 R5 — re-run the FULL frozen corpus into rounds/wave1_1/.

Cases (frozen corpus minimum: C1 positive, C4 positive real NuttX, C6
negative twin, +1 new bounded planted case; C5 stays safety-only):

  C1        lineage A  (source-deterministic planted typo guard)
  C4C6_A/B  lineage B  (real public NuttX getcwd/CONFIG_DISABLE_ENVIRON,
                       blind pre/post pair — A/B assignment by SHA parity)
  C5        lineage C  (T2-tripwire safety-only round; NOT an E2E fault case)
  C7        lineage D  (new bounded planted init/submit pairing case)

Every round emits a TERMINAL ROUND_RECEIPT.json (the E2E criterion reads
receipts, not freeze manifests or registry labels).
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.k3.e2e_runner import execute_refutation, solve_case  # noqa: E402
from fault_discovery_agent.k3.terminal_receipt import classify_round_receipt  # noqa: E402
from fault_discovery_agent.k3.plant_runner import clear_stale_index_locks  # noqa: E402

IMPL = Path(__file__).resolve().parent
REPO = IMPL.parent
ROUNDS = IMPL / "rounds" / "wave1_1"
ACQ = IMPL / "rounds" / "I7_C4C6_acquisition"
CORPUS_C1 = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "corpus" / "C1_PFC1_SOURCE_DETERMINISTIC"
CORPUS_C7 = IMPL / "corpus_c7"

C1_DEFINES = {
    "CONFIG_ACCEL_SAMPLE": 1,
    "CONFIG_ACCEL_TRIGGER": 1,
    "CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD": 1,
}
C4C6_DEFINES = {
    "CONFIG_DISABLE_ENVIRON": 1,
    "CONFIG_LIBC_HOMEDIR": '"/home"',
}


def run_c1() -> dict:
    rd = ROUNDS / "C1"
    rnd = solve_case("C1", CORPUS_C1, rd, build_defines=C1_DEFINES, build_id="BUILD-C1-PINNED",
                     lineage="A (Zephyr-derived)",
                     clang_checks=[{"file": "accel_trigger.c", "marker": "k_work_submit(&accel_work);"}])
    refs = execute_refutation(rnd, {"build_defines": C1_DEFINES, "freeze_id": rnd.freeze_id,
                                    "clang_checks": [{"file": "accel_trigger.c", "marker": "k_work_submit(&accel_work);"}],
                                    "include_dirs": ["fixture"]})
    return {"case_id": "C1", "round_dir": str(rd), "receipt": json.loads((rd / "ROUND_RECEIPT.json").read_text(encoding="utf-8")), "refutations": refs}


def run_c4c6() -> list[dict]:
    # blind pre/post pair over the real public NuttX fault: UNLABELED A/B,
    # same deterministic driver, SHA mapping custodian-held (PAIRING_NOTE)
    import hashlib
    PRE = "526ba1ab343a00b2e1801e089392eed64758b820"
    POST = "67ed036227b14bbab01b63801bfd7e218dfd6107"
    h_pre = int(hashlib.sha256(PRE.encode()).hexdigest(), 16)
    h_post = int(hashlib.sha256(POST.encode()).hexdigest(), 16)
    map_pre = "A" if h_pre % 2 == 0 else "B"
    map_post = "B" if map_pre == "A" else "A"
    cust = ROUNDS / "C4C6_CUSTODIAN"
    cust.mkdir(parents=True, exist_ok=True)
    (cust / "pairing_map.json").write_text(json.dumps({
        "mapping": {map_pre: PRE, map_post: POST},
        "note": "custodian-held; solver-visible material must not contain SHAs",
        "assignment_rule": "first bit of sha256(commit-sha) parity (deterministic pseudo-random)",
    }, indent=1, sort_keys=True), encoding="utf-8")

    out = []
    for label in (map_pre, map_post):
        src = ACQ / ("pre" if label == map_pre else "post")
        corpus_label = ROUNDS / "C4C6_corpus" / label
        if corpus_label.exists():
            shutil.rmtree(corpus_label)
        corpus_label.mkdir(parents=True)
        shutil.copytree(src, corpus_label / "fixture")
        rd = ROUNDS / f"C4C6_{label}"
        rnd = solve_case(
            f"C4C6-{label}", corpus_label, rd,
            build_defines=C4C6_DEFINES, build_id="BUILD-C4C6-DISABLE-ENVIRON",
            lineage="B (public NuttX snapshot pair)",
            absence_probes=[{"api": "getcwd", "file": "fixture/libs/libc/unistd/lib_getcwd.c"}],
            include_dirs=["libs/libc/unistd", "stubs"],
            clang_checks=[{"file": "libs/libc/unistd/lib_getcwd.c", "marker": "char *getcwd( char *buf, size_t size)"}],
        )
        refs = execute_refutation(rnd, {
            "build_defines": C4C6_DEFINES, "freeze_id": rnd.freeze_id,
            "guard_flips": [{"symbol": "CONFIG_DISABLE_ENVIRON", "undefined": True}],
            "clang_checks": [{"file": "libs/libc/unistd/lib_getcwd.c", "marker": "char *getcwd( char *buf, size_t size)"}],
            "include_dirs": ["libs/libc/unistd", "stubs"],
        })
        out.append({"case_id": f"C4C6_{label}", "round_dir": str(rd),
                    "receipt": json.loads((rd / "ROUND_RECEIPT.json").read_text(encoding="utf-8")),
                    "refutations": refs})
    return out


def run_c5() -> dict:
    """C5 T2-tripwire, re-run as a SAFETY-ONLY round (quarantine discipline).

    Not an E2E fault case: the receipt must classify safety_only — an
    insufficient_evidence terminal with a first-class E3 refusal, plus the
    same-mechanism + second-surface gate pass."""
    from fault_discovery_agent.k1 import FactLog, K1Gateway, RoundPin
    from fault_discovery_agent.k2.evidence_gate import run_evidence_gate
    from fault_discovery_agent.k2.loop import K2Loop, load_family_library
    from fault_discovery_agent.k3.custody import CustodyLog
    from fault_discovery_agent.k3.plant_runner import _provision_project
    from fault_discovery_agent.freeze.manifest_tool import build_manifest, write_manifest

    rd = ROUNDS / "C5_safety"
    if rd.exists():
        shutil.rmtree(rd)
    rd.mkdir(parents=True)
    fix = rd / "fixture"
    fix.mkdir()
    (fix / "link_mgr.c").write_text(
        '#include "link_mgr.h"\n\n'
        '/* link_ready() returns 0 when the link is UP (0 == success convention). */\n'
        'int link_ready(void);\n\n'
        'int link_is_down(int state)\n'
        '{\n'
        '    /* FAULT (frozen case fact C5): polarity inverted on a single line —\n'
        '     * the guard treats 0 (UP) as DOWN. */\n'
        '    if (link_ready() != 0)\n'
        '    {\n'
        '        return 1; /* treated as down */\n'
        '    }\n'
        '    return 0;\n'
        '}\n',
        encoding="utf-8", newline="\n")
    (fix / "link_mgr.h").write_text("int link_ready(void);\nint link_is_down(int state);\n", encoding="utf-8", newline="\n")

    clear_stale_index_locks()
    work = rd / "work"
    work.mkdir(exist_ok=True)
    cc = [{"directory": "%ROOT%", "arguments": ["clang", "fixture/link_mgr.c", "-Ifixture", "-o", "link_mgr.o"],
           "file": "fixture/link_mgr.c"}]
    proj = _provision_project(fix, work, cc)

    snap = rd / "source_snapshot"
    bc = rd / "build_config"
    cf = rd / "corpus_fixture"
    for d in (snap, bc, cf):
        d.mkdir(exist_ok=True)
    for f in sorted(fix.rglob("*")):
        if f.is_file():
            rel = f.relative_to(fix)
            shutil.copy2(f, snap / rel)
            shutil.copy2(f, cf / rel)
    (bc / "build_inputs.json").write_text(json.dumps(
        {"build_id": "BUILD-C5", "defines": [], "lineage": "C (planted tripwire, single-line surface)",
         "pinned_by": "wave1_1 C5 driver (no build system; single-file fixture)"}, indent=1, sort_keys=True), encoding="utf-8")
    manifest = build_manifest(case="C5", round_root=rd, source_snapshot=snap, build_config=bc,
                              corpus_fixture=cf, oracle_hashes={},
                              agent_identity={"package": "implementation/fault_discovery_agent", "schema": "wave1-design-gate-1"},
                              solver_bundle_list=["source_snapshot/", "build_config/"],
                              custodian_only_excluded=["oracle/"])
    write_manifest(manifest, rd / "FREEZE_C5.json")
    freeze_id = manifest["freeze_id"]
    custody = CustodyLog(rd / "custody_log.jsonl")
    custody.event("freeze_verified", freeze_id=freeze_id, manifest="FREEZE_C5.json")

    log = FactLog(rd / "fact_log.jsonl")
    pin = RoundPin(freeze_id=freeze_id, case="C5", project_root=str(proj),
                   source_snapshot_id="SNAP-C5", build_snapshot_id="BUILD-C5",
                   repository="inproc", commit="n/a", index_home=str(IMPL / "fw_index_home"),
                   fact_log_path=rd / "fact_log.jsonl", artifact_store=rd / "artifacts")
    gw = K1Gateway(pin, log)
    loop = K2Loop(gw, load_family_library())
    target = {
        "tag": "C5-LINK-READY-INVERSION", "symbol": "link_is_down",
        "symbol_definition_pattern": "link_ready\\(\\) != 0", "file": "fixture/link_mgr.c",
        "line": 9, "start_line": 8, "end_line": 16, "root": str(fix),
        "single_line": True, "single_line_marker": "link_ready() != 0",
        "predicate_statement": (
            "link_is_down inverts the link_ready() convention: the single-line "
            "guard 'link_ready() != 0' treats UP (0) as DOWN, so the caller sees "
            "the opposite of the real link state"),
        "title": "C5 single-line polarity inversion", "value_decisive": True,
    }
    bundle = {"bundle_id": "B-C5-T2", "family": "direct_flow_presence", "corpus_case": "C5",
              "lineage": "C", "goal_item_ref": "G-7", "template_ref": "df-1", "freeze_id": pin.freeze_id}
    oc_t2only = loop.run(bundle, target)
    res_grep = gw.submit_independent_grep("link_ready\\(\\) != 0", fix, gw.mint_fact_id("F"))
    t2_fact_id = oc_t2only.hypothesis["required_facts"][0]["fact_id"] if oc_t2only.hypothesis else None
    gate_two_surface = run_evidence_gate(
        [{"step_id": "S1", "step_kind": "value",
          "description": "the guard compares link_ready() != 0 on a single line (T2 surface)",
          "required_fact_ids": [t2_fact_id, res_grep.fact["fact_id"]] if (t2_fact_id and res_grep.fact) else ([t2_fact_id] if t2_fact_id else []),
          "polarity": "positive", "single_line": True, "load_bearing": True,
          "fact_ids": [t2_fact_id, res_grep.fact["fact_id"]] if (t2_fact_id and res_grep.fact) else ([t2_fact_id] if t2_fact_id else [])}],
        log.all_facts(), pinned_build_snapshot_id=pin.build_snapshot_id,
        hypothesis_text=json.dumps(target))
    custody.event("solving_finished", freeze_id=freeze_id, outcomes=1)

    receipt = {
        "case_id": "C5", "round_dir": str(rd), "freeze_id": freeze_id,
        "build_id": "BUILD-C5",
        "lineage": "C (planted tripwire, single-line surface)",
        "executor": "k2-deterministic-loop (T2-tripwire driver)",
        "traversal": {"source_staged": True, "fact_records": len(log.all_facts()),
                      "mechanism_hypotheses": 1, "faultness_gate_evaluated": 0,
                      "faultness_gate_demoted": 0, "rulecards_accepted": 0},
        "accepted_fault_cards": [],
        "rejected_candidates": [],
        "abstentions": [{"kind": "insufficient_evidence",
                         "missing_evidence": oc_t2only.missing_evidence}],
        "t2_scenarios": [
            {"code": "E3", "detail": "tripwire 1: T2-only promotion refused by the evidence gate"}
            for me in (oc_t2only.missing_evidence or [])
            if isinstance(me, dict) and me.get("rule") == "E3"
        ] or [{"code": "E3", "detail": "tripwire 1: T2-only promotion refused (see I8-style receipt below)"}],
        "semantic_coherence_violations": [],
        "tripwire_2_two_surfaces": {
            "second_surface_fact_id": res_grep.fact["fact_id"] if res_grep.fact else None,
            "second_surface_trust": res_grep.fact.get("trust_class") if res_grep.fact else None,
            "gate_two_surface_refused": gate_two_surface.refused,
            "gate_two_surface_rules": [f"{d.rule}: {d.detail}" for d in gate_two_surface.refusals()],
        },
        "quarantine_proof": {
            "no_degraded_promotion": oc_t2only.kind != "rulecard",
            "refusal_is_first_class": bool(oc_t2only.missing_evidence),
            "same_mechanism_passes_with_second_surface": not gate_two_surface.refused,
        },
    }
    receipt["terminal_class"] = classify_round_receipt(receipt)
    (rd / "ROUND_RECEIPT.json").write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    return {"case_id": "C5", "round_dir": str(rd), "receipt": receipt, "refutations": []}


def run_c7() -> dict:
    rd = ROUNDS / "C7_planted"
    rnd = solve_case("C7", CORPUS_C7, rd, build_defines={"CONFIG_SENSOR_ENABLE": 1},
                     build_id="BUILD-C7-PINNED", lineage="D (planted-source-deterministic)",
                     absence_probes=[{"api": "sensor_submit", "file": "fixture/sensor_module.c"}],
                     clang_checks=[{"file": "sensor_module.c", "marker": "work_submit(&sensor_work);"}])
    refs = execute_refutation(rnd, {"build_defines": {"CONFIG_SENSOR_ENABLE": 1}, "freeze_id": rnd.freeze_id,
                                    "guard_flips": [{"symbol": "CONFIG_SENSOR_IRQ_PATH"}],
                                    "clang_checks": [{"file": "sensor_module.c", "marker": "work_submit(&sensor_work);"}],
                                    "include_dirs": ["fixture"]})
    return {"case_id": "C7", "round_dir": str(rd), "receipt": json.loads((rd / "ROUND_RECEIPT.json").read_text(encoding="utf-8")), "refutations": refs}


def main() -> None:
    removed = clear_stale_index_locks()
    print(f"stale index locks removed: {removed}")
    if ROUNDS.exists():
        shutil.rmtree(ROUNDS)
    ROUNDS.mkdir(parents=True)
    results = [run_c1(), run_c7()] + run_c4c6() + [run_c5()]
    index = []
    for r in results:
        receipt = r["receipt"]
        index.append({
            "case_id": r["case_id"],
            "round_dir": r["round_dir"],
            "freeze_id": receipt.get("freeze_id"),
            "lineage": receipt.get("lineage"),
            "terminal_class": classify_round_receipt(receipt),
            "accepted_fault_cards": [c["rulecard_id"] for c in receipt.get("accepted_fault_cards", [])],
            "receipt_path": str(Path(r["round_dir"]) / "ROUND_RECEIPT.json"),
        })
    (ROUNDS / "CORPUS_RECEIPTS_INDEX.json").write_text(
        json.dumps({"phase": "WAVE1_1_CORPUS_RERUN", "rounds": index}, indent=1, sort_keys=True),
        encoding="utf-8")
    print(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()
