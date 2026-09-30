import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/Z3/responses_z3.json", encoding="utf-8"))
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    print(f"== {k[:64]} err={v.get('isError')}")
    print("  ", " ".join(t.split())[:340])
