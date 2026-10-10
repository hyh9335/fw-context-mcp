"""M7.3 Verifier — verify M6 fact payloads are enriched (not v2 copies)
and that World/BuildSet registration happened."""
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
)

LEGACY_DB = REPO / "tests" / "fixtures" / "legacy_rows2" / "index.db"

tmp_project = Path(tempfile.mkdtemp(prefix="m73_payload_"))
try:
    stats = index_legacy_db_into_m6(
        legacy_db_path=LEGACY_DB,
        project_root=tmp_project,
        build_name="verifier-test",
    )
    m6_db = tmp_project / ".fw-context" / "index-m6.db"
    m6_conn = sqlite3.connect(str(m6_db))
    m6_conn.row_factory = sqlite3.Row

    print("=== m6_shared_facts schema ===")
    cols = [c[1] for c in m6_conn.execute("PRAGMA table_info(m6_shared_facts)").fetchall()]
    print(f"columns: {cols}")

    print("\n=== M6 shared facts (enriched payload check) ===")
    rows = m6_conn.execute(
        "SELECT fact_kind, fact_canon, payload_json FROM m6_shared_facts"
    ).fetchall()
    print(f"Total M6 facts: {len(rows)}")
    for r in rows:
        p = json.loads(r["payload_json"]) if r["payload_json"] else {}
        enriched = [k for k in (
            "file", "line", "qualified_name", "signature",
            "docstring", "end_line", "is_definition"
        ) if k in p]
        print(f"\n  kind={r['fact_kind']}")
        print(f"  canon={r['fact_canon']}")
        print(f"  enriched_fields_present={enriched}")
        print(f"  full_payload={json.dumps(p, indent=4, default=str)}")

    print("\n=== World & BuildSet registration ===")
    for t in ["m6_buildsets", "m6_world_builds", "m6_world_fact_refs",
              "m6_certificates", "m6_body_facts"]:
        try:
            cnt = m6_conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {cnt} rows")
            sample = m6_conn.execute(f"SELECT * FROM {t} LIMIT 1").fetchall()
            for s in sample:
                print(f"    sample: {dict(s)}")
        except Exception as e:
            print(f"  {t}: error {e}")

    m6_conn.close()
finally:
    shutil.rmtree(tmp_project, ignore_errors=True)
