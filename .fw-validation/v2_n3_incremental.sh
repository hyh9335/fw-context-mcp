#!/bin/bash
# N3 v2: incremental mutation on N1's completed index
# pre-snapshot -> mutate one TU -> incremental reindex -> compare
set -x
PY=~/fwctx-venv311/bin/python
WORK=~/fwctx-v2-work/N1/nuttx-src
IDX=~/fwctx-v2-work/N1/index
OUT=/mnt/d/github/fw-context-mcp/.fw-validation/artifacts/V2/N3
mkdir -p "$OUT"
TARGET=$WORK/libs/libc/stdio/lib_gets.c

# 1) pre-mutation DB snapshot (controller oracle duty; N1 tree untouched so far)
$PY - "$IDX" "$OUT" <<'EOF'
import glob, json, sqlite3, sys
idx, out = sys.argv[1], sys.argv[2]
db = glob.glob(idx + "/*/index.db")[0]
con = sqlite3.connect(db)
n = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
gets = con.execute("SELECT COUNT(*) FROM symbols WHERE name LIKE '%lib_gets%'").fetchone()[0]
probe = con.execute("SELECT COUNT(*) FROM symbols WHERE name='n3_probe_fn'").fetchone()[0]
json.dump({"pre_symbol_count": n, "pre_lib_gets_syms": gets, "pre_probe_present": probe},
          open(out + "/n3_pre_db.json", "w"), indent=1)
print("pre:", n, "symbols;", gets, "lib_gets syms; probe present:", probe)
EOF

# 2) mutate the disposable copy
if ! grep -q n3_probe_fn "$TARGET"; then
cat >> "$TARGET" <<'EOF'

static int n3_probe_fn(int x)
{
	return x + 7;
}
EOF
fi
tail -5 "$TARGET"

# 3) kill watcher, incremental reindex SAME index dir (project root = work root, as registered); time it
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null
export FW_CONTEXT_INDEX_DIR="$IDX"
cd "$WORK"
START=$(date +%s)
$PY -m fw_context_mcp.cli index "$WORK/build/compile_commands.json" --project ~/fwctx-v2-work/N1 --no-analyze --no-embeddings 2>&1 | tail -2
END=$(date +%s)
echo "N3-INCREMENTAL-SECONDS: $((END - START))"
p=$(cat "$IDX/watcher.pid" 2>/dev/null); [ -n "$p" ] && kill -9 "$p" 2>/dev/null

# 4) post-snapshot + delta
$PY - "$IDX" "$OUT" <<'EOF'
import glob, json, sqlite3, sys
idx, out = sys.argv[1], sys.argv[2]
db = glob.glob(idx + "/*/index.db")[0]
con = sqlite3.connect(db)
n = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
probe = con.execute("SELECT COUNT(*) FROM symbols WHERE name='n3_probe_fn'").fetchone()[0]
pre = json.load(open(out + "/n3_pre_db.json"))
delta = n - pre["pre_symbol_count"]
res = {"post_symbol_count": n, "delta": delta, "probe_present": probe > 0,
       "delta_reasonable": 0 < delta < 20}
json.dump(res, open(out + "/n3_post_db.json", "w"), indent=1)
print("post:", n, "delta:", delta, "probe:", probe)
EOF
echo "N3-INDEXED"
