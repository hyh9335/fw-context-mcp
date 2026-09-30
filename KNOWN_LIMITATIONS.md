# Known Limitations

Operational limits observed and bounded during the fw-context-mcp closeout
(W0–W7, September 2026). Each entry states what was measured, where the
evidence lives, and what bounds the code already applies. These are
documented behaviors, not open defects — the issue ledger
(`.fw-closeout/issue_ledger.json`) tracks everything actionable.

## 1. Git subprocess stalls under live daemons (23–53 s), win32 + EDR/minifilter suspect

**Symptom.** While a live stdio MCP server (watcher daemon running) issues
its periodic staleness probes, `git` subprocess spawns on the W0 Windows
host intermittently take 23–53 s wall clock to return, where the same
invocation returns in <1 s on an idle host or on WSL.

**Suspected cause.** Third-party endpoint-security / minifilter drivers
intercepting `git.exe` image loads and `CreateFile` sweeps over the
working tree while the daemon holds locks on `.fw-context/**` and performs
index writes. Not confirmed against a specific driver — host policy
prevents unloading them for an A/B measurement.

**Evidence.** `.fw-closeout/artifacts/W2/` — `git_trace_srv.log`
(per-spawn timings), `phase_b_*` py-spy dumps, and the timeline files;
verdict in `.fw-closeout/artifacts/W2_verify/VERDICT.md`.

**Bound the code applies.** The staleness probe wraps every `git`
invocation in a bounded `Popen` timeout (`_GIT_LS_TIMEOUT_S = 30.0` in
`src/fw_context_mcp/mcp/shared/stale.py`) and tree-kills on expiry; the
probe result is then treated as "no git data" — the query degrades to
mtime-based staleness instead of hanging. A stalled spawn therefore costs
at most one timeout window per probe, never an unbounded hang.

**Operator guidance.** On hosts with aggressive EDR/antivirus, add the
project's `.fw-context/` directory (and `git.exe`) to the exclusion list,
or raise `_GIT_LS_TIMEOUT_S` via configuration rather than disabling the
probe.

## 2. Corporate proxy environments hijack loopback LLM probes (WSL)

**Symptom.** On WSL hosts whose ambient proxy environment lacks a
`localhost`/`127.0.0.1` entry in `NO_PROXY`, the LLM enrichment phases'
loopback probe (`http://localhost:11434/api/tags`) is routed through the
corporate proxy and times out (`httpx.ReadTimeout` after ~5 s) even when
a local Ollama is healthy.

**Disposition (NEW-W1-2).** The environment is the trigger; the code-level
bug it exposed (transport errors escaping the enrichment phases'
documented non-fatal degradation contract) is FIXED — `_reindex_llm_analysis`
and `_reindex_embeddings` now degrade `httpx.HTTPError` to their
`analysis_warning` / `embedding_warning` keys (see
`tests/test_reindex_enrichment_degrades.py`). On such hosts the
enrichment phases report a warning instead of failing the reindex; the
underlying network reachability is an operator environment concern
(add `localhost` to `NO_PROXY`, or set `llm.enabled = false` in
`.fw-context/config.toml`).

## 3. Tests that require gcc skip on hosts without a compiler

Tests marked `libclang` are auto-skipped when `gcc` is not on `PATH`
(`tests/conftest.py`). The indexer itself does not invoke a compiler —
libclang parses `compile_commands.json` flags directly — but the gate is
kept conservative for the fixture-building tests. Consequence: on a
Windows host without MinGW, the full slice suite reports the libclang
tests as skipped (the W0 baseline: 161 passed / 5 skipped / 1 xfailed);
the same tests execute on WSL (`~/fwctx-venv311`), which has gcc.

---

# Known Limitations — FINAL REPAIR WAVE additions (2026-09-17)

The sections below extend the W0–W7 list with everything the final repair
wave (R1–R3) measured or deferred. Categories: ENVIRONMENT LIMITATION /
TEST DEBT / DEFERRED PRODUCT DEFECT / HOUSEKEEPING. Every item has a
ledger ID (`.fw-final/FINAL_ISSUE_LEDGER.json`) or an explicit mechanism.

## 4. Corporate proxy must bypass loopback for indexer CLI subprocesses (WSL)

With the corporate proxy enabled and no `no_proxy=127.0.0.1,localhost`,
indexer CLI subprocesses' loopback LLM probes are proxy-routed and stretch
wall-clock dramatically (WSL full suite: 251 s proxy-OFF vs 63m50s
proxy-ON, **results byte-identical** — hermeticity proven both ways,
`.fw-final/artifacts/R3/g1_wsl_comparison.json`). The W1 platform HTTP
layer builds loopback probe clients with `trust_env=False` (this wave's
conftest hardening); ambient CLI subprocess environments remain the
operator's responsibility. Operator guidance: export
`no_proxy=127.0.0.1,localhost` alongside the corporate proxy.

## 5. Capability skips on toolchain-less hosts (explicit reasons, never silent)

- clang **binary** (not the libclang binding): the 2 asm-dependent
  linker-integration tests skip via `_needs_clang` (FR-NEW-4; the assembly
  reader preprocesses through `clang -E` at `asm.py:109`; per-unit graceful
  skip is the contracted behavior). Non-vacuity proven: with a gcc-as-clang
  shim the same tests pass 8/8 (`.fw-final/artifacts/R3/diag4/`).
- gcc/cc/bash absent (Windows `bash.exe` is the WSL stub): 8
  autobuild-environment tests capability-skip (FR-NEW-5 D-N5-9).
- gcc-gated fixture builders on Windows (conftest, pre-existing).
Full-suite decomposition at wave close: WSL 192 skipped / Windows 368
skipped — every skip carries an explicit reason string.

## 6. WSL VM cold start (stdio gate B)

First WSL invocation after a VM restart can take ~10 s before the first MCP
call responds (9969 ms measured; steady-state cold_max 516 ms). The
recommended per-step operational timeout of 5000 ms assumes a warm VM
(`.fw-final/artifacts/R3/stdio_abc_latency.json`).

## 7. Windows file-lock test debt (FR-NEW-6 — DEFERRED_WITH_REASON)

2 failures + 1 error on Windows only: `WinError 5` (os.replace over an open
db; read-only .git object) and `WinError 32` (TemporaryDirectory teardown
unlinking an open index.db). Fix requires a close-db-before-unlink teardown
pattern across test infrastructure. Zero product impact; test-debt class.

## 8. Deferred product decisions (FR-NEW-7, FR-NEW-8 — DEFERRED_WITH_REASON)

- FR-NEW-7 (6 Windows failures): singleton contracts needing cross-platform
  semantic decisions — `os.kill(pid, 0)` liveness (not portable), POSIX-abs
  include roots in config_hash (×2), finetune empty-db contract,
  review_fixes WinError-text regex, recorder-cap kwarg.
- FR-NEW-8 (1 Windows failure): `zephyr.py:608` probes shims via a
  `"/shims/"` substring marker that cannot match native Windows path
  spellings.

## 9. Housekeeping dispositions (this wave)

- KEPT: `%LOCALAPPDATA%\Programs\Python\Python311\fcntl.py` (202-byte inert
  no-op flock stub; sha256 captured) and untracked `packaging/` (1305 files,
  zero repo references; per-file sha256 inventory).
- REMOVED after merge verification: 13 worktrees and 18 `fwctx/*` branches
  (each via `git branch -d` — git-verified merged; zero unmerged content);
  WSL clone `~/w7-wsl` (its 3 dirty files verified as pure line-ending
  churn, content identical; HEAD commit present in the main object store);
  stray B1/B2 logs (copied to `.fw-final/artifacts/R3/housekeeping/` first).
- Log-artifact loss (disclosed): one WSL proxy-ON gate log was lost to a WSL
  VM restart; the identical run's counts were controller-observed
  (3294P/192S/0F) and regenerated post-commit
  (`gate7_wsl_proxyon_RERUN.log`).

## 10. Test-infra notes

- `asm.py:88-89` docstring says the preprocessor comes from the unit's own
  flags while `asm.py:109` hard-codes `clang` — stale doc/code mismatch;
  behavior is the documented graceful skip (future cleanup item).
- `deps/_checks.py:502` builds `file:<path>` (no `//`) — verified VALID on
  Windows (no URI authority parsed); categorically different from the
  FR-NEW-2 `file://{path}` bug; intentionally unchanged.
- The `r2-win` venv's editable install was found resolving into a stale
  worktree; reinstalled from the main tree this wave. Windows runs should
  assert `fw_context_mcp.__file__` resolves under the intended tree.
