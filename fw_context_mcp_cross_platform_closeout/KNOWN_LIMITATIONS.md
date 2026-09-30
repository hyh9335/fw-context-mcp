# KNOWN_LIMITATIONS — fw-context-mcp closeout release consolidation (A13_RELEASE)

Consolidates the repo-root `KNOWN_LIMITATIONS.md` (W2/W7 environment findings), the
three RELEASE_AUDIT dispositions (NEW-W7-1, NEW-W9-1, NEW-W9-2), the platform
capability skips, and the design-accepted limitations frozen in
`.fw-closeout/artifacts/arch/PLATFORM_ADR.md` + `accepted_design_W1..W7.md`.
Everything here is disclosed and dispositioned in the ledger
(`FINAL_ISSUE_LEDGER.json`); nothing in this file is an undisclosed defect.

## 1. Git subprocess stalls under live daemons (23–53 s), win32 + EDR/minifilter suspect
*(from repo-root KNOWN_LIMITATIONS.md; ledger NEW-W2-2 FIXED-as-documented)*

While a live stdio MCP server (watcher daemon running) probes staleness, `git`
spawns on the W0 Windows host intermittently take 23–53 s (<1 s idle/WSL).
Suspected third-party EDR/minifilter interception of `git.exe` image loads while
the daemon holds `.fw-context/**` locks; not confirmable (host policy forbids
unloading drivers for an A/B). Evidence: `.fw-closeout/artifacts/W2/`
(git_trace_srv.log, py-spy dumps) + `W2_verify/VERDICT.md`. **Bound the code
applies:** every git invocation runs under a bounded `Popen` timeout
(`_GIT_LS_TIMEOUT_S = 30.0`, `src/fw_context_mcp/mcp/shared/stale.py`) with
tree-kill on expiry; the probe degrades to mtime staleness instead of hanging.
Operator guidance: exclude `.fw-context/` and `git.exe` from EDR scanning.

## 2. Corporate proxy environments hijack loopback LLM probes (WSL)
*(ledger NEW-W1-2 FIXED — code bug fixed, environment remains)*

On WSL hosts whose ambient proxy env lacks `localhost` in `NO_PROXY`, the
enrichment phases' loopback probe (`http://localhost:11434/api/tags`) is routed
through the corporate proxy and times out. The code-level bug this exposed
(transport errors escaping the enrichment phases' non-fatal degradation
contract) is FIXED (`e503d88`: `_reindex_llm_analysis`/`_reindex_embeddings`
degrade `httpx.HTTPError` to `analysis_warning`/`embedding_warning`;
`tests/test_reindex_enrichment_degrades.py`). The network reachability itself is
an operator concern (add `localhost` to `NO_PROXY` or set `llm.enabled=false`).

## 3. Compiler/toolchain capability gates in the test suite

- **gcc gate:** tests marked `libclang` auto-skip when `gcc` is absent
  (`tests/conftest.py`). The indexer never invokes a compiler, but
  fixture-building tests keep the conservative gate. Consequence: on Windows
  without MinGW the full slice suite reports those as skipped (W0 baseline
  161P/5S/1xf); the same tests run on WSL. Ledger NEW-W2-2 / repo-root §3.
- **LLVM skip (slice_i):** `tests/test_dead_code_reachability_slice_i.py`
  skips with `capability: clang + ld.lld + llvm-nm not available` when the host
  lacks LLVM tools. Windows has LLVM at the hardcoded `C:\Program Files\LLVM\bin`
  (not on PATH); the WSL venv host lacks them, so the WSL slice suite carries
  this one extra conditional skip (W4 verify C: 160P/6S/1xf vs win
  161P/5S/1xf). Host-optional tooling, not a product gap.

## 4. Patch-seam rot: 76 tests attempt real network I/O (ledger NEW-W7-1 — DEFERRED_WITH_REASON)

Authoritative count at release head 954514a (fresh WSL full suite,
`.fw-closeout/artifacts/RELEASE/wsl_full_main_954514a.log`): **76 tests** =
`test_ollama_client` 36 + `test_chat_router` 26 + `test_streaming` post-side 14.
Earlier sources measured 74 (targeted byte-identical E_rot rerun: 34+26) and 75
(parity-gate record: ollama 35) — the ollama file shows ±2 run-to-run variance
in the rot set across full-suite collection; the file is untouched by W1–W7
(`git log 7bf462f..954514a -- tests/test_ollama_client.py` is empty), so the
whole family is pre-existing. The tests patch **pre-factory seams**
(module-level `httpx.stream` / `httpx.post`) that production abandoned at W1
(platform factory `http_post`/`http_stream`). The mocks never engage, so each
test attempts a real connect and fails. Failure sets are byte-identical at base
00d9af9 (`W7_verify/E_rot_base.log == E_rot_w7.log`) — W1-era rot, zero
behavior change by W1–W7 (test_streaming was touched only by the mock-target
migration `af37a0f`). **Not product defects:** the affected sites hold
independent passing coverage (`tests/test_platform_http_stream.py` 5/5; 28
migrated seams inside test_streaming's 67P). Consequence: until repaired, these
three files are not valid regression coverage for the LLM-client surface.
Post-release repair: migrate the seams to the factory imports.

## 5. Env/test-infrastructure failures on WSL: configure_llm ×4 + incremental_reindex ×10 (ledger NEW-W9-1 — DEFERRED_WITH_REASON)

PROVEN pre-existing at base 7bf462f (job pwsh-57, identical 14F). Root-caused by
this audit on a representative (`tests/test_configure_llm.py:58-68`): the tests
patch `fw_context_mcp.llm.chat_router.httpx.post` — a seam production no longer
uses since W1 (`chat_router.py:36` imports `platform.http_post`, `:349` calls
it) — so the mock never engages and a real HTTPS call is attempted; the proxied
WSL env makes it fail. The 10 `test_incremental_reindex` failures are
`@pytest.mark.ollama` live-LLM analysis tests (file line 2302+) degraded by the
same env. Same family also fails on the Windows full suite (configure_llm 4F).
Not product defects; product paths hold W1/W7-verified coverage. Post-release
repair: hermetic seams + live-LLM fixture gating.

## 6. Integrity residual: duplicate USRs after degraded-LLM reindex (ledger NEW-W9-2 — DEFERRED_WITH_REASON; **product-impacting, WSL + proxied env**)

Rerun by this audit at main 954514a on WSL
(`.fw-closeout/artifacts/RELEASE/NEW-W9-2_no_duplicate_usrs_wsl.log`):
`tests/test_index_integrity.py::TestReindexIntegrity::test_reindex_no_duplicate_usrs`
fails — `reindex_file_impl("src/driver.c")` returns **no error** yet leaves
**17 duplicate USRs**: exactly driver.c's full symbol set (enum OperationMode +
4 constants, 4 `driver_*` functions, struct Config + 3 fields, anon struct
Response + 2 fields, typedef Response), each present twice under the same
`config_hash`. Windows never reproduces (zero test_index_integrity failures in
the win full suite). Same environment family as the 5 sibling tests FIXED by
`e503d88` (proxy-hijacked loopback LLM probes), but a **different escape**: no
error surfaces and the TU's old rows are not removed around the re-insert —
suspected transaction/cleanup-ordering path in `_reindex_parse_and_store`
(`src/fw_context_mcp/mcp/handlers/maintenance.py:2102`). Root cause beyond this
signature is unexamined and needs product-level debugging; no fix commit exists,
so the release auditor (forbidden from production changes) recorded it
DEFERRED_WITH_REASON. **Residual risk:** on hosts where LLM enrichment hits
transport errors, a reindex can leave duplicate symbol rows in the same
config_hash; recommended post-release product fix.

## 7. Windows-host fixture quirks (known pre-existing failures, disclosed)

- `test_lookup_definition.py::TestIntegrationInheritanceChain` ×2 —
  `tomllib.TOMLDecodeError: Invalid hex value` from backslash paths in a
  checked-in TOML fixture (W3 verify B; WSL 0F — Windows-only).
- `tests/test_graph_file_field.py::TestWithAbsoluteFile` ×2 and
  `tests/test_content_based_staleness.py::TestCountModifiedGate::
  test_the_two_helpers_agree_on_a_backdated_file` (W2 verify confirmed
  pre-existing in `C_failure_names.txt`); `test_utils.py::TestAbsPath` ×1;
  `test_linker_pass_integration.py::TestTheRunReachesThePass::
  test_slot_zero_is_not_reported_as_code` (W4-verified at base, both platforms).

## 8. Design-accepted limitations (frozen ADRs, not defects)

- **Kill authority is conservative by design** (PLATFORM_ADR D2/D11): identity
  unknown ⇒ deny (never kill); non-/proc POSIX ⇒ coordination-only, direct
  child only; legacy bare-int markers are identity-unknown (liveness only);
  watcher-only `allowed_roles` enforcement is an implementer choice where the
  design is silent (W5 verify F). `tree_kill` is only for pids this process
  spawned itself; no token check there is by contract (W2 attempt-2 verdict §2).
- **tree_kill POSIX scope:** `/proc`-based descendant walk; on non-/proc POSIX
  only the direct child is signaled — documented in `platform/posix.py:180`.
- **Header reindex covers one translation unit** (the manifest-chosen TU), not
  every including unit — a cost decision documented in `reindex_file_impl`
  (application headers reach a median of 3 units, up to 266); the result
  carries an explicit coverage warning.
- **`binary_status ∈ {present, gc_removed, unknown}`** — `unknown` is the
  honest default; presence is never fabricated (accepted_design_W4).
- **sys.platform leftovers** outside `platform/` (macros.py:42 win32 LLVM path,
  auto_model.py:84 darwin gate) are outside the binding `os.name` gate —
  disclosed as W1 receipt deviation 6, informational.
- **test_streaming.py encoding:** UTF-8 BOM + mojibake box-drawing comment
  characters (W7 verify follow-up 3) — cosmetic; parses and passes.
- **Parity-gate record erratum:** `GATES/cross_platform_parity.json` records
  96 WSL full-suite failures while its categorization sums to 95 (1 slot
  arithmetically unaccounted; the /tmp list was lost). The release-audit rerun
  at main (`RELEASE/wsl_full_main_954514a.log`) regenerates the authoritative
  list. Disclosed in FINAL_RELEASE_AUDIT.md §8.
