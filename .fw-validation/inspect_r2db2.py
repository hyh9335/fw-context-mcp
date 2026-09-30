import glob
import sqlite3

db = glob.glob("/home/h00969597/fwctx-v2-work/R1/index/*/index.db")[0]
con = sqlite3.connect(db)
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", tables)
cols = [c[1] for c in con.execute("PRAGMA table_info(files)").fetchall()]
print("files cols:", cols)
print("files rows:", con.execute("SELECT COUNT(*) FROM files").fetchone()[0])
