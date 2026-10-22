# fw-context-mcp — MCP Server

fw-context-mcp is an MCP (Model Context Protocol) server. fw-context-mcp exposes the fw-context symbol index as tools for AI assistants. fw-context-mcp communicates over **JSON-RPC 2.0**, through stdio.

## What it is

Your AI assistant (for example, Claude Code or OpenCode) starts fw-context-mcp as a persistent subprocess. fw-context-mcp provides **45 tools** to navigate embedded firmware codebases:

| Category | Tools |
|----------|-------|
| **Search & lookup** | `search_code`, `lookup_symbol`, `smart_search`, `semantic_search`, `search_bodies`, `search_content`, `find_variables` |
| **Understanding** | `get_file_map`, `get_source`, `explain_symbol`, `get_symbol_context`, `read_file` |
| **Call graph** | `find_callers`, `find_references`, `find_call_path`, `find_all_callers_recursive`, `find_callees_recursive`, `find_dead_code`, `find_hotspots`, `get_vector_table`, `find_wrapper_callers`, `find_indirect_call_sites`, `find_indirect_targets`, `trace_data_flow` |
| **Class analysis** | `get_inheritance_chain`, `get_class_members`, `get_template_instances`, `get_method_overrides` |
| **Conditional compilation** | `explain_condition`, `list_conditional_regions` |
| **Variant analysis** | `compare_variants`, `group_equivalent_variants` |
| **Index maintenance** | `get_active_build`, `get_environment_status`, `check_dependencies`, `configure_llm`, `reindex_file`, `reset_index`, `list_projects`, `list_variants`, `get_project_info`, `check_ollama` |
| **Build / project state** | `get_project_status`, `get_build_info`, `get_build_health` |

For the full tool reference, with schemas and examples, see **[Tools Reference](docs/tools.md)**.

## Protocol

fw-context-mcp uses JSON-RPC 2.0 over stdin and stdout. Each line contains one JSON object. A newline character (`\n`) ends each line.

### Initialization

```
Client → Server:
  {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05",…}}

Server → Client:
  {"jsonrpc":"2.0","id":1,"result":{…}}

Client → Server:
  {"jsonrpc":"2.0","method":"notifications/initialized"}
```

### Calling a tool

```
Client → Server:
  {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_code","arguments":{"query":"uart init"}}}

Server → Client:
  {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"<json>"}],"isError":false}}
```

The `text` field is a JSON-encoded string. Parse the string to get the return value.

### Listing tools

```
Client → Server:  {"jsonrpc":"2.0","id":99,"method":"tools/list","params":{}}
Server → Client:  {"jsonrpc":"2.0","id":99,"result":{"tools":[{…},…]}}
```

## Integration

### Claude Code

```bash
# pip install (binary in ~/.local/bin or venv bin/)
claude mcp add --scope user fw-context fw-context-mcp

# source install
claude mcp add --scope user fw-context ~/.fw-context/.venv/bin/fw-context-mcp
```

Run `fw-context init` to do this automatically. This command also adds usage instructions to `CLAUDE.md` of the project (the default `--scope project`). With `--scope global`, it writes them to `~/.claude/CLAUDE.md`. With `--scope all`, it writes them to the two files.

### OpenCode

Add to `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "fw-context": {
      "command": ["fw-context-mcp"],
      "enabled": true,
      "type": "local"
    }
  }
}
```

If `fw-context-mcp` is not on your PATH, use the full path:
`"command": ["/home/<user>/.local/bin/fw-context-mcp"]` (pip) or
`"command": ["/home/<user>/.fw-context/.venv/bin/fw-context-mcp"]` (source).

### DeepSeek Harness (dsh)

dsh (DeepSeek Harness, `@deepseek-ai/dsh`) does not read `settings.yaml` for
MCP servers; it connects through the official `@deepseek-ai/dsh-mcp-client`
bridge via a Cordis patch. `fw-context init --tool dsh` writes that entry for
you (see `dsh-adaptation/` for the hand-written patch and full docs):

```bash
fw-context init --tool dsh                # register for all dsh profiles
fw-context init --tool dsh --dsh-profile web   # register into one profile
# dsh-home override for testing/custom locations:
fw-context init --tool dsh --dsh-home "$HOME/.dsh"
```

This writes (or updates again on re-run, idempotently) the `mcp-fw-context`
row into `$DSH_HOME/cordis.patch.yml` (all profiles) or
`$DSH_HOME/profiles/<name>/cordis.patch.yml`. After restarting dsh, the
tools are available as `mcp__fw_context__<tool>`.

### Other MCP clients

Start the server as a subprocess:

```bash
fw-context-mcp
# equivalent: python -m fw_context_mcp.mcp.server
```

If the binary is not on your PATH, use the full path. For pip installs
the binary is typically at `~/.local/bin/fw-context-mcp`. For source
installs it is at `~/.fw-context/.venv/bin/fw-context-mcp`. Run
`which fw-context-mcp` to find the exact path on your system.

## Error handling

**JSON-RPC errors** (protocol level):
```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"Invalid request parameters"}}
```

**Tool-level errors** (the tool ran, but did not complete):
```json
{"result":{"content":[{"type":"text","text":"{\"error\": \"No index found for …\"}"}],"isError":true}}
```

Tool errors use `isError: true` (ADR-006): whether the handler reported a
business error (a top-level `"error"` dict or list record) or RAISED an
exception, the result arrives with `isError: true` and the JSON body in the
`text` field. `{"info": …}` and `{"warning": …}` records are degraded
answers, not failures — they keep `isError: false`. Only JSON-RPC protocol
errors use the top-level `error` key.

### Staleness and the state of the index

Query tools detect staleness per file, against the results they are about
to return: when a named file changed after the last index run (mtime +
source-hash tiered check), the tool returns its results anyway — marked
with a `stale` flag and a `stale_warning` on dict-shaped results, or a
leading `warning` record on list-shaped results — and kicks off a
background daemon that re-indexes the project outside the query path.
Queries never block on a reindex, and there is no per-query reindex cap or
timeout: the repair is asynchronous by design.

Bounded scans that DO exist (so an empty result is read correctly): the
mtime-scan cache is valid for 30 seconds (`_CACHE_TTL_S`), the
`git ls-files` probe of the staleness check times out after 30 seconds
(`_GIT_LS_TIMEOUT_S`), unindexed-source reports cap at 20 names
(`_UNINDEXED_REPORT_LIMIT`), and the manifest header scan caps at 200
files. An empty result after staleness detection means "not found within
the indexed view", not proof of absence.

```json
{
  "file": "src/spi.c",
  "stale": true,
  "stale_warning": "src/spi.c changed after the last index run. …"
}
```

`index_state` is `"reindexing"` or `"pending"` (wait for the run), `"not_scheduled"` (no run updates the index: the warning names the command, or `get_project_status` names it), or `"failed"` (the last index run failed). See [The state of the index](docs/tools.md#the-state-of-the-index).

#### Ghost-record purge after file deletion

When a source file is deleted from disk (e.g. after a git branch switch),
the next `fw-context index` run automatically removes its symbols, references,
and edges from the index. The query-time staleness check now detects deleted
files and triggers a background reindex, which in turn runs the purge.

A safety guard prevents accidental mass deletion: the automatic purge aborts
when more than 20 percent of indexed files are missing from disk. This guards
against offline network mounts or a wrong project root.

Configure the threshold in `.fw-context/config.toml`:

```toml
[index]
purge_max_missing_percent = 20   # default; set to 100 to disable the guard
```

The log contains a warning when the guard blocks a purge:

```
Ghost-file purge aborted: 150/200 files (75.0%) missing from disk (threshold 20%).
Possible offline mount or wrong project root.
```

### Database corruption

No query tool checks the integrity of the database, because the check reads the full file. A query that reads a damaged page fails with the error of SQLite, for example `database disk image is malformed`:

- A query tool gives a dict that holds `error` with that text.
- `get_project_status` and `get_build_info` fail as a call: the client gets an error of the tool with that text.

To find if the database is damaged, call `check_dependencies`. Its check `db-integrity` runs `PRAGMA quick_check` at each call, and it does not change the database. The check has `status: "error"` when it cannot read the database, and its `message` tells the cause. When the message shows damage (for example `malformed`), call `reset_index(confirm=true)` to delete the database, and run `fw-context index` to make it again. A lock or a missing permission does not damage the database: repair that cause.

## Environment

- **CWD:** The server resolves the project root from `$PWD` when the request does not provide `project_root`. Launch the server from the firmware project root.
- **The core tools need no network connection** (search, lookup, source, graph). `smart_search` and `explain_symbol` can optionally use a local Ollama server.
- **The server asks PyPI for a newer release** in a background thread at server start. After a request that succeeded, it asks again only after 24 hours. A request that failed has no effect on the tools, and the next server start tries again. No tool waits for the request. `[updates] check = false` in the global config, or `FW_CONTEXT_NO_UPDATE_CHECK=1`, stops it. See [`[updates]`](docs/configuration.md#updates--release-check).
- **The server keeps one connection for the queries of each index database.** It opens that connection again after a reindex or an error of the connection. A call reads the config of the build through a short read-only connection.

## Debugging

```bash
# Manual tool listing
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | fw-context-mcp

# Verbose logging
FW_CONTEXT_LOG_LEVEL=DEBUG fw-context-mcp

# Ollama debug log (when debug_log is set in config)
tail -f ~/.fw-context/llm-debug.jsonl
```

### Staleness and auto-reindex

Query tools detect staleness per file, against the results they are about
to return: when a named file changed after the last index run (mtime +
source-hash tiered check), the tool returns its results anyway — marked
with a `stale` flag and a `stale_warning` on dict-shaped results, or a
leading `warning` record on list-shaped results — and kicks off a
background daemon that re-indexes the project outside the query path.
Queries never block on a reindex, and there is no per-query reindex cap or
timeout: the repair is asynchronous by design.

Bounded scans that DO exist (so an empty result is read correctly): the
mtime-scan cache is valid for 30 seconds (`_CACHE_TTL_S`), the
`git ls-files` probe of the staleness check times out after 30 seconds
(`_GIT_LS_TIMEOUT_S`), unindexed-source reports cap at 20 names
(`_UNINDEXED_REPORT_LIMIT`), and the manifest header scan caps at 200
files. An empty result after staleness detection means "not found within
the indexed view", not proof of absence.

```json
{
  "file": "src/spi.c",
  "stale": true,
  "stale_warning": "src/spi.c changed after the last index run. …"
}
```

#### Ghost-record purge after file deletion

When a source file is deleted from disk (e.g. after a git branch switch),
the next `fw-context index` run automatically removes its symbols, references,
and edges from the index. The query-time staleness check now detects deleted
files and triggers a background reindex, which in turn runs the purge.

A safety guard prevents accidental mass deletion: the automatic purge aborts
when more than 20 percent of indexed files are missing from disk. This guards
against offline network mounts or a wrong project root.

Configure the threshold in `.fw-context/config.toml`:

```toml
[index]
purge_max_missing_percent = 20   # default; set to 100 to disable the guard
```

The log contains a warning when the guard blocks a purge:

```
Ghost-file purge aborted: 150/200 files (75.0%) missing from disk (threshold 20%).
Possible offline mount or wrong project root.
```
