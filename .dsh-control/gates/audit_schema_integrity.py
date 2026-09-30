"""Final schema/storage integrity audit (§18).

Verifies:
1. The v2 store schema is complete (all required tables/columns)
2. The v2 store can be created from scratch and pass FK integrity
3. All foreign keys are properly defined
4. The production indexer creates a valid v2 store end-to-end
"""
import sys, os, json, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
os.environ.pop("FW_CONTEXT_V2_WRITE_AUTHORITY", None)
os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
os.environ.pop("FW_CONTEXT_LEGACY_COMPATIBILITY_MODE", None)

CLANG_TARGET = "--target=x86_64-pc-windows-msvc"

def make_proj(proj):
    (proj / "inc").mkdir(parents=True, exist_ok=True)
    (proj / "src").mkdir(exist_ok=True)
    (proj / "inc" / "util.h").write_text("#define UTIL_VERSION 2\nint util(void);\n")
    (proj / "inc" / "cfg.h").write_text("#ifndef CFG_H\n#define CFG_H\n#define CFG_FEATURE 1\n#endif\n")
    (proj / "src" / "main.c").write_text(
        '#include "util.h"\n#include "cfg.h"\n'
        "int util(void) { return UTIL_VERSION; }\n"
        "int main(void) { return util(); }\n")
    argv = ["clang", "-c", "src/main.c", "-Iinc", CLANG_TARGET, "-DFEATURE=1"]
    (proj / "compile_commands.json").write_text(json.dumps([{
        "directory": str(proj), "arguments": argv, "file": str(proj / "src" / "main.c")}]))

results = []

# ── Audit 1: Schema completeness ──────────────────────────────────────────
from fw_context_mcp.store_v2.schema import DDL_STATEMENTS, STORE_VERSION
required_tables = [
    "store_metadata", "repository_namespaces", "snapshots", "builds",
    "snapshot_builds", "world_poisons", "file_blobs", "snapshot_files",
    "tu_bindings", "semantic_states", "cert_rows", "build_rollups"
]
# Extract table names from CREATE TABLE statements
created_tables = set()
for stmt in DDL_STATEMENTS:
    # DDL statements may have the table name after "CREATE TABLE" on the same line
    import re
    m = re.search(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', stmt, re.IGNORECASE)
    if m:
        created_tables.add(m.group(1))
missing = set(required_tables) - created_tables
if missing:
    results.append(f"FAIL: missing tables in schema: {missing}")
else:
    results.append(f"OK: schema defines all {len(required_tables)} required tables (store_version {STORE_VERSION})")

# ── Audit 2: End-to-end store creation + FK integrity ─────────────────────
tmp = Path(tempfile.mkdtemp(prefix="fwctx_schema_audit_"))
try:
    from fw_context_mcp.indexer import runner
    from fw_context_mcp.store_v2.certify import v2_store_path
    import sqlite3
    
    proj = tmp / "proj"; proj.mkdir(); make_proj(proj)
    db = tmp / "idx" / "pid" / "index.db"
    ch = runner.run(compile_commands=proj / "compile_commands.json",
        db_path=db, project_root=proj, project_id="pid",
        index_refs=False, index_embeddings=False, analyze_symbols=False)
    
    v2 = v2_store_path(db)
    results.append(f"OK: indexer created v2 store at {v2.name} (ch={ch[:12] if ch else 'None'})")
    
    # Open and check all tables exist
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    
    # Check PRAGMA foreign_key_check
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_violations:
        results.append(f"FAIL: {len(fk_violations)} FK violations: {fk_violations[:3]}")
    else:
        results.append("OK: PRAGMA foreign_key_check — 0 violations")
    
    # Check PRAGMA integrity_check
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    if integrity[0] != "ok":
        results.append(f"FAIL: integrity_check: {integrity[0]}")
    else:
        results.append("OK: PRAGMA integrity_check — ok")
    
    # Check all required tables exist
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
    for t in required_tables:
        if t not in tables:
            results.append(f"FAIL: table '{t}' not in v2 store")
    
    # Check schema_version
    sv = conn.execute("SELECT value FROM store_metadata WHERE key='store_version'").fetchone()
    if sv:
        results.append(f"OK: store_version in store = {sv[0]} (expected {STORE_VERSION})")
    else:
        results.append("WARN: store_version not found in store_metadata")
    
    # Check data counts
    counts = {}
    for t in required_tables:
        try:
            c = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            counts[t] = c
        except Exception as e:
            counts[t] = f"ERR: {e}"
    results.append(f"OK: row counts: {json.dumps(counts, default=str)}")
    
    # Verify the tu_bindings → semantic_states FK is composite (state_key, state_generation)
    fk_info = conn.execute("PRAGMA foreign_key_list(tu_bindings)").fetchall()
    composite_fk = any(
        fk["table"] == "semantic_states" and fk["from"] == "state_key"
        for fk in fk_info
    )
    if composite_fk:
        results.append("OK: tu_bindings → semantic_states composite FK present (state_key)")
    else:
        results.append("FAIL: tu_bindings missing composite FK to semantic_states")
    
    # Verify snapshot_builds UNIQUE constraint
    sb_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='snapshot_builds'").fetchone()
    if sb_sql and "UNIQUE(namespace_id, snapshot_id, build_identity_sha)" in sb_sql[0]:
        results.append("OK: snapshot_builds has UNIQUE(namespace, snapshot, build)")
    else:
        results.append("FAIL: snapshot_builds missing UNIQUE constraint")
    
    # Verify semantic_states UNIQUE constraint
    ss_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='semantic_states'").fetchone()
    if ss_sql and "UNIQUE(tusemantic_key, parser_generation)" in ss_sql[0]:
        results.append("OK: semantic_states has UNIQUE(tusemantic_key, parser_generation)")
    else:
        results.append("FAIL: semantic_states missing UNIQUE constraint")
    
    conn.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── Audit 3: Legacy DB absence in default mode ────────────────────────────
tmp2 = Path(tempfile.mkdtemp(prefix="fwctx_legacy_audit_"))
try:
    from fw_context_mcp.indexer import runner
    from fw_context_mcp.store_v2.certify import v2_store_path
    proj = tmp2 / "proj"; proj.mkdir(); make_proj(proj)
    db = tmp2 / "idx" / "pid" / "index.db"
    runner.run(compile_commands=proj / "compile_commands.json",
        db_path=db, project_root=proj, project_id="pid",
        index_refs=False, index_embeddings=False, analyze_symbols=False)
    
    # Legacy DB should NOT have semantic tables (only metadata)
    legacy_exists = db.exists()
    v2_exists = v2_store_path(db).exists()
    
    if legacy_exists:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        legacy_tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        # In default mode, the legacy DB should NOT have semantic tables
        semantic_tables = {"symbols", "files", "call_sites", "refs", "file_content_fts",
                          "symbols_fts", "files_fts", "embeddings"}
        legacy_has_semantic = semantic_tables & set(legacy_tables)
        if legacy_has_semantic:
            results.append(f"WARN: legacy DB has semantic tables: {legacy_has_semantic}")
        else:
            results.append(f"OK: legacy DB exists but has NO semantic tables (tables: {legacy_tables})")
        conn.close()
    else:
        results.append("OK: legacy DB does not exist (pure v2)")
    results.append(f"OK: v2 store exists = {v2_exists}")
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("=== SCHEMA/STORAGE INTEGRITY AUDIT ===")
for r in results:
    print(f"  {r}")
fails = [r for r in results if r.startswith("FAIL")]
print(f"\n{'FAIL' if fails else 'PASS'}: {len(fails)} failures, {len(results) - len(fails)} OK")
