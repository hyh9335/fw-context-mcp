import glob
import sqlite3

db = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G13/n1/index_p/*/index.db")[0]
con = sqlite3.connect(db)
print("cols:", [c[1] for c in con.execute("PRAGMA table_info(symbols)").fetchall()])
for name in ("shared_value", "f_000"):
    rows = con.execute(
        "SELECT name, kind, is_definition, length(source), substr(source,1,60) "
        "FROM symbols WHERE name=?", (name,)).fetchall()
    print(name, "->", rows)
print("all symbols:", con.execute("SELECT name, length(source) FROM symbols").fetchall())
