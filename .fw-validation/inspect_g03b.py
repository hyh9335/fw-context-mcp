import sqlite3
import glob

dbs = glob.glob(r"C:/Users/h00969597/fwctx-validation-work/V1/G03/index/*/index.db")
con = sqlite3.connect(dbs[0])
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT s.id, s.name, s.kind, s.line, s.is_definition, s.context_id, "
    "f.path, substr(s.source,1,120) AS src_head, length(s.source) AS src_len "
    "FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name IN ('helper','selected') "
    "ORDER BY s.name, s.id").fetchall()
for r in rows:
    print(dict(r))
# conditional region evaluation for the helper #if group
cb = con.execute(
    "SELECT c.file_id, c.root_line, c.directive, c.condition, "
    "b.line AS bline, b.kind AS bkind, b.taken "
    "FROM conditionals c JOIN conditional_branches b ON b.conditional_id=c.id "
    "WHERE c.condition LIKE '%DISABLE_HELPER%'").fetchall()
for r in cb:
    print(dict(r))
