import glob
import sqlite3

db = glob.glob("/home/h00969597/fwctx-v2-work/R1/index/*/index.db")[0]
con = sqlite3.connect(db)
cfgs = con.execute("SELECT config_hash, COUNT(DISTINCT id) FROM build_configs GROUP BY config_hash").fetchall()
print("build_configs:", cfgs)
ctx = con.execute("SELECT config_hash, COUNT(*) FROM build_contexts GROUP BY config_hash").fetchall()
print("build_contexts per config:", ctx)
for pat in ("saul", "core/msg.c", "core/thread.c", "panic.c"):
    n = con.execute("SELECT COUNT(DISTINCT path) FROM files WHERE path LIKE ?",
                    ("%" + pat + "%",)).fetchone()[0]
    print(pat, "->", n)
print("symbol rows:", con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
