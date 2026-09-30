import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/C1/responses_c1.json", encoding="utf-8"))
vt = None
for k, v in d.items():
    if k.startswith("get_vector_table") and isinstance(v, dict):
        t = v.get("text") or ""
        print("== get_vector_table (first 1800 chars)")
        print(t[:1800])
