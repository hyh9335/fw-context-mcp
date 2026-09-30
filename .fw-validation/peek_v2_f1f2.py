import json
import io
from pathlib import Path

for tag in ("F1", "F2"):
    d = json.load(io.open(rf".fw-validation/artifacts/V2/{tag}/responses_{tag.lower()}.json",
                          encoding="utf-8"))
    print(f"======== {tag} ========")
    for k, v in d.items():
        if not isinstance(v, dict):
            continue
        t = (v.get("text") or "")
        err = v.get("isError")
        head = " ".join(t.split())[:400]
        print(f"== {k[:70]} err={err}")
        print("  ", head)
