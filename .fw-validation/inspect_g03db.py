import glob
import sqlite3

db = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G03/index/*/index.db")[0]
con = sqlite3.connect(db)
rows = con.execute(
    "SELECT name, line, end_line, length(source), (end_line - line + 1) "
    "FROM symbols WHERE kind='function' ORDER BY name").fetchall()
for r in rows:
    print(r)
