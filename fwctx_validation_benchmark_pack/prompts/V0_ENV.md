# V0 — environment and acquisition

Validation-only.

Capture:
- fw-context HEAD/package version
- Windows Python/clang/libclang/git
- WSL distro/kernel/Python/clang/gcc/cmake/ninja/git
- WSL test path filesystem type
- proxy/no_proxy
- real stdio smoke Windows and WSL

Validate the benchmark pack:
- fixture files exist
- G12 CRLF hash matches expected
- golden generator works
- public manifest parses

Clone required public repositories into WSL according to PUBLIC_CORPUS.yaml.
Record requested version and exact resolved SHA in `PUBLIC_LOCK.json`.

Do not build all public projects yet.

Output V0_ENV_REPORT.md + receipt.
