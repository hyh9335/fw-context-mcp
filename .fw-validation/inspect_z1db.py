import glob
import sqlite3

db = glob.glob("/home/h00969597/fwctx-v2-work/Z1/index/*/index.db")[0]
con = sqlite3.connect(db)
for n in ("z_impl_printk", "z_impl_k_sleep", "printk"):
    rows = con.execute(
        "SELECT s.name, s.kind, f.path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? LIMIT 4", (n,)).fetchall()
    print(n, "->", rows if rows else "ABSENT")
rows = con.execute(
    "SELECT s.name, f.path FROM symbols s JOIN files f ON s.file_id = f.id "
    "WHERE s.name LIKE '%printk%' AND s.kind='function' LIMIT 8").fetchall()
print("printk-like functions:", [(r[0], r[1].split('/')[-1]) for r in rows])
