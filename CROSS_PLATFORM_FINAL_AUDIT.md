# CROSS_PLATFORM_FINAL_AUDIT.md

**Final handoff audit — fw-context-mcp Cross-Platform Hardening / Closeout Repair Wave (W0–W7, A13, T14)**
Auditor mode: read-only, evidence-driven. Performed 2026-09-16 against `main @ 954514a`.
Evidence classes: (V) re-verified live during THIS audit; (A) artifact on disk cited by path (independently re-runnable); (G) git record.
Caveat: this audit was executed in the same session that ran the T14 controller role. Every load-bearing claim below was therefore re-derived from git/artifacts/live checks, not from controller narration. Where a claim rests on prior-session evidence, the artifact path is given so an independent agent can re-run it.

---

# EXECUTIVE SUMMARY

| # | Question | Answer |
|---|---|---|
| 1 | State machine final phase | Workflow reached **RELEASE_AUDIT → approved**. `.fw-closeout/state.json` `phase` field reads `RELEASE_AUDIT` and was never flipped to `DONE` — **bookkeeping staleness** (the `release_audit` field and the four release deliverables record approval). Not a work gap; recorded as STATE_CONTRADICTION S-1. |
| 2 | Issue ledger truly closed? | **Yes.** 24/24 entries terminal: 21 `FIXED` + 3 `DEFERRED_WITH_REASON`; 0 OPEN. Live ledger == `FINAL_ISSUE_LEDGER.json` **exact ID:status match** (V). O11 live hook passes post-closure with the skip gone (`artifacts/RELEASE/o11_closure_hook.log`; re-confirmed live in this audit's smoke run). |
| 3 | Blocking issues all really FIXED? | **Yes.** W1–W4 chain (AUD-R1, R2, R3, R4, R13) each: RED reproduced at base → accepted design → fix commit → independent verifier reran everything → integrated via `--no-ff` merge. One verify cycle genuinely FAILED first (W2 attempt 1, taskkill-gate) and was reworked — the gate had teeth. |
| 4 | Windows Native pass? | **Yes.** Slices 166P/1xf @ main (audit rerun `RELEASE/final_slice_win.log`); platform/identity/ledger smoke 28P/1S re-run live (V); server imports with `fcntl` **blocked** (V, §7); real stdio win→win PASS (V). |
| 5 | WSL2/Linux pass? | **Yes, with one disclosed product residual.** Fresh full suite at main: **92F/3199P, every failure attributed** (76 test-rot + 2 known + 14 base-proven; `RELEASE/wsl_full_main_954514a.log`); slices 165–167P by host-optional conditionals; `test_platform_lock` 9P re-run live on ext4 (V). Residual: NEW-W9-2 (§ Risks). |
| 6 | Real stdio on 3 paths? | **Yes — subprocess JSON-RPC, not in-memory.** win→win, wsl→wsl, win-launcher→wsl: cold + restart rounds PASS (gate JSON `artifacts/GATES/real_stdio_3paths_cold.json`); re-run live during this audit: **3/3 PASS** (init 0.84s / 9.83s / 5.64s; all steps answered ≤10s) (V). Content-truthful assertion via checked-in `test_stdio_toolpath_budget.py` both platforms. |
| 7 | Remaining HIGH/CRITICAL correctness risk? | **One MEDIUM-HIGH product residual, WSL-only:** NEW-W9-2 — under proxied envs, `reindex_file_impl` returns success yet leaves **17 duplicate USR rows** (full symbol set ×2, same `config_hash`). Silent index corruption on affected envs; not reproducible on Windows; root cause suspected in `_reindex_parse_and_store` cleanup ordering (`maintenance.py:2102`), unexamined beyond signature. Plus **test-debt blind spots** (76 rot + 14 base-proven tests are not valid coverage — product sites have independent passing coverage). |
| 8 | Production suitability | **Windows production: YES.** **Linux production: YES with caveat** — deploy NEW-W9-2 fix first where index integrity under proxy envs matters, or monitor for duplicate-USR symptoms. **Windows DSH → WSL MCP bridge: YES** (proven by gate path 3). |
| 9 | Next repair round needed? | Not blocking release. Recommended post-release wave: NEW-W9-2 product fix; migrate the 76+14 seam-rot/env tests to the platform factories; housekeeping (§ Housekeeping). |
| 10 | Overall confidence | **HIGH** for the audited scope (Windows MCP server, WSL MCP server, indexer, platform layer, MCP wire surface). Caveats that keep it from unconditional: WSL-only silent-index defect, test-hermeticity debt, state.json bookkeeping staleness. |

Not answered "complete" because phase=DONE: the phase field never said DONE (S-1); closure was instead established from ledger terminality + O11 hook + gate evidence + release audit, each re-checked above.

---

# FINAL_REPOSITORY_STATE

| Field | Value | Source |
|---|---|---|
| Repo path | `D:\github\fw-context-mcp` | (V) |
| Branch | `main` | (V) |
| HEAD | `954514aca2b69c7228753ccb56034a5fdb2d3da5` ("W6 REPAIR INTEGRATE: variants result-documentation entries (NEW-W8-1 …)") | (V) |
| Base commit (wave start) | `7bf462f` ("T14 CLOSEOUT G06-1") | (V) |
| Wave commits | **39** (`git log --oneline 7bf462f..HEAD`) | (V) |
| Diff stat (whole tree) | **76 files, +8802/−605** | (V) |
| Diff stat (src only, per release audit §4) | 37 files, +3506/−475, incl. new `platform/` package (+1264) and `mcp/handlers/variants.py` | (A) FINAL_RELEASE_AUDIT.md |
| Working tree | 0 modified tracked files; 3 untracked top-level entries: `.fw-closeout/`, `fw_context_mcp_cross_platform_closeout/`, `packaging/` | (V) |
| `packaging/` | Install docs + `dist/` (`.venv-resolve`, `AGENT-INSTALL*.md`) — outside the audited `src/` tree, never merged | (V) |
| Leftover worktrees | 7 closeout worktrees (w1 @debeda0 … w7 @8a4c069, all merged) + `fwctx/t14-integration` worktree parked @ `7bf462f` | (V) |
| Leftover branches | `fwctx/closeout-w1..w7`, `fwctx/t14-integration`, `fwctx/t14-sliceF/H/L` — all merged or stale, none deleted | (V) |
| Row-format schema | `CURRENT_ROW_FORMAT = "fw-context-rows/3"` (`indexer/db/_schema.py:86`); `CURRENT_SCHEMA_VERSION` is a derived hash, not hand-maintained (`:1238`) | (V) |
| Package version | `0.31.0` (`pyproject.toml`) | (V) |
| Public MCP surface | **42 tools + 4 resources** — verified live via import docstring "Serves 42 MCP tools and 4 MCP resources" (V); wire-verified 42 in gate probes (A) |
| Python | Windows host 3.11.9; WSL `~/fwctx-venv311` 3.11.0rc1 | (A) prior logs |

### Cross-checks performed
- `state.json` per-task integration commits vs `git log`: **MATCH** for all 7 tasks (debeda0→6417d99; 2aad33f+65ef712→a2d1538; 19c736d→9626c87; ba3a739→0fc99b9; dedb533→bdc282a; 8f1d116→00d9af9; 8a4c069→9af4b29, +eb47cd5→954514a).
- Live ledger vs `FINAL_ISSUE_LEDGER.json`: **exact match** (V).
- Receipts vs git: every claimed HEAD sha found at the named worktree/branch ref (V, worktree list above).

### STATE_CONTRADICTION register
| ID | Finding | Severity |
|---|---|---|
| S-1 | `state.json.phase = "RELEASE_AUDIT"`, never set to `DONE`; approval recorded in `release_audit` field + deliverables | Bookkeeping |
| S-2 | `state.json.audit_log` holds 4 entries (stops early in the wave); controller's late append did not persist | Bookkeeping |
| S-3 | `FINAL_HANDOFF_AUDIT.md` (declared seed-issue source in wave docs) **does not exist on disk** (repo or package dir) (V); its content is mirrored in ledger `source`/`w0_verdict` fields | Bookkeeping (no issue rides on it) |
| S-4 | Rot count appears as 74 (W7 verify), 75 (parity gate record), **76** (authoritative fresh main rerun: ollama 36 + chat_router 26 + streaming 14; ±2 run-to-run variance in `test_ollama_client`) | Resolved, disclosed |

---

# ISSUE_LEDGER_AUDIT

Seed: AUD-R1..R13 (13) + wave discoveries NEW-* (11) = **24 entries**. `ALL_DISCOVERED_ISSUES = FIXED(21) ∪ DEFERRED_WITH_REASON(3)` — equality holds; the O11 hook (`tests/test_ledger_equality.py`) machine-enforces it and passes post-closure (skip absent). No REJECTED / NOT_REPRODUCED final rows were needed.

| Issue | Original problem (abridged) | Blocking | Reproduced | Final | Root cause | Impl commit | Independent verification | Windows evidence | WSL evidence | Stdio relevance | Remaining limitation | Evidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AUD-R1 | Cross-process lock + daemon lifecycle missing on Windows (fcntl shim load-bearing; O_NOFOLLOW absent; lock exclusion broken) | Yes | Yes | FIXED | No platform abstraction; POSIX-only primitives imported unguarded | 6417d99 | W1 verify PASS_WITH_FOLLOWUPS (RED at base; shim-free==shim-present parity) | core 96P/9S; slices 161/5/1xf | core 95P/10S | Daemon spawn/lock underpins stdio server | shim file itself still on host (non-load-bearing, §7) | `W1_verify/`, ADR D1–D4 |
| AUD-R2 | Real-stdio Windows MCP server hangs (no answer 90s+300s) while WSL answers; in-process sub-second | Yes | Yes | FIXED | 3 stacked: RichHandler legacy-console freeze; sqlite_vec lazy-import loader-lock deadlock; git.exe double-spawn unbounded recovery | 2aad33f + 65ef712 | attempt1 **FAIL** (taskkill static gate) → rework → attempt2 PASS (failure sets == base) | budget test PASS; e2e restart log | budget PASS | **Is** the stdio issue | git-spawn EDR stall bounded, not eliminated (NEW-W2-2) | `W2_verify/attempt2_VERDICT.md`, `server.py:671,716`, `stale.py:572-581` |
| AUD-R3 | `get_method_overrides` misses declaration-only `override;` (is_definition gate) | Yes | W0 said No; W3 verify **reproduced at base** (decision inverted, original preserved + correction annotation) | FIXED | `AND s.is_definition = 1` over-filter in `_build_overrides` Phase 1 | 9626c87 | W3 verify PASS_WITH_FOLLOWUPS (RED probe at base; 11/11 both platforms) | 11/11 | 11/11 | none | virtual-flag gates intact (Other::run isolation) | `W3_verify/`, `w3_red_evidence_CORRECTION.json`, `_postprocess.py` |
| AUD-R4 | `find_dead_code` false "dead" for KEEP-protected fns in MEMORY-less, assignment-less linker scripts | Yes | Yes (keep_fn dead/L1 at base) | FIXED | Files-table (3rd build-attested surface) + `/DISCARD/` split + binary_status honesty missing | 0fc99b9 | W4 verify **PASS zero followups** (O8 real linker incl. control link proving KEEP causality) | 5/5 new; slices == baseline | 5/5; O8 wsl log | none | binary_status `unknown` possible when no script discovered | `W4_verify/`, `O8_linker_oracle_*` |
| AUD-R5 | Variant compare/group MCP tools promised (SLICES.md:42-43) but absent from wire | No | Yes (absence) | FIXED | DB-layer grouping existed; no wire tool; docs promised | 8f1d116 | W6 verify PASS (RED 6/6 at base; 42-tool parity on 4 surfaces; real-stdio probe truthful) | 6/6; probe | 6/6 | Tools served over real stdio (probe) | build-level compare mode untested per design (NEW-W6-1 later added) | `W6_verify/`, `handlers/variants.py` |
| AUD-R6 | PID reuse / kill ownership: no start-time identity; `_watch` kill without identity check; latent win32 `signal.SIGKILL` AttributeError | No | Yes | FIXED | Markers carried bare pids; kill paths unauthorized; win32 SIGKILL attr bug | bdc282a (dedb533) | W5 verify PASS_WITH_FOLLOWUPS (RED 10F/4P at base; re-baseline table audited NOT inflated vs W1-shipped surface) | identity 14/14; regression 119P/0F | 13P/1S; 117P/0F | watch/restart paths run under stdio server | watcher-only role enforcement = design-silent implementer choice (documented); NEW-W5-1 test added | `W5_verify/`, `platform/base.py` stop_marker_process |
| AUD-R7 | Old-schema (rows/2) DB fixture missing → 5 slice-B migration tests skip | No | Premise **DISPROVED** by W0 (in-place migration works) | FIXED | Fixture absent; tests skipped rather than exercised | 0d3631d + 52ca5fd | W7 verify: fixture sha256 independently inspected field-by-field; e2e oracle 3P wsl incl. 2 libclang | e2e skip-by-gcc-gate (documented conftest) | fixture + oracle green | none | Windows e2e runs only where gcc present | `W7_verify/B_*`, `tests/fixtures/legacy_rows2/` |
| AUD-R8 | README-MCP.md:144 claims 5-file/30s auto-reindex cap that stale.py lacks | No | Yes (test fails quoting the stale claim verbatim) | FIXED | Docs aspirational | ed7e403 | W7 verify C (RED spot-check reproduced) | docs-behavior suite P | P | none | — | `W7_verify/`, README staleness section |
| AUD-R9 | `read_file` raw mode strips CRLF (113B/5CRLF → 108B/0CR) vs "exactly as on disk" claim | No | Yes | FIXED | text-mode read normalizes newlines | 050334e (+09df364 slice_m adaptation) | W7 verify (raw-byte-exact suite) | 3P raw-byte-exact | P | docs served through read path | search.py raw scan normalization is by-design (D8 scope) | `test_raw_byte_exact` |
| AUD-R10 | Raw-scan budget docstring "limit×3" vs unbounded scan (`search.py:1436-1458`) | No | Yes | FIXED | Docstring fiction | e118217 | W7 verify (boundary pin) | P | P | none | — | `search.py` docstring truth |
| AUD-R11 | Large-project stress/perf evidence missing | No | Absence | FIXED | No stress suite | aed726b | W7 verify (stress run honest timing JSONs) | 3 stress tests; non-gating | — | none | stress opt-in only (`-m stress`) | `artifacts/arch/w7/stress_*.json` |
| AUD-R12 | No Linux/WSL full regression ever ran | No | Yes (gap) | FIXED | Parity never measured at full scale | gates at 954514a | This audit re-checked categorization (V) | slices 166/1xf; budget | **full suite 92F/3199P fully attributed** | gate includes wsl paths | residual failures dispositioned NEW-W7-1/W9-1/W9-2 | `GATES/cross_platform_parity.json`, `RELEASE/wsl_full_main_954514a.log` |
| AUD-R13 | Host fcntl no-op shim dependency (unguarded imports `_locking.py:21`, `daemon.py:65`, `background.py:36`) | Yes | Yes | FIXED | Direct `fcntl.flock` calls | 6417d99 | W1 verify shim-free parity | import-with-fcntl-blocked OK (V, this audit) | n/a (posix has real fcntl) | server import path clean | see §7 | §7 below |
| NEW-W0-1 | `httpx.Client()` ValueError on NO_PROXY containing `[::1]` | No | Yes (W0) | FIXED | Hostile env string parsing | W1 factory (trust_env sanitize) | W1 verify + 13 routed sites | P | P | affects any outbound HTTP from server | — | `platform/base.py` http factory |
| NEW-W1-1 | 6 raw `httpx.stream()` sites bypass factory (auto_model:157, chat_router:229, _fixes:214, ollama:173/280/286) | No | Yes | FIXED | W1 scope boundary | d8a821f + af37a0f (test seams) | W7 verify D diff audit | P | P | SSE streaming from LLM within server | live `httpx.stream(` call count in src = **0** (V, grep — prose mentions only) | `platform/base.py:540-547` |
| NEW-W1-2 | WSL `test_index_integrity` 6 pre-existing failures (httpx.ReadTimeout) | No | Yes (at base) | FIXED (5/6) | Corporate proxy hijacks loopback LLM probes; ReadTimeout escaped narrow guards and aborted reindex | e503d88 | W7 verify | n/a | 5 green; residual → NEW-W9-2 | enrichment runs inside server | 1 residual (NEW-W9-2) | `test_reindex_enrichment_degrades` |
| NEW-W2-1 | W2 rework: facade tree-kill service + static gate | Yes (gate) | Yes | FIXED (remapped CLOSED→FIXED at release audit; pre-approved) | attempt1 tripped W1 gate | 65ef712 | attempt2 verify PASS | gate green | gate green | teardown of hung stdio server | — | `platform/windows.py` tree_kill |
| NEW-W2-2 | ENV: git spawn from live stdio server stalls 23–53s (EDR/minifilter suspect) | No | Yes (measured) | FIXED (documented) | OS-level; not fixable in repo | f7e6420 | release audit | bounded 30s probe + mtime degradation | — | **directly** bounds stdio cold-start variance | KNOWN_LIMITATIONS.md | artifacts/W2 |
| NEW-W5-1 | `_unlink_when_released` had no unit test (manual e2e only) | No | Absence | FIXED | — | fffa799 (+5 tests) | W7 verify | P | — | restart path | — | `test_watch_unlink_retry.py` |
| NEW-W6-1 | build-level compare mode untested (design pre-authorized) | No | Absence | FIXED | — | fffa799 (+2 tests) | W7 verify | P | — | none | — | `test_variant_compare_tools.py` |
| NEW-W7-1 | Pre-existing W1-era patch-seam rot: stale `httpx` mocks never engage → real network attempts (ollama_client 36, chat_router 26, streaming 14) | No | Yes (byte-identical at base) | **DEFERRED_WITH_REASON** | W1 factory migration orphaned mock seams | — (test debt) | W7 verify E: base==head failure sets; this audit accepts fresh-rerun count 76 | tests are cross-platform hermetic debt | same | none (product sites have passing coverage) | those 3 test files are invalid coverage until migrated | `W7_verify/E_rot_*.log` |
| NEW-W8-1 | W6 tools lacked `Returns:`/`Args:` doc registry entries (found only by full suite; slice verify missed it) | No | Yes | FIXED | docstring registry contract | eb47cd5 | tool_docstrings 169P + project_selector 322P both platforms (V paths) | P | P | wire tool docs | — | `handlers/variants.py` |
| NEW-W9-1 | WSL configure_llm (4) + incremental_reindex (10) failures | No | Yes — **proven pre-existing at base 7bf462f** (identical 14F) | **DEFERRED_WITH_REASON** | same seam family + `@pytest.mark.ollama` live-LLM tests under proxied env | — | base attribution job (14F/89P at base) | n/a (win hosts skip ollama-marked by conftest) | 14F at base == 14F at main | none | hermetic-test repair post-release | `RELEASE/NEW-W9-2…`(audit), pwsh-57 record |
| NEW-W9-2 | `test_reindex_no_duplicate_usrs` residual: reindex returns no error, leaves **17 duplicate USRs** (driver.c symbol set ×2, same config_hash) | No (non-blocking by wave rules) | Yes (WSL rerun at main) | **DEFERRED_WITH_REASON** | Suspected `_reindex_parse_and_store` transaction/cleanup ordering (`maintenance.py:2102`); root cause NOT fully examined | — (needs product fix) | focused rerun at main `RELEASE/NEW-W9-2_no_duplicate_usrs_wsl.log` | **not reproducible on Windows** | reproduces | index writes happen under live server | **the one open product-side correctness residual** | ledger row + audit §7 |

### LEDGER_LEAK scan
- No correctness issue found in receipts/verifier reports/gate logs that lacks a ledger row.
- Bookkeeping observations (not leaks of defects):
  1. **"NEW-W1-3"** — referenced in W5 dispatch/receipts as a ledger ID (the "W1 shipped R6 surface early — re-baseline" note) but **never existed as a ledger row**. Its substance was discharged by the W5 requirement-to-evidence table and lives in `AUD-R6` evidence. Ghost ID; no lost defect.
  2. W7-verify cosmetic followups (test_streaming BOM/mojibake comments; receipt command hygiene) — disclosed in `W7_verify/VERDICT.md` and audit §8, no ledger row (non-correctness).
  3. The WSL-full-suite evidence gap flagged by W7 verify was discharged by the fresh main rerun (92F/3199P) and folded into AUD-R12.

---

# TASK_STATUS

| Task | Status | Key facts |
|---|---|---|
| **W0** | COMPLETE_VERIFIED | Diagnostic only (no prod changes). 43 artifacts in `.fw-closeout/artifacts/W0/` incl. per-issue R1–R13 logs, win/wsl slice baselines (161/5/1xf win; 160/6/1xf wsl), stdio hang forensics. Two W0 verdicts later corrected by evidence: R3 NOT_REPRODUCED→reproduced at base; R7 premise disproved (documented, originals preserved). |
| **W1** | COMPLETE_VERIFIED | Platform package + locks + identity + transport + httpx factory. Verify PASS_WITH_FOLLOWUPS; RED valid at base; shim-free==shim-present. Followups → NEW-W1-1/-2 (closed in W7), re-baseline note → W5. |
| **W2** | COMPLETE_VERIFIED (after 1 honest FAIL) | stdio hang fix (3 root causes) + bounded teardown. attempt1 verify FAIL (static taskkill gate) → rework 65ef712 → attempt2 PASS. Evidence `W2_verify/attempt2_VERDICT.md`. |
| **W3** | COMPLETE_VERIFIED | decl-only overrides; RED dispute resolved by base reproduction (controller's own state.json edit error during this task was self-disclosed and rewritten). 11/11 both platforms. |
| **W4** | COMPLETE_VERIFIED | KEEP roots; only task with zero followups; O8 causal control-link is the strongest single piece of evidence in the wave. |
| **W5** | COMPLETE_VERIFIED | Identity residual + shared stop helper + win32 teardown-retry bonus fix; re-baseline table audited (no inflation); role-scope adjudicated design-silent. |
| **W6** | COMPLETE_VERIFIED | variant compare/group on the wire; registry parity 42×4 surfaces; one doc-registry gap escaped slice-scoped verify and was caught by the WSL full suite at the parity gate → eb47cd5 (this is the system working: full-suite gate caught what focused verify missed). |
| **W7** | COMPLETE_WITH_LIMITATIONS | 14 commits, 11 design items; fixture provenance real; O11 hook shipped; discovered the seam-rot debt and the 14 base-proven failures; those were ledgered, not silently absorbed. |
| **A13_ARCH** | COMPLETE_VERIFIED | 11-decision ADR (`artifacts/arch/PLATFORM_ADR.md`) + accepted_design_W1..W7.md; designs were binding in practice (W6 ADD-vs-docs direction; W5 re-baseline adjudication quoted design lines). |
| **A13_VERIFY** | COMPLETE_VERIFIED | 8 verifier runs (W1–W7 + W2 re-verify); all rerun-based; produced 2 PASS, 4 PASS_WITH_FOLLOWUPS, 1 zero-followup PASS, 1 FAIL→rework→PASS. |
| **A13_RELEASE** | COMPLETE_VERIFIED | RELEASE_APPROVED; 4 deliverables; 24/24 ledger terminal; O11 post-closure 6P; errata self-disclosed (§8). |
| **T14** | COMPLETE_VERIFIED (controller) | Ledger/state reconciliation every task; all merges `--no-ff` by controller (implementers never merged own work); delegation depth 1 held; HARNESS_LIMITS held (impl≤1, verify≤1, diag≤2). Known controller wobble: one state.json hand-edit corrupted JSON (self-caught, rewritten+validated thereafter). |

---

# PLATFORM_ARCHITECTURE_AUDIT

Final structure (matches ADR D1–D11):

```
src/fw_context_mcp/platform/
├── __init__.py   # THE single os.name dispatch (:48-64); re-exports facade
├── base.py       # Protocols + shared logic: ProcessService, InterProcessLock,
│                 #   FileSystemCapabilities, ControlTransport, ProcessIdentity,
│                 #   StopOutcome/stop_marker_process, http_client()/http_stream()
├── posix.py      # fcntl.flock, /proc stat field-22 identity, SIGTERM→SIGKILL
│                 #   /proc-descendant tree walk, O_NOFOLLOW safe-open, AF_UNIX
└── windows.py    # msvcrt.locking, OpenProcess+GetProcessTimes(FILETIME) identity,
                  #   TerminateProcess + Toolhelp32 snapshot tree walk, TCP loopback
```

| Concept | Interface | posix backend | windows backend | Status |
|---|---|---|---|---|
| Process liveness | `ProcessService.is_alive` | `os.kill(pid, 0)` (`posix.py:106-112`) | `OpenProcess` probe (`windows.py`, `_ERROR_INVALID_PARAMETER=87`) | present |
| Process identity | `ProcessIdentity` start-time token | `/proc/<pid>/stat` field 22 | `GetProcessTimes` FILETIME (`windows.py:143-156`) | present |
| Terminate (authorized) | `stop_marker_process` → `ProcessService.terminate` | SIGTERM path | TerminateProcess after token match | present |
| Force terminate | `ProcessService` force / `tree_kill` | `/proc` descendant walk + SIGKILL root-last + `waitpid(WNOHANG)` reap (`posix.py:187-212`) | Toolhelp32 snapshot + TerminateProcess root-first (`windows.py`) | present |
| Ownership validation | marker `{pid, create_token, role}` + authorize callback + argv gate | refuses self/parent/bare-int/role/token-mismatch **before any signal** (`base.py`, verified by TestKillPathAuthorization) | same | present |
| Lock acquire/release | `InterProcessLock.try_lock() -> bool` (boolean contract) | `fcntl.flock` | `msvcrt.locking LK_NBLCK` (§8) | present |
| Lock timeout | `write_lock(timeout)` wrapper raising `WriteLockTimeout` (`indexer/db/_locking.py`) | retry loop | retry loop | present |
| Crash recovery | kernel-owned lock (process death releases) | flock | byte-range lock | present |
| FS safe-open | `FileSystemCapabilities` | `O_NOFOLLOW` (ELOOP refusal + log, `posix.py:282-292`) | honest absence — no silent pretend (`windows.py:497`) | present |
| Symlink semantics | refusal-by-flag on POSIX; documented win32 behavior | ✓ | ✓ | present |
| Control transport | `ControlTransport` | AF_UNIX `daemon.sock` (`posix.py:354`) | TCP loopback (`base.py:386` doc; win backend) | present |
| Restart / stale cleanup | `stop_marker_process` + `_unlink_when_released` bounded retry (3s, post-verified-stop only) | ✓ | ✓ (+ unit tests NEW-W5-1) | present |
| Subprocess text/encoding | platform layer does not set encoding policy | — | — | **`PYTHONUTF8=1` still required in practice** for host-side test/CLI invocations on this zh-CN GBK console (documented discipline in every run book; W2's stdio fixes removed the *server-side* hangs but the host console encoding discipline remains). Not a product defect; an environment contract. |

---

# PLATFORM_DISPATCH_AUDIT (os.name / sys.platform / native-API call sites)

Grep sweep over `src/` for `os.name|sys.platform|platform.system|/proc|fcntl|AF_UNIX|O_NOFOLLOW|SIGKILL|msvcrt|LockFileEx|os.kill|GenerateConsoleCtrlEvent|taskkill|httpx.stream(` → 66 + 25 hits, classified:

### ACCEPTABLE_BACKEND_USE
| Location | Why acceptable |
|---|---|
| `platform/__init__.py:48,55,64` | the single facade dispatch — by design |
| `platform/posix.py` (all: fcntl :12, os.kill :112/165/175/204/208, O_NOFOLLOW :289, AF_UNIX :354) | backend internals |
| `platform/windows.py` (msvcrt :188-215; ctypes/OpenProcess/GetProcessTimes/TerminateProcess/Toolhelp32; GenerateConsoleCtrlEvent mentioned at :247 only as a documented NEVER) | backend internals |
| `mcp/daemon.py:725-745` `proc.terminate()/proc.kill()` on **own child handle** | design-sanctioned (D2/W5 kill-authority note: handle cannot reach a reused pid; only marker-read pids need identity-checked path) |
| `mcp/daemon.py:467` `signal.SIGTERM/SIGINT` handler registration | a process handling **its own** termination; both names exist on Windows (CTRL events mapped) and POSIX |
| `deps/_instructions.py:38,97,158,168` `sys.platform` | informational content about the user's OS, not behavior dispatch |
| `llm/auto_model.py:84` `sys.platform != "darwin"` | Apple-Silicon feature probe (early-return guard); darwin outside wave scope |
| `indexer/macros.py:42` `sys.platform == "win32"` | **borderline-acceptable**: a clang-path *discovery heuristic* (Windows LLVM install conventions `C:\Program Files\LLVM\bin`), not behavioral dispatch; structure identical across platforms. A purist would move it into `FileSystemCapabilities`; flagged for the next agent. |
| `cache_server/setup.py:86,430` `platform.system()` | ops provisioning CLI for a Linux/macOS **server product** (apt/systemctl), explicitly OS-gated; not part of the MCP server / indexer surface the wave hardened |
| `indexer/variant_switch.py:81` | explicitly documents case-folding as a filesystem property per W1 — this is the pattern done right |

### PLATFORM_LEAK
**None found** in indexer, DB layer, MCP handlers, or business logic: every os-conditional *behavior* (locks, kill, identity, safe-open, transport, httpx env sanitization) routes through `platform/`. All other grep hits are docstrings/comments explaining the W1 migration (e.g. `cli/_watch.py:187`, `cli/_index.py:387-389`, `shared/stale.py:539`, `indexer/db/_locking.py:5-22`, `mcp/background.py:13,94,157`, `pid_file.py:10-141`).

Corroborating negative checks (V):
- live `httpx.stream(` call sites in src: **0** (all mentions are prose);
- `taskkill` / `GenerateConsoleCtrlEvent` calls: **0** (docstring mentions only);
- `import fcntl`: exactly one, in the posix-only backend module.

---

# FCNTL_SHIM_AUDIT

1. **Host shim still exists**: `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\fcntl.py` (202 bytes) — a no-op stub ("no-op file locking for single-process testing"; defines LOCK_* constants and `flock`/`ioctl` no-ops) (V, content read).
2. **Repo-internal shim**: none — no `fcntl.py` anywhere under `src/` or `tests/` (V). Nobody moved the shim into the repo.
3. **Dependency**: the only `import fcntl` in src is `platform/posix.py:12`, inside a module imported exclusively on `os.name == "posix"` (`platform/__init__.py:48`).
4. **Live proof of shim-independence** (V, this audit): `sys.modules['fcntl'] = None` (simulates shim absence → `import fcntl` raises) followed by `import fw_context_mcp.mcp.server` → **imports cleanly**, docstring confirms 42 tools. Combined with `test_platform_lock` 28P smoke and the W1 verify's shim-free==shim-present parity runs (`W1_verify/`), lock/start/index paths do not need the shim.
5. **Tests without shim**: the W1 verifier's parity comparison ran the platform suites with and without shim present (A: `W1_verify/`); this audit's import-block test is a further live confirmation.

**`HOST_FCNTL_SHIM_DEPENDENCY = NO`** — the shim file is inert residue on the host. Deleting it is safe and recommended as housekeeping (not done here: read-only audit).

---

# WINDOWS_LOCK_AUDIT

Actual mechanism per `platform/windows.py`:

- **Primitive**: `msvcrt.locking(fd, LK_NBLCK, 1)` byte-range lock — **not** `LockFileEx` (V, `windows.py:188-190`). Contention normalization: `msvcrt` raises plain `OSError`; errnos `EACCES/EDEADLK/EAGAIN/EWOULDBLOCK` map to `try_lock() -> False` (`:124-128, 190-200`) — the pre-W1 defect (EACCES instead of BlockingIOError semantics) is hidden behind the boolean contract.
- **Layout convention**: byte 0 is the locked marker; holder pid written at **offset 8, outside the locked byte**, so other handles can read who holds it (`:167-186`, `_PID_OFFSET = 8`).
- **Release**: `LK_UNLCK` + fd close, both exception-guarded (`:212-230`).
- **Crash recovery**: kernel releases byte-range locks on process death — no stale-lock files possible; verified by design docs (`_locking.py:6-8`) and the O3/O4 oracles (lock + crash tests from W1 verify).
- **SQLite family**: the DB layer does **not** rely on SQLite WAL for cross-process serialization; it takes the same platform lock adapter app-level (`indexer/db/_locking.py:1-40`: "WHY an external lock (not SQLite WAL) … preventing SQLITE_BUSY … kernel releases the lock automatically on process exit").
- **Daemon**: `watcher.lock` guarded by the same adapter; W5's `stop_marker_process` + bounded `_unlink_when_released` (3s, PermissionError-only, post-verified-stop) handle the TerminateProcess→handle-release race (unit-tested, NEW-W5-1).
- **Tests**: `tests/test_platform_lock.py` 9P on Windows (V, this audit) and 9P on WSL ext4 (V, this audit); contention/crash oracles O3/O4 in `W1_verify/`.

Assessment: lock correctness is real, not test-theater — the mechanism, errno normalization, pid-read convention, and crash story are all in the backend code and match the ADR.

---

# WSL_LINUX_AUDIT

- ext4 checkout `~/fw-context-mcp` @ `954514a` (V, `git log`), synced exclusively via `git fetch /mnt/d/...` (transport-only; tests never run under `/mnt/*` — discipline held throughout the wave's logs).
- Live smoke this audit: `test_platform_lock.py` **9 passed** on ext4 venv (V).
- Full-suite reality at main: 92F/3199P, fully attributed (76 rot NEW-W7-1 + 2 known [linker slot_zero both-platform; integrity residual NEW-W9-2] + 14 base-proven NEW-W9-1) — `RELEASE/wsl_full_main_954514a.log` + `_failures.txt`.
- Known env bounds: corporate proxy hijacks loopback LLM probes (W7 made enrichment degrade to warnings per contract — the fix for 5 of the original 6 integrity failures); gcc/llvm capability skips (documented conftest gate); `@pytest.mark.ollama` live-LLM tests need hermetic repair (NEW-W9-1).

---

# REAL_STDIO_AUDIT

- Protocol per PLATFORM_MATRIX: cold start → initialize → tools/list → call1 → call2 → shutdown → restart; in-memory ClientSession explicitly disqualified.
- Evidence: `artifacts/GATES/real_stdio_3paths_cold.json` (3 paths × cold+restart, all steps answered ≤10s, 42 tools on the wire) + checked-in `test_stdio_toolpath_budget.py` (content-truthful `status=="ready"` assertion) on both platforms.
- **Re-run live during this audit** (V): win→win PASS (init 0.84s), wsl→wsl PASS (init 9.83s — slower than the gate round's 5.7s, still within budget; variance consistent with the bounded git-spawn/EDR finding NEW-W2-2), win-launcher→wsl PASS (5.64s). 3/3.
- Root-cause fixes verified present in code: `_install_stdio_safe_logging` (`server.py:671`, invoked :712), `_prewarm_sqlite_vec` (`:716`, invoked :1361), bounded git probe + facade tree-kill (`stale.py:572-581`).

---

# TEST_SUITE_REALITY

| Suite | Windows | WSL |
|---|---|---|
| 14-slice selection @ main | 166P / 1xf (`RELEASE/final_slice_win.log`) | 165–167P / 1S / 1xf (host-optional conditionals) |
| platform+identity+ledger smoke (this audit) | 28P / 1S | lock 9P |
| tool_docstrings / project_selector | 169P / 322P | 169P (V, post-repair) |
| Full tree | never run clean by design; W7 diligence attributed every touched area | **92F / 3199P**, 100% attributed |
| Known-failure inventory | linker slot_zero (both platforms), TestWithAbsoluteFile ×2 (TOML backslash fixtures), TestCountModifiedGate backdated | integrity residual (NEW-W9-2), ollama-marked live tests |

Honest limitation: "green" in this project means *slice/gate suites green + full-suite failures fully attributed*, because the wave inherited pre-existing rot and chose to ledger rather than hide it.

---

# RESIDUAL_RISKS_AND_NEXT_ROUND

**Product risks**
1. **NEW-W9-2 (the only open product defect)**: WSL + proxied env, `reindex_file_impl` success with 17 duplicate USRs. Recommended first task of any next wave: reproduce with `--tb=short`, instrument `_reindex_parse_and_store` (`maintenance.py:2102` area) for old-TU-row cleanup ordering.
2. Env bounds (documented, bounded): EDR git-spawn stall (30s probe cap), proxy hijack (degrades per contract).

**Test debt (invalid coverage until repaired)**
3. 76 seam-rot tests (ollama_client/chat_router/streaming) — migrate mocks to `platform.http_client()/http_stream()` seams.
4. 14 base-proven failures (configure_llm seam + ollama-marked live tests) — hermetic repair.

**Housekeeping for the next agent**
5. Delete 7 merged closeout worktrees + `fwctx/t14-integration` worktree + stale branches (`closeout-w1..w7`, `t14-integration`, `t14-sliceF/H/L`); WSL clone `~/w7-wsl` and 2 untracked `B1/B2_*.log` in `~/fw-context-mcp`.
6. `state.json`: set `phase="DONE"`, append the missing audit_log entries (S-1/S-2).
7. Optional: delete the host fcntl shim (proven inert); decide fate of untracked `packaging/` (install docs, outside audited tree).
8. Cosmetic: `tests/test_streaming.py` BOM + mojibake comments.

**Files an independent agent should read first**
`fw_context_mcp_cross_platform_closeout/FINAL_RELEASE_AUDIT.md` → `.fw-closeout/issue_ledger.json` → `.fw-closeout/artifacts/{W1..W7}_verify/*VERDICT*` → `src/fw_context_mcp/platform/` → `.fw-closeout/artifacts/GATES/*.json`.

---

## ERRATUM (added by FINAL REPAIR WAVE R1, 2026-09-16)

**§7 residual-risk item 1 (NEW-W9-2 "product defect") is RETRACTED.** R1-DIAG
forensics (`.fw-final/artifacts/R1/diag/`) + controller verification proved:
- The 17 duplicate USRs are legitimate per-context observations created by the
  NORMAL FULL INDEX at T0 — the rows/3 schema keys symbols on
  `UNIQUE(config_hash, usr, context_id)` (`_schema.py:677`, f669c57 T14
  SLICE-C); the failing test asserts rows/2 whole-config USR uniqueness
  (stale expectation). Paired rows differ only in id/file_id/context_id.
- Reindex is idempotent (17→17→17, total 228 constant), deletes are
  ctx-scoped, proxy ON/OFF makes no difference (both 17).
- "Not reproducible on Windows" was actually "never runs on Windows"
  (no gcc → conftest auto-skip).
Also corrected above: `_reindex_parse_and_store` lives at
`mcp/handlers/maintenance.py:1441` (not `indexer/maintenance.py`).
Disposition: TEST_EXPECTATION_WRONG; corrected by the FINAL REPAIR WAVE
(FR-R1, test-side; design `.fw-final/artifacts/R1/DESIGN_R1.md`).
