"""Build V0_receipt.json — machine-readable phase receipt with artifact hashes."""
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"D:\github\fw-context-mcp")
ART = REPO / ".fw-validation" / "artifacts" / "V0"

# fresh validate_pack run for the receipt evidence
log_path = ART / "V0_validate_pack.log"
r = subprocess.run([sys.executable, str(REPO / "fwctx_validation_benchmark_pack" / "validate_pack.py")],
                   capture_output=True, text=True)
log_path.write_text(f"exit: {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}", encoding="utf-8")

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

artifacts = [
    "V0_ENV_REPORT.md", "stdio_win_summary.json", "stdio_wsl_summary.json",
    "stdio_win_transcript.json", "stdio_wsl_transcript.json", "stdio_win_output.jsonl",
    "stdio_win_stderr.txt", "wsl_env_facts.txt", "manifest_parse_check.json",
    "public_clone_results.txt", "V0_validate_pack.log",
]
entries = []
for name in artifacts:
    p = ART / name
    if p.exists():
        entries.append({"path": f".fw-validation/artifacts/V0/{name}", "sha256": sha256(p), "bytes": p.stat().st_size})
    else:
        entries.append({"path": f".fw-validation/artifacts/V0/{name}", "missing": True})

lock = REPO / ".fw-validation" / "PUBLIC_LOCK.json"
if lock.exists():
    entries.append({"path": ".fw-validation/PUBLIC_LOCK.json", "sha256": sha256(lock), "bytes": lock.stat().st_size})
ledger = REPO / ".fw-validation" / "case_ledger.json"
entries.append({"path": ".fw-validation/case_ledger.json", "sha256": sha256(ledger), "bytes": ledger.stat().st_size})

receipt = {
    "phase": "V0_ENV",
    "mode": "VALIDATION_ONLY",
    "product_head": "d8f08a41d3fb9df6de255a187720f6332ed0f1c9",
    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "controller_side_gate": "ALL_CONTROLLER_ITEMS_PASS" if r.returncode == 0 else "VALIDATE_PACK_FAILED",
    "validate_pack_exit": r.returncode,
    "verifier_signoff": "PENDING",
    "artifacts": entries,
}
(ART / "V0_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"validate_pack_exit": r.returncode,
                  "controller_side_gate": receipt["controller_side_gate"],
                  "artifact_count": len(entries)}, indent=1))
