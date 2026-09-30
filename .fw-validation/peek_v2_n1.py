import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/N1/responses_n1.json", encoding="utf-8"))
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    print(f"== {k[:60]} err={v.get('isError')}")
    print("  ", " ".join(t.split())[:300])
