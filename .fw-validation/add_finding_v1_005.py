import json
import io

path = r".fw-validation/findings.json"
d = json.load(io.open(path, encoding="utf-8"))
f = {
    "id": "F-V1-005",
    "phase": "V1_GOLDEN",
    "area": "dead-code-root-set",
    "severity": "HIGH",
    "summary": "G07 (dead/keep): find_dead_code correctly classifies the mutually-recursive dead_a/dead_b as graph_unreachable with scc=[dead_a,dead_b] (L2) and correctly protects kept_by_linker (used attribute root evidence; never definite dead — linker oracle on WSL proves KEEP honors it: kept_by_linker is a T symbol in app.elf linked with --gc-sections -T linker.ld). The failure: main, live_root, live_leaf — a plainly live call chain (ENTRY main -> live_root -> live_leaf) — are ALL reported status=graph_unreachable ('called only by other unreachable code'). Root cause: the root set requires ENTRY() from a build-attested linker script; the manual/compile_commands backend never links and its get_linker_scripts returns [] by design (src/fw_context_mcp/indexer/builders/manual.py:237-243), linker.ld is absent from the files table (only main.c is recorded), memory_regions has 0 rows, and no configuration key exists to declare a linker script (grep for linker_script/ld_script across docs = no matches). Generic external linkage is deliberately not a root, so on any compile_commands-only project the tool structurally cannot root main and misreports the live entry chain as unreachable — a WRONG_ANSWER surfaced directly to consumers of the dead-code report. Not documented in KNOWN_LIMITATIONS.md.",
    "classification": "WRONG_ANSWER",
    "status": "OPEN",
    "case": "G07_dead_keep",
    "secondary": "Oracle nuance recorded honestly: clang 14 at -O0 already drops unreferenced static functions (dead_a/dead_b absent from main.o), so the final-image evidence covers the live chain + kept_by_linker; the product's dead_a/dead_b SCC classification itself matches source-level truth. The misclassification of the LIVE chain is independent of that nuance.",
    "evidence": [
        ".fw-validation/artifacts/V1/G07/G07.result.json (live_functions_not_reported=false; 5/6 cells pass)",
        ".fw-validation/artifacts/V1/G07/raw/responses.json (find_dead_code rows: live_leaf/live_root/main status=graph_unreachable)",
        "WSL ~/fwctx-v1-work/G07/oracle/oracle_rigor.txt (nm main.o + nm app.elf: kept_by_linker T, live chain present, dead_a/b absent; .fw-validation/oracle_g07b.sh)",
        "src/fw_context_mcp/indexer/builders/manual.py:237-243 (never links, no linker scripts); G07 index DB: files=[main.c], memory_regions=0",
        "KNOWN_LIMITATIONS.md grep dead|entry|root|linker -> no relevant entry"
    ],
    "repro": "python .fw-validation/v1_runner.py G07  (Windows index; linker oracle: .fw-validation/oracle_g07b.sh on WSL)"
}
if f["id"] not in {x["id"] for x in d["findings"]}:
    d["findings"].append(f)
json.dump(d, io.open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("findings now:", [x["id"] for x in d["findings"]])
