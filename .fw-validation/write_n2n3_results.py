import json
import io

n3 = {
    "case_id": "N3_nuttx_incremental", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
    "scenario": "N3 incremental mutation: append a static probe fn to a libc TU, verify the index re-derives correct facts",
    "fixture": "N1 work copy (nuttx sim:nsh), target libs/libc/stdio/lib_gets.c, appended static int n3_probe_fn(int x){return x+7;}",
    "method_notes": ["pre-mutation DB snapshot captured as clean anchor (n3_pre_db.json: 1494309 symbols)",
                      "the first attempt passed --project <nuttx-src> while the project was registered at the work root → 5s error no-op (harness note: --project must match the registered root)",
                      "after correction, mutation was picked up and verified directly in the DB (controller oracle duty)"],
    "results": {
        "symbol_delta": 1,
        "probe_row": "n3_probe_fn / function / int n3_probe_fn(int x) / nuttx-src/libs/libc/stdio/lib_gets.c",
        "probe_body_in_stored_content": True,
        "pickup_latency": "~30s from mutation to correct DB state",
        "total_count_after": 1494310
    },
    "observations": ["the synchronous `fw-context index` invocation launched a long full re-scan on top (killed as redundant); "
                      "the mutation pickup itself was fast and exact",
                      "delta exactly +1 shows no duplicate/lost symbols from the incremental update"],
    "status": "PASS"
}
open(r".fw-validation/artifacts/V2/N3/N3.result.json", "w", encoding="utf-8").write(json.dumps(n3, indent=2, ensure_ascii=False) + "\n")

n2 = {
    "case_id": "N2_nuttx_variant_pair", "phase": "V2_PUBLIC_REAL", "platform": "wsl",
    "scenario": "N2 sim:smp variant vs N1 sim:nsh — variant separation at real nuttx scale",
    "fixture": "same public nuttx clone, BOARD_CONFIG=boards/sim/sim/sim/configs/smp, own index dir",
    "env_notes": ["build tree was externally reconfigured mid-session once (phantom cmake at 12:55, root cause unidentified; artifact CC copy preserved; rebuilt cleanly)",
                   "index wall ~5h (per-TU header closure at smp scale)"],
    "queries": {
        "config_hashes": {"n1_nsh": "1a82705430eb4b32…", "n2_smp": "c543f8bd28fef7d7…", "different": True},
        "symbol_counts": {"n1_nsh": 1494310, "n2_smp": 1431214, "delta": -63096},
        "file_counts": {"n1_nsh": 58254, "n2_smp": 55602},
        "hotspots_n1": ["up_current_regs(937015)", "nxmutex_unlock(914352)", "nxmutex_lock(879798)"],
        "hotspots_n2": ["up_current_regs(859313)", "nxmutex_unlock(764784)", "invdiv_umulh64(758641)"],
        "config_diff_keys": 125
    },
    "analysis": "variant separation works at scale: different config_hash, materially different symbol/file counts and "
                "hotspot profiles (smp loses nxmutex_lock from top-3 and gains invdiv_umulh64; both plausible for SMP "
                "vs UP configs); .config diff 125 keys corroborates genuinely different builds",
    "status": "PASS"
}
open(r".fw-validation/artifacts/V2/N2/N2.result.json", "w", encoding="utf-8").write(json.dumps(n2, indent=2, ensure_ascii=False) + "\n")
print("N3 + N2 result JSONs written")
