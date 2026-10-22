# Tools Reference

This is the complete reference for all fw-context CLI commands and MCP server tools.

## CLI commands

### `fw-context index`

Build or update the symbol index from `compile_commands.json`.

> **Incremental by default.** `fw-context` reuses an existing
> `compile_commands.json`, and re-parses only the changed files. Use
> `--build` to force a clean build and a full re-index when needed, for
> example after an SDK update or a build system change. Use `--force` to
> bypass the mtime checks and force a re-index of all files, embeddings,
> LLM analysis, overrides, and caches, without rebuilding, for example
> after a schema change or a tool update.

```bash
# Incremental (default) — reuse existing compile_commands.json, fast:
fw-context index

# Force clean build + full re-index:
fw-context index --build

# Force full re-index of all files, embeddings, analysis, and caches
# (skips mtime checks — use after schema changes or tool updates):
fw-context index --force

# Explicit path, verbose
fw-context index compile_commands.json -v

# Skip cross-references for faster indexing on large projects
fw-context index --no-refs

# Skip embeddings (no Ollama available)
fw-context index --no-embeddings

# Skip LLM symbol analysis
fw-context index --no-analyze

# Custom vendor and project directories
fw-context index --vendor-paths sdk third_party --project-paths src lib
```

| Option | Default | Description |
|--------|---------|-------------|
| `compile_commands.json` | from config | Path to the compilation database (skips build) |
| `--project DIR` | `.` | Project root directory |
| `--build` | off | Force a clean build and regenerate `compile_commands.json` |
| `--no-clean` | off | With `--build`: skip the clean step, and do an incremental build |
| `--vendor-paths DIR…` | `[index] vendor_paths` | Vendor and SDK directories, added to the directories that the build system detects. The option replaces `[index] vendor_paths`. A `[[build.variants]]` entry adds its own directories to it. |
| `--project-paths DIR…` | `[index] project_paths` | Project directories. They win over each vendor pattern. The option replaces `[index] project_paths`. A `[[build.variants]]` entry adds its own directories to it. |
| `--name NAME` | directory name | Project name override |
| `--no-refs` | off | Skip cross-reference / call graph indexing |
| `--embeddings` | `[index] index_embeddings` (`true`) | Generate symbol embeddings |
| `--no-embeddings` | off | Skip embedding generation. Wins over `--embeddings`. |
| `--analyze` | `[llm] analyze_symbols` (`true`) | Generate the LLM symbol analysis (summary, inputs, outputs) |
| `--no-analyze` | off | Skip LLM symbol analysis. Wins over `--analyze`. |
| `--analyze-vendor` | `[llm] analyze_vendor` (`false`) | Also analyze the vendor and SDK code |
| `--no-analyze-vendor` | off | Skip the analysis of the vendor and SDK code. Wins over `--analyze-vendor` and the config. |
| `--background` | off | The mode of the file-watcher daemon. The run does not rebuild a stale `compile_commands.json`, does not repair it, and prunes no registry rows. It builds only when the backend can build on its own. |
| `--force` | off | Force re-index of all files, embeddings, LLM analysis, overrides, PageRank, and hotspot cache (skips the hash checks) |
| `--variant NAME` | all variants | Restrict indexing to one build variant (name from `[[build.variants]]`) |
| `--variants A,B` | all variants | Comma-separated list of build variants to index |
| `--image NAME` | all images | Restrict indexing to one sysbuild image |
| `--exclude-image NAME` | none | Exclude a sysbuild image from indexing (repeatable) |
| `-v` | off | Verbose progress output |

**Automatic build:**

Without `--build`, a run can build by itself.

A run that you start builds as `--build` does when the build is not there:
`compile_commands.json` does not exist, or a `directory` of its entries does
not exist. fw-context asks the compiler of each unit in that directory.
When fw-context cannot run the build (STM32CubeIDE, TI CCS, a
`[build] command` in a background run, or a background run of a backend
that compiles in your tree), the run stops with an error and does not
index. A run with an explicit `compile_commands.json`, or a project with
`[[build.variants]]`, does not do this check.

A run also builds when an index exists and one of these conditions is true:

- The build is not there (a background run).
- A source file on disk is not in `compile_commands.json`. Such a file has
  no translation unit, thus a reindex skips it.
- The tree has a different checkout than the index: another branch, or
  a detached HEAD at another commit (for example the commit of a tag).
  `compile_commands.json` belongs to the old checkout. For the rules of
  this comparison, see [`get_project_status`](#get_project_status).

The run builds only when the backend can build on its own (see
[Build Configuration](build.md#automatic-build)). Each build, this one and
one that you start, goes to `.fw-context/build/<variant>/out`, or
`.fw-context/build/default/out` for a project without variants.
`--build` and `--background` are mutually exclusive, except for this
automatic build.

When a run stops without an index (the build is not there, or the run
failed), fw-context writes the reason to `build_problem.json` next to the
index. Each answer of a query tool of the project gives the state
`"failed"` with that text (see [The state of the index](#the-state-of-the-index)).
`get_project_status` gives it as a reason, with the status
`reindex_needed`. The next run that ends well with the build there removes
the file. `get_project_status` removes a reason that said the build is not
there when it finds the build again. A failed automatic build is not
paused: the next background run builds again.

#### The state of the index

When the index of the build of a query is not current, each answer of a
query tool says so. The maintenance tools do not:
`get_project_status` and `get_build_info` give `reindex_state` of each
build, from the full check of the build.

- A dict gets `index_state` and `index_warning`. For `"failed"` it also
  gets `build_warning`, the text of the failed run.
- A list gets a leading row `{"warning": …, "index_state": …}`.
- No `index_state` means that the quick checks of the query found no
  reason. They can miss a changed header, a changed file that the answer
  does not name, or a new source file when the answer is not empty.
  `get_project_status` does the full check.

| `index_state` | When | What the caller does |
|---|---|---|
| `"reindexing"` | The index is not current, and an index run is active. | Waits, and repeats the call or `get_project_status` after about 30 s. Uses the old answer only when the user says so. |
| `"pending"` | The index is not current, and the watcher scheduled an index run. | The same as `"reindexing"`. |
| `"failed"` | The index is not current, and the last index run failed. | Gives the text to the user: it names the command. |
| `"not_scheduled"` | The index is not current, and no index run updates it. | Tells the user to run `fw-context index` (`--build` after a checkout, and for a new source file that fw-context does not build in the background). Where fw-context does not do that build (a database of the user, a build in an IDE, or a build system that fw-context does not know), the warning tells the user to make `compile_commands.json` outside of fw-context, then to run `fw-context index`. The warning and `get_project_status` name the same action. When the query cannot read the config, the warning names no command and sends you to `get_project_status`. |

An index run that is active or scheduled wins over `"failed"`: during the
run that repairs a failed run, the command of the failed run starts a
second run of the same work.

A query checks only what costs no scan: the checkout, a changed
`compile_commands.json`, the schema, the row format, an index without
full header tracking, and the files that the answer names. While an index
run is active or scheduled, and these checks find no reason, it also
compares the hash of each file of its build: the state then tells whether
the run changes this build. A build that the run does not change stays
current. Changed headers, a changed file that the answer does not name
while no run is active or scheduled, and a new source file in an answer
that is not empty are in the status tools only (`get_project_status`,
`get_build_info`).

**When another index run is in progress:**

Only one index run can own a project's index at a time. A manual run takes
the index before it builds anything, so a refused run never cleans the build
directory or rewrites `compile_commands.json`. This holds for a single build
and for `[[build.variants]]`.

- **A background run** (the file-watcher daemon starts these): a manual run
  always takes over. It sends `SIGTERM`, and the background run stops with
  exit code 75 (superseded); the daemon retries it later. A run that does
  not stop within 10 seconds gets `SIGKILL`.
- **Another foreground run**: the new run is refused with exit code 69, and
  the message names the PID of that run. Add `--takeover` to terminate it
  the same way and index anyway.
- **A `--background` run** never takes over from another run.

Before it sends `SIGTERM`, the run that takes over writes
`reindex.takeover` in the index directory, with its own PID and the PID of
the run that it stops. Thus the stopped run knows why it stopped:

| Cause of the `SIGTERM` | Exit code | Message | Daemon retries |
|---|---|---|---|
| Another index run takes over | 75 | `Superseded: …` | Yes |
| Anything else (a CI timeout, `kill`, the shutdown of the daemon) | 143 | `Terminated: …` | No |

A run that already released the index when the `SIGTERM` of a takeover
arrives ignores that signal. The other run only wanted the index, and it
already has it.

Each build runs in its own process group. When a run stops, it stops the
whole group, the compilers of `make -j`, `bear` and `pio` included, before
the other run builds in the same directory. A run that `SIGKILL` stops
cannot do that. It records its build group in `reindex.build`, and the next
run that owns the index stops that group first.

The build is not in the session of the terminal, thus it does not get the
`SIGHUP` of a closed terminal. The index run gets it, and stops as with a
foreign `SIGTERM`: `Terminated`, exit code 143, and the build group stops
too.

**The global registry:**

`fw-context index` and `fw-context init` write this project into the global
registry at `~/.fw-context/projects.db`. Then they remove the rows that
nothing on disk confirms. The registry only grew before this, and two rows
of one name make the `project="<name>"` tool selector fail with an
ambiguity error.

A row stays when one of these conditions is true:

- An index database of that project ID is in the index directory.
- The project root holds a `.fw-context/config.toml` that declares that
  same project ID.

The command names each row that it removes, up to five names. To keep the
rows, use `--no-prune`. A live project can look stale: this happens when
its filesystem is not mounted, and its index is not in the default index
directory. If such a row goes, the next `init` or `index` of that project
writes it again.

A `--background` run removes no row. The file-watcher daemon starts those
runs, no flag can reach them, and their output goes to `reindex.log`.

For a multi-variant project, `fw-context index --build` builds and indexes
every variant. Use `--variant`, `--variants`, `--image`, or
`--exclude-image` to narrow the run. See
[Build Configuration → Multi-project and multi-image builds](build.md).

**Generating `compile_commands.json`:**

| Build system | Command |
|-------------|---------|
| **Zephyr** | `west build -b <board> -- -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` |
| **PlatformIO** | `pio run --target compiledb` |
| **Mbed OS** | `bear -- mbed compile --profile release` |
| **CMake** | `bear -- cmake --build build` or `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` |
| **Make** | `bear -- make` |
| **Custom** | `bear -- <your-build-command>` |

### `fw-context search`

Full-text search over indexed symbols from the command line.

```bash
fw-context search "uart_init"
fw-context search "spi transfer" --limit 10
fw-context search "ble_*"                # prefix wildcard
fw-context search '"i2c read"'           # exact phrase
fw-context search "uart_init" --variant nrf52840-dev --image app
```

The search reads ONE build, as each MCP query does. On a project of
several builds, give `--variant` and `--image` of one build. A search
without them, or with the options of no build, gets an error that lists
each build with its options, and the command returns 1. A variant that
the config declares and the index does not hold gets the command that
indexes it.
`fw-context status` lists the builds. A project of one build needs no
option.

### `fw-context status`

Show the state of the index, and of each build of the project.

```bash
$ fw-context status
Project : /home/user/firmware
Status  : reindex_needed
          1 of 2 builds are not up to date — nrf52840-dev/app: not_scheduled. Read reindex_reasons for the action, and builds for each build.
Builds  : 2
  nrf52840-dev/app      reindex_needed (not_scheduled)  symbols=29 464  files=896  indexed=2026-10-07 23:23:56
      compile_commands.json changed after the last index run. Run `fw-context index`.
  nrf52840-dev/mcuboot  ready                           symbols=14 329  files=778  indexed=2026-10-07 23:00:25
```

The command prints what the MCP tool
[`get_project_status`](#get_project_status) gives: the status and the
message of the project, then one line for each build with its status, the
state of the index run (`reindex_state`) when the build is not current,
its counts, and its reasons. A build without a variant name and an image
name shows as `(no name)`. A variant that the config declares and the index
does not hold gets the command that indexes it. A project that is not
initialized, that has no index, or whose config file cannot be used
(`config_error`) gets the message and the command returns 1. A value of a
config file that fw-context did not use gives a warning in the log on
stderr, as in each other command, and the exit code does not change.

### `fw-context list`

List all indexed projects.

```bash
$ fw-context list
PROJECT     PATH                    BUILDS  SYMBOLS  FILES  INDEXED
zephyr-app  /home/user/zephyr-app        7        -      -  2026-10-08 00:18:04
pio-app *   /home/user/pio-app           1    8 586    952  2026-10-04 16:20:45
```

The table shows what the MCP tool [`list_projects`](#list_projects)
gives. A project of several builds shows no counts (`-`): a file that two
builds compile is in the index of each, thus a sum counts it two times.
`fw-context status` gives the counts of each build. `*` marks a project
whose schema or row format is old, or whose compile_commands.json of a
build changed. `fw-context status` gives each reason, also a checkout, a
failed run and modified files. `INDEXED` is the time of the newest build.
`fw-context list -v` adds a line below each project with the path of its
index database (`db=…`).

### `fw-context db`

Manage the index database.

```bash
fw-context db list                   # list all builds
fw-context db stats <hash>           # show statistics for a build
fw-context db delete <hash>          # delete a specific build
fw-context db delete --all           # delete the entire index
fw-context db cleanup                # remove orphaned compile_commands artifacts
```

#### `fw-context db list`

List all rows of builds for a project, with per-row statistics. Each row
shows its label (`variant/image`, or `(no name)`), its state and its
description:

- `queried`, with a `*` marker: the row that the queries of its build
  read, the newest completed row of that `(variant, image)` pair.
- `indexing`: a row that an index run writes, or that a stopped run left.
- `older row`: an older row of a pair, which an interrupted run left.

The row shows the first 12 characters of its `config_hash`.
`fw-context db list -v` shows the full hash of 64 characters.

```bash
$ fw-context db list
Project: my-zephyr-app  path=/home/user/zephyr-app

*a1b2c3d4e5f6  nrf52840-dev/app  queried  branch: main
  First indexed:   2026-10-07 23:00:31     Last indexed:   2026-10-07 23:23:56
  Symbols:         29,464                  Files:          896  Refs:    81,203

*7f8e9d0c1b2a  nrf52840-dev/mcuboot  queried  branch: main
  First indexed:   2026-10-07 22:50:12     Last indexed:   2026-10-07 23:00:25
  Symbols:         14,329                  Files:          778  Refs:    40,117

* = the row that the queries of its build read
```

#### `fw-context db stats`

Show detailed statistics for a specific build: symbols by kind, LLM
analysis coverage, embedding count, reference count, PageRank coverage,
and more.

```bash
$ fw-context db stats a1b2c3d4e5f6
Build:          a1b2c3d4e5f6...
  Label:        nrf52840-dev/app
  Board:        nrf52840dk/nrf52840
  Description:  branch: main
  First indexed: 2026-10-07 23:00:31
  Last indexed: 2026-10-07 23:23:56
  ...

Symbols by kind:
  function                   9874
  macro                      7012
  variable                   4203
  ...
  ---                         ---
  TOTAL                     29464
  Definitions (is_definition=1): 18950

LLM analysis (project): 2980/2980 analyzed
LLM analysis (vendor):  0/5470 analyzed
  Skipped:        0 project, 0 vendor
  analyze_vendor: False
  Complete:       True
Embeddings:     29464 symbols with embeddings
Files:          896
References:     81203
Macros:         7012
Indirect calls: 156
FP assignments: 89
Overrides:      42
Hotspot cache:  20
PageRank cov:   29464 / 29464 symbols
```

#### `fw-context db delete`

Delete a specific build, by its config hash. You can resolve a hash with
the first 12 or more characters.

```bash
fw-context db delete a1b2c3d4e5f6       # delete a build (interactive confirmation)
fw-context db delete a1b2c3d4e5f6 -y    # skip confirmation
fw-context db delete --all              # delete the entire index
fw-context db delete --all -y           # delete all without confirmation
```

You cannot delete a row that the queries of its build read (`queried` in
`fw-context db list`) without `--force`. The queries of that build then
read an older row of the build, or no row. You cannot delete an
`indexing` row without `--force`: a run that still writes it writes it
again when it ends, without the data of the delete. You cannot delete the
only build that the queries read: use `--all` instead.

#### `fw-context db cleanup`

Remove orphaned `compile_commands.<hash>.json` artifacts that have no
matching build in the index.

```bash
fw-context db cleanup                  # clean up for current project
fw-context db cleanup --project /path  # clean up for specific project
```

### `fw-context init`

Provision a project in one command: audit and fix dependencies, generate
`compile_commands.json` when possible, register AI assistants, and print
a checklist of the remaining manual steps.

```bash
fw-context init                         # full provisioning (all detected assistants)
fw-context init --quick                 # skip AI tool registration only
fw-context init --tool claude-code      # specific tool only
fw-context init --dry-run               # preview without writing
fw-context init --force                 # overwrite collisions
fw-context init --list-tools            # show supported tools
fw-context init --skip-doctor           # skip the dependency audit
fw-context init --skip-build            # skip compile_commands.json generation
fw-context init --non-interactive       # disable prompts (CI/pipe)
fw-context init --name NAME             # project name in the global registry
fw-context init --no-prune              # keep the stale global-registry rows
fw-context init --scope global          # write the global instruction files
fw-context init --instructions-only     # write the instructions, skip MCP registration
```

`--scope {all,global,project}` selects the instruction files, the skills
and the agents that `fw-context init` writes. The default is `project`:
only the files of the current project. `global` writes only the files in
your home directory, and `all` writes the two. Without `--tool`, the scope
also selects the AI tools: `global` takes the tools that fw-context
detects on the machine, `project` and `all` also take the tools that the
project uses.

`--instructions-only` writes the instructions, and does not register the
MCP server.

`fw-context init` runs these steps, in order:

1. **Dependencies** — audit and auto-fix fixable issues, such as missing
   pip packages. Model pulls (`ollama pull`) never run in this step.
   `fw-context init` never removes a config value: only
   `fw-context doctor --fix --only config`, or a full
   `fw-context doctor --fix`, does that. `--skip-doctor` skips this step.
2. **Build system** — detect the build system and its parameters, or ask
   for a missing value (see the interactive fallback below).
3. **Project ID** — generate the ID, and register the project in the
   global registry.
4. **Build environment** — detect the environment that the build needs on
   this machine, and write it to `.fw-context/local.toml`.
5. **Config files** — create the global config, `.fw-context/config.toml`
   and `.fw-context/local.toml` from their templates, or add the options
   that a file does not have.
6. **Build** — generate `compile_commands.json` when the project is
   buildable. A project of several variants builds with
   `fw-context index --build`.
7. **AI tools** — register the MCP server and inject the instructions.
8. **Project files** — add the `.gitignore` entries, and install the
   skills and the agents.
9. **LLM** — configure the LLM backend (see the LLM step below).
10. **Embedding model** — detect the embedding model, and save it one time.
11. **Checklist** — print the remaining manual steps, grouped by category.

**Interactive fallback.** When build-system detection fails (no project
markers, no board, no FQBN), `fw-context init` asks for the missing value.
Each prompt has a default; press Enter to accept it. The answers are
written to `.fw-context/config.toml`. In a pipe, or with
`--non-interactive`, the missing value goes to the checklist instead.

The command is idempotent. A second run asks `Change? [y/N]` for each
value of the build that the project config sets (the build system and its
parameters), and for an LLM backend that a project file sets. Press Enter
to keep the current value.

**The LLM step.** The step tells where the LLM backend comes from, and
then shows a menu. Option 3 sets `[llm] analyze_symbols = false` in
`.fw-context/local.toml`: the LLM analysis stops, and the embeddings
stay. The other options depend on the file that sets the backend:

- Only the global config sets it: option 0 "Use the global settings" is
  the default (Enter), and writes nothing.
- `.fw-context/local.toml` sets it: the step asks `Change? [y/N]`. When
  the global config also sets it, option 0 after `y` removes the `[llm]`
  backend keys from `.fw-context/local.toml`, and the global settings
  then apply. Enter keeps the keys.
- `.fw-context/config.toml` sets it: the step asks `Change? [y/N]`, and
  the menu has no option 0.
- No file sets it: Enter skips the step.

In non-interactive mode, the LLM step writes nothing. When only the global
config sets the backend, the step prints the settings. When no file sets
it, the step prints how to configure it.

**The embedding model.** When no config file sets `[llm] embed_model`,
fw-context detects the model from the hardware. Step 10 saves that name
one time to the global config, thus a later detection cannot select a
different model. Nothing is saved when the LLM is off.

`--quick` skips step 7, and the skills and the agents of step 8. It still
creates the project ID, the config files, the `.gitignore` entries, and
runs the dependency audit, the build, and the checklist.

| Tool | ID | MCP registration (global) | Instructions, `--scope project` | Instructions, `--scope global` |
|------|----|---------------------------|---------------------------------|--------------------------------|
| Claude Code | `claude-code` | `claude mcp add --scope user` | `CLAUDE.md` | `~/.claude/CLAUDE.md` |
| OpenCode | `opencode` | `~/.config/opencode/opencode.json` | `AGENTS.md` | `~/.config/opencode/rules/fw-context.md` |
| Kilo Code | `kilocode` | `~/.config/kilo/kilo.json` | `AGENTS.md` | `~/.kilo/rules/fw-context.md` |
| Codex | `codex` | `codex mcp add` | `.codex/rules/fw-context.md` | `~/.codex/rules/fw-context.md` |
| Cursor | `cursor` | `~/.cursor/mcp.json` | `.cursor/rules/fw-context.mdc` | `~/.cursor/rules/fw-context.mdc` |

The MCP registration is global for each tool, and does not change with
`--scope`. Kilo Code inherits from OpenCode: when fw-context detects
OpenCode, it skips Kilo Code. Only `--tool kilocode --force` sets up Kilo
Code then.

### `fw-context quickstart`

Alias for `fw-context init --quick`. Provision the project, but skip AI
tool registration only. Use this command for CI, or when the AI tools
already work.

```bash
fw-context quickstart                  # deps + build + config + checklist
```

This command shares the `--project`, `--dry-run`, `--skip-doctor`,
`--skip-build`, `--non-interactive`, and `--name` flags with
`fw-context init`.

### `fw-context init-variants`

Manage `[[build.variants]]` in `.fw-context/config.toml`, for a project
that is already initialized. This command adds, removes, or lists build
variants, without re-running the agent and dependency provisioning.

```bash
fw-context init-variants list                          # list declared variants
fw-context init-variants add --name <name> --board <board>
fw-context init-variants add --name <name> --board <board> --description "text"
fw-context init-variants add --name <name> --build-dir build/<name>
fw-context init-variants add --name <name> --env BOARD_ENV=DEV
fw-context init-variants remove <name>
```

| Subcommand | Description |
|-----------|-------------|
| `list` | Read `[[build.variants]]` from `config.toml` (what the config declares) |
| `add` | Append one variant to `config.toml` |
| `remove` | Remove one variant from `config.toml` |

`list` shows what the config declares. It does not show what fw-context
has indexed. After you add or remove a variant, run `fw-context index
--build` to index the new set. The MCP `list_variants` tool shows what is
actually indexed.

### `fw-context export`

Export the symbol index as portable JSON.

```bash
fw-context export                     # stdout
fw-context export -o index.json       # file
fw-context export --no-refs           # symbols only
fw-context export --variant nrf52840-dev --image app -o app.json
```

The format is `fw-context-export/1`. This format is suitable for sharing
between machines, for debugging, or as input to other tools.

An export holds ONE build. `--variant` and `--image` select it as in
`fw-context search`. The JSON names the build: `config_hash`, `variant`,
`image` and `board`.

### `fw-context analyze`

Run LLM symbol analysis on an already-indexed project. This command is
useful when the index was built with `--no-analyze` and you want to add
analysis later. This command is also useful when you want to regenerate
the analysis for all symbols.

```bash
fw-context analyze                        # analyze current project
fw-context analyze --project /path        # specific project
fw-context analyze --variant nrf52840-dev --image app   # one build
```

Without `--variant` and `--image`, the command analyzes each build of the
project, and prints one line for each build. The analysis is
content-addressed, thus a symbol that one build already analyzed is not
sent to the model again. With the options of one build, it analyzes only
that build. Options that name no build get the error of
`fw-context search`, and the command returns 1. The command does not
change the time of the index run of a build.

fw-context generates the analysis for each function or method. fw-context
uses the full function body (through the libclang extent) and the callee
names, as supplementary context. fw-context stores the results in the
`llm_analysis` table, and denormalizes the results into the FTS5 index. So
symbols become searchable by their purpose, not only by their name.

Configure with `[llm] analyze_symbols`, `[llm] model`, `[llm] analyze_vendor`,
and `[llm] num_ctx` in `~/.fw-context/config.toml`.

### `fw-context cache stats`

Show cache statistics for one or both tiers. The `--remote` flag queries
the server in real time, and shows a per-model breakdown with percentages.
When you run this command inside a project directory that has an existing
index, this flag also shows how many of that project's symbols the server
has cached.

```bash
fw-context cache stats                    # both tiers
fw-context cache stats --remote           # Tier 2 only (remote server)
```

Output:
```
$ fw-context cache stats --remote
Remote cache (Tier 2): https://fw-cache.montyho.com
  Total entries: 14486
  Newest entry:  2026-07-06 11:32:23
  Models:
    qwen2.5-coder:14b: 14476 (100%)
  Project cache: 3138/3138 cached (452361ffbf84f774)

$ fw-context cache stats
Local cache (Tier 1): 3160 entries  (/home/user/.fw-context/llm_cache.db)
Remote cache (Tier 2): https://fw-cache.montyho.com
  Total entries: 14486
  ...
  Project cache: 3138/3138 cached (452361ffbf84f774)
```

### `fw-context cache clear`

Delete cache entries for one or both tiers.

```bash
fw-context cache clear                    # local cache (Tier 1) only
fw-context cache clear --remote           # project's entries from server (Tier 2)
fw-context cache clear --all              # both tiers
fw-context cache clear --remote -y        # skip confirmation prompt
```

The `--remote` flag reads all the content hashes from the project's
`llm_analysis_cache` table, and sends the hashes to the server's
`POST /cache/clear` endpoint. This flag requires `[cache_server]` in
`.fw-context/local.toml`, and a token with `can_write` permission.

All clear operations are safe. fw-context rebuilds the cache entries
automatically, on the next `fw-context index --analyze` or
`fw-context analyze` run.

### `fw-context cache push`

Push all local cache entries to the remote cache server. By default, this
command uses overwrite mode, with `X-Cache-Overwrite`. So a newer local
entry replaces an older remote entry.

```bash
fw-context cache push                       # push all, batch size from config (100)
fw-context cache push --batch 500           # larger batches for faster transfer
```

This command requires `[cache_server]`, configured with `can_write` and
`can_overwrite` permissions. This command reports progress in batches.

### `fw-context cache remote-init`

Interactive wizard to configure the remote cache server connection. This
wizard prompts you for the URL and the token. This wizard verifies
connectivity, and writes `[cache_server]` to `.fw-context/local.toml`.

```bash
fw-context cache remote-init                 # configure for current project
fw-context cache remote-init --project DIR   # configure for specific project
```

The wizard:

1. Shows the currently configured URL (if any)
2. Prompts for the server URL (press Enter to keep current or accept default)
3. Prompts for the authentication token (required — paste your read or read+write token)
4. Verifies the connection: calls ``/health`` then ``/cache/stats`` with the token
5. Writes the ``[cache_server]`` section to ``local.toml`` (idempotent)

Typical output:

```
No remote cache configured.

Cache server URL [https://fw-cache.example.com]: https://fw-cache.montyho.com

Token (paste your read or read+write token): <token>

Verifying connection to https://fw-cache.montyho.com ...
  Connected. Server has 14486 cached entries.

Remote cache configured: https://fw-cache.montyho.com
Config written to: /path/to/project/.fw-context/local.toml
Run 'fw-context cache stats --remote' to verify.
```

### `fw-context finetune`

Fine-tune the embedding model on project code. This command generates
synthetic training pairs from the symbol index, and trains the embedding
model to produce better vector representations of your codebase.

```bash
fw-context finetune                          # fine-tune on current project
fw-context finetune --project /path          # fine-tune on specific project
fw-context finetune --sample-limit 500       # limit training pairs
fw-context finetune --epochs 5               # number of training epochs
fw-context finetune --batch-size 32          # training batch size
```

This command requires the `st` extra: `pip install fw-context-mcp[st]`.
You can control the number of synthetic pairs with `--sample-limit`
(default 2000). More epochs and pairs give better quality, but take
more time.

### `fw-context doctor`

Audit the installation: check dependencies, database integrity, disk
space, and configuration. This command reports issues, and can attempt
automatic repairs.

```bash
fw-context doctor                    # audit, report issues
fw-context doctor --fix              # audit and attempt repair
fw-context doctor --json             # machine-readable JSON output
fw-context doctor --project /path    # audit specific project
fw-context doctor --only clang-resource,disk-space   # run only these checks
fw-context doctor --fix --only config                # repair only the config files
```

`--only NAMES` takes a comma-separated list of the check names below. An
unknown name stops the command with exit code 2. `--fix --only <names>`
without `config` does not repair the config files.

`fw-context doctor` runs these checks, in this order:

- `pysqlite3` — the `pysqlite3` package is installed, and the `sqlite3`
  redirect of fw-context is active.
- `sqlite-ext` — SQLite can load extensions (`enable_load_extension()`).
- `sqlite-vec` — the `sqlite-vec` extension loads into SQLite.
- `libclang-python` — the clang Python bindings are installed, version
  18.1.1 or later.
- `libclang-so` — fw-context finds the native libclang shared library,
  and the library loads.
- `clang-resource` — the clang compiler headers of the libclang major
  version are there. Not critical: without them, some GCC headers do not
  parse, and the index holds the errors.
- `watchfiles` — the `watchfiles` package is installed. The background
  file watcher needs it.
- `tomli-w` — the `tomli-w` package is installed. `fw-context init` needs
  it to write the config files.
- `config` — the config files load (see below).
- `ollama` — the Ollama server answers. `skipped` when the LLM is off.
- `chat-model` — the chat model is installed on the Ollama server of the
  chat calls. `skipped` when an OpenAI-compatible API runs the chat model.
- `embed-model` — the embedding model is installed on the Ollama server.
- `db-integrity` — `PRAGMA quick_check` of the index database. When it
  finds damage, the check runs `PRAGMA integrity_check`.
- `obsolete-files` — the files, directories and config keys of an older
  fw-context. `--fix` removes them.
- `disk-space` — the free space on the disk of the index. Less than
  512 MB makes the check `degraded`, less than 128 MB makes it an error.

The check `config` names a config file that cannot be read, or that is
not UTF-8 or not valid TOML. It also lists each value that fw-context
cannot use: a value of the wrong type, a section that is not a table, a
variant without a name, a URL that is not valid. See
[A config file that fw-context cannot use](configuration.md#a-config-file-that-fw-context-cannot-use).
`--fix` removes each such value. Then the value of a lower file, or the
default, applies. Use `--fix --only config` to do only this fix: a full
`--fix` can also pull a missing model. The checks that read the config
(Ollama, the models, the database and the disk) are `skipped` while the
config does not load. After `--fix` repairs the config, they run. A value
that fw-context did not use, because a lower file or the default applies
(a timeout or a switch, for example), makes the check `degraded` and not
critical: the other checks run. When `--only` names one of these checks
and the config does not load, the command also gives the check `config`.

When you run this command with `--fix`, it attempts to repair fixable
issues automatically. The command reports issues that it cannot fix, and
tells you how to fix them manually.

`--fix` pulls a missing model onto the Ollama server that the check
asks, also when that server is on a different machine:

- The embedding model: the server of `[llm] ollama_url`.
- The chat model: the Ollama server of the chat calls. This is the server
  of `ollama_url`, or the Ollama server that `chat_api_base` names. When
  `chat_api_base` names an OpenAI-compatible API, that API runs the chat
  model, and the check `chat-model` is `skipped`.

The manual command sets `OLLAMA_HOST`, because the `ollama` CLI reads the
server from it. It sets it also for the default server, because the
`OLLAMA_HOST` of your shell can name a different server. For example:
`OLLAMA_HOST=http://gpu-box:11434 ollama pull qwen2.5-coder:14b`. On
Windows, the command has the syntax of PowerShell. In `cmd.exe`, first
run `set OLLAMA_HOST=<url>`, and then `ollama pull <model>`.

### `fw-context cleanup`

Remove the files, directories and config keys that an older fw-context
wrote and that no release reads now. The list is in
[Obsolete keys and files](configuration.md#obsolete-keys-and-files).
`fw-context index` and `fw-context doctor --fix` do the same cleanup, thus
you need this command only to see the list, or to clean now.

```bash
fw-context cleanup --dry-run         # show the list, change nothing
fw-context cleanup                   # remove the items in the list
fw-context cleanup --project /path   # clean a specific project
```

The command gives one line for each item. Exit code 1 means that a path
could not be read or changed.

`fw-context db cleanup` is a different command: it removes the per-build
files of the index that no build uses.

### `fw-context watch`

Manage the background watcher daemon that auto-reindexes changed source files.

```bash
fw-context watch status              # show daemon status
fw-context watch restart             # restart the daemon
fw-context watch restart --project /path  # specific project
```

#### `fw-context watch status`

Show the watcher daemon status for the current project: whether the daemon
is running, its PID and uptime, the modified file count of each build, and
the state of the index:

- `Index:      running`: an index run holds the index, or a `reindex_file`
  or a `reset_index` writes to it.
- `Index:      scheduled`: the daemon has work that it did not complete.
  The work waits for the debounce, for a manual operation, or for the
  start of the run.
- `Index:      idle`: no run is active, and the daemon has no work.

```bash
$ fw-context watch status
Project:    my-zephyr-app
Path:       /home/user/zephyr-app
DB dir:     /home/user/.fw-context/index/a1b2c3d4
Daemon:     running (pid 12345, uptime 3600s)
Socket:     /home/user/.fw-context/index/a1b2c3d4/daemon.sock (active)
Modified:   3 file(s) in build nrf52840-dev/app
Modified:   0 file(s) in build nrf52840-dev/mcuboot
Index:      idle
Last index: [45/45] main.cpp: unchanged
```

#### `fw-context watch restart`

Stop the current watcher daemon, with `SIGTERM`. If the daemon does not
stop, this command falls back to `SIGKILL` after 3 seconds. This command
cleans up leftover socket, pid, and lock files, and spawns a fresh daemon.
This command verifies that the new daemon responds to a ping, before it
returns.

```bash
$ fw-context watch restart
Stopping daemon (pid 12345)...
Old daemon stopped.
Starting new daemon...
Daemon restarted successfully.
```

### `fw-context version`

Show version information.

```bash
$ fw-context version
fw-context-mcp <version>
```

---

## MCP tools

Your AI assistant calls these tools over JSON-RPC. The MCP server keeps
one connection to each index database for the life of the process. A lock
lets one query at a time use that connection.

Each tool except `get_project_info` accepts an optional `project_root`
parameter, which defaults to a value that fw-context detects automatically
from the current working directory. Every input block below shows this
parameter, but you typically omit it, because the automatic detection
handles the common case. `get_project_info` takes only `project_id`.

**A question about a different project.** Every tool answers about one
project. To ask about a project that is not the project of the current
directory, give the `project` parameter — the `name` or the `project_id`
that `list_projects` shows. It is an alternative to `project_root`, which
takes the root path. Give one of the two, not both. The `project`
parameter must go on each call, `get_project_status` included, because
fw-context keeps no session state. The exception is `get_project_info`,
which takes no `project` and no `project_root`.

```
list_projects   →  {"name": "boot-loader", "project_id": "<32 hex chars>", "root_path": "/…/boot-loader"}
lookup_symbol   →  {"name": "main", "project": "boot-loader"}
```

A parameter name that no tool declares causes an error that names it.
Earlier releases dropped such an argument without a message, and the tool
then answered about the project of the current directory.

**Multi-variant projects.** For a project with `[[build.variants]]`, every
query tool also accepts `variant` and `image` parameters. When you omit
`variant`, fw-context uses `[build] default_variant`, or returns an error
that lists the valid names. Set `variant` to `"*"` to query every variant.
A multi-variant result annotates each record with its `variant` and
`image`. Call `get_active_build` to see the variants and images that
fw-context has indexed.

### Build / project state

#### `get_project_status`

Project-level overview: the builds the project holds, which one a query
without an explicit selector reads (`active`), each build's indexed-state
(`facts`/`worlds`/`stale`) and the total facts.  A pure M6-store read,
usable on v2-only indexes that keep no legacy `build_configs`.

#### `get_build_info`

Detail of one build: its `facts` count, `fact_kinds` breakdown, how many
Worlds reference it, and its `stale` flags.  Unknown builds return a
named error with the accepted names.

#### `get_build_health`

Per-build indexed-state report for every build of the project
(`build_name`/`facts`/`worlds`/`stale`).  `stale` is True when a build
was re-indexed more than once or carries no facts.

### Search & lookup

#### `search_code`

Full-text search with FTS5 syntax.

```
Input:  {"query": "uart init", "project_root?": "/path/to/project", "kind?": "function", "limit?": 20}
Output: [{"name": "uart_init", "qualified_name": "drv::uart_init", "kind": "function",
          "file": "/path/src/uart.c", "line": 42, "is_definition": true,
          "signature": "void uart_init(int baudrate)", "docstring": "Initialize UART",
          "is_template": false, "is_virtual": false, "is_pure_virtual": false,
          "llm_analysis": {"summary": "Initialize the UART peripheral…",
                           "inputs": "baudrate…", "outputs": "…"},
          "enum_value": null, "_fallback": "fts5+kind"}, …]
```

Enum constants include `enum_value` (the integer value) when non-None.
Results include `is_template`, `is_virtual`, `is_pure_virtual` flags
(boolean, always present). When the symbol is a template instantiation,
`template_usr` references the template definition. When the symbol is
a member (method/field/nested type), `parent_usr` references the parent class.
When fw-context generates LLM analysis (with `fw-context index --analyze`),
a result carries `llm_analysis` — `{summary, inputs, outputs}`, with
plain-English descriptions.

A model wrote that text, and the code did not. It is nested for that
reason: a flat key would sit beside `signature` and `docstring`, which
come from the source, with nothing to separate a description from a
guess. Use `llm_analysis` to find a symbol. Quote `signature`,
`docstring`, or the `source` of `get_source`.
`get_active_build().analysis.model` names the model — one for each index.

**Precision:** FTS5 indexes the qualified name, thus a local variable
matches through the name of its parent. A query for `sensor` also
returns `V`, `ret`, and `tmp_value` when they live inside
`read_sensor_value`. Pass `kind` to exclude them, or read `kind` on
each result before you act on it.

**Progressive relaxation:** when the initial FTS5 search returns nothing, the
tool automatically broadens the search in up to six steps:

1. *FTS5 with kind filter* — the original query with the user-provided `kind`
   constraint.
2. *FTS5 without kind filter* — drops the `kind` constraint (users often guess
   the wrong kind for a symbol).
3. *`name_tokens` substring match* — searches the pre-computed CamelCase/
   snake_case token column (for example, fw-context indexes `BuildType` as
   `"build type"`). Requires at least N-1 of N query terms to match.
4. *Single-term docstring LIKE* — when you give only one query term, and the
   token-based steps find nothing, does a raw LIKE search over the
   docstring column, to catch terms that the FTS5 tokenizer did not match.
5. *Individual term FTS5* — searches each query word separately and merges
   the results.
6. *Macro FTS5 fallback* — searches the `macros_fts` table for matching
   `#define` names and values (`kind="macro"`, `_fallback="macros_fts"`).

Results from fallback steps carry `_fallback` indicating which method
succeeded: `"fts5"`, `"name_tokens_like"`, `"docstring_like"`,
`"individual_terms"`, or `"macros_fts"`. Results from the primary FTS5 path have
`"_fallback": "fts5+kind"` or omit the field.

**FTS5 syntax:**
- `uart*` — prefix wildcard
- `"spi transfer"` — exact phrase match
- fw-context treats an underscore as a word separator, so `modem_init`
  asks for the two tokens next to each other and misses
  `modem_parser_oob_init`
- **`search_code` and `search_content`** give every bare term a trailing
  `*` and OR-join them: `modem init` becomes `modem* OR init*`. Prefer
  single-word queries
- **`search_bodies`** sends the query as written: a space is an AND of two
  exact tokens, and no wildcard is added
- Punctuation is not searchable in any of them. FTS5 cannot parse
  `.attach(` as a term, so the query is repaired into the phrase
  `".attach("` — and the tokenizer drops punctuation inside a phrase too,
  so what runs is the word `attach`. In `search_bodies` such a result
  carries `_fallback: "sanitized"` and `_query_used`; a query FTS5 accepts
  (`NEAR(a b)`, `^term`, a column filter) always runs untouched

**Kind filter:** `function`, `method`, `constructor`, `destructor`, `class`, `struct`, `union`, `enum`, `enum_constant`, `typedef`, `variable`, `field`, `namespace`

#### `search_bodies`

Find patterns in the **text of a definition** — the code inside its extent.

Searches the stored text of every definition, and a definition is not only
a callable. Measured on one project of 60,877 symbols, the text covers:

- Callables — `function`, `method`, `constructor`, `destructor`.
- Types — `class`, `struct`, `union`, `enum`, `namespace`. An enum
  constant, a bit field, and a member declaration such as
  `InterruptIn _pin;` are inside the body of the type that holds them.
- Definitions of data — `varglobal`, `varlocal`, `typedef`. A table with
  a multi-line initializer is found by its content.

A match on a type reports the type as the result. A query for one enum
constant thus answers with the enum, and `match_lines` gives the line of
the constant itself.

**Only the text matches.** The query is bound to the stored body: a hit
in the name, the signature, the docstring or the `llm_analysis` of a
symbol is not a hit here. Measured on one project, `sensor` used to give
36 results of which 22 matched only through a summary that a model wrote
— untrusted text that cannot be cited, and a `_match_snippet` with no
match in it. Use `search_code` to reach a name or a concept. A column
filter you write yourself (`summary : sensor`) overrides the binding.

Text that belongs to **no** definition is out of reach: `#define`,
`#include`, `#ifdef`, `extern "C"`, and a comment or declaration at file
scope. Use `search_content` for those.

**The stored text is ifdef-filtered.** A line of an inactive `#if` branch
is blank in the stored body, and line numbers do not move. An empty
result can thus mean that the pattern is only in a dead branch: the build
of the query does not compile it.

```
Input:  {"query": "attach", "project_root?": "/path/to/project", "kind?": "function", "limit?": 20, "project_only?": true}
Output: [{"name": "setup", "qualified_name": "setup", "kind": "function",
          "file": "/path/src/main.cpp", "line": 55, "is_definition": true,
          "signature": "void setup()",
          "_match_snippet": "…_timeout.<b>attach</b>(callback(&led_blink, 1000))…",
          "match_lines": [58],
          "source": "… (the text of the definition)",
          "_source_truncated": true}]
```

`line` is the first line of the definition, which in a long function is
far from the match. Cite from `match_lines`, never from `line`. The name
carries no leading underscore for a reason: a field the caller must cite
is an answer, while an `_`-prefixed field (`_match_snippet`, `_fallback`,
`_source_truncated`) says where the answer came from.

`_source_truncated` says that `source` is cut. A callable keeps 2000
characters, any other kind 500 — the body of a type is mostly members
that the match has nothing to do with, and `_match_snippet` already
carries the match in context. `get_source` gives the whole text.

- **When to use `search_bodies` vs. `search_code` vs. `search_content`:**
  - `search_bodies` — which **definition** holds the pattern (what the
    code does or declares): `attach`, `SELF_TEST`, `InterruptIn`
  - `search_content` — which **files** a topic touches, and the
    preprocessor and file scope: `extern "C"`, `#define`, `#include`
  - `search_code` — find symbols by **name**:
    `modem init`, `interrupt handler`

  `search_content` is the complement of `search_bodies`, not its
  fallback: it covers the same definitions plus the text between them,
  and it widens the query. Measured on one project, `SELF_TEST` gave 6
  files there and 5 definitions here — the extra file held the comment
  `Self tester`, which the literal query cannot reach. For the footprint
  of one feature, run both.

**FTS5 query tips for `search_bodies`:** the query goes to FTS5 as
written. A space is an AND of two exact tokens and no wildcard is added,
so `SELF_TEST` misses `Self tester` while `SELF_TEST*` finds it. A single
word is the broadest form: `"attach"` reaches every `.attach(...)`
pattern in the codebase.

A query the engine refuses is repaired and re-run once, and the results
then carry `_fallback: "sanitized"` with `_query_used`. Only what FTS5
rejects is touched, so `NEAR(a b)`, `^term` and a column filter keep
their exact meaning. When the repair cannot help either — an unbalanced
quote, a bare operator — the answer is a `warning` with a `hint`, never
an empty list.

Results include `_match_snippet` — a highlighted excerpt showing each
match in context with `<b>…</b>` tags. Project code sorts before vendor
code. Set `project_only=True` to filter to application code only.

#### `search_content`

Find patterns in **full file content** — the whole file, and not only the
text that belongs to a definition.

Searches **ifdef-filtered** file text: only the code that actually
compiles for the build of the query. fw-context replaces an
inactive `#ifdef` branch with blank lines, and keeps the original line
numbers.

```
Input:  {"query": "InterruptIn", "project_root?": "/path/to/project", "limit?": 20, "project_only?": false}
Output: [{"file": "/path/src/main.cpp", "language": "cpp",
          "mtime": "2026-06-05T09:35:18", "_match_snippet": "…InterruptIn…",
          "match_lines": [29, 105]}]
```

Covers the text that belongs to no definition, which is what
`search_bodies` cannot see: `#define`, `#include`, `#ifdef`, `extern "C"`,
and a comment at file scope. Results are file-level, with one entry for
each matching file. Use `search_bodies` when you want the symbol that
holds the match.

`match_lines` gives the lines of the file that hold a query term, up to
20 of them — the line numbers of the file itself, because an inactive
`#ifdef` branch is a blank line and the count never shifts. The field is
absent when FTS5 matched a variant of the token that the term is not a
substring of: `SELF_TEST` matches a file that writes `Self tester`, and
no line there holds `self_test`. Read `_match_snippet` in that case.

When `files_fts` is missing, in a legacy index, this tool falls back to a
LIKE search on `files.content`. The results include `_fallback: "like"`,
and no snippet highlighting. Run `fw-context index` to upgrade. A query
that FTS5 refuses to parse takes the same path and adds a `warning` with
a `hint` at the head of the list.

**FTS5 query tips for `search_content`:** fw-context gives every bare
term a trailing `*` and OR-joins them. Prefer single-word queries.

#### `lookup_symbol`

Find a symbol by name (exact or prefix match). Searches functions, methods,
classes, enums, typedefs, variables, fields, and **macros**.

```
Input:  {"name": "uart_init", "project_root?": "/path/to/project", "exact?": true, "limit?": 50}
Output: [{"name": "uart_init", "qualified_name": "drv::uart_init", "kind": "function",
          "file": "/path/src/uart.c", "line": 42, "is_definition": true,
          "signature": "void uart_init(int baudrate)", "docstring": "Initialize UART"}]
```

Macro example:
```
Input:  {"name": "CONFIG_UART_BAUDRATE", "project_root?": "/path/to/project", "exact?": true}
Output: [{"name": "CONFIG_UART_BAUDRATE", "kind": "macro",
          "file": "/path/include/config.h", "line": 15,
          "value": "115200", "expanded_value": "115200"}]
```

fw-context sorts definitions before declarations. Use `exact: true` for an
exact name match. The default is a prefix match: `uart` matches
`uart_init`, `uart_write`, and other names with that prefix.

fw-context extracts macros with `clang -dM -E`, using the compiler flags
from `compile_commands.json`. So a macro with an `#ifdef` condition
resolves correctly for the indexed build configuration. Enum constants
include `enum_value` when the value is not `None`.

#### `smart_search`

Natural language → FTS5 keywords via Ollama (optional).

```
Input:  {"query": "how does the modem connect to the network?", "project_root?": "/path/to/project", "limit?": 20}
Output: [
  {"_generated_queries": ["network_reg*", "modem_attach*", "pdp_context*"]},
  {"_rough_queries": ["modem", "connect", "network"]},
  …symbol results…
]
```

Multi-phase pipeline: translate → rough search → LLM query generation
→ FTS5 search → refine → vector re-rank → deduplicate → format.

When you disable Ollama (`[llm] enabled = false`), this tool falls back to
a word-split FTS5 search. Phase 0 auto-translates a non-English query.

#### `semantic_search`

Concept search using pre-computed symbol embeddings. Finds symbols that
are conceptually related to a natural-language query, even when the
query words do not appear literally in the code.

```
Input:  {"query": "parcel locker state machine", "project_root?": "/path/to/project", "threshold?": 0.60, "limit?": 20}
Output: [{"name": "set_shipment", "qualified_name": "Locker::set_shipment",
          "_similarity": 0.72, "_method": "embedding", …}, …]
```

Uses cosine similarity over variable-dimension embeddings (generated during
`fw-context index --embeddings`). Models: mxbai-embed-large → 1024-dim,
qwen3-embedding → 4096-dim. **When to prefer over `search_code`:**
conceptual queries ("power consumption" → `get_load_power`) where keywords
do not match. **When to prefer `search_code`:** known keywords or symbol
names (`"fram_write"`, `"cbor encode"`).

Each symbol holds `name`, `qualified_name`, `kind`, `file`, `line`,
`is_definition`, `signature`, `docstring`, and `_method` (`"embedding"` or
`"search_code_fallback"`). An `"embedding"` result also holds
`_similarity`, the raw cosine similarity. A symbol also holds `summary`,
`inputs`, and `outputs` when `fw-context index --analyze` wrote them (model
text, not a fact), and `_rerank_score` when `llm.reranker_model` is set.
Source-aware ranking multiplies the similarity of project code by 1.2 and
of all other code by 0.85. The multiplied score sets the order, and
`_similarity` does not show it. `threshold` applies to the raw cosine
similarity.

**Pages.** The tool ranks every symbol above `threshold`, as far as one
KNN query reaches: 4096 vector rows. A symbol of several chunks uses
several rows. A page is a slice of that order: windows of 200 raw ranks,
and inside a window the multiplied score, then the symbol id. Thus the
multiplier moves a symbol only inside its window, and the first page is
the one that the tool gave before it paged. `total` counts the whole set.
When the KNN query stops at
4096 rows and its last row is still above `threshold`, the notice holds
`total_capped: true`, and `total` is a lower bound. `limit` is the size of
one page, from 1 to 100. The hint repeats a `threshold` that is not the
default. The reranker changes the order of the first symbols of a page
only, and it moves no symbol to another page. A page after the last
symbol gives one `info` dict that names the total.

**Relevance floor.** When the best raw cosine similarity of all matches is
below 0.68, the result is the page notice and one dict: a `warning` that
the matches are likely unrelated, `_fallback_suggestion: "search_code"`,
`_best_similarity`, and the matches of the page in `_results`.

When the embedding search finds no match above `threshold`, the tool runs
the lexical fallback below, with a `warning` that suggests a lower
threshold. When a phase of the search failed, that `warning` gives the
error of the phase. One dict with `error` means that the query failed.
When the index of the build is not current, a leading row holds a
`warning` and `index_state` (see
[The state of the index](#the-state-of-the-index)).

**Threshold guidance** (mxbai-embed-large):
- `0.50` — exploratory, more results
- `0.55` — balanced, ~1000 results
- `0.60` — high precision (default)
- `0.65` — strict, may miss relevant symbols

Requires Ollama with an embedding model. Falls back to `search_code` if
Ollama is unavailable or disabled.

#### `find_variables`

Find C/C++ variables by name or prefix, and trace who reads or writes them
through the call graph. Splits variables into global (`varglobal`: file,
namespace, or class scope) and local (`varlocal`: inside a function body).

```
Input:  {"name": "g_", "project_root?": "/path/to/project", "kind?": "varglobal", "limit?": 20}
Output: [{"name": "g_debug_level", "qualified_name": "g_debug_level",
          "kind": "varglobal", "file": "/path/src/logging.c", "line": 15,
          "signature": "int g_debug_level",
          "enclosing_function": "<file scope>", "enclosing_class": "",
          "references": [
            {"function": "log_init", "file": "/path/src/logging.c", "line": 42, "ref_kind": "ref"},
            {"function": "log_write", "file": "/path/src/logging.c", "line": 68, "ref_kind": "ref"}
          ]}, …]
```

Each result includes a type signature, for example `"bool timeSet"` or
`"const IPAddress modbus_ip"`. Each result also includes the enclosing
function for a local variable (`"<file scope>"` for a global variable),
and the enclosing class for a static member. Each result includes a
`references` list, showing every function that reads or writes the
variable. This list uses the same `ref_kind` values as `find_references`:
`"call"`, `"ref"`, `"member"`.

Use `find_variables` when you need to:
- understand shared state
- find who modifies a global variable
- trace side effects
- distinguish important globals from loop counters

For a general symbol search, use `search_code` or `lookup_symbol`. For all
references to a specific variable, including reads in expressions, use
`find_references`.

The `kind` parameter accepts `"varglobal"`, `"varlocal"`, or `None` for
both. Results include legacy indexes that still use `kind="variable"`,
from before the split. Reindex the project to get the full benefit of
the split.

### Understanding

#### `get_file_map`

Structural overview of a file: all symbols grouped by kind. This tool
works like a fast table of contents, before you read the whole file.

```
Input:  {"file_path": "src/net_msg.cpp", "project_root?": "/path/to/project", "signatures?": false, "max_per_kind?": 30}
Output: {"file": "src/net_msg.cpp", "total_symbols": 426,
         "symbols": {
           "method":    [{"name": "_is_socket_ok", "line": 140, "signature": "bool _is_socket_ok()"}, …],
           "variable":  [{"name": "_buffer_msg", "line": 105}, …],
           "constructor": [{"name": "ModemMsg", "line": 130, "signature": "void ModemMsg(…)"}, …],
           "function":  [{"name": "memset", "line": 94}, …],
           "struct":    [{"name": "buffer", "line": 3561}, …],
           "enum_constant": {
             "count": 8,
             "subgroups": [
               {"name": "StatusCode", "count": 5,
                "constants": [
                  {"name": "OPERATION_SUCCESSFUL", "enum_value": 1, "line": 21},
                  {"name": "TOKEN_INVALID", "enum_value": -2, "line": 23}, …]},
               {"name": "State", "count": 3,
                "constants": [{"name": "Idle", "enum_value": 0, "line": 10}, …]}
             ]
           }
         }}
```

fw-context groups enum constants into `subgroups`, by the parent enum.
Each subgroup has a `name` (the parent enum), a `count` (the real total,
even when `max_per_kind` limits the `constants` list), and a `constants`
list with `name`, `qualified_name`, `line`, and `enum_value` (the integer
value, when available).

Pass a relative path, such as `src/main.cpp`, or just the filename, such
as `main.cpp` (fw-context matches the suffix). Use this tool instead of
`Read` on large files. fw-context organizes the symbols by kind, in a
single response.

#### `get_source`

Read a symbol's definition body — no LLM, fast.

```
Input:  {"name": "adc_read", "project_root?": "/path/to/project"}
Output: {"name": "adc_read", "kind": "function", "file": "/path/src/adc.c",
         "line": 55, "signature": "uint16_t adc_read(uint8_t channel)",
         "source": "  55  uint16_t adc_read(uint8_t channel) {\n  …\n  70  }"}
```

Uses libclang's `end_line` for exact body boundaries. Falls back to
brace-matching for older indexes. The result also carries `end_line`, thus
`file:line-end_line` is the citation — no counting.

Every line of `source` starts with its line number in the file: four
columns, right-aligned, then two spaces. The `source` of `search_bodies`
is bare, and so is the `content` of `read_file` until you ask for
`line_numbers=True`.

For enum constants, the result includes `enum_value` (the integer value).
For enums, the result includes a `constants` array that lists all the
member constants, with their names and values:

```
Input:  {"name": "BleCmd::StatusCode", "project_root?": "/path/to/project"}
Output: {"name": "StatusCode", "kind": "enum", "file": "/path/src/radio_cmd.h",
         "line": 20, "signature": "",
         "constants": [
           {"name": "OPERATION_SUCCESSFUL", "enum_value": 1},
           {"name": "TOKEN_INVALID", "enum_value": -2}
         ],
         "source": "  20  enum StatusCode {\n  …\n  24  }"}
```

#### `explain_symbol`

Look up a symbol and get a plain-English explanation of its purpose, inputs,
outputs, and side effects. Falls back to a **macro explanation** when the
name matches a macro definition. In that case, this tool returns
`kind: "macro"`, with the expanded value.

```
Input:  {"name": "spi_transfer", "project_root?": "/path/to/project", "context_lines?": 40}
Output: {"name": "spi_transfer", "kind": "function", "file": "/path/src/spi.c",
         "line": 120, "signature": "int spi_transfer(const uint8_t* tx, uint8_t* rx, size_t n)",
         "explanation": "This function performs a full-duplex SPI transfer…\n\nInputs: …\nOutputs: …",
         "llm_analysis": {"summary": "…", "inputs": "…", "outputs": "…",
                          "model": "qwen2.5-coder:14b", "analyzed_at": "2026-06-21T17:51:14"}}
```

Macro fallback:
```
Input:  {"name": "CONFIG_UART_BAUDRATE", "project_root?": "/path/to/project"}
Output: {"name": "CONFIG_UART_BAUDRATE", "kind": "macro",
         "file": "/path/include/config.h", "line": 15,
         "signature": "#define CONFIG_UART_BAUDRATE",
         "value": "115200", "expanded_value": "115200",
         "source": "#define CONFIG_UART_BAUDRATE 115200"}
```

**Pre-computed (instant):** When you build the index with `--analyze` (the
default), symbols have pre-generated descriptions, stored in the
`llm_analysis` table. `explain_symbol` returns these descriptions
instantly. This tool makes no Ollama call, so there is no waiting.

**On-demand fallback (10–30 s):** When no pre-computed analysis exists, for
example when you built the index with `--no-analyze`, or when fw-context
re-indexed the symbol without re-analysis, this tool falls back to calling
Ollama directly. When Ollama is disabled, this tool returns `source` and
`explain_prompt`, so the AI assistant can answer with its own model.

fw-context generates the analysis during indexing. fw-context uses the
ifdef-filtered body that the index holds and the callee names from the
reference index, as context. Code in an inactive `#if` branch does not
reach the model. When every line of a body is inactive, fw-context skips
the symbol and calls no model, because the build compiles none of its
code. When you re-index a file with `reindex_file` and
`with_analysis=True` (the default), fw-context regenerates the analysis
of the changed symbols.

The on-demand fallback reads a window of `context_lines` around the
symbol from the disk. That window is not ifdef-filtered.

When the name matches more than one symbol, the result adds
`ambiguous_warning`, `candidates`, and `candidates_total`, in the same
shape as `get_source`. When the file changed after the last index run,
the result adds `stale: true` and a `stale_warning`.

#### `get_symbol_context`

Rich LLM context — body, callers, and callees in one response. Returns
**all** callers and callees, including vendor and SDK code. The call
graph naturally spans the project and vendor boundaries.

```
Input:  {"name": "modem_connect", "project_root?": "/path/to/project"}
Output: {"name": "modem_connect", "kind": "function",
         "file": "/path/src/modem.c", "line": 210,
         "signature": "int modem_connect(const char* apn)",
         "source": " 210  int modem_connect(const char* apn) {\n  …\n 245  }",
         "callers": [
           {"name": "network_init", "file": "/path/src/net.c", "line": 80, "kind": "function"},
           {"name": "on_registration_timeout", "file": "/path/src/modem.c", "line": 310, "kind": "function"}
         ],
         "callees": [
           {"name": "send_at_command", "kind": "function", "file": "/path/src/modem.c"},
           {"name": "wait_for_urc", "kind": "function", "file": "/path/src/modem.c"},
           {"name": "pdp_activate", "kind": "function", "file": "/path/src/net.c"}
         ],
         "indirect_call_sites": [],
         "resolution": null}
```

For function pointer fields the output includes call sites and resolution:

```
Input:  {"name": "onData", "project_root?": "/path/to/project"}
Output: {"name": "onData", "kind": "field",
         "indirect_call_sites": [
           {"file": "/path/src/main.c", "line": 16,
            "expr_text": "drv . onData",
            "fn_ptr_type": "void (*)(unsigned char *, int)",
            "caller": "test_assign"}
         ],
         "resolution": {
           "assignments_found": 1,
           "call_sites_found": 1,
           "resolved": true,
           "note": "1 function(s) assigned; 1 call site(s); fully resolved"
         }}
```

This tool is designed as one-shot LLM context. This tool answers "what
does this do, and how does it fit?" in a single call. This tool returns
all the direct callers and callees, with no artificial limit.

For a field or variable symbol that has a function pointer type, the
result also includes a `resolution` block:
`{assignments_found, call_sites_found, resolved, note}`. This block
indicates whether the assignments and the call sites are linked (Phase 3).
When the data is incomplete, `resolved` is `false`, with an explanatory
note. The LLM can detect the uncertainty from this signal.

For enums, the result includes a `constants` array, with all the member
constants and their values, in the same shape as `get_source`. Enum
constants include `enum_value`.

#### `read_file`

Read a source file, one page at a time, with ifdef-filtered content —
only code that actually compiles for the build of the query.
fw-context replaces an inactive `#ifdef` branch with blank lines, and
keeps the original line numbers.

```
Input:  {"file_path": "src/modem.c", "project_root?": "/path/to/project"}
Output: {"file": "/path/src/modem.c", "language": "c", "mtime": 1748534400.0,
         "lines": 512, "content": "/* Modem driver */\n\n#include \"modem.h\"\n…"}
```

Unlike a generic file reader, this tool returns build-accurate content.
Code that is behind `#ifdef BOARD_V2` is visible only when the build
defines `BOARD_V2`.

The path can be relative to the project root, such as `src/main.cpp`, or
just the filename, such as `main.cpp`. This tool falls back to the raw
disk content, with a `warning`, when the indexed `files.content` column is
empty. Run `fw-context index` to populate the ifdef-filtered content.

`line_numbers=True` prefixes every line with its number, in the format
that `get_source` uses. `start_line` and `end_line` cut a window out of
the file — 1-based, both ends inclusive, and 0 means no bound on that
side. Reading around a known line costs a fraction of the whole file.

```
Input:  {"file_path": "src/command.h", "start_line": 25, "end_line": 31, "line_numbers": true}
Output: {"file": "/path/src/command.h", "language": "c", "mtime": 1748534400.0,
         "lines": 140, "start_line": 25, "end_line": 31,
         "content": "  25      CLEAR_SD     = 11,\n  …"}
```

`lines` stays the length of the whole file. `start_line` and `end_line`
in the result say which lines the `content` really holds, after the end
was clamped to the length of the file. A negative bound, an `end_line`
before `start_line`, or a `start_line` past the end of the file gives an
`error`.

### Call graph

All graph tools use the cross-reference index. fw-context enables this
index by default. Disable this index with `fw-context index --no-refs`,
or with `[index] index_refs = false`, if you do not need these tools.

#### `find_callers`

Who calls this function? This tool finds direct callers, and indirect
calls through function pointers that fw-context detects in call
arguments, assignments, variable initializers, and struct or array
initializer lists. This tool automatically falls back to a **macro
lookup** when fw-context does not find the symbol as a function or method.

```
Input:  {"name": "uart_write", "project_root?": "/path/to/project", "limit?": 50}
Output: [{"file": "/path/src/main.c", "line": 35, "ref_kind": "call",
          "caller": "main", "caller_kind": "function"},
         {"file": "/path/src/setup.c", "line": 12, "ref_kind": "indirect",
          "caller": "setup", "caller_kind": "function"}]
```

Indirect edges (`ref_kind: "indirect"`) appear when a function pointer
references a function through any of these patterns:
`callback(&Class::method, this)`, `driver.onData = &handleData`,
`void (*fp)(int) = &handler`, or `{.on_data = &handler}`.

For the invocation side — where fw-context actually calls the stored
pointer — use `find_indirect_call_sites`. For linking assignments to call
sites — which specific functions can run at a given call site — use
`find_indirect_targets`.

#### `find_references`

All uses of a symbol — calls, reads, member access, indirect references.
When fw-context does not find the symbol, this tool automatically falls
back to a **macro lookup**. In that case, this tool returns
`ref_kind: "macro_use"` for a file that uses the macro.

```
Input:  {"name": "g_sensor_data", "project_root?": "/path/to/project", "limit?": 50}
Output: [{"file": "/path/src/sensor.c", "line": 12, "ref_kind": "ref",
          "caller": "sensor_task", "caller_kind": "function"}, …]
```

Macro fallback:
```
Input:  {"name": "CONFIG_BUFFER_SIZE", "project_root?": "/path/to/project"}
Output: [{"kind": "macro", "name": "CONFIG_BUFFER_SIZE", "file": "…", "line": 42, …},
         {"file": "…", "ref_kind": "macro_use", "_match_snippet": "…CONFIG_BUFFER_SIZE…"}, …]
```

`ref_kind` values: `"call"` (direct call), `"ref"` (variable read/write),
`"member"` (member access), `"indirect"` (function pointer reference in
arguments, assignments, initializers, or init lists), `"template_ref"`,
`"macro_use"` (macro usage in file).

#### `find_call_path`

Find paths between two functions via BFS in the call graph.

```
Input:  {"from_name": "main", "to_name": "uart_send_byte", "project_root?": "/path/to/project", "max_depth?": 10}
Output: [{"depth": 3, "chain": "main → app_init → uart_write → uart_send_byte"}]
```

Returns up to 5 shortest paths. Requires both symbols to be in the index.

#### `find_all_callers_recursive`

All transitive callers — who calls this, directly or indirectly?

```
Input:  {"name": "gpio_set", "project_root?": "/path/to/project", "max_depth?": 5, "limit?": 50}
Output: [{"name": "led_toggle", "qualified_name": "led_toggle", "kind": "function",
          "file": "/path/src/led.c", "depth": 1}, … (2 steps away), … (3 steps away)]
```

fw-context deduplicates the results. Each caller appears once, at its
shortest distance.

#### `find_callees_recursive`

What does this call, directly or indirectly?

```
Input:  {"name": "main", "project_root?": "/path/to/project", "max_depth?": 5, "limit?": 50}
Output: [{"name": "spi_init", "kind": "function", "file": "/path/src/spi.c", "depth": 1},
         {"name": "spi_transfer", "kind": "function", "file": "/path/src/spi.c", "depth": 2}, …]
```

#### `find_dead_code`

Finds functions that have a definition, but no caller. Returns two categories:

```
Input:  {"project_root?": "/path/to/project", "limit?": 100, "project_only?": true, "exclude_paths?": ["lib/%"]}
Output: [
  {"name": "orphan_fn", "kind": "function", "file": "/path/src/utils.c",
   "signature": "void orphan_fn()", "line": 200,
   "status": "dead", "reason": "no references found — likely unused"},
  {"name": "handler_timeout", "kind": "function", "file": "/path/src/main.c",
   "signature": "void handler_timeout()", "line": 25,
   "status": "possibly_dead",
   "reason": "assigned as function pointer but call sites unresolved",
   "indirect_refs": "src/main.c:60"},
  …
]
```

**`"dead"`** — no references at all (neither calls nor function pointer
assignments). Likely unused.

**`"possibly_dead"`**: code assigns the function to a function pointer
(`ref_kind="indirect"`), but fw-context did not resolve a call site
through that pointer. This means the function **might** run through
unindexed code, or through a type-erased API. Treat this result as
uncertain, not as confirmed dead code. Verify each result with
`find_indirect_targets`, before you delete the function.

Expect additional false positives from entry points (`main`), ISRs,
virtual method overrides, constructors called via factories, and
weak-aliased symbols. fw-context checks only definitions with
`kind IN ('function', 'method', 'constructor', 'destructor')`.

By default, fw-context excludes SDK and vendor paths automatically, with
the `is_project` column, which respects the project's `vendor_paths` and
`project_paths` configuration. Use `exclude_paths` for additional LIKE
patterns on top of this. A `%` matches any suffix, for example `lib/%`.

#### `get_vector_table`

Read the interrupt vector table of the indexed image, and say what services
each interrupt. The vector table is how an interrupt reaches code — nothing
CALLS a handler, the hardware reads a slot and jumps, so this tool reads the
table itself instead of a call graph.

```
Input:  {"project_root?": "/path/to/project", "limit?": 400,
         "unhandled_only?": false, "variant?": null, "image?": null}
Output: [{"slot": 44, "name": "TIM2_IRQHandler", "file": "/path/startup.c",
          "line": 120, "source": "assembly", "status": "c",
          "table_file": "/path/startup.c", "table_line": 88}, …]
```

`status` says what services the interrupt: `"c"` — a C definition outside
assembly runs it; `"assembly"` — a strong assembly definition; `"unhandled"`
— a weak alias of `Default_Handler`, an infinite loop, the interrupt would
stop the device; `"runtime"` — code installs the real handler with
`NVIC_SetVector` at run time; `"dispatcher"` — the slot reaches a shared
dispatcher that decides at run time where to go. The `slot` is the position
in the table; what the position means belongs to the architecture (on
Cortex-M, slot 16 + n is external interrupt n).

Use it to answer "which interrupts does this firmware service", to find the
handler for one interrupt, or — with `unhandled_only=true` — to list every
vector nothing services.

#### `find_hotspots`

Most-called functions ranked by caller count.

```
Input:  {"project_root?": "/path/to/project", "limit?": 20, "project_only?": true, "exclude_paths?": ["lib/%"]}
Output: [{"name": "log_debug", "kind": "function", "caller_count": 147, …},
         {"name": "millis", "kind": "function", "caller_count": 89, …}, …]
```

#### `find_wrapper_callers`

Find wrapper classes that call methods of a driver class. This tool is
useful for understanding an adapter or wrapper architecture.

```
Input:  {"class_name": "UART_DRIVER", "project_root?": "/path/to/project", "limit?": 50}
Output: [{"wrapper_class": "UART", "wrapper_method": "send",
          "driver_method": "UART_DRIVER::send", "file": "/path/src/uart.cpp",
          "line": 45, "ref_kind": "call"}, …]
```

Pass a fully-qualified class name (`hal::UART_DRIVER`) or just the bare
name (`UART_DRIVER`). fw-context groups the results by wrapper class.

#### `find_indirect_call_sites`

Find indirect call sites where a function pointer field or variable is invoked.

```
Input:  {"name": "onData", "project_root?": "/path/to/project", "limit?": 50}
Output: [{"file": "/path/src/main.c", "line": 36, "expr_text": "drv . onData",
          "target_usr": "c:@S@Driver@FI@onData", "target_name": "onData",
          "fn_ptr_type": "void (*)(unsigned char *, int)",
          "caller": "test_assign", "caller_kind": "function"}]
```

Returns locations where code calls a function pointer, through a field
access (`driver.onData(buf, len)`) or a variable (`stored_callback(42)`).
Use this tool to answer *"where does code invoke this function pointer?"*
Combine this tool with `find_indirect_targets`, to answer *"which
functions are assigned to this field?"*

Uses three-tier name resolution: exact name, exact qualified, suffix LIKE.

#### `find_indirect_targets`

Find functions assigned to a function pointer field, variable, or parameter.

```
Input:  {"name": "onData", "project_root?": "/path/to/project", "limit?": 50}
Output: [{"rhs_name": "handler_data", "rhs_qname": "handler_data",
          "fn_ptr_type": "void (*)(unsigned char *, int)",
          "method": "assignment",
          "assign_file": "/path/src/main.c", "assign_line": 14,
          "assign_caller": "test_assign",
          "call_file": "/path/src/main.c", "call_line": 16,
          "call_expr_text": "drv . onData"}]
```

Links assignment sites (`driver.onData = &handler`) to call sites
(`driver.onData(buf, len)`) via the field's USR. Shows the execution
flow: *code assigns handler to onData at line 14, and code calls onData
at line 16, so handler might run at that call site.*

When code assigns a function, but fw-context finds no call site,
`call_file` and `call_line` are `null`. The assignment exists, but the
invocation might be in unindexed code. The `method` field indicates how
fw-context detected the assignment: `"assignment"` for a direct field
assignment, `"call_arg"` for a value passed as a callback argument,
`"var_init"` for a variable initializer, or `"init_list"` for a struct or
array designated initializer.

For the reverse query — where does code call this field — use
`find_indirect_call_sites`.

#### `trace_data_flow`

Trace how data of a given type flows to a target function. **Experimental.**

```
Input:  {"type_name": "SensorData", "to_symbol": "uart_send", "project_root?": "/path/to/project", "max_depth?": 8, "limit?": 15}
Output: [{"source_function": "sensor_read", "source_type": "SensorData",
          "file": "/path/src/sensor.cpp", "line": 120,
          "call_path": "sensor_read → pack_payload → uart_send"}, …]
```

Finds the functions whose signature mentions `type_name`, then looks for
call paths from those functions to `to_symbol`. Does **not** resolve type
transformations, for example CBOR encoding. Use this tool together with
`find_call_path`, to verify specific paths.

### Class analysis

#### `get_inheritance_chain`

Return the C++ inheritance hierarchy for a class or struct — direct bases
(what this inherits from) and direct derived classes (what inherits from
this), with access level and virtual flag.

```
Input:  {"class_name": "UART_DRIVER", "project_root?": "/path/to/project", "transitive?": false, "max_depth?": 10}
Output: {"name": "UART_DRIVER", "qualified_name": "hal::UART_DRIVER",
         "kind": "class", "file": "/path/src/UART_DRIVER.h", "line": 45,
         "bases": [{"name": "SerialBase", "usr": "c:@...", "access": "public",
                     "is_virtual": false, "file": "/path/src/SerialBase.h"}],
         "derived": [{"name": "UART", "usr": "c:@...", "access": "public",
                       "is_virtual": false, "file": "/path/src/UART.h"}]}
```

When `transitive: true`, adds `all_bases` (ancestors BFS) and `all_derived`
(descendants BFS) with `depth` and cycle detection for diamond inheritance.

The `class_name` can be a bare name (`UART_DRIVER`) or qualified
(`hal::UART_DRIVER`). Only classes and structs are valid targets.

#### `get_class_members`

Return all methods, fields, and nested types of a class/struct grouped by kind.

```
Input:  {"class_name": "ModemManager", "project_root?": "/path/to/project"}
Output: {"name": "ModemManager", "qualified_name": "ns::ModemManager",
         "kind": "class", "file": "/path/src/modem.h", "line": 120,
         "members": {
             "method": [{"name": "send", "qualified_name": "ns::ModemManager::send",
                          "signature": "int send(const uint8_t*,size_t)",
                          "is_virtual": false, "is_pure_virtual": false, "line": 150}, …],
             "field": [{"name": "_baudrate", …}, …],
             "constructor": [{"name": "ModemManager", …}],
             "enum": [{"name": "State", …}]
         },
         "member_count": 12}
```

fw-context orders the members by kind, then by name. This tool shows the
full API surface, without opening the header file. This tool also works
for C structs, but a struct will not have methods. This tool returns
`member_count: 0` for an index that predates this feature.

#### `get_template_instances`

Find concrete instantiations of a class or function template.

```
Input:  {"template_name": "std::vector", "project_root?": "/path/to/project", "limit?": 50}
Output: {"template_name": "std::vector", "template_usr": "c:@...",
         "instance_count": 3,
         "instances": [{"name": "vector", "qualified_name": "std::vector<int>",
                         "kind": "class", "file": "/path/src/main.cpp", "line": 42,
                         "signature": "class vector<int>", "is_definition": true}, …]}
```

Returns an empty `instances` list when the template is declared but
fw-context never finds an instantiation in the project code. Works for
both class templates (`CXCursor_CLASS_TEMPLATE`) and function templates
(`CXCursor_FUNCTION_TEMPLATE`). fw-context resolves the template by name,
exact or qualified, in the same way as `lookup_symbol`.

#### `get_method_overrides`

Show which virtual methods override which base-class methods, and which
derived-class methods override this one.

```
Input:  {"method_name": "UART_DRIVER::send", "project_root?": "/path/to/project"}
Output: {"method": "send", "qualified_name": "hal::UART_DRIVER::send",
         "kind": "method", "file": "/path/src/UART_DRIVER.cpp", "line": 88,
         "is_virtual": true, "is_pure_virtual": false,
         "overrides": [{"name": "send", "qualified_name": "SerialBase::send",
                         "usr": "c:@...", "file": "/path/src/SerialBase.h"}],
         "overridden_by": []}
```

Uses the `overrides` table, which fw-context builds during indexing. A
parameter-type comparison filters out accidental name collisions, such as
overloads that are not overrides. For a non-virtual method, or a method
whose class has no ancestors, both `overrides` and `overridden_by` are
empty lists.

### Conditional compilation

#### `explain_condition`

Explain which conditional branches decide one source line.

```
Input:  {"file_path": "src/main.c", "line": 88, "project_root?": "/path/to/project"}
Output: {"regions": [{"line": 84, "condition": "#if BOARD_V2", "taken": 1}],
         "macro_inputs": [{"name": "BOARD_V2", "def_source": "command_line"}]}
```

Returns the chain of enclosing `#if`/`#ifdef` groups for the line, with the
evaluated branch state of the active build (`taken`: 1 taken, 0 false, -1
not evaluated) and the macros the evaluated conditions depend on, each with
where its definition comes from (`file`, `command_line`, or `builtin`). Use
it to answer "why did this branch compile?" and "which -D flag flips it?".

#### `list_conditional_regions`

List the conditional regions of the index with their evaluated branches.

```
Input:  {"file_path?": "src/main.c", "limit?": 200, "project_root?": "/path/to/project"}
Output: [{"file": "src/main.c", "start_line": 84, "end_line": 91,
          "condition": "#if BOARD_V2", "taken": 1}, …]
```

Each region is one `#if`/`#ifdef`/`#ifndef` group: its file, root and
`#endif` lines, condition text, and the evaluated branch states of the
active build context. Omit `file_path` to see every conditional the build
activates at a glance.

### Variant analysis

#### `compare_variants`

Structurally compare two indexed builds (variant, image) for a symbol, a
file, or build-level facts.

```
Input:  {"a": "A", "b": "B", "symbol_or_file?": "transport_send",
         "image?": "img2", "project_root?": "/path/to/project"}
Output: {"kind": "symbol", "target": "transport_send", "method": "text",
         "a": {"variant": "A", "image": "", "config_hash": "…"},
         "b": {"variant": "B", "image": "", "config_hash": "…"},
         "dimensions": {"existence": {"equal": true, "a": …, "b": …},
                        "signature": {"equal": true, "a": …, "b": …},
                        "body_fingerprint": {"equal": false, "a": …, "b": …,
                                             "method": "fingerprint", "note": "…"},
                        "edges": {"equal": false, "a": […], "b": […],
                                  "only_in_a": […], "only_in_b": […]},
                        "conditions": {"equal": false, "differing": […],
                                       "only_in_a": […], "only_in_b": […]},
                        "defines": {"equal": false, "only_in_a": {},
                                    "only_in_b": {"IMPL_B": "1"}}}}
```

Dimensions: `existence`, `signature`, `body_fingerprint` (fingerprint-based —
a SHA-256 over the normalized signature, the stored ifdef-filtered body
tokens and the sorted confirmed outgoing call edges, the same fingerprint
`group_equivalent_variants` uses), outgoing `edges` (targets, kinds and
call-site `certainty`), `conditions` (condition text + evaluated branch
states per file) and `defines` (the `-D` flags every unit of each build
shares, per `get_active_build.defines` semantics). Every fact carries the
provenance of its build: `variant`, `image`, `config_hash` — the build IS
its config_hash; the index has no separate build id.

All dimensions are compared textually/structurally over stored index
records — the result is labeled `method: "text"`; only `body_fingerprint`
is fingerprint-based (`method: "fingerprint"`). No compiler or semantic
analysis runs here: builds that compile different branches of one `#ifdef`
store different bodies and fingerprint DIFFERENTLY even when the source
lines are identical. `symbol_or_file` resolves as a symbol name first,
then as an indexed file path (file mode: the file's conditional regions
plus a per-symbol rollup). Omit it for a build-level comparison (defines,
symbol/file/conditional-region counts). Errors name the problem: an
unindexed variant, a multi-image variant without `image`, or a target
matching nothing.

#### `group_equivalent_variants`

Group the project's builds by the observed behavior of one symbol.

```
Input:  {"symbol": "transport_send", "image?": "img2",
         "project_root?": "/path/to/project"}
Output: {"symbol": "transport_send", "build_count": 3,
         "groups": [{"fingerprint": "…", "variants": ["A", "C"],
                     "config_hashes": ["…", "…"],
                     "builds": [{"variant": "A", "image": "", "config_hash": "…"}, …]},
                    {"fingerprint": "…", "variants": ["B"], …}],
         "unobserved": []}
```

Builds whose stored facts for the symbol agree land in the same group —
the fingerprint hashes the normalized signature, the stored ifdef-filtered
body tokens and the sorted confirmed outgoing call edges of one
(config_hash, usr) observation. It is an equality key only (never a
certainty claim). Groups are ordered by descending size, then smallest
config_hash; members within a group by config_hash. Every member build
carries its `variant`/`image`/`config_hash` provenance. Builds with no
observation of the symbol are listed in `unobserved`, so "equivalent"
stays distinguishable from "not compiled here".

### Index maintenance

#### `get_environment_status`

Return the complete project environment status in one call. This tool
aggregates the dependency audit, the detected build system, the
compilation database, the index state, and the LLM backend.

```
Input:  {"project_root?": "/path/to/project", "fast?": true}
Output: {"project_id": "c3d4…", "project_root": "/path/to/project",
         "build_system": "zephyr", "schema_version": 84935291, "current_schema": 84935291,
         "vendor_paths": [], "project_paths": [],
         "status": "ready", "reindex_needed": false, "reindex_reasons": [], "stale": false,
         "index_message": "Index is fully up to date (2 builds).",
         "builds": [
           {"variant": "nrf52840-dev", "image": "app", "board": "nrf52840dk/nrf52840",
            "symbol_count": 12430, "file_count": 1502, "indexed_at": "2026-06-05 09:35:18",
            "status": "ready", "reindex_state": null, "reindex_needed": false, "stale": false,
            "modified_files_count": 0, "header_affected_tus": 0, "reasons": []},
           {"variant": "nrf52840-dev", "image": "mcuboot", "board": "nrf52840dk/nrf52840",
            "symbol_count": 3120, "file_count": 480, "indexed_at": "2026-06-05 09:31:02",
            "status": "ready", "reindex_state": null, "reindex_needed": false, "stale": false,
            "modified_files_count": 0, "header_affected_tus": 0, "reasons": []}],
         "bg_reindex_running": false, "vec_available": true}
```

Read `status`, `fix_cmd`, and `instructions` per issue. A `status` of
`"skipped"` means a prerequisite check is missing. This tool is the MCP
wrapper for `fw-context doctor` without `--fix`; it repairs nothing. For a
single-call overview of the whole environment, use
`get_environment_status`.

#### `get_active_build`

Check index health — call at session start.

```
Input:  {"project_root?": "/path/to/project"}
Output: {"config_hash": "a1b2…", "project_id": "c3d4…", "project_root": "/path/to/project",
         "build_system": "zephyr", "compile_commands": "/path/build/compile_commands.json",
         "indexed_at": "2026-06-05 09:35:18", "symbol_count": 12430, "file_count": 1502,
         "reference_count": 8900, "modified_files_count": 3,
         "header_affected_tus": 0, "manifest_verification": "full",
         "schema_version": 84935291, "current_schema": 84935291,
         "analysis": {"model": "qwen2.5-coder:14b", "analyze_vendor": false,
                      "project": {"analyzed": 8450, "skipped": 6, "total": 8570},
                      "vendor": {"analyzed": 0, "skipped": 0, "total": 42330},
                      "complete": false},
         "vendor_paths": [], "project_paths": [],
         "bg_reindex_running": false, "reindex_progress": null,
         "status": "ready", "reindex_needed": false, "reindex_reasons": [],
         "index_message": "Index is fully up to date (12430 symbols) | LLM analysis: project 8450/8570 (vendor skipped — analyze_vendor=false)"}
```

**Read-only.** This tool has no side effects, and does not spawn background
tasks. The server startup thread and the file watcher manage the
background reindex. `bg_reindex_running` reports whether an index run is
active, or the watcher has work that it did not complete. Only while a
run is active, `reindex_progress` holds the newest
progress or error line that fw-context wrote for that run. fw-context
skips the output of the build tools in the same log.

**`vec_available`** is `true` when the `sqlite-vec` extension loads into
SQLite in this server process. When it is `false`, `vec_error` (a string)
holds the reason. `vec_error` is not there when the extension loads.

**`builds`** lists every build of the index, one entry for each
`(variant, image)` pair: `{variant, image, board, symbol_count,
file_count, indexed_at, status, reindex_state, reindex_needed, stale,
modified_files_count, header_affected_tus, reasons}`. A build whose
manifest is not `"full"` also holds `manifest_verification`. The list shows
what fw-context has indexed, not what the config declares. On a project
of more than one build, fw-context selects no build: each query tool must
get the arguments of one build, as the error lists them (`variant`,
`image`, or the two). Select the build from the task, and ask the user
when the task does not identify one.

**`reindex_state`** of a build has the values of `index_state` (see
[The state of the index](#the-state-of-the-index)), and `null` when the
index of the build is current. Read it for the build that the task needs:
one build can be current while an index run updates another. The state of
a build counts each reason of the build, the modified files, the stale
headers, and an index without full header tracking.

**`declared_not_indexed`** is present when `[[build.variants]]` declares
a variant that the index does not hold. Each entry is `{variant,
command}`, and `command` is `fw-context index --build --variant <name>`.
No query reaches such a variant until you run the command.

**One build or several builds.** A project of one build also gets the
fields of that build at the top level: `status`, `reindex_state`,
`reindex_needed`, `reindex_reasons`, `stale`, `indexed_at`,
`first_indexed_at`, `symbol_count`, `file_count`, `reference_count`,
`modified_files_count`, `header_affected_tus`, `manifest_verification`,
`analysis`, and `description`. A project of several builds gets
`status`, `reindex_needed`, `reindex_reasons`, and `stale` of the project
at the top level, and no `reindex_state`. It gets no sums, because a file
that two images compile is in the index of each. The counts of each build
are in `builds`. `reference_count`, `analysis`, `description`, and
`first_indexed_at` of one build are in `get_build_info`.

**`status` field (use for decision-making):**

| Status | Meaning | What to do |
|--------|---------|------------|
| ``"ready"`` | Fully up to date, no issues | Continue normally |
| ``"reindexing"`` | Background reindex in progress or scheduled, or the next index run builds a new source file without a command | Queries work — read `reindex_state` of the build before you continue |
| ``"reindex_needed"`` | A structural cause — see `reindex_reasons` below | Queries still work, but read `reindex_reasons` for the command |
| ``"no_index"`` | Initialized, but `index.db` does not exist | Run ``fw-context index`` |
| ``"not_initialized"`` | ``fw-context init`` was not run | Run ``fw-context init`` |
| ``"config_error"`` | A config file cannot be read, is not UTF-8 or not valid TOML, a section is not a table, a key that selects the data or where data goes has a value of the wrong type, or a URL is not valid. `config_file` names it | Tell the operator the file and the problem. Do not run ``fw-context init`` or ``fw-context index``: they cannot repair the file. When the message names ``fw-context doctor --fix --only config``, that command removes each value that cannot be used. Run it, or correct the file, only with the approval of the operator |

A project of several builds gets the worst status of its builds:
`"reindex_needed"`, then `"reindexing"`, then `"ready"`.

A failure sets no `status`. On a database that holds no build config,
the result is a dict that holds only `error`. Read that key first. A
database that fw-context cannot read (damage, no access) makes the call
fail with the error of SQLite. The message of the check `db-integrity`
of [`check_dependencies`](#check_dependencies) tells if it is damaged.

`"reindex_needed"` outranks `"reindexing"`: the answers of this moment
come from the last finished run. When the `reindex_state` of a build is
`"reindexing"` or `"pending"`, `index_message` starts with the advice to
wait for the index run that is running or scheduled, instead of naming a
command.

**`reindex_reasons`** names each cause of `"reindex_needed"`:

- The last index run failed. The reason is the text that this run wrote,
  and it names the command that repairs the cause.
- The build is not there, for example after you removed the build
  output. The reason names the command: `fw-context index --build`, or,
  when fw-context cannot run the build of the project, a build outside of
  fw-context and then `fw-context index`.
- Schema mismatch (`schema_mismatch: <old> < <new>`). The reason names
  the command.
- An index that an older version wrote, and that lacks the columns of this
  version (`schema_mismatch: an older version wrote this index; it lacks
  the columns …`). No tool reads a build of such an index. The status
  tools give this reason with the action: wait for an index run that is
  in progress or scheduled, else run `fw-context index`. When the last
  run failed before it could add the columns, the text of that failure
  comes first, with its repair, and `reindex_state` is `"failed"`.
  `list_projects` gives the fact as the `error` of that database.
  `reindex_file` gives the same text as its `error`. Each query gives the
  fact as its `error`, and names `get_project_status` for the action.
  When the daemon starts, it starts the index run that adds the columns.
- Older row format (`row_format_mismatch: <old> != <new> — <effect>`).
  The stored text has an older meaning. For example, an index written
  before `fw-context-rows/1` keeps every inactive `#if` branch, thus
  answers can hold dead code. The reason names the command.
- compile_commands.json changed after the last index run. Or it does not
  exist, and the check of the build above does not cover it, for example
  the file of a build variant. The reason names the command.
- A source file on disk is missing from compile_commands.json, and
  fw-context will not build it by itself. The reason says why, and it
  names the command.
- The tree has another checkout than the index (`checkout changed:
  indexed at '<old>', now at '<new>'`). A checkout is a branch, or a
  detached HEAD at a commit. The reason names a detached HEAD as
  `commit <sha>`, also when a tag names that commit.

A missing source file, a missing compile_commands.json and a changed
checkout need a build, because only a build regenerates
compile_commands.json. The reason names the build:

- `fw-context index --build` for a build that fw-context can run.
- For a database that `[index] compile_commands` names: make that file
  again, then run `fw-context index`. fw-context does not build it.
- For a build that fw-context cannot run (a build in an IDE, or a build
  system that fw-context does not know): build the project outside of
  fw-context, then run `fw-context index`.

A new source file that fw-context builds in the background needs only
the next index run. A schema mismatch, an older row format, and a
changed compile_commands.json need `fw-context index`. Read the reason
text: it names the command. The `index_state` of a query names the same
action (`autobuild.build_advice`).

When an index run is in progress or scheduled, some reasons tell you to
wait for that run: a missing build, a missing source file that
fw-context does not build by itself, a changed or missing
compile_commands.json, a schema mismatch, an older row format, and a
changed checkout. This is also true for a database that
`[index] compile_commands` names: you can make the file again during the
run, and run `fw-context index` after it. The command of
such a reason is often the command that runs. For example, a run with
`--build` removes the database of the build before the build writes it
again. The reason still names the command, for the case that the reason
stays after the run.

On a project of several builds, a reason that every build gives comes one
time, without a label, for example an old schema or a failed build. Each
other reason starts with the label of its build:

- `build <variant>/<image>: <reason>` for a build with the two names.
- `build <image>: <reason>` for a build without a variant name.
- `build <variant>: <reason>` for a build without an image name.
- `build (no name): <reason>` for a build without the two names.

The `reasons` of each entry in `builds` hold the reasons of that build.
fw-context reads the marker of the last index run and finds the new
source files one time for the project. For the two, it uses the build
that `fw-context index` checks. fw-context reads HEAD of the tree one
time, and compares it with the HEAD that each build recorded:

- fw-context compares the commit of a detached HEAD, not a tag. Two
  tags of one commit are one checkout. A branch and a detached HEAD at
  the commit of that branch are two checkouts.
- A commit on a branch does not change the checkout.
- `git commit`, also with `--amend`, on a detached HEAD does not change
  the checkout when the reflog of HEAD shows only such commits after the
  recorded HEAD. Each other move of a detached HEAD changes the
  checkout, for example `git cherry-pick`, `git merge` or `git revert`.
- During a rebase of a branch, the checkout is the branch that the
  rebase updates. A rebase of a detached HEAD changes the checkout.
- A run that does not build keeps the recorded HEAD of a build that
  fw-context makes. Thus the reason stays until a run builds again.
- An index of an older version recorded no HEAD. Then fw-context
  compares the branch in the description of the build, and does not
  compare a detached HEAD.

One reason does not set `"reindex_needed"`: a new source file that is
missing from compile_commands.json, when fw-context will build it by
itself. That reason is in `reindex_reasons` while `reindex_needed` is
`false` and `status` is `"reindexing"`.

**`client_restart_required`.** This server process does not run the code
that it must run. Two causes set the field:

- The index holds a NEWER row format than this server process reads.
- The operator installed a different fw-context after the LLM client
  started this server process. This cause needs no index, thus it shows
  also with `status` `"not_initialized"`, `"no_index"` and
  `"config_error"`.

The result then holds `client_restart_required: true` and
`client_restart_reason`, which names each cause that applies, and
`index_message` opens with it. `status` does not change, and every query
keeps working. Do NOT reindex: the indexer writes the same new format
again. Tell the user to restart the LLM client (Claude Code, opencode, or
the client in use). The MCP server is a child process of that client.

**`update_notice`.** PyPI has a newer fw-context than the installed one.
The field is not a status. Tell the user one time, and do not upgrade
fw-context yourself: the user knows the tool that installed it. The
server asks PyPI in a background thread at server start. After a request
that succeeded, it asks again only after 24 hours. A request that failed
writes nothing, thus the next server start tries again. The tool reads
only the stored answer, thus it never waits for the network. An editable install gets no `update_notice`. To stop the
check, see [`[updates]`](configuration.md#updates--release-check).

**`config_warnings`.** A list, one line for each value of a config file
that fw-context did not use: `<file>: <problem>`. The value of a lower
file, or the default of the key, applies, and every tool keeps working.
The field is not a status. Tell the user each line. With the approval of
the user, `fw-context doctor --fix --only config` removes the values. See
[A config file that fw-context cannot use](configuration.md#a-config-file-that-fw-context-cannot-use).

`modified_files_count` counts the files whose content no longer matches
the index. Both modes count it. `fast` controls only the header check:
`fast=true` (the default) reuses the cached manifest hashes, and
`fast=false` recomputes them, which is much slower.
`header_affected_tus` reports how many source files have stale header
dependencies. A file that `compile_commands.json` lists more than once
counts once. This count is non-zero when headers changed since the
last index with `manifest_verification: "full"` or `"partial"`.

`manifest_verification` is `"full"` when `manifest.json` is available. In
that case, fw-context tracks header staleness with SHA-256 hashes that
fw-context collects during indexing. The two other values:

- `"partial"`: the last index run wrote `manifest.json`, but its
  embeddings are not complete: the embedding model was not reachable (for
  example Ollama does not run), or batches of the model failed. The
  symbols, the references and the header tracking are complete, thus the
  build is current, and no query answer carries a state for it. Only
  `semantic_search` and `smart_search` can miss symbols. The `_warning`
  of the status tools asks you to tell the user, and names the repair:
  `fw-context doctor --fix`, which does a check of each dependency and
  installs a missing embedding model, and then `fw-context index`.
  `fw-context doctor --fix` also installs the other missing dependencies,
  for example the chat model. `fw-context index` does not install the
  model, because `[llm] auto_pull` is `false` by default. `fw-context index` prints the cause in its log. A failed full-text index is not
  `"partial"`: it stops the run, and the marker of the failed run names
  the error.
- `"none"`: no manifest exists.

For `"none"`, the build is not current: fw-context cannot find a changed
header in the index, and the result holds a `_warning`:

- While an index run is in progress or scheduled, the `_warning` tells
  you to wait for it.
- When no index run is in progress or scheduled, and `reindex_needed`
  is `false`, it tells you to ask the user to run `fw-context index`.
  That run writes the index again with full header tracking.
- When `reindex_needed` is `true`, the `_warning` tells you to give the
  user the command that `reindex_reasons` names. Each reason names its
  command, and some need more than a plain run, for example a build or a
  repair. The run of that command also writes full header tracking.

`analysis` splits LLM-analysis coverage by project vs vendor symbols so
the reader can tell intentionally-skipped vendor symbols apart from
project symbols that still need analysis:

- `project.analyzed` / `project.total` — project definition symbols.
- `vendor.analyzed` / `vendor.total` — vendor/SDK definition symbols.
- `project.skipped` / `vendor.skipped` — symbols that the pipeline tried
  and cannot analyze. The body is larger than the model context, or the
  model gave an unparseable answer. fw-context stores a `skip:` sentinel
  for these symbols and does not try them again.
- `analyze_vendor` — whether vendor analysis was enabled at index time.
- `complete` — `true` when the pipeline has no more work: every project
  symbol is analyzed or skipped (and, when `analyze_vendor` is true, every
  vendor symbol too).

`index_message` also gives the analysis coverage, split into project and
vendor symbols. An unanalyzed symbol is not a reindex reason. When the
config asks for the analysis (`[llm] enabled` and `analyze_symbols`) and
symbols wait for it, `index_message` says how many, and names
`fw-context index`: each index run analyzes the symbols that wait, and
an analyzed symbol does not go to the model again. Run it only with the
approval of the user, because the LLM can need hours. The analysis needs
a running LLM: when the number does not go down after the run, call
`check_ollama`. During an index run, or when another action of the
message needs an index run (a reindex reason, a degraded index), the
message says that the run does the analysis. When the config turns the
analysis off, or a newer row format needs a restart of the client, the
message gives no advice. The vendor symbols count when `[llm]
analyze_vendor` of the config is true, as for the run.

A project of several builds gives no `analysis` field and no coverage in
`index_message`. When symbols wait for the analysis, its `index_message`
names each build with its number, for example `Symbols wait for the LLM
analysis in 2 of 7 builds (board-a/app: 6, board-a/mcuboot: 2)`, and
the same action. The message gives no sum, because a file that two
builds compile is in the index of each build.

`index_message` also names the definitions that have no embedding, when
the config asks for embeddings (`[llm] enabled` and `[index]
index_embeddings`) and an embedding step wrote to the build before. The
search by meaning (`semantic_search`, `smart_search`) does not find these
definitions. A re-parse with `reindex_file(with_analysis=False)`
leaves them, and the stamp of the last index run does not show it. The
action is `fw-context index`, and the run in progress, or the run of a
reason, does the embeddings too. A project of several builds names each
build with its number, as for the analysis. A build with the stamp
`partial` gets no such text: its `_warning` names the repair.

When `analyze_vendor` is `false`, vendor symbols are skipped by design —
a large unanalyzed vendor count is normal, not a defect.

`indexed_at` and `first_indexed_at` are UTC, in `"YYYY-MM-DD HH:MM:SS"`
format. File modification times are local time. Do not compare the two
directly. In UTC+2, a file that fw-context indexed correctly looks 2 hours
newer than `indexed_at`. To find modified files, use `modified_files_count`
with `fast=false`.

For a project that is not initialized, the result holds only `status`,
`project_root`, and `index_message`. When no index exists, the result
also holds `project_id`. For `"config_error"`, it also holds
`config_file`. The two version fields (`client_restart_*` for an
upgrade, and `update_notice`) can come with each result.
[`config_warnings`](#get_project_status) can come with each result that
has a status other than `"config_error"`.

- `multi` — `true` for a multi-variant project
- `variants` — a list of `{name, description, board}`
- `images` — a list of `{name, description, dir, type, board?}`
- `variant_images` — a map of variant name to its image names
- `active_variant` — the `[build] default_variant` value
- `active_image` — the `[build] default_image` value

#### `list_variants`

```
Input:  {"project_root?": "/path/to/project", "variant?": "nrf52840-dev", "image?": "app", "fast?": true}
Output: {"variant": "nrf52840-dev", "image": "app", "board": "nrf52840dk/nrf52840",
         "config_hash": "a1b2…", "project_id": "c3d4…", "project_root": "/path/to/project",
         "build_system": "zephyr", "compile_commands": "/path/.fw-context/build/nrf52840-dev/out/app/compile_commands.json",
         "indexed_at": "2026-06-05 09:35:18", "first_indexed_at": "2026-06-01 11:02:44",
         "symbol_count": 12430, "file_count": 1502,
         "reference_count": 8900, "modified_files_count": 0,
         "header_affected_tus": 0, "manifest_verification": "full",
         "analysis": {"model": "qwen2.5-coder:14b", "analyze_vendor": false,
                      "project": {"analyzed": 8450, "skipped": 6, "total": 8570},
                      "vendor": {"analyzed": 0, "skipped": 0, "total": 42330},
                      "complete": false},
         "description": "branch: main",
         "vendor_paths": [], "project_paths": [], "effective_vendor_patterns": ["zephyr/%", …],
         "status": "ready", "reindex_state": null, "reindex_needed": false, "reindex_reasons": [], "stale": false,
         "index_message": "Index is fully up to date (12430 symbols) | LLM analysis: project 8450/8570 (vendor skipped — analyze_vendor=false)",
         "entry_point": "__start",
         "memory": [{"name": "FLASH", "attributes": "rx", "origin": "0x0", "length": "0x100000",
                     "origin_value": 0, "length_value": 1048576,
                     "file_path": "build/zephyr/linker.cmd", "line": 12}, …],
         "defines": {"__ZEPHYR__": "1", …}, "defines_varying": 4,
         "bg_reindex_running": false, "vec_available": true}
```

**Read-only, with the same two exceptions as `get_project_status`.** When
the tool finds a build there again after the last index run said that it
is not there, it removes that marker. It also removes a `reindex.pid` or a
`reindex.pending` of a process that no longer runs. The tool takes the selector of the query
tools. On a project of more than one build, give the arguments of one
build, as the error lists them: `variant`, `image`, or the two.
`get_project_status` lists the names of each build in `builds`. Without
them, the result is an `error` that lists the builds (see
[A project of several builds](#mcp-tools)). A project of one build needs
no selector.

The health fields (`status`, `reindex_state`, `reindex_needed`, `reindex_reasons`,
`index_message`, `modified_files_count`, and the others) have the meaning
that [`get_project_status`](#get_project_status) gives them, for this
build only. When the index of this build holds a NEWER row format than
this server process reads, the result also holds
`client_restart_required` and `client_restart_reason`, as in
`get_project_status`. `update_notice`, and a restart after an upgrade of
fw-context, are only in `get_project_status`. A refusal of the selector, or an
index with no build config, gives a dict that holds only `error`. A
database that fw-context cannot read makes the call fail, as in
`get_project_status`. A project that is not initialized, that has no
index, or whose config file cannot be used gets the answer of
`get_project_status` without the version fields: only `status`,
`project_root`, `index_message`, and, when no index exists,
`project_id`, or for `"config_error"`, `config_file`. `config_warnings`
comes as in `get_project_status`.

`config_hash` identifies the build in the index. `compile_commands` is
the `compile_commands.json` that the index run read for this build.

`effective_vendor_patterns` holds the SQL LIKE patterns that this build
really used to mark vendor code. `vendor_paths` and `project_paths` hold
what the config asks for. The list is empty when the manifest cannot be
read.

**Build facts.** `entry_point` is the `ENTRY()` of the linker script of
the build. `memory` lists the `MEMORY` regions of that script:
`{name, attributes, origin, length, origin_value, length_value,
file_path, line}`. `origin` and `length` are the expression that the
script writes. `origin_value` and `length_value` are numbers, or `null`
for an expression that names a symbol. A name that the link command
defines with `--defsym` counts as a number. The end of a region is
`origin_value + length_value`. An empty value means "not recorded", never
"no memory". A build system such as Keil or IAR records no linker script.
A PlatformIO project records its link only in `fw-context index --build`,
thus an empty value there means that no build recorded the link yet.
`defines` holds the `-D` flags that every translation unit of the build
carries with one value. `defines_varying` counts the names that are left
out because they are not on every unit or have different values. Thus a
name that is not in `defines` is not defined, or is not defined in every
unit. To get the memory map of each build, call `get_build_info` one time
for each build.

#### `check_dependencies`

Run the dependency audit of the installation. Read-only, and it creates
no file. Returns one entry for each check.

```
Input:  {"project_root?": "/path/to/project"}
Output: {"multi": true, "builds": [
           {"variant": "nrf52840-dev", "image": "app", "board": "nrf52840dk/nrf52840",
            "config_hash": "a1b2…", "symbol_count": 12430, "file_count": 1502,
            "manifest_verification": "full"},
           {"variant": "nrf52840-dev", "image": "mcuboot", "board": "nrf52840dk/nrf52840",
            "config_hash": "c3d4…", "symbol_count": 3120, "file_count": 480,
            "manifest_verification": "full"}]}
```

Call it when `get_project_status` or another tool reports a problem of
the installation, or before the first `fw-context init`. It is not a call
for each session, because it runs every check of the installation.

Each entry holds `name`, `status`, `critical`, and `message`. An entry
that has a repair also holds `action`: a `message` for a human, and the
shell `command` that repairs it. Give both to the user without
interpretation. A `fw-context doctor` command holds `--project <root>`:
the shell of the user can be in a different directory. A `status` of `"skipped"` means that a prerequisite is
missing. For example, the audit skips `libclang-so` when
`libclang-python` is absent. A skipped check is not a failure.

In a project that is not initialized, only the checks that need no
project config run: the Python packages and libclang. The Ollama, model,
database, and build checks need the config. This tool is the MCP wrapper
for `fw-context doctor` without `--fix`, and it repairs nothing.

#### `reindex_file`

Re-index a single file after editing.

```
Input:  {"file_path": "/abs/path/to/src/main.c", "project_root?": "/path/to/project",
         "variant?": "a", "image?": "app", "with_analysis?": true}
Output (no selector, the builds a, b and c, and c does not compile the file):
        {"file": "/abs/path/to/src/main.c", "translation_units": 2,
         "symbols_updated": 56, "elapsed_s": 5.1,
         "builds": [{"variant": "a", "image": "", "translation_units": 1,
                     "symbols_updated": 28, "elapsed_s": 2.5},
                    {"variant": "b", "image": "", "translation_units": 1,
                     "symbols_updated": 28, "elapsed_s": 2.4}],
         "builds_without_file": ["c"]}
```

Without `variant` and `image`, the tool re-parses the file in each build
of the project that compiles it. An edit changes the file on disk for each
build of the tree, thus each build must read it again. `builds` holds the
result of each build that compiles the file, and `builds_without_file`
names the builds that do not. With the arguments of one build, as
`get_project_status` lists them, the tool re-parses only that build. A
selector that names no build gets the `error` of each query tool: the
list of the builds, or, for a variant that the config declares and the
index does not hold, the command that indexes it.

When one build compiles the file, the top level holds the full result of
that build, as before. When several builds do, the top level holds:

- The sums of the builds that did not fail, and `elapsed_s` of the whole
  call.
- `skipped_count`, the sum of the units that did not parse.
- `warning` only when each of those builds has it.
- `failed_builds`, the label of each build whose entry holds `error`.

The top level holds `error` only when no build compiles the file, or the
update failed in each build that it tried. In the second case it also
holds `failed_builds`, and, when each build gives the same `action`, that
`action`.

Optional keys of a result are present only when they apply. They are
absent otherwise, never `null`:

- `skipped_tus` and `skipped_count` — translation units that failed to
  parse.
- `warning` — a header went through one translation unit.
- `analysis_updated` and `embeddings_updated` — counts for the whole
  build, not only for this file.
- `analysis_warning`, `overrides_warning`, `pagerank_warning`,
  `embedding_warning` — a phase failed.

A file that is gone from disk but is still in the index has its records
removed in each build that holds it. The result is then `{"file": …,
"symbols_removed": 28, "action": "deleted", "builds": [{"variant": …,
"image": …, "symbols_removed": 28, "action": "deleted"}]}`. For a deleted
file, `builds_without_file` names the builds that do not hold it. On
failure, the dict holds `error`, and can also hold `skipped_tus` or
`action`.

When reference indexing is on (`[index] index_refs`), the tool rebuilds
the method override relationships, the PageRank scores and the hotspot
cache of each build that it re-parses. It does this with and without
`with_analysis`. When `with_analysis=True` (the default) and the LLM is on
(`[llm] enabled`), it also regenerates the LLM symbol analysis and the
embeddings of the symbols that the re-parse inserted, also the symbols of
the headers that the unit includes. The analysis also needs `[llm]
analyze_symbols`. The two need a model, thus this is slower. Set
`with_analysis=False` for a fast update. The symbols then wait for the
analysis and the embeddings, and `get_project_status` names them with
`fw-context index` as the action. The background reindex does not use
this tool: the daemon starts `fw-context index --background`.

The call runs in a thread of the server and sends a notice each 5 s. It
holds the lock of the query tools: another query waits 5 s, then gets
"server busy". After 300 s the call returns an `error`, and a part of the
work can continue or fail. Call `get_project_status` for the state of
each build, and repeat the call for a build that is not current. The
answer holds no `index_state`. This tool requires an existing index.

**Limitation:** A source file must appear in `compile_commands.json`.
compile_commands.json lists no headers, thus fw-context re-parses a header
through ONE translation unit that includes it, from the manifest: the unit
with the lowest path in sort order, in each build. The result then
holds a `warning` ("Header re-indexed via one TU…"). Other units can include the header
with other `#define` values and still hold stale symbols. Run a full
`fw-context index` for full accuracy. A file that no unit builds or
includes gives an `error`.

#### `reset_index`

Delete the index. Always run a dry run first, then pass `confirm: true`.

```
Input:  {"project_root?": "/path/to/project", "confirm": false}     → {"action": "dry_run", "symbol_count": 8586, …}
Input:  {"project_root?": "/path/to/project", "confirm": true}      → {"action": "deleted", "message": "…"}
```

The dry run gives the counts of the whole database, because the reset
deletes all of it. `build_count` counts the completed builds of the
project, as `get_project_status` lists them. `symbol_count` counts every
symbol row in the database: also the rows of a build that an index run
writes now, and the rows of an older build that an interrupted run left.
`indexed_at` is the time of the newest build. The three fields are
present only when fw-context can read a build config. A `warning` key
means that the database is corrupt: the integrity check or a read of the
database failed, thus fw-context counts nothing and the three fields are
absent. The reset can delete a corrupt database.

The delete removes the database with its `-wal`, `-shm`, and `-journal`
files. It also removes the automatic-build markers `build_problem.json` and
`excluded_sources.json` next to the database, because they describe the
index that is gone.

#### `list_projects`

List all indexed firmware projects.

```
Input:  {"project_root?": "/path/to/project"}
Output: [{"project_id": "a1b2…", "name": "my-zephyr-app", "root_path": "/path",
          "build_system": "zephyr", "symbol_count": 12430, "file_count": 1502,
          "indexed_at": "2026-06-05 09:35:18", "description": "branch: main",
          "first_indexed_at": "2026-06-01 11:02:44",
          "schema_version": 84935291, "current_schema": 84935291,
          "reindex_needed": false, "status": "ready", "db": "…",
          "variant_count": 2, "image_count": 5,
          "analysis": {"project": {"analyzed": 8450, "skipped": 6, "total": 8570},
                       "vendor": {"analyzed": 0, "skipped": 0, "total": 42330}}}, …]
```

`indexed_at` and `first_indexed_at` are UTC, in `"YYYY-MM-DD HH:MM:SS"`
format. `analysis` holds the `project` and `vendor` counts only, and is
`null` when the project has no indexed build. For the `model`,
`analyze_vendor`, and `complete` fields, call `get_active_build` for that
project.

When no project has an index, the result is one entry with an `info` key.
When fw-context cannot read a database, the result holds one entry with `db`
and `error` keys for that file.

#### `get_project_info`

Look up project metadata from the global project registry.

```
Input:  {"project_id": "a1b2c3d4…"}
Output: {"project_id": "a1b2c3d4…", "name": "my-zephyr-app",
         "project_type": "zephyr", "root_path": "/path/to/project",
         "created_at": "2026-06-01T12:00:00", "updated_at": "2026-06-05T09:35:18"}
```

Looks up the global project registry at `~/.fw-context/projects.db`. Use
this tool to identify a project from its UUID4: find out which build
system the project uses, the project's name, and when fw-context last
indexed the project. Returns an error when the registry does not have the
`project_id`.

#### `check_ollama`

Verify Ollama availability. Call before `smart_search`, `semantic_search`,
or when `explain_symbol` needs on-demand analysis (no pre-computed analysis
available).

```
Input:  {"project_root?": "/path/to/project"}
Output: {"status": "ok", "ollama_running": true, "ollama_enabled": true,
         "configured_model": "qwen2.5-coder:14b", "num_ctx": 16384,
         "installed_models": ["qwen2.5-coder:14b", "mxbai-embed-large:latest"], …}
```

Returns `status: "disabled"` when `[llm] enabled = false`. In that case,
this tool needs no Ollama.

`chat_api` says whether an external chat API replaces Ollama for chat,
and which model it names. When it is configured, `chat_api` also holds
`endpoint` and `format`, and a `compliance_warning` for a host that is not
local.

`check_ollama` looks for the chat model on the Ollama server of the chat
calls: the server of `ollama_url`, or the Ollama server that
`chat_api_base` names. When `chat_api_base` names an OpenAI-compatible
API, that API runs the chat model, and an Ollama server does not have to
hold it. When the server of `ollama_url` does not answer, `check_ollama`
does not look for the chat model; `fw-context doctor` does.

`status: "model_missing"` has a `message` that names the command for each
missing model. The command sets `OLLAMA_HOST` to the server of the check,
in the syntax of PowerShell on Windows and of a POSIX shell on other
systems. `status: "error"` also means that the Ollama server of
`chat_api_base` gives no model list.

`embedding_backend` is `"ollama"` for an Ollama embedding model, and
`"local"` for a sentence-transformers or `ft://` model. A local model needs
no Ollama: it counts as installed, and with chat on an external API the
status is `"ok"` even when Ollama does not run. `"embedding_unavailable"`
thus means an Ollama embedding model and an Ollama that fw-context cannot
use. `"not_configured"` and `"embedding_unavailable"` come when Ollama does
not run, and also when `ollama_url` names a server that is not Ollama, for
example a proxy that gives its own page or an HTTP error. `message` names
the cause and the repair.

Note: `explain_symbol`, with pre-computed analysis (the default), returns
instantly, and does not require Ollama at query time. `num_ctx` is 16384
by default, to allow full function bodies during analysis generation.

#### `configure_llm`

Write per-developer LLM settings to `.fw-context/local.toml` (gitignored).
This tool writes nothing else. After writing, it tests the configuration
with a simple API call.

```
Input:  {"chat_api_base?": "https://api.deepseek.com/v1", "chat_api_key?": "sk-…",
         "model?": "deepseek-chat", "auto_pull?": false, "stream?": false}
Output: {"status": "ok", "model": "deepseek-chat", "test_latency_s": 1.2, …}
```

Pass `chat_api_base` for a cloud or proxy API. When it is empty, this
tool uses the local Ollama instance. When `chat_api_base` points to an
external host, source code in chat prompts is sent to that host. Verify
this complies with your data security policy. Prefer the local Ollama
instance.

### MCP Resources

Resources are read-only, URI-addressable endpoints that return structured
content, in Markdown or JSON. Unlike tools, resources take no JSON-RPC
parameters, except `symbols/{name}`, which takes a name path segment.

#### `fw-context://stats`

Markdown summary of all indexed projects — symbol counts, file counts,
freshness, and index timestamps.

```
URI:    fw-context://stats
Output: # fw-context — 3 project(s)

        - **my-zephyr-app** (a1b2…) — 12430 symbols, 1502 files, indexed 2026-06-05T09:35:18, ✓ fresh
        - **my-pio-app** (c3d4…) — 8586 symbols, 952 files, indexed 2026-06-04T16:20:45, ⚠ stale
```

Aggregates data across all project databases under `~/.fw-context/index/`.
This resource marks a project that has errors with an **ERROR** marker.

#### `fw-context://projects`

Same data as [`list_projects`](#list_projects), serialized as indented JSON.

```
URI:    fw-context://projects
Output: [{"project_id": "a1b2…", "name": "my-zephyr-app", "symbol_count": 12430, …}, …]
```

#### `fw-context://symbols/{name}`

Definition source of a symbol rendered as a Markdown document. Uses the
same lookup as [`get_source`](#get_source).

```
URI:    fw-context://symbols/uart_init
Output: # uart_init

        - **qualified:** `drv::uart_init`
        - **kind:** function
        - **file:** `/path/src/uart.c:42`
        - **signature:** `void uart_init(int baudrate)`

        ```cpp
        void uart_init(int baudrate) {
          …
        }
        ```
```

When fw-context does not find the symbol, this resource returns a JSON
error object.

---

## How the search pipeline works

### `smart_search` — multi-phase natural-language search

```
Phase 0: Translate (LLM)
┌────────────────────────────────────────────────────┐
│ Non-English queries → English.                     │
│ "SPI komunikace s displayem" → "SPI communication  │
│ with display".  English queries pass through.      │
└────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 1: Rough search (FTS5)
┌────────────────────────────────────────────────────┐
│ Word-pair + single-word FTS5 queries.              │
│ "modem connect network" → samples from the index   │
│ (12–20 symbols with their naming conventions).     │
└────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 2a: LLM query generation
┌────────────────────────────────────────────────────┐
│ Ollama sees the query + sample symbols.            │
│ Learns naming style (snake_case vs camelCase).     │
│ UNDERSTANDING: <subsystem, intent>                 │
│ QUERIES: ["network_reg*", "modem_attach*", …]      │
└────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 2b: LLM refinement (feedback loop)
┌────────────────────────────────────────────────────┐
│ First-round queries executed.                      │
│ LLM sees top results → course-corrects if needed.  │
│ Better queries OR [] if already correct.            │
└────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 3: FTS5 search + merge
┌────────────────────────────────────────────────────┐
│ All generated queries run via OR.                  │
│ Deduplicated by (name, file_path).                 │
│ Scored: name match=3, qualified_name=2,            │
│ file_path=1, project-local=+1, kind=+0..2          │
└────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 4: Vector re-rank (sqlite-vec)
┌────────────────────────────────────────────────────┐
│ FTS5 candidates re-ranked by cosine distance.      │
│ Query embedded via mxbai-embed-large.              │
│ Hybrid: text recall + vector precision.            │
└────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 5: Deduplicate + format
┌────────────────────────────────────────────────────┐
│ Merge FTS5 + vector results.                       │
│ Prefer definitions.  Score-sort.  Limit to N.      │
│ Add metadata entries (_generated_queries, …).      │
└────────────────────────────────────────────────────┘
```

### Search quality scoring

| Match location | Points |
|---------------|--------|
| `name` / `name_tokens` (camelCase split) | 3 |
| `qualified_name` | 2 |
| `file_path` (module context) | 1 |
| Project-local code (`src/`, `lib/`, not OS framework) | +1 |
| Kind: function / method / class / struct / enum / typedef | +2 |
| Kind: enum constant / namespace | +1 |
| Kind: variable / field | 0 |

### Staleness recovery on query

The query tools check each file that their result names against the
index. The content decides: fw-context compares the hash of the file on
disk with `files.source_hash`. A `git checkout`, `pull`, or `stash pop`
that only changes the `mtime` thus gives no warning. Only for a row with
no stored hash does the `mtime` decide.

These tools do the check: `search_code`, `search_bodies`,
`search_content`, `lookup_symbol`, `find_variables`, `get_file_map`, the
call-graph tools, and the class-analysis tools. The result shape decides
where the warning goes:

- A list gets a leading `{"warning": …}` record.
- A dict gets `stale_warning` and `stale: true`.

An empty result names no file, thus it gets its own message. It says that
indexed files changed after the last index run, thus the empty result is
not proof that the code does not exist. When source files are not in
`compile_commands.json`, the message says so instead. It names no
command, because the command depends on the build system: `index_state`
and `get_project_status` name it.

`get_source` and `get_symbol_context` add `source_origin`: `"index"` or
`"disk"`. When the file changed and the symbol did not move, the body
comes from the disk, with every `#ifdef` branch. When an edit moved the
symbol, the tool gives the indexed body with `stale: true`, because the
stored line number no longer points at the symbol. `explain_symbol` and
`read_file` also set `stale` and `stale_warning` when the file changed.

The search tools start the watcher daemon in the background when they find
a changed file, or an empty result with a source file that
`compile_commands.json` does not cover, so no query has to wait for a
reindex. The file watcher
independently handles a recently edited file within 500 ms after the
save, so a stale result is rare in practice.

For manual recovery, use `reindex_file` or `fw-context index`.

### Index building (detailed)

```
1. Read compile_commands.json → extract translation units (file + compiler args)

2. For each translation unit (sequential, per-TU write lock):
   Parse with libclang using exact compiler flags (-I, -D, -std, --target)
   Traverse AST → extract all symbols (no source filtering: fw-context
   indexes everything from compile_commands.json and the includes)
   Category: function, method, class, enum, typedef, variable, field, and other kinds
   Compute is_project per-symbol (project code = 1, vendor/SDK = 0)
   Extract cross-references including vendor/SDK (on by default; skip with --no-refs)
   Extract macros via clang -dM -E (preprocessor dump; stored in macro_defs table)
   Release the write lock between translation units. A manual operation,
   such as `reindex_file`, can interleave through the pause marker
   mechanism, without blocking.

4. Write to SQLite (atomic per-TU transaction):
   Delete old symbols for this TU
   Insert new symbols + FTS5 triggers
   Insert references (on by default; skip with --no-refs)
   Insert macros with expanded values
   Generate + store vector embeddings (on by default; skip with --no-embeddings)

5. Write build metadata:
   compile_commands.json hash, file mtimes, symbol/file/ref/macro counts
```

### Vector search

When fw-context generates embeddings during indexing
(`fw-context index --embeddings`), fw-context stores the symbols in two tables:

| Table | Storage | Query method |
|-------|---------|-------------|
| `embeddings` | BLOB (4 bytes × dim floats — 4096 for mxbai 1024-dim, 16384 for qwen3 4096-dim) | Legacy brute-force (Python) |
| `vec_symbols` (vec0) | sqlite-vec virtual table | KNN via `MATCH` (C implementation) |

The `EmbeddingPhase` prefers `vec0` when available, in an index built
after fw-context added this feature. The `EmbeddingPhase` falls back to
BLOB brute-force search for an older index. The `EmbeddingPhase` also
operates as a **hybrid re-rank** when FTS5 results already exist. This
avoids duplicate searches and expensive merging.

---

## Auto-detection of build system

| Ecosystem | Detected by | compile_commands.json location |
|-----------|------------|-------------------------------|
| **Zephyr** | `west.yml` or `prj.conf` | `build/compile_commands.json` |
| **PlatformIO** | `platformio.ini` | Project root |
| **Mbed OS** | `mbed-os/` directory or `mbed_app.json` | Project root |
| **CMake** | `CMakeLists.txt` + `compile_commands.json` | `build/` |
| **Bare-metal** | Any build with `bear` | Project root |

---

## Search tips

- **Use short queries.** A query with 1 to 3 words works best. FTS5 is a
  token-based engine, so a longer phrase narrows the results too
  aggressively.
- **Omit underscores.** FTS5 treats `_` as a word separator. `modem_init` →
  `modem AND init`. Write `modem init` instead.
- **Trailing `*` for a prefix match.** `uart_*` finds `uart_init`,
  `uart_write`, `uart_read`, and other names with that prefix.
- **Quotes for exact phrases.** `"spi transfer"` matches that exact token sequence,
  not `transfer over spi`.
- **Kind filter for precision.** Narrow `search_code` to `kind=function`
  when you know that you are looking for a function. This filter
  eliminates variables, fields, and enums.
