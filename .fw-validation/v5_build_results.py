import json
import io
from pathlib import Path

ROOT = Path(r"D:\github\fw-context-mcp\.fw-validation")
A = ROOT / "artifacts"


def jload(p):
    return json.load(io.open(p, encoding="utf-8"))


state = jload(ROOT / "state.json")
ledger = jload(ROOT / "case_ledger.json")["cases"]
findings_old = jload(ROOT / "findings.json")
findings_old = findings_old["findings"] if isinstance(findings_old, dict) and "findings" in findings_old else findings_old

# --- normalize findings: classification = defect class, severity = impact tier
SEV = {"SILENT_DATA_LOSS": "HIGH", "INDEX_DIVERGENCE": "CRITICAL", "WRONG_ANSWER": "HIGH",
       "API_CONTRACT": "MEDIUM", "CORRUPTION": "CRITICAL", "STDIO_FAILURE": "HIGH"}
findings = []
for f in findings_old:
    g = dict(f)
    cls = f.get("classification") or f.get("severity")
    sev = f.get("severity") if f.get("severity") in SEV or f.get("severity") in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else None
    if cls in SEV:  # severity/classification were swapped in early records
        g["classification"], g["severity"] = cls, sev if sev in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else SEV[cls]
    if f.get("id") == "F-V2-001":
        g["classification"], g["severity"] = "WRONG_ANSWER", "HIGH"
    if f.get("id") == "F-V2-002":
        g["classification"], g["severity"] = "API_CONTRACT", "MEDIUM"
    findings.append(g)
io.open(ROOT / "FINDINGS.json", "w", encoding="utf-8").write(
    json.dumps({"generated_for": "V5_RELEASE", "count": len(findings), "findings": findings},
               indent=2, ensure_ascii=True) + "\n")

# --- coverage matrix
BLOCKED = "BLOCKED_ENVIRONMENT"
coverage = {
    "golden_windows": [
        {"case": c["case_id"], "status": c["status"], "evidence": c.get("evidence", [])}
        for c in ledger if c["case_id"].startswith("V1-")
    ],
    "golden_wsl": [
        {"case": "G04", "status": "FAIL_PRODUCT", "note": "WSL semantic coverage; Windows side BLOCKED (toolchain absent)"},
        {"case": "G11", "status": "PASS", "note": "WSL semantic coverage; Windows side BLOCKED (RISC-V toolchain absent)"},
        {"case": "G01-G03,G05-G07,G10,G12", "status": "see golden_windows (V3-WSL leg reproduces all 8)",
         "evidence": ["artifacts/V3/wsl/", "artifacts/V3/PARITY_DIFF.json"]},
    ],
    "public_real": [
        {"case": c["case_id"], "status": c["status"], "evidence": c.get("evidence", [])}
        for c in ledger if c["case_id"].startswith("V2-")
    ],
    "cross_platform": [
        {"case": "V3 parity Windows-baseline vs WSL-leg", "status": "8/8 SAME",
         "evidence": ["artifacts/V3/PARITY_DIFF.json", "artifacts/V3/wsl/"]},
        {"case": "G04/G11 Windows", "status": BLOCKED, "note": "clang/cmake/ninja/make/MSVC all missing on Windows"},
    ],
    "adversarial": [
        {"case": c["case_id"], "status": c["status"], "evidence": c.get("evidence", [])}
        for c in ledger if c["case_id"].startswith("V4-")
    ],
    "residuals": [
        {"case": "V2-F3-FREERTOS-QEMU-DEMO", "status": "NOT_RUN", "note": "OPTIONAL case; FreeRTOS linker/--gc-sections/map truth uncovered — recorded in KNOWN_VALIDATION_LIMITS.md"},
        {"case": "G13", "status": "see V1 verifier notes", "note": "unreliable first-pass comparison flagged by V1 verifier"},
    ],
}

# --- machine results
phase_verifier = {
    "V0_ENV": {"status": "SIGN_OFF", "verdict": "PASS",
               "report": "artifacts/V0/verifier2/verifier_report.md"},
    "V1_GOLDEN": {"status": "SIGN_OFF", "verdict": "PASS_WITH_NOTES",
                  "report": "artifacts/V1/verifier/verifier_report.md"},
    "V2_PUBLIC_REAL": {"status": "SIGN_OFF", "verdict": "SIGN_OFF (7 corrections applied)",
                       "verifier": "1299923d-eb43-460c-8f53-7157b60b3920"},
    "V3_CROSS_PLATFORM": {"status": "RE_AUDIT_IN_FLIGHT",
                          "first_audit": "NO_SIGN_OFF (labeling + missing WSL leg) — corrected: WSL leg run, PARITY_DIFF.json emitted, relabels applied"},
    "V4_ADVERSARIAL": {"status": "AUDIT_IN_FLIGHT", "controller_result": "8/8 PASS"},
    "V5_RELEASE": {"status": "COMPLETE_PENDING_VERIFIERS"},
}
from collections import Counter
tally = Counter(c["status"] for c in ledger)
results = {
    "generated_at_phase": "V5_RELEASE",
    "mode": "VALIDATION_ONLY",
    "product": state["product"],
    "phases": phase_verifier,
    "case_tally": dict(tally),
    "total_cases": len(ledger),
    "required_not_run": [c["case_id"] for c in ledger if c["status"] == "NOT_RUN" and "OPTIONAL" not in str(c.get("description", "")).upper()],
    "optional_not_run": [c["case_id"] for c in ledger if c["status"] == "NOT_RUN"],
    "failures": [{"case": c["case_id"], "status": c["status"]} for c in ledger if "FAIL" in c["status"]],
    "coverage_matrix": coverage,
    "verdict_drivers": [
        {"finding": "F-V2-001", "classification": "WRONG_ANSWER", "severity": "HIGH",
         "statement": "get_vector_table returns false 'No vector table' for the standard CMSIS6 C-template form (__VECTOR_TABLE varglobal const int[240]) while the assembly form is recognized; reproduced on Windows and WSL (V3)"},
        {"finding": "F-V1-003", "classification": "INDEX_DIVERGENCE", "severity": "CRITICAL",
         "statement": "G04 bare build + build.variants A/B/C: variant defines diverge from ground truth"},
        {"finding": "F-V1-001/F-V1-002/F-V1-007", "classification": "SILENT_DATA_LOSS", "severity": "HIGH",
         "statement": "same-source multi-define contexts, file-level conditional functions, and single-line definitions lose data silently"},
    ],
    "verdict_rule": "Any WRONG_ANSWER / SILENT_DATA_LOSS / INDEX_DIVERGENCE / CORRUPTION / STDIO_FAILURE that is not proven a benchmark/oracle defect forces NOT_READY",
    "benchmark_defect_exclusion": "No oracle defect found for F-V2-001: the CMSIS6 C-template __VECTOR_TABLE form is the industry-standard ground truth demanded by expected.json; the assembly form IS recognized, isolating the gap to C-form detection",
    "FINAL_VERDICT": "NOT_READY",
}
io.open(ROOT / "FINAL_VALIDATION_RESULTS.json", "w", encoding="utf-8").write(
    json.dumps(results, indent=2, ensure_ascii=True) + "\n")
print("FINDINGS.json + FINAL_VALIDATION_RESULTS.json written; verdict NOT_READY")
print("tally:", dict(tally))
