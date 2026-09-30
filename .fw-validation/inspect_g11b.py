import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V1/G11/wsl/responses.json", encoding="utf-8"))
for k, v in d.items():
    print("KEY:", k)
    print((v.get("text") or "")[:400].replace("\n", " "))
    print()
