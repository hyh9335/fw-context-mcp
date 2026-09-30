# dsh-adaptation - mount fw-context-mcp in DeepSeek Harness (dsh)

Turn `fw-context-mcp` (this repo, 38 code-intelligence tools) into **native
dsh tools** using DeepSeek Harness's official MCP bridge
`@deepseek-ai/dsh-mcp-client`. No changes to `fw-context-mcp`
source are required.

> Tested with `dsh` **0.1.1-rc.2** (`npm i -g @deepseek-ai/dsh`). See
> [COMPATIBILITY.md](./COMPATIBILITY.md) for the full analysis.

## What you get

After enabling, the model sees every fw-context tool as:

```
mcp__fw_context__search_code
mcp__fw_context__lookup_symbol
mcp__fw_context__find_callers
... (all 38 tools that the server reports; see docs/tools.md)
```

Each tool maps 1:1 over JSON-RPC `tools/call` to the fw-context server.

## Install

Requirement: `fw-context-mcp` must be on `PATH` (started via the stdio MCP
server command). Verify:

```bash
fw-context-mcp          # starts the server (Ctrl-C to stop)
```

### Option A - persistent (recommended for daily use)

Add this entry to the profile you use (e.g. `$DSH_HOME/profiles/web/cordis.patch.yml`,
or `$DSH_HOME/cordis.patch.yml` to affect every profile):

```yaml
- insert:
    - id: mcp-fw-context
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: fw_context
        transport: stdio
        command: fw-context-mcp
        args: []
        cwd: 'D:\github\fw-context-mcp'   # <-- your indexed firmware project
        toolCallTimeoutMs: 180000
        failOnStartupError: false
        reconnect:
          enabled: true
          initialDelayMs: 1000
          maxDelayMs: 15000
          maxAttempts: 5
```

The exact file is provided at [`cordis.fw-context.patch.yml`](./cordis.fw-context.patch.yml).
Copy the `- insert:` block into your profile patch, then restart dsh.

### Option B - zero side effects (overlay only, no config change)

Apply the patch at boot without editing any profile file:

```bash
dsh --profile web --patch dsh-adaptation/cordis.fw-context.patch.yml
```

or for a one-shot headless run:

```bash
dsh --profile headless "search for uart init usage" --patch dsh-adaptation/cordis.fw-context.patch.yml
```

## Configuration notes

| Field | Meaning |
|---|---|
| `serverName` | Local namespace; must be `[A-Za-z0-9_-]{1,32}` and unique per profile. Sets the `mcp__fw_context__` prefix. |
| `command` / `args` | How to spawn `fw-context-mcp` (no shell interpolation). |
| `cwd` | Child's working directory. Set it to the directory of the **indexed** firmware project; when the model omits `project_root`, the server falls back to this path. Keep it explicit - dsh's empty-string default is unreliable for `spawn`. |
| `toolCallTimeoutMs` | Per tool-call timeout. Raised to 180 s because big firmware indexes can be slow. |
| `failOnStartupError` | `false` keeps the profile alive if the server cannot start (e.g. no index yet); tools appear once `fw-context index --build` succeeds. |
| `reconnect` | Restarts the server on crash with exponential backoff. |

To build an index first (if you have not already):

```bash
cd <firmware-project>
fw-context index --build
```

## Auto-register with the fw-context installer

The `fw-context init` installer now supports dsh as a first-class tool, so
you do not have to paste the patch by hand:

```bash
# register fw-context into dsh for all profiles (writes $DSH_HOME/cordis.patch.yml)
fw-context init --tool dsh

# or target a single profile (writes $DSH_HOME/profiles/<name>/cordis.patch.yml)
fw-context init --tool dsh --dsh-profile web

# override the dsh home (testing / non-standard location)
fw-context init --tool dsh --dsh-home "$HOME/.dsh"

# preview without writing anything
fw-context init --tool dsh --dry-run --dsh-home "$HOME/.dsh"
```

It is idempotent: re-running updates the `mcp-fw-context` row in place
(command/cwd) and leaves every other row and any `!!js` expressions intact.
`--list-tools` now shows `DeepSeek Harness (dsh)` and auto-detection picks it
up whenever the `dsh` binary or `~/.dsh` is present.

## Verify it works

```bash
# 0. FULL end-to-end on a real machine (installs into an ISOLATED dsh home,
#    builds a real index, runs the official bridge, then cleans up).
#    Requires a machine where node can spawn processes (not this sandbox).
powershell -ExecutionPolicy Bypass -File dsh-adaptation/test/run-e2e-real.ps1
#    (on WSL/Linux: use pwsh, or translate: fw-context init --tool dsh, then
#     node dsh-adaptation/test/dsh-mcp-integration.mjs)

# 1. Confirm dsh accepts the patch (composes cleanly, exit 0):
dsh --profile web --dump-config --patch dsh-adaptation/cordis.fw-context.patch.yml | grep -A6 mcp-fw-context

# 2. Live registration + a tool call through the official bridge:
node dsh-adaptation/test/dsh-mcp-integration.mjs

# 3. Server-side MCP handshake / tool inventory (independent of dsh):
python dsh-adaptation/test/mcp_probe.py
```

Then, inside dsh, ask the model to use e.g. `mcp__fw_context__lookup_symbol`
or `mcp__fw_context__search_code` on your firmware project.

> Note: this workspace's sandbox blocks `node` from spawning child processes
> (`EPERM`), so step 2 auto-reports `BLOCKED` there. Run it inside a real dsh
> terminal (WSL/Linux or an unlocked Windows console).