import glob
import json
import sqlite3


def _has(con, t):
    try:
        con.execute(f"SELECT 1 FROM {t} LIMIT 1")
        return True
    except Exception:
        return False


db = glob.glob("/home/h00969597/fwctx-v2-work/N1/index/*/index.db")[0]
con = sqlite3.connect(db)
n = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
refs = con.execute("SELECT COUNT(*) FROM references_table").fetchone()[0] if _has(con, "references_table") else None
pre = json.load(open("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N3/n3_pre_db.json"))
delta = n - pre["pre_symbol_count"]
rows = con.execute(
    "SELECT s.name, s.kind, s.signature, s.is_definition, f.path FROM symbols s JOIN files f ON s.file_id=f.id "
    "WHERE s.name='n3_probe_fn' LIMIT 3").fetchall()
c = con.execute("SELECT content FROM files WHERE path LIKE '%lib_gets.c' LIMIT 1").fetchone()
body = c[0] if c else ""
out = {
    "note": "corrected post-mutation DB dump (first n3_post_db.json was the FAILED attempt-1 no-op; this dump is the direct oracle for the corrected run)",
    "post_symbol_count": n,
    "pre_symbol_count": pre["pre_symbol_count"],
    "delta": delta,
    "probe_rows": [{"name": r[0], "kind": r[1], "signature": r[2], "is_definition": r[3], "path": r[4]} for r in rows],
    "probe_body_in_stored_content": "return x + 7" in body,
    "stored_content_len": len(body),
    "reference_count": refs,
}
json.dump(out, open("/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N3/n3_post_db_corrected.json", "w"), indent=1)
print(json.dumps(out, indent=1)[:500])
