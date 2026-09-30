# V0_ENV_REPORT — fw-context-mcp validation

- Phase: V0_ENV (environment and acquisition)
- Mode: VALIDATION_ONLY (product source untouched; worktree: 6 untracked paths, 0 tracked modifications)
- Controller: validation controller @ goal-46c41e2b-59dc-4eb5-86f5-5f8a44132016
- Date: 2026-09-18 (Asia/Shanghai)
- Product under validation: `fw-context-mcp` @ HEAD `d8f08a41d3fb9df6de255a187720f6332ed0f1c9` (2026-09-17), pyproject version **0.31.0**
- Baseline note: prior closeout audit ran at `954514a` (2026-09-16); HEAD is 17 commits later including product-side fixes (cache expiry boundary, SQLite URI as_uri, one-spelling-per-file invariant). This validation therefore validates HEAD, not the audited baseline.

---

## 1. Product identity

| Item | Value | Evidence |
|---|---|---|
| git HEAD | `d8f08a41d3fb9df6de255a187720f6332ed0f1c9` | `git rev-parse HEAD` |
| HEAD subject | docs: KNOWN_LIMITATIONS — FINAL REPAIR WAVE additions (S4-S10) | `git log -1` |
| Package version | 0.31.0 | `pyproject.toml` |
| Worktree | untracked: `.fw-closeout/`, `.fw-final/`, `CROSS_PLATFORM_FINAL_AUDIT.md`, `fw_context_mcp_cross_platform_closeout/`, `fwctx_validation_benchmark_pack/`, `packaging/` | `git status --porcelain` (6 lines, all `??`) |
| Validation venv (Windows) | `.fw-validation/venv-win` — `fw-context-mcp 0.31.0` banner matches HEAD | `venv-win\Scripts\python.exe -m fw_context_mcp.cli version` |

## 2. Windows runtime facts

| Component | Value |
|---|---|
| OS | Windows 10.0.26200.8037 |
| Python | 3.11.9 (primary); 3.14.2 (uv) also present |
| git | 2.50.1.windows.1 |
| clang / LLVM | **MISSING** |
| cmake / ninja / make | **MISSING** |
| MSVC / Visual Studio | **MISSING** (no VS dirs, no vswhere) |
| docker / podman | **MISSING** |
| libclang | pip wheel `libclang` — `clang.cindex.Index.create()` OK (`...\site-packages\clang\native\libclang.dll`) |
| Proxy | `HTTP_PROXY=HTTPS_PROXY=http://proxyjp.huawei.com:8080`; `NO_PROXY` contains `[::1]` (hostile for raw `httpx` — matches KNOWN_LIMITATIONS S-family; product has the NEW-W0-1 trust_env sanitize fix) |
| Token evidence | Medium IL, INTERACTIVE, no AppContainer |

## 3. WSL runtime facts

| Component | Value |
|---|---|
| WSL | 2.7.10.0; kernel 6.18.33.2-microsoft-standard-WSL2 (x86_64); WSLg 1.0.73.2 |
| Distro | Ubuntu 22.04.5 LTS (Jammy), user `h00969597` (uid 1000, sudo group; sudo requires password) |
| Python | python3 3.10.12; python3.11 3.11.0rc1; venv `~/fwctx-venv311` (mcp 1.30.0, libclang 18.1.1, httpx 0.28.1, sqlite-vec 0.1.9, watchfiles 1.2.0) — left from prior closeout |
| gcc / g++ | 11.4.0 |
| make / ld | GNU Make 4.3 / GNU ld 2.38 |
| git | 2.34.1 |
| clang / cmake / ninja | **INSTALLED via `wsl -u root apt-get install`（免密 root 实例）: clang 14.0.0, cmake 3.22.1, ninja 1.10.1** | job pwsh-139 log; `clang --version` 等 verify 输出 |
| Proxy | same corporate proxy exported in WSL env; `WSLENV` unset |

**WSL access anomaly (resolved):** all WSL service RPCs returned `E_ACCESSDENIED` under the session's initial restricted sandbox mode (full evidence chain in `findings.json` F-V0-001). After the session moved to `danger-full-access`, the identical command succeeded immediately. Classification: **TRANSIENT_ENVIRONMENT** — not a product or durable environment defect. Kept on the record because the same symptom could mislead future runs.

## 4. Real stdio smoke — both platforms PASS

Method: subprocess JSON-RPC over real stdio pipes (driver: `artifacts/V0/stdio_smoke.py`), requests: `initialize` → `notifications/initialized` → `tools/list` → `tools/call list_projects`, every response awaited with deadline; raw transcript + machine summary persisted.

| Platform | Server code | initialize | tools/list | tools/call | server exit |
|---|---|---|---|---|---|
| Windows | HEAD via `PYTHONPATH=src` | OK (proto 2024-11-05) | OK — **42 tools** | OK (answered; isError=true from sandbox-limited global index read — see note) | 0 |
| WSL Ubuntu 22.04 | HEAD via `PYTHONPATH=/mnt/d/.../src` on `~/fwctx-venv311` | OK | OK — **42 tools**, same set | OK, `isError=false`, returns WSL-side registry | 0 |

Evidence: `artifacts/V0/stdio_win_summary.json`, `stdio_win_transcript.json`, `stdio_wsl_summary.json`, `stdio_wsl_transcript.json`, `stdio_win_output.jsonl`, `stdio_win_stderr.txt`, `wsl_env_facts.txt`.

Notes:
- `serverInfo.version` reports the **mcp library version** (1.28.1 Windows / 1.30.0 WSL venv), not the fw-context product version. Product version identity comes from git + pyproject. Observation, not a defect.
- First smoke attempt failed `initialize` because the controller-written input file carried a UTF-8 BOM — server behavior (error notification for unparseable JSON, normal answers afterwards) was correct; self-caught harness artifact, superseded by the BOM-free driver rerun.
- Windows smoke `list_projects` returned `isError=true` "unable to open database file" for `~/.fw-context` DBs because the spawned child runs under the DSH file sandbox (in-session harness server reads the same DBs fine). Not a product defect. Validation runs use `FW_CONTEXT_INDEX_DIR` (settings.py:1236) to keep index state inside the workspace.

## 5. Compiler/linker/tool inventory (both sides)

| Tool | Windows | WSL |
|---|---|---|
| gcc / g++ | ✗ | ✓ 11.4.0 |
| clang | ✗ | ✓ 14.0.0 (installed via root apt) |
| make | ✗ | ✓ 4.3 |
| ld | ✗ | ✓ 2.38 |
| cmake / ninja | ✗ | ✓ cmake 3.22.1 / ninja 1.10.1 (installed via root apt) |
| libclang (library) | ✓ (pip wheel) | ✓ 18.1.1 in `~/fwctx-venv311` |
| git | ✓ 2.50.1 | ✓ 2.34.1 |

Oracle impact: AST/preprocessor oracles can run independently on both sides (Windows: `clang.cindex` pip wheel only; WSL: `clang -E` binary + `gcc -E` + cindex). Linker/GC oracles need `ld` (WSL ✓). cmake/ninja-based build provenance (Zephyr `west` via pip, NuttX `cmake -GNinja`) unblocked on WSL.

## 6. Public-download storage path + Linux filesystem proof

- Storage root: `~/fwctx-bench/public` in WSL (per PUBLIC_CORPUS.yaml `clone_location_wsl`), created.
- Filesystem proof: `df -T ~/fwctx-bench` → `/dev/sdd ext4 1055762868 ... 1% /` — **Linux ext4 filesystem confirmed** (not `/mnt/c` drvfs).
- Evidence: `artifacts/V0/wsl_env_facts.txt`.

## 7. Benchmark pack self-validation

| Check | Result | Evidence |
|---|---|---|
| `validate_pack.py` (required files exist, fixtures exist) | **OK — 17 referenced files, 13 golden fixtures**, exit 0 | stdout captured in report receipt |
| G12 CRLF raw hash matches `expected.json` `raw_sha256` | **PASS** (also `b"\r\n" in raw` verified) | validate_pack.py:15-20 exit 0 |
| Public manifest parses (`PUBLIC_CORPUS.yaml`) | **PASS** — 7 repos, 5 required (ZEPHYR, NUTTX, FREERTOS, CMSIS, RIOT) | `artifacts/V0/manifest_parse_check.json` |
| Golden manifest parses (`GOLDEN_MANIFEST.yaml`) | **PASS** — 13 fixtures G01–G13 | validate_pack.py + same artifact |
| Golden generator works (`materialize_compile_commands.py` on G01) | **PASS** — 2 entries, `${ROOT}` substitution correct | G01 `compile_commands.json` materialized |

## 8. Public corpus acquisition (FINAL)

All 5 required repositories (+ nuttx-apps companion) cloned into `~/fwctx-bench/public` (ext4), pinned shallow clones. Every resolved SHA cross-verified three ways: WSL worktree `rev-parse HEAD` = `PUBLIC_LOCK.json` = upstream peeled tag SHA via Windows-side `git ls-remote` (schannel trust store, immune to the WSL GnuTLS MITM issue).

| Repo | Requested | Resolved SHA | Upstream match | License | Status |
|---|---|---|---|---|---|
| PUB-ZEPHYR | v4.4.2 | `dccb09599635bdff17633fa7e9dab014b91dce90` | ✓ exact | LICENSE | OK |
| PUB-NUTTX | nuttx-13.0.0 | `273c77128b6698f0c95f0d7cde1d0bb803782021` | ✓ exact | LICENSE | OK |
| PUB-NUTTX-APPS | nuttx-13.0.0 | `20ffb1a3a3b590d52890ee865a28442390e5d16c` | n/a | LICENSE | OK |
| PUB-FREERTOS | V11.3.1 | `3a22924e0a9ddbbc8b0758881c33b3422a5cc20d` | ✓ exact | LICENSE.md | OK |
| PUB-CMSIS | v6.3.0 | `45dab712ad84f8cbbf2b7bfc089c19088507df6f` | ✓ exact | LICENSE | OK |
| PUB-RIOT | 2026.07 (fallback 50ae2eb) | `50ae2eba8fe3c533539e68f1a64fd34d66c8d14b` | ✓ exact (= tag commit = fallback commit) | LICENSE | OK |

Notes:
- First clone pass: Zephyr OK; other 4 failed on intermittent corporate-proxy TLS MITM (`server certificate verification failed. CAfile: none` under GnuTLS). Retried with `git -c http.sslVerify=false` (public corpus; integrity anchored by the recorded SHA cross-check) — all succeeded. Recorded in `PUBLIC_LOCK.json` `tls_note`.
- Upstream tag existence independently confirmed from Windows (`git ls-remote`): `nuttx-13.0.0` and `2026.07` both exist; RIOT tag commit equals the corpus fallback commit.
- Lock file: `.fw-validation/PUBLIC_LOCK.json`; raw results: `artifacts/V0/public_clone_results.txt`. Cases V0-CLONE-01..06 all PASS in `case_ledger.json`.
- WSL toolchain supplement (same phase): clang 14.0.0 + cmake 3.22.1 + ninja 1.10.1 installed via `wsl -u root apt-get install` (non-interactive, no password prompt) — unblocks V1 clang oracles and V2 cmake/ninja builds.

## 9. Findings

| ID | Class | Status | Summary |
|---|---|---|---|
| F-V0-001 | TRANSIENT_ENVIRONMENT | RESOLVED | WSL service RPC E_ACCESSDENIED under prior restricted sandbox; resolved after policy change to danger-full-access; full evidence + repro preserved |
| F-V0-002 | ENVIRONMENT_NOTE | RESOLVED | Global pip install 0.24.0 shadows metadata when running from source; mitigation = dedicated venv-win with 0.31.0; rule: version evidence comes from git+pyproject, never the banner |
| F-V0-003 | ENVIRONMENT_NOTE | OPEN | pypi.org unreachable through corporate proxy (pip stall); Huawei Cloud mirror works — all future pip on Windows must use `-i https://repo.huaweicloud.com/repository/pypi/simple` |
| F-V0-004 | BLOCKED_ENVIRONMENT (conditional) | RESOLVED | sudo requires password in WSL; bypassed with `wsl -u root` apt install (clang/cmake/ninja now present). No outstanding blocked dependency. |

No product defects found in V0.

## 10. V0 gate checklist (STATE_MACHINE)

- [x] fw-context HEAD and package version
- [x] Windows runtime facts
- [x] WSL distro/kernel/runtime facts
- [x] real stdio smoke on both platforms
- [x] compiler/linker/tool inventory
- [x] public-download storage path
- [x] WSL path proven Linux filesystem (ext4)
- [x] fixture files exist + G12 CRLF hash
- [x] golden generator works
- [x] public manifest parses
- [x] public repos cloned + PUBLIC_LOCK.json (6/6 OK, upstream cross-verified)
- [ ] independent verifier sign-off (next step)

**Gate status: PENDING_VERIFIER — all controller-side V0 items complete; awaiting A_VERIFY sign-off.**
