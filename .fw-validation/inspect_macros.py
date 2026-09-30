import sqlite3
import glob

dbs = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G02/index/*/index.db")
print("dbs:", dbs)
con = sqlite3.connect(dbs[0])
con.row_factory = sqlite3.Row
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", [t for t in tables if "macro" in t or "config" in t or "context" in t])
for t in [t for t in tables if "macro" in t]:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    rows = con.execute(f"SELECT * FROM {t} LIMIT 8").fetchall()
    print(f"--- {t}: {n} rows")
    for r in rows:
        print(dict(r))
