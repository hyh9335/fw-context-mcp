"""PMR-4 repair: deterministic, family-scoped FAULTNESS / CONSEQUENCE GATE.

Evidence integrity (RS1/RS2: re-derivation, independent-surface agreement)
proves that EVIDENCE is true. It does NOT prove that the observed source
pattern constitutes a FAULT. This gate sits between hypothesis/card
construction and final fault-RuleCard promotion and demands, per frozen
Wave-1 family, COMPOSED evidence of harmful consequence / violated invariant /
broken required relationship 鈥?never pattern presence alone.

Design constraints (from the repair directive):
- deterministic, NO LLM, no causal DAG;
- family-scoped to the frozen wave1-families-1 library 鈥?no generalization;
- case-agnostic: NO case ids, file names, line numbers, or symbol names of
  any specific corpus case appear in this module; every decision derives
  from the round's own structured targets and K1 fact log;
- evaluated at ROUND level (after all K2 outcomes) so cross-family
  composition evidence (e.g. an existence_absence probe on the same file as a
  condition_config_gated region) is visible;
- a card that fails the gate is NOT promoted: it becomes a REJECTED terminal
  outcome with the failing roles recorded (fail-closed, first-class).

Role definitions (see FAULTNESS_GATE_SPEC.md):

existence_absence (subject symbol S, probe file F):
  A1 absence_in_pinned_build : negative fact naming S (exact lookup or raw-text grep)
  A2 definition_file_located : F exists in the frozen snapshot AND the round
     holds a fact pinned to F (index-coverage proof)
  A3 config_causal_cutoff    : a condition fact for F shows a CONFIG_*-guarded
     region compiled OUT under the pinned build (the guard, not bad luck,
     removes the code that would define S) AND S is not itself that guard
     symbol (a missing config macro is the CAUSE; its absence alone is not a
     consequence)

condition_config_gated (region [A,B] of file F, guard symbol G):
  R1 pattern_compiled_out  : a conditional-region fact for F shows an untaken
     region overlapping [A,B]
  R2 config_symbol_guard   : G is a CONFIG_* symbol
  R3 harmful_consequence   : at least one of 鈥?     (a) definition_cutoff : an existence_absence probe on the SAME file F
         proved a DEFINITION-LEVEL symbol absent (a symbol that is not any
         guard symbol of this region) 鈥?the compiled-out region is where that
         definition lives, so the guard removes required API/build behavior;
     (b) pairing_cutoff    : a negative call-flow fact names symbol X (no live
         caller) AND a raw-text grep hit shows X( ... ) at a line inside
         [A,B] 鈥?the compiled-out region contains the only call site, so the
         required init/submit relationship is broken.

init_submit_pairing (file F, call symbol X):
  P1 config_mismatch_condition : condition fact for F with a CONFIG_* guard
  P2 pairing_definition        : positive definition-window fact for F
  P3 pairing_call_flow_broken  : negative call-flow fact naming X

direct_flow_presence: roles [flow_evidence, definition_evidence,
  harmful_consequence]; the wave-1 templates mint no consequence fact, so the
  gate is UNSATISFIABLE for this family in wave 1 (fail-closed by design).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .statement_render import CONFIG_SYMBOL_RE

_REGION_FACT_SURFACES = ("fw_context_list_conditional_regions", "fw_context_explain_condition")
_ABSENCE_SURFACES = ("fw_context_lookup_symbol", "independent_grep")
_CALLFLOW_SURFACE = "fw_context_find_callers"
_DEFINITION_SURFACES = ("fw_context_read_file", "fw_context_lookup_symbol")

# definition-level = NOT a config guard symbol (a missing CONFIG_* macro is
# the cause of a dead guard, not a harmful consequence of one)
_CONTROL_KEYWORDS = ("if", "for", "while", "switch", "return", "else", "case", "sizeof")


def _artifact_text(row: dict) -> str:
    ref = str(row.get("raw_artifact_ref") or "")
    path = ref.split("#")[0]
    p = Path(path)
    if not path or not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _grep_rows(row: dict) -> list[dict]:
    out = []
    for line in _artifact_text(row).splitlines():
        m = re.match(r"^(.+?):(\d+): (.*)$", line)
        if m:
            out.append({"file": m.group(1).replace("\\", "/"), "line": int(m.group(2)), "text": m.group(3)})
    return out


def _norm(path: str) -> str:
    return str(path).replace("\\", "/")


def _same_file(a: str, b: str) -> bool:
    a, b = _norm(a).rstrip("/"), _norm(b).rstrip("/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def _dedupe(fact_rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for r in fact_rows:
        key = (r.get("fact_id"), r.get("exact_query"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _untaken_region_overlaps(fact_rows: list[dict], file: str, a: int, b: int) -> dict | None:
    """Find a condition fact for *file* whose artifact shows an untaken
    conditional region overlapping [a, b]. The taken state lives on the
    region's BRANCH rows in list_conditional_regions payloads (1 = taken,
    0 = false, -1 = not evaluated) 鈥?a region with no branch taken==1 does
    not compile."""
    for r in _dedupe(fact_rows):
        if r.get("evidence_surface") not in _REGION_FACT_SURFACES:
            continue
        rfile = str(r.get("file") or "")
        if not rfile or not _same_file(rfile, file):
            continue
        text = _artifact_text(r)
        if not text.strip():
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        regions = payload.get("regions") if isinstance(payload, dict) else payload
        if not isinstance(regions, list):
            continue
        for reg in regions:
            if not isinstance(reg, dict):
                continue
            rl, re_ = int(reg.get("root_line", 0) or 0), int(reg.get("end_line", 0) or 0)
            if not (rl <= b and re_ >= a):
                continue
            branches = reg.get("branches") or []
            taken_flags = [int(br.get("taken", -1) or -1) for br in branches if isinstance(br, dict)]
            compiled_in = any(t == 1 for t in taken_flags) or int(reg.get("taken", 0) or 0) == 1
            if not compiled_in:
                return {"fact_id": r.get("fact_id"), "region": [rl, re_],
                        "condition": reg.get("condition"),
                        "branch_taken": taken_flags}
    return None


def _condition_fact_for_file(fact_rows: list[dict], file: str) -> dict | None:
    for r in _dedupe(fact_rows):
        if r.get("evidence_surface") not in _REGION_FACT_SURFACES:
            continue
        rfile = str(r.get("file") or "")
        if rfile and _same_file(rfile, file):
            return r
    return None


def _negative_symbol_facts(fact_rows: list[dict], symbol: str) -> list[dict]:
    out = []
    for r in _dedupe(fact_rows):
        if r.get("polarity") != "negative":
            continue
        q = str(r.get("exact_query") or "")
        if symbol and symbol in q:
            out.append(r)
    return out


def _negative_callflow_symbols(fact_rows: list[dict]) -> dict[str, dict]:
    out = {}
    for r in _dedupe(fact_rows):
        if r.get("evidence_surface") != _CALLFLOW_SURFACE or r.get("polarity") != "negative":
            continue
        m = re.search(r"find_callers\(name='([^']+)'", str(r.get("exact_query") or ""))
        if m:
            out.setdefault(m.group(1), r)
    return out


def _guard_symbols_of(targets: list[dict], file: str, a: int, b: int) -> set[str]:
    gs: set[str] = set()
    for t in targets:
        if t.get("file") and _same_file(t["file"], file) and int(t.get("start_line", 0) or 0) <= b \
                and int(t.get("end_line", 0) or 0) >= a:
            gs.update(t.get("symbols") or [])
            if t.get("guard_symbol"):
                gs.add(t["guard_symbol"])
    return gs


def _ea_probe_file(targets: list[dict], symbol: str) -> str | None:
    for t in targets:
        if t.get("symbol") == symbol and t.get("file"):
            return t["file"]
    return None


def _tag_to_hypothesis_id(corpus_case: str, tag: str) -> str:
    return "H-" + re.sub(r"[^A-Z0-9-]", "-", f"{corpus_case}-{str(tag).upper()}")


def _outcome_target_map(outcomes: list, targets: list[dict], corpus_case: str) -> dict:
    """Map K2Outcome -> its structured target via the deterministic
    hypothesis_id derivation (H-<case>-<TAG>)."""
    m = {}
    for oc in outcomes:
        hyp = oc.hypothesis or {}
        hid = hyp.get("hypothesis_id")
        if hid:
            m[id(oc)] = next((t for t in targets
                              if _tag_to_hypothesis_id(corpus_case, t.get("tag", "")) == hid), None)
    return m


def evaluate_card_faultness(
    family: str,
    target: dict | None,
    card: dict,
    fact_rows: list[dict],
    *,
    targets: list[dict],
    snapshot_dir: Path,
) -> dict:
    """Evaluate the family-scoped faultness roles for one candidate card.
    Returns {passed, family, roles: {role: {satisfied, evidence}}, detail}."""
    roles: dict[str, dict] = {}

    def set_role(name: str, ok: bool, evidence: str):
        roles[name] = {"satisfied": bool(ok), "evidence": evidence[:300]}

    if family == "existence_absence" and target:
        sym = str(target.get("symbol") or "")
        probe_file = target.get("file") or _ea_probe_file(targets, sym)
        negs = _negative_symbol_facts(fact_rows, sym)
        set_role("A1_absence_in_pinned_build", bool(negs),
                 f"negative facts for {sym!r}: " + ", ".join(sorted({r.get('fact_id', '') for r in negs})))
        snap_ok = bool(probe_file) and (snapshot_dir / _norm(probe_file).split("/fixture/")[-1]).is_file() \
            or bool(probe_file) and (snapshot_dir.parent / _norm(probe_file)).is_file()
        file_fact = any(
            r.get("file") and _same_file(str(r["file"]), probe_file or "")
            for r in _dedupe(fact_rows)
        )
        set_role("A2_definition_file_located", bool(probe_file) and (snap_ok or file_fact),
                 f"probe file {probe_file!r} in frozen snapshot, coverage fact={file_fact}")
        causal = None
        if probe_file:
            for r in _dedupe(fact_rows):
                if r.get("evidence_surface") not in _REGION_FACT_SURFACES:
                    continue
                rfile = str(r.get("file") or "")
                if not rfile or not _same_file(rfile, probe_file):
                    continue
                text = _artifact_text(r)
                try:
                    payload = json.loads(text) if text.strip() else {}
                except json.JSONDecodeError:
                    payload = {}
                regions = payload.get("regions") if isinstance(payload, dict) else []
                for reg in regions or []:
                    cond = str(reg.get("condition") or "")
                    if CONFIG_SYMBOL_RE.search(cond) and not reg.get("taken"):
                        causal = {"fact_id": r.get("fact_id"), "condition": cond}
                        break
                if causal:
                    break
        guard_syms = set(CONFIG_SYMBOL_RE.findall(json.dumps(targets, default=str)))
        is_guard_itself = sym in guard_syms
        set_role("A3_config_causal_cutoff",
                 bool(causal) and not is_guard_itself,
                 f"config-caused compiled-out condition fact on {probe_file!r}: {causal}; "
                 f"subject is itself a guard symbol={is_guard_itself} (a missing CONFIG_* macro "
                 "is the cause, not a consequence)")

    elif family == "condition_config_gated" and target:
        f = str(target.get("file") or "")
        a = int(target.get("start_line", 0) or 0)
        b = int(target.get("end_line", 0) or 0)
        untaken = _untaken_region_overlaps(fact_rows, f, a, b)
        set_role("R1_pattern_compiled_out", bool(untaken),
                 f"untaken region overlapping [{a},{b}] in {f!r}: {untaken}")
        guards = _guard_symbols_of(targets, f, a, b) | set(target.get("symbols") or [])
        set_role("R2_config_symbol_guard", any(CONFIG_SYMBOL_RE.fullmatch(g) for g in guards),
                 f"guard symbols of region: {sorted(guards)}")
        # (a) definition_cutoff / pairing_cutoff: a negative fact proved a
        # symbol absent (definition-level, i.e. NOT any guard symbol of ANY
        # region on this file 鈥?a missing CONFIG_* macro is the cause, not the
        # consequence) whose structured target lives on this file. For targets
        # carrying a call site line, the call site must lie INSIDE the
        # compiled-out region for the link to hold.
        file_guard_syms: set[str] = set()
        for t in targets:
            if t.get("file") and _same_file(t["file"], f):
                file_guard_syms.update(t.get("symbols") or [])
                if t.get("guard_symbol"):
                    file_guard_syms.add(t["guard_symbol"])
        cut = None
        for t in targets:
            tsym = t.get("symbol")
            if not tsym or not t.get("file") or not _same_file(t["file"], f):
                continue
            if tsym in file_guard_syms:
                continue  # a missing guard macro is the cause, not the consequence
            tline = t.get("call_line", t.get("line"))
            if tline is not None and not (a <= int(tline) <= b):
                continue  # call/definition site outside this region: no link
            if _negative_symbol_facts(fact_rows, tsym):
                cut = {"symbol": tsym,
                       "via": "pairing_cutoff" if t.get("call_line") is not None else "definition_cutoff",
                       "site": f"{t['file']}:{tline}",
                       "absence_probe_file": t["file"]}
                break
        # (b) pairing_cutoff: negative call-flow for X + raw-text grep hit of
        # X( ... ) inside the region.
        if cut is None:
            for sym, row in _negative_callflow_symbols(fact_rows).items():
                for r in _dedupe(fact_rows):
                    if r.get("evidence_surface") != "independent_grep":
                        continue
                    for hit in _grep_rows(r):
                        hf = hit["file"]
                        if not (_same_file(hf, f) or Path(hf).name == Path(f).name):
                            continue
                        if not (a <= hit["line"] <= b):
                            continue
                        if re.search(rf"\b{re.escape(sym)}\s*\(", hit["text"]):
                            cut = {"symbol": sym, "via": "pairing_cutoff",
                                   "call_site": f"{hf}:{hit['line']}",
                                   "callflow_fact": row.get("fact_id")}
                            break
                    if cut:
                        break
                if cut:
                    break
        set_role("R3_harmful_consequence", bool(cut), json.dumps(cut or {}, sort_keys=True))

    elif family == "init_submit_pairing" and target:
        f = str(target.get("file") or "")
        call_sym = str(target.get("call_symbol") or target.get("symbol") or "")
        cond = _condition_fact_for_file(fact_rows, f)
        cond_txt = json.dumps({}) 
        if cond:
            try:
                cond_txt = _artifact_text(cond) or "{}"
            except Exception:
                cond_txt = "{}"
        set_role("P1_config_mismatch_condition", bool(cond) and bool(CONFIG_SYMBOL_RE.search(cond_txt)),
                 f"condition fact for {f!r}: {cond.get('fact_id') if cond else None}")
        defs = [r for r in _dedupe(fact_rows)
                if r.get("evidence_surface") in _DEFINITION_SURFACES and r.get("polarity") == "positive"
                and r.get("file") and _same_file(str(r["file"]), f)]
        set_role("P2_pairing_definition", bool(defs),
                 "definition-window facts: " + ", ".join(sorted({r.get("fact_id", "") for r in defs})))
        neg_cf = _negative_callflow_symbols(fact_rows).get(call_sym)
        set_role("P3_pairing_call_flow_broken", bool(neg_cf),
                 f"negative call-flow fact for {call_sym!r}: {neg_cf.get('fact_id') if neg_cf else None}")

    else:
        # unknown target / family: fail closed
        set_role("G0_family_roles_evaluable", False,
                 f"family={family!r} target={'present' if target else 'MISSING'} 鈥?fail-closed")

    passed = all(r["satisfied"] for r in roles.values())
    return {"passed": passed, "family": family, "roles": roles,
            "detail": "; ".join(f"{k}={'OK' if v['satisfied'] else 'FAIL'}" for k, v in roles.items())}


def run_round_faultness_gate(
    outcomes: list,
    targets: list[dict],
    fact_rows: list[dict],
    *,
    snapshot_dir: Path,
    corpus_case: str,
) -> tuple[list[dict], list[dict]]:
    """Round-level Faultness Gate.

    Returns (results, demotions):
      results    鈥?one record per rulecard outcome: {rulecard_id, family,
                   passed, roles, detail, hypothesis_id};
      demotions  鈥?one record per FAILED card: {rulecard_id, family,
                   hypothesis_id, failed_roles, detail} (the runner converts
                   these outcomes to REJECTED terminals).
    Card candidates without a resolvable structured target fail closed.
    """
    oc_target = _outcome_target_map(outcomes, targets, corpus_case)
    results: list[dict] = []
    demotions: list[dict] = []
    for oc in outcomes:
        if oc.kind != "rulecard" or oc.rulecard is None:
            continue
        card = oc.rulecard
        fam = card.get("fault_class", "")
        tgt = oc_target.get(id(oc))
        res = evaluate_card_faultness(fam, tgt, card, fact_rows,
                                      targets=targets, snapshot_dir=snapshot_dir)
        res.update({"rulecard_id": card["rulecard_id"],
                    "hypothesis_id": (oc.hypothesis or {}).get("hypothesis_id"),
                    "target_tag": (tgt or {}).get("tag")})
        results.append(res)
        if not res["passed"]:
            demotions.append({
                "rulecard_id": card["rulecard_id"],
                "family": fam,
                "hypothesis_id": res["hypothesis_id"],
                "failed_roles": sorted(k for k, v in res["roles"].items() if not v["satisfied"]),
                "detail": res["detail"],
            })
    return results, demotions
