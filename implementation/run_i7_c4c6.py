"""I7_C4_C6 — blind pre/post-fix pair over the real public NuttX fault.

PAIRING NOTE compliance (custodian-held): the two snapshots are presented as
UNLABELED "A"/"B" snapshots; the SAME deterministic driver adjudicates each
independently; the A/B <-> SHA mapping lives ONLY in the custodian area
(rounds/I7_C4C6_CUSTODIAN/pairing_map.json), never in solver-visible material.

Pinned build context (PAIRING_NOTE §1): CONFIG_DISABLE_ENVIRON=y on lineage B.
Fixture provenance: structure-faithful derived fixture — see
rounds/I7_C4C6_acquisition/ACQUISITION_RECEIPT.json (recorded Wave-1
limitation: web_fetch view pruning blocked byte-exact staging; CLI egress
blocked; blob-hash verification attempted-blocked).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.k3.e2e_runner import execute_refutation, solve_case  # noqa: E402
from fault_discovery_agent.k3.plant_runner import clear_stale_index_locks  # noqa: E402
from fault_discovery_agent.k3.scorer import score_rulecard  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ACQ = Path(__file__).resolve().parent / "rounds" / "I7_C4C6_acquisition"
ROUNDS = Path(__file__).resolve().parent / "rounds"

removed = clear_stale_index_locks()
print(f"stale index locks removed: {removed}")
for stale in ("I7_C4C6_A", "I7_C4C6_B"):
    d = ROUNDS / stale
    if d.exists():
        import shutil
        shutil.rmtree(d)

# custodian: deterministic pseudo-random A/B assignment from the SHAs
import hashlib

PRE = "526ba1ab343a00b2e1801e089392eed64758b820"
POST = "67ed036227b14bbab01b63801bfd7e218dfd6107"
h_pre = int(hashlib.sha256(PRE.encode()).hexdigest(), 16)
h_post = int(hashlib.sha256(POST.encode()).hexdigest(), 16)
map_pre = "A" if h_pre % 2 == 0 else "B"
map_post = "B" if map_pre == "A" else "A"
cust_dir = ROUNDS / "I7_C4C6_CUSTODIAN"
cust_dir.mkdir(parents=True, exist_ok=True)
(cust_dir / "pairing_map.json").write_text(json.dumps({
    "mapping": {map_pre: PRE, map_post: POST},
    "note": "custodian-held; solver-visible material must not contain SHAs",
    "assignment_rule": "first bit of sha256(commit-sha) parity (deterministic pseudo-random)",
}, indent=1, sort_keys=True), encoding="utf-8")

BUILD_DEFINES = {
    "CONFIG_DISABLE_ENVIRON": 1,
    "CONFIG_LIBC_HOMEDIR": '"/home"',
}
CLANG_CHECKS = [{"file": "libs/libc/unistd/lib_getcwd.c", "marker": "char *getcwd( char *buf, size_t size)"}]
ABSENCE_PROBES = [{"api": "getcwd", "file": "fixture/libs/libc/unistd/lib_getcwd.c"}]

# ── solver phase: adjudicate each UNLABELED snapshot independently ───────────
import shutil

solver_results = {}
for label in (map_pre, map_post):
    src = ACQ / ("pre" if label == map_pre else "post")
    # corpus layout: <corpus>/<label>/fixture/<snapshot tree>
    corpus_label = ROUNDS / "I7_C4C6_corpus" / label
    if corpus_label.exists():
        shutil.rmtree(corpus_label)
    corpus_label.mkdir(parents=True)
    shutil.copytree(src, corpus_label / "fixture")
    label_dir = ROUNDS / f"I7_C4C6_{label}"
    rnd = solve_case(
        f"I7-{label}", corpus_label, label_dir,
        build_defines=BUILD_DEFINES,
        build_id="BUILD-C4C6-DISABLE-ENVIRON",
        absence_probes=ABSENCE_PROBES,       # deterministic rule: lib_<api>.c => <api>
        include_dirs=["libs/libc/unistd", "stubs"],
        guard_flips=[{"symbol": "CONFIG_DISABLE_ENVIRON", "undefined": True}],
        clang_checks=CLANG_CHECKS,
    )
    refutations = execute_refutation(rnd, {
        "build_defines": BUILD_DEFINES,
        "freeze_id": rnd.freeze_id,
        "guard_flips": [{"symbol": "CONFIG_DISABLE_ENVIRON", "undefined": True}],
        "clang_checks": CLANG_CHECKS,
        "include_dirs": ["libs/libc/unistd", "stubs"],
    })
    scores = []
    for oc in rnd.outcomes:
        if oc.kind == "rulecard" and oc.rulecard is not None:
            comp = score_rulecard(oc.rulecard, fact_log_rows=rnd.gateway.log.all_facts(), sample_rate=0.30)
            scores.append({"rulecard_id": oc.rulecard["rulecard_id"],
                           "family": oc.rulecard["fault_class"],
                           "polarity": oc.rulecard["polarity"],
                           "statement": oc.rulecard["trigger_conditions"][0]["statement"][:140],
                           "verdict": comp.s5_verdict})
    solver_results[label] = {
        "freeze_id": rnd.freeze_id,
        "round_dir": str(label_dir),
        "outcomes": [{"kind": o.kind, "family": (o.rulecard or {}).get("fault_class"),
                      "polarity": (o.rulecard or {}).get("polarity"),
                      "missing_evidence": o.missing_evidence} for o in rnd.outcomes],
        "refutations": [{"rulecard_id": r["rulecard_id"], "state": r["state"]} for r in refutations],
        "scores": scores,
        "absence_cards": [s for s in scores if s["family"] == "existence_absence" and s["polarity"] == "negative"],
        "present_cards": [s for s in scores if s["family"] == "existence_absence" and s["polarity"] == "positive"],
    }

# solver-visible receipt: NO SHA content, only labels
(ROUNDS / "I7_C4C6_SOLVER_RECEIPT.json").write_text(json.dumps(solver_results, indent=1, sort_keys=True), encoding="utf-8")

print(f"pairing (custodian-held): {map_pre}=pre-fix, {map_post}=post-fix")
for label, res in solver_results.items():
    print(f"[{label}] freeze={res['freeze_id']} outcomes={len(res['outcomes'])}")
    for s in res["scores"]:
        print(f"   {s['verdict']:8s} {s['family']:24s} pol={s['polarity']:8s} {s['statement']}")
    print(f"   absence_cards={len(res['absence_cards'])} present_cards={len(res['present_cards'])}")
