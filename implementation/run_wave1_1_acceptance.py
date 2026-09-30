"""Wave-1.1 acceptance runner (post semantic-repair revalidation).

Executable gates G1..G14 (each derived from ACTUAL artifacts; fail-closed:
a missing artifact is a FAIL, never a vacuous pass). Emits:
  outputs/wave1_1/WAVE1_1_ACCEPTANCE_RESULTS.json
  outputs/wave1_1/REFUTATION_RESULTS_V2.json   (normalized exactly-once aggregate)
  outputs/wave1_1/NEGATIVE_CONTROL_REPORT.json (family-agnostic, ALL accepted cards)

Verdicts (exact enum): WAVE1_1_MVP_ACCEPTED |
WAVE1_1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS | WAVE1_1_MVP_NOT_READY |
WAVE1_1_MEASUREMENT_INVALID.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

IMPL = Path(__file__).resolve().parent
sys.path.insert(0, str(IMPL))

from fault_discovery_agent.k2.statement_render import validate_semantic_coherence  # noqa: E402
from fault_discovery_agent.k3.scorer import score_rulecard  # noqa: E402
from fault_discovery_agent.k3.terminal_receipt import classify_round_receipt  # noqa: E402
from fault_discovery_agent.schemas.loader import validate  # noqa: E402

REPO = IMPL.parent
W11 = IMPL / "rounds" / "wave1_1"
OUT = IMPL / "outputs" / "wave1_1"
OUT.mkdir(parents=True, exist_ok=True)
SUBSTRATE_PIN = "d8f08a41d3fb9df6de255a187720f6332ed0f1c9"

# mechanism modules that must stay case-agnostic (no case-specific tokens in
# executable code); drivers legitimately name case parameters and are excluded
CASE_AGNOSTIC_MODULES = [
    IMPL / "fault_discovery_agent" / "k2" / "faultness_gate.py",
    IMPL / "fault_discovery_agent" / "k2" / "statement_render.py",
    IMPL / "fault_discovery_agent" / "k3" / "terminal_receipt.py",
]
CASE_TOKEN_RE = re.compile(
    r"getcwd|accel|sensor_submit|sensor_module|link_ready|k_work_submit|work_submit"
    r"|CONFIG_(DISABLE_ENVIRON|ACCEL_[A-Z_]+|SENSOR_[A-Z_]+)"
    r"|\bC[1-7]\b|\bI[6-9]\b",
    re.IGNORECASE)


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _fact_rows(round_dir: Path) -> list[dict]:
    rows = []
    log = round_dir / "fact_log.jsonl"
    if not log.is_file():
        return rows
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rec = row.get("record", row)
        if isinstance(rec, dict) and rec.get("fact_id"):
            rows.append(rec)
    return rows


def _accepted_cards(round_dir: Path) -> list[dict]:
    cards, _ = _load_round_cards(round_dir)
    return cards


def _load_round_cards(round_dir: Path) -> tuple[list[dict], list[dict]]:
    """Load a round's rulecard files FAIL-CLOSED (audit V5 repair).

    Returns (cards, issues). A missing rulecards directory or an unparseable
    card file is an ISSUE, never a silent empty set: gates that consume cards
    must fail when a receipt claims accepted cards that cannot be loaded and
    verified from disk."""
    rd = round_dir / "rulecards"
    issues: list[dict] = []
    if not rd.is_dir():
        issues.append({"error": "rulecards directory missing", "dir": str(rd)})
        return [], issues
    cards: list[dict] = []
    for f in sorted(rd.glob("*.json")):
        card = _load(f)
        if card is None:
            issues.append({"error": "unparseable card file", "file": f.name})
            continue
        cards.append(card)
    return cards, issues


def reconcile_cards(receipt_accepted: list[dict], disk_cards: list[dict], issues: list[dict]) -> tuple[bool, dict]:
    """Receipt-vs-disk card reconciliation (audit V5 repair).

    Fatal when: any load issue on a round whose receipt claims accepted
    cards; an unparseable card file; or receipt-claimed ids != disk ids in
    EITHER direction (claims without disk evidence, or disk cards the
    receipt never claimed)."""
    claimed = {c.get("rulecard_id") for c in (receipt_accepted or [])}
    disk = {c.get("rulecard_id") for c in (disk_cards or [])}
    fatal_issues = [i for i in (issues or []) if claimed or i.get("error") == "unparseable card file"]
    detail = {
        "claimed_by_receipt": sorted(x for x in claimed if x),
        "found_on_disk": sorted(x for x in disk if x),
        "claimed_missing_on_disk": sorted(claimed - disk),
        "on_disk_unclaimed": sorted(disk - claimed),
        "load_issues": issues or [],
    }
    ok = not fatal_issues and claimed == disk
    return ok, detail


def gate_result(gid: str, name: str, passed: bool, detail: dict) -> dict:
    return {"gate": gid, "name": name, "pass": bool(passed), "detail": detail}


def main() -> None:
    gates: list[dict] = []
    rounds: dict[str, dict] = {}

    # ── load receipts (fail-closed: missing/unreadable = not_e2e) ──────────
    index = _load(W11 / "CORPUS_RECEIPTS_INDEX.json")
    if not index:
        gates.append(gate_result("G1", "e2e_case_rounds", False, {"error": "CORPUS_RECEIPTS_INDEX.json missing/unreadable"}))
    else:
        for entry in index.get("rounds", []):
            rd = Path(entry["round_dir"])
            receipt = _load(rd / "ROUND_RECEIPT.json")
            if receipt is None:
                receipt = {"case_id": entry.get("case_id"), "terminal_class": "not_e2e", "_missing": True}
            rounds[entry.get("case_id", "?")] = {"round_dir": rd, "receipt": receipt}

    # card discovery is FAIL-CLOSED (audit V5 repair): a round whose receipt
    # claims accepted cards must have them loadable from disk; an unparseable
    # card file is always fatal
    cards_by_round: dict[str, tuple[list[dict], list[dict]]] = {
        cid: _load_round_cards(r["round_dir"]) for cid, r in rounds.items()
    }

    def _recon(cid: str) -> tuple[bool, dict]:
        cards, issues = cards_by_round[cid]
        return reconcile_cards(rounds[cid]["receipt"].get("accepted_fault_cards", []), cards, issues)

    # G1: >=4 genuine E2E case-rounds by terminal receipts
    e2e = {cid: r for cid, r in rounds.items()
           if classify_round_receipt(r["receipt"]) in ("e2e_positive", "e2e_negative")}
    gates.append(gate_result("G1", "e2e_case_rounds>=4", len(e2e) >= 4, {
        "count": len(e2e),
        "rounds": {cid: classify_round_receipt(r["receipt"]) for cid, r in rounds.items()},
        "criterion": "receipt-based (traversal + accepted/rejected terminals), not freeze-manifest-based",
    }))

    # G2: C1 correct
    c1 = rounds.get("C1", {})
    c1_cls = classify_round_receipt(c1.get("receipt", {}))
    c1_cards = cards_by_round.get("C1", ([], []))[0]
    c1_recon_ok, c1_recon = _recon("C1") if c1 else (False, {"error": "C1 round missing"})
    c1_coherent = all(not validate_semantic_coherence(c, fact_rows=_fact_rows(c1.get("round_dir", Path(".")))) for c in c1_cards)
    c1_scoring = all(not score_rulecard(c, fact_log_rows=_fact_rows(c1.get("round_dir", Path("."))), sample_rate=0.30).overclaims for c in c1_cards)
    gates.append(gate_result("G2", "C1_correct", c1_cls == "e2e_positive" and bool(c1_cards) and c1_recon_ok and c1_coherent and c1_scoring, {
        "terminal_class": c1_cls, "accepted": [c["rulecard_id"] for c in c1_cards],
        "coherent": c1_coherent, "scoring_clean": c1_scoring, "reconciliation": c1_recon,
    }))

    # G3: C4 correct (real NuttX pre-fix twin discovers getcwd absence + region)
    ca = rounds.get("C4C6_A", {})
    ca_cls = classify_round_receipt(ca.get("receipt", {}))
    ca_cards = cards_by_round.get("C4C6_A", ([], []))[0]
    ca_recon_ok, ca_recon = _recon("C4C6_A") if ca else (False, {"error": "C4C6_A round missing"})
    ca_rows = _fact_rows(ca.get("round_dir", Path(".")))
    absence_card = next((c for c in ca_cards
                         if c["fault_class"] == "existence_absence" and c["polarity"] == "negative"
                         and "has no compiled definition" in c["trigger_conditions"][0]["statement"]), None)
    ca_coherent = all(not validate_semantic_coherence(c, fact_rows=ca_rows) for c in ca_cards)
    gates.append(gate_result("G3", "C4_correct", ca_cls == "e2e_positive" and bool(absence_card) and ca_recon_ok and ca_coherent, {
        "terminal_class": ca_cls,
        "accepted": [c["rulecard_id"] for c in ca_cards],
        "absence_card_statement": absence_card["trigger_conditions"][0]["statement"] if absence_card else None,
        "coherent": ca_coherent, "reconciliation": ca_recon,
    }))

    # G4: C6 zero accepted fault RuleCards; benign conditional retained as
    # evidence-grade rejection (first-class, named roles)
    cb = rounds.get("C4C6_B", {})
    cb_receipt = cb.get("receipt", {})
    cb_cls = classify_round_receipt(cb_receipt)
    cb_rejected = cb_receipt.get("rejected_candidates", [])
    gates.append(gate_result("G4", "C6_zero_accepted_fault_cards", cb_cls == "e2e_negative" and not cb_receipt.get("accepted_fault_cards") and bool(cb_rejected), {
        "terminal_class": cb_cls,
        "accepted": cb_receipt.get("accepted_fault_cards", []),
        "rejected_candidates": cb_rejected,
        "note": "the benign conditional is demoted by the family-agnostic Faultness Gate (no case-specific logic)",
    }))

    # G5: T2-touching scenario exercised (C5 safety-only, quarantine discipline)
    c5 = rounds.get("C5", {})
    c5_receipt = c5.get("receipt", {})
    qp = c5_receipt.get("quarantine_proof", {})
    gates.append(gate_result("G5", "t2_scenario_exercised", bool(c5_receipt.get("t2_scenarios")) and bool(qp) and all(qp.values()), {
        "terminal_class": classify_round_receipt(c5_receipt),
        "t2_scenarios": c5_receipt.get("t2_scenarios", []),
        "quarantine_proof": qp,
        "accepted_fault_cards": c5_receipt.get("accepted_fault_cards", []),
    }))

    # G6: detector plants + full suite green (run the actual suite now)
    suite = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "--tb=no"],
                           cwd=IMPL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    m = re.search(r"(\d+) passed", suite.stdout)
    suite_tail = (suite.stdout or suite.stderr).strip().splitlines()[-1:] [0] if (suite.stdout or suite.stderr).strip() else ""
    gates.append(gate_result("G6", "detector_plants_and_suite_green", suite.returncode == 0, {
        "exit_code": suite.returncode, "summary": suite_tail[:160],
        "scope": "full pytest suite incl. detector plants G1/G4/G6/G7/G8",
    }))

    # G7: semantic coherence PASS for EVERY accepted card (all rounds);
    # fail-closed on card-discovery issues (audit V5 repair)
    coherence_failures = []
    all_cards: list[tuple[str, Path, dict]] = []
    for cid, r in rounds.items():
        rows = _fact_rows(r["round_dir"])
        for card in cards_by_round[cid][0]:
            all_cards.append((cid, r["round_dir"], card))
            errs = validate_semantic_coherence(card, fact_rows=rows)
            if errs:
                coherence_failures.append({"round": cid, "rulecard_id": card["rulecard_id"], "errors": errs[:3]})
        recon_ok, recon = _recon(cid)
        if not recon_ok:
            coherence_failures.append({"round": cid, "error": "card reconciliation failed", "reconciliation": recon})
    gates.append(gate_result("G7", "semantic_coherence_all_accepted", not coherence_failures, {
        "cards_checked": len(all_cards), "failures": coherence_failures,
    }))

    # G8: Faultness Gate PASS recorded for every accepted card
    faultness_failures = []
    for cid, r in rounds.items():
        fr = _load(r["round_dir"] / "FAULTNESS_RESULTS.json") or {}
        passed_ids = {x["rulecard_id"] for x in fr.get("results", []) if x.get("passed")}
        for card in cards_by_round[cid][0]:
            v = card.get("validation") or {}
            if v.get("faultness_status") != "PASS" or card["rulecard_id"] not in passed_ids:
                faultness_failures.append({"round": cid, "rulecard_id": card["rulecard_id"],
                                           "faultness_status": v.get("faultness_status")})
    gates.append(gate_result("G8", "faultness_gate_all_accepted", not faultness_failures, {
        "cards_checked": len(all_cards), "failures": faultness_failures,
    }))

    # G9: every accepted card carries the separate validation object (schema v1.1);
    # fail-closed on card-discovery issues (audit V5 repair)
    validation_failures = []
    for cid, r in rounds.items():
        for card in cards_by_round[cid][0]:
            try:
                validate(card, "rulecard_v1_1")
            except Exception as exc:  # noqa: BLE001
                validation_failures.append({"round": cid, "rulecard_id": card.get("rulecard_id"), "error": str(exc)[:160]})
        recon_ok, recon = _recon(cid)
        if not recon_ok:
            validation_failures.append({"round": cid, "error": "card reconciliation failed", "reconciliation": recon})
    gates.append(gate_result("G9", "separate_validation_axes", not validation_failures, {
        "cards_checked": len(all_cards), "failures": validation_failures,
        "axes": ["evidence_integrity_status", "faultness_status", "refutation_state"],
    }))

    # G10: family-agnostic negative control — mechanism modules must contain
    # no case-specific tokens in executable code (docstrings/comments stripped)
    hardcoding_hits = []
    for mod in CASE_AGNOSTIC_MODULES:
        src = mod.read_text(encoding="utf-8")
        # strip block comments/docstrings, then line comments
        stripped = re.sub(r'"""[\s\S]*?"""', "", src)
        stripped = re.sub(r"#.*", "", stripped)
        for mline in CASE_TOKEN_RE.finditer(stripped):
            line_no = stripped[:mline.start()].count("\n") + 1
            hardcoding_hits.append({"module": mod.name, "line": line_no, "token": mline.group(0)})
    # the negative control is applied to ALL accepted cards (family-agnostic):
    # build pinning is verified on the card's CITED FACTS (the frozen
    # scope_stamp is closed and carries no build field by design)
    nc_records = []
    for cid, r in rounds.items():
        rows = _fact_rows(r["round_dir"])
        by_id = {x["fact_id"]: x for x in rows}
        build_ids = {x.get("build_snapshot_id") for x in rows if x.get("build_snapshot_id")}
        for card in cards_by_round[cid][0]:
            cited = [fr.get("fact_id") for fr in (card["trigger_conditions"][0].get("fact_refs") or [])]
            cited_builds = {by_id[f].get("build_snapshot_id") for f in cited if f in by_id}
            nc_records.append({
                "round": cid,
                "rulecard_id": card["rulecard_id"],
                "family": card["fault_class"],
                "checks": {
                    "forbidden_overclaims_nonempty": bool(card.get("forbidden_overclaims")),
                    "cited_facts_exist_and_pinned": bool(cited) and all(f in by_id for f in cited)
                    and all(b in build_ids for b in cited_builds),
                    "no_oracle_paths_in_evidence": not any("oracle/" in json.dumps(e) for e in card.get("evidence", [])),
                    "statement_structurally_coherent": not validate_semantic_coherence(card),
                },
            })
    nc_all_pass = all(all(v for v in r["checks"].values()) for r in nc_records) and nc_records
    gates.append(gate_result("G10", "family_agnostic_negative_control", nc_all_pass and not hardcoding_hits, {
        "cards_controlled": len(nc_records),
        "case_specific_tokens_in_mechanism_modules": hardcoding_hits,
        "per_card_failures": [r for r in nc_records if not all(r["checks"].values())],
    }))

    # G11: no silent unsupported claims (blind scorer re-run, all accepted);
    # fail-closed on card-discovery issues (audit V5 repair)
    claim_failures = []
    for cid, r in rounds.items():
        rows = _fact_rows(r["round_dir"])
        for card in cards_by_round[cid][0]:
            comp = score_rulecard(card, fact_log_rows=rows, sample_rate=0.30)
            if comp.overclaims or comp.s5_verdict != "PASS":
                claim_failures.append({"round": cid, "rulecard_id": card["rulecard_id"],
                                       "overclaims": comp.overclaims, "verdict": comp.s5_verdict})
        recon_ok, recon = _recon(cid)
        if not recon_ok:
            claim_failures.append({"round": cid, "error": "card reconciliation failed", "reconciliation": recon})
    gates.append(gate_result("G11", "no_silent_unsupported_claims", not claim_failures, {
        "cards_checked": len(all_cards), "failures": claim_failures,
    }))

    # G12: no oracle leak (custody logs + solver bundles across all rounds)
    leak_failures = []
    for cid, r in rounds.items():
        clog = r["round_dir"] / "custody_log.jsonl"
        if not clog.is_file():
            leak_failures.append({"round": cid, "error": "custody_log.jsonl missing"})
            continue
        for line in clog.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == "freeze_breach":
                leak_failures.append({"round": cid, "breach": row.get("detail")})
        bundle = r["round_dir"] / "solver_bundle"
        if bundle.is_dir():
            for f in bundle.rglob("*"):
                if f.is_file() and "oracle" in f.as_posix().lower():
                    leak_failures.append({"round": cid, "oracle_in_bundle": f.name})
    gates.append(gate_result("G12", "no_oracle_leak", not leak_failures, {"failures": leak_failures}))

    # G13: substrate untouched
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    status = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    gates.append(gate_result("G13", "substrate_untouched",
                             head.stdout.strip() == SUBSTRATE_PIN and not status.stdout.strip(), {
                                 "head": head.stdout.strip(),
                                 "expected": SUBSTRATE_PIN,
                                 "src_status": status.stdout.strip()[:200],
                             }))

    # ── REFUTATION_RESULTS_V2: normalized exactly-once aggregate ───────────
    records = []
    for cid, r in rounds.items():
        refs = _load(r["round_dir"] / "REFUTATIONS.json")
        rows = refs if isinstance(refs, list) else (refs or {}).get("refutations", [])
        cards = {c["rulecard_id"]: c for c in cards_by_round[cid][0]}
        for rs in rows:
            card = cards.get(rs.get("rulecard_id"), {})
            v = card.get("validation") or {}
            records.append({
                "source_artifact_path": str((r["round_dir"] / "REFUTATIONS.json").relative_to(REPO)),
                "round": cid,
                "rulecard_id": rs.get("rulecard_id"),
                "final_state": rs.get("state"),
                "evidence_integrity_status": v.get("evidence_integrity_status", "UNRECORDED"),
                "faultness_status": v.get("faultness_status", "UNRECORDED"),
                "family": card.get("fault_class"),
                "historical": False,
            })
        # demoted candidates: explicit REJECTED terminals (not refuted — the
        # gate refused them BEFORE refutation; recorded so coverage is total)
        for d in (r["receipt"].get("rejected_candidates") or []):
            records.append({
                "source_artifact_path": str((r["round_dir"] / "FAULTNESS_RESULTS.json").relative_to(REPO)),
                "round": cid,
                "rulecard_id": d.get("rulecard_id"),
                "final_state": "REJECTED_FAULTNESS_GATE",
                "evidence_integrity_status": "PASS",
                "faultness_status": "FAIL",
                "family": d.get("family"),
                "historical": False,
            })
    # historical wave-1 records (unchanged receipts; motivating evidence only)
    hist_sources = [
        (IMPL / "rounds" / "I6_C1" / "E2E_RECEIPT.json", "C1 (wave-1)"),
        (IMPL / "rounds" / "I7_C4C6_A" / "REFUTATIONS.json", "C4-twin (wave-1)"),
        (IMPL / "rounds" / "I7_C4C6_B" / "REFUTATIONS.json", "C6-twin (wave-1)"),
    ]
    for path, label in hist_sources:
        data = _load(path)
        if data is None:
            continue
        rows = data if isinstance(data, list) else data.get("refutations", [])
        for rs in rows:
            records.append({
                "source_artifact_path": str(path.relative_to(REPO)),
                "round": label,
                "rulecard_id": rs.get("rulecard_id"),
                "final_state": rs.get("state"),
                "evidence_integrity_status": "PASS_HISTORICAL",
                "faultness_status": "NOT_APPLICABLE_HISTORICAL",
                "family": None,
                "historical": True,
                "note": "produced before the wave1-1 semantic repair; retained as motivating evidence, NOT as current validation",
            })
    seen: dict[tuple, int] = {}
    dupes = []
    for rec in records:
        key = (rec["source_artifact_path"], rec["rulecard_id"])
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            dupes.append(key)
    current_promoted = {c["rulecard_id"] for _, _, c in all_cards}
    covered = {rec["rulecard_id"] for rec in records if not rec["historical"]}
    gates.append(gate_result("G14", "normalized_aggregate_exactly_once",
                             not dupes and current_promoted <= covered, {
                                 "records": len(records),
                                 "historical_records": sum(1 for r in records if r["historical"]),
                                 "duplicates": dupes,
                                 "promoted_cards_missing_from_aggregate": sorted(current_promoted - covered),
                             }))

    # G15: receipt-vs-disk card reconciliation (audit V5 repair) — explicit
    # gate so the verdict is unconditionally trustworthy
    recon_failures = []
    for cid in rounds:
        ok, detail = _recon(cid)
        if not ok:
            recon_failures.append({"round": cid, **detail})
    gates.append(gate_result("G15", "receipt_vs_disk_card_reconciliation", not recon_failures, {
        "rounds_checked": len(rounds),
        "failures": recon_failures,
        "rule": "receipt-claimed accepted ids == disk card ids (both directions); unparseable card files always fatal; missing rulecards dir fatal whenever the receipt claims accepted cards",
    }))
    v2 = {
        "phase": "WAVE1_1_NORMALIZED_REFUTATION_AGGREGATE",
        "schema_note": "every promoted card exactly once: source_artifact_path, rulecard_id, final_state, evidence_integrity_status, faultness_status; historical wave-1 records included with historical=true (unchanged receipts)",
        "records": records,
        "coverage": {
            "records": len(records),
            "duplicates": len(dupes),
            "promoted_cards_current": len(current_promoted),
            "exactly_once": not dupes and current_promoted <= covered,
        },
    }
    (OUT / "REFUTATION_RESULTS_V2.json").write_text(json.dumps(v2, indent=1, sort_keys=True), encoding="utf-8")

    # negative-control report
    (OUT / "NEGATIVE_CONTROL_REPORT.json").write_text(json.dumps({
        "phase": "WAVE1_1_NEGATIVE_CONTROL",
        "scope": "ALL accepted fault cards across ALL rounds (family-agnostic); mechanism modules scanned for case-specific hardcoding",
        "cards": nc_records,
        "mechanism_module_scan": {"modules": [m.name for m in CASE_AGNOSTIC_MODULES], "hits": hardcoding_hits},
        "pass": nc_all_pass and not hardcoding_hits,
    }, indent=1, sort_keys=True), encoding="utf-8")

    all_pass = all(g["pass"] for g in gates)
    results = {
        "phase": "WAVE1_1_SEMANTIC_CORRECTNESS_REVALIDATION",
        "verdict": "WAVE1_1_MVP_ACCEPTED" if all_pass else "WAVE1_1_MVP_NOT_READY",
        "e2e_count_basis": "terminal execution receipts (ROUND_RECEIPT.json + classify_round_receipt), freeze manifests never counted",
        "rounds": {cid: {"round_dir": str(r["round_dir"]),
                         "terminal_class": classify_round_receipt(r["receipt"]),
                         "accepted_fault_cards": [c["rulecard_id"] for c in _accepted_cards(r["round_dir"])]}
                   for cid, r in rounds.items()},
        "gates": gates,
    }
    (OUT / "WAVE1_1_ACCEPTANCE_RESULTS.json").write_text(json.dumps(results, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"verdict": results["verdict"],
                      "gates": {g["gate"]: g["pass"] for g in gates}}, indent=1))


if __name__ == "__main__":
    main()
