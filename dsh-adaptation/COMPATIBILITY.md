# fw-context-mcp × DeepSeek Harness (dsh) — Compatibility Analysis

**Scope:** `fw-context-mcp` (this repo) as an MCP server for **DeepSeek Harness**
(`dsh`, the `@deepseek-ai/dsh` CLI, v `0.1.1-rc.2` tested here).

**Bottom line:** fully compatible. No change to `fw-context-mcp` source is needed.
You mount it in dsh with a two-line Cordis entry through dsh's official MCP
bridge (`@deepseek-ai/dsh-mcp-client`). The ready-to-use patch lives in
[`cordis.fw-context.patch.yml`](./cordis.fw-context.patch.yml).

---

## 1. What each side is

### fw-context-mcp
- A standard **Model Context Protocol (MCP) server**, JSON-RPC 2.0 over stdio.
- Exposes **38 build-aware code-intelligence tools** for embedded C/C++
  firmware (search/lookup, call graph, class analysis, index maintenance).
- Protocol used (verified against the running server):
  - `initialize` / `notifications/initialized`
  - `tools/list` (38 tools, each with JSON-schema `inputSchema`)
  - `tools/call` → `{ content: [{ type: "text", text: "<json>" }] }`
- Independent of any particular MCP client.

### DeepSeek Harness (`dsh`)
- A local coding-agent CLI/Web/TUI built on the **Cordis** plugin framework.
- dsh **does not** read a `settings.yaml` "mcpServers" block (that file only
  carries LLM providers, credentials, and profile snapshots).
- dsh integrates external tools exclusively through **plugins**:
  - Native tools registered on `ctx.tools` (the harness ToolRuntime);
  - so every plug-in ships as a Cordis plugin/bundle with a `cordis.patch.yml`.
- dsh ships an official **MCP client bridge** as a plugin:
  `@deepseek-ai/dsh-mcp-client` (present in dsh's own `node_modules`). It
  connects to any stdio or streamable-HTTP MCP server and re-registers every
  tool on `ctx.tools` under `mcp__<serverName>__<rawName>`.

## 2. Why this is a clean fit

| Concern | fw-context-mcp | dsh (`dsh-mcp-client`) | Result |
|---|---|---|---|
| Wire transport | JSON-RPC 2.0 over stdio | stdio transport (`socket/stdio`) | same |
| Tool discovery | `tools/list` | calls `tools/list` on connect and re-syncs on `notifications/tools/list_changed` | exact |
| Tool call | `tools/call` with raw name + arguments | forwards raw name + args; never reparses the public name | exact |
| Timeout / abort | per-call | `toolCallTimeoutMs` + `AbortSignal` | configurable |
| Startup when no index | server stays up, tools return `no_index` status | `failOnStartupError: false` keeps the profile alive | graceful |
| Reconnect | n/a (server is persistent) | `reconnect.*` policy restarts the child on crash | supported |
| Tool naming | `search_code`, `find_callers`, … | model sees `mcp__fw_context__search_code`, … | deterministic, 64-char contract satisfied |

## 3. What the official ecosystem recommends (evidence)

Docs and real plugins installed in this environment show the pattern used here:

- `@deepseek-ai/dsh-mcp-client` is the sanctioned bridge:
  - package `description`: *"MCP client bridge: connects to MCP servers and
    registers their tools on `ctx.tools`"*;
  - README config examples (stdio + streamable-http) match the patch here;
  - `Config` schema: `{ transport, serverName, command, args, env, cwd,
    toolCallTimeoutMs, failOnStartupError, reconnect }`.
- Real plugins (e.g. `@anysearch/anysearch-dsh`, installed in the `web`
  profile) confirm the adaptation layer is a **Cordis patch** that inserts
  plugin rows; dsh composes bundle patches → profile `cordis.patch.yml` →
  home patch → `--patch` overlays.
- A dsh patch is a top-level list of ops; adding a *new* plugin row requires
  the `insert:` op (a bare `{id, ...}` entry targets an existing row and fails
  with `entry "<id>" not found`).

## 4. Compatibility report (tested locally)

| # | Check | Method/Result |
|---|---|---|
| 1 | Server speaks MCP over stdio | `python` probe: `initialize` + `tools/list` → **38 tools** ¶ |
| 2 | Tool schemas are harness-consumable | each tool carries a JSON-schema `inputSchema`; raw names are ≤ 64 chars and `[A-Za-z0-9_-]`‑safe, so `mcp__fw_context__*` names stay clean (no hash suffix) |
| 3 | Patch format accepted by dsh | `dsh --profile headless --dump-config --patch cordis.fw-context.patch.yml` (isolated `DSH_HOME`) → **exit 0**, row `mcp-fw-context` composed on top of `dsh-headless` |
| 4 | Config accepted by the official bridge | Node: `@deepseek-ai/dsh-mcp-client` `Config(config)` normalizes our config (serverName, stdio, command, cwd, timeout, reconnect) |
| 5 | Live registration + call through the bridge | ✅ **verified live**: the official bridge spawned `fw-context-mcp`, registered **all 38 tools** on `ctx.tools` as `mcp__fw_context__*`, and a real `tools/call` round-trip returned content (`node test/dsh-mcp-integration.mjs` → `RESULT: PASS`). Earlier sandbox runs were blocked only by the harness denying `node` child-process `spawn`; once that permission was granted the test passed unchanged |

> ¶ The README says "37 tools"; the running server lists **38** (there is also
> `list_variants`). The bridge publishes whatever `tools/list` returns, so both
> counts are fine.

### Environment note (resolved, not an incompatibility)
- dsh's MCP bridge must `spawn` the server from Node, and dsh boot always
  rewrites `<profile>/cordis.yml`. In the first test runs the harness denied
  `node` child-process `spawn` (`EPERM`) and profile writes, so we validated
  with an isolated `DSH_HOME` (`--dsh-home`) + MCP probes. After the harness
  granted process/write access (approval `never` + full file access), the
  **same** bridge test ran live and **passed**: 38 tools registered and a real
  `tools/call` returned data. `dsh` itself accepted the installer-written
  patch (`--dump-config` → exit 0).
- Testing used an isolated `DSH_HOME` throughout, so the real `~/.dsh` was
  never modified.

## 5. Installer integration (`fw-context init --tool dsh`)

`fw-context init` can now register into dsh itself (no manual patch): it
writes the same `mcp-fw-context` Row via `@deepseek-ai/dsh-mcp-client` into
`$DSH_HOME/cordis.patch.yml` (default, all profiles) or a profile's
`cordis.patch.yml` (`--dsh-profile`), idempotently. Covered by
`tests/test_dsh_registration.py` and validated against dsh here (see
section 4).

## 5. No source changes to fw-context-mcp
The adapter is configuration-only on the dsh side. `src/`, `pyproject.toml`,
tests, and docs of `fw-context-mcp` are untouched. The only new files are under
`dsh-adaptation/` (patch, docs, tests).

See [`README.md`](./README.md) for install/usage instructions.
