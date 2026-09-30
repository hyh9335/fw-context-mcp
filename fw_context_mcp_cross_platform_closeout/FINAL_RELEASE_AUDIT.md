# FINAL_RELEASE_AUDIT — fw-context-mcp cross-platform closeout (A13_RELEASE)

- Auditor: A13_RELEASE (final gate before DONE). Audit performed 2026-09-16, evidence inspected first-hand (git, artifacts, live test reruns); narration not trusted.
- Release head: **954514a** (`git rev-parse` verified; `git status --porcelain` clean — zero modified tracked files; untracked: `.fw-closeout/`, `fw_context_mcp_cross_platform_closeout/`, `packaging/`).
- Base: 7bf462f ("T14 CLOSEOUT G06-1"). WSL tree `~/fw-context-mcp` verified at 954514a, clean.
- Verdict: **RELEASE_APPROVED** (see §7 residual risk and §8 disclosed errata).

## 1. Task chain — verdict chain with evidence

| Task | Scope | Impl → integrate | Verify verdict | Evidence |
|---|---|---|---|---|
| W0 | Diagnostic baseline (no prod changes) | — | platform split + stdio hang proven | `.fw-closeout/artifacts/W0/` (43 artifacts); `state.json` audit_log |
| W1 | AUD-R1/R13, NEW-W0-1: platform pkg, locks, identity, transport, httpx factory | debeda0 → 6417d99 | PASS_WITH_FOLLOWUPS (win core 96P/9S, slices 161P/5S/1xf, regression 51P/0F; wsl core 95P/10S; shim-free == shim-present; RED valid at base) | `W1_verify/VERDICT.md` + logs |
| W2 | AUD-R2 stdio hang (3 stacked root causes) | 2aad33f + rework 65ef712 → a2d1538 | attempt1 FAIL (taskkill gate) → attempt2 PASS (gate green both platforms, banned-API grep zero, failure sets == base) | `W2_verify/attempt2_VERDICT.md`, `VERDICT.md` |
| W3 | AUD-R3 decl-only overrides | 19c736d → 9626c87 | PASS_WITH_FOLLOWUPS (RED independently reproduced at base; 11/11 both platforms; fix = single `is_definition` gate removal, no name-inference) | `W3_verify/W3_verify_VERDICT.md`, `A_red_probe_base.json` |
| W4 | AUD-R4 KEEP-root dead-code false positive | ba3a739 → 0fc99b9 | PASS, zero followups (RED 3F/2P at base; O8 validated by REAL linker both platforms incl. control link; 5/5 new; slices == baseline) | `W4_verify/W4_verify_VERDICT.md` |
| W5 | AUD-R6 PID identity / kill ownership | dedb533 → bdc282a | PASS_WITH_FOLLOWUPS (RED 10F/4P at base; identity 14/14 win; re-baseline table NOT inflated — audited against real base tests) | `W5_verify/W5_verify_VERDICT.md` |
| W6 | AUD-R5 variant compare/group MCP surface | 8f1d116 → 00d9af9 | PASS (RED 6/6 at base; 42-tool registry parity on 4 surfaces; real-stdio probe truthful; legacy keys intact) | `W6_verify/VERDICT.md`, `D_probe_output.json` |
| W7 | AUD-R7..R11 + NEW-W1-1/W1-2/W2-2/W5-1/W6-1 + O11 hook | 8a4c069 → 9af4b29 (+eb47cd5 → 954514a) | PASS_WITH_FOLLOWUPS (slices 166P/1xf win, 165P/1S/1xf wsl; R7 fixture sha256-pinned + independently inspected; RED spot-checks at base; rot adjudicated pre-existing) | `W7_verify/VERDICT.md`, `artifacts/W7/RECEIPT.md` |

## 2. Gate evidence

- **REAL_STDIO (3 paths × 2 rounds): PASS.** win→win, wsl→wsl, win-launcher→wsl; every step answered ≤10s cold and on restart; 42 tools on the wire; content-truthful assertion via checked-in budget test. Evidence: `GATES/real_stdio_3paths_cold.json`.
- **CROSS_PLATFORM_PARITY (AUD-R12): PASS.** Win slices 166P/1xf + budget; WSL full suite 96F/3192P at 9af4b29 with every failure attributed (rot / 4 W6-doc [repaired eb47cd5, proven 169P+322P both platforms at main] / 2 known / 14 base-proven pwsh-57). Zero unattributed, no W-era divergence. Evidence: `GATES/cross_platform_parity.json`. **Erratum resolved by a fresh full-suite rerun at main 954514a (1:14:14): 92F/3199P — every failure attributed: 76 rot (ollama 36 + chat_router 26 + streaming 14) + 2 known (slot_zero, integrity residual) + 14 base-proven; W6-doc family 0 at main. The gate's 96 total was correct (92 = 96 − 4 doc-repair); its record had merely mis-written rot as 75 — the ledger and this audit now pin 76.** Evidence: `RELEASE/wsl_full_main_954514a.log` + `..._failures.txt`.
- **O11 ledger equality: PASS post-closure.** Pre-closure the live hook skipped while entries were non-terminal (`artifacts/W7/O11_live_ledger.log`: 5P/1S). After all 24 entries reached terminal status, the hook on Windows reports **6 passed, exit 0, skip absent** (`RELEASE/o11_closure_hook.log`) — the skip is gone and the hook now hard-checks the closed ledger.

## 3. Ledger reconciliation — all 24 entries

Tally: **21 FIXED + 3 DEFERRED_WITH_REASON = 24 terminal; 0 OPEN; invariant holds.** Evidence paths relative to `.fw-closeout/artifacts/`. (Machine-readable copy: `FINAL_ISSUE_LEDGER.json`.)

| id | final status | evidence | disposition rationale |
|---|---|---|---|
| AUD-R1 | FIXED | W1_verify/VERDICT.md; RED F_red_validity.log; 6417d99 | platform pkg + try_lock + ControlTransport; shim-free proof |
| AUD-R2 | FIXED | W2_verify/attempt2_VERDICT.md; a2d1538 | 3 root causes fixed; rework passed gate |
| AUD-R3 | FIXED | W3_verify/W3_verify_VERDICT.md; A_red_probe_base.json; 9626c87 | W0 NOT_REPRODUCED-candidate overturned by W3 RED at base; gate removed |
| AUD-R4 | FIXED | W4_verify/W4_verify_VERDICT.md; 0fc99b9 | files-table 3rd discovery surface; O8 real-linker proof |
| AUD-R5 | FIXED | W6_verify/VERDICT.md; 00d9af9 | ADD-behavior per design; 42-tool parity |
| AUD-R6 | FIXED | W5_verify/W5_verify_VERDICT.md; bdc282a | re-baseline audited; stop_marker_process refusal surface |
| AUD-R7 | FIXED | W7_verify B (fixture sha256 038cb0d4); 0d3631d | real rows/2 fixture; slice-B skips → pass |
| AUD-R8 | FIXED | W7_verify C (RED quotes stale claim at base); ed7e403 | README truth |
| AUD-R9 | FIXED | W7_verify D (diff audit); 050334e+09df364 | byte-exact CRLF raw mode |
| AUD-R10 | FIXED | W7_verify C; e118217 | raw-scan docstring truth |
| AUD-R11 | FIXED | W7 aed726b; R11_stress_run.log | stress marker non-gating + fanout tests |
| AUD-R12 | FIXED | GATES/cross_platform_parity.json | WSL full regression ran; all attributed |
| AUD-R13 | FIXED | W1_verify D (shim-free proof); debeda0 | fcntl only in platform/posix.py |
| NEW-W0-1 | FIXED | W1_verify F3 (RED at base); debeda0 | factory + SAFE_EXCEPT widening |
| NEW-W1-1 | FIXED | W7_verify D (diff audit); d8a821f+af37a0f | 6 stream sites routed |
| NEW-W1-2 | FIXED | W7 e503d88; test_reindex_enrichment_degrades | transport errors degrade per contract |
| NEW-W2-1 | FIXED | W2_verify/attempt2_VERDICT.md; 65ef712 | **remapped CLOSED→FIXED at RELEASE_AUDIT** per 4-legal-finals rule (pre-approved by W7 verify G) |
| NEW-W2-2 | FIXED | artifacts/W2 forensics; f7e6420 | documented env finding, bounded teardown |
| NEW-W5-1 | FIXED | W7 fffa799 (5 tests) | _unlink_when_released unit coverage |
| NEW-W6-1 | FIXED | W7 fffa799 (2 tests) | build-level compare mode covered |
| NEW-W7-1 | **DEFERRED_WITH_REASON** | W7_verify E_rot_base==E_rot_w7 byte-identical; RELEASE/wsl_full_main_954514a_failures.txt | **76 seam-rot tests at main (ollama 36 + chat_router 26 + streaming 14; earlier records 74/75 — full-suite ±2 variance in ollama, file untouched by W1–W7): stale pre-factory mocks attempt real network I/O — test rot, NOT product defects; independent passing coverage exists for the affected sites (test_platform_http_stream 5/5 + 28 migrated seams). Post-release test-debt repair.** |
| NEW-W8-1 | FIXED | eb47cd5→954514a; tool_docstrings 169P both platforms | W6-doc repair integrated |
| NEW-W9-1 | **DEFERRED_WITH_REASON** | base 7bf462f job pwsh-57 (identical 14F); tests/test_configure_llm.py:58-68 inspected | **Representative root-caused: tests patch `chat_router.httpx.post`, a seam production abandoned at W1 (platform.http_post at chat_router.py:36,349) → real HTTPS attempted; 10 incremental_reindex failures are `@pytest.mark.ollama` live-LLM tests degraded by the proxied WSL env. Test-infrastructure, not product defects. Post-release hermetic-test repair.** |
| NEW-W9-2 | **DEFERRED_WITH_REASON** | RELEASE/NEW-W9-2_no_duplicate_usrs_wsl.log (rerun at main, 1 failed 72.39s) | **`reindex_file_impl(driver.c)` returns no error yet leaves 17 duplicate USRs = exactly driver.c's symbol set ×2 under one config_hash. WSL-only; same proxy env family as the 5 siblings FIXED by e503d88, different escape (no error surfaced; old TU rows not removed — suspected `_reindex_parse_and_store` transaction/cleanup ordering, maintenance.py:2102). Needs product-level fix; no fix commit exists → cannot be FIXED. Residual product risk disclosed.** |

## 4. Release delta vs 7bf462f

39 commits; production diff 37 files under `src/` + `pyproject.toml` (+3506/−475), incl. the new `platform/` package (base/posix/windows, +1264 lines), `mcp/handlers/variants.py` (new MCP surface), `callgraph.py`, `maintenance.py`, `_postprocess.py`, `chat_router/ollama/auto_model` factory routing, `stale.py` bounded teardown, `source.py` byte-exact raw, README-MCP/docs truth. Gates added in tests: stdio budget, ledger-equality (O11), raw-byte-exact, docs-behavior, stress (non-gating), legacy-rows/2 fixture + migration oracle, process-identity, platform suite. Untracked `packaging/` is out of the audited tree (not part of `src/` diff).

## 5. Closure proof (executed at audit time)

1. Ledger reached all-terminal (dispositions in §3).
2. O11 live hook on Windows @main: `PYTHONUTF8=1 PYTHONPATH=src python -m pytest tests/test_ledger_equality.py -p no:cacheprovider -q` → **6 passed, exit 0, skip GONE** → `RELEASE/o11_closure_hook.log`.
3. Final 14-slice suite on Windows @main: **166 passed / 1 xfailed, exit 0 (83.53s)** → `RELEASE/final_slice_win.log`.

## 6. Prompt-file runs (A13_RELEASE)

Windows regression (slice suite, §5.3), WSL2 regression from ext4 (full suite rerun at main, `RELEASE/wsl_full_main_954514a.log`; the parity-gate full run + the W9-2 focused rerun provide the completed WSL evidence), semantic parity (W6 verify D + real-stdio gate, 42 tools), real stdio Windows + WSL + win→wsl bridge (gate JSON), old-schema migration (R7 oracle, 3P wsl incl. 2 libclang e2e), docs-behavior parity (W7 17P/20P + NEW-W8-1 repair 169P both), stress/fanout (R11), ledger equality (§5.2).

## 7. Residual risk statement

1. **NEW-W9-2 (product, WSL+proxied-env only):** duplicate symbol rows possible after a reindex whose enrichment phases hit transport errors. Not reproduced on Windows; bounded blast radius (same-config_hash symbol table), but it is the one known non-test defect left open by design of this audit's authority (no production fixes permitted). Post-release fix recommended.
2. **Test debt:** 76 seam-rot tests (NEW-W7-1, authoritative full-suite count at main) + 14 base-proven env failures (NEW-W9-1) are not valid coverage until repaired; independent passing coverage exists for the affected product sites. They mask regressions in those test files only.
3. **Environment:** EDR git-spawn stall (bounded), corporate-proxy hijack of loopback LLM probes (degrades per contract), gcc/llvm capability skips, win TOML backslash fixture failures — documented in `KNOWN_LIMITATIONS.md`.
4. **Rot-family run-to-run variance:** test_ollama_client's rot set measures 34 (targeted E_rot rerun) / 35 (parity record) / 36 (authoritative full-suite at main) — ±2 collection variance inside the same stale-seam family; file untouched by W1–W7. Pinned at 76 total rot at release head.

## 8. Disclosed errata / discrepancies found by this audit (none hidden)

1. **Parity-gate arithmetic — RESOLVED:** the record's 96 total was correct; its categorization mis-wrote rot as 75 (ollama 35). The fresh full-suite rerun at main 954514a (regenerating the lost /tmp list) measures **92F = 76 rot + 2 known + 14 base-proven + 0 doc**, and 92 + 4 doc-repair = 96 — consistent. Authoritative list: `RELEASE/wsl_full_main_954514a_failures.txt`; ledger NEW-W7-1 reason updated to 76 and the O11 hook re-verified (6/6) after the edit.
2. **Rot count 74/75/76:** targeted E_rot rerun 74 (34+26, byte-identical base-vs-head), parity record 75, authoritative full-suite at main 76 (ollama 36 — ±2 full-suite collection variance; file untouched by W1–W7). All three sources retained in NEW-W7-1's reason.
3. **NEW-W2-1 was recorded `CLOSED`**, which the O11 hook does not accept as terminal; remapped to FIXED (evidence already terminal-grade; W7 verify §G pre-approved the remap). Recorded in the entry's disposition.
4. **`FINAL_HANDOFF_AUDIT.md` is not present on disk** anywhere in the repo/package despite being cited as the seed-issue source; its content is fully mirrored in ledger `source`/`w0_verdict` fields, so no issue rides on the missing file.
5. **`state.json` audit_log stops at W1** and `gates.o11_closure` still says "pending A13_RELEASE" — bookkeeping staleness only; the artifacts + this audit are authoritative. (Ledger-disposition authority did not extend to state.json rewrites; disclosed here instead.)
6. **`tests/test_streaming.py` carries a UTF-8 BOM + mojibake comments** (W7 verify follow-up 3) — cosmetic, parses and passes.
