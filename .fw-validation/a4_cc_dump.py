import glob
import json
import os
import sqlite3

W = "/mnt/c/Users/h00969597/fwctx-validation-work/V4/A4_source_delete_add"
cc = json.load(open(W + "/work/compile_commands.json", encoding="utf-8-sig"))
print("CC entries:", len(cc))
for e in cc:
    print("  file:", os.path.basename(str(e.get("file", "?"))), "| -o:", e["arguments"][e["arguments"].index("-o") + 1] if "-o" in e["arguments"] else None)
dbs = glob.glob(W + "/index/*/index.db")
for d in dbs:
    con = sqlite3.connect(d)
    names = sorted(r[0] for r in con.execute("SELECT DISTINCT name FROM symbols"))
    print("db:", d.split("/")[-2][:8], "mtime:", os.path.getmtime(d), "symbols:", names)
