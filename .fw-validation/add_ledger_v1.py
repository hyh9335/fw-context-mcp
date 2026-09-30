import json
import io
from pathlib import Path

path = Path(r".fw-validation/case_ledger.json")
d = json.load(io.open(path, encoding="utf-8"))

ART = Path(r".fw-validation/artifacts/V1")
entries = []


def add(case_id, desc, status, evidence, platform="windows", exit_code=0):
    entries.append({
        "case_id": case_id, "phase": "V1_GOLDEN", "description": desc,
        "status": status, "platform": platform, "exit_code": exit_code,
        "evidence": evidence,
    })


add("G01_multi_tu_header", "Per-context ifdef-filtered bodies across 2 TUs x 2 contexts; no context collapse",
    "PASS", ["artifacts/V1/G01/G01.result.json (13/13 comparison checks: values 11/22 and 100/200 per context, "
             "2 lookup rows per symbol, explain_condition taken states, defines_varying=2)"])
add("G02_same_source_multi_command", "Same source, two compile actions -DMODE_VALUE=1/2: per-action macro values",
    "FAIL_PRODUCT", ["artifacts/V1/G02/G02.result.json (values_1_and_2_present=false)",
                     "findings F-V1-001: macro values stored in DB but unobservable on every query surface "
                     "(lookup/search_code/explain_symbol/export); macros.expanded_value self-inconsistent (value=1, expanded=2)"],
    exit_code=0)
add("G03_conditional_matrix", "3 contexts over #if/#elif/#else chain; helper wrapped in file-level #if",
    "FAIL_PRODUCT", ["artifacts/V1/G03/G03.result.json (6/7 cells pass; helper_in_two_of_three=false)",
                     "findings F-V1-002: symbols.source='' for helper in both active contexts (bodies of "
                     "conditionally-wrapped functions silently lost); selected bodies correct in all 3 contexts"])
add("G04_variant_semantic", "Variant semantics A/B/C via bare + [[build.variants]] + group_equivalent_variants/compare_variants",
    "FAIL_PRODUCT", ["artifacts/V1/G04/wsl/G04.result.json (equivalent_groups_AC_B=false, A_vs_B_callee_flip=false)",
                     "findings F-V1-003: bare generator writes ONE shared compile_commands.json (last variant wins); "
                     "compare_variants(A,B) asserts equal semantics for differing variants (WRONG_ANSWER surface); "
                     "get_source(variant=B) served inactive uart body from disk"],
    platform="wsl")
add("G05_cpp_override_template", "C++ override edges (incl. negative Other::run) + template instances",
    "PASS", ["artifacts/V1/G05/G05.result.json (Derived->Base override, decl-only reversed relation, "
             "twice<int>/twice<double> instances, forbidden edge absent)"])
add("G06_indirect_callbacks", "Function-pointer may-targets via initializers/assignments/registration",
    "FAIL_PRODUCT", ["artifacts/V1/G06/G06.result.json (8/9 cells pass; invoke_registered_may_plus1=false)",
                     "findings F-V1-004: transitive registration flow (setup->register_cb(plus1)->param cb->"
                     "registered) unresolved; direct initializer flows correct; opaque param negative holds"])
add("G07_dead_keep", "Dead-code reachability classes vs linker truth (WSL clang --gc-sections oracle)",
    "FAIL_PRODUCT", ["artifacts/V1/G07/G07.result.json (5/6 cells pass; live_functions_not_reported=false)",
                     "findings F-V1-005: main/live_root/live_leaf reported graph_unreachable — no root set possible "
                     "on compile_commands-only builds (manual backend never links, no config key for linker script)",
                     "WSL oracle: app.elf keeps kept_by_linker (KEEP honored), live chain present"])
add("G08_zephyr_async_stub", "Async stub: REGISTER evidence visible, NO invented direct CALL trigger->handler",
    "PASS", ["artifacts/V1/G08/G08.result.json (register_init_work_handler=true, no_invented_call_trigger_handler=true)",
             "observation: ASYNC_DISPATCH semantic edge absent (dispatch bridge entry z_work_q_main not in stub index; "
             "documented skip-silently behavior)"])
add("G09_freertos_async_stub", "FreeRTOS stub: SPAWN/REGISTER indirect evidence, queue object touched by both ends, "
    "no invented producer->consumer call",
    "PASS", ["artifacts/V1/G09/G09.result.json (all 4 checks true incl. queue_object_evidence)"])
add("G10_cmsis_vector", "Cortex-M vector table slot map from source initializer table",
    "FAIL_PRODUCT", ["artifacts/V1/G10/G10.result.json (8/8 handler slots exact; slot_map_exact=false)",
                     "findings F-V1-006: slot 0 (initial SP &__initial_sp) unresolved although refs table holds "
                     "the initializer ref (startup.c:13) — c-source table extraction only emits function words"])
add("G11_riscv_mtvec", "RISC-V mtvec negative: branch table must NOT be read as pointer table; handlers indexed",
    "PASS", ["artifacts/V1/G11/wsl/G11.result.json (no_pointer_table_interpretation=true, branch_handlers_indexed=true)",
             "observation: asm indexer stores no refs for branch targets (j) — not asserted by expected.json",
             "Windows run impossible: .S preprocessing needs clang binary (BLOCKED_ENVIRONMENT for win side)"],
    platform="wsl")
add("G12_raw_crlf", "Raw/byte-exact file contract (CRLF preserved), ifdef-filtered active view, directives mode",
    "PASS", ["artifacts/V1/G12/G12.result.json (raw_sha256_exact=true, raw_crlf_count_6=true, "
             "active_contains/excludes true, directives_exact=true)"])

existing_ids = {c["case_id"] for c in d["cases"]}
for e in entries:
    if e["case_id"] not in existing_ids:
        d["cases"].append(e)

d.setdefault("planned_required_case_ids", [])
for cid in ["G01_multi_tu_header", "G02_same_source_multi_command", "G03_conditional_matrix",
            "G04_variant_semantic", "G05_cpp_override_template", "G06_indirect_callbacks",
            "G07_dead_keep", "G08_zephyr_async_stub", "G09_freertos_async_stub",
            "G10_cmsis_vector", "G11_riscv_mtvec", "G12_raw_crlf", "G13_fanout_scale"]:
    if cid not in d["planned_required_case_ids"]:
        d["planned_required_case_ids"].append(cid)

json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger cases:", len(d["cases"]))
print("V1 statuses:", {c["case_id"]: c["status"] for c in d["cases"] if c["phase"] == "V1_GOLDEN"})
