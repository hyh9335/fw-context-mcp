import glob
import sqlite3

for case, names in [("G12", ["active_line", "inactive_line"]),
                    ("G01", ["role_value_a", "role_value_b", "mode_value"]),
                    ("G03", ["helper", "selected"]),
                    ("G02", ["selected"])]:
    dbs = glob.glob(rf"C:/Users/h00969597/fwctx-validation-work/V1/{case}/index/*/index.db")
    if not dbs:
        print(case, "no db")
        continue
    con = sqlite3.connect(dbs[0])
    print("==", case)
    for n in names:
        rows = con.execute(
            "SELECT name, length(source), substr(replace(source,char(10),' '),1,50) "
            "FROM symbols WHERE name LIKE ?", (f"%{n}%",)).fetchall()
        if not rows:
            print(f"   {n}: (no rows)")
        for r in rows[:4]:
            print(f"   {r[0]}: len={r[1]} src={r[2]!r}")
