import glob
import sqlite3
from pathlib import Path

db = glob.glob(str(Path.home() / "fwctx-v2-work/R1/index/*/index.db"))[0]
con = sqlite3.connect(db)
n_files = con.execute("SELECT COUNT(DISTINCT file_path) FROM files").fetchone()[0]
print("distinct files:", n_files)
for pat in ("%saul%", "%core/msg.c", "%core/thread.c", "%core/init.c", "%panic.c"):
    rows = con.execute("SELECT COUNT(DISTINCT file_path) FROM files WHERE file_path LIKE ?",
                       (f"%{pat.replace('%','') if 'saul' not in pat else 'saul'}%",) if pat != "%saul%" else ("%saul%",)).fetchall()
    print(pat, "->", rows[0][0])
rows = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
print("symbol rows:", rows)
# check compile_commands recorded in config
try:
    cfg = con.execute("SELECT value FROM meta WHERE key LIKE '%compile%'").fetchall()
    print("meta compile:", cfg[:2])
except Exception as e:
    print("meta err:", e)
