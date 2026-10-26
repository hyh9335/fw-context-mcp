# Contributing to fw-context

Thank you for considering a contribution. fw-context is build-aware code
intelligence for embedded C/C++ firmware — every change should keep the core
promise: **the index reflects what the compiler actually builds.**

## Development setup

Requirements: Python 3.11+, libclang, GNU make (or run the pip commands manually).

```bash
git clone https://github.com/hyh9335/fw-context-mcp.git
cd fw-context-mcp
make dev          # editable install with dev dependencies into .venv
make test         # unit test suite
make lint         # style checks
make lint-security
```

## Test suite

The pytest suite is marker-filtered. Default runs exclude tests that need
external services or long execution:

| Marker | Meaning |
|---|---|
| `ollama` | needs a running Ollama instance |
| `libclang` | needs a real compiler toolchain |
| `system` | needs system-level provisioning |
| `slow`, `stress` | long-running |
| `integration_external` | external network or services |

Run the default set exactly as CI does:

```bash
python -m pytest -m "not ollama and not libclang and not system and not slow and not stress and not integration_external"
```

If your change touches indexing behaviour, please also run at least one
integration pass against a real project (`fw-context index --build` on a
small firmware) and include what you observed in the pull request.

## Reporting bugs

The most useful bug reports include:

- build system and version (Mbed OS, Zephyr, PlatformIO, Keil, …)
- target/board
- the `fw-context doctor` output
- a minimal reproducer (a small project or a single translation unit)
- what the tool returned vs. what the build actually contains

Incorrect results (a caller that is not real, an inactive branch treated as
active, a missing symbol) are the most valuable reports of all — they
directly damage agent trust in the tool.

## Pull requests

1. Fork and create a feature branch.
2. Keep each PR focused; one behavioural change per PR.
3. Add or update tests for any behaviour change.
4. Update `docs/` when you change user-visible behaviour (tools, CLI, config).
5. Run `make test`, `make lint`, `make lint-security` before submitting.

## Scope notes

- MCP tool surface: tool handlers live in `src/fw_context_mcp/mcp/handlers/`,
  registration in `src/fw_context_mcp/mcp/server.py`. New tools need a
  reference entry in `docs/tools.md`.
- Storage: the index is SQLite-backed; schema changes must keep existing
  indexes openable or provide a migration path.
- Build detection: new build systems go through
  `src/fw_context_mcp/build/` with a detector, a generate/convert step, and a
  documented `config.toml` section.

## License

By contributing, you agree that your contributions are licensed under the
MIT License of this repository.
