import json
import io
import re
from pathlib import Path

ROOT = Path(r"D:\github\fw-context-mcp")
FV = ROOT / ".fw-validation"
ledger = json.load(io.open(FV / "case_ledger.json", encoding="utf-8"))["cases"]
BASES = [FV, FV / "artifacts", ROOT]
missing = []
prose = 0
for c in ledger:
    cid = c["case_id"]
    slug = cid.split("-", 2)[-1] if cid.count("-") >= 2 else cid
    for e in c.get("evidence", []):
        s = str(e)
        if not ((" / " in s) or (" " in s and "/" not in s) or ":" in s or "§" in s) or (
                "/" in s and " " not in s):
            cand = [b / s for b in BASES]
            if "/" not in s:  # bare filename: also try the case's artifact dir
                for ph in ("V0", "V1", "V2", "V3", "V4"):
                    cand.append(FV / "artifacts" / ph / s)
                    cand.append(FV / "artifacts" / ph / slug / s)
            if not any(p.exists() for p in cand):
                missing.append((cid, s))
        else:
            prose += 1
print("UNRESOLVED evidence entries:", len(missing))
for m in missing[:15]:
    print("  ", m)
print("prose citations:", prose)
