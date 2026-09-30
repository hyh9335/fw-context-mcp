import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G03/raw/responses.json", encoding="utf-8"))
key = 'search_bodies::{"query": "helper"}'
r = d.get(key)
print(json.dumps(r, indent=1)[:1800])
