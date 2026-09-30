"""Z1 analysis: compile-DB TU truth vs fw-context coverage."""
import json
import io
from pathlib import Path

OUT = Path(r".fw-validation/artifacts/V2/Z1")
cc = json.load(io.open(OUT / "zephyr_compile_commands.json", encoding="utf-8"))
cc_files = sorted({Path(e["file"]).as_posix().split("fwctx-bench/")[-1] for e in cc})

d = json.load(io.open(OUT / "responses_z1.json", encoding="utf-8"))
ab = None
main_rows = []
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    if k.startswith("get_active_build"):
        ab = json.JSONDecoder().raw_decode(t)[0]
    elif k.startswith("lookup_symbol") and '"main"' in k:
        s = t.strip()
        i = 0
        while i < len(s):
            s2 = s[i:].lstrip()
            if not s2:
                break
            off = len(s[i:]) - len(s2)
            try:
                o, end = json.JSONDecoder().raw_decode(s2)
                if isinstance(o, dict) and o.get("name") == "main":
                    main_rows.append(o.get("file", ""))
                i = i + off + end
            except ValueError:
                break

print("CC TU count:", len(cc))
print("CC sample flags:", cc[0]["command"][:200])
print("indexed: symbol_count=%s file_count=%s build=%s" %
      (ab.get("symbol_count"), ab.get("file_count"), ab.get("build_system")))
print("indexed_at:", ab.get("indexed_at"), "modified_files:", ab.get("modified_files_count"))
print("main definitions:", [f.split("fwctx-bench/")[-1] for f in main_rows])

# TU truth: every CC .c file should be indexed (modulo generated/tooling exclusions)
# file_count includes headers; the strict check is via the DB
import glob
import sqlite3
db = glob.glob(str(Path.home() / "fwctx-v2-work/Z1/index/*/index.db"))
if db:
    con = sqlite3.connect(db[0])
    rows = con.execute(
        "SELECT DISTINCT file_path FROM files WHERE file_path LIKE '%.c' "
        "AND file_path NOT LIKE '%header%'").fetchall()
    idx_c = sorted({Path(r[0]).as_posix().split("fwctx-bench/")[-1] for r in rows})
    only_cc = [f for f in cc_files if f not in idx_c]
    print("CC .c files:", len(cc_files), "indexed .c files:", len(idx_c))
    print("CC files NOT indexed:", len(only_cc), only_cc[:10])
    prov = {"cc_tu_count": len(cc), "indexed_c_files": len(idx_c),
            "cc_files_not_indexed": only_cc,
            "symbol_count": ab.get("symbol_count"),
            "file_count_index": ab.get("file_count"),
            "indexed_at": ab.get("indexed_at"),
            "main_defs": [f.split("fwctx-bench/")[-1] for f in main_rows]}
    (OUT / "z1_tu_truth.json").write_text(json.dumps(prov, indent=1), encoding="utf-8")
    print("wrote z1_tu_truth.json")
