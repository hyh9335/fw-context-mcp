"""READ-ONLY evidence extraction for post-MVP external review.

Copies existing artifacts byte-for-byte (shutil.copy2 preserves mtime),
computes SHA-256, and writes MANIFEST entries. No artifact is edited,
normalized, or regenerated. Run from implementation/:  python extract_post_mvp.py
"""
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

IMPL = Path(__file__).resolve().parent
REPO = IMPL.parent
PKG = REPO / "post_mvp_extract"
if PKG.exists():
    shutil.rmtree(PKG)

manifest = []


def copy(src: Path, dst_rel: str, role: str) -> dict:
    dst = PKG / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)  # byte-for-byte, preserves mtime
    data = src.read_bytes()
    st = src.stat()
    entry = {
        "extracted_path": str(dst.relative_to(REPO)).replace("\\", "/"),
        "original_path": str(src.relative_to(REPO)).replace("\\", "/"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": st.st_size,
        "semantic_role": role,
        "original_mtime_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    manifest.append(entry)
    return entry


R = IMPL / "rounds"
W1 = IMPL / "outputs" / "wave1"

# ── Target A: C6 promoted RuleCard (+ supporting artifacts for split fields)
copy(R / "I7_C4C6_B" / "rulecards" / "RC-I7-B-LIB-GETCWD-107.json",
     "A_C6_RULECARD.json", "TARGET A: C6-twin promoted RuleCard, full JSON as persisted at solving time (embedded refutation_result.state=UNEXECUTED; executed state lives in per-round REFUTATIONS.json)")
copy(R / "I7_C4C6_B" / "REFUTATIONS.json",
     "A_C6_SUPPORTING/REFUTATIONS.json", "TARGET A/C6 semantic: per-round refutation ledger for RC-I7-B-LIB-GETCWD-107 (state NOT_REFUTED_WITHIN_SCOPE, RS1-RS3)")
copy(R / "I7_C4C6_B" / "SOLVING_SUMMARY.json",
     "A_C6_SUPPORTING/SOLVING_SUMMARY.json", "TARGET A/C6 semantic: scorer verdict (s1-s4, s5_verdict PASS, sampled_rows) + abstention row")

# ── Target B: C5 complete solving result (SOLVING_SUMMARY.json does NOT exist for this round)
copy(R / "I8_C5_T2_TRIPWIRE" / "I8_RECEIPT.json",
     "B_C5/I8_RECEIPT.json", "TARGET B: C5 round receipt / terminal-outcome ledger (tripwire_1 T2-only E3 refusal, tripwire_2 second-surface pass, no card promoted)")
copy(R / "I8_C5_T2_TRIPWIRE" / "fact_log.jsonl",
     "B_C5/fact_log.jsonl", "TARGET B: K1 fact ledger incl. F-0003 independent_grep T1 second-surface fact + refusal rows")
copy(R / "I8_C5_T2_TRIPWIRE" / "custody_log.jsonl",
     "B_C5/custody_log.jsonl", "TARGET B: custody events (freeze_verified, bundle_assembled)")
copy(R / "I8_C5_T2_TRIPWIRE" / "FREEZE_C5.json",
     "B_C5/FREEZE_C5.json", "TARGET B/C5 E2E count: freeze manifest that acceptance gate A1 counts")

# ── Target C: representative C1 RuleCard
copy(R / "I6_C1" / "rulecards" / "RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR.json",
     "C_C1_REPRESENTATIVE_RULECARD.json", "TARGET C: representative C1 RuleCard (fault_class=init_submit_pairing; envelope {hypothesis, rulecard})")

# ── Target D: C4 public-bug RuleCard
copy(R / "I7_C4C6_A" / "rulecards" / "RC-I7-A-API-GETCWD.json",
     "D_C4_RULECARD.json", "TARGET D: C4-twin absence RuleCard (getcwd compiled out under CONFIG_DISABLE_ENVIRON=y)")

# ── Target E: refutation artifacts
copy(R / "I6_C1" / "E2E_RECEIPT.json",
     "E_REFUTATIONS/C1/E2E_RECEIPT.json", "TARGET E: C1 refutation records (4 cards, RS1-RS3 each, NOT_REFUTED_WITHIN_SCOPE) + scores + outcomes; C1 predates per-round REFUTATIONS.json persistence")
copy(R / "I8_C5_T2_TRIPWIRE" / "I8_RECEIPT.json",
     "E_REFUTATIONS/C5/I8_RECEIPT.json", "TARGET E: C5 proof that no refutation executed (terminal outcome insufficient_evidence BEFORE RuleCard creation; byte-identical duplicate of B_C5/I8_RECEIPT.json)")

# ── Supporting artifacts cited by the explanation documents
copy(W1 / "WAVE1_ACCEPTANCE_RESULTS.json",
     "SUPPORTING/WAVE1_ACCEPTANCE_RESULTS.json", "SUPPORTING: top-level acceptance result (10 gates, 7 promoted cards, A1/A10 entries)")
copy(W1 / "REFUTATION_RESULTS.json",
     "SUPPORTING/REFUTATION_RESULTS.json", "SUPPORTING: top-level refutation aggregate as exposed to external review (3 entries)")
copy(R / "I7_C4C6_A" / "REFUTATIONS.json",
     "SUPPORTING/I7_C4C6_A_REFUTATIONS.json", "SUPPORTING: per-round refutation ledger, C4-twin (2 cards)")
copy(R / "I7_C4C6_A" / "SOLVING_SUMMARY.json",
     "SUPPORTING/I7_C4C6_A_SOLVING_SUMMARY.json", "SUPPORTING: C4-twin scorer verdicts")
copy(R / "I7_C4C6_SOLVER_RECEIPT.json",
     "SUPPORTING/I7_C4C6_SOLVER_RECEIPT.json", "SUPPORTING: I7 driver solver receipt (A/B outcome kinds, refutations, scores; no SHAs)")

# metadata sidecars for A and D
for tag, card_rel, meta_rel in (
    ("A", "A_C6_RULECARD.json", "A_C6_RULECARD_SOURCE.txt"),
    ("D", "D_C4_RULECARD.json", "D_C4_RULECARD_SOURCE.txt"),
):
    e = next(m for m in manifest if m["extracted_path"].endswith(card_rel))
    lines = [
        f"artifact: {card_rel}",
        f"original_path: {e['original_path']}",
        f"sha256: {e['sha256']}",
        f"size_bytes: {e['size_bytes']}",
        f"source_artifact_mtime_utc: {e['original_mtime_utc']}",
    ]
    (PKG / meta_rel).write_text("\n".join(lines) + "\n", encoding="utf-8")

(PKG / "MANIFEST.json").write_text(
    json.dumps({"package": "post_mvp_extract", "extraction": "READ-ONLY byte-for-byte copy; no artifacts edited or normalized",
                "artifacts": manifest}, indent=1, sort_keys=True), encoding="utf-8")
print(json.dumps({"copied": len(manifest),
                  "roles": [m["semantic_role"][:60] for m in manifest]}, indent=1))
