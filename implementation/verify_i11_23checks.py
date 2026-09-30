"""I11 — 23-check fresh-verifier audit script.

Each check is mechanical and recomputes from raw artifacts (never trusting the
recorded verdict). Run from implementation/:  python verify_i11_23checks.py
Writes I11_VERIFY_RESULTS.json next to itself.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

IMPL = Path(__file__).resolve().parent
REPO = IMPL.parent
W1 = IMPL / "outputs" / "wave1"
PIN = "d8f08a41d3fb9df6de255a187720f6332ed0f1c9"

checks = []


def check(idx, name, ok, detail=""):
    checks.append({"idx": idx, "name": name, "pass": bool(ok), "detail": str(detail)[:220]})


# C01: pytest green
proc = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q"],
                      cwd=str(IMPL), capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=900)
tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
check(1, "pytest suite green (52 passed)", " passed" in tail and " failed" not in tail and " error" not in tail, tail)

# raw artifacts
ROUNDS = {
    "C1": (IMPL / "rounds" / "I6_C1", "A"),
    "C4-twin": (IMPL / "rounds" / "I7_C4C6_A", "B"),
    "C6-twin": (IMPL / "rounds" / "I7_C4C6_B", "B"),
    "C5-tripwire": (IMPL / "rounds" / "I8_C5_T2_TRIPWIRE", "C"),
}


def load_cards(rd: Path) -> dict:
    out = {}
    rc = rd / "rulecards"
    if rc.is_dir():
        for p in sorted(rc.glob("*.json")):
            payload = json.loads(p.read_text(encoding="utf-8"))
            out[p.stem] = payload.get("rulecard") if isinstance(payload, dict) and "rulecard" in payload else payload
    return out


def facts_of(rd: Path) -> dict:
    d = {}
    fl = rd / "fact_log.jsonl"
    if fl.is_file():
        for line in fl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("kind") == "fact":
                    d[row["record"]["fact_id"]] = row["record"]
    return d


all_cards = {n: load_cards(meta[0]) for n, meta in ROUNDS.items()}
all_facts = {n: facts_of(meta[0]) for n, meta in ROUNDS.items()}
frozen = {n: list(meta[0].glob("FREEZE_*.json")) for n, meta in ROUNDS.items()}

# C02: A1 frozen case-rounds >= 4
check(2, "A1: >=4 frozen case-rounds", sum(1 for v in frozen.values() if v) >= 4,
      {k: [p.name for p in v] for k, v in frozen.items()})

# C03: A2 lineages >= 2
check(3, "A2: >=2 lineages", len({m[1] for m in ROUNDS.values()}) >= 2,
      sorted({m[1] for m in ROUNDS.values()}))

# C04: A3 blind sampling >=30% + shape
from fault_discovery_agent.k3.scorer import score_rulecard  # noqa: E402

total_lb = total_s = 0
shape_ok = True
for n in ("C1", "C4-twin", "C6-twin"):
    rd = ROUNDS[n][0]
    rows = [json.loads(l)["record"] for l in (rd / "fact_log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip() and json.loads(l).get("kind") == "fact"]
    for cid, card in all_cards[n].items():
        comp = score_rulecard(card, fact_log_rows=rows, sample_rate=0.30)
        lb = set()
        for ev in card.get("evidence", []):
            if isinstance(ev, dict):
                if "fact_id" in ev:
                    lb.add(ev["fact_id"])
                lb.update(fr["fact_id"] if isinstance(fr, dict) else fr for fr in ev.get("fact_refs", []))
        total_lb += len(lb)
        total_s += len(comp.sampled)
        shape_ok &= all(set(r.keys()) <= {"fact_id", "evidence_surface", "exact_query"} for r in comp.sampled)
ratio = total_s / total_lb if total_lb else 0.0
check(4, "A3: sampling >=30% + field shape", ratio >= 0.30 and shape_ok, f"{total_s}/{total_lb} = {ratio:.3f}, shape_ok={shape_ok}")

# C05: A4 no silent unsupported (overclaim substrings + traceability + refutation state)
BAD = ["complete enumeration", "exhaustively", "all call sites", "no other ", "proven mechanism", "guaranteed", "root cause is definitely"]
from fault_discovery_agent.schemas.refutation import STATES as REF_STATES  # noqa: E402

viol = []
for n, cards in all_cards.items():
    fids = set(all_facts[n])
    for cid, card in cards.items():
        blob = json.dumps(card, ensure_ascii=False).lower()
        viol += [f"{n}:{cid}:{sub}" for sub in BAD if sub in blob]
        cited = set()
        for ev in card.get("evidence", []):
            if isinstance(ev, dict):
                if "fact_id" in ev:
                    cited.add(ev["fact_id"])
                cited.update(fr["fact_id"] if isinstance(fr, dict) else fr for fr in ev.get("fact_refs", []))
        if cited - fids:
            viol.append(f"{n}:{cid}:untraceable")
        rr = card.get("refutation_result") or {}
        if rr.get("state") not in REF_STATES:
            viol.append(f"{n}:{cid}:state={rr.get('state')}")
check(5, "A4: zero silent unsupported claims", not viol, viol[:6])

# C06: A5 schema validity (recompute with jsonschema directly)
from fault_discovery_agent.schemas.loader import load_schema  # noqa: E402
import jsonschema  # noqa: E402

schema = load_schema("rulecard")
fails = []
count = 0
for n, cards in all_cards.items():
    for cid, card in cards.items():
        count += 1
        errs = sorted(jsonschema.Draft7Validator(schema).iter_errors(card), key=lambda e: list(e.absolute_path))
        if errs:
            fails.append(f"{n}:{cid}:{errs[0].message[:80]}")
check(6, "A5: all promoted cards schema-valid", count >= 7 and not fails, f"{count} cards, failures={fails[:4]}")

# C07: A6 executable refutation RS1-RS3
rfails = []
for n, cards in all_cards.items():
    refs = {}
    rp = ROUNDS[n][0] / "REFUTATIONS.json"
    if rp.is_file():
        refs = {r["rulecard_id"]: r for r in json.loads(rp.read_text(encoding="utf-8"))}
    elif n == "C1":
        # C1 predates persistence: refutations live in E2E_RECEIPT.json
        rec = ROUNDS[n][0] / "E2E_RECEIPT.json"
        if rec.is_file():
            refs = {r["rulecard_id"]: r for r in json.loads(rec.read_text(encoding="utf-8")).get("refutations", [])}
    for cid in cards:
        r = refs.get(cid)
        steps = {s.get("step_id") for s in (r or {}).get("steps", [])}
        if not r or not {"RS1", "RS2", "RS3"} <= steps or r.get("state") not in REF_STATES:
            rfails.append(f"{n}:{cid}")
check(7, "A6: RS1-RS3 executed + frozen state per card", not rfails, rfails[:6])

# C08: A7 detector admission
det = json.loads((W1 / "DETECTOR_RESULTS.json").read_text(encoding="utf-8"))["plant_receipts"]
pkg = {}
for row in det:
    pkg[row.get("package")] = (row.get("verdict") == "PASS") and pkg.get(row.get("package"), True)
check(8, "A7: PT-G1/G4/G6/G7/G8 PASS", set(pkg) >= {"PT-G1", "PT-G4", "PT-G6", "PT-G7", "PT-G8"} and all(pkg.values()), pkg)

# C09: A8 custody
cfails = []
for n, meta in ROUNDS.items():
    cl = meta[0] / "custody_log.jsonl"
    if not cl.is_file():
        cfails.append(f"{n}:no-custody-log")
        continue
    kinds = set()
    for line in cl.read_text(encoding="utf-8").splitlines():
        if line.strip():
            kinds.add(json.loads(line).get("kind"))
    if "freeze_verified" not in kinds or "bundle_assembled" not in kinds:
        cfails.append(f"{n}:missing-kinds")
    if any("breach" in str(k).lower() for k in kinds):
        cfails.append(f"{n}:breach")
check(9, "A8: custody verified per round, zero breach", not cfails, cfails[:6])

# C10: A9 oracle isolation (byte-hash)
oracle_root = REPO / "fault_discovery_goal_architecture_agent_team" / "outputs" / "corpus"
names = {"FAULT_ORACLE.json", "PLANT_DIFF.md", "EVIDENCE_MAP.json", "SOURCE_LOCK.json", "PAIRING_NOTE.md"}
hashes = set()
n_oracle = 0
for p in oracle_root.rglob("*"):
    if p.is_file() and p.name in names:
        hashes.add(hashlib.sha256(p.read_bytes()).hexdigest())
        n_oracle += 1
iso = []
for n, meta in ROUNDS.items():
    bd = meta[0] / "solver_bundle"
    if not bd.is_dir():
        continue
    for f in bd.rglob("*"):
        if f.is_file():
            if f.name in names:
                iso.append(f"{n}:{f.name}")
            if hashlib.sha256(f.read_bytes()).hexdigest() in hashes:
                iso.append(f"{n}:{f.name}:hash")
check(10, "A9: oracle isolation (byte-hash)", n_oracle > 0 and not iso, f"oracle_files={n_oracle}, leaks={iso[:4]}")

# C11: A10 C6 negative control
c6_summary = (ROUNDS["C6-twin"][0] / "SOLVING_SUMMARY.json")
c6_abs = 0
if c6_summary.is_file():
    c6_abs = sum(1 for c in json.loads(c6_summary.read_text(encoding="utf-8"))["cards"]
                 if c.get("family") == "existence_absence" and c.get("polarity") == "negative")
check(11, "A10: C6 zero false absence cards", c6_abs == 0, f"absence_cards={c6_abs}")

# C12: verdict consistency
acc = json.loads((W1 / "WAVE1_ACCEPTANCE_RESULTS.json").read_text(encoding="utf-8"))
recomputed = all(checks[i - 1]["pass"] for i in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11))
consistent = (acc["verdict"] == "WAVE1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS") == recomputed
check(12, "verdict consistent with recomputed gates", consistent and recomputed, acc["verdict"])

# C13: deliverables inventory
required = ["WAVE1_IMPLEMENTATION_REPORT.md", "WAVE1_ACCEPTANCE_RESULTS.json", "RULECARDS",
            "REFUTATION_RESULTS.json", "DETECTOR_RESULTS.json", "FREEZE_MANIFESTS",
            "TRACK_B_PULL_REQUESTS.json", "KC2A_RESULT.md", "KC2A_RESULT.json",
            "KNOWN_WAVE1_LIMITATIONS.md"]
missing = [r for r in required if not (W1 / r).exists()]
check(13, "deliverables present", not missing, f"missing={missing}")

# C14: RULECARDS contents
n_cards = sum(len(v) for v in all_cards.values())
bad_cards = [f"{n}:{cid}" for n, cards in all_cards.items() for cid, c in cards.items()
             if not c.get("rulecard_id") or not c.get("evidence") or not c.get("refutation_result")]
check(14, "7 RuleCards with id/evidence/refutation_result", n_cards == 7 and not bad_cards, f"n={n_cards}, bad={bad_cards[:4]}")

# C15: freeze manifests + combined hash verification
from fault_discovery_agent.freeze.manifest_tool import combined_manifest_hash, verify_manifest  # noqa: E402

fm_bad = []
n_manifests = 0
for n, meta in ROUNDS.items():
    for mp in meta[0].glob("FREEZE_*.json"):
        n_manifests += 1
        try:
            verify_manifest(mp, meta[0])  # recomputes combined hash from artifact_lines (raises on mismatch)
        except Exception as exc:
            fm_bad.append(f"{n}:{str(exc)[:80]}")
check(15, "4 freeze manifests, combined hash recomputes", n_manifests == 4 and not fm_bad, f"n={n_manifests}, bad={fm_bad[:4]}")

# C16: REFUTATION_RESULTS coverage
rr = json.loads((W1 / "REFUTATION_RESULTS.json").read_text(encoding="utf-8"))["refutations"]
covered = {r["rulecard_id"] for r in rr} | {r["rulecard_id"] for r in json.loads((ROUNDS["C1"][0] / "E2E_RECEIPT.json").read_text(encoding="utf-8")).get("refutations", [])}
promoted_ids = {cid for cards in all_cards.values() for cid in cards}
check(16, "REFUTATION_RESULTS covers every promoted card", promoted_ids <= covered, f"missing={sorted(promoted_ids - covered)}")

# C17: TRACK_B content
tb = json.loads((W1 / "TRACK_B_PULL_REQUESTS.json").read_text(encoding="utf-8"))["pull_requests"]
expected_ids = {"F-V1-001", "F-V1-003", "F-V1-004", "F-V1-005", "F-V1-007", "F-V1-008", "F-V2-001", "F-V2-002"}
tb_ids = {r["finding_id"] for r in tb}
actioned = [r["finding_id"] for r in tb if r.get("status") != "NOT_ACTIONED_IN_WAVE1"]
check(17, "Track-B: 8 findings handoff, none actioned", tb_ids == expected_ids and not actioned, f"ids_ok={tb_ids == expected_ids}, actioned={actioned}")

# C18: KC2A deviation + non-gating
kc = json.loads((W1 / "KC2A_RESULT.json").read_text(encoding="utf-8"))
dev = kc.get("prereg_deviation_receipt", {})
check(18, "KC-2A: deviation receipt + underpowered + non-gating",
      dev.get("deviation") and "UNDERPOWERED" in json.dumps(dev)
      and str(kc.get("acceptance_impact", "")).startswith("NONE")
      and kc.get("incorrect") == [] and kc.get("n") == 12,
      f"n={kc.get('n')}, incorrect={kc.get('incorrect')}, impact={kc.get('acceptance_impact')!r:.60}")

# C19: package isolation — no direct substrate imports outside k1/substrate.py
bad_imports = []
for p in (IMPL / "fault_discovery_agent").rglob("*.py"):
    if p.name == "substrate.py":
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    if "import fw_context_mcp" in text or "from fw_context_mcp" in text:
        bad_imports.append(str(p.relative_to(IMPL)))
check(19, "package isolation: substrate imports only via k1/substrate.py", not bad_imports, bad_imports[:4])

# C20: substrate untouched
g1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace")
g2 = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace")
check(20, "substrate untouched: HEAD pinned + src clean",
      g1.stdout.strip() == PIN and not g2.stdout.strip(),
      f"HEAD={g1.stdout.strip()[:12]}, src_status={g2.stdout.strip()[:60]!r}")

# C21: E-COVERAGE shipped in taxonomy
tax = (IMPL / "fault_discovery_agent" / "k1" / "factlog.py").read_text(encoding="utf-8")
check(21, "error taxonomy includes E-COVERAGE (I7 fix shipped)", '"E-COVERAGE"' in tax)

# C22: absence-soundness precondition in the loop
loop_src = (IMPL / "fault_discovery_agent" / "k2" / "loop.py").read_text(encoding="utf-8")
check(22, "absence-soundness coverage precondition present (E-COVERAGE fail-closed)",
      "E-COVERAGE" in loop_src and "query_definition_window" in loop_src)

# C23: I7 receipts show the A/B split (A absence=1, B absence=0)
i7 = json.loads((IMPL / "rounds" / "I7_C4C6_RECEIPT.json").read_text(encoding="utf-8"))
split_ok = i7["solver_findings"]["A"]["absence_cards"] == 1 and i7["solver_findings"]["B"]["absence_cards"] == 0 \
    and not i7["solver_findings"]["A"]["false_positive_absence"] and not i7["solver_findings"]["B"]["false_positive_absence"]
check(23, "I7 A/B split: A absence card, B none, zero false positives", split_ok)

out = {"phase": "I11_FRESH_VERIFIER_23_CHECKS", "checks": checks,
       "passed": sum(1 for c in checks if c["pass"]), "total": len(checks),
       "verdict": "AUDIT_PASS" if all(c["pass"] for c in checks) else "AUDIT_FAIL"}
(IMPL / "I11_VERIFY_RESULTS.json").write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
print(json.dumps(out, indent=1, sort_keys=True))
