import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G13/raw/responses_clean1.json", encoding="utf-8"))
for k, v in d.items():
    t = (v.get("text") or "")
    print("==", k, "len", len(t))
    print(t[:500])
    print()
