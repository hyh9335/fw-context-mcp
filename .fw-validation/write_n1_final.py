import json
import io

n1 = {
    "case_id": "N1_nuttx_sim_nsh", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
    "scenario": "N1 nuttx sim:nsh clean build + index + product queries at real scale",
    "fixture": "nuttx (public clone, single cp -a work copy) BOARD_CONFIG=sim:nsh, cmake-only flow (NO configure.sh)",
    "env_fixes": ["kconfiglib olddefconfig on PATH (venv312)", "genromfs", "cmake -B build -DBOARD_CONFIG=<dir-path>"],
    "build": {"tu_count": 1194},
    "index": {"db_gb": 4.6, "index_wall_hours": "~5.3 (12:09→17:25 incl. Z1 slot; nuttx per-TU header closure + FTS5 dominates)",
              "symbol_count": 1494309, "file_count": 58254, "reference_count": 437176,
              "config_hash": "1a82705430eb4b32…", "modified_files": 0},
    "queries": {
        "nx_start": "sched/init/nx_start.c:502 is_definition=true (with docstring) ✓",
        "find_hotspots limit=5": "up_current_regs caller_count=937015 (sim-arch inline hot function; index survives 937k call edges) ✓",
        "search_code 'work queue'": "work_queue @ wqueue.h:406 (decl) ✓"
    },
    "scale_observations": ["nuttx index ~2x wall of zephyr Z1 at 16x the TUs; per-TU header closure cost dominates",
                            "product remained responsive at 1.49M symbols"],
    "status": "PASS"
}
open(r".fw-validation/artifacts/V2/N1/N1.result.json", "w", encoding="utf-8").write(json.dumps(n1, indent=2, ensure_ascii=False) + "\n")
print("N1.result.json final")
