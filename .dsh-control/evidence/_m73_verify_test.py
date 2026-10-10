"""M7.3 Fresh Verifier — materialization proof test.

Runs index_legacy_db_into_m6 against the real legacy fixture DB and checks
that canonical M6 facts are actually produced in the M6 store.
"""
import sys
import sqlite3
import tempfile
import shutil
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / ".dsh-control" / "m6_research"))

from fw_context_mcp.store_v2.m6_production_indexing import (
    index_legacy_db_into_m6,
    open_m6_store,
)

LEGACY_DB = REPO / "tests" / "fixtures" / "legacy_rows2" / "index.db"

print("=" * 70)
print("STEP 1: Inspect legacy fixture DB")
print("=" * 70)
print(f"Legacy DB path: {LEGACY_DB}")
print(f"Legacy DB exists: {LEGACY_DB.exists()} size={LEGACY_DB.stat().st_size}")

conn = sqlite3.connect(str(LEGACY_DB))
conn.row_factory = sqlite3.Row
tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"Tables in legacy DB: {tables}")
for t in ["symbols", "call_sites", "refs", "overrides", "build_configs"]:
    if t in tables:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {cnt} rows")
# Sample one symbol to confirm the schema is real
try:
    sample = conn.execute(
        "SELECT usr, name, kind, file_path, line FROM symbols LIMIT 3"
    ).fetchall()
    print("Sample symbols:")
    for s in sample:
        print(f"  usr={s['usr'][:40]} name={s['name']} kind={s['kind']} "
              f"file={s['file_path']} line={s['line']}")
except Exception as e:
    print(f"  symbols sample failed: {e}")
conn.close()

print()
print("=" * 70)
print("STEP 2: Run index_legacy_db_into_m6 (REAL materialization)")
print("=" * 70)

# Use a temp project root so we don't pollute the repo's .fw-context
tmp_project = Path(tempfile.mkdtemp(prefix="m73_verify_"))
try:
    stats = index_legacy_db_into_m6(
        legacy_db_path=LEGACY_DB,
        project_root=tmp_project,
        build_name="verifier-test",
    )
    print(f"Returned stats: {json.dumps(stats, indent=2, default=str)}")
    print(f"facts_total: {stats.get('facts_total', 'MISSING')}")

    print()
    print("=" * 70)
    print("STEP 3: Verify M6 store actually contains facts (not just stats)")
    print("=" * 70)
    m6_db = tmp_project / ".fw-context" / "index-m6.db"
    print(f"M6 DB path: {m6_db}")
    print(f"M6 DB exists: {m6_db.exists()} size={m6_db.stat().st_size if m6_db.exists() else 0}")

    if m6_db.exists():
        m6_conn = sqlite3.connect(str(m6_db))
        m6_conn.row_factory = sqlite3.Row
        m6_tables = [r[0] for r in m6_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"M6 tables: {m6_tables}")

        # Count facts of each kind
        for t in ["facts", "worlds", "build_sets", "fact_buildsets"]:
            if t in m6_tables:
                cnt = m6_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  M6 {t}: {cnt} rows")

        # Sample enriched payloads — proves this is NOT a v2 copy
        if "facts" in m6_tables:
            cols = [c[1] for c in m6_conn.execute("PRAGMA table_info(facts)").fetchall()]
            print(f"  facts columns: {cols}")
            sample = m6_conn.execute(
                "SELECT fact_kind, fact_canon, payload FROM facts LIMIT 5"
            ).fetchall()
            print("  Sample M6 facts:")
            for s in sample:
                payload = json.loads(s["payload"]) if s["payload"] else {}
                # Show enriched fields that prove it's not a v2 copy
                enriched_keys = [k for k in (
                    "file", "line", "qualified_name", "signature",
                    "docstring", "end_line", "is_definition"
                ) if k in payload]
                print(f"    kind={s['fact_kind']} enriched_fields={enriched_keys}")
                print(f"      canon={s['fact_canon'][:80]}")
                print(f"      payload sample: name={payload.get('name')} "
                      f"file={payload.get('file')} line={payload.get('line')}")
        m6_conn.close()
    else:
        print("FAIL: M6 DB was NOT created!")
finally:
    shutil.rmtree(tmp_project, ignore_errors=True)

print()
print("=" * 70)
print("STEP 4: Test no-silent-fallback (failure path raises)")
print("=" * 70)
# Point at a bogus legacy DB path — should raise, not silently return
try:
    bogus = index_legacy_db_into_m6(
        legacy_db_path=Path("/nonexistent/bogus.db"),
        project_root=Path(tempfile.mkdtemp(prefix="m73_bogus_")),
        build_name="bogus",
    )
    print(f"SILENT FALLBACK DETECTED: returned stats={bogus} instead of raising")
    print("FAIL: M6 write path silently swallowed the error!")
except Exception as e:
    print(f"GOOD: failure raised ({type(e).__name__}: {e})")
