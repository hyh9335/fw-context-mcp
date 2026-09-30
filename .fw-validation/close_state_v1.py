import json
import io

path = r".fw-validation/state.json"
d = json.load(io.open(path, encoding="utf-8"))
d["phases"]["V1_GOLDEN"] = "CONTROLLER_COMPLETE_AWAITING_VERIFIER"
d["verifier_signoffs"]["V1_GOLDEN"] = {
    "verdict": "V1_GOLDEN PASS_WITH_NOTES",
    "verifier": "independent subagent (session-0f41ec5a-33b7-4cf0-aff6-2e2df649686a child)",
    "report": ".fw-validation/artifacts/V1/verifier/verifier_report.md",
    "raw_evidence": ".fw-validation/artifacts/V1/verifier/raw/<case>/",
    "method": "fresh work dirs + own extraction code + real CLI/stdio MCP; one rerun per "
              "semantic family (G01, G03, G06, G10, G12) + full read-only artifact/finding audit",
    "rerun_agreement": "all 5 rerun families AGREE with controller classifications; "
                       "F-V1-001..006 all AGREE (DB/product-source claims independently confirmed)",
    "g13_closure": "G13 was PENDING at verifier audit time; verifier pre-approved re-derivation "
                   "and observed the invariant holding at all 5 sizes. Controller closed it with "
                   "daemon-disciplined requeries + exact-phrase probes (G13.result.json v5): PASS "
                   "at 1/10/50/100/250. Two post-audit findings (F-V1-007 single-line empty stored "
                   "bodies, F-V1-008 100-row cap) recorded with DB-level evidence; carried to V5.",
    "notes_adopted": ["Windows-side raw G11 relabeled BLOCKED_ENVIRONMENT (wsl PASS canonical)",
                      "F-V1-005 wording nit (main status='dead' L1) recorded; substance unchanged"],
    "signed_off_at": "2026-09-18"
}
d.setdefault("controller_progress", {})
d["controller_progress"]["V1_GOLDEN"]["pending"] = []
d["controller_progress"]["V1_GOLDEN"]["cases_done"]["G13"] = "PASS (v5 definitive comparison; invariant holds at all sizes)"
d["controller_progress"]["V1_GOLDEN"]["summary"] = "artifacts/V1/V1_SUMMARY.md"
d["controller_progress"]["V1_GOLDEN"]["tally"] = "6 PASS, 6 FAIL_PRODUCT, 1 PASS (win side BLOCKED_ENVIRONMENT); findings F-V1-001..008"
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state.json: V1_GOLDEN closed with verifier signoff")
