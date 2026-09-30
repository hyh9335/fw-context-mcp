import sqlite3
import glob
import json
import io

dbs = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G11/index/*/index.db")
print("win dbs:", dbs)
d = json.load(io.open(r".fw-validation/artifacts/V1/G11/wsl/responses.json", encoding="utf-8"))
for k, v in d.items():
    if "trap_base" in k or k.startswith("lookup_symbol::{\"name\": \"irq\""):
        print("==", k, "->", (v.get("text") or "")[:300])
