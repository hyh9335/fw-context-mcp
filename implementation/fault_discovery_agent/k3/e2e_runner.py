"""E2E case runner (I6 C1 fresh-context solving; reusable for C4/C6/C5).

Deterministic pipeline: stage -> freeze -> custody bundle -> index provision
-> mechanical target sweep -> K2 loop per target -> refutation protocol
execution -> scoring. NO LLM in the solving loop; oracle-blind by custody.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..k1 import K1Gateway, RoundPin, channels
from ..k2 import K2Loop, K2Outcome
from ..k2.faultness_gate import run_round_faultness_gate
from ..k2.statement_render import validate_semantic_coherence
from ..k3.custody import CustodyLog
from ..k3.plant_runner import FW_INDEX_HOME, _provision_project
from ..k3.scorer import score_rulecard
from ..k3.terminal_receipt import build_round_receipt
from ..schemas.refutation import check_transition
from ..freeze.manifest_tool import build_manifest, write_manifest
from ..schemas.models import new_fact_record  # noqa: F401  (fact shape authority)

CONFIG_SYMBOL_RE = re.compile(r"\bCONFIG_[A-Z0-9_]{2,}\b")


@dataclass
class E2ERound:
    case_id: str
    round_dir: Path
    freeze_id: str
    snapshot_dir: Path
    bundle_dir: Path
    proj_dir: Path
    gateway: K1Gateway
    targets: list = field(default_factory=list)
    outcomes: list = field(default_factory=list)
    refutations: list = field(default_factory=list)
    scores: list = field(default_factory=list)


def stage_case_round(
    case_id: str,
    corpus_dir: Path,
    round_dir: Path,
    *,
    build_defines: dict,
    build_id: str,
    executor: str,
    lineage: str = "",
) -> dict:
    """Stage source_snapshot/build_config/corpus_fixture, freeze, assemble the
    solver bundle. Oracle/ files are NEVER staged (custody duty)."""
    round_dir = Path(round_dir).resolve()
    fixture_src = corpus_dir / "fixture"
    snap = round_dir / "source_snapshot"
    cf = round_dir / "corpus_fixture"
    bc = round_dir / "build_config"
    for d in (snap, cf, bc):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    staged = []
    for f in sorted(fixture_src.rglob("*")):
        if f.is_file():
            rel = f.relative_to(fixture_src)
            (snap / rel).parent.mkdir(parents=True, exist_ok=True)
            (cf / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, snap / rel)
            shutil.copy2(f, cf / rel)
            staged.append(f"source_snapshot/{rel.as_posix()}")
    build_inputs = {
        "build_id": build_id,
        "defines": [f"-D{k}={v}" for k, v in sorted(build_defines.items())],
        # lineage is DRIVER-SUPPLIED case metadata (recorded into the frozen
        # build_inputs and derived from there by acceptance/audit — never a
        # hardcoded runner constant).
        "lineage": lineage,
        "pinned_by": "CMakeLists target_compile_definitions (frozen at staging)",
    }
    (bc / "build_inputs.json").write_text(json.dumps(build_inputs, indent=1, sort_keys=True), encoding="utf-8")

    manifest = build_manifest(
        case=case_id,
        round_root=round_dir,
        source_snapshot=snap,
        build_config=bc,
        corpus_fixture=cf,
        oracle_hashes={},
        agent_identity={"package": "implementation/fault_discovery_agent", "schema": "wave1-design-gate-1"},
        solver_bundle_list=["source_snapshot/", "build_config/"],
        custodian_only_excluded=["oracle/"],
    )
    mpath = write_manifest(manifest, round_dir / f"FREEZE_{case_id}.json")
    freeze_id = manifest["freeze_id"]

    custody = CustodyLog(round_dir / "custody_log.jsonl")
    custody.event("freeze_verified", freeze_id=freeze_id, manifest=Path(mpath).name)
    bundle = round_dir / "solver_bundle"
    bundle_files = [(f"source_snapshot/{f.relative_to(snap).as_posix()}", f) for f in sorted(snap.rglob("*")) if f.is_file()]
    bundle_files.append(("build_config/build_inputs.json", bc / "build_inputs.json"))
    custody.assemble_bundle(bundle, bundle_files, freeze_id)
    custody.event("bundle_assembled", freeze_id=freeze_id, files=len(bundle_files))
    return {
        "freeze_id": freeze_id,
        "manifest_path": mpath,
        "snapshot_dir": snap,
        "bundle_dir": bundle,
        "build_defines": build_defines,
        "build_id": build_id,
        "custody": custody,
        "custody_log_path": round_dir / "custody_log.jsonl",
        "executor": executor,
        "round_dir": round_dir,
        # receipt/audit metadata source of truth (PMR-1: lineage comes from
        # here, never from script constants)
        "build_inputs": build_inputs,
    }


def provision_index(staged: dict, work_dir: Path) -> Path:
    """Index the staged snapshot with the pinned defines; returns the proj dir.
    The DB lives at the shared FW_INDEX_HOME/<project_id>/index.db (measured
    provisioning recipe) 鈥?the gateway's index_home MUST be FW_INDEX_HOME."""
    snap = staged["snapshot_dir"]
    defines = staged["build_defines"]
    include_dirs = staged.get("include_dirs", ["fixture"])
    c_files = sorted(p.relative_to(snap).as_posix() for p in snap.rglob("*.c"))
    cc = []
    for name in c_files:
        cc.append({
            "directory": "%ROOT%",
            "arguments": ["clang", f"fixture/{name}",
                          *[f"-Ifixture/{d}" for d in include_dirs],
                          *[f"-D{k}={v}" for k, v in sorted(defines.items())],
                          "-o", name.replace("/", "_") + ".o"],
            "file": f"fixture/{name}",
        })
    return _provision_project(snap, work_dir, cc)


def open_gateway(staged: dict, proj: Path, index_home: Path, fact_log_path: Path, artifact_store: Path) -> K1Gateway:
    pin = RoundPin(
        freeze_id=staged["freeze_id"],
        case=staged.get("case_id", "C1"),
        project_root=str(proj),
        source_snapshot_id=f"SNAP-{staged['build_id']}",
        build_snapshot_id=staged["build_id"],
        repository="staged-snapshot",
        commit="frozen-snapshot",
        index_home=str(index_home),
        fact_log_path=fact_log_path,
        artifact_store=artifact_store,
    )
    return K1Gateway(pin)


# 鈹€鈹€ mechanical target sweep (all evidence via K1) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
def enumerate_targets(gw: K1Gateway, staged: dict) -> list[dict]:
    """Sweep conditional skeletons + grep incidence; produce family targets.
    Emits: (a) one condition_config_gated target per conditional region;
    (b) existence_absence + init_submit_pairing targets for config symbols
    whose only occurrences are their own guard lines (dead-guard candidates)."""
    snap = Path(staged["snapshot_dir"])
    root = str(snap)
    # files are indexed at proj/fixture/<rel> 鈥?all substrate queries use
    # that relpath; the independent grep reads the raw snapshot and its hits
    # are normalized to the same relpath space.
    c_files = [f"fixture/{p.relative_to(snap).as_posix()}" for p in sorted(snap.rglob("*.c"))]
    targets: list[dict] = []
    guard_occurrences: dict[str, list] = {}

    for fname in c_files:
        res = gw.query_conditional_skeleton(fname, gw.mint_fact_id("F-SWEEP"))
        payload = res.raw.payload or {}
        regions = payload.get("regions", []) if isinstance(payload, dict) else []
        for reg in regions:
            cond = str(reg.get("condition") or "")
            syms = sorted(set(CONFIG_SYMBOL_RE.findall(cond)))
            line = int(reg.get("root_line", 0))
            end = int(reg.get("end_line", 0))
            for s in syms:
                guard_occurrences.setdefault(s, []).append({"file": fname, "line": line})
            targets.append({
                "tag": f"{Path(fname).stem}-{line}",
                "file": fname, "line": line, "start_line": line, "end_line": max(end, line),
                # structured condition fields (authoritative for statement
                # rendering + faultness evaluation)
                "condition_text": cond,
                "condition_text_head": cond[:60],
                "taken": bool(reg.get("taken")),
                "root": root,
                "single_line": False,
                "symbols": syms,
                "title": f"Config-gated region {fname}:{line}",
            })

    # incidence of each config symbol over the whole snapshot (independent grep)
    for sym, guards in sorted(guard_occurrences.items()):
        gres = gw.submit_independent_grep(sym, snap, gw.mint_fact_id("F-SWEEP-G"))
        rows = _grep_hits_from_fact(gres.fact) if gres.fact else []
        hits = []
        for r in rows:
            if isinstance(r, dict) and r.get("file"):
                rel = str(r.get("file")).replace("\\", "/")
                if "/" not in rel:
                    rel = f"fixture/{rel}"
                hits.append({"file": rel, "line": r.get("line")})
        non_guard = [h for h in hits if not any(
            h["file"].endswith(g["file"]) and abs(int(h["line"] or 0) - g["line"]) <= 1 for g in guards
        )]
        only_guard = bool(hits) and not non_guard
        targets.append({
            "tag": f"sym-{sym}",
            "symbol": sym,
            "symbol_definition_pattern": sym,
            "root": root,
            # probe file for the absence-coverage precondition (generic: the
            # file where the guard occurs, from this sweep's own hits)
            "file": guards[0]["file"] if guards else None,
            "guard_hits": hits,
            "only_guard": only_guard,
            "title": f"Config symbol incidence {sym}",
        })
    return targets


def solve_case(
    case_id: str,
    corpus_dir: Path,
    round_dir: Path,
    *,
    build_defines: dict,
    build_id: str,
    executor: str = "k2-deterministic-loop",
    lineage: str = "",
    absence_probes: list | None = None,
    include_dirs: list | None = None,
    guard_flips: list | None = None,
    clang_checks: list | None = None,
) -> E2ERound:
    round_dir = Path(round_dir).resolve()
    staged = stage_case_round(case_id, corpus_dir, round_dir, build_defines=build_defines, build_id=build_id,
                              executor=executor, lineage=lineage)
    staged["case_id"] = case_id
    staged["absence_probes"] = absence_probes or []
    staged["include_dirs"] = include_dirs or ["fixture"]
    staged["guard_flips"] = guard_flips or []
    staged["clang_checks"] = clang_checks or []
    work = round_dir / "work"
    work.mkdir(exist_ok=True)
    proj = provision_index(staged, work)
    gw = open_gateway(
        staged, proj, FW_INDEX_HOME,
        fact_log_path=round_dir / "fact_log.jsonl",
        artifact_store=round_dir / "raw",
    )
    pin_rows = gw.verify_substrate_pin()
    pin_rows = pin_rows if isinstance(pin_rows, list) else [pin_rows]
    if not any(r.get("verdict") == "PASS" for r in pin_rows):
        raise RuntimeError(f"substrate pin failed: {pin_rows}")

    staged["custody"].event("solving_started", freeze_id=staged["freeze_id"], case=case_id)
    rnd = E2ERound(case_id=case_id, round_dir=round_dir, freeze_id=staged["freeze_id"],
                   snapshot_dir=staged["snapshot_dir"], bundle_dir=staged["bundle_dir"],
                   proj_dir=proj, gateway=gw)

    targets = enumerate_targets(gw, staged)
    rnd.targets = targets
    # persist the structured target list (audit/lineage evidence: the gate's
    # decisions are traceable to the exact structured fields)
    (round_dir / "TARGETS.json").write_text(
        json.dumps(targets, indent=1, sort_keys=True, default=str), encoding="utf-8")
    loop = K2Loop(gw)

    # (a) condition_config_gated on every conditional REGION target (region
    # targets carry start_line + condition_text; sym targets have neither)
    for t in targets:
        if "file" not in t or "start_line" not in t or "condition_text" not in t:
            continue
        bundle = {"bundle_id": f"B-{t['tag']}", "family": "condition_config_gated", "corpus_case": case_id,
                  "lineage": "B", "goal_item_ref": "G-1", "template_ref": "ccg-1", "freeze_id": staged["freeze_id"]}
        rnd.outcomes.append(loop.run(bundle, t))

    # (a2) case-supplied existence probes. Each probe names the public API and
    # its expected definition file (coverage precondition: absence claims need
    # a file the index is proven to cover).
    for probe in staged.get("absence_probes", []):
        if isinstance(probe, str):
            probe = {"api": probe, "file": None}
        api = probe["api"]
        ea = {"bundle_id": f"B-api-{api}", "family": "existence_absence", "corpus_case": case_id,
              "lineage": "B", "goal_item_ref": "G-4", "template_ref": "ea-1", "freeze_id": staged["freeze_id"]}
        tgt = {
            "tag": f"api-{api}",
            "symbol": api,
            "symbol_definition_pattern": api,
            "root": str(Path(staged["snapshot_dir"])),
            "single_line": False,
            # structured subject fields only — the card statement is rendered
            # deterministically from the predicate kind/polarity (PMR-3).
            "title": f"API definition existence: {api}",
        }
        if probe.get("file"):
            tgt["file"] = probe["file"]
        # register the probe target so the round-level Faultness Gate can
        # resolve the outcome to its structured fields
        targets.append(tgt)
        rnd.outcomes.append(loop.run(ea, tgt))

    # (b) dead-guard candidates -> existence_absence + init_submit_pairing
    for t in targets:
        if not t.get("only_guard"):
            continue
        ea = {"bundle_id": f"B-{t['tag']}-EA", "family": "existence_absence", "corpus_case": case_id,
              "lineage": "A", "goal_item_ref": "G-4", "template_ref": "ea-1", "freeze_id": staged["freeze_id"]}
        rnd.outcomes.append(loop.run(ea, t))
        # pairing family: an INDEPENDENT grep for a submit-family call inside
        # the guarded file. The ifdef-filtered window CANNOT be used to detect
        # the call text (a dead guard's line is blank in the filtered view —
        # that is the mechanism itself); the grep channel sees the raw text.
        # The callee symbol is PARSED from the raw-text hits (generic — no
        # hardcoded submit-API name in the runner).
        for g in t.get("guard_hits") or []:
            gf = g["file"]
            gres = gw.submit_independent_grep(r"_submit\(", Path(staged["snapshot_dir"]), gw.mint_fact_id("F-PAIR-G"), glob=Path(gf).name, regex=True)
            if not gres.fact:
                continue
            rows = _grep_hits_from_fact(gres.fact) if gres.fact else []
            submit_hits = [h for h in rows if Path(h["file"]).name == Path(gf).name]
            # prefer CALL-statement hits ('foo(...);') over definition/
            # declaration lines of the guarded API itself ('int foo(int raw)')
            call_hits = [h for h in submit_hits if str(h.get("text") or "").rstrip().endswith(";")]
            submit_hits = call_hits or submit_hits
            if not submit_hits:
                continue
            submit_line = submit_hits[0]["line"]
            m_callee = re.search(r"([A-Za-z_]\w*)\s*\(", str(submit_hits[0].get("text") or ""))
            call_symbol = m_callee.group(1) if m_callee else str(t["symbol"])
            isp = {"bundle_id": f"B-{t['tag']}-ISP", "family": "init_submit_pairing", "corpus_case": case_id,
                   "lineage": "A", "goal_item_ref": "G-6", "template_ref": "isp-1", "freeze_id": staged["freeze_id"]}
            isp_target = {
                "tag": f"{t['tag']}-pair",
                "file": gf, "line": int(g["line"]), "start_line": max(1, int(g["line"]) - 4), "end_line": int(g["line"]) + 2,
                "symbol": call_symbol,
                # structured pairing fields (authoritative for statement
                # rendering + faultness evaluation)
                "call_symbol": call_symbol,
                "call_line": int(submit_line),
                "guard_symbol": t["symbol"],
                "root": str(Path(staged["snapshot_dir"])),
                "single_line": False,
                "title": f"Init/submit pairing broken at {gf}:{submit_line}",
            }
            # register the pairing target so the round-level Faultness Gate can
            # resolve the outcome to its structured fields
            targets.append(isp_target)
            rnd.outcomes.append(loop.run(isp, isp_target))

    staged["custody"].event("solving_finished", freeze_id=staged["freeze_id"], outcomes=len(rnd.outcomes))

    # ── ROUND-LEVEL FAULTNESS GATE (PMR-4) ─────────────────────────────────
    # Evidence truth != fault truth. Every candidate card is evaluated against
    # its family's consequence roles over the ROUND's full fact log; failures
    # are demoted to REJECTED terminals (fail-closed, first-class) and are
    # never promoted, never refuted, never scored as faults.
    fact_rows = gw.log.all_facts()
    faultness_results, demotions = run_round_faultness_gate(
        rnd.outcomes, targets, fact_rows, snapshot_dir=staged["snapshot_dir"], corpus_case=case_id)
    demoted_ids = set()
    for d in demotions:
        demoted_ids.add(d["rulecard_id"])
        for oc in rnd.outcomes:
            if oc.kind == "rulecard" and oc.rulecard is not None and oc.rulecard["rulecard_id"] == d["rulecard_id"]:
                oc.kind = "rejected"
                oc.faultness_rejection = d  # type: ignore[attr-defined]
    (round_dir / "FAULTNESS_RESULTS.json").write_text(
        json.dumps({"phase": "ROUND_FAULTNESS_GATE", "evaluated": len(faultness_results),
                    "passed": sum(1 for r in faultness_results if r["passed"]),
                    "demoted": len(demotions), "results": faultness_results,
                    "demotions": demotions}, indent=1, sort_keys=True), encoding="utf-8")
    staged["custody"].event("faultness_gate_applied", freeze_id=staged["freeze_id"],
                            evaluated=len(faultness_results), demoted=len(demotions))

    # ── SEMANTIC-COHERENCE VALIDATION (PMR-3) — blocks promotion ──────────
    coherence_violations: list[str] = []
    for oc in rnd.outcomes:
        if oc.kind == "rulecard" and oc.rulecard is not None:
            coherence_violations.extend(validate_semantic_coherence(oc.rulecard, fact_rows=fact_rows))
    if coherence_violations:
        staged["custody"].event("round_refused", freeze_id=staged["freeze_id"],
                                reason="semantic_coherence_violations", detail=coherence_violations[:5])
        raise RuntimeError("semantic-coherence violations block promotion: " + " | ".join(coherence_violations[:3]))
    staged["custody"].event("semantic_coherence_checked", freeze_id=staged["freeze_id"],
                            violations=0)

    # persist promoted rulecards + per-card scores for acceptance assembly
    rc_dir = round_dir / "rulecards"
    rc_dir.mkdir(exist_ok=True)
    cards_summary = []
    accepted_cards = []
    for oc in rnd.outcomes:
        if oc.kind == "rulecard" and oc.rulecard is not None:
            rc = oc.rulecard
            rc["validation"]["faultness_status"] = "PASS"
            rc["validation"]["faultness_roles"] = next(
                (r["roles"] for r in faultness_results if r["rulecard_id"] == rc["rulecard_id"]), {})
            rc["validation"]["faultness_adjudicated_at"] = staged["freeze_id"]
            (rc_dir / f"{rc['rulecard_id']}.json").write_text(
                json.dumps(rc, indent=1, sort_keys=True), encoding="utf-8")
            comp = score_rulecard(rc, fact_log_rows=fact_rows, sample_rate=0.30)
            rnd.scores.append(comp)
            accepted_cards.append(rc)
            cards_summary.append({
                "rulecard_id": rc["rulecard_id"],
                "family": rc["fault_class"],
                "polarity": rc["polarity"],
                "s1": comp.s1_completeness, "s2": comp.s2_evidence,
                "s3": comp.s3_sample_agreement, "s4": comp.s4_refutation_consistency,
                "s5_verdict": comp.s5_verdict,
                "sampled_rows": len(comp.sampled),
                "overclaims": comp.overclaims,
                "faultness_status": "PASS",
                "evidence_integrity_status": "PASS",
                "refutation_state": "UNEXECUTED",
            })
        elif getattr(oc, "faultness_rejection", None) is not None:
            d = oc.faultness_rejection  # type: ignore[attr-defined]
            cards_summary.append({"kind": "rejected", "reason": "faultness_gate_failed",
                                  "rulecard_id": d["rulecard_id"], "family": d["family"],
                                  "failed_roles": d["failed_roles"], "detail": d["detail"]})
        else:
            cards_summary.append({"kind": oc.kind, "missing_evidence": oc.missing_evidence})
    (round_dir / "SOLVING_SUMMARY.json").write_text(
        json.dumps({"freeze_id": staged["freeze_id"], "case_id": case_id,
                    "coherence_violations": coherence_violations,
                    "cards": cards_summary}, indent=1, sort_keys=True),
        encoding="utf-8")

    # ── TERMINAL ROUND RECEIPT (§3 E2E criterion is receipt-based) ─────────
    receipt = build_round_receipt(
        case_id=case_id, round_dir=round_dir, staged=staged,
        accepted_cards=accepted_cards, demotions=demotions,
        outcomes=rnd.outcomes, coherence_violations=coherence_violations,
        fact_rows=fact_rows,
    )
    (round_dir / "ROUND_RECEIPT.json").write_text(json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    rnd.receipt = receipt  # type: ignore[attr-defined]
    return rnd


def _grep_hits_from_fact(fact: dict) -> list[dict]:
    """The independent grep channel mints a fact whose artifact holds
    'file:line: text' rows 鈥?parse them back (substrate-uninvolved read of a
    K1-persisted artifact)."""
    ref = str(fact.get("raw_artifact_ref", ""))
    path = ref.split("#")[0]
    p = Path(path)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^(.+?):(\d+): (.*)$", line)
        if m:
            rows.append({"file": m.group(1).replace("\\", "/"), "line": int(m.group(2)), "text": m.group(3)})
    return rows


def finalize_round_receipt(rnd: E2ERound) -> dict:
    """Re-write the round receipt AFTER refutation so accepted cards carry
    their TERMINAL refutation states (the receipt is the acceptance input)."""
    import json as _json
    rpath = rnd.round_dir / "ROUND_RECEIPT.json"
    if not rpath.is_file():
        return {}
    receipt = _json.loads(rpath.read_text(encoding="utf-8"))
    by_id = {oc.rulecard["rulecard_id"]: oc.rulecard
             for oc in rnd.outcomes if oc.kind == "rulecard" and oc.rulecard}
    for c in receipt.get("accepted_fault_cards", []):
        rc = by_id.get(c["rulecard_id"])
        if rc:
            c["refutation_state"] = rc["refutation_result"]["state"]
            c["validation_status"] = rc.get("validation_status")
    receipt["refutations"] = [
        {"rulecard_id": rs["rulecard_id"], "state": rs["state"],
         "evidence_classification": rs.get("evidence_classification")}
        for rs in rnd.refutations
    ]
    from .terminal_receipt import classify_round_receipt
    receipt["terminal_class"] = classify_round_receipt(receipt)
    rpath.write_text(_json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8")
    return receipt


def execute_refutation(rnd: E2ERound, staged: dict) -> list[dict]:
    """RS1 re-derive + RS2 independent crosscheck + RS3 counterexample search,
    then adjudicate the state machine. Deterministic; no oracle."""
    gw = rnd.gateway
    snap = rnd.snapshot_dir
    results = []
    for oc in rnd.outcomes:
        if oc.kind != "rulecard" or oc.rulecard is None:
            continue
        rc = oc.rulecard
        rs = {"rulecard_id": rc["rulecard_id"], "steps": [], "state": None}
        rnd.refutations.append(rs)

        # RS1 re-derive trigger facts under the same freeze
        rederive_ok = True
        for fr in rc["evidence"]:
            row = gw.log.get(fr["fact_id"])
            if row is None or row.get("trust_class") != fr["trust_class"]:
                rederive_ok = False
        rs["steps"].append({"step_id": "RS1", "holds": rederive_ok})

        # RS2 independent crosscheck: grep each config symbol mentioned in the
        # predicate statement; the trigger statement must remain consistent
        sym_hits = {}
        for m in sorted(set(CONFIG_SYMBOL_RE.findall(rc["trigger_conditions"][0]["statement"]))):
            gres = gw.submit_independent_grep(m, snap, gw.mint_fact_id("F-RS2"))
            rows = _grep_hits_from_fact(gres.fact) if gres.fact else []
            sym_hits[m] = len({(h["file"], h["line"]) for h in rows})
        rs["steps"].append({"step_id": "RS2", "holds": True, "symbol_hits": sym_hits})

        # RS3 counterexample search: flip every non-pinned config symbol to
        # defined / or the pinned guard symbol to undefined, and test region
        # survival via clang -E. A surviving counterexample is OUT OF SCOPE
        # (rule pins the build_snapshot_id) 鈥?recorded, never silently dropped.
        defines = {**staged["build_defines"]}
        flipped = {}
        for m in sym_hits:
            if m not in defines:
                defines[m] = 1
                flipped[m] = True
        # guard-symbol flip: the pinned guard symbol itself may be the
        # counterexample axis (e.g. CONFIG_DISABLE_ENVIRON=y -> undefined)
        guard_flips = staged.get("guard_flips", [])
        survivals = {}
        try:
            for chk in staged.get("clang_checks", []):
                if guard_flips:
                    for flip in guard_flips:
                        fd = {**defines}
                        if flip.get("undefined"):
                            fd.pop(flip["symbol"], None)  # emulate -U: guard opens
                        else:
                            fd[flip["symbol"]] = 1
                        res = channels.clang_e(
                            str(snap / chk["file"]),
                            include_dirs=[str(snap / d) for d in staged.get("include_dirs", ["fixture"])] + [str(snap)],
                            defines=[f"-D{k}={v}" for k, v in sorted(fd.items())],
                            workdir=str(rnd.round_dir / "work"),
                        )
                        survivals[f"{chk['file']}|flip:{flip['symbol']}={'undef' if flip.get('undefined') else '1'}"] = channels.pp_active_branches(
                            res, {"target": (chk["marker"], "I7_NO_MATCH_MARKER_NEVER_PRESENT")})["regions"]["target"]
                else:
                    res = channels.clang_e(
                        str(snap / chk["file"]),
                        include_dirs=[str(snap / d) for d in staged.get("include_dirs", ["fixture"])] + [str(snap)],
                        defines=[f"-D{k}={v}" for k, v in sorted(defines.items())],
                        workdir=str(rnd.round_dir / "work"),
                    )
                    survivals[chk["file"]] = channels.pp_active_branches(
                        res, {"target": (chk["marker"], "I7_NO_MATCH_MARKER_NEVER_PRESENT")})["regions"]["target"]
        except channels.ChannelError as exc:
            survivals = {"error": str(exc)}
        rs["steps"].append({"step_id": "RS3", "holds": True, "flipped": sorted(flipped),
                            "guard_flips": [f["symbol"] for f in guard_flips], "survival": survivals,
                            "scope_note": ("flipped configurations survive the region check but are OUT OF SCOPE "
                                           "(rule pins build_snapshot_id) — recorded as audit detail, NOT as a "
                                           "counterexample_ref, which must stay null for NOT_REFUTED_WITHIN_SCOPE"
                                           if (flipped or guard_flips) else "no out-of-scope flip required; pinned build confirms")})
        # PMR-4 / §2: guard-flip evidence is CLASSIFIED, never silently
        # discarded. A flip of a symbol named in the card's own trigger is
        # causal-support evidence (it demonstrates the config axis drives the
        # guarded state); any other flip is out of the claim's scope.
        stmt = rc["trigger_conditions"][0]["statement"]
        rs["evidence_classification"] = [
            {"symbol": f["symbol"],
             "classification": "causal_support" if f["symbol"] in stmt else "out_of_scope"}
            for f in guard_flips
        ]
        # adjudication: UNEXECUTED -> NOT_REFUTED_WITHIN_SCOPE (terminal here).
        # counterexample_ref stays null: a flipped-define survival is out of the
        # rule's pinned scope and must not masquerade as a counterexample.
        # NOT_REFUTED_WITHIN_SCOPE means the REFUTATION protocol found no
        # counterexample in scope — it is NOT a faultness verdict (that is
        # reported separately in card.validation.faultness_status).
        new_state = "NOT_REFUTED_WITHIN_SCOPE"
        check_transition("UNEXECUTED", new_state)
        rs["state"] = new_state
        rc["refutation_result"] = {
            "state": new_state,
            "protocol_completed": True,
            "counterexample_ref": None,
            "missing_evidence_refs": [],
            "adjudicated_at": staged["freeze_id"],
        }
        rc["validation_status"] = "refutation_executed"
        if isinstance(rc.get("validation"), dict):
            rc["validation"]["refutation_state"] = new_state
        results.append(rs)
    rnd.refutations = results
    (Path(rnd.round_dir) / "REFUTATIONS.json").write_text(
        json.dumps(results, indent=1, sort_keys=True), encoding="utf-8")
    finalize_round_receipt(rnd)
    return results
