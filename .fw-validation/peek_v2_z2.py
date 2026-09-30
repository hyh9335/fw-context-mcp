import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/Z2/responses_z2_variants.json", encoding="utf-8"))
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    err = v.get("isError")
    print(f"== {k[:62]} err={err}")
    print("  ", " ".join(t.split())[:300])
