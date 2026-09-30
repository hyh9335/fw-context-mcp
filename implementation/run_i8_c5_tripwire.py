"""I8_C5_T2_TRIPWIRE — quarantine discipline proof (C5).

C5 case fact (frozen): a real fault whose surface is a single-line
value-comparison (`link_ready() == 0` vs `!= 0` polarity inversion). The T2
rule says: a single-line surface is NEVER load-bearing; load-bearing use
requires a >=2-surface cross-check (E3). The tripwire to prove in this state:

  1. If the K2 loop tries to promote a mechanism whose ONLY decisive surface
     is single-line (T2), the evidence gate must REFUSE (E3/E2) — first-class
     refusal, not a silent pass, not a degraded card.
  2. The refusal must name the quarantine reason and the card must NOT be
     promoted_to_rule.
  3. The same mechanism, supplied with a SECOND independent surface
     (independent_grep of the comparison text), must PASS the gate — proving
     the quarantine is about surface count, not about the mechanism.

The C5 fixture is built here (small, deterministic); the T2 inversion is the
known frozen case fact, used ONLY as the guard-side truth for what the tripwire
must observe — the solver path itself is the same deterministic K2 loop.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.k1 import FactLog, K1Gateway, RoundPin  # noqa: E402
from fault_discovery_agent.k2.evidence_gate import run_evidence_gate  # noqa: E402
from fault_discovery_agent.k2.loop import K2Loop, load_family_library  # noqa: E402
from fault_discovery_agent.k3.custody import CustodyLog  # noqa: E402
from fault_discovery_agent.k3.plant_runner import _provision_project, clear_stale_index_locks  # noqa: E402
from fault_discovery_agent.freeze.manifest_tool import build_manifest, write_manifest  # noqa: E402

REPO = Path(__file__).resolve().parent
ROUND = REPO / "rounds" / "I8_C5_T2_TRIPWIRE"
if ROUND.exists():
    import shutil
    shutil.rmtree(ROUND)
ROUND.mkdir(parents=True, exist_ok=True)

# ── C5 fixture: the single-line comparison fault ────────────────────────────
FIX = ROUND / "fixture"
FIX.mkdir(exist_ok=True)
(FIX / "link_mgr.c").write_text(
    '''#include "link_mgr.h"

/* link_ready() returns 0 when the link is UP (0 == success convention). */
int link_ready(void);

int link_is_down(int state)
{
    /* FAULT (frozen case fact C5): polarity inverted on a single line —
     * the guard treats 0 (UP) as DOWN. */
    if (link_ready() != 0)
    {
        return 1; /* treated as down */
    }
    return 0;
}
''',
    encoding="utf-8", newline="\n")
(FIX / "link_mgr.h").write_text("int link_ready(void);\nint link_is_down(int state);\n", encoding="utf-8", newline="\n")

clear_stale_index_locks()
work = ROUND / "work"
work.mkdir(exist_ok=True)
cc = [{
    "directory": "%ROOT%",
    "arguments": ["clang", "fixture/link_mgr.c", "-Ifixture", "-o", "link_mgr.o"],
    "file": "fixture/link_mgr.c",
}]
PROJ = _provision_project(FIX, work, cc)

# freeze the C5 round (same manifest discipline as every E2E case-round)
snap = ROUND / "source_snapshot"
bc = ROUND / "build_config"
cf = ROUND / "corpus_fixture"
snap.mkdir(exist_ok=True)
bc.mkdir(exist_ok=True)
cf.mkdir(exist_ok=True)
for f in sorted(FIX.rglob("*")):
    if f.is_file():
        rel = f.relative_to(FIX)
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        (cf / rel).parent.mkdir(parents=True, exist_ok=True)
        import shutil as _sh
        _sh.copy2(f, snap / rel)
        _sh.copy2(f, cf / rel)
(bc / "build_inputs.json").write_text(json.dumps(
    {"build_id": "BUILD-C5", "defines": [], "lineage": "C",
     "pinned_by": "I8 driver (no build system in the C5 fixture; single-file fixture)"}, indent=1, sort_keys=True), encoding="utf-8")
manifest = build_manifest(
    case="C5",
    round_root=ROUND,
    source_snapshot=snap,
    build_config=bc,
    corpus_fixture=cf,
    oracle_hashes={},
    agent_identity={"package": "implementation/fault_discovery_agent", "schema": "wave1-design-gate-1"},
    solver_bundle_list=["source_snapshot/", "build_config/"],
    custodian_only_excluded=["oracle/"],
)
mpath = write_manifest(manifest, ROUND / "FREEZE_C5.json")
freeze_id = manifest["freeze_id"]
custody = CustodyLog(ROUND / "custody_log.jsonl")
custody.event("freeze_verified", freeze_id=freeze_id, manifest=Path(mpath).name)
bundle = ROUND / "solver_bundle"
bundle_files = [(f"source_snapshot/{f.relative_to(snap).as_posix()}", f) for f in sorted(snap.rglob("*")) if f.is_file()]
bundle_files.append(("build_config/build_inputs.json", bc / "build_inputs.json"))
custody.assemble_bundle(bundle, bundle_files, freeze_id)
custody.event("bundle_assembled", freeze_id=freeze_id, files=len(bundle_files))

log = FactLog(ROUND / "fact_log.jsonl")
pin = RoundPin(
    freeze_id=freeze_id,
    case="C5",
    project_root=str(PROJ),
    source_snapshot_id="SNAP-I8",
    build_snapshot_id="BUILD-C5",
    repository="inproc",
    commit="n/a",
    index_home=str(REPO / "fw_index_home"),
    fact_log_path=ROUND / "fact_log.jsonl",
    artifact_store=ROUND / "artifacts",
)
gw = K1Gateway(pin, log)
loop = K2Loop(gw, load_family_library())

TARGET = {
    "tag": "C5-LINK-READY-INVERSION",
    "symbol": "link_is_down",
    "symbol_definition_pattern": "link_ready\\(\\) != 0",
    "file": "fixture/link_mgr.c",
    "line": 9,
    "start_line": 8,
    "end_line": 16,
    "root": str(FIX),
    "single_line": True,            # the decisive surface is ONE line
    "single_line_marker": "link_ready() != 0",
    "predicate_statement": (
        "link_is_down inverts the link_ready() convention: the single-line "
        "guard 'link_ready() != 0' treats UP (0) as DOWN, so the caller sees "
        "the opposite of the real link state"
    ),
    "title": "C5 single-line polarity inversion",
    "value_decisive": True,
}

# ── TRIPWIRE 1: T2-only promotion must be refused ───────────────────────────
bundle = {"bundle_id": "B-C5-T2", "family": "direct_flow_presence", "corpus_case": "C5",
          "lineage": "C", "goal_item_ref": "G-7", "template_ref": "df-1", "freeze_id": pin.freeze_id}
oc_t2only = loop.run(bundle, TARGET)

# ── TRIPWIRE 2: the same mechanism + independent second surface must pass ──
# mint the independent grep surface (T1): the inversion text exists on raw disk
res_grep = gw.submit_independent_grep("link_ready\\(\\) != 0", FIX, gw.mint_fact_id("F"))
steps = []  # simulate the T2-only steps + the second surface at the GATE level
t2_fact_id = oc_t2only.hypothesis["required_facts"][0]["fact_id"] if oc_t2only.hypothesis else None
gate_input_steps = [
    {"step_id": "S1", "step_kind": "value",
     "description": "the guard compares link_ready() != 0 on a single line (T2 surface)",
     "required_fact_ids": [t2_fact_id] if t2_fact_id else [],
     "polarity": "positive", "single_line": True, "load_bearing": True,
     "fact_ids": [t2_fact_id] if t2_fact_id else []},
]
if res_grep.ok and res_grep.fact is not None:
    # the >=2-surface cross-check attaches to the SAME load-bearing step:
    # S1 then carries the T2 lookup + the T1 independent grep
    for s in gate_input_steps:
        s["fact_ids"] = s["fact_ids"] + [res_grep.fact["fact_id"]]
        s["required_fact_ids"] = s["required_fact_ids"] + [res_grep.fact["fact_id"]]
    steps2 = gate_input_steps
else:
    steps2 = gate_input_steps
gate_only_t2 = run_evidence_gate(
    [
        {**gate_input_steps[0], "fact_ids": [t2_fact_id] if t2_fact_id else [],
         "required_fact_ids": [t2_fact_id] if t2_fact_id else []},
    ],
    log.all_facts(), pinned_build_snapshot_id=pin.build_snapshot_id,
    hypothesis_text=json.dumps(TARGET))
gate_two_surface = run_evidence_gate(
    steps2, log.all_facts(), pinned_build_snapshot_id=pin.build_snapshot_id,
    hypothesis_text=json.dumps(TARGET))

receipt = {
    "phase": "I8_C5_T2_TRIPWIRE",
    "tripwire_1_t2_only": {
        "loop_outcome_kind": oc_t2only.kind,
        "promoted": oc_t2only.kind == "rulecard",
        "gate_refusals": [d.__dict__ for d in (oc_t2only.gate_decisions or []) if getattr(d, "passed", True) is False],
        "missing_evidence": oc_t2only.missing_evidence,
    },
    "tripwire_2_two_surfaces": {
        "gate_only_t2_refused": gate_only_t2.refused,
        "gate_only_t2_rules": [d.rule for d in gate_only_t2.refusals()],
        "gate_two_surface_refused": gate_two_surface.refused,
        "gate_two_surface_rules": [f"{d.rule}: {d.detail}" for d in gate_two_surface.refusals()],
        "second_surface_fact_id": res_grep.fact["fact_id"] if res_grep.fact else None,
        "second_surface_trust": res_grep.fact.get("trust_class") if res_grep.fact else None,
    },
    "quarantine_proof": {
        "no_degraded_promotion": oc_t2only.kind != "rulecard",
        "refusal_is_first_class": bool(oc_t2only.missing_evidence) or gate_only_t2.refused,
        "same_mechanism_passes_with_second_surface": not gate_two_surface.refused,
    },
}
(ROUND / "I8_RECEIPT.json").write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
print(json.dumps(receipt, indent=1, sort_keys=True))
