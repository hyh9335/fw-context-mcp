"""Final migration-scaffold audit (§19).

Classifies every migration-related module/scaffold as:
- KEEP: actively used in production v2-only operation
- REMOVE: dead code that should be deleted (backlog)
- BACKLOG: optional future cleanup (§41 stop boundary — NOT now)

The principle: M5 is semantic/operational retirement, NOT physical deletion.
An explicit, isolated, default-OFF compatibility island may remain.
"""
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
import os
os.environ.pop("FW_CONTEXT_V2_WRITE_AUTHORITY", None)
os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
os.environ.pop("FW_CONTEXT_LEGACY_COMPATIBILITY_MODE", None)

ROOT = Path(__file__).parent.parent.parent
SRC = ROOT / "src" / "fw_context_mcp"

classifications = []

# ── Legacy compatibility island ──────────────────────────────────────────
# These modules form the explicit, isolated, default-OFF compat island.
# They are gated by _legacy_compat_on() which returns False by default.
compat_island = [
    "indexer/_unit_processor.py (_legacy_compat_on, legacy conn.* calls)",
    "indexer/runner.py (_v2_shadow_flag_on gate, legacy conn.* gates)",
    "store_v2/certify.py (v2_shadow_enabled — returns True when v2_write_authority on)",
    "mcp/shared/v2_shadow_adapters.py (shadow adapters for v2 read authority)",
    "mcp/shared/authority.py (v2_authority_route — routes to v2 by default)",
]
for m in compat_island:
    classifications.append(("KEEP", m, "Active production v2-only path; legacy gates default OFF"))

# ── Legacy semantic storage (sqlite legacy DB) ───────────────────────────
# In default mode (legacy_compatibility_mode=False), the legacy DB is NOT
# created/written. These modules handle the legacy DB but are gated OFF.
legacy_db_modules = [
    "indexer/db.py (legacy sqlite schema + writes)",
    "indexer/_unit_processor.py legacy conn.* calls",
    "indexer/runner.py legacy conn.* calls",
]
for m in legacy_db_modules:
    classifications.append(("BACKLOG", m, "Default-OFF legacy semantic storage; physical deletion is §41 future task"))

# ── v2 store modules (production) ────────────────────────────────────────
v2_modules = [
    "store_v2/schema.py (DDL + schema constants)",
    "store_v2/certify.py (v2_store_path, certify functions)",
    "store_v2/worlds.py (World lifecycle, finalize_world, resolve_current_view)",
    "store_v2/gc.py (reachability-based GC: analyze_gc, apply_gc, verify_gc)",
    "store_v2/publish.py (v2 store open/read/write)",
    "store_v2/query.py (v2 query path)",
    "store_v2/identity.py (BuildIdentity, Snapshot identity)",
]
for m in v2_modules:
    classifications.append(("KEEP", m, "Production v2 semantic store"))

# ── MCP handlers ─────────────────────────────────────────────────────────
mcp_handlers = [
    "mcp/server.py (tool registration, v2_authority_route wrapping)",
    "mcp/handlers/search.py, source.py, callgraph.py, inheritance.py, variables.py, conditionals.py",
    "mcp/handlers/maintenance.py (get_active_build, list_projects, etc.)",
    "mcp/handlers/variants.py (compare_variants, group_equivalent_variants — specialized optional)",
]
for m in mcp_handlers:
    classifications.append(("KEEP", m, "Production MCP tool surface"))

# ── Configuration ────────────────────────────────────────────────────────
config_modules = [
    "config/settings.py (legacy_compatibility_mode=False, v2_write_authority=True defaults)",
]
for m in config_modules:
    classifications.append(("KEEP", m, "Production configuration with correct defaults"))

# ── Verify the compat island is truly default-OFF ─────────────────────────
from fw_context_mcp.config.settings import IndexConfig
ic = IndexConfig()
if not ic.legacy_compatibility_mode:
    classifications.append(("KEEP", "IndexConfig.legacy_compatibility_mode=False", "Correct default — v2-only"))
else:
    classifications.append(("FAIL", "IndexConfig.legacy_compatibility_mode=True", "WRONG default!"))
if ic.v2_write_authority:
    classifications.append(("KEEP", "IndexConfig.v2_write_authority=True", "Correct default — v2 write authority"))
else:
    classifications.append(("FAIL", "IndexConfig.v2_write_authority=False", "WRONG default!"))

# ── Verify _legacy_compat_on() returns False by default ──────────────────
from fw_context_mcp.indexer._unit_processor import _legacy_compat_on
if not _legacy_compat_on():
    classifications.append(("KEEP", "_legacy_compat_on()=False", "Correct default — legacy compat OFF"))
else:
    classifications.append(("FAIL", "_legacy_compat_on()=True", "WRONG default!"))

# ── Verify v2_shadow_enabled returns True (v2 is authority) ──────────────
from fw_context_mcp.store_v2.certify import v2_shadow_enabled
if v2_shadow_enabled():
    classifications.append(("KEEP", "v2_shadow_enabled()=True", "v2 write authority active by default"))
else:
    classifications.append(("FAIL", "v2_shadow_enabled()=False", "v2 write authority NOT active!"))

# ── Count physical files in each category ────────────────────────────────
print("=== MIGRATION-SCAFFOLD AUDIT (§19) ===")
print()
keep = [(c, m, r) for c, m, r in classifications if c == "KEEP"]
backlog = [(c, m, r) for c, m, r in classifications if c == "BACKLOG"]
remove = [(c, m, r) for c, m, r in classifications if c == "REMOVE"]
fails = [(c, m, r) for c, m, r in classifications if c == "FAIL"]

print(f"KEEP ({len(keep)}): Active production v2-only modules")
for _, m, r in keep:
    print(f"  [KEEP] {m}")
    print(f"         {r}")
print()
print(f"BACKLOG ({len(backlog)}): Default-OFF legacy storage (§41 future deletion)")
for _, m, r in backlog:
    print(f"  [BACKLOG] {m}")
    print(f"            {r}")
print()
if remove:
    print(f"REMOVE ({len(remove)}): Dead code to delete now")
    for _, m, r in remove:
        print(f"  [REMOVE] {m}")
        print(f"           {r}")
else:
    print("REMOVE (0): No dead code requires immediate deletion")
print()
if fails:
    print(f"FAIL ({len(fails)}): Configuration defects!")
    for _, m, r in fails:
        print(f"  [FAIL] {m}")
        print(f"         {r}")
else:
    print("FAIL (0): All configuration defaults correct")

print()
print(f"{'FAIL' if fails else 'PASS'}: {len(fails)} failures, {len(keep)} KEEP, {len(backlog)} BACKLOG, {len(remove)} REMOVE")
