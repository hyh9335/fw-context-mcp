import json
import io
from pathlib import Path

FV = Path(r"D:\github\fw-context-mcp\.fw-validation")

# 1) state: V4 SIGN_OFF
sp = FV / "state.json"
s = json.load(io.open(sp, encoding="utf-8"))
s["phases"]["V4_ADVERSARIAL"] = {
    "status": "SIGN_OFF",
    "verifier": "e56ff54d (SIGN_OFF; 4 cases materially verified) + f1d17599 relaunch (SIGN_OFF; 6/6 checklist) — two independent sign-offs",
    "verifier_corrections_applied": [
        "A4 mid reindex now logged (raw/reindex_delete.log: 'TUs to index: 1', '1 updated, 0 unchanged, 0 skipped') — MINOR 1 closed with measured evidence",
        "A2 re-run with materialize step (raw/materialize.log exit 0) — MINOR 3 closed; artifact set uniform",
        "A3 helper-probe qualification note added to result JSON — MINOR 2 documented",
        "Daemon AssertionError (early A4 episodes) recorded as F-V4-001 candidate finding",
    ],
}
json.dump(s, io.open(sp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

# 2) findings: add F-V4-001
fp = FV / "findings.json"
d = json.load(io.open(fp, "utf-8")) if False else json.load(io.open(fp, encoding="utf-8"))
finds = d["findings"] if isinstance(d, dict) and "findings" in d else d
if not any(f.get("id") == "F-V4-001" for f in finds):
    finds.append({
        "id": "F-V4-001",
        "classification": "ROBUSTNESS_NOTE",
        "severity": "LOW",
        "title": "Watcher daemon crashed with AssertionError 'assert cfg.project.id is not None' (fw_context_mcp/mcp/background.py:360 -> shared/readiness.py:106, via daemon.py:419) during early V4-A4 episodes; the daemon's own background index also exited 1 in the final A4 run (benign to the CLI-written DB)",
        "evidence": ["C:/Users/h00969597/fwctx-validation-work/V4/A4_source_delete_add/index/5f8dc551cff64988a0bca19bc134d70e/daemon.log"],
        "discovered_in": "V4_ADVERSARIAL (verifier e56ff54d NOTE 1)",
        "disposition": "open — candidate for repair wave; not part of the NOT_READY driver set (no data loss/corruption observed)",
    })
json.dump({"findings": finds}, io.open(fp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

# 3) A3 result: qualification note
a3p = FV / "artifacts" / "V3" / "PARITY_DIFF.json"  # keep import local; real edit below
a3 = FV / "artifacts" / "V4" / "A3_build_variant_switch" / "A3_build_variant_switch.result.json"
a3d = json.load(io.open(a3, encoding="utf-8"))
if "verifier_qualification" not in a3d:
    a3d["verifier_qualification"] = ("Verifier MINOR 2: both helper search_bodies probes returned empty (pre and post); "
                                     "the 'helper untouched' claim rests on stable per-TU symbol counts 2/1/2, not a direct helper probe. "
                                     "Classification unaffected (the audited property is the selected() variant re-derivation).")
    io.open(a3, "w", encoding="utf-8").write(json.dumps(a3d, indent=2, ensure_ascii=True) + "\n")

# 4) ledger: annotate V4 phase verdict
lp = FV / "case_ledger.json"
ld = json.load(io.open(lp, encoding="utf-8"))
ld.setdefault("phase_verdicts", {})["V4_ADVERSARIAL"] = "SIGN_OFF (e56ff54d + f1d17599, independent)"
ld.setdefault("phase_verdicts", {})["V3_CROSS_PLATFORM"] = "SIGN_OFF (f5a26796 re-audit rev2)"
json.dump(ld, io.open(lp, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("state/findings/A3/ledger updated; F-V4-001 added")
