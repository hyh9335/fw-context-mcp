import glob
import json
import sqlite3

db = glob.glob("/home/h00969597/fwctx-v2-work/N1/index/*/index.db")[0]
con = sqlite3.connect(db)
n = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
pre = json.load(open("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N3/n3_pre_db.json"))
delta = n - pre["pre_symbol_count"]
rows = con.execute(
    "SELECT s.name, s.kind, s.signature, f.path FROM symbols s JOIN files f ON s.file_id=f.id "
    "WHERE s.name='n3_probe_fn' LIMIT 3").fetchall()
print("count:", n, "pre:", pre["pre_symbol_count"], "delta:", delta)
print("probe rows:", rows)
# body check via content: the file content should contain the probe
c = con.execute("SELECT content FROM files WHERE path LIKE '%lib_gets.c' LIMIT 1").fetchone()
body = c[0] if c else ""
print("probe body in stored content:", "return x + 7" in body, "| len:", len(body))
