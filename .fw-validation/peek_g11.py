import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G11/wsl/responses.json", encoding="utf-8"))
for k, v in d.items():
    if "lookup" in k:
        print("==", k)
        print((v.get("text") or "")[:1500])
        print()
