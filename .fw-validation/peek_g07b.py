import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G07/raw/responses.json", encoding="utf-8"))
for k, v in d.items():
    if k.startswith("find_dead_code"):
        t = v.get("text") or ""
        print("==", k, "len", len(t))
        # find live function mentions with context
        for needle in ("live_leaf", "live_root", '"main"'):
            idx = 0
            while True:
                i = t.find(needle, idx)
                if i < 0:
                    break
                print("  ...", t[max(0, i - 240):i + 60].replace("\n", " ")[:300])
                idx = i + 1
        print()
