import glob
import sqlite3

db = glob.glob("/home/h00969597/fwctx-v2-work/R1/index/*/index.db")[0]
con = sqlite3.connect(db)
print("build_configs cols:", [c[1] for c in con.execute("PRAGMA table_info(build_configs)").fetchall()])
cfgs = con.execute("SELECT config_hash FROM build_configs").fetchall()
print("config hashes:", cfgs)
for pat in ("saul", "core/msg.c", "core/thread.c", "panic.c"):
    n = con.execute("SELECT COUNT(DISTINCT path) FROM files WHERE path LIKE ?",
                    ("%" + pat + "%",)).fetchone()[0]
    print(pat, "->", n)
print("symbol rows:", con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
