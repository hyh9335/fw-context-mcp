"""Final reuse economics benchmark (§15).

Verifies that indexing a new version approaches work proportional to
actual semantic TU changes, not full repository reprocessing.

Benchmark:
1. Index version A (baseline)
2. Index version B with N% of TUs changed
3. Verify that the number of NEW SemanticStates created is proportional
   to the changed TUs, NOT the total TUs

parse_fraction ≈ semantic_change_fraction
"""
import sys, os, json, tempfile, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
os.environ.pop("FW_CONTEXT_V2_WRITE_AUTHORITY", None)
os.environ.pop("FW_CONTEXT_SHADOW_V2", None)
os.environ.pop("FW_CONTEXT_LEGACY_COMPATIBILITY_MODE", None)

CLANG_TARGET = "--target=x86_64-pc-windows-msvc"

def make_proj(proj, n_tus, *, changed_indices=None, version_suffix=""):
    """Create a project with n_tus translation units.
    changed_indices: set of TU indices whose content differs from baseline."""
    if changed_indices is None:
        changed_indices = set()
    (proj / "inc").mkdir(parents=True, exist_ok=True)
    (proj / "src").mkdir(exist_ok=True)
    entries = []
    for i in range(n_tus):
        # Each TU is a separate .c file including its own header
        h = proj / "inc" / f"util{i}.h"
        c = proj / "src" / f"tu{i}.c"
        if i in changed_indices:
            # Changed TU: different macro value
            h.write_text(f"#define VAL{i} 100{version_suffix}\nint tu{i}(void);\n")
        else:
            h.write_text(f"#define VAL{i} {i}\nint tu{i}(void);\n")
        c.write_text(f'#include "util{i}.h"\nint tu{i}(void) {{ return VAL{i}; }}\n')
        argv = ["clang", "-c", f"src/tu{i}.c", "-Iinc", CLANG_TARGET]
        entries.append({
            "directory": str(proj), "arguments": argv,
            "file": str(proj / "src" / f"tu{i}.c")})
    (proj / "compile_commands.json").write_text(json.dumps(entries))

def index(proj, db):
    from fw_context_mcp.indexer import runner
    return runner.run(compile_commands=proj / "compile_commands.json",
        db_path=db, project_root=proj, project_id="pid",
        index_refs=False, index_embeddings=False, analyze_symbols=False)

def count_states(db):
    from fw_context_mcp.store_v2.certify import v2_store_path
    import sqlite3
    v2 = v2_store_path(db)
    conn = sqlite3.connect(f"file:{v2}?mode=ro", uri=True)
    states = conn.execute("SELECT COUNT(*) FROM semantic_states").fetchone()[0]
    worlds = conn.execute("SELECT COUNT(*) FROM snapshot_builds WHERE world_status='active'").fetchone()[0]
    bindings = conn.execute("SELECT COUNT(*) FROM tu_bindings").fetchone()[0]
    conn.close()
    return states, worlds, bindings

results = []
tmp = Path(tempfile.mkdtemp(prefix="fwctx_reuse_econ_"))
try:
    N = 20  # 20 TUs
    db = tmp / "idx" / "pid" / "index.db"

    # Version A: baseline (all 20 TUs)
    proj_a = tmp / "projA"; proj_a.mkdir()
    make_proj(proj_a, N)
    index(proj_a, db)
    states_a, worlds_a, bindings_a = count_states(db)
    results.append(f"Version A: {N} TUs indexed → {states_a} states, {worlds_a} worlds, {bindings_a} bindings")

    # Version B: 2 of 20 TUs changed (10% change rate)
    changed = {5, 12}  # 2 of 20 = 10%
    proj_b = tmp / "projB"; proj_b.mkdir()
    make_proj(proj_b, N, changed_indices=changed, version_suffix="_v2")
    index(proj_b, db)
    states_b, worlds_b, bindings_b = count_states(db)
    results.append(f"Version B: {N} TUs (2 changed = 10%) → {states_b} total states, {worlds_b} worlds, {bindings_b} bindings")

    new_states = states_b - states_a
    # 2 changed TUs should produce ~2 new SemanticStates
    # 18 unchanged TUs should reuse existing states
    results.append(f"New states created: {new_states} (expected ~{len(changed)} for {len(changed)} changed TUs)")

    # Reuse economics: new_states / N should ≈ changed / N
    parse_fraction = len(changed) / N  # 0.10
    semantic_change_fraction = new_states / N  # should be ~0.10
    results.append(f"parse_fraction = {parse_fraction:.2f}, semantic_change_fraction = {semantic_change_fraction:.2f}")

    # The benchmark passes if new_states is proportional to changed TUs,
    # not to total TUs. We allow a small overhead (e.g. for cert rows).
    # new_states should be <= len(changed) + 1 (small overhead acceptable)
    proportional = new_states <= len(changed) + 1
    results.append(f"Proportional: new_states ({new_states}) <= changed ({len(changed)}) + 1 = {proportional}")

    # Version C: 0 TUs changed (identical to A) — should reuse ALL states
    proj_c = tmp / "projC"; proj_c.mkdir()
    make_proj(proj_c, N)
    index(proj_c, db)
    states_c, worlds_c, bindings_c = count_states(db)
    new_states_c = states_c - states_b
    results.append(f"Version C: {N} TUs (0 changed = 0%) → {states_c} total states, {new_states_c} new states")
    results.append(f"Zero-change reuse: new_states = {new_states_c} (expected 0)")

    print("=== REUSE ECONOMICS BENCHMARK (§15) ===")
    for r in results:
        print(f"  {r}")

    passes = proportional and new_states_c == 0
    print(f"\n{'PASS' if passes else 'FAIL'}: proportional={proportional}, zero_change_reuse={new_states_c == 0}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
