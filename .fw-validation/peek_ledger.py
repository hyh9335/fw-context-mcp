import json
import io

fp = r".fw-validation/case_ledger.json"
d = json.load(io.open(fp, encoding="utf-8"))
print(type(d).__name__, "len", len(d))
item = d[0] if isinstance(d, list) else next(iter(d.items()))
print(json.dumps(item, ensure_ascii=False)[:300])
