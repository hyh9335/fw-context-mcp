import json
import io

fp = r".fw-validation/case_ledger.json"
d = json.load(io.open(fp, encoding="utf-8"))
print("keys:", list(d.keys()))
v1cases = d.get("v1_cases") or d.get("cases")
print("v1 case container:", type(v1cases).__name__ if v1cases else None,
      "len", len(v1cases) if v1cases else 0)
if v1cases:
    one = v1cases[0] if isinstance(v1cases, list) else next(iter(v1cases.values()))
    print(json.dumps(one, ensure_ascii=False)[:300])
