"""Wave-1.1 semantic-repair regression tests (R7).

Each test reproduces a SPECIFIC post-MVP defect or hardens a verifier flaw:
- T1  the exact C4 incoherence (definition_absent/negative card whose statement
      asserts the definition EXISTS) is rejected by the machine-enforced
      semantic-coherence validator, and the deterministic renderer can never
      produce it;
- T2  a C6-like benign conditional region (no in-round consequence evidence)
      FAILS the family-scoped Faultness Gate while the C4-like region (same
      file holds an independently-proved-absent definition) PASSES;
- T3  init_submit_pairing faultness requires the negative call-flow fact, and
      a call site outside the region cannot satisfy the cutoff link;
- T4  the terminal-receipt classifier is fail-closed (freeze-only / missing
      artifacts / non-terminal refutation states are NOT e2e) and reproduces
      every §3 class;
- T5  lineage comes from the driver-supplied metadata recorded in
      build_inputs.json, never from a runner constant;
- T6  the negative-control hardcoding scan finds zero case-specific tokens in
      the mechanism modules;
- T7  cards carrying the validation object validate against rulecard_v1_1
      while the FROZEN rulecard schema still rejects it (historical cards
      unchanged).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

IMPL = Path(__file__).resolve().parents[1]
REPO = IMPL.parent
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.k2.faultness_gate import (  # noqa: E402
    evaluate_card_faultness,
    run_round_faultness_gate,
)
from fault_discovery_agent.k2.statement_render import (  # noqa: E402
    render_statement,
    validate_semantic_coherence,
)
from fault_discovery_agent.k3.terminal_receipt import classify_round_receipt  # noqa: E402
from fault_discovery_agent.schemas.loader import load_schema, validate  # noqa: E402
from fault_discovery_agent.schemas.loader import SchemaValidationError  # noqa: E402


# ── T1: PMR-3 semantic coherence ────────────────────────────────────────────
def test_t1_c4_incoherence_defect_rejected():
    """The EXACT historical defect: structured predicate asserts absence while
    the statement asserts presence."""
    defect_card = {
        "rulecard_id": "RC-I7-A-API-GETCWD",
        "fault_class": "existence_absence",
        "polarity": "negative",
        "trigger_conditions": [{
            "predicate_id": "P1", "kind": "definition_absent", "negatable": True,
            "fact_refs": [{"fact_id": "F-1", "trust_class": "T1"}],
            "statement": "the public API getcwd has a compiled definition under the pinned build "
                         "(BUILD-C4C6-DISABLE-ENVIRON); its definition file and build listing are "
                         "config-gated in this snapshot",
        }],
        "evidence": [],
    }
    errs = validate_semantic_coherence(defect_card)
    assert errs, "the C4 defect MUST be rejected by the validator"
    assert any("contradicts structured predicate" in e for e in errs)


def test_t1_renderer_is_deterministic_and_cue_exact():
    stmt = render_statement("definition_absent", "negative", "getcwd", build_id="B1")
    assert stmt == "the symbol getcwd has no compiled definition under the pinned build (B1)"
    assert "has a compiled definition" not in stmt
    with pytest.raises(ValueError):
        render_statement("definition_absent", "positive", "x")  # incoherent combo
    with pytest.raises(ValueError):
        render_statement("condition_text", "negative", "x")  # incoherent combo


def test_t1_region_and_pairing_rendering():
    region = render_statement("condition_text", "positive", "fixture/mod.c", build_id="B",
                              condition_text="defined(CONFIG_X)", start_line=20, end_line=30)
    assert "compiled OUT" in region and "defined(CONFIG_X)" in region
    pair = render_statement("call_reachable", "negative", "fixture/mod.c", build_id="B",
                            call_symbol="work_submit", call_line=24, guard_symbol="CONFIG_X")
    assert "no live caller" in pair and "work_submit" in pair


def test_t1_evidence_polarity_consistency():
    card = {
        "rulecard_id": "R", "fault_class": "init_submit_pairing", "polarity": "negative",
        "trigger_conditions": [{"predicate_id": "P1", "kind": "call_reachable", "negatable": True,
                                "fact_refs": [], "statement":
                                "the work_submit call at f.c:1 is guarded by dead config symbol CONFIG_X; "
                                "under the pinned build it has no live caller"}],
        "evidence": [],
    }
    rows = [{"fact_id": "F1", "polarity": "positive"}]
    errs = validate_semantic_coherence(card, fact_rows=rows)
    assert any("no cited fact has negative polarity" in e for e in errs)


# ── T2/T3: Faultness Gate family scope ──────────────────────────────────────
def _mk_env(tmp_path: Path, *, api_absent: bool):
    """Synthetic round evidence: file fixture/mod.c with an untaken guarded
    region [20,30]; optional absent non-guard API on the same file."""
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    art = raw / "lcr_mod.json"
    art.write_text(json.dumps({"count": 1, "regions": [{
        "root_line": 20, "end_line": 30, "directive": "if",
        "condition": "defined(CONFIG_X_GUARD)",
        "branches": [{"line": 20, "kind": "if", "taken": 0}],
    }]}), encoding="utf-8")
    (tmp_path / "mod.c").write_text("// probe file exists in the snapshot\n", encoding="utf-8")
    rows = [
        {"fact_id": "F-COND", "evidence_surface": "fw_context_list_conditional_regions",
         "file": "fixture/mod.c", "polarity": "positive", "freeze_id": "FRZ", "trust_class": "T0",
         "exact_query": "list_conditional_regions(file_path='fixture/mod.c')",
         "raw_artifact_ref": str(art)},
        {"fact_id": "F-WIN", "evidence_surface": "fw_context_read_file",
         "file": "fixture/mod.c", "polarity": "positive", "freeze_id": "FRZ", "trust_class": "T0",
         "exact_query": "read_file(file_path='fixture/mod.c', start_line=20, end_line=30)"},
    ]
    if api_absent:
        rows.append({"fact_id": "F-NEG-API", "evidence_surface": "fw_context_lookup_symbol",
                     "file": "fixture/mod.c", "polarity": "negative", "freeze_id": "FRZ",
                     "trust_class": "T0", "exact_query": "lookup_symbol(name='needed_api', exact=True)"})
    return rows


def _region_card(cid: str) -> dict:
    return {"rulecard_id": cid, "fault_class": "condition_config_gated", "polarity": "positive",
            "trigger_conditions": [{"predicate_id": "P1", "kind": "condition_text", "negatable": True,
                                    "fact_refs": [], "statement": "region fixture/mod.c:20-30 guarded by "
                                    "'defined(CONFIG_X_GUARD)' is compiled OUT under the pinned build (B)"}],
            "evidence": []}


def test_t2_c6_like_region_candidate_fails_without_consequence(tmp_path):
    """Benign conditional: guard + compiled-out region, but NO independently
    proved-absent definition on the file -> NOT a fault card."""
    rows = _mk_env(tmp_path, api_absent=False)
    target = {"tag": "mod-20", "file": "fixture/mod.c", "start_line": 20, "end_line": 30,
              "symbols": ["CONFIG_X_GUARD"], "condition_text": "defined(CONFIG_X_GUARD)"}
    res = evaluate_card_faultness("condition_config_gated", target, _region_card("RC-X"),
                                  rows, targets=[target], snapshot_dir=tmp_path)
    assert not res["passed"]
    assert "R3_harmful_consequence" in res["roles"] and not res["roles"]["R3_harmful_consequence"]["satisfied"]


def test_t2_c4_like_region_candidate_passes_with_definition_cutoff(tmp_path):
    rows = _mk_env(tmp_path, api_absent=True)
    target = {"tag": "mod-20", "file": "fixture/mod.c", "start_line": 20, "end_line": 30,
              "symbols": ["CONFIG_X_GUARD"], "condition_text": "defined(CONFIG_X_GUARD)"}
    ea_target = {"tag": "api-needed_api", "symbol": "needed_api", "file": "fixture/mod.c"}
    res = evaluate_card_faultness("condition_config_gated", target, _region_card("RC-X"),
                                  rows, targets=[target, ea_target], snapshot_dir=tmp_path)
    assert res["passed"], res["detail"]
    assert res["roles"]["R3_harmful_consequence"]["evidence"].find("definition_cutoff") >= 0


def test_t2_absent_guard_macro_is_cause_not_consequence(tmp_path):
    """An EA card whose subject IS the guard symbol must FAIL A3 (a missing
    CONFIG_* macro is the CAUSE of a dead guard, not a harmful consequence)."""
    rows = _mk_env(tmp_path, api_absent=False)
    rows.append({"fact_id": "F-NEG-MACRO", "evidence_surface": "fw_context_lookup_symbol",
                 "file": "fixture/mod.c", "polarity": "negative", "freeze_id": "FRZ",
                 "trust_class": "T0", "exact_query": "lookup_symbol(name='CONFIG_X_GUARD', exact=True)"})
    ea_target = {"tag": "sym-CONFIG_X_GUARD", "symbol": "CONFIG_X_GUARD", "file": "fixture/mod.c"}
    card = {"rulecard_id": "RC-MACRO", "fault_class": "existence_absence", "polarity": "negative",
            "trigger_conditions": [{"predicate_id": "P1", "kind": "definition_absent", "negatable": True,
                                    "fact_refs": [], "statement":
                                    "the symbol CONFIG_X_GUARD has no compiled definition under the pinned build (B)"}],
            "evidence": []}
    res = evaluate_card_faultness("existence_absence", ea_target, card, rows,
                                  targets=[ea_target], snapshot_dir=tmp_path)
    assert not res["passed"]
    assert not res["roles"]["A3_config_causal_cutoff"]["satisfied"]


def test_t2_taken_region_never_yields_a_fault_card(tmp_path):
    """A region whose branch is TAKEN (compiles IN) cannot be a dead-guard fault."""
    rows = _mk_env(tmp_path, api_absent=True)
    raw = tmp_path / "raw" / "lcr_taken.json"
    raw.write_text(json.dumps({"count": 1, "regions": [{
        "root_line": 20, "end_line": 30, "directive": "if",
        "condition": "defined(CONFIG_X_GUARD)",
        "branches": [{"line": 20, "kind": "if", "taken": 1}],
    }]}), encoding="utf-8")
    rows[0] = {**rows[0], "raw_artifact_ref": str(raw)}
    target = {"tag": "mod-20", "file": "fixture/mod.c", "start_line": 20, "end_line": 30,
              "symbols": ["CONFIG_X_GUARD"], "condition_text": "defined(CONFIG_X_GUARD)"}
    ea_target = {"tag": "api-needed_api", "symbol": "needed_api", "file": "fixture/mod.c"}
    res = evaluate_card_faultness("condition_config_gated", target, _region_card("RC-X"),
                                  rows, targets=[target, ea_target], snapshot_dir=tmp_path)
    assert not res["passed"]
    assert not res["roles"]["R1_pattern_compiled_out"]["satisfied"]


def _pair_env(tmp_path: Path, *, call_line: int):
    rows = _mk_env(tmp_path, api_absent=False)
    rows.append({"fact_id": "F-CALLFLOW", "evidence_surface": "fw_context_find_callers",
                 "file": "fixture/mod.c", "polarity": "negative", "freeze_id": "FRZ",
                 "trust_class": "T0", "exact_query": "find_callers(name='work_submit', fact_id='F-CALLFLOW', direction='callers')"})
    return rows, {"tag": "mod-20-pair", "file": "fixture/mod.c", "line": 20, "start_line": 20,
                  "end_line": 30, "symbols": ["CONFIG_X_GUARD"], "symbol": "work_submit",
                  "call_symbol": "work_submit", "call_line": call_line, "guard_symbol": "CONFIG_X_GUARD"}


def test_t3_pairing_card_passes_with_negative_callflow(tmp_path):
    rows, target = _pair_env(tmp_path, call_line=24)
    card = {"rulecard_id": "RC-PAIR", "fault_class": "init_submit_pairing", "polarity": "negative",
            "trigger_conditions": [{"predicate_id": "P1", "kind": "call_reachable", "negatable": True,
                                    "fact_refs": [], "statement":
                                    "the work_submit call at fixture/mod.c:24 is guarded by dead config symbol "
                                    "CONFIG_X_GUARD; under the pinned build it has no live caller"}],
            "evidence": []}
    res = evaluate_card_faultness("init_submit_pairing", target, card, rows,
                                  targets=[target], snapshot_dir=tmp_path)
    assert res["passed"], res["detail"]


def test_t3_pairing_requires_negative_callflow(tmp_path):
    rows, target = _pair_env(tmp_path, call_line=24)
    rows = [r for r in rows if r["fact_id"] != "F-CALLFLOW"]
    card = {"rulecard_id": "RC-PAIR", "fault_class": "init_submit_pairing", "polarity": "negative",
            "trigger_conditions": [{"predicate_id": "P1", "kind": "call_reachable", "negatable": True,
                                    "fact_refs": [], "statement": "x"}],
            "evidence": []}
    res = evaluate_card_faultness("init_submit_pairing", target, card, rows,
                                  targets=[target], snapshot_dir=tmp_path)
    assert not res["passed"]


def test_t3_region_cutoff_requires_call_site_inside_region(tmp_path):
    """A negative call-flow whose call site lies OUTSIDE the region cannot
    satisfy the consequence link for that region."""
    rows, pair_target = _pair_env(tmp_path, call_line=99)
    region_target = {"tag": "mod-20", "file": "fixture/mod.c", "start_line": 20, "end_line": 30,
                     "symbols": ["CONFIG_X_GUARD"], "condition_text": "defined(CONFIG_X_GUARD)"}
    res = evaluate_card_faultness("condition_config_gated", region_target, _region_card("RC-X"),
                                  rows, targets=[region_target, pair_target], snapshot_dir=tmp_path)
    assert not res["passed"]


def test_t3_unresolvable_target_fails_closed(tmp_path):
    """A card whose structured target cannot be resolved must FAIL (never pass
    vacuously)."""
    class _OC:
        kind = "rulecard"
        rulecard = _region_card("RC-ORPHAN")
        hypothesis = {"hypothesis_id": "H-DOES-NOT-EXIST"}
        missing_evidence = []
        gate_decisions = []
    results, demotions = run_round_faultness_gate([_OC()], targets=[], fact_rows=[],
                                                  snapshot_dir=tmp_path, corpus_case="CX")
    assert not results[0]["passed"] and demotions


# ── T4: terminal-receipt classifier (fail-closed E2E criterion) ─────────────
def _receipt(**over):
    base = {
        "case_id": "CX", "traversal": {"source_staged": True, "fact_records": 5,
                                       "mechanism_hypotheses": 2, "rulecards_accepted": 1},
        "accepted_fault_cards": [{"rulecard_id": "RC-1", "faultness_status": "PASS",
                                  "evidence_integrity_status": "PASS",
                                  "refutation_state": "NOT_REFUTED_WITHIN_SCOPE"}],
        "rejected_candidates": [], "abstentions": [], "t2_scenarios": [],
        "semantic_coherence_violations": [],
    }
    base.update(over)
    return base


def test_t4_full_traversal_is_positive():
    assert classify_round_receipt(_receipt()) == "e2e_positive"


def test_t4_freeze_only_or_empty_is_not_e2e():
    assert classify_round_receipt({"traversal": {"source_staged": True}}) == "not_e2e"
    assert classify_round_receipt({}) == "not_e2e"
    assert classify_round_receipt(None) == "not_e2e"


def test_t4_unterminal_refutation_state_is_not_e2e():
    r = _receipt(accepted_fault_cards=[{"rulecard_id": "RC-1", "faultness_status": "PASS",
                                        "evidence_integrity_status": "PASS",
                                        "refutation_state": "UNEXECUTED"}])
    assert classify_round_receipt(r) != "e2e_positive"


def test_t4_coherence_violation_is_not_e2e():
    assert classify_round_receipt(_receipt(semantic_coherence_violations=["x"])) != "e2e_positive"


def test_t4_explicit_rejection_is_negative():
    r = _receipt(accepted_fault_cards=[], mechanism_hypotheses=1,
                 rejected_candidates=[{"rulecard_id": "RC-X", "family": "condition_config_gated",
                                       "failed_roles": ["R3_harmful_consequence"]}])
    assert classify_round_receipt(r) == "e2e_negative"


def test_t4_t2_refusal_is_safety_only_and_hypothesis_abstention_is_not():
    r = _receipt(accepted_fault_cards=[], mechanism_hypotheses=1,
                 abstentions=[{"kind": "insufficient_evidence", "missing_evidence": []}],
                 t2_scenarios=[{"code": "E3", "detail": "tripwire"}])
    assert classify_round_receipt(r) == "safety_only"
    r2 = {**r, "t2_scenarios": []}
    assert classify_round_receipt(r2) == "not_e2e"


def test_t4_hypothesis_abstention_alone_is_not_negative():
    """An abstention WITHOUT an explicit no-fault terminal marker is never a
    negative E2E case (it may be an under-traversal)."""
    r = _receipt(accepted_fault_cards=[], mechanism_hypotheses=1,
                 abstentions=[{"kind": "insufficient_evidence", "missing_evidence": []}])
    assert classify_round_receipt(r) in ("safety_only", "not_e2e")


# ── T5: lineage from metadata, not script constants ─────────────────────────
def test_t5_lineage_recorded_from_driver_metadata(tmp_path):
    from fault_discovery_agent.k3.e2e_runner import stage_case_round
    corpus = tmp_path / "corpus" / "fixture"
    corpus.mkdir(parents=True)
    (corpus / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    staged = stage_case_round("CX", corpus.parent, tmp_path / "round",
                              build_defines={"K": 1}, build_id="BUILD-X",
                              executor="test", lineage="Z (custom-test-lineage)")
    bi = json.loads((tmp_path / "round" / "build_config" / "build_inputs.json").read_text(encoding="utf-8"))
    assert bi["lineage"] == "Z (custom-test-lineage)"
    assert staged["build_inputs"]["lineage"] == "Z (custom-test-lineage)"


# ── T6: mechanism modules stay case-agnostic ────────────────────────────────
def test_t6_no_case_specific_tokens_in_mechanism_modules():
    import re
    from run_wave1_1_acceptance import CASE_AGNOSTIC_MODULES, CASE_TOKEN_RE
    hits = []
    for mod in CASE_AGNOSTIC_MODULES:
        src = mod.read_text(encoding="utf-8")
        stripped = re.sub(r'"""[\s\S]*?"""', "", src)
        stripped = re.sub(r"#.*", "", stripped)
        hits += [(mod.name, m.group(0)) for m in CASE_TOKEN_RE.finditer(stripped)]
    assert not hits, f"case-specific tokens leaked into mechanism modules: {hits}"


# ── T8: acceptance-runner card discovery is fail-closed (audit V5 repair) ───
def test_t8_missing_rulecards_dir_is_an_issue_not_a_vacuous_pass(tmp_path):
    from run_wave1_1_acceptance import _load_round_cards, reconcile_cards
    cards, issues = _load_round_cards(tmp_path / "round")
    assert cards == [] and issues, "a missing rulecards dir must surface as an issue"
    # a receipt CLAIMING accepted cards with a missing dir is fatal
    ok, detail = reconcile_cards([{"rulecard_id": "RC-X"}], cards, issues)
    assert not ok and detail["claimed_missing_on_disk"] == ["RC-X"]


def test_t8_unparseable_card_file_is_fatal(tmp_path):
    from run_wave1_1_acceptance import _load_round_cards, reconcile_cards
    rd = tmp_path / "round" / "rulecards"
    rd.mkdir(parents=True)
    (rd / "RC-BAD.json").write_text("{not json", encoding="utf-8")
    (rd / "RC-GOOD.json").write_text(json.dumps({"rulecard_id": "RC-GOOD"}), encoding="utf-8")
    cards, issues = _load_round_cards(tmp_path / "round")
    assert [c["rulecard_id"] for c in cards] == ["RC-GOOD"]
    assert issues and issues[0]["error"] == "unparseable card file"
    ok, _ = reconcile_cards([{"rulecard_id": "RC-GOOD"}], cards, issues)
    assert not ok, "an unparseable card file is always fatal"


def test_t8_reconciliation_both_directions(tmp_path):
    from run_wave1_1_acceptance import reconcile_cards
    disk = [{"rulecard_id": "RC-A"}, {"rulecard_id": "RC-B"}]
    # receipt under-claims (disk card unclaimed)
    ok, d = reconcile_cards([{"rulecard_id": "RC-A"}], disk, [])
    assert not ok and d["on_disk_unclaimed"] == ["RC-B"]
    # receipt over-claims (claimed id missing on disk)
    ok, d = reconcile_cards([{"rulecard_id": "RC-A"}, {"rulecard_id": "RC-Z"}], disk, [])
    assert not ok and d["claimed_missing_on_disk"] == ["RC-Z"]
    # exact match with no issues passes
    ok, d = reconcile_cards([{"rulecard_id": "RC-B"}, {"rulecard_id": "RC-A"}], disk, [])
    assert ok and d["claimed_missing_on_disk"] == [] and d["on_disk_unclaimed"] == []


def test_t8_missing_dir_with_zero_claims_is_not_fatal(tmp_path):
    from run_wave1_1_acceptance import reconcile_cards
    ok, _ = reconcile_cards([], [], [{"error": "rulecards directory missing", "dir": "x"}])
    assert ok, "a negative-control round with no accepted cards and no card files is consistent"
def test_t7_frozen_schema_still_rejects_validation_object():
    refs = [{"fact_id": "F-1", "trust_class": "T0"}]
    card = {
        "rulecard_id": "RC-H", "mechanism_id": "M-H", "hypothesis_ref": "H-CX-T",
        "fault_class": "condition_config_gated", "scope_stamp": {"goal_item": "G-1", "corpus_case": "CX",
                                                                 "lineage": "Z", "schema_version": "wave1-design-gate-1"},
        "trigger_conditions": [{"predicate_id": "P1", "kind": "condition_text", "negatable": True,
                                "fact_refs": refs, "statement": "s"}],
        "evidence": [{"fact_id": "F-1", "trust_class": "T0"}],
        "provenance": {"derived_from": ["fw_context_list_conditional_regions"], "certainty": "measured", "k1_mediated": True},
        "polarity": "positive", "forbidden_overclaims": ["no runtime claims"],
        "refutation_protocol": {"steps": [{"step_id": "RS1", "action": "query_substrate_via_k1",
                                           "inputs": {"query": "list_conditional_regions"},
                                           "expected_two_outcomes": {
                                               "rule_holds_when": "the region fact shows the guard untaken",
                                               "rule_falsified_when": "the region fact shows the guard taken"}}],
                                "oracle_requirement": "blind_oracle_custodian"},
        "refutation_result": {"state": "UNEXECUTED", "protocol_completed": False,
                              "counterexample_ref": None, "missing_evidence_refs": [],
                              "adjudicated_at": None},
        "validation_status": "unvalidated", "oracle_provenance": "none",
        "validation": {"evidence_integrity_status": "PASS", "faultness_status": "PASS",
                       "refutation_state": "UNEXECUTED"},
    }
    with pytest.raises(SchemaValidationError):
        validate(card, "rulecard")           # frozen contract unchanged
    validate(card, "rulecard_v1_1")          # extended package-local schema
    assert "validation" in load_schema("rulecard_v1_1")["properties"]
    # a v1.1 card missing an axis fails
    bad = json.loads(json.dumps(card))
    del bad["validation"]["faultness_status"]
    with pytest.raises(SchemaValidationError):
        validate(bad, "rulecard_v1_1")


def test_t7_new_rulecard_attaches_validation(tmp_path, monkeypatch):
    from fault_discovery_agent.schemas import models
    card = models.new_rulecard(
        rulecard_id="RC-V", mechanism_id="M-V", hypothesis_ref="H-CX-V",
        fault_class="condition_config_gated",
        scope_stamp={"goal_item": "G-1", "corpus_case": "CX", "lineage": "Z", "schema_version": "wave1-design-gate-1"},
        trigger_conditions=[{"predicate_id": "P1", "kind": "condition_text", "negatable": True,
                             "fact_refs": [{"fact_id": "F-1", "trust_class": "T0"}],
                             "statement": "region f.c:1-2 guarded by 'defined(CONFIG_X)' is compiled OUT under the pinned build (B)"}],
        evidence=[{"fact_id": "F-1", "trust_class": "T0"}],
        provenance={"derived_from": ["fw_context_list_conditional_regions"], "certainty": "measured", "k1_mediated": True},
        polarity="positive", forbidden_overclaims=["no runtime claims"],
        refutation_protocol={"steps": [{"step_id": "RS1", "action": "query_substrate_via_k1",
                                        "inputs": {"query": "list_conditional_regions"},
                                        "expected_two_outcomes": {
                                            "rule_holds_when": "the region fact shows the guard untaken",
                                            "rule_falsified_when": "the region fact shows the guard taken"}}],
                             "oracle_requirement": "blind_oracle_custodian"},
        validation={"evidence_integrity_status": "PASS", "faultness_status": "PENDING",
                    "refutation_state": "UNEXECUTED"},
    )
    assert card["validation"]["faultness_status"] == "PENDING"
