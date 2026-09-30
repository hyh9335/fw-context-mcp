import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G07/raw/responses.json", encoding="utf-8"))
for k, v in d.items():
    if k.startswith("find_dead_code"):
        print("==", k)
        for obj in (v.get("json") or []):
            if isinstance(obj, dict) and obj.get("name") in ("main", "live_root", "live_leaf"):
                print(json.dumps(obj, indent=1))
