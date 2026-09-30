import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/C1/responses_c1c.json", encoding="utf-8"))
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    print(f"== {k[:64]}")
    print("  ", " ".join(t.split())[:350])
