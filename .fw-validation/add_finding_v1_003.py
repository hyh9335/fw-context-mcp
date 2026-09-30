import json
import io

path = r".fw-validation/findings.json"
d = json.load(io.open(path, encoding="utf-8"))

f = {
    "id": "F-V1-003",
    "phase": "V1_GOLDEN",
    "area": "variant-isolation",
    "severity": "CRITICAL",
    "summary": "G04 (bare build + [[build.variants]] A/B/C, defines []/USE_USB=1/UNRELATED=1): the bare generator writes ONE shared compile_commands.json at cc_output_path(root) regardless of variant (src/fw_context_mcp/indexer/builders/manual.py:188-189). Per-variant artifacts are not isolated, so the last generated variant's defines win and every variant is indexed from identical flags. Observed: 3 distinct config_hashes recorded, but transport_send groups as ONE equivalence group [A,B,C] with identical body fingerprint; compare_variants(A,B) reports edges_equal=true although variant B compiles the #ifdef USE_USB branch (transport_send must call usb_send, not uart_send) — the tool asserts semantic equality of semantics that differ (WRONG_ANSWER surface). get_source(name, variant='B') additionally served the INACTIVE uart-branch body from disk (source_origin='disk', line 6) as if live, with no stale warning — for variant B the live body is line 3 (usb_send). Feature combination documented as supported (docs/configuration.md:60 'works for every build system, not only Zephyr'; docs:80 variant build_dir default 'build/<name>' not honored by the bare generator).",
    "classification": "INDEX_DIVERGENCE",
    "status": "OPEN",
    "case": "G04_variant_semantic (run on WSL — Windows has no gcc/clang binary required by the bare generator)",
    "secondary": "Equivalence-grouping and compare_variants silently validate the collapsed data: group_equivalent_variants(app_entry) correctly one group, but the same tooling reports transport_send equivalent across A/B/C, so the defect is invisible to the product's own consistency checks.",
    "evidence": [
        ".fw-validation/artifacts/V1/G04/wsl/G04.result.json (equivalent_groups_AC_B=false, A_vs_B_callee_flip=false, A_vs_B.edges_equal=true)",
        ".fw-validation/artifacts/V1/G04/wsl/responses.json (get_source variant A vs B both 'return uart_send(x);'; group_equivalent_variants transport_send -> one group [A,B,C], 3 config_hashes)",
        "WSL ~/fwctx-v1-work/G04/work/.fw-context/build/compile_commands.json — single file, only '-D UNRELATED=1' (variant C, last generated); no per-variant compile DB exists",
        ".fw-validation/peek_g04_cc.sh output; src/fw_context_mcp/indexer/builders/manual.py:160,188-189 (shared cc_output_path, no variant dimension)",
        ".fw-validation/wsl_g04.py (full WSL reproduction: init -> init-variants add -> config defines -> index --build --variants A,B,C -> stdio MCP queries)"
    ],
    "repro": "wsl.exe -d Ubuntu-22.04 -e bash -c '~/fwctx-venv311/bin/python /mnt/d/github/fw-context-mcp/.fw-validation/wsl_g04.py'"
}
if f["id"] not in {x["id"] for x in d["findings"]}:
    d["findings"].append(f)
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings now:", [x["id"] for x in d["findings"]])
