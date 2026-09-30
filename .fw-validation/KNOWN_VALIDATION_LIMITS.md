# Known Validation Limits — fw-context-mcp 0.31.0 validation campaign

Scope: what this campaign did NOT establish, and residual risks in what it did.

## 1. Coverage residuals

- **F3 FreeRTOS QEMU demo — NOT_RUN (optional case).** The linker / `--gc-sections` /
  map-file truth surface was never exercised. Any claim about linker-driven symbol
  preservation is unsupported by this campaign.
- **G13 (V1)**: the V1 verifier flagged an unreliable first-pass comparison; G13's
  classification carries that caveat.
- **Windows→WSL stdio bridge and public-corpus smokes (V3 pack prompt extras)** were not
  exercised. The V3 parity evidence covers the 8-case golden subset on both platforms, not
  the launch-path or public-corpus extras.
- **V4 mutation classes** ran on 2–3 TU golden fixtures by design; nuttx/zephyr-scale
  mutation behavior is covered only by the V2 probes (N1/N2/N3, Z2/Z4), not re-exercised.

## 2. Environment constraints that shaped the evidence

- **Windows toolchain absence**: clang/cmake/ninja/make/MSVC all missing → G04/G11
  Windows cells are BLOCKED_ENVIRONMENT, covered only on the WSL side.
- **Corporate proxy**: TLS MITM forced `git -c http.sslVerify=false` for some public clones
  (integrity anchored by recorded resolved SHAs in PUBLIC_LOCK.json); pypi.org was
  unreachable without the proxy (F-V0-003).
- **WSL venv MCP SDK 1.28.1 (win) vs 1.30.0 (wsl)** — cosmetic, but version-sensitive
  behaviors cannot be fully excluded from the parity claim.
- **pip metadata staleness** (F-V0-002): global pip showed 0.24.0 while the repo is 0.31.0;
  all runs used repo-source venvs, but packagers should not trust global pip metadata.

## 3. Product-observation caveats (from findings, not fixed here)

- **G03 explain_condition context resolution is nondeterministic across runs** (resolved
  context A vs B in different runs) — classification unaffected, but conditional-context
  queries should not be assumed stable run-to-run.
- **Single-line definitions** store `symbols.source=''` (F-V1-007) — body text is absent
  for any definition spanning one line.
- **exact=True lookup fallback** (F-V2-002) can return unrelated symbols without any
  disclosure unless callers check for `_fallback` — downstream integrators must treat
  exact queries as best-effort until fixed.
- **Result caps** at limit<=100 (F-V1-008) — large-codebase queries are truncated, not
  streamed.

## 4. Verdict scope

NOT_READY applies to a real-project trial of the full surface. It does not negate the
passing surfaces (call graph, hotspots, dead-code L1/L2, semantic search, no-op reindex
idempotence, honest empty-results on unsupported-arch queries, A1–A8 adversarial classes,
index integrity under repeated mutation).
