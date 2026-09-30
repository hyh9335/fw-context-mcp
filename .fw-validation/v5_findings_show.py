import json
import io

d = json.load(io.open(r".fw-validation/FINDINGS.json", encoding="utf-8"))
for f in d["findings"]:
    print(f["id"], "| cls =", f.get("classification", "?"), "| sev =", f.get("severity", "?"))
