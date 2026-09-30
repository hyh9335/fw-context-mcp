import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G05/raw/responses.json", encoding="utf-8"))
for k, v in d.items():
    if k.startswith("get_method_overrides") or k.startswith("get_template_instances"):
        print("== G05", k)
        print((v.get("text") or "")[:800], "\n")

d6 = json.load(io.open(r".fw-validation/artifacts/V1/G06/raw/responses.json", encoding="utf-8"))
for k, v in d6.items():
    if k.startswith("find_indirect_targets") and ("registered" in k or '"cb"' in k):
        print("== G06", k)
        print((v.get("text") or "")[:1200], "\n")
