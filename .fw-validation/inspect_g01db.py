import glob
import sqlite3

db = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G01/index/*/index.db")[0]
con = sqlite3.connect(db)
rows = con.execute(
    "SELECT name, kind, length(source), (end_line - line + 1) AS nlines, "
    "substr(replace(source, char(10), ' '), 1, 40) "
    "FROM symbols WHERE kind='function' ORDER BY name").fetchall()
for r in rows:
    print(f"{r[0]}: srclen={r[2]} lines={r[3]} src={r[4]!r}")
