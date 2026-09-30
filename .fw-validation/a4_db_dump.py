import glob
import json
import sqlite3

dbs = sorted(glob.glob("/mnt/c/Users/h00969597/fwctx-validation-work/V4/A4_source_delete_add/index/*/index.db"))
# newest by mtime via os.path.getmtime
import os
dbs.sort(key=os.path.getmtime, reverse=True)
print("dbs:", [(d.split("/")[-2][:8], os.path.getmtime(d)) for d in dbs])
con = sqlite3.connect(dbs[0])
rows = con.execute("SELECT name, kind, is_definition, line FROM symbols ORDER BY name").fetchall()
print("symbol rows:")
for r in rows:
    print("  ", r)
n = con.execute("SELECT COUNT(DISTINCT name) FROM symbols").fetchone()[0]
print("distinct names:", n, "| total rows:", len(rows))
