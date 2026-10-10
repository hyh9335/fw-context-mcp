"""Index runner: parse compile_commands.json, extract symbols, store to SQLite.

WHY a separate runner: the indexing pipeline has four distinct phases
(preparation, parsing, post-processing, optional analysis) that are
orchestrated in sequence.  Keeping them in one module allows the
write-lock and heartbeat mechanisms to span the entire run.

Uses ``indexer/ops.py`` for the shared "parse TU → store symbols" loop so
that runner, reindex_file, and auto-reindex all use the same code path.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..config.settings import derive_project_id

# Re-exported so an existing `from .runner import EXIT_SUPERSEDED` keeps
# working.  The constants live in fw_context_mcp.exit_codes because
# mcp/daemon.py needs them and must not pull the indexer — see that module.
from ..exit_codes import (  # noqa: F401 — re-exported
    EXIT_ALREADY_RUNNING,
    EXIT_SUPERSEDED,
)
from ..mcp.shared.pid_file import PidFile
from ..utils import TU_EXTENSIONS
from ._embedding import (
    _build_embeddings,
    _chunk_body,
    _cleanup_orphaned_cc_artifacts,
    _fmt_dur,
    _truncate_body,
)
from ._llm_analysis import _fetch_callees, _fetch_referencers
from ._postprocess import (
    _build_hotspot_cache,
    _build_overrides,
    _build_pagerank,
    _run_postprocess,
    IndexIntegrityError,
)
from ._unit_processor import (
    _check_and_parse_unit,
    _handle_unchanged,
    _process_unit,
    _v2_shadow_flag_on,
    _v2_write_authority_on,
)
from .compile_commands import parse as parse_compile_commands
from .compile_commands import validate_include_files
from .db import (
    drop_fts_triggers,
    get_file_hashes_by_context,
    open_db,
    rebuild_fts,
    transaction,
    upsert_build_config,
    upsert_build_context,
    upsert_project,
    write_lock,
)

log = logging.getLogger(__name__)

__all__ = [
    "_build_embeddings",
    "_build_hotspot_cache",
    "_build_overrides",
    "_build_pagerank",
    "_chunk_body",
    "_cleanup_orphaned_cc_artifacts",
    "_fetch_callees",
    "_fetch_referencers",
    "_fmt_dur",
    "_run_postprocess",
    "_truncate_body",
    "run",
]


def _tu_rel(unit, project_root: Path) -> str:
    """The unit's path relative to the project root — the manifest key form."""
    try:
        return unit.file.resolve().relative_to(project_root).as_posix()
    except ValueError:
        return unit.file.resolve().as_posix()



# ═══════════════════════════════════════════════════════════════
# SECTION: Embedding building (→ llm_analysis.py)
# ═══════════════════════════════════════════════════════════════


class IndexSuperseded(Exception):
    """Another process took over the index, so this run gave up.

    Not a failure: nothing is wrong with the index, and nothing was written
    that has to be undone.  The run simply stopped because continuing would
    have applied decisions made from a snapshot the other process has since
    invalidated.

    Deliberately outside :data:`SAFE_EXCEPT` — a handler that swallowed this
    as "a step failed, carry on" would resume exactly the run this exists to
    stop.  Callers report it as superseded and retry the work from the start.
    """


def raise_if_superseded(db_dir: Path) -> None:
    """Raise :class:`IndexSuperseded` when another process owns the index.

    The MCP server writes ``<pid>`` to ``reindex.pause`` before a manual
    ``reindex_file`` or ``reset_index``.  An indexing run used to block here
    until the marker cleared and then carry on from the same translation unit
    — which is not safe.  Everything the loop decides with was captured
    before the pause: ``existing_files`` (the mtime and hash snapshot), the
    manifest lookup, the stale-header set, and the header ownership built up
    TU by TU.  After a manual operation those all describe an index that no
    longer exists.

    The damage is concrete.  ``reset_index`` empties the database; a resumed
    run then reads its pre-pause ``existing_files``, takes the mtime
    fast-path for translation units whose rows are gone, and finishes by
    stamping a manifest over a half-built index.  A ``reindex_file`` is
    subtler but the same shape: the manual parse re-assigned header
    ownership, and the resumed run's ``replace_file_data`` deletes rows it
    just wrote.

    So the run gives up instead.  The caller reports it as superseded rather
    than as a failure, and the work is retried from the start — with a fresh
    snapshot — once the marker clears.

    A marker holding our own PID is ignored: a foreground index writes it to
    stop the background reindex, not to stop itself.  A marker whose process
    is gone is ignored too — nothing owns the index any more.
    """
    pause_file = db_dir / "reindex.pause"
    if not PidFile.is_active(pause_file):
        return
    requester_pid = PidFile.read_pid(pause_file)
    if requester_pid is None or requester_pid == os.getpid():
        return
    raise IndexSuperseded(
        f"another process (pid {requester_pid}) took over the index; "
        f"abandoning this run so it can be redone from a fresh snapshot"
    )





def _tus_to_requeue(
    manifest: dict,
    stale_headers: set[str],
    project_root: Path,
) -> frozenset[str]:
    """Return the normalized TU paths that must skip the cheap staleness tiers.

    Two sources, and the union of them:

    - a TU that includes a header whose content changed, and
    - a TU whose entry carries ``needs_reparse``.

    The second source must be read even when *stale_headers* is empty.  That
    is the whole point of the mark: another unit's re-parse already wrote the
    current hash into the shared header table, so the difference the header
    pass looks for is gone.  Built inside an ``if stale_headers:`` the set
    stays empty and the mark reaches nothing.
    """
    from .manifest import tus_affected_by_headers
    from .ops import _normalize_file_path

    flagged = {
        e["file"] for e in manifest.get("entries", []) if e.get("needs_reparse")
    }
    from_headers = tus_affected_by_headers(manifest, stale_headers)
    affected = from_headers | flagged
    if not affected:
        return frozenset()

    log.info(
        "%d changed header(s) -> %d TU(s) queued for reparse, "
        "%d more queued by needs_reparse",
        len(stale_headers), len(from_headers), len(flagged - from_headers),
    )
    return frozenset(
        _normalize_file_path(str((project_root / tu_rel).resolve()), project_root)
        for tu_rel in affected
    )


def _store_linker_scripts(
    *,
    conn,
    config_hash: str,
    project_root: Path,
    db_dir: Path,
    compile_commands: Path,
    build_system: str | None,
    variant: str,
    units: list,
    vendor_patterns: list[str],
    project_patterns: list[str],
):
    """Read the linker scripts of this build and store what they define.

    Returns a ``linker_script.LinkerResult``, or None when the build names
    no script.

    **The position of this pass is between the C units and the assembly**,
    and both neighbours depend on it:

    * AFTER C, so ``store_scripts`` can see a definition the compiled code
      makes and leave that name alone.  A linker script and a C file can
      name the same symbol, and the compiled definition is the real one.
    * BEFORE assembly, so ``asm._declare_referenced_only`` finds the name
      already in the index and adds no ``kind="undefined"`` row for it.
      That is what closes slot 0 of a vector table: measured before this,
      the STM32 project held ``_estack`` and the Mbed project held ``__StackTop`` with that kind, one
      per project.

    *build_system* is the ``[build] system`` config key, which wins over
    marker detection for the same reason it does for the vendor patterns: a
    freestanding NCS application reads as a plain CMake project by its
    markers alone.

    A build that names no script gets a log line and nothing else.  Reading
    a script the build did not name would be a guess, and a wrong memory
    map is worse than none — see ``builders._linker``.
    """
    from .build import detect_build_system
    from .builders import linker_scripts, registry
    from .linker_script import store_scripts

    key = build_system or detect_build_system(project_root)
    builder_cls = registry.get(key) if key else None
    scripts = linker_scripts(
        builder_cls() if builder_cls else None,
        project_root,
        compile_commands=compile_commands,
        variant=variant,
        units=units,
    )
    if not scripts:
        log.info("linker script: none found for this build")
        return None

    with write_lock(db_dir, timeout=120.0):
        with transaction(conn, checkpoint=False):
            result = store_scripts(
                conn, config_hash, scripts, project_root,
                vendor_patterns, project_patterns,
            )
    log.info(
        "linker script: %d file(s) -> %d symbol(s), %d region(s), entry %s%s",
        result.files, result.symbols, result.regions,
        result.entry or "(none)",
        f", {result.skipped_defined} name(s) already defined"
        if result.skipped_defined else "",
    )
    # A refusal nobody can see looks exactly like a script that held nothing
    # worth indexing.
    summary = result.report.summary()
    if summary:
        log.info("linker script refusals: %s", summary)
    return result


def run(
    compile_commands: Path,
    db_path: Path,
    vendor_paths: list[str] | None = None,
    project_paths: list[str] | None = None,
    project_name: str | None = None,
    index_refs: bool = False,
    index_embeddings: bool = False,
    analyze_symbols: bool = False,
    analyze_overrides: bool = True,
    project_root: Path | None = None,
    project_id: str | None = None,
    llm_config=None,
    cache_server_config=None,
    force: bool = False,
    index_macros_expanded: bool = True,
    config_header: str = "",
    build_dir_patterns: list[str] | None = None,
    build_system: str | None = None,
    analyze_vendor: bool = False,
    purge_max_missing_percent: int = 20,
    variant: str = "",
    image: str = "",
    board: str = "",
    build_env: dict[str, str] | None = None,
    defer_fts: bool = False,
    defer_cleanup: bool = False,
) -> str:
    """Index a project: parse translation units, extract symbols, and store to SQLite.

    This is the main entry point for indexing a firmware project.  It reads
    ``compile_commands.json``, parses every translation unit with libclang,
    deduplicates symbols across files, and persists the result to the SQLite
    database at ``db_path``.  Optionally builds embeddings, LLM-based symbol
    analysis, file-level summaries, and the method override graph.

    All files from all included headers are indexed unconditionally.
    ``is_project`` is computed per-symbol from *vendor_paths* (auto-detected
    SDK dirs + user-configured) and *project_paths* (user-configured overrides).

    Args:
        compile_commands: Path to ``compile_commands.json`` (generated by
            ``bear``, ``compiledb``, or ``CMAKE_EXPORT_COMPILE_COMMANDS``).
        db_path: Path to the SQLite database file that will store the index.
        vendor_paths: Additional vendor/SDK directory patterns (additive to
            auto-detection).  Paths matching these get ``is_project=0``.
        project_paths: Manual project directory patterns that override
            auto-detection.  Paths matching these get ``is_project=1``.
            Use absolute paths for directories outside the project root.
        project_name: Human-readable name for the project (defaults to the
            directory name of ``project_root``).
        index_refs: When True, extract call-graph references (call, ref,
            member, and indirect edges) during AST traversal.
        index_embeddings: When True, generate vector embeddings for all
            definition symbols using Ollama after indexing.
        analyze_symbols: When True, generate structured LLM analysis
            (summary, inputs, outputs) for project symbols via Ollama.
        analyze_overrides: When True (default), build the method override
            graph by matching virtual methods across the inheritance chain.
            Runs entirely on the local index — no LLM needed.
        project_root: Root directory of the project.  Used to resolve relative
            paths and derive defaults.  Defaults to the parent of
            ``compile_commands``.
        build_system: The ``[build] system`` config key of this project.  It
            decides WHICH builder answers for the vendor patterns.  Pass it
            whenever the config has it: the config and the markers can
            disagree, and a freestanding NCS application reads as a CMake
            project by its markers alone.  With None the markers decide.
        project_id: Unique project identifier (auto-derived from
            ``project_root`` when not provided).
        llm_config: Configuration dataclass for Ollama connection (URL,
            model names, enabled flag).  Required when any ``index_*`` or
            ``analyze_*`` option is enabled.

    Returns:
        The ``config_hash`` string — a content-addressable fingerprint of the
        ``compile_commands.json`` used for staleness detection.
    """
    if project_root is None:
        project_root = compile_commands.parent.resolve()
    else:
        project_root = project_root.resolve()

    # Normalize patterns without % wildcard to match subdirectories.  The
    # vendor set waits for the translation units — see below.
    from .sdk_detect import _build_sdk_excludes, _normalize_patterns

    project_patterns_list = _normalize_patterns(list(project_paths)) if project_paths else []

    if project_id is None:
        project_id = derive_project_id(project_root)
    name = project_name or project_root.name
    # Collect git context for build description (branch + last tag).
    from .git_context import get_git_description

    git_description = get_git_description(project_root)
    # Clean up compile_commands.<hash>.json artifacts left by a previous
    # interrupted run before computing a new config_hash.
    _cleanup_orphaned_cc_artifacts(db_path, project_id)
    # Parse compile_commands.json to discover translation units.  Must
    # happen before config_hash computation so the manifest can be built
    # from the actual TU list.
    all_units = list(parse_compile_commands(compile_commands))

    # libclang reads a translation unit as C or C++.  Given an assembly
    # unit, it parses that unit as C and gets no cursor and one error
    # diagnostic per file — measured on a Zephyr startup.S: 0 cursors,
    # "error: expected identifier or '('".  So libclang never gets them.
    #
    # They are NOT dropped.  The assembly pass further down preprocesses
    # them, stores their symbols, and logs its own "assembly:" summary.
    # This line reports only the split, and it must say so: the wording
    # was "skipping %d of %d TUs", which reads as data loss to anyone who
    # watches the run — the units are indexed, by a different reader.
    #
    # The line stays, because no other line reports these units before the
    # assembly pass ends.  The new-file scan cannot report them either: a
    # suffix outside TU_EXTENSIONS is not even a candidate there, so the
    # units are invisible from that direction.  Five of the seven test
    # projects compile assembly.
    units = [u for u in all_units if u.file.suffix in TU_EXTENSIONS]
    asm_units = [u for u in all_units if u.file.suffix not in TU_EXTENSIONS]
    if asm_units:
        from collections import Counter
        by_ext = Counter(u.file.suffix for u in asm_units)
        log.info(
            "%d of %d TUs go to the assembly indexer "
            "(libclang cannot read them as C or C++): %s",
            len(asm_units), len(all_units),
            ", ".join(f"{n}x {ext or '(no suffix)'}" for ext, n in by_ext.most_common()),
        )
    log.info("TUs to index: %d", len(units))

    # ── The effective vendor set, computed HERE and not earlier ──
    # A builder may read the compiler flags: Zephyr takes ZEPHYR_BASE and
    # WEST_TOPDIR from -fmacro-prefix-map, which names the roots of the build
    # that is indexed rather than the shell that runs fw-context.  The units
    # carry those flags, so the set cannot be built before they are parsed.
    # Its first use is the header staleness pre-pass further down.
    vendor_patterns = list(
        _build_sdk_excludes(project_root, build_system, units=units)
    )
    if vendor_paths:
        vendor_patterns.extend(_normalize_patterns(vendor_paths))
    log.info(
        "vendor patterns for %s: %s",
        variant or project_root.name,
        ", ".join(vendor_patterns) or "none",
    )

    # Determine config_hash from manifest.json.  The manifest captures the
    # full structural build identity (files, directories, compiler flags) —
    # more comprehensive than hashing compile_commands.json alone.
    #
    # When a manifest exists, compute the expected structural hash from the
    # current units and compare.  If they match, reuse the stored hash so
    # _update_manifest_after_index() can do an incremental header update.
    # If they differ (compile_commands.json changed), rebuild the preliminary
    # manifest.  When no manifest exists yet (first index), build one.
    # Inject user-configured config header when the build system doesn't emit
    # -include flags (custom builds, legacy Makefiles, etc.).
    if config_header:
        ch = project_root / config_header
        if not ch.exists():
            raise RuntimeError(
                f"Configured config_header not found: {ch}\nCheck [index] config_header in .fw-context/config.toml"
            )
        ch_abs = ch.resolve()
        for unit in units:
            unit.clang_args.extend(("-include", str(ch_abs)))

    from .manifest import build_preliminary, build_scope, compute_structural_hash
    from .manifest import load as load_manifest

    scope = build_scope(variant, image, build_env)

    expected_hash = compute_structural_hash(
        compile_commands,
        project_root,
        units,
        build_dir_patterns,
        project_id=project_id,
        scope=scope,
    )
    manifest = load_manifest(db_path.parent, expected_hash)
    if manifest is not None:
        config_hash = manifest.get("config_hash", expected_hash)
    else:
        config_hash = build_preliminary(
            compile_commands,
            db_path.parent,
            project_root,
            units,
            build_dir_patterns,
            project_id=project_id,
            scope=scope,
        )
        # Reload manifest from disk — build_preliminary may have written a
        # preliminary (empty source_hash) manifest.  The in-memory manifest
        # must reflect what _update_manifest_after_index will find on disk
        # (degraded or fresh), so the early-return guard can detect
        # preliminary entries and fall through to regeneration.
        manifest = load_manifest(db_path.parent, config_hash)

    # WHY heartbeat: the index subprocess can deadlock (libclang hang, disk
    # full, NFS stall).  Without a heartbeat, the watchdog has no way to
    # distinguish "slow but alive" from "deadlocked."  This daemon thread
    # writes a timestamp to a known file every 30s — if the file stops
    # updating, the watchdog kills and restarts the process.
    _hb_log = os.environ.get("FW_CONTEXT_HEARTBEAT_LOG")
    if _hb_log:
        _hb_stop = threading.Event()

        def _heartbeat() -> None:
            while not _hb_stop.wait(30.0):
                try:
                    with open(_hb_log, "a") as f:
                        f.write(f"{time.strftime('%H:%M:%S')} heartbeat\n")
                except (OSError, ValueError, TypeError, RuntimeError, AttributeError, sqlite3.Error):
                    pass  # non-fatal — continue

        _hb_thread = threading.Thread(target=_heartbeat, daemon=True)
        _hb_thread.start()

    log.info("", extra={"phase": f"Indexing {name} ({config_hash[:12]})"})
    log.info("project=%s project_id=%s config_hash=%s", name, project_id, config_hash[:12])

    # ── M5-1: legacy DB is opened ONLY in explicit compat mode ──
    # Normal/default operation is v2-only: no legacy index.db is created or
    # opened. The v2 store (index-v2.db) owns all semantic storage.
    # FW_CONTEXT_LEGACY_COMPATIBILITY_MODE=1 opts back into the legacy mirror.
    from ._unit_processor import _legacy_compat_on
    _legacy_compat = _legacy_compat_on()
    conn = None
    initial_manifest_verification = "indexing"
    if _legacy_compat:
        conn = open_db(db_path)
        # Determine initial manifest verification for this config.
        # New config_hash → 'indexing' (hidden from MCP queries until complete).
        # Existing config → preserve the previous value (in-place update).
        old_row = conn.execute(
            "SELECT manifest_verification FROM build_configs WHERE config_hash=?",
            (config_hash,),
        ).fetchone()
        if old_row is None:
            initial_manifest_verification = "indexing"
        else:
            initial_manifest_verification = old_row["manifest_verification"]
    else:
        # v2-only: log that legacy mirror is off.
        log.debug("M5 v2-only mode: legacy DB not opened (compat=%s)", _legacy_compat)

    # Resolve analyze_vendor: CLI flag takes precedence, config falls back.
    # Only meaningful when analyze_symbols is True — otherwise analysis doesn't
    # run and the flag is irrelevant (storing True would mislead get_active_build).
    if analyze_symbols:
        _analyze_vendor = analyze_vendor or (llm_config is not None and llm_config.analyze_vendor)
    else:
        _analyze_vendor = False

    # Serialize with any concurrent writer (background reindex, daemon)
    if _legacy_compat:
        with write_lock(db_path.parent, timeout=120.0):
            with transaction(conn):
                upsert_project(conn, project_id, name, str(project_root))
                upsert_build_config(
                    conn,
                    config_hash,
                    project_id,
                    str(compile_commands),
                    # None keeps the recorded git context of the CONTENT.  This
                    # write happens before a single translation unit is read, so
                    # stamping the current branch here would mark the index as
                    # coming from a tree it has not been built from — and a run
                    # that fails leaves exactly that.  `_run_postprocess` writes
                    # the real value at the end, when the content matches it.
                    description=None,
                    manifest_verification=initial_manifest_verification,
                    analyze_vendor=int(_analyze_vendor),
                    variant=variant,
                    image=image,
                    board=board,
                )

    # Validate that all -include/-imacros referenced files exist BEFORE
    # starting any libclang parsing.  A missing build-generated config
    # header (e.g. BUILD/.../mbed_config.h) would otherwise cause libclang
    # to fail for every TU that includes the SDK, producing a partial index
    # and wasting ~seconds per TU on doomed parse attempts.
    for unit in units:
        validate_include_files(unit.clang_args)

    # ── Per-context identity (ADR-001/002, T14 SLICE-C) ──
    # One compile_commands.json entry is one context: the same source file
    # listed twice with different -D flags is two contexts of one build, and
    # each keeps its own observations (per-(config,usr,context) rows).  The
    # identity and its build_contexts row are computed by the ONE shared
    # helper, so manifest and storage cannot drift (ADR-001 risk note).
    #
    # Registered up front in one transaction, before any TU work: the row is
    # idempotent per (config_hash, context_id), a re-run recomputes the same
    # identities, and an unchanged TU must not depend on Phase 2 to have its
    # context registered.
    from .context_identity import unit_identity_fields

    unit_identities: dict[int, dict] = {}
    # M5: unit_identity_fields is a computation (no DB needed). The legacy
    # upsert_build_context write is gated behind _legacy_compat.
    if _legacy_compat:
        with transaction(conn, checkpoint=False):
            for i, unit in enumerate(units):
                fields = unit_identity_fields(unit, config_hash, project_root, entry_index=i)
                unit_identities[i] = fields
                upsert_build_context(conn, config_hash, fields["context_id"], **{
                    key: value
                    for key, value in fields.items()
                    if key not in ("config_hash", "context_id")
                })
    else:
        for i, unit in enumerate(units):
            unit_identities[i] = unit_identity_fields(
                unit, config_hash, project_root, entry_index=i)

    # Staleness records per (path, context_id).  A TU's Tier-1/2 comparison
    # must read the row of ITS context — the path-keyed view would let the
    # first context's record falsely skip a second context's never-indexed
    # command of the same file (T02 RC2 incremental false-claim).
    existing_by_context: dict[str, dict] = {}
    if _legacy_compat:
        for (path, ctx), rec in get_file_hashes_by_context(conn, config_hash).items():
            existing_by_context.setdefault(ctx, {})[path] = rec

    # Pre-build lookup dict for O(1) manifest entry access during Tier 2 checks.
    # *manifest* was loaded above (before config_hash computation) — reuse it.
    # KEYED PER (file, context): one file compiled with two flag sets has two
    # entries, and a file-keyed dict (last-wins) resolved both contexts to
    # the same — the last — entry, so the first context's Tier-2 check read
    # the second context's hashes.  The selection order (context id, then
    # argument match, then first) lives in manifest.select_entry_for_unit.
    manifest_lookup: dict[tuple[str, str], dict] = {}
    if manifest is not None:
        from .manifest import select_entry_for_unit

        entries_by_file: dict[str, list[dict]] = {}
        for e in manifest.get("entries", []):
            entries_by_file.setdefault(e.get("file", ""), []).append(e)
        for i, unit in enumerate(units):
            tu_rel = _tu_rel(unit, project_root)
            entry = select_entry_for_unit(
                manifest,
                entries_by_file.get(tu_rel, []),
                unit_identities[i]["context_id"] if unit_identities else None,
                unit,
            )
            if entry is not None:
                manifest_lookup[(tu_rel, unit_identities[i]["context_id"])] = entry
    # The entries hold header paths; their hashes live in this shared map.
    # Both travel together into the staleness checks.
    manifest_header_table: dict[str, dict] = (
        dict(manifest.get("headers") or {}) if manifest is not None else {}
    )

    # ── Header staleness pre-pass ──
    # A header is not a translation unit, so editing one leaves the mtime and
    # source hash of every dependent TU untouched — the per-TU mtime fast-path
    # would report them all as unchanged and the header's symbols would stay
    # frozen at their first-index state.  Hash the project headers once here
    # and mark every TU that includes a changed one for re-parsing.
    #
    # ALL dependent TUs are marked, not a subset: symbols from a shared header
    # are claimed by whichever TU stores them first, and a TU that keeps its
    # old rows would block the fresh ones via the ON CONFLICT(config_hash,
    # usr) guard in insert_symbols_batch.
    #
    # header_hash_cache is shared with the per-TU staleness checks below, so
    # each project header is read and hashed exactly once per run.
    header_hash_cache: dict[str, str] = {}
    header_stale_tus: frozenset[str] = frozenset()
    if manifest is not None:
        from .manifest import collect_stale_headers

        stale_headers = collect_stale_headers(
            manifest, project_root, hash_cache=header_hash_cache
        )
        header_stale_tus = _tus_to_requeue(manifest, stale_headers, project_root)

    # Drop FTS5 content-sync triggers before bulk indexing — each symbol
    # INSERT/DELETE/UPDATE would otherwise pay per-row FTS index overhead
    # (~2× write I/O).  The FTS table is rebuilt from scratch in one pass
    # after all TUs are stored.
    # If a previous run crashed after drop_fts_triggers() but before
    # rebuild_fts(), the triggers are missing — repair now so FTS stays
    # consistent during this run.
    # M5: FTS trigger maintenance is legacy DB only.
    missing_triggers = False
    if _legacy_compat:
        for trigger_name in ("symbols_ai", "files_ai", "macros_ai"):
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger_name,)
            )
            if cur.fetchone() is None:
                missing_triggers = True
        if missing_triggers and not defer_fts:
            log.info("FTS5 triggers missing (possibly from a crashed run) — rebuilding FTS first")
            rebuild_fts(conn)
        drop_fts_triggers(conn)

    total_syms = 0
    total_refs = 0
    skipped = 0
    unchanged = 0
    updated = 0
    acc_parse = 0.0
    acc_lock = 0.0
    acc_write = 0.0
    content_filled = 0
    # Collect headers during tokenization for incremental manifest update.
    # Maps (file_path, context_id) → list of {path, hash, generated} header
    # dicts — per CONTEXT, because two compile actions of one file collect
    # different header lists and a file-keyed dict kept only the last
    # context's.
    tu_headers: dict[tuple[str, str], list[dict]] = {}
    # TUs actually re-parsed in this run, as (file, context_id) pairs.  The
    # manifest may only take fresh header hashes from these — an entry
    # refreshed for a TU that kept its previous symbols would erase the
    # evidence that the index is behind.
    reparsed_tus: set[tuple[str, str]] = set()
    t0 = time.monotonic()

    # Is any PROJECT file still missing its ifdef-filtered content?  One query
    # per run instead of one per TU.  While a backfill is pending, unchanged
    # TUs keep running the content pass so it can complete; once every project
    # file has content they skip it, along with the libclang parse behind it.
    #
    # WHY project files only (relative path = inside project_root, see
    # _normalize_file_path): out-of-tree system headers routinely end up with
    # an empty content column — e.g. libstdc++ headers whose cursors were all
    # claimed by an earlier TU.  Counting those would pin the flag to True
    # forever on any C++ project and the pass would never be skipped.
    content_backfill_needed = (
        conn.execute(
            "SELECT 1 FROM files WHERE config_hash = ? AND content = '' "
            "AND path NOT LIKE '/%' LIMIT 1",
            (config_hash,),
        ).fetchone()
        is not None
        if _legacy_compat
        else False
    )

    # Header-claim map, per context: ``{path: context_id}`` — the context
    # that already processed the file IN THIS RUN.  The old flat skip set
    # let the first TU's claim suppress a second TU's view of the same
    # header even when the two differ by -D flags: under context-aware
    # storage that erased one context's symbols and its cross-context call
    # edges (T02 RC2/RC3, F01/F02).  A TU skips a path only when the SAME
    # context already processed it; two contexts of one build each walk
    # their own view of the shared headers.  Starts empty each run — never
    # loaded from manifest, never persisted.
    claimed_by: dict[str, str] = {}

    log.info("", extra={"phase": f"Parsing ({len(units)} TUs)"})

    # ── Phase-4 Slice 2: construct the v2 world context ONCE per run.
    # M5: v2 is the DEFAULT semantic engine. When v2 is enabled (either
    # v2_write_authority=True OR FW_CONTEXT_SHADOW_V2=1), the v2 world
    # context is built. When v2 is explicitly disabled (v2_write_authority
    # =False AND shadow_v2=0), v2 is not run (legacy-primary mode).
    world_ctx = None
    if _v2_shadow_flag_on():
        try:
            # M5: v2 is the DEFAULT semantic engine. The v2 world context is
            # ALWAYS built — in v2-only mode (default) it is the sole path, and
            # in compat mode it runs alongside legacy (M4 authority).
            import importlib
            _worlds_mod = importlib.import_module(
                "fw_context_mcp.store_v2.worlds")
            world_ctx = _worlds_mod.build_world_context(
                project_root=project_root,
                build_dir_patterns=build_dir_patterns,
                build_system=build_system,
                project_id=project_id)
        except Exception as _w_exc:                       # pragma: no cover
            log.debug("v2 world context unavailable: %s",
                      _w_exc)
            world_ctx = None

    # Single sequential loop with per-TU write lock for responsiveness.
    # The lock is acquired and released for each translation unit so that
    # manual operations (reindex_file, reset_index) can interleave via
    # the pause marker mechanism instead of blocking for 60+ seconds.
    #
    # Libclang parsing runs OUTSIDE the write lock — a single TU can take
    # seconds to minutes on large codebases (mbed-os, Zephyr).  Holding the
    # lock during parsing starves other indexers (bg reindex, concurrent
    # ``fw-context index --force``) and causes WriteLockTimeout errors.

    for i, unit in enumerate(units):
        raise_if_superseded(db_path.parent)  # Give up if another process took over
        fname = unit.file.name
        processed = i + 1
        ctx_fields = unit_identities[i]
        ctx_id = ctx_fields["context_id"]
        # This context's own staleness records and its own header claims.
        existing_ctx = existing_by_context.get(ctx_id, {})
        skip_files = frozenset(
            path for path, cid in claimed_by.items() if cid == ctx_id
        )

        # ── Phase 0 (Slice-4A): fast-reuse admission (pre-parse) ──
        # Flag-isolated: runs only when the v2 shadow produced a world
        # context (world_ctx is None ⇒ flag off ⇒ ZERO behavior change).
        # Accepted order (task §30): after TU discovery + world context,
        # BEFORE the expensive tiers/parse.  A COMMITTED hit (A8 bind
        # success) runs the BL-1 mandatory-minimum bookkeeping — the exact
        # "unchanged" path with FRESH hashes — and skips parse + legacy
        # semantic writes; manifest entries are NOT refreshed (reparsed_tus
        # untouched, §19).  ANY miss falls through to the unchanged full
        # path below (task §24).  The admission module never raises.
        if world_ctx is not None:
            _s4a_hit = None
            try:
                # Lazy dynamic import (Slice-0 inertness gate: flag off ⇒
                # store_v2 is never even loaded; same pattern as the
                # _cert_mod hook in _unit_processor).
                import importlib
                _admission_mod = importlib.import_module(
                    "fw_context_mcp.store_v2.admission")
                _s4a_hit = _admission_mod.attempt_fast_reuse(
                    unit=unit,
                    config_hash=config_hash,
                    context_id=ctx_id,
                    project_root=project_root,
                    db_path=db_path,
                    world=world_ctx,
                    existing_ctx=existing_ctx,
                    manifest=manifest_lookup,
                    hash_cache=header_hash_cache,
                    header_table=manifest_header_table,
                )
            except Exception:                                # pragma: no cover
                _s4a_hit = None                              # fail closed
            if _s4a_hit is not None:
                result = _handle_unchanged(
                    unit, "unchanged", _s4a_hit.hashes, conn, config_hash,
                    project_root, build_dir_patterns, db_path, existing_ctx,
                    skip_files=skip_files if skip_files else None,
                    manifest_lookup=manifest_lookup,
                    content_backfill_needed=content_backfill_needed,
                    context_id=ctx_id,
                )
                unchanged += 1
                content_filled += result["content_filled"]
                if result["headers"]:
                    tu_headers.update(result["headers"])
                log.info("[%d/%d] %s: fast-reused (cert %s, %s)",
                         processed, len(units), fname, _s4a_hit.cert_id,
                         _s4a_hit.binding_status)
                continue

        # ── Phase 1: staleness check + libclang parse (no lock) ──
        # WHY outside lock: libclang parsing can take seconds to minutes per
        # TU on large codebases (mbed-os, Zephyr).  Holding the write lock
        # during parsing starves other indexers (bg reindex, concurrent
        # ``fw-context index --force``) and causes WriteLockTimeout errors.
        check_status, parsed_data, parse_timing, hashes = _check_and_parse_unit(
            unit,
            config_hash,
            project_root,
            index_refs,
            existing_ctx,
            force=force,
            manifest=manifest_lookup,
            skip_files=skip_files,
            header_stale_tus=header_stale_tus,
            hash_cache=header_hash_cache,
            header_table=manifest_header_table,
            context_id=ctx_id,
        )

        # Snapshot the skip set BEFORE folding in this TU's own files.
        # Content fill must skip only headers already processed by EARLIER
        # TUs.  This TU's headers are filled now — their active lines come
        # from the AST walk, because get_tokens() returns tokens only for
        # the main file.  A post-update skip set would skip this TU's
        # headers entirely and leave files.content empty.
        skip_before = skip_files if skip_files else None

        if parsed_data is not None and hasattr(parsed_data, 'newly_seen_files'):
            claimed_by.update({path: ctx_id for path in parsed_data.newly_seen_files})

        if check_status == "unchanged":
            result = _handle_unchanged(
                unit, check_status, hashes, conn, config_hash, project_root,
                build_dir_patterns, db_path, existing_ctx,
                skip_files=skip_before,
                manifest_lookup=manifest_lookup,
                content_backfill_needed=content_backfill_needed,
                context_id=ctx_id,
            )
            unchanged += 1
            content_filled += result["content_filled"]
            if result["headers"]:
                tu_headers.update(result["headers"])
            log.info("[%d/%d] %s: %s", processed, len(units), fname, result["status"])
            continue

        if check_status == "skipped":
            skipped += 1
            log.info("[%d/%d] %s: skipped", processed, len(units), fname)
            continue

        # ── Phase 2: DB store (inside lock) ──
        with write_lock(db_path.parent, timeout=120.0):
            status, syms, refs, timing, tu_headers_list = _process_unit(
                unit,
                config_hash,
                project_root,
                vendor_patterns,
                project_patterns_list,
                index_refs,
                db_path,
                existing_ctx,
                conn=conn,
                force=force,
                pre_parsed=parsed_data,
                parse_timing=parse_timing,
                hashes=hashes,
                build_dir_patterns=build_dir_patterns,
                skip_files=skip_before,
                context_id=ctx_id,
                world_ctx=world_ctx,
            )
            if status == "updated":
                updated += 1
                total_syms += syms
                total_refs += refs
                acc_parse += timing[0]
                acc_lock += timing[1]
                acc_write += timing[2]
                try:
                    tu_key = unit.file.resolve().relative_to(project_root).as_posix()
                except ValueError:
                    tu_key = unit.file.resolve().as_posix()
                # Only a re-parsed TU may refresh its manifest entry — its
                # symbols now match the headers it just read.  Keyed per
                # (file, context) so a pair of compile actions of one file
                # refreshes BOTH entries, not just the last one's.
                reparsed_tus.add((tu_key, ctx_id))
                if tu_headers_list:
                    tu_headers[(tu_key, ctx_id)] = tu_headers_list
                log.info(
                    "[%d/%d] %s: %d syms, %d refs, %.1fs",
                    processed,
                    len(units),
                    fname,
                    syms,
                    refs,
                    sum(timing),
                )
            else:
                skipped += 1
                log.info("[%d/%d] %s: skipped", processed, len(units), fname)


    # ── Phase-4 Slice 2: finalize the run's world + persist §40 metrics.
    # M4: when v2 write-authority is ON, v2 World finalization is the
    # AUTHORITATIVE success criterion (CASE A–D).  A failed or incomplete
    # finalize ⇒ INDEX FAILURE, even if legacy succeeded.  When OFF,
    # behavior is flag-gated and fully exception-isolated (legacy unaffected).
    # M7.5: v2 finalization is now gated behind FW_CONTEXT_V2_WRITE_AUTHORITY
    # (default "0" — v2 is retired from normal operation). When M6 write
    # authority is ON (default), M6 is the authoritative success criterion.
    _v2_authority = _v2_write_authority_on()
    _m6_write_authority = os.environ.get(
        "FW_CONTEXT_M6_WRITE_AUTHORITY", "1"
    ).strip() in ("1", "true", "yes", "on")
    _v2_finalize_enabled = _v2_authority and not _m6_write_authority
    if world_ctx is not None and _v2_finalize_enabled:
        try:
            import importlib
            _worlds_mod = importlib.import_module(
                "fw_context_mcp.store_v2.worlds")
            _publish_mod = importlib.import_module(
                "fw_context_mcp.store_v2.publish")
            v2_path = db_path.parent.parent / db_path.parent.name / "index-v2.db"
            store = _publish_mod.open_v2_store_for_write(v2_path)
            try:
                fin = _worlds_mod.finalize_world(store, world_ctx)
                _worlds_mod.record_run_metrics(store, world_ctx)
                try:
                    _query_mod = importlib.import_module(
                        "fw_context_mcp.store_v2.query")
                    _query_mod.ensure_slice3_schema(store._conn)
                except Exception:                 # pragma: no cover
                    pass                          # read-efficiency only
                log.info(
                    "v2 world %s: %s (%d bound, %d poisoned, %d missing)",
                    world_ctx.world_id, fin.world_status, fin.bound_count,
                    fin.poisoned_count, len(fin.missing))
                # M4: when v2 write-authority is on, an incomplete finalize
                # is an authoritative failure (CASE D: incomplete World must
                # never become current/queryable, and the run must fail).
                if _v2_authority and not fin.ok:
                    raise IndexIntegrityError(
                        f"v2 World {world_ctx.world_id} finalization failed "
                        f"(status={fin.world_status}, "
                        f"missing={len(fin.missing)}, "
                        f"bound={fin.bound_count}, "
                        f"poisoned={fin.poisoned_count}) — v2 is "
                        f"authoritative (FW_CONTEXT_V2_WRITE_AUTHORITY=1)")
            finally:
                store.close()
        except IndexIntegrityError:
            # M4 CASE C/D: propagate — v2 failure is authoritative.
            raise
        except Exception as _f_exc:
            if _v2_authority:
                # M4 CASE C: v2 finalize raised — authoritative failure.
                raise IndexIntegrityError(
                    f"v2 World finalization raised (v2 is authoritative): "
                    f"{_f_exc}") from _f_exc
            log.debug("v2 world finalize failed (legacy unaffected): %s",
                      _f_exc)

    # ── Linker scripts, between the C units and the assembly ──
    # The position is deliberate, and both neighbours depend on it — see
    # _store_linker_scripts.  M5: legacy-only path — gated behind compat mode.
    # In v2-only mode, linker scripts are handled by the v2 store.
    if _legacy_compat:
        linker = _store_linker_scripts(
            conn=conn,
            config_hash=config_hash,
            project_root=project_root,
            db_dir=db_path.parent,
            compile_commands=compile_commands,
            build_system=build_system,
            variant=variant,
            units=all_units,
            vendor_patterns=vendor_patterns,
            project_patterns=project_patterns_list,
        )
    else:
        linker = None

    asm = None

    # ── Assembly, after every other unit ──
    # The ordering is deliberate and the steps built on this one depend on
    # it: deciding whether a vector slot is handled means asking whether a
    # strong definition exists, and nothing can answer that until the C and
    # C++ units are stored.  M5: legacy-only path — gated behind compat mode.
    if asm_units and _legacy_compat:
        from .asm import store_units as _store_asm

        with write_lock(db_path.parent, timeout=120.0):
            with transaction(conn, checkpoint=False):
                asm = _store_asm(
                    conn, config_hash, asm_units, project_root, build_dir_patterns,
                    vendor_patterns, project_patterns_list,
                )
        log.info(
            "assembly: %d unit(s) -> %d file(s), %d symbol(s), "
            "%d vector edge(s), %d alias edge(s), %d unresolved vector(s)%s",
            len(asm_units), asm.files, asm.symbols, asm.vectors, asm.aliases,
            asm.unresolved,
            f", {asm.failed} could not be preprocessed" if asm.failed else "",
        )
        # A separate line, and only when a unit held a `.macro` at all.
        # A refused macro leaves its symbols out of the index, and a
        # refusal nobody can see looks exactly like a file that held
        # nothing worth indexing.
        macro_summary = asm.macros.summary()
        if macro_summary:
            log.info("assembly macros: %s", macro_summary)

    # ── Post-processing ──
    # Every path no libclang unit covers travels in one set.  The coverage
    # purge counts a file it cannot see in the units as missing, and the
    # assembly pass lost every row it wrote to exactly that purge before
    # its paths were threaded through.  A linker script is invisible the
    # same way — it is an input to the linker, not a compilation unit.
    uncovered_paths = set(asm.paths) if asm is not None else set()
    if linker is not None:
        uncovered_paths |= linker.paths
    # M5: legacy postprocess runs ONLY in explicit compat mode. In v2-only
    # mode (default), all 18 legacy postprocess steps are skipped — v2 owns
    # certification, publication, binding, and finalization.
    if _legacy_compat:
        _run_postprocess(
            asm_paths=uncovered_paths,
            conn=conn,
            config_hash=config_hash,
            project_root=project_root,
            db_dir=db_path.parent,
            units=units,
            tu_headers=tu_headers,
            manifest=manifest,
            compile_commands=compile_commands,
            updated=updated,
            build_dir_patterns=build_dir_patterns,
            vendor_patterns=vendor_patterns,
            project_patterns_list=project_patterns_list,
            project_id=project_id,
            git_description=git_description,
            index_refs=index_refs,
            index_embeddings=index_embeddings,
            index_macros_expanded=index_macros_expanded,
            analyze_symbols=analyze_symbols,
            analyze_overrides=analyze_overrides,
            analyze_vendor=_analyze_vendor,
            llm_config=llm_config,
            cache_server_config=cache_server_config,
            force=force,
            purge_max_missing_percent=purge_max_missing_percent,
            variant=variant,
            image=image,
            board=board,
            scope=scope,
            defer_fts=defer_fts,
            defer_cleanup=defer_cleanup,
            header_hash_cache=header_hash_cache,
            reparsed_tus=reparsed_tus,
            unit_context_ids={
                i: unit_identities[i]["context_id"] for i in unit_identities
            },
        )

    elapsed = time.monotonic() - t0
    log.info("", extra={"phase": f"Done — {total_syms} symbols, {total_refs} refs, {_fmt_dur(elapsed)}"})
    log.info("%d updated, %d unchanged, %d skipped  config_hash=%s", updated, unchanged, skipped, config_hash[:12])

    # ── M7.3: M6 PRODUCTION INDEXING ────────────────────────────────────────
    # After the legacy index completes, extract canonical M6 facts from
    # the freshly-written legacy DB and populate the M6 Shared Fact Graph.
    # This makes M6 the production WRITE authority (§8: production write →
    # M6 indexing → M6 store population).
    # When M6 write authority is disabled (FW_CONTEXT_M6_WRITE_AUTHORITY=0),
    # this step is skipped (legacy/v2 remains write authority).
    _m6_write_authority = os.environ.get(
        "FW_CONTEXT_M6_WRITE_AUTHORITY", "1"
    ).strip() in ("1", "true", "yes", "on")
    if _m6_write_authority:
        try:
            from fw_context_mcp.store_v2.m6_production_indexing import (
                index_legacy_db_into_m6,
            )
            build_name = variant or image or "default"
            if variant and image:
                build_name = f"{variant}/{image}"
            stats = index_legacy_db_into_m6(
                legacy_db_path=db_path,
                project_root=project_root,
                build_name=build_name,
            )
            log.info(
                "M6 production indexing: %d facts extracted for build '%s' "
                "(M6 is now production write authority)",
                stats.get("facts_total", 0), build_name,
            )
        except Exception as _m6_exc:
            # M6 indexing failure is NOT silently swallowed — it is logged
            # and (when M6 write authority is ON) raised as authoritative.
            log.error("M6 production indexing FAILED: %s", _m6_exc)
            raise IndexIntegrityError(
                f"M6 production indexing failed (M6 is authoritative): "
                f"{_m6_exc}") from _m6_exc

    return config_hash
