import sqlite3
import glob

db = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G10/index/*/index.db")[0]
con = sqlite3.connect(db)
print("initial_sp symbol:", con.execute(
    "SELECT id, name, kind FROM symbols WHERE name LIKE '%initial%'").fetchall())
cols = [c[1] for c in con.execute("PRAGMA table_info(refs)").fetchall()]
print("refs cols:", cols)
sid = con.execute("SELECT id FROM symbols WHERE name='__initial_sp'").fetchone()
if sid:
    print("refs rows:", con.execute(
        f"SELECT * FROM refs WHERE to_usr LIKE '%initial%' OR from_usr LIKE '%initial%' LIMIT 6").fetchall())
