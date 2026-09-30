import json
import io

path = r".fw-validation/findings.json"
d = json.load(io.open(path, encoding="utf-8"))

new = [
    {
        "id": "F-V1-001",
        "phase": "V1_GOLDEN",
        "area": "macro-observability",
        "severity": "HIGH",
        "summary": "G02 (same source compiled twice with -DMODE_VALUE=1 / -DMODE_VALUE=2): the per-context macro values ARE stored in the index DB (macros table: two rows, value '1' context 1d405fa4c07e76fc, value '2' context 9655faf2257c623d, def_source=command_line) but NO supported consumer surface exposes them. lookup_symbol('MODE_VALUE', exact=True) returns the FUNCTION mode_value rows via relaxed prefix fallback (_fallback: true); search_code never reaches macros_fts because function hits short-circuit the pipeline; explain_symbol('MODE_VALUE') returns 'Symbol not found'; fw-context export contains no macro/defines data; search_content shows only the unexpanded token in body text. Net effect: a consumer cannot tell which compile action carries which value — the distinguishing semantic of the two contexts is silently lost while both contexts exist as rows.",
        "classification": "SILENT_DATA_LOSS",
        "status": "OPEN",
        "case": "G02_same_source_multi_command",
        "secondary": "macros.expanded_value is self-inconsistent for context 1d405fa4c07e76fc: value='1' but expanded_value='2' (clang -dM -E for -DMODE_VALUE=1 must expand to 1). Stored index data diverges from compiler truth (INDEX_DIVERGENCE evidence, same root cause area).",
        "evidence": [
            ".fw-validation/artifacts/V1/G02/G02.result.json (values_1_and_2_present=false, MODE_VALUE_macro_rows_2=false)",
            ".fw-validation/artifacts/V1/G02/raw/responses.json (lookup_symbol exact=true -> function rows with _fallback)",
            ".fw-validation/probe_g02_results.json (search_code/search_content/get_active_build/list_conditional_regions probes)",
            ".fw-validation/probe_g02b.py output: explain_symbol('MODE_VALUE') -> error 'Symbol not found: MODE_VALUE'",
            ".fw-validation/artifacts/V1/G02/raw/export.json (no macro data)",
            "Index DB C:/Users/h00969597/fwctx-validation-work/V1/G02/index/8eb36fccfe8c46d0b87bb7c46b54999b/index.db: macros table has the two correct value rows; macros_fts indexes them; query layer never returns them",
            "docs/tools.md:797-798 claims macros are extracted via clang -dM -E from compile_commands flags (includes -D); KNOWN_LIMITATIONS.md has no macro/command-line entry (grep empty)"
        ],
        "repro": "python .fw-validation/v1_runner.py G02  (runner: init+index via venv CLI, FW_CONTEXT_INDEX_DIR isolated, stdio MCP queries)"
    },
    {
        "id": "F-V1-002",
        "phase": "V1_GOLDEN",
        "area": "conditional-body-storage",
        "severity": "HIGH",
        "summary": "G03 (config.c with three -D contexts): a function defined inside a file-level conditional group that is TAKEN in that context stores an EMPTY body. symbols.source = '' for 'helper' (defined under #if !defined(DISABLE_HELPER)) in both contexts where the symbol exists (context_ids ae31c76ed0365596, ee7c6b4fa91cd95a), while the unwrapped function 'selected' stores correct per-context filtered bodies in all three contexts. search_bodies('helper') returns [] (no body text to match) although lookup_symbol finds 2 helper rows. Functions invisible to body search = silent data loss for any consumer relying on body text.",
        "classification": "SILENT_DATA_LOSS",
        "status": "OPEN",
        "case": "G03_conditional_matrix",
        "secondary": "Symptom scope: helper_in_two_of_three=false in G03 comparison; all other G03 cells pass (3 contexts, selected values 10/20/30 per context, condition texts A and B verified incl. elif branch_states).",
        "evidence": [
            ".fw-validation/artifacts/V1/G03/G03.result.json (helper_in_two_of_three=false, all other cells true)",
            ".fw-validation/artifacts/V1/G03/raw/responses.json (search_bodies helper -> {\"result\": []}; lookup_symbol helper -> 2 rows)",
            ".fw-validation/inspect_g03b.py output: symbols table helper rows src_len=0 vs selected rows with correct filtered bodies",
            "Index DB C:/Users/h00969597/fwctx-validation-work/V1/G03/index/*/index.db"
        ],
        "repro": "python .fw-validation/v1_runner.py G03"
    },
]
existing_ids = {f["id"] for f in d["findings"]}
for f in new:
    if f["id"] not in existing_ids:
        d["findings"].append(f)
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings now:", [f["id"] for f in d["findings"]])
