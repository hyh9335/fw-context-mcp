import sqlite3
import glob

dbs = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G03/index/*/index.db")
con = sqlite3.connect(dbs[0])
con.row_factory = sqlite3.Row
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", tables)
# find helper symbol rows
for t in tables:
    if t.endswith("_fts") or t.endswith("_data") or t.endswith("_idx") or t.endswith("_docsize") or t.endswith("_config"):
        continue
    cols = [c[1] for c in con.execute(f"PRAGMA table_info({t})").fetchall()]
    if "name" in cols:
        rows = con.execute(f"SELECT * FROM {t} WHERE name='helper'").fetchall()
        if rows:
            print(f"--- {t}: {len(rows)} helper rows")
            for r in rows:
                d = dict(r)
                body = d.get("body") or d.get("source") or ""
                print({k: (v[:200] if isinstance(v, str) and len(v) > 200 else v)
                       for k, v in d.items() if k in ("id", "name", "kind", "file_id", "context_id",
                                                      "config_hash", "line", "is_definition", "body", "source")})
