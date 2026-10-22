"""Maintenance MCP tools — index health checks, LLM configuration,
project listing, index reset, and single-file reindexing.

These are diagnostic and administrative tools distinct from the
search and code-navigation tools in other handler modules.  They
do not query the symbol index for code-analysis purposes; instead
they inspect index metadata, configure runtime behavior, and
manage the index lifecycle.

Public MCP handlers in this module:
    get_active_build         — mandatory first-call index health report
    get_environment_status   — aggregate deps/build/index/llm in one call
    check_ollama             — LLM backend availability and model status
    check_dependencies       — read-only dependency audit (structured results)
    configure_llm            — write per-developer LLM settings to local.toml
    reindex_file             — re-parse a single source file into the index
    reset_index              — delete the symbol database
    list_projects            — enumerate all indexed projects
    get_project_info         — look up a project by UUID4 in the global registry

Each handler is read-only except where noted in its docstring.
The ``Field(description=…)`` strings on public signatures are tool
registration metadata — they must not be modified casually."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ...config import ConfigError, derive_project_id
from ...config import load as load_config
from ...config.settings import Config
from ...indexer.autobuild import AutobuildState
from ...indexer.autobuild import build_advice
from ...indexer.autobuild import clear_problem as clear_build_problem
from ...indexer.autobuild import project_state as project_autobuild_state
from ...indexer.autobuild import read_problem as read_build_problem
from ...indexer.autobuild import state as autobuild_state
from ...indexer.build import checked_compile_commands, user_database
from ...indexer.compile_commands import command_line_defines
from ...indexer.compile_commands import parse as parse_cc
from ...indexer.db import (
    CURRENT_ROW_FORMAT,
    CURRENT_SCHEMA_VERSION,
    DatabaseCorruptionError,
    WriteLockTimeout,
    compute_analysis_coverage,
    count_refs,
    error_shows_damage,
    get_active_config,
    get_all_builds_for_project,
    get_all_projects,
    get_db_schema_version,
    get_entry_point,
    get_entry_points_by_config,
    get_memory_regions,
    get_memory_regions_by_config,
    make_analysis_summary,
    open_db,
    row_format_is_newer,
    row_format_is_older,
    symbols_without_embedding,
    transaction,
)
from ...indexer.git_context import branch_moved_since, head_moved, read_head, stored_head
from ...llm.ollama import check_setup
from ...utils import HEADER_EXTENSIONS, resolve_project_root
from ...indexer.autobuild import AutobuildState, build_advice, build_missing_reason
from ...indexer.db import (
    CURRENT_ROW_FORMAT,
    CURRENT_SCHEMA_VERSION,
    DatabaseCorruptionError,
    WriteLockTimeout,
    compute_analysis_coverage,
    count_refs,
    error_shows_damage,
    get_active_config,
    get_all_projects,
    get_db_schema_version,
    get_entry_point,
    get_memory_regions,
    make_analysis_summary,
    open_db,
    row_format_effect,
    row_format_is_newer,
    row_format_is_older,
    symbols_without_embedding,
    transaction,
)
from ..background import read_reindex_progress, read_reindex_state
from ..shared.stale import (
    _check_header_staleness,
    _count_modified_files,
    build_label,
    find_unindexed_sources,
    latest_builds,
    outdated_index_fact,
    outdated_index_reason,
    stored_row_format,
)
from ..shared.context import (
    _db_path,
    _detect_build_system,
    _is_stale,
    _quick_open_readonly,
    _resolve_context,
    get_executor,
    invalidate_executor,
)
from ..shared.index_state import RUN_STATES, WAIT_ADVICE, index_state, manifest_reason
from ..shared.stale import (
    _check_header_staleness,
    _count_modified_files,
    find_unindexed_sources,
    latest_builds,
    outdated_index_fact,
    outdated_index_reason,
    stored_row_format,
)
from ..shared.stale import after_the_run as _after_the_run
from ..shared.stale import with_action as _with_action
from ..shared.variants import IMAGE_DESCRIPTION, VARIANT_DESCRIPTION

log = logging.getLogger(__name__)


#: The command of the degraded `_warning` when `reindex_needed` is true and
#: no index run is in progress or scheduled: each reason then names its own
#: action.  The message of a project of several builds looks for it.
_REASON_COMMAND = "the command that reindex_reasons names"

#: What a build with ``manifest_verification = "partial"`` misses: the
#: embedding step failed (`_embedding.EmbeddingsIncomplete`), and the rest
#: of the index is complete.
PARTIAL_EMBEDDINGS = "semantic_search and smart_search can miss symbols (manifest verification: partial)"


def _new_source_advice(auto_state: AutobuildState, bg_running: bool, rebuild: str) -> str:
    """Return the advice for a source file that compile_commands.json does not cover.

    *rebuild* is the action that makes compile_commands.json again
    (``autobuild.build_advice``).  A build that failed is not an outcome
    here: the failed run writes its own text (``autobuild.read_problem``),
    which comes first.

    WHY *bg_running*: "fw-context builds them on its own" is true only when
    an index run comes.  The daemon schedules one for a new file
    (`daemon._staleness_check`), and without a daemon nothing does.  For
    another backend a run in progress gets the same advice as a checkout:
    wait, and act when the reason stays.  The state of a query also asks to
    wait during a run.

    The text comes after a dash in the reason, and after a colon in the
    message, thus it holds no dash and no colon of its own.
    """
    if auto_state is AutobuildState.WILL_BUILD:
        if bg_running:
            return "fw-context builds them on its own; no command is needed"
        return (
            "fw-context builds them in the next index run, and no index run is in "
            "progress or scheduled; run `fw-context index`"
        )
    run = _after_the_run(rebuild)
    if auto_state is AutobuildState.USER_DATABASE:
        # `rebuild` says that fw-context does not build that file.
        return f"an {run}" if bg_running else rebuild
    if bg_running:
        # The fact comes first: the condition of the action is the run.
        return f"fw-context does not build them on its own. An {run}"
    return f"fw-context does not build them on its own; {rebuild}"


def _autobuild_state(
    root: Path, proj_cfg, db_path: Path, new_sources: list[str]
) -> AutobuildState:
    """Say what will happen to *new_sources*, for the message and the status.

    Answers UNSUPPORTED when the build system cannot be determined: without
    a backend nothing can build on its own, and that is the answer that
    tells the caller to act rather than to wait.
    """
    if not new_sources:
        return AutobuildState.WILL_BUILD  # unused — no file to report
    return project_autobuild_state(root, proj_cfg)

    from dataclasses import replace

    from ...indexer.builders import registry
    from ...utils import autobuild_dir

    system = proj_cfg.build.system or _detect_build_system(root)
    builder_cls = registry.get(system) if system else None
    candidate = replace(proj_cfg.build, isolated_build_dir=autobuild_dir())
    return autobuild_state(builder_cls, candidate, db_path.parent, new_sources)


def _expand_dir_placeholder(dir_str: str, root: Path) -> str:
    """Expand the ``${NCS}`` placeholder in an image source dir (best-effort).

    ``dir`` for an SDK image may point outside project_root (``${NCS}/…``) —
    a machine-specific path that must not be committed literally.  The
    placeholder is expanded to the NCS root detected from standard signals;
    on failure the raw string is returned (the LLM still sees the placeholder).
    """
    if "${NCS}" not in dir_str:
        return dir_str
    try:
        from ...indexer.builders.zephyr import ZephyrBuildSystem

        ncs = ZephyrBuildSystem._detect_ncs(root)
        if ncs is not None:
            _, version, ncs_root = ncs
            return dir_str.replace("${NCS}", f"{ncs_root}/{version}")
    except Exception:  # nosec B110 — best-effort display expansion
        pass
    return dir_str


def _compile_commands_reason(stale_reason: str | None, cfg, root: Path, proj_cfg, bg_running: bool) -> str:
    """Give the reason of a compile_commands.json that changed after the index of the build *cfg*.

    *stale_reason* is the code of ``readiness._is_stale``.  For a missing
    file the caller gives the reason only when the build check
    (``_build_missing_advice``) does not cover the file, for example for
    the file of a build variant.  WHY a sentence and not the code: each
    other reason is a sentence that names its action, and a caller cannot
    act on a bare code.

    A missing file needs the action that makes it again
    (``autobuild.build_advice``): a plain run reads the same missing file.
    A newer file needs only a plain run, which reads it.
    """
    if stale_reason == "compile_commands_missing":
        return _with_action(
            f"compile_commands.json {cfg['compile_commands_path']} does not exist",
            build_advice(root, proj_cfg),
            bg_running,
        )
    if stale_reason == "compile_commands_newer":
        fact = "compile_commands.json changed after the last index run"
    elif stale_reason:
        # `is_compile_commands_stale` could not compare the two times, and
        # its code holds the error.  A run repairs a time of the index that
        # does not parse, because it writes the time again.  A run does not
        # repair an error of the file system (a file that this process
        # cannot read), and the error in the text names that cause.
        fact = f"fw-context cannot compare compile_commands.json with the index ({stale_reason})"
    else:
        fact = "compile_commands.json changed"
    return _with_action(fact, "run `fw-context index`", bg_running)


def _background_build_allowed(root: Path, proj_cfg: Config) -> bool:
    """Say whether fw-context may run a build of this project on its own.

    The same question `daemon._branch_needs_build` asks, and the same
    answer: `background_build_safe` refuses where a build of fw-context
    would reach the object files of the build of the user.  Asked here only
    to word the advice — a project the daemon will handle should not read
    like one that needs a command typed.

    Any failure to decide answers no, which keeps the command in the advice.
    """
    from dataclasses import replace

    from ...indexer.build import detect_build_system
    from ...indexer.builders import background_build_safe, registry
    from ...utils import SAFE_EXCEPT, autobuild_dir

    try:
        key = proj_cfg.build.system or detect_build_system(root)
        builder_cls = registry.get(key) if key else None
        if builder_cls is None:
            return False
        candidate = replace(
            proj_cfg.build,
            # ``default_variant`` retired 2026-10 (v0.35): no config field
            # any more — the plain (non-variant) autobuild layout.
            isolated_build_dir=autobuild_dir(getattr(proj_cfg.build, "default_variant", None) or ""),
        )
        return background_build_safe(builder_cls(), candidate)
    except SAFE_EXCEPT:
        return False


def _build_variant_discovery(cfg: Config, builds: list, root: Path) -> dict:
    """Build discovery data (variants/images/variant_images) for the LLM.

    WHY discovery: the LLM must know what the project contains (parts = images)
    and for which MCU variants indexes exist — without guessing names.  Source
    of truth is config ``[[build.variants]]`` first; when config ``images`` is
    empty (auto-detect), the actually-indexed ``build_configs`` rows fill in.
    """
    build_cfg = cfg.build
    variants_cfg = build_cfg.variants
    multi = bool(variants_cfg)

    variants = [
        {
            "name": v.name,
            "description": v.description,
            "board": v.board or build_cfg.board or "",
        }
        for v in variants_cfg
    ]

    images: list[dict] = []
    variant_images: dict[str, list[str]] = {}
    if any(v.images for v in variants_cfg):
        seen: set[str] = set()
        for v in variants_cfg:
            variant_images[v.name] = [img.name for img in v.images]
            for img in v.images:
                if img.name in seen:
                    continue
                seen.add(img.name)
                entry = {
                    "name": img.name,
                    "description": img.description,
                    "dir": _expand_dir_placeholder(img.dir, root) if img.dir else "",
                    "type": img.type,
                }
                if img.board:
                    entry["board"] = img.board
                images.append(entry)
    else:
        # Auto-detect from build_configs (no explicit images in config).
        seen_images: set[str] = set()
        for b in builds:
            variant_name = b["variant"] or ""
            image_name = b["image"] or ""
            if variant_name and image_name:
                variant_images.setdefault(variant_name, [])
                if image_name not in variant_images[variant_name]:
                    variant_images[variant_name].append(image_name)
            if image_name and image_name not in seen_images:
                seen_images.add(image_name)
                images.append({"name": image_name, "description": "", "dir": "", "type": "project"})

    return {
        "multi": multi,
        "variants": variants,
        "images": images,
        "variant_images": variant_images,
        # Retired 2026-10 (v0.35): the server selects no build; the keys stay
        # in the payload as None for tool-response stability.
        "active_variant": getattr(build_cfg, "default_variant", None),
        "active_image": getattr(build_cfg, "default_image", None),
    }


def list_variants(
    project_root: Annotated[
        str | None, Field(description="Project root directory. Auto-detected from CWD if omitted.")
    ] = None,
) -> dict:
    """List every indexed build with its (variant, image, board) identity.

    Read-only diagnostic — shows what is actually indexed, not what the config
    declares.  Each row is one ``(variant, image)`` build with its own
    ``config_hash`` and symbol count.  For single-project indexes this returns
    one row with ``variant``/``image`` empty.

    Use ``get_active_build`` for the mandatory first-call health check and the
    human-readable ``variants``/``images`` discovery; use this tool to see the
    per-build ``config_hash`` and symbol counts (authoritative per-build state).

    Args:
        project_root: Project root directory. Auto-detected from CWD if
            omitted.

    Returns:
        dict: {builds (list[dict]), multi (bool — True when the config
        declares variants or a build has a non-empty variant name)}.

        Each build dict holds: variant (str — empty for a single-project
        index), image (str — empty for a single-project index), board (str),
        config_hash (str), symbol_count (int), file_count (int),
        manifest_verification (str — "full" or "none"),
        entry_point (str — the `ENTRY()` of the linker script of this build,
        empty when no script names one),
        memory (list[dict] — the `MEMORY` regions of this build:
        {name, attributes, origin, length, origin_value, length_value,
        file_path, line}).  `origin` and `length` are the expression the
        script writes; `origin_value` and `length_value` are numbers, and
        they are None for an expression that names a symbol such as
        `ORIGIN(RAM) + LENGTH(RAM)`.  Empty for a build whose system
        records no linker script — see the note below.

        THIS is where a per-build memory map lives, not in the `images`
        list of ``get_active_build``: that list holds one entry per image
        NAME, and one name can belong to two variants with different
        addresses.

        When the project is not initialized, or has no index, the result is
        {builds: [], multi: False, error (str)}.
    """
    root = resolve_project_root(project_root)
    cfg = load_config(root)
    project_id = cfg.project.id
    if not project_id:
        # v2-only index: a project can hold an M6 store with no legacy
        # config.  List the M6 builds instead of reporting uninitialized.
        from fw_context_mcp.store_v2.m6_query import get_m6_store, list_builds
        m6 = get_m6_store(root)
        if m6 is not None:
            try:
                names = list_builds(m6)
                builds = [{"variant": n, "image": "", "build_name": n,
                           "config_hash": "", "m6_only": True} for n in names]
                return {"builds": builds, "multi": len(builds) > 1}
            except Exception:
                pass
        return {"builds": [], "multi": False, "error": f"Project at {root} is not initialized."}
    db_path = cfg.index.db_dir / project_id / "index.db"
    if not db_path.exists():
        # v2-only index (M6 store present, no legacy index.db): list the
        # M6 builds instead of degrading to an error.
        from fw_context_mcp.store_v2.m6_query import get_m6_store, list_builds
        m6 = get_m6_store(root)
        if m6 is not None:
            try:
                names = list_builds(m6)
                builds = [{"variant": n, "image": "", "build_name": n,
                           "config_hash": "", "m6_only": True} for n in names]
                return {"builds": builds, "multi": len(builds) > 1}
            except Exception:
                pass
        return {"builds": [], "multi": False, "error": f"No index found for {root}."}

    conn = _quick_open_readonly(db_path)
    try:
        builds = get_all_builds_for_project(conn, project_id)
        # The memory map is per build, thus it belongs on these rows and not
        # in the `images` list of get_active_build: that list holds one entry
        # per image NAME, and two variants of the Zephyr project both carry an
        # image called `app` with different flash addresses.
        hashes = [b["config_hash"] for b in builds]
        regions_by_config = get_memory_regions_by_config(conn, hashes)
        # Read separately and not as a column of the query above: this
        # connection comes from `_quick_open_readonly`, which skips the
        # schema block by design, so an index written before this feature
        # holds no such column.  Naming it in a shared SELECT made
        # get_active_build raise on the ESP32 project.
        entry_by_config = get_entry_points_by_config(conn, hashes)
    finally:
        conn.close()

    result_builds: list[dict] = []
    for b in builds:
        result_builds.append(
            {
                "variant": b["variant"] or "",
                "image": b["image"] or "",
                "board": b["board"] or "",
                "config_hash": b["config_hash"],
                "symbol_count": b["symbol_count"],
                "file_count": b["file_count"],
                "manifest_verification": b["manifest_verification"],
                "entry_point": entry_by_config.get(b["config_hash"], ""),
                "memory": regions_by_config.get(b["config_hash"], []),
            }
        )

    multi = bool(cfg.build.variants) or any(r["variant"] for r in result_builds)
    return {"builds": result_builds, "multi": multi}


# ── moved from server.py ──
def get_active_build(
    project_root: Annotated[
        str | None, Field(description="Project root directory. Auto-detected from CWD if omitted.")
    ] = None,
    fast: Annotated[
        bool, Field(description="When True (default), reuse the cached manifest "
        "hashes for the header check. Both modes count modified files. Pass "
        "False to recompute the header hashes, which is far slower.")
    ] = True,
) -> dict:
    """MANDATORY FIRST CALL for C/C++ projects. Return metadata about the
    most recently indexed build configuration — check index health before
    using any other fw-context tools.

    Read-only, and it spawns no subprocess — the startup daemon thread and
    the file watcher own the background reindex.

    Act on ``reindex_state`` of the build that the task needs, in
    ``builds`` (a project of one build has it at the top level too):

    * ``null`` — the index of the build is current. Continue.
    * ``"reindexing"`` or ``"pending"`` — the index of the build is not
      current, and an index run is active or scheduled. Wait, and call
      this tool again after about 30 s. Continue with the old index only
      when the user tells you to.
    * ``"not_scheduled"`` — the index of the build is not current, and no
      run updates it. Tell the user the command that the reasons name.
    * ``"failed"`` — the last index run failed. Give its text to the user:
      it names the command that repairs it.

    Each query answer carries the state of its build as ``index_state``,
    from quick checks only. A query thus can miss a changed header, a
    changed file that its answer does not name, or a new source file when
    its answer is not empty. This tool does the full check.

    ``status`` gives the state of the project:

    * ``"ready"`` — up to date. Continue.
    * ``"reindexing"`` — background reindex running; queries stay accurate.
      Continue. ``reindex_progress`` holds its last log line.
    * ``"reindex_needed"`` — schema mismatch, an old row format, changed
      compile_commands.json, or a source file that compile_commands.json
      does not cover. Queries still work on existing data. Read
      ``reindex_reasons``: a missing source file needs
      ``fw-context index --build``, the others need only
      ``fw-context index``.

      Read ``bg_reindex_running`` before you name any command here.  A run
      already under way does the work, thus a second one only waits on the
      same lock, and ``index_message`` and the branch reason both say so.
      That field covers a run of the daemon AND a run the operator started
      — the arguments of neither are readable, thus a reason that needs
      ``--build`` can outlive a run without it.  This status wins over
      ``"reindexing"`` deliberately: the answers of this moment come from
      the rows of the last FINISHED run.
    * ``"no_index"`` — initialized, never indexed. Run ``fw-context index``.
    * ``"not_initialized"`` — run ``fw-context init``.
    * ``"error"`` — DB corruption or access error. Use other tools.

    Four conditions set ``reindex_needed``: an outdated schema, an outdated
    ROW FORMAT, a changed compile_commands.json, and a source file that is
    on disk but absent from compile_commands.json.  The last one needs a
    build, because only the build system writes that file — a plain reindex
    has no translation unit for the file and skips it without a word.
    Modified source files are something else: they are handled per-query,
    and never set it.

    ``row_format_mismatch`` means that the same columns hold text with an
    older meaning.  Take it seriously: an index written before
    ``fw-context-rows/1`` keeps every inactive ``#ifdef`` branch, thus a
    body or a file from it can show code that the compiler never sees.

    ``client_restart_required`` is the OPPOSITE case, and no command repairs
    it.  The index carries a NEWER row format than this server process
    reads, thus the index is the correct one and this process is the old
    reader.  A reindex makes it worse than useless: the indexer writes the
    same new format again, and the message comes back over an index that had
    nothing wrong with it.  ``status`` therefore stays ``"ready"`` and
    ``reindex_needed`` stays False — every query keeps working.

    Do NOT run a reindex for this field.  Tell the operator to restart the
    LLM client — Claude Code, opencode, or whichever one is in use.  The MCP
    server is a child process of that client, thus nobody can restart the
    server by itself.  ``client_restart_reason`` holds the wording, and
    ``index_message`` opens with it.

    ``indexed_at`` and ``first_indexed_at`` are UTC; file mtimes are local
    time.  Never compare the two directly — in UTC+2 a correctly indexed
    file looks 2 hours newer than ``indexed_at``.  Call with ``fast=False``
    to find modified files.

    ``analysis`` splits the LLM-analysis coverage into project and vendor
    symbols:

    * ``model`` — the model of the analysis, or None. One model only, even
      when several were used.
    * ``analyze_vendor`` — the value at index time, not the current config.
    * ``project`` / ``vendor`` — ``{analyzed, skipped, total}``.
      ``skipped`` = tried, but not analyzable (body larger than the model
      context, an unparseable answer, or a body that was not readable).
    * ``complete`` — no work left: every project symbol is analyzed or
      skipped.  True exactly when ``reindex_reasons`` holds no "unanalyzed
      symbols" entry.  Vendor symbols excluded by ``analyze_vendor=False``
      never block it, thus ``vendor.total`` large with ``vendor.analyzed=0``
      is expected, not a defect.

    Args:
        project_root: Project root directory. Auto-detected from CWD if
            omitted.
        fast: When True (default), the header check reuses the cached
            manifest hashes.  Both modes run the per-file scan, thus
            ``modified_files_count`` is accurate either way — a tool that
            reported "ready" while the search tools warned about the same
            file gave the caller two readings and no way to choose.
            False recomputes the header hashes and costs several times more.

    Returns:
        dict: {config_hash, project_id, project_root, build_system,
        compile_commands, indexed_at (str — "YYYY-MM-DD HH:MM:SS" in UTC,
        the completion time of the last full index), symbol_count, file_count,
        reference_count, modified_files_count (int — files whose content no
        longer matches the index; counted in both modes),
        header_affected_tus (int — number of TUs with stale header
        dependencies), manifest_verification (str —
        "full" when manifest.json exists, "none" otherwise),
        analysis (dict — LLM-analysis coverage split by project/vendor:
        {model, analyze_vendor, project: {analyzed, skipped, total},
        vendor: {analyzed, skipped, total}, complete}),
        description (str), first_indexed_at (str — UTC, same format as
        indexed_at),
        vendor_paths (list[str] — config index.vendor_paths),
        project_paths (list[str] — config index.project_paths),
        effective_vendor_patterns (list[str] — the SQL LIKE patterns that
        this build really used to mark vendor code, for example
        ``["mbed-os/%"]``; empty when the manifest cannot be read.  The two
        lists above hold what the config asks for, this one what the index
        did),
        bg_reindex_running (bool),
        reindex_progress (str or None — last log line when reindex is running),
        schema_version (int — DB schema version),
        current_schema (int — code expects), status (str — "ready"|"reindexing"|
        "reindex_needed"|"no_index"|"not_initialized"|"error"), reindex_needed (bool —
        structural mismatch requiring a full reindex),
        reindex_reasons (list[str] — why reindex is needed, empty when False.
        One of them asks for `fw-context index --build` rather than a plain
        reindex: when the tree is on a different branch than the index,
        compile_commands.json belongs to the OLD branch and carries its file
        list and its compiler flags, so only a build regenerates it.  Read
        the reason text — it names the command it needs),
        stale (bool — True when reindex_needed or header_affected_tus > 0),
        _warning (str, optional — when manifest verification is not "full"),
        vec_available (bool), vec_error (str, optional),
        index_message (str — human-readable summary of index state),
        multi (bool — True for a multi-variant project),
        variants (list[dict] — {name, description, board}),
        images (list[dict] — {name, description, dir, type}),
        variant_images (dict — variant name to its image names),
        active_variant (str or None — [build] default_variant),
        active_image (str or None — [build] default_image),
        entry_point (str — the `ENTRY()` of the linker script of the build
        that the other fields describe, empty when no script names one),
        memory (list[dict] — the `MEMORY` regions of that build:
        {name, attributes, origin, length, origin_value, length_value,
        file_path, line})}

        About ``memory``: ``origin`` and ``length`` hold the expression the
        script writes, thus they differ by platform — an mbed script writes
        `0xefe00` and a Zephyr script writes `((673792) - 0xe6)`.
        ``origin_value`` and ``length_value`` hold the number, and both are
        None for an expression that names a symbol, such as
        `ORIGIN(RAM) + LENGTH(RAM)`.  The end of a region is
        ``origin_value + length_value``.

        ``memory`` and ``entry_point`` describe ONE build.  For a
        multi-variant project they follow ``config_hash``, which is the
        build named by ``[build] default_variant``, and both are empty when
        the config names no default.  Use ``list_variants`` for the map of
        every build.

        ``memory`` is empty for a build system that records no linker
        script.  A PlatformIO project is the measured case: SCons writes no
        ninja file and no link command the index can read, and the map file
        never names the script.  An empty list means "not recorded", never
        "no memory".

        ``defines`` (dict — the `-D` flags of that build) and
        ``defines_varying`` (int).  ``defines`` holds only the names that
        EVERY translation unit of the build carries with the same value, so
        the tool never shows the defines of one file as the defines of the
        build.  ``defines_varying`` counts the names left out, thus a name
        absent from ``defines`` is either not defined at all or not defined
        everywhere — measured on the Mbed project: 27 names in all 881 units, 59
        in only some, where the three assembly files get a shorter set.

        This is the configuration the BUILD states, not every macro the
        preprocessor saw.  The second is three orders of magnitude larger —
        27800 distinct names on the STM32 project — and almost all of it comes from the
        headers and the compiler.  A Zephyr build keeps its real
        configuration in ``autoconf.h`` (740 `CONFIG_*` names) and passes
        few `-D` flags, so ``defines`` says little there and a great deal on
        an mbed build, where it holds `APPLICATION_ADDR`,
        `APPLICATION_SIZE`, and `CMSIS_VECTAB_VIRTUAL`.

        For a project that is not initialized, the result holds only
        ``status``, ``project_root``, and ``index_message``.  When no index
        exists, the result adds ``project_id``.
    """
    root = resolve_project_root(project_root)
    # Resolve project ID directly — bypass _db_path/derive_project_id so
    # get_active_build can report not_initialized/no_index gracefully
    # instead of raising.  Other tools still use derive_project_id
    # (via _resolve_context) and raise ProjectNotInitializedError when
    # the project is not initialized — fail-fast for operational tools,
    # graceful degradation for this diagnostic tool.
    fw_cfg = load_config(root)
    project_id = fw_cfg.project.id
    if not project_id:
        # The project directory exists but was never initialized.
        # Return a human-readable status so the LLM can guide the user.
        # v2-only projects (indexed straight into the M6 store, no legacy
        # init/config) are NOT "not initialized": answer from the M6 store.
        from fw_context_mcp.store_v2.m6_query import get_m6_store, project_status
        m6 = get_m6_store(root)
        if m6 is not None:
            try:
                status = project_status(m6, active="")
                return {
                    "status": "ready",
                    "project_root": str(root),
                    "project_id": fw_cfg.project.id or "",
                    "indexed_as": "m6",
                    "builds": status["builds"],
                    "total_facts": status["total_facts"],
                    "multi": status["multi"],
                    "active_variant": status["active"] or None,
                    "index_message": (
                        f"Project at {root} has an M6-native index "
                        f"({len(status['builds'])} build(s), "
                        f"{status['total_facts']} facts). "
                        "No legacy config on disk; queries read the M6 store."
                    ),
                }
            except Exception as e:
                from fw_context_mcp.utils import log
                log.debug("get_active_build M6 branch failed: %s", e)
        return {
            "status": "not_initialized",
            "project_root": str(root),
            "index_message": (
                f"Project at {root} is not initialized. "
                "Run `fw-context init` via bash to create a project ID and config, "
                "then call get_active_build() again."
            ),
        }
    db_path = fw_cfg.index.db_dir / project_id / "index.db"
    if not db_path.exists():
        # Project is initialized but no index was ever built.
        # The LLM uses this to prompt the user for indexing.
        return {
            "status": "no_index",
            "project_root": str(root),
            "project_id": project_id,
            "index_message": (
                f"No symbol index found for {root}. "
                "Run `fw-context index <path/to/compile_commands.json>` via bash "
                "to build the index, then call get_active_build() again."
            ),
        }

    # Check bg reindex status BEFORE querying the DB — when a bg reindex
    # is running, the _modified_cache may contain stale counts from before
    # the reindex started.  Skipping the cache ensures accurate counts.
    bg_running = _is_bg_reindex_running(root)

    # Open a short-lived read-only connection to fetch the active config
    # hash.  We close it immediately — the actual heavy queries run
    # through the executor on its own connection.
    conn = _quick_open_readonly(db_path)
    try:
        project_id = derive_project_id(root)
        cfg = get_active_config(conn, project_id)
        if not cfg:
            return {"error": f"No build config indexed for project at {root}."}
        config_hash = cfg["config_hash"]
    finally:
        conn.close()

    # The executor serializes all DB access through a single shared
    # connection.  This prevents SQLITE_BUSY errors when the MCP server
    # handles concurrent requests and a background reindex is running.
    executor = get_executor(db_path)

    def _query(conn, config_hash):
        # Runs under the executor lock on the single shared connection;
        # must not open its own connection.  Timeout is enforced by
        # _wrap_tool (300 s + interrupt), not here.
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols WHERE config_hash=?", (config_hash,)).fetchone()[0]
        file_count = conn.execute("SELECT COUNT(*) FROM files WHERE config_hash=?", (config_hash,)).fetchone()[0]
        ref_count = count_refs(conn, config_hash)
        manifest_verification = cfg.get("manifest_verification", "none")
        if fast:
            # The scan runs in fast mode too.  Without it this tool answered
            # "ready" while search_code on the same index warned about the
            # same file, and the caller had no way to tell which reading
            # held.  It costs about 41 ms on a project of 1900 files,
            # against the 749 ms that the header check below already spends.
            #
            # use_cache=False on purpose: the cache validates itself against
            # MAX(mtime) of the files table, which only a reindex moves, thus
            # a cached answer would miss the edit that just happened.
            modified_count = _count_modified_files(conn, config_hash, root, use_cache=False)
            # NOTE: the header check is NOT cheap — it hashes every header of
            # every translation unit, which is 22,693 files and 749 ms on
            # The Mbed project.  `fast` only gives it a cache; it does not skip it.
            if manifest_verification == "full":
                header_affected_tus, _ = _check_header_staleness(
                    conn, config_hash, root, use_cache=True,
                )
            else:
                header_affected_tus = 0
        else:
            # Full stat scan — walks every indexed file on disk to detect
            # mtime changes.  Slow (~100 ms for large projects) but accurate.
            modified_count = _count_modified_files(conn, config_hash, root, use_cache=False)
            # Check header dependencies separately (different metric: TUs, not files)
            if manifest_verification == "full":
                header_affected_tus, _ = _check_header_staleness(
                    conn,
                    config_hash,
                    root,
                    use_cache=False,
                )
            else:
                header_affected_tus = 0
        db_schema_ver = get_db_schema_version(conn)

        # LLM analysis coverage — split by project vs vendor so the LLM can
        # tell intentionally-skipped vendor symbols apart from project
        # symbols that genuinely still need analysis.
        coverage = compute_analysis_coverage(conn, config_hash)
        # Exclude the skip:* sentinels (skip:toolarge, skip:unparseable) —
        # they are not model names and must never surface as analysis.model.
        analysis_model_row = conn.execute(
            """SELECT a.model FROM llm_analysis a
               JOIN symbols s ON s.id = a.symbol_id
               WHERE s.config_hash = ?
                 AND a.model NOT LIKE 'skip:%' LIMIT 1""",
            (config_hash,),
        ).fetchone()
        model = analysis_model_row["model"] if analysis_model_row else None

        # Read stored analyze_vendor — what was actually indexed.
        # Fall back to False for DBs that predate the column.
        try:
            stored_analyze_vendor = bool(
                conn.execute(
                    "SELECT analyze_vendor FROM build_configs WHERE config_hash = ?",
                    (config_hash,),
                ).fetchone()["analyze_vendor"]
            )
        except sqlite3.OperationalError:
            stored_analyze_vendor = False

        # Load project config for vendor_paths/project_paths in the response.
        proj_cfg = load_config(project_root=root)

        analysis = make_analysis_summary(coverage, stored_analyze_vendor, model)

        cc_changed, stale_reason = _is_stale(cfg, cfg["compile_commands_path"])
        schema_old = db_schema_ver < CURRENT_SCHEMA_VERSION
        # Does the stored text still mean what this version reads it to mean?
        # The schema version above cannot answer that: it hashes the column
        # SET, thus it moves only when a column appears or goes, while the
        # content of a column can change under an unchanged name.  See
        # CURRENT_ROW_FORMAT.
        # The test is ordinal, and not a plain inequality, because the two
        # directions need opposite answers.  An OLDER format asks for a
        # reindex, and the run repairs it.  A NEWER one cannot be repaired by
        # any command: the index is right and THIS PROCESS is the old reader,
        # thus the indexer writes the same new format again.  See the warning
        # that `row_format_newer` builds below.
        stored_row_format = str(cfg.get("row_format") or "")
        row_format_old = row_format_is_older(stored_row_format)
        row_format_newer = row_format_is_newer(stored_row_format)

        # A source file that the build system never saw has no translation
        # unit, thus a plain reindex cannot pick it up: it is absent from
        # compile_commands.json, and only a build puts it there.  Without
        # this check the tool reported "ready" over a file it knew nothing
        # about, and it kept reporting it after a reindex.
        new_sources = find_unindexed_sources(
            conn, config_hash, root, Path(cfg["compile_commands_path"])
        )
        # What will happen to those files decides both the status and the
        # wording.  fw-context builds them on its own where the backend can
        # isolate its output, and then the caller has nothing to do; where it
        # cannot, or where a build already failed, the caller must act.
        auto_state = _autobuild_state(root, proj_cfg, db_path, new_sources)

        # Three conditions force a reindex:
        #   1. Schema version in DB is older than current code expects.
        #   2. compile_commands.json was modified since indexing
        #      (detected via mtime comparison in _is_stale).
        #   3. A source file is missing from compile_commands.json AND
        #      fw-context will not build it on its own.  When it will, the
        #      state is "reindexing" below — work is under way, and telling
        #      the caller to run a command would only duplicate it.
        # Modified source files are handled per-query via auto-reindex
        # and do NOT cause reindex_needed=True.
        blocked_sources = bool(new_sources) and auto_state is not AutobuildState.WILL_BUILD

        # ── The tree moved to another branch since this index ──
        # A fourth condition, and the only one a plain reindex cannot fix.
        # compile_commands.json is a build artifact of the branch it was
        # generated on: it carries that branch's file list AND its compiler
        # flags.  Measured on the Mbed project, a switch from 4.15.3 to 4.15.1
        # left two generated zcbor sources listed in that file and absent
        # from the tree, and a reindex reads the same file again.
        indexed_branch, live_branch = branch_moved_since(
            cfg["description"] if "description" in cfg.keys() else "", root
        )
        branch_moved = bool(indexed_branch)
        needs_reindex = (
            cc_changed or schema_old or row_format_old or blocked_sources or branch_moved
        )

        # Asked once, because the call reaches the build backend and is not
        # free.  Only the branch reason below reads it.
        bg_build_allowed = branch_moved and _background_build_allowed(root, proj_cfg)

        # Build reindex_reasons — only when reindex is actually needed
        reindex_reasons: list[str] = []
        if branch_moved:
            # Always names `--build`, never a plain reindex: that would reuse
            # compile_commands.json, which belongs to the branch the index
            # was built on, with its file list and its compiler flags.
            #
            # The daemon runs it on its own where the backend allows a
            # background build, so the advice says both — a checkout on a
            # project nobody is watching gets no burst, and then the manual
            # command is the only thing that fixes it.  Measured: without the
            # second half this reason read as a command while the
            # new-source reason beside it read "no command is needed", and
            # the two contradicted each other on one checkout of the Mbed project.
            # A run already under way changes the advice: the command named
            # here is the command that runs, thus naming it asks for the same
            # work a second time.
            #
            # WHY the wording stays careful: `bg_running` says that AN INDEX
            # RUN exists, and nothing more.  The run belongs to the daemon or
            # to the operator — `reindex.pid` carries both (see
            # `background._is_bg_reindex_running`) — and its arguments are
            # not readable from here.  Only `--build` regenerates
            # compile_commands.json, thus a plain run leaves this reason
            # standing, and a promise that the run clears it would be a
            # guess.
            if bg_running:
                branch_advice = (
                    "; an index run is in progress — wait for it rather than "
                    "start a second one, and run `fw-context index --build` "
                    "afterwards only if this reason stays (a run without "
                    "--build cannot clear it)"
                )
            elif bg_build_allowed:
                branch_advice = (
                    "; a background reindex regenerates it with --build when "
                    "the watcher sees a change, or run "
                    "`fw-context index --build` now"
                )
            else:
                branch_advice = ", run `fw-context index --build`"
            reindex_reasons.append(
                f"branch changed: indexed on {indexed_branch!r}, "
                f"now on {live_branch!r} — compile_commands.json belongs to "
                f"the old branch" + branch_advice
            )
        if schema_old:
            reindex_reasons.append(f"schema_mismatch: {db_schema_ver} < {CURRENT_SCHEMA_VERSION}")
        if row_format_old:
            # Names the effect, not only the value: the caller has to know
            # that the answers it gets now describe a world this version
            # reads differently, which is not obvious from a version string.
            # The meaning per stored format is kept in one place — rows/2
            # means pre-per-context rows (rows/3 added context_id and the
            # per-context keys), anything else falls back to the generic
            # wording.
            _row_format_meanings = {
                "fw-context-rows/2": (
                    "its rows predate per-context storage and carry no "
                    "compile-context identity"
                ),
            }
            meaning = _row_format_meanings.get(
                stored_row_format,
                "the stored rows were written by an older row format",
            )
            reindex_reasons.append(
                f"row_format_mismatch: {stored_row_format or '(none)'} != "
                f"{CURRENT_ROW_FORMAT} — {meaning}. Run `fw-context index`"
            )
        if cc_changed:
            reindex_reasons.append(stale_reason or "compile_commands_changed")
        if new_sources:
            listed = ", ".join(new_sources[:3])
            more = f" and {len(new_sources) - 3} more" if len(new_sources) > 3 else ""
            reindex_reasons.append(
                f"{len(new_sources)} source file(s) not in compile_commands.json "
                f"({listed}{more}) — {_AUTOBUILD_ADVICE[auto_state]}"
            )

        # Determine status — single value that drives LLM decision-making.
        # Priority: reindex_needed > reindexing > ready.
        #
        # A file that fw-context will build on its own counts as
        # "reindexing": the work is under way or about to be, and every query
        # keeps working meanwhile.  "reindex_needed" would ask the caller for
        # a command that only duplicates it.
        if needs_reindex:
            status = "reindex_needed"
        elif bg_running or (new_sources and auto_state is AutobuildState.WILL_BUILD):
            status = "reindexing"
        else:
            status = "ready"

        # Build human-readable index message
        if status == "ready" and modified_count:
            # The index is structurally sound, thus the status stays "ready"
            # and every query keeps working.  But files on disk have moved
            # past it, and the per-query warnings will say so — this message
            # must not contradict them by claiming full freshness.
            index_message = (
                f"Index is usable ({sym_count} symbols), but {modified_count} file(s) "
                f"changed since the last index run. Results about those files can be "
                f"out of date — run `fw-context index`."
            )
        elif status == "ready":
            index_message = f"Index is fully up to date ({sym_count} symbols)"
        elif status == "reindexing":
            if new_sources and auto_state is AutobuildState.WILL_BUILD:
                index_message = (
                    f"{len(new_sources)} source file(s) are not in compile_commands.json "
                    f"(first: {new_sources[0]}). fw-context builds them on its own — "
                    f"no command is needed. Queries work on existing data meanwhile."
                )
            elif modified_count:
                index_message = (
                    f"Index is usable — {modified_count} file(s) being reindexed "
                    f"in background. All queries return accurate results."
                )
            else:
                index_message = (
                    "Index is usable — background reindex in progress. All queries return accurate results."
                )
        elif new_sources:
            # Checked before the other reindex reasons: `--build` is the only
            # command that repairs this one, and a message that says plain
            # `index` would send the caller in a circle.  Reaching here means
            # fw-context will NOT build them itself — _AUTOBUILD_ADVICE says
            # which of the two reasons applies.
            index_message = (
                f"{len(new_sources)} source file(s) are not in compile_commands.json "
                f"(first: {new_sources[0]}). A plain reindex cannot see them — "
                f"{_AUTOBUILD_ADVICE[auto_state]}. Queries still work on existing data."
            )
        elif schema_old and cc_changed:
            index_message = (
                f"Schema version mismatch ({db_schema_ver} < {CURRENT_SCHEMA_VERSION}) "
                f"and compile_commands.json changed. Run fw-context index. "
                f"Queries still work on existing data."
            )
        elif schema_old:
            index_message = (
                f"Schema version mismatch ({db_schema_ver} < {CURRENT_SCHEMA_VERSION}). "
                f"Run fw-context index. Queries still work on existing data."
            )
        else:
            index_message = "Compile commands changed — run fw-context index. Queries still work on existing data."

        # ── A reason and a running index at the same time ──
        # Every message above names a command.  When a run is already under
        # way that reads as a contradiction, and a caller that follows it
        # asks the operator for work that is being done.  Say so in front,
        # because the front of this string is what a caller acts on.
        #
        # `status` stays "reindex_needed" on purpose.  The answers of THIS
        # moment still come from the rows of the last finished run, thus a
        # caller must not read them as final — and "reindexing" carries
        # "fully operational, continue" in the tool instructions.
        #
        # The wording promises nothing about the outcome: `bg_running` says
        # only that an index run exists.  It belongs to the daemon or to the
        # operator, and its arguments cannot be read from here, thus a reason
        # that needs `--build` may well survive it.
        if needs_reindex and bg_running:
            index_message = (
                "An index run is in progress — wait for it rather than start "
                "a second one, and ask the operator for no command yet. It "
                "can clear the reasons below, but a reason that needs "
                "`--build` clears only if the run uses it. Answers meanwhile "
                "come from the last finished run. " + index_message
            )

        # When manifest verification is not full, warn the LLM.
        _warning = None
        if manifest_verification != "full":
            if bg_running:
                _warning = (
                    "Index was built without full header dependency "
                    "tracking (manifest verification: "
                    f"{manifest_verification}). A reindex is currently "
                    "running — the index will be updated once it "
                    "completes. You may continue analysis, but note "
                    "that header changes since the last completed "
                    "index may not be reflected."
                )
                index_message += (
                    f" — manifest verification: {manifest_verification} (reindex in progress — wait for completion)"
                )
            else:
                _warning = (
                    "⚠️ INDEX DEGRADED — STOP ALL C/C++ ANALYSIS "
                    "IMMEDIATELY.\n\n"
                    "manifest.json is NOT available "
                    "for this index (manifest verification: "
                    f"{manifest_verification}). The index may contain "
                    "STALE data — header changes cannot be detected "
                    "without manifest.json. Continuing analysis with a "
                    "stale index WILL produce incorrect results.\n\n"
                    "REQUIRED: Tell the user to run 'fw-context index' "
                    "to rebuild with full dependency tracking. Do NOT "
                    "continue any C/C++ analysis until the user "
                    "confirms the index has been rebuilt."
                )
                index_message += (
                    f" — manifest verification: {manifest_verification} (run 'fw-context index' for full tracking)"
                )

        if header_affected_tus:
            # A TU that failed to parse keeps its needs_reparse mark, so it
            # counts here run after run.  Without the second half of this
            # sentence the user is told to run 'fw-context index', and the
            # index does not clear it.
            index_message += (
                f" | {header_affected_tus} TU(s) have stale header dependencies "
                "— header changes since last index, or a TU that failed to "
                "parse (see the index log)"
            )

        # Append LLM-analysis coverage, split by project/vendor, so the LLM
        # reads unanalyzed vendor symbols as expected (analyze_vendor=false),
        # not as a defect.
        index_message += _analysis_message(analysis, stored_analyze_vendor)

        # ── This process is older than the index it reads ──
        # No command repairs this, thus it is a warning and not a reindex
        # reason.  It goes in front of `index_message` because that string is
        # what a caller reads first, and a "fully up to date" message alone
        # would hide the one action that helps.
        client_restart_reason = ""
        if row_format_newer:
            client_restart_reason = (
                f"This session reads row format {CURRENT_ROW_FORMAT} and the index holds "
                f"{stored_row_format}. The index is the newer one and it is correct — do "
                f"NOT reindex, because the indexer writes {stored_row_format} again and "
                f"this message comes back. This server process runs older code. Tell the "
                f"operator to restart the LLM client (Claude Code, opencode, or the client "
                f"in use). The MCP server is a child process of that client, thus nobody "
                f"can restart the server alone."
            )
            index_message = (
                f"Restart the LLM client — this session reads row format "
                f"{CURRENT_ROW_FORMAT} and the index holds {stored_row_format}. "
                f"Queries work meanwhile. {index_message}"
            )

        result: dict = {
            "config_hash": config_hash,
            "project_id": project_id,
            "project_root": str(root),
            "build_system": proj_cfg.build.system or _detect_build_system(root),
            "compile_commands": cfg["compile_commands_path"],
            "indexed_at": cfg["created_at"],
            "symbol_count": sym_count,
            "file_count": file_count,
            "reference_count": ref_count,
            "modified_files_count": modified_count,
            "header_affected_tus": header_affected_tus,
            "schema_version": db_schema_ver,
            "current_schema": CURRENT_SCHEMA_VERSION,
            "analysis": analysis,
            "manifest_verification": manifest_verification,
            "description": cfg["description"] if "description" in cfg.keys() else "",
            "first_indexed_at": cfg["first_indexed_at"] if "first_indexed_at" in cfg.keys() else "",
            "vendor_paths": proj_cfg.index.vendor_paths,
            "project_paths": proj_cfg.index.project_paths,
            # What the INDEX RUN applied, which is not always what the config
            # says today: the builder derives part of it, a variant adds to
            # it, and the config can have changed since.  Three layers with no
            # way to read the result are worse than two.
            "effective_vendor_patterns": _effective_vendor_patterns(
                db_path.parent, config_hash, root
            ),
            "status": status,
            "reindex_needed": needs_reindex,
            "reindex_reasons": reindex_reasons,
            "stale": needs_reindex or header_affected_tus > 0,
            "index_message": index_message,
        }
        if client_restart_reason:
            # No leading underscore: this is an answer to act on, and not a
            # note about where an answer came from.
            result["client_restart_required"] = True
            result["client_restart_reason"] = client_restart_reason
        if _warning is not None:
            result["_warning"] = _warning

        # ── Multi-variant discovery (§5.6.A) ──
        # Tell the LLM what the project contains (parts = images) and for which
        # variants indexes exist — before it guesses names.  Sources: config
        # [[build.variants]] first, build_configs fallback.
        builds = get_all_builds_for_project(conn, project_id)
        discovery = _build_variant_discovery(proj_cfg, builds, root)
        result["multi"] = discovery["multi"]
        result["variants"] = discovery["variants"]
        result["images"] = discovery["images"]
        result["variant_images"] = discovery["variant_images"]
        result["active_variant"] = discovery["active_variant"]
        result["active_image"] = discovery["active_image"]

        if discovery["multi"]:
            # Aggregate health fields across builds (shared common/ code is
            # counted N times — sums are "total symbol records", not unique).
            result["symbol_count"] = sum(b["symbol_count"] for b in builds)
            result["file_count"] = sum(b["file_count"] for b in builds)
            result["reference_count"] = sum(b["reference_count"] for b in builds)
            # config_hash/compile_commands reflect the default build only; empty
            # without [build] default_variant (the LLM must pick a variant).
            default_build = None
            if discovery["active_variant"]:
                default_build = get_active_config(
                    conn, project_id, discovery["active_variant"], discovery["active_image"] or ""
                )
            result["config_hash"] = default_build["config_hash"] if default_build else ""
            result["compile_commands"] = (
                default_build["compile_commands_path"] if default_build else ""
            )

        # ── The memory map of the build these fields describe ──
        # AFTER the multi-variant block, because that block replaces
        # `config_hash` with the default build.  Read before it, a
        # multi-variant project would report the map of one build under the
        # identity of another — measured on the Zephyr project, where `app`
        # starts at flash 372736 and `mcuboot` at 110592.
        #
        # One build only.  `list_variants` reports every build's map, and
        # this tool has no variant parameter to ask for another.
        active_hash = result["config_hash"]
        if active_hash:
            result["entry_point"] = get_entry_point(conn, active_hash)
            result["memory"] = get_memory_regions(conn, active_hash)
            # The `-D` flags of the build that `config_hash` names, read
            # from ITS compile_commands.json rather than from the project's.
            # Computed here and not stored: the read costs 19 ms on
            # The Mbed project with 881 units, measured, so a column would buy
            # nothing and a stale one would mislead.
            defines, varying = command_line_defines(
                Path(result["compile_commands"])
                if result["compile_commands"] else Path()
            )
            result["defines"] = defines
            result["defines_varying"] = varying
        else:
            # A multi-variant project with no [build] default_variant: the
            # LLM has to pick a variant first, thus there is no one map to
            # report.  Empty rather than a guess at which build is meant.
            result["entry_point"] = ""
            result["memory"] = []
            result["defines"] = {}
            result["defines_varying"] = 0

        return result

    result = executor.execute_sync(_query, config_hash)
    if "error" in result:
        return result
    # Background reindex is managed by the startup daemon thread and the
    # file watcher — get_active_build() is a read-only tool and should
    # not spawn subprocesses.
    result["bg_reindex_running"] = bg_running
    if bg_running:
        result["reindex_progress"] = read_reindex_progress(db_path.parent)

    # sqlite-vec is an optional C extension for vector embeddings.
    # Report its availability so semantic_search can degrade gracefully.
    from ...deps import _vec_available
    vec_ok, vec_err = _vec_available()
    result["vec_available"] = vec_ok
    if vec_err is not None:
        result["vec_error"] = vec_err

    return result


# ── moved from server.py ──
def _analysis_message(analysis: dict, analyze_vendor: bool) -> str:
    """Return the LLM-analysis part of ``index_message``.

    Takes the ``analysis`` dict from ``make_analysis_summary`` and gives one
    line for a human reader.  The line always names the skipped symbols,
    because the pipeline cannot analyze them — the gap between ``analyzed``
    and ``total`` is not a defect.

    With ``analyze_vendor=False`` the line reports the project counts, and
    says that fw-context skipped the vendor symbols by design.  With
    ``analyze_vendor=True`` it reports both sides.

    Args:
        analysis: The ``analysis`` dict — {project, vendor} each with
            ``analyzed``, ``skipped``, and ``total``.
        analyze_vendor: The stored value, thus the message describes the
            build that fw-context indexed, not the current config.

    Returns:
        str: the message part, with a leading ``" | "`` separator.
    """
    p = analysis["project"]
    v = analysis["vendor"]
    if analyze_vendor:
        skipped_note = ""
        if p["skipped"] or v["skipped"]:
            skipped_note = (
                f" ({p['skipped']} project, {v['skipped']} vendor symbols skipped)"
            )
        return (
            f" | LLM analysis: project {p['analyzed']}/{p['total']}, "
            f"vendor {v['analyzed']}/{v['total']}{skipped_note}"
        )
    skipped_note = f", {p['skipped']} project symbols skipped" if p["skipped"] else ""
    return (
        f" | LLM analysis: project {p['analyzed']}/{p['total']}{skipped_note} "
        "(vendor skipped — analyze_vendor=false)"
    )


def _analysis_advice(
    analysis: dict, proj_cfg: Config, bg_running: bool, *, other_run: bool, restart: bool, stored_analyze_vendor: bool,
) -> str:
    """Return the advice for an LLM analysis that is not complete, or "".

    Decision of the user (2026-10-18): only advice.  The analysis is not a
    reason to reindex, the status stays as it is, and the daemon starts no
    run for it: the LLM can need hours, and that needs the approval of the
    user, as a pull of a model does (``auto_pull`` is false).  Before, the
    status gave ``complete: false`` and the counts, but no action, and
    nothing analyzed the symbols until another cause started an index run.

    The command is ``fw-context index``: each index run analyzes the
    symbols that wait, when the config asks for the analysis, and the
    analysis is content-addressed, thus an analyzed symbol does not go to
    the model again.  No advice when the config does not ask for the
    analysis: the user turned it off.  During an index run, the run
    analyzes the symbols, thus the advice names no command.

    *other_run*: a reason to reindex, or a degraded index, names its own
    run, and that run also does the analysis.  A bare ``fw-context index``
    next to it was not sufficient (the reason can need ``--build``).
    *restart*: a newer row format needs a restart of the client, and no
    run at all, thus no advice.

    The vendor symbols count with ``[llm] analyze_vendor`` of the config,
    as the run reads it, not with the value that the build stored.  When
    the two differ, the text says it: the coverage part before it describes
    the build as it was indexed ("vendor skipped").
    """
    waiting = _analysis_waiting(analysis, proj_cfg, restart=restart)
    if not waiting:
        return ""
    vendor_note = _analysis_vendor_note(proj_cfg, stored_analyze_vendor)
    return (
        f" — {waiting} symbol(s){vendor_note} wait for the analysis"
        + _analysis_action(bg_running, other_run=other_run, several=False)
    )


def _analysis_waiting(analysis: dict, proj_cfg: Config, *, restart: bool) -> int:
    """Return how many symbols of one build wait for the LLM analysis, for the advice.

    0 when no advice applies: the config does not ask for the analysis, or
    a newer row format needs a restart of the client.  The rules are the
    ones of :func:`_analysis_advice`.
    """
    if restart or not (proj_cfg.llm.enabled and proj_cfg.llm.analyze_symbols):
        return 0
    counts = [analysis["project"]]
    if proj_cfg.llm.analyze_vendor:
        counts.append(analysis["vendor"])
    return sum(max(0, c["total"] - c["analyzed"] - c["skipped"]) for c in counts)


def _analysis_vendor_note(proj_cfg: Config, stored_analyze_vendor: bool) -> str:
    """Return the note that the vendor symbols count now, or "" (see :func:`_analysis_advice`)."""
    if proj_cfg.llm.analyze_vendor and not stored_analyze_vendor:
        return " (with the vendor symbols: [llm] analyze_vendor is now true)"
    return ""


def _analysis_action(bg_running: bool, *, other_run: bool, several: bool) -> str:
    """Return the end of the analysis advice: the run that does the analysis.

    One text for a build and for a project of several builds, thus the two
    give the same action.  *several* selects the plural: the message of a
    project gives one number for each build.
    """
    numbers = "the numbers do not go down" if several else "the number does not go down"
    return _run_action(
        bg_running, other_run=other_run,
        plain=(
            ": run `fw-context index` with the approval of the user, because the LLM can need "
            f"hours. The analysis needs a running LLM: when {numbers} after the run, call check_ollama"
        ),
    )


def _run_action(bg_running: bool, *, other_run: bool, plain: str) -> str:
    """Return the end of an advice that an index run satisfies.

    During a run, or when another action of the message names a run, the
    text names that run: a second command next to it made the caller
    start a second run, or a run without ``--build``.  Else *plain*.
    """
    if bg_running:
        return ", and the index run in progress or scheduled does it"
    if other_run:
        return ", and the index run that the action above names does it"
    return plain


# The plain action of the embedding advice.  An index run embeds each
# definition that has no embedding (``_build_embeddings`` skips only an
# unchanged content hash).  When Ollama cannot serve the run, the run
# stamps the build "partial", and that warning names the repair.
_EMBEDDING_PLAIN_ACTION = ": run `fw-context index`"


def _embeddings_missing(conn, cfg, proj_cfg: Config, *, restart: bool) -> int:
    """Return how many definitions of one build have no embedding, for the advice.

    0 when no advice applies:

    * The config does not ask for embeddings (``[llm] enabled`` and
      ``[index] index_embeddings``): the user turned them off.
    * The build has no ``embedding_dim``: no embedding step wrote to it,
      thus each definition is without one, and the next run with
      embeddings does them all.
    * The stamp is "partial": its own warning names the repair.
    * A newer row format needs a restart of the client, and no run.

    WHY a count from the data and not a stamp: a re-parse of one file
    (``reindex_file`` with ``with_analysis=False``, or a failed model)
    leaves definitions without an embedding, and the stamp of the last
    index run still says "full".  The search by meaning then did not find
    them, and nothing told the user.
    """
    if restart or not (proj_cfg.llm.enabled and proj_cfg.index.index_embeddings):
        return 0
    if (cfg["manifest_verification"] or "none") == "partial":
        return 0
    if ("embedding_dim" not in cfg.keys()) or cfg["embedding_dim"] is None:
        return 0
    return len(symbols_without_embedding(conn, cfg["config_hash"]))


def _list_status(db_schema_ver: int, cc_stale: bool) -> str:
    """Map schema version and compile-commands staleness to a status label.

    Returns ``"reindex_needed"`` when either the DB schema is outdated
    or compile_commands.json has changed.  Returns ``"ready"`` otherwise.
    Used by ``list_projects`` to summarize each indexed project.
    """
    needs = (db_schema_ver < CURRENT_SCHEMA_VERSION) or cc_stale
    return "reindex_needed" if needs else "ready"


def list_projects(
    project_root: Annotated[
        str | None,
        Field(description="Project root. Auto-detected if omitted. Pass to distinguish multiple indexed projects."),
    ] = None,
) -> list[dict]:
    """List all indexed firmware projects with their statistics.

    Read-only. No side effects. Use at session start to discover available
    projects; use ``get_active_build`` for details on the currently active project.

    ``indexed_at`` and ``first_indexed_at`` are UTC, in ``"YYYY-MM-DD
    HH:MM:SS"`` format — the same format that ``get_active_build`` returns.

    ``analysis`` holds the ``project`` and ``vendor`` counts only.  For the
    ``model``, ``analyze_vendor``, and ``complete`` fields, call
    ``get_active_build`` for that project.

    Args:
        project_root: Project root. Auto-detected if omitted. Pass to
            distinguish multiple indexed projects.

    Returns:
        list of dicts, each with: project_id, name, root_path, build_system,
        symbol_count, file_count, indexed_at (str — UTC), description (str),
        first_indexed_at (str — UTC), schema_version, current_schema,
        reindex_needed (bool), status (str — "ready" or "reindex_needed"),
        db (path to SQLite database file),
        variant_count (int — number of build variants),
        image_count (int — number of sysbuild images),
        analysis (dict — LLM-analysis coverage
        {project: {analyzed, skipped, total},
        vendor: {analyzed, skipped, total}}, or None when no build is
        indexed).

        When no project has an index, the result is a single dict with an
        ``info`` key.  When fw-context cannot read a database, the result
        holds a dict with ``db`` and ``error`` keys for that file.
    """
    # resolve_project_root, not Path().resolve(): project_root can hold a
    # project name or a project_id, and only the shared resolver reads the
    # global registry that maps those to a root directory.
    cfg = load_config(project_root=resolve_project_root(project_root) if project_root else None)
    index_dir = cfg.index.db_dir
    # Glob for subdirectories containing index.db — each subdirectory
    # is named after a project_id UUID4.
    db_files = list(index_dir.glob("*/index.db")) if index_dir.exists() else []
    if not db_files:
        return [{"info": f"No indexed projects found under {index_dir}."}]
    results: list[dict] = []
    for db_path in sorted(db_files):
        try:
            executor = get_executor(db_path)

            def _query(conn, _config_hash):
                # Runs under the executor lock on the single shared
                # connection; must not open its own connection.
                # The fact only: the list does not read the state of a run,
                # and a command during the run that adds the columns would
                # take that run over.
                outdated = outdated_index_fact(conn)
                if outdated:
                    return f"{outdated}. To see the action, call get_project_status for that project."
                rows = get_all_projects(conn)
                schema_ver = get_db_schema_version(conn)
                counts = {}
                for row in conn.execute(
                    "SELECT project_id, "
                    "COUNT(DISTINCT CASE WHEN variant != '' THEN variant END) AS vc, "
                    "COUNT(DISTINCT CASE WHEN image != '' THEN image END) AS ic "
                    "FROM build_configs GROUP BY project_id"
                ).fetchall():
                    counts[row["project_id"]] = (row["vc"] or 0, row["ic"] or 0)
                coverage_map = {
                    row["config_hash"]: compute_analysis_coverage(conn, row["config_hash"])
                    for row in rows
                    if row["config_hash"]
                }
                return rows, schema_ver, counts, coverage_map

            rows, db_schema_ver, variant_counts, coverage_map = executor.execute_sync(_query, "")
            for r in rows:
                # Staleness check: compare stored creation time with
                # compile_commands.json mtime.  Does not require
                # _wrap_tool — list_projects is informational only.
                cc_stale = (
                    _is_stale(
                        {"created_at": r["created_at"]},
                        r["compile_commands_path"],
                    )[0]
                    if r["compile_commands_path"]
                    else False
                )
                root = Path(r["root_path"]) if r["root_path"] else None
                # Report the CONFIGURED build system first — projects without
                # markers (e.g. an NCS workspace) resolve system from config,
                # not from marker detection, so _detect_build_system would
                # wrongly report "unknown".
                try:
                    _pc = load_config(project_root=root) if root else None
                    _bs = (_pc.build.system if _pc else None) or (_detect_build_system(root) if root else "unknown")
                except Exception:
                    _bs = _detect_build_system(root) if root else "unknown"
                cov = coverage_map.get(r["config_hash"]) if r["config_hash"] else None
                results.append(
                    {
                        "project_id": r["project_id"],
                        "name": r["name"],
                        "root_path": r["root_path"],
                        "build_system": _bs,
                        "symbol_count": r["symbol_count"],
                        "file_count": r["file_count"],
                        "indexed_at": r["created_at"],
                        "description": r["description"] if "description" in r.keys() else "",
                        "first_indexed_at": r["first_indexed_at"] if "first_indexed_at" in r.keys() else "",
                         "schema_version": db_schema_ver,
                         "current_schema": CURRENT_SCHEMA_VERSION,
                         "reindex_needed": cc_stale or db_schema_ver < CURRENT_SCHEMA_VERSION,
                         "status": _list_status(db_schema_ver, cc_stale),
                         "db": str(db_path),
                         "variant_count": variant_counts.get(r["project_id"], (0, 0))[0],
                         "image_count": variant_counts.get(r["project_id"], (0, 0))[1],
                         "analysis": (
                             {"project": cov["project"], "vendor": cov["vendor"]}
                             if cov else None
                         ),
                    }
                )
        except (sqlite3.Error, OSError) as e:
            results.append({"db": str(db_path), "error": str(e)})

    # ── M6-only projects (v2 direct-materialization indexing, no legacy
    # index.db / no global registry) ──────────────────────────────────────
    # The M6 query layer keeps the stores it opened this session in
    # m6_query._m6_stores.  Such a project is indexed and queryable but
    # never appears in the registry-driven list above, so supplement it.
    existing_roots = {
        str(Path(r["root_path"]).resolve())
        for r in results if r.get("root_path")
    }
    try:
        from ...store_v2.m6_query import _m6_stores
        for m6_root, _store in list((_m6_stores or {}).items()):
            m6_root = str(m6_root)
            if m6_root in existing_roots:
                continue
            if not Path(m6_root).exists():
                continue
            try:
                from ...store_v2.m6_query import list_builds
                builds = list_builds(_store)
                results.append({
                    "project_id": _safe_project_id(Path(m6_root)),
                    "name": Path(m6_root).name,
                    "root_path": str(Path(m6_root)),
                    "build_system": _detect_build_system(Path(m6_root)),
                    "symbol_count": 0, "file_count": 0,
                    "indexed_at": "", "description": "M6-only index (no legacy DB)",
                    "first_indexed_at": "",
                    "schema_version": 0, "current_schema": CURRENT_SCHEMA_VERSION,
                    "reindex_needed": False, "status": "ready",
                    "db": str(Path(m6_root) / ".fw-context" / "index-m6.db"),
                    "variant_count": len(builds), "image_count": 0,
                    "analysis": None, "indexed_as": "m6",
                })
            except Exception:
                pass
    except Exception:
        pass
    return results


def _shows_damage(exc: sqlite3.DatabaseError) -> bool:
    """Tell if *exc* shows a damaged database, and not a lock or a fault of the disk.

    WHY: a lock, a full disk, an I/O error or a missing permission does
    not damage the database.  If the tool calls such a database damaged,
    the user can delete a good index.  Each other error of SQLite shows a
    database that the tool must let the user delete: damaged pages, and
    also a schema that open_db cannot repair.

    open_db wraps most errors of SQLite in `DatabaseCorruptionError`, and
    keeps the error of SQLite as the cause.  The other errors of open_db
    and of the reads come without a wrap.  A report of the integrity check
    has no cause: that is damage.  ``locked=True`` tells that open_db could
    not get the lock.  An error without a result code, for example a
    `sqlite3.ProgrammingError`, is a fault of the code, not damage.
    """
    error: sqlite3.Error = exc
    if isinstance(exc, DatabaseCorruptionError):
        if exc.locked:
            return False
        if not isinstance(exc.__cause__, sqlite3.Error):
            return True
        error = exc.__cause__
    return error_shows_damage(error)


# ── moved from server.py ──
def _safe_project_id(root) -> str:
    """Derive a project id without raising on an uninitialized v2-only tree.

    reset_index touches a project whose only index is the M6 store (no
    legacy init/config), where ``derive_project_id`` raises
    ``ProjectNotInitializedError``.  The id is only for display here, so a
    fallback to the root basename keeps the destructive-path report intact.
    """
    try:
        return derive_project_id(root)
    except Exception:
        return Path(root).name


def reset_index(
    project_root: Annotated[str | None, Field(description="Project root. Auto-detected if omitted.")] = None,
    confirm: Annotated[
        bool, Field(description="Must be True to execute. Call without confirm first as dry-run.")
    ] = False,
) -> dict:
    """Delete the entire symbol index for a project.

    Not read-only — permanently deletes the SQLite database and WAL files.
    Call with ``confirm=False`` first (dry-run) to see what would be deleted.
    Re-index with ``fw-context index`` afterwards.

    Handles corrupt databases gracefully — you can delete a corrupt index
    without needing to open it first.

    Args:
        project_root: Project root directory. Auto-detected if omitted.
        confirm: Must be True to execute. Call without first as dry-run.

    Returns:
        dict: {project_root, db, project_id, action: "dry_run"|"deleted",
        message, symbol_count, indexed_at (dry-run)}.

        A ``warning`` key means that the database is corrupt — the
        integrity check or a read of the database failed, thus the tool
        counts nothing and the three count fields are absent.

        On failure the dict holds only ``error`` with the reason.
    """
    root = resolve_project_root(project_root)
    # v2-only index: detect the M6 store WITHOUT calling _db_path, which
    # raises ProjectNotInitializedError on a tree that has no legacy
    # init/config.  The M6 store is a project-owned file, no config needed.
    m6_db_path = root / ".fw-context" / "index-m6.db"
    if m6_db_path.exists():
        targets = [m6_db_path]
        for suffix in ("-wal", "-shm"):
            p = root / ".fw-context" / (m6_db_path.stem + suffix)
            if p.exists():
                targets.append(p)
        if not confirm:
            return {
                "project_root": str(root),
                "db": str(m6_db_path),
                "project_id": _safe_project_id(root),
                "action": "dry_run",
                "message": "M6 store would be deleted (dry-run; pass confirm=True to execute).",
                "m6": True,
                "targets": [str(t) for t in targets],
            }
        for t in targets:
            try:
                t.unlink(missing_ok=True)
            except Exception as e:
                return {"error": f"Could not delete {t}: {e}", "m6": True}
        return {
            "project_root": str(root),
            "db": str(m6_db_path),
            "project_id": _safe_project_id(root),
            "action": "deleted",
            "message": f"M6 store deleted ({len(targets)} file(s)). Re-run `fw-context index`.",
            "m6": True,
            "deleted": [str(t) for t in targets],
        }
    db_path = _db_path(root)
    if not db_path.exists():
        return {"error": f"No index found for {root}. Nothing to reset."}
    project_id = derive_project_id(root)
    cfg_data = None
    sym_count = 0
    corrupt = False
    conn = None
    try:
        conn = open_db(db_path)
        with conn:
            cfg_data = get_active_config(conn, project_id)
            if cfg_data:
                sym_count = conn.execute(
                    "SELECT COUNT(*) FROM symbols WHERE config_hash=?",
                    (cfg_data["config_hash"],),
                ).fetchone()[0]
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    info: dict[str, object] = {
        "project_root": str(root),
        "db": str(db_path),
        "project_id": project_id,
    }
    if cfg_data:
        info["symbol_count"] = sym_count
        info["indexed_at"] = cfg_data["created_at"]
    elif corrupt:
        info["warning"] = "Database is corrupt — integrity check or read failed."
    if not confirm:
        info["action"] = "dry_run"
        if corrupt:
            info["message"] = (
                f"Database at {db_path} is corrupt. "
                "Call reset_index(confirm=True) to delete it anyway, "
                "then run 'fw-context index' to rebuild."
            )
        else:
            info["message"] = f"Would delete {db_path}. Call reset_index(confirm=True) to proceed."
        return info
    from ..background import bg_reindex_pause

    with bg_reindex_pause(root):
        # Mandatory ordering:
        # 1. The dry-run connection above is already closed (finally block).
        # 2. Invalidate the executor BEFORE deleting the DB file —
        #    otherwise it keeps a connection to an unlinked file and
        #    every subsequent query silently reads stale data forever.
        # 3. Only then delete the files.
        invalidate_executor(str(db_path.resolve()))
        db_path.unlink()
        # WAL and SHM files are sidecar files managed by SQLite in WAL
        # journal mode.  They must be deleted alongside the main DB file
        # to prevent a zombie WAL from being replayed into a fresh index.
        for suffix in ("-wal", "-shm", "-journal"):
            p = db_path.with_name(db_path.name + suffix)
            p.unlink(missing_ok=True)
        # Both autobuild markers describe the index that just went away: a
        # build that failed for a file list, and the files a build ran for
        # and did not cover.  Left behind they would suppress or delay work
        # on a fresh index that knows nothing about either.
        from ...indexer.autobuild import clear_excluded, clear_failure
        clear_failure(db_path.parent)
        clear_excluded(db_path.parent)
        from ...mcp.shared.stale import _invalidate_modified_cache
        _invalidate_modified_cache()  # clear all entries — DB is gone
        info["action"] = "deleted"
        info["message"] = f"Index deleted. Run 'fw-context index' in {root} to rebuild."
    return info


# ── moved from server.py ──
def _reindex_cleanup_deleted_file(
    conn: sqlite3.Connection,
    cfg_data: sqlite3.Row,
    target: Path,
    root: Path,
    db_path: Path,
) -> dict | None:
    """Remove index records for a file that no longer exists on disk.

    Called when ``reindex_file`` is invoked for a path that the user
    deleted after indexing.  Fetches the file's old ID from the index,
    counts deleted symbols for reporting, and purges all records
    (symbols, references, analysis) via ``purge_file_records``.

    Pauses background reindex to avoid a race: the bg thread might
    also detect the deletion and attempt its own cleanup.

    Returns None when this build does not know the file.  Each build of the
    project has its own rows, and a file of one build is not a file of the
    other builds; the caller decides from all builds together.
    """
    config_hash = cfg_data["config_hash"]
    from ...indexer.db import get_file_mtimes, purge_file_records
    from ...indexer.db._locking import WriteLockTimeout
    from ...indexer.ops import _normalize_file_path
    from ..background import bg_reindex_pause

    known = get_file_mtimes(conn, config_hash)
    file_path_str = _normalize_file_path(str(target), root)
    if file_path_str not in known:
        return None

    file_id_old, _ = known[file_path_str]
    symbol_count = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE file_id = ?", (file_id_old,)
    ).fetchone()[0]

    from ...mcp.shared.stale import _invalidate_modified_cache
    _invalidate_modified_cache(config_hash)

    with bg_reindex_pause(root):
        try:
            # write_lock_held=False, transaction_held=False — this caller
            # does NOT hold either; purge_file_records acquires its own.
            purge_file_records(
                conn, config_hash, file_id_old, file_path_str,
                db_dir=db_path.parent,
            )
            return {
                "file": str(target),
                "symbols_removed": symbol_count,
                "action": "deleted",
            }
        except WriteLockTimeout as e:
            return {"error": f"Could not acquire write lock for cleanup of {target}: {e}", "action": "timeout"}


def _reindex_parse_and_store(
    conn: sqlite3.Connection,
    matching: list,
    cfg_data: sqlite3.Row,
    cfg_refs: bool,
    root: Path,
    db_path: Path,
    target: Path,
    vendor_patterns: list[str],
    project_patterns_list: list[str],
) -> tuple[int, dict]:
    """Parse one or more translation units with libclang and store their symbols.

    Steps:
    1. Call ``extract_all`` for each TU — this invokes libclang to parse
       the file with the compiler flags from compile_commands.json.
    2. Within a single write-lock transaction, call ``store_symbols_for_unit``
       for each successfully parsed TU to upsert symbols into the database.
    3. Run macro resolution (best-effort — silently skipped on failure).
    4. Delete orphan file records (files that disappeared from the index).
    5. Update the manifest.json header-dependency tracking file.

    Returns ``(total_symbols, result_dict)``.  The result dict includes
    ``symbols_updated``, ``elapsed_s``, and optionally ``skipped_tus``.
    """
    from ...indexer.compile_commands import CompilationUnit as TU
    from ...indexer.context_identity import unit_identity_fields
    from ...indexer.db import get_file_hashes_by_context
    from ...indexer.db import write_lock as db_write_lock
    from ...indexer.ops import store_symbols_for_unit
    from ...indexer.symbols import (
        ExtractionResult,
        extract_all,
    )
    from ..background import bg_reindex_pause

    config_hash = cfg_data["config_hash"]

    # Staleness records per (path, context_id) — the same split the full
    # index run uses.  The path-keyed view would let the first context's
    # record falsely skip a second context's never-indexed command of the
    # same file.
    existing_by_context: dict[str, dict] = {}
    for (path, ctx), rec in get_file_hashes_by_context(conn, config_hash).items():
        existing_by_context.setdefault(ctx, {})[path] = rec

    # Phase 1: parse all matching TUs before acquiring the write lock.
    # Parsing is CPU-bound and does not touch the database — doing it
    # outside the lock minimises contention.
    parsed_units: list[tuple[TU, ExtractionResult]] = []
    skipped_tus: list[str] = []
    for unit in matching:
        try:
            parsed = extract_all(unit, with_refs=cfg_refs)
            parsed_units.append((unit, parsed))
        except sqlite3.Error as exc:
            return 0, {"error": f"DB error during parse of {unit.file.name}: {exc}"}
        except RuntimeError as exc:
            # Clang parse failures (missing headers, syntax errors) are
            # non-fatal — skip the TU and continue with others.
            log.warning("skip TU %s during reindex: %s", unit.file.name, exc)
            skipped_tus.append(str(unit.file.name))

    from ...mcp.shared.stale import _invalidate_modified_cache
    _invalidate_modified_cache(config_hash)

    with bg_reindex_pause(root):
        t0 = time.monotonic()
        total_symbols = 0

        try:
            # Phase 2: acquire the write lock and store all parsed symbols
            # in a single transaction per TU.  Each TU gets its own
            # transaction so partial failures don't roll back all work.
            with db_write_lock(db_path.parent, timeout=60.0):
                for unit, parsed in parsed_units:
                    # ADR-001 identity of THIS compile action.  Without it
                    # the rows landed under the canonical context_id='',
                    # a phantom that overwrote per-context rows and let
                    # the other context's stale rows survive.
                    ctx_id = unit_identity_fields(
                        unit, config_hash, root, entry_index=0
                    )["context_id"]
                    with transaction(conn):
                        syms_added, _, _headers = store_symbols_for_unit(
                            conn, unit, config_hash, root,
                            vendor_patterns=vendor_patterns,
                            project_patterns=project_patterns_list,
                            index_refs=cfg_refs,
                            pre_parsed=parsed,
                            existing_files=existing_by_context.get(ctx_id),
                            context_id=ctx_id,
                        )
                        total_symbols += syms_added

                # Phase 2.5 (M4): v2 certify/publish/bind for each re-parsed TU.
                # When v2 write-authority (or shadow) is on, publish the
                # updated SemanticState to v2 and bind the TU into the v2
                # World.  This closes M4-BUG-002 (reindex_file_impl bypassed
                # v2, leaving the v2 World stale and the M3B read authority
                # returning stale data).
                from ...indexer._unit_processor import _legacy_compat_on
                # M5: v2 certification always runs (default semantic engine).
                # In compat mode, legacy also runs alongside.
                if True:
                    try:
                        import importlib as _imp
                        _worlds_mod = _imp.import_module(
                            "fw_context_mcp.store_v2.worlds")
                        _cert_mod = _imp.import_module(
                            "fw_context_mcp.store_v2.certify")
                        _publish_mod = _imp.import_module(
                            "fw_context_mcp.store_v2.publish")
                        _v2_world_ctx = _worlds_mod.build_world_context(
                            project_root=root,
                            build_system=cfg_data.get("build_system")
                            if isinstance(cfg_data, dict)
                            else (cfg_data["build_system"]
                                  if "build_system" in cfg_data.keys()
                                  else None),
                            project_id=cfg_data.get("project_id")
                            if isinstance(cfg_data, dict)
                            else (cfg_data["project_id"]
                                  if "project_id" in cfg_data.keys()
                                  else None),
                        )
                        if _v2_world_ctx is not None:
                            for unit, parsed in parsed_units:
                                _ctx_id = unit_identity_fields(
                                    unit, config_hash, root, entry_index=0
                                )["context_id"]
                                _cert_mod.certify_and_publish_for_unit(
                                    unit=unit, parsed=parsed,
                                    project_root=root,
                                    config_hash=config_hash,
                                    context_id=_ctx_id,
                                    db_path=db_path, world=_v2_world_ctx)
                            # Finalize the v2 World for this reindex run.
                            _v2_path = db_path.parent.parent / \
                                db_path.parent.name / "index-v2.db"
                            _v2_store = _publish_mod.open_v2_store_for_write(
                                _v2_path)
                            try:
                                _fin = _worlds_mod.finalize_world(
                                    _v2_store, _v2_world_ctx)
                                _worlds_mod.record_run_metrics(
                                    _v2_store, _v2_world_ctx)
                                log.info(
                                    "v2 reindex world %s: %s "
                                    "(%d bound, %d poisoned, %d missing)",
                                    _v2_world_ctx.world_id,
                                    _fin.world_status, _fin.bound_count,
                                    _fin.poisoned_count, len(_fin.missing))
                            finally:
                                _v2_store.close()
                    except Exception as _v2_re_exc:
                        from ...indexer._unit_processor import \
                            _v2_write_authority_on
                        if _v2_write_authority_on():
                            raise
                        log.debug(
                            "v2 reindex leg failed (legacy unaffected): %s",
                            _v2_re_exc)

                # Phase 3: resolve macro definitions after all TUs are stored.
                # Macro resolution needs the clang flags from one TU (they
                # are identical for TUs of the same file) and runs a
                # best-effort pass — failure is silently ignored.
                if parsed_units:
                    try:
                        from ...indexer.macros import resolve_and_update
                        first_unit = parsed_units[0][0]
                        resolve_and_update(conn, config_hash, first_unit.clang_args, first_unit.file.resolve(), cwd=root)
                    except Exception:  # nosec B110 — macro expansion is best-effort
                        pass

                # Phase 4: clean up orphan files — files that existed in
                # the previous index but are now absent from the TU list.
                from ...indexer.db import delete_orphan_files
                deleted_orphans = delete_orphan_files(conn, config_hash, root)
                if deleted_orphans:
                    log.debug("Orphan files cleaned up: %d", deleted_orphans)

                _update_manifest_after_reindex(parsed_units, root, db_path.parent, config_hash)

                elapsed = round(time.monotonic() - t0, 2)
                result = {
                    "file": str(target),
                    "translation_units": len(matching),
                    "symbols_updated": total_symbols,
                    "elapsed_s": elapsed,
                }
                if skipped_tus:
                    result["skipped_tus"] = skipped_tus
                    result["skipped_count"] = len(skipped_tus)
                if not parsed_units and skipped_tus:
                    return 0, {"error": f"All {len(matching)} translation unit(s) failed to parse", "skipped_tus": skipped_tus}
            return total_symbols, result
        except (sqlite3.Error, WriteLockTimeout) as exc:
            return 0, {"error": f"DB error during reindex: {exc}"}

def _update_manifest_after_reindex(
    parsed_units: list,
    root: Path,
    db_dir: Path,
    config_hash: str,
) -> None:
    """Update manifest.json entries for reindexed translation units.

    The manifest tracks each TU's source hash and its set of included
    headers.  After re-parsing a file, the manifest entry must be
    refreshed so header-staleness detection (used by ``get_active_build``)
    reflects the file's current dependencies.

    When the manifest does not exist (first index, or manifest not
    enabled), this is a no-op — the ``load_manifest`` guard returns
    early.
    """
    try:
        from fw_context_mcp.utils import compute_source_hash

        from ...indexer.manifest import (
            _collect_headers_from_tokens,
            _intern_arguments,
            header_is_trusted,
            mark_entries_behind,
            merge_header_records,
            update_entry,
        )
        from ...indexer.manifest import (
            load as load_manifest,
        )
        from ...indexer.manifest import (
            save as save_manifest,
        )
        manifest_data = load_manifest(db_dir, config_hash)
        if manifest_data is not None:
            # The patterns the INDEX RUN used, read from the manifest it
            # wrote.  With None here _is_generated_header() answers False for
            # every header, so every record this function writes claimed
            # "generated": False.  After the end of vendor trust `generated`
            # is the only trust rule left, so that turned every build of a
            # re-indexed file into a full reparse.  Measured on the Zephyr project
            # variant nrf52840-dev: 27 generated headers dropped to 0.
            bdp = manifest_data.get("build_dir_patterns")
            if not bdp:
                bdp, _ = _reindex_build_patterns_for_generated(root)
            # Snapshot the shared header table BEFORE the loop.  A per-unit
            # difference would make the second unit read what the first one
            # just wrote as somebody else's change.
            table_before = {
                path: record.get("hash", "")
                for path, record in (manifest_data.get("headers") or {}).items()
            }
            reparsed_rel: set[str] = set()
            from ...indexer.context_identity import unit_identity_fields

            for unit, _parsed in parsed_units:
                # Records go into a per-unit table and are merged by
                # merge_header_records, so a header this re-parse is the first
                # to see still contributes its hash to the manifest's shared
                # map — without a downgrade of `generated` on the way.
                header_records: dict[str, dict] = {}
                headers = _collect_headers_from_tokens(
                    unit, root, bdp, header_records
                )
                source_hash = compute_source_hash(unit.file.resolve())
                try:
                    tu_rel = unit.file.resolve().relative_to(root).as_posix()
                except ValueError:
                    tu_rel = unit.file.resolve().as_posix()
                reparsed_rel.add(tu_rel)
                # EVERY entry of this file, not the first one: a file
                # compiled with two flag sets has two entries, and the
                # first-match break left the second context at its stale
                # hashes — the staleness check then re-parsed it on every
                # run while reindex_file claimed the manifest current.
                unit_ctx = unit_identity_fields(
                    unit, config_hash, root, entry_index=0
                )["context_id"]
                matched = False
                for idx, entry in enumerate(manifest_data.get("entries", [])):
                    if entry.get("file") != tu_rel:
                        continue
                    # Stamp the ADR-001 identity on entries that predate
                    # per-context manifests; a per-context entry keeps its
                    # own id, so the re-parsed context and its entry stay
                    # bound together.
                    entry.setdefault("context_id", unit_ctx)
                    if entry.get("context_id") == unit_ctx or not matched:
                        update_entry(
                            manifest_data, idx, source_hash, headers, header_records
                        )
                        matched = True
                if not matched:
                    # Same merge as update_entry uses.  This branch bypasses
                    # update_entry completely, so a plain .update() here is a
                    # second place where a False overwrites a True.
                    merge_header_records(manifest_data, header_records)
                    manifest_data.setdefault("entries", []).append({
                        "file": tu_rel,
                        "directory": str(unit.directory) if unit.directory else str(root),
                        "arg_set": _intern_arguments(
                            unit.clang_args,
                            manifest_data.setdefault("arg_sets", []),
                        ),
                        "source_hash": source_hash,
                        "headers": headers,
                        "context_id": unit_ctx,
                    })
            # This function refreshed the SHARED header hashes, so the next
            # full index run sees no difference for the OTHER units that
            # include those headers — their rows still come from the old
            # text.  Flag them, or reindex_file hides the change from every
            # later run.
            changed = {
                path
                for path, record in (manifest_data.get("headers") or {}).items()
                if not header_is_trusted(record)
                and record.get("hash", "") != table_before.get(path, "")
            }
            behind = mark_entries_behind(manifest_data, changed, reparsed_rel)
            if behind:
                log.info(
                    "reindex_file: %d entr(ies) flagged needs_reparse after "
                    "%d refreshed header(s)", behind, len(changed),
                )
            save_manifest(manifest_data, db_dir, config_hash)
    except OSError:
        log.debug("manifest.json update skipped during reindex_file", exc_info=True)


def _transport_error_types() -> tuple:
    """httpx transport-error types for the enrichment guards (NEW-W1-2).

    httpx exceptions are NOT in ``SAFE_EXCEPT`` (utils.py) and NOT in the
    guards' ``(sqlite3.Error, RuntimeError, OSError)``: the W1-2 diagnosis
    measured a WSL host whose corporate proxy env hijacks the loopback LLM
    probe (NO_PROXY without localhost) — ``httpx.ReadTimeout`` escaped
    ``_reindex_llm_analysis`` and aborted ``reindex_file_impl``,
    contradicting both phases' documented non-fatal contract ("the result
    dict gets an analysis_warning key instead of a hard error").  Both
    guards degrade the whole ``httpx.HTTPError`` family to their warning.

    Returns ``()`` when httpx is unavailable — the guards keep their
    previous shape (an empty tuple tail in an ``except`` is legal).
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a hard dependency
        return ()
    return (httpx.HTTPError,)


def _reindex_llm_analysis(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg: Config,
    db_dir: Path,
    result: dict,
    *,
    project_root: Path,
) -> None:
    """Regenerate LLM symbol analysis for symbols affected by a reindex.

    Only runs when both ``cfg.llm.enabled`` and ``cfg.llm.analyze_symbols``
    are True.  Reads the stored ``analyze_vendor`` flag from the build
    config to determine whether vendor symbols should be re-analysed.

    Uses ``CacheClient`` to avoid redundant LLM calls for symbols whose
    source has not changed.  Failures are non-fatal — the result dict
    gets an ``analysis_warning`` key instead of a hard error.
    """
    if not (cfg.llm.enabled and cfg.llm.analyze_symbols):
        return
    try:
        from ...cache_client import CacheClient
        from ...indexer._llm_analysis import _build_llm_analysis
        try:
            stored_av = bool(
                conn.execute(
                    "SELECT analyze_vendor FROM build_configs WHERE config_hash = ?",
                    (config_hash,),
                ).fetchone()["analyze_vendor"]
            )
        except sqlite3.OperationalError:
            stored_av = False
        cc = CacheClient.from_config(cfg)
        try:
            _build_llm_analysis(
                conn, config_hash, cfg.llm, db_dir,
                write_lock_held=False, retry_unparseable=True,
                cache_client=cc, project_only=not stored_av,
                project_root=project_root,
            )
            conn.commit()
        finally:
            if cc:
                cc.close()
        analyzed_count = conn.execute(
            "SELECT COUNT(*) FROM llm_analysis a JOIN symbols s ON s.id = a.symbol_id WHERE s.config_hash = ?",
            (config_hash,),
        ).fetchone()[0]
        result["analysis_updated"] = analyzed_count
    except (sqlite3.Error, RuntimeError, OSError, *_transport_error_types()) as exc:
        result["analysis_warning"] = f"LLM analysis skipped: {exc}"


def _reindex_overrides(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg: Config,
    db_dir: Path,
    result: dict,
) -> None:
    """Rebuild virtual method override relationships after reindex.

    Only runs when ``cfg.index.index_refs`` is True (reference indexing
    must be enabled).  The override table maps virtual methods to their
    overrides in derived classes and is used by ``get_method_overrides``.

    Failures are non-fatal — an ``overrides_warning`` key is added to
    the result dict.
    """
    if not cfg.index.index_refs:
        return
    try:
        from ...indexer.runner import _build_overrides
        _build_overrides(conn, config_hash, db_dir, write_lock_held=False, force=True)
        conn.commit()
    except (sqlite3.Error, RuntimeError) as exc:
        result["overrides_warning"] = f"Override analysis skipped: {exc}"


def _reindex_pagerank(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg: Config,
    result: dict,
) -> None:
    """Rebuild PageRank scores and the hotspot cache after reindex.

    PageRank runs on the call graph to rank functions by architectural
    importance.  The hotspot cache pre-computes the top-N most-called
    functions for ``find_hotspots``.  Both depend on the reference index
    (``cfg.index.index_refs`` must be True).

    Failures are non-fatal.
    """
    if not cfg.index.index_refs:
        return
    try:
        from ...indexer.runner import _build_hotspot_cache, _build_pagerank
        _build_pagerank(conn, config_hash, write_lock_held=False, force=True)
        _build_hotspot_cache(conn, config_hash, force=True)
        conn.commit()
    except (sqlite3.Error, RuntimeError) as exc:
        result["pagerank_warning"] = f"PageRank/hotspot recompute skipped: {exc}"


def _reindex_embeddings(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg: Config,
    db_dir: Path,
    target: Path,
    root: Path,
    result: dict,
    missing_before: frozenset[int],
) -> None:
    """Regenerate vector embeddings for the symbols that the re-parse inserted.

    Only runs when ``cfg.llm.enabled`` is True (the embedding model is
    configured through the LLM subsystem).  Calls ``_build_embeddings``
    with a set of IDs, to avoid recomputing embeddings for the entire
    index.  The set holds two parts:

    * The symbols of the file path.  An edit can change their text, and
      ``_build_embeddings`` embeds a symbol again when its content hash
      changed.
    * Each definition that lost its embedding in this re-parse: it has
      none now, and it is not in *missing_before*.  WHY: the re-parse also
      deletes the symbols of the headers that the unit includes, for
      example a ``static inline`` function, and inserts them again with
      new IDs.  ``ON DELETE CASCADE`` removed their embeddings, and a set
      of the file path alone left them without one.

    WHY not each definition without an embedding: a build can have an
    older gap of thousands of definitions, for example after a run that
    could not reach Ollama, or with ``index_embeddings = false``.  One call
    of this tool then embedded the whole build, which can take tens of
    minutes.  ``fw-context index`` closes an older gap, and the status
    tools report it.  WHY a set of gaps and not a set of IDs: SQLite can
    give a new symbol the ID of a deleted one (no AUTOINCREMENT), thus an
    ID that existed before can belong to a new symbol.

    When no symbols are found for the file path (possible for symlink
    or out-of-root files), the warning is logged but no error is raised.
    """
    if not cfg.llm.enabled:
        return
    from ...indexer.ops import _normalize_file_path
    try:
        from ...indexer.runner import _build_embeddings as _reembed
        file_symbol_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM symbols WHERE config_hash = ? AND file_path = ?",
                (config_hash, _normalize_file_path(str(target), root)),
            ).fetchall()
        ]
        if not file_symbol_ids:
            log.warning(
                "No symbols found for path %s in DB — skipping embedding regeneration "
                "of its symbols (this may happen for files outside the project root "
                "or after a symlink change)",
                target,
            )
        new_gaps = set(symbols_without_embedding(conn, config_hash)) - missing_before
        symbol_ids = sorted(set(file_symbol_ids) | new_gaps)
        if symbol_ids:
            _reembed(conn, config_hash, cfg.llm, db_dir, symbol_ids=symbol_ids)
        conn.commit()
        reembedded = conn.execute(
            "SELECT COUNT(DISTINCT symbol_id) FROM embeddings e "
            "JOIN symbols s ON s.id = e.symbol_id WHERE s.config_hash = ?",
            (config_hash,),
        ).fetchone()[0]
        result["embeddings_updated"] = reembedded
    except (sqlite3.Error, RuntimeError, OSError, *_transport_error_types()) as exc:
        result["embedding_warning"] = f"Embedding regeneration skipped: {exc}"


def _reindex_post_write_phases(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg: Config,
    total_symbols: int,
    db_dir: Path,
    target: Path,
    result: dict,
    root: Path,
    *,
    missing_before: frozenset[int],
) -> dict:
    """Run the model phases after reindex_file, when ``with_analysis`` is True.

    *missing_before* holds the definitions of the build that had no
    embedding before the re-parse (see :func:`_reindex_embeddings`).

    Each phase is independent — failure in one does not prevent others
    from running.  The phases are:

    1. LLM symbol analysis (function descriptions)
    2. Vector embeddings for semantic search — after the analysis, because
       the text of an embedding holds the summary of the analysis.

    The two need a model and can take minutes, thus ``with_analysis=False``
    skips them.  The status tools report the symbols that they left
    without an analysis or an embedding.  The graph phases do not need a
    model: :func:`_reindex_graph_phases` runs them in the two modes.

    When ``total_symbols <= 0``, all phases are skipped (nothing was
    added or updated).

    The coverage warning for a header is NOT set here.  It used to be, and
    that put it behind two gates it has nothing to do with: this function
    returns early when ``total_symbols <= 0``, and the caller skips it
    entirely when ``with_analysis=False``, the fast symbol-only update.  A
    caller that re-parsed a header through one unit
    has to hear about it whether or not the enrichment phases ran, thus
    _header_coverage_warning() is applied by the caller.
    """
    if total_symbols <= 0:
        return result

    _reindex_llm_analysis(conn, config_hash, cfg, db_dir, result, project_root=root)
    _reindex_embeddings(conn, config_hash, cfg, db_dir, target, root, result, missing_before)
    return result


def _reindex_graph_phases(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg: Config,
    total_symbols: int,
    db_dir: Path,
    result: dict,
) -> None:
    """Rebuild the virtual method overrides, the PageRank scores and the hotspot cache after reindex_file.

    Runs also with ``with_analysis=False``.  WHY: the re-parse deletes the
    symbols of the translation unit and its headers and inserts them again
    with new IDs.  ``ON DELETE CASCADE`` removes their rows of the hotspot
    cache, and the new rows start with PageRank 0.  Measured on a project
    of 7 builds: after a fast re-parse ``memset``, with 684 references,
    was not in ``find_hotspots``, and nothing reported it.  No status can
    see a missing score, because many symbols have PageRank 0 in a
    complete index.  The phases need no model, and they are fast: at most
    2 s for PageRank and the hotspot cache and 0.1 s for the overrides on
    an index of 60 000 symbols (``indexer/_postprocess.py``).

    Each phase is independent — a failure in one adds its warning to
    *result* and does not stop the other.  Skipped when
    ``total_symbols <= 0`` (nothing was added or updated).
    """
    if total_symbols <= 0:
        return
    _reindex_overrides(conn, config_hash, cfg, db_dir, result)
    _reindex_pagerank(conn, config_hash, cfg, result)


def _header_coverage_warning(
    target: Path,
    matching: list,
    via_header: bool,
    manifest: dict | None = None,
    project_root: Path | None = None,
    config_hash: str = "",
) -> str | None:
    """Say how much of a header the re-index really covered.

    Another unit can include the same header under a different set of
    ``#define`` values and still hold symbols from before the edit.  With a
    per-context manifest the coverage is countable: the manifest's entries
    that include *target* are the contexts that need refreshing, and the
    re-parsed units — identified by the same ADR-001 identity the index
    used — are what actually got refreshed.  A partial result warns with
    the real numbers; a full result stays silent, because a warning on a
    complete re-index is noise that trains callers to ignore the field.

    Without a manifest the fallback stays one-unit conservative, and the
    suffix test covers a header that compile_commands.json does list, which
    some build systems emit.  The suffix test alone is not enough: it names
    ``.h`` and ``.hpp``, while an ``#include`` reaches ``.hh``, ``.hxx``,
    ``.inc`` and extension-less headers just as well, and the fallback
    resolves those too.
    """
    if manifest is not None and project_root is not None and config_hash:
        from ...indexer.context_identity import unit_identity_fields

        root = project_root.resolve()
        target_str = str(target)
        reprinted: set[tuple[str, str]] = set()
        for unit in matching:
            try:
                tu_rel = Path(unit.file).resolve().relative_to(root).as_posix()
            except ValueError:
                tu_rel = Path(unit.file).resolve().as_posix()
            ctx = unit_identity_fields(unit, config_hash, root, entry_index=0)[
                "context_id"
            ]
            reprinted.add((tu_rel, ctx))
        # Count coverage at (file, context) granularity; legacy entries
        # without a context id fall back to file-level matching.
        total = 0
        refreshed = 0
        for entry in manifest.get("entries") or ():
            if not entry.get("file"):
                continue
            includes_target = any(
                (header if Path(header).is_absolute() else str(root / header)) == target_str
                for header in entry.get("headers") or ()
            )
            if not includes_target:
                continue
            total += 1
            entry_ctx = entry.get("context_id")
            if entry_ctx is not None and (entry.get("file"), entry_ctx) in reprinted:
                refreshed += 1
            elif entry_ctx is None and entry.get("file") in {
                rel for rel, _ in reprinted
            }:
                refreshed += 1
        if total and refreshed < total:
            return (
                f"Header re-indexed: refreshed {refreshed} of {total} includer "
                "contexts — run 'fw-context index' for full accuracy."
            )
        if total:
            return None
    if not (via_header or (len(matching) == 1 and target.suffix in HEADER_EXTENSIONS)):
        return None
    return (
        "Header re-indexed via one TU. Other TUs including this header "
        "may still have stale symbols — run 'fw-context index' for full accuracy."
    )


def _reindex_m6_direct(
    file_path: str,
    root: Path,
    m6_db_path: Path,
) -> dict:
    """M7-FINAL-ECONOMIC-ADDENDUM (§6): pure-M6 reindex_file path.

    Used when the legacy index.db does NOT exist (pure M6 mode, the
    default) but the M6 store (index-m6.db) does.  Parses the file with
    libclang, materializes canonical facts DIRECTLY into the M6 store,
    and returns.  This makes reindex_file a NORMAL M6-direct write
    entrypoint (§7: NORMAL_WRITE_ENTRYPOINTS == M6_DIRECT_WRITE_ENTRYPOINTS).

    Flow (§6):
        source/file change
        → production reindex_file
        → M6 direct semantic extraction/materialization
        → correct World/Build update
        → exact production query results
    """
    import time as _time_mod
    from ...indexer.compile_commands import CompilationUnit as TU
    from ...indexer.symbols import extract_all
    from ...store_v2.m6_query import open_m6_store
    from ...store_v2.m6_direct_materialization import (
        materialize_unit_into_m6,
    )

    t0 = _time_mod.monotonic()

    # Resolve the target file.
    target = Path(file_path)
    if not target.is_absolute():
        target = (root / target).resolve()
    else:
        target = target.resolve()
    if not target.exists():
        return {"error": f"File not found: {target}"}

    # Load compile_commands.json to find the matching TU.
    cc_path = root / "compile_commands.json"
    if not cc_path.exists():
        return {"error": f"No compile_commands.json in {root}"}

    # Use the production compile_commands parser — it normalizes args,
    # detects language, and handles compiler-specific include paths.
    from ...indexer.compile_commands import parse as parse_compile_commands
    all_units = list(parse_compile_commands(cc_path))

    # Find matching TUs (source file direct match).
    matching = []
    for unit in all_units:
        try:
            if unit.file.resolve() == target.resolve():
                matching.append(unit)
        except Exception:
            continue

    # If no direct match, try header-via-TU (use first TU as candidate).
    if not matching and all_units:
        matching = [all_units[0]]

    if not matching:
        return {"error": f"No matching TU for {target} in compile_commands.json"}

    # Open the M6 store.
    store = open_m6_store(root)

    # Derive build_name and world_id from the existing M6 store.
    # Use the first registered build (or "default").
    try:
        builds = store.list_builds()
    except Exception:
        builds = []
    build_name = builds[0] if builds else "default"

    # Derive world_id deterministically (same as _unit_processor fallback).
    import hashlib as _hash_mod
    world_id = (
        int(_hash_mod.sha256(
            build_name.encode()
        ).hexdigest()[:8], 16) % 1000000
    ) + 1
    store.register_world(world_id, {build_name})

    # Derive config_hash from the first M6 fact (or use empty).
    config_hash = ""
    try:
        proj = store.project_build(build_name)
        for kind, facts in proj.items():
            for f in facts:
                p = f.get("payload", {}) or {}
                if p.get("config_hash"):
                    config_hash = p["config_hash"]
                    break
            if config_hash:
                break
    except Exception:
        pass

    # Parse each matching TU and materialize into M6.
    symbols_updated = 0
    skipped_tus = []
    for unit in matching:
        try:
            parsed = extract_all(unit, with_refs=True)
        except RuntimeError as exc:
            skipped_tus.append(str(unit.file.name))
            continue

        stats = materialize_unit_into_m6(
            store=store, parsed=parsed, project_root=root,
            build_name=build_name, world_id=world_id,
            config_hash=config_hash, context_id="",
        )
        symbols_updated += stats.get("facts_indexed", 0)

    elapsed = _time_mod.monotonic() - t0
    return {
        "file": str(target),
        "translation_units": len(matching),
        "symbols_updated": symbols_updated,
        "elapsed_s": round(elapsed, 3),
        "m6_direct_materialization": True,
        "skipped_tus": skipped_tus if skipped_tus else None,
    }


REINDEX_VARIANT_DESCRIPTION = (
    "Variant of the one build to re-parse. Omit it and image to re-parse the file in "
    "each build that compiles it. A part of a pair gets an error that lists the builds."
)
REINDEX_IMAGE_DESCRIPTION = (
    "Image of the one build to re-parse: a separate program, such as the application or "
    "the bootloader. Give it when that build has an image name."
)


# ── moved from server.py ──
def reindex_file(
    file_path: Annotated[
        str,
        Field(
            description="Absolute or project-relative path to the file to re-parse. A source file must have an entry in compile_commands.json. A header is re-parsed through one translation unit that includes it, and the result then carries a warning that other units can still hold stale symbols."
        ),
    ],
    project_root: Annotated[
        str | None, Field(description="Project root directory. Auto-detected from cwd if omitted.")
    ] = None,
    variant: Annotated[str | None, Field(description=REINDEX_VARIANT_DESCRIPTION)] = None,
    image: Annotated[str | None, Field(description=REINDEX_IMAGE_DESCRIPTION)] = None,
    *,
    with_analysis: Annotated[
        bool,
        Field(
            description="When True (default), also regenerates the LLM symbol analysis and the embeddings — slower, because the two need a model. Set False for a fast update: the tool skips the two, and get_project_status then reports the symbols that wait for them. The method overrides, the PageRank scores and the hotspot cache are rebuilt in the two modes when reference indexing is on (`[index] index_refs`)."
        ),
    ] = True,
) -> dict:
    """Re-parse a single source file with libclang and update its symbols in the index.

    Not read-only — uses the exact compiler flags from ``compile_commands.json``.
    Use after editing a file to keep the index current without a full rebuild.

    Without ``variant`` and ``image`` the file is re-parsed in EACH build
    of the project that compiles it: an edit on disk changes the file for
    every build of the tree.  A build that does not compile the file is
    named in ``builds_without_file``.  With the two arguments of one build,
    as ``get_project_status`` lists them, only that build is re-parsed; a
    part of a pair gets an error that lists the builds.

    The call runs in a thread of the server and sends a notice each 5 s.
    It holds the lock of the query tools: another query waits 5 s, then
    gets "server busy".  After 300 s the call returns an ``error``, and a
    part of the work can continue or fail.  Call ``get_project_status``
    for the state of each build, and repeat the call for a build that is
    not current.  The answer holds no ``index_state``.

    A source file must be listed in ``compile_commands.json``.  A header is
    not listed there — compile_commands.json names translation units — so it
    is re-parsed through one unit that includes it, taken from the manifest
    of each build.  That answer describes a single compilation context,
    thus the result carries a ``warning``: another unit can see the header
    under a different set of ``#define`` values and still hold stale
    symbols.  Only a full ``fw-context index`` covers every context.  One
    unit and not all of them is a cost decision — an application header
    reaches a median of 3 units but as many as 266 on a real project, at
    tens of seconds each.

    Also rebuilds the method override relationships, the PageRank scores
    and the hotspot cache when reference indexing is on
    (``[index] index_refs``), with and without ``with_analysis``.  When
    ``with_analysis=True`` and the LLM is on (``[llm] enabled``), it also
    regenerates the LLM analysis and the embeddings of the affected
    symbols.  The analysis also needs ``[llm] analyze_symbols``.  It is
    content-addressed, thus an unchanged symbol is never re-analysed.

    Args:
        file_path: Path to the file to re-parse.  A source file must be in
            compile_commands.json; a header goes through one including unit.
        project_root: Project root directory. Auto-detected if omitted.
        variant: The variant of the one build to re-parse.  Omit it, and
            ``image``, to re-parse each build that compiles the file.
        image: The image of the one build to re-parse.
        with_analysis: When True (default), also regenerates the LLM symbol
            analysis and the embeddings, which need a model. Set False for a
            fast update: the status tools then report the symbols that wait
            for the two.

    A file that is gone from disk but still in the index has its records
    removed in each build that holds it; the result is then {file,
    symbols_removed, action: "deleted", builds}.

    Returns:
        dict: {file, translation_units, symbols_updated, elapsed_s, builds},
        plus builds_without_file when a build does not compile the file.
        ``builds`` has one entry for each build that compiles the file:
        {variant, image, translation_units, symbols_updated, elapsed_s},
        plus when they apply: skipped_tus and skipped_count (units that
        failed to parse), warning (header coverage), analysis_updated and
        embeddings_updated (counts for the whole build, not this file), and
        analysis_warning, overrides_warning, pagerank_warning, or
        embedding_warning when a phase failed, or ``error`` when the
        re-parse of that build failed.

        When one build compiles the file, the top level holds the full
        result of that build.  When several do, the top level holds the
        sums of the builds that did not fail, ``elapsed_s`` of the whole
        call, the sum of ``skipped_count``, ``warning`` only when each of
        those builds has it, and ``failed_builds`` with the label of each
        build whose entry holds ``error``.

        On failure the dict holds ``error`` with the reason: a refusal of
        the selector, no build that compiles the file, or an update that
        failed in each build.  It can add ``skipped_tus`` or ``action``.
    """
    db_path, cfg, project_id, root = _resolve_context(project_root, skip_ready_check=True)

    # M7-FINAL-ECONOMIC-ADDENDUM (§6): M6 direct materialization for
    # reindex_file.  In pure M6 mode (legacy compat OFF, the default),
    # the legacy index.db is NOT created by runner.run(), so the legacy
    # DB check below would fail.  When M6 write authority is on (default),
    # fall through to the M6-direct path: open the M6 store, parse the
    # file, materialize canonical facts directly from the libclang
    # ExtractionResult, and return.  This makes reindex_file a NORMAL
    # M6-direct write entrypoint (§7: NORMAL_WRITE_ENTRYPOINTS ==
    # M6_DIRECT_WRITE_ENTRYPOINTS).
    import os as _os_mod
    _m6_write_auth = _os_mod.environ.get(
        "FW_CONTEXT_M6_WRITE_AUTHORITY", "1"
    ).strip() in ("1", "true", "yes", "on")

    if not db_path.exists():
        # Legacy DB doesn't exist.  In pure M6 mode, try the M6-direct
        # path: if the M6 store exists, reindex into it directly.
        if _m6_write_auth:
            m6_db_path = root / ".fw-context" / "index-m6.db"
            if m6_db_path.exists():
                return _reindex_m6_direct(
                    file_path, root, m6_db_path,
                )
        return {"error": f"No index found for {root}. Run 'fw-context index' first."}
    target, error = _reindex_resolve_target(file_path, root)
    if error:
        return error

    builds, refusal = _reindex_builds(db_path, project_id, cfg, variant or "", image or "", root)
    if refusal:
        return {"error": refusal}
    if not builds:
        return {"error": "No build config indexed."}

    executor = get_executor(db_path)

    def _query(conn, config_hash):
        # WRITE path: the executor is not read-only — writes are
        # serialized with reads by the same lock.  A collision with a
        # concurrently running CLI indexer is absorbed by the executor's
        # 120 s busy_timeout (the CLI indexer keeps its own 10 s
        # fail-fast).  Timeout is enforced by _wrap_tool, not here.
        target, error = _reindex_resolve_target(file_path, root)
        if error:
            return error
        if not target.exists():
            return _reindex_cleanup_deleted_file(conn, cfg_data, target, root, db_path)

        # The manifest of THIS build carries the vendor set the index run
        # applied.  reindex_file must write is_project with that same
        # boundary, or one file ends up on the other side of it.
        #
        # Loaded before the match because it answers a second question:
        # compile_commands.json lists no headers, so a header target finds
        # its translation unit only through the manifest.  One load serves
        # both — it costs 14 ms on a 9.3 MB manifest.
        from ...indexer.manifest import load as load_manifest

        build_manifest = load_manifest(db_path.parent, config_hash)

        matching, via_header, error = _reindex_match_tus(
            target, cfg_data, manifest=build_manifest, project_root=root,
        )
        if error:
            return error

        vendor_patterns, project_patterns_list = _reindex_build_patterns(
            cfg, root, build_manifest
        )
        total_symbols, result = _reindex_parse_and_store(
            conn, matching, cfg_data, cfg.index.index_refs,
            root, db_path, target, vendor_patterns, project_patterns_list,
        )
        if "error" in result:
            return result

        # Set before the with_analysis gate: the warning describes how much
        # of the header the re-parse covered, which does not depend on
        # whether the enrichment phases run.
        coverage_warning = _header_coverage_warning(
            target, matching, via_header,
            manifest=build_manifest, project_root=root, config_hash=config_hash,
        )
        if coverage_warning:
            result["warning"] = coverage_warning

        if not with_analysis:
            return result

        return _reindex_post_write_phases(
            conn, config_hash, cfg, total_symbols, db_path.parent,
            target, result, root,
        )

    try:
        # One call of the executor for each build: its lock is free between
        # two builds, thus a query of another tool does not wait for all of
        # them.
        for build in builds:
            answers.append((build, executor.execute_sync(
                _reindex_one_build, build["config_hash"],
                build, target, cfg, root, db_path, with_analysis,
            )))
    finally:
        # Best-effort statistics refresh after the write.  Runs through
        # the executor so it serializes with other queries.
        def _optimize(conn, _ch):
            conn.execute("PRAGMA optimize")

        try:
            executor.execute_sync(_optimize, builds[0]["config_hash"])
        except sqlite3.Error:
            pass
    return _reindex_answer(target, answers, round(time.monotonic() - t0, 2))


def _reindex_builds(
    db_path: Path, project_id: str, cfg: Config, variant: str, image: str, root: Path,
) -> tuple[list[sqlite3.Row], str | None]:
    """Return the builds that ``reindex_file`` re-parses, or the refusal of the selector.

    Without a selector: each build of the project (`latest_builds`).  An
    edit changes the file for the whole tree, thus each build that
    compiles it must read it again, or its answers stay old while the
    others are new.  With a selector: the build that ``resolve_build``
    gives, with the refusal that each query tool gives for a part of a
    pair.  Ordered by label, as `get_project_status` lists them, and not
    by the time of the index run: the order of ``builds`` in the answer
    then does not change with each run.
    """
    from ..shared.variants import resolve_build

    conn = _quick_open_readonly(db_path)
    try:
        outdated = outdated_index_reason(conn, read_reindex_state(root) is not None)
        if outdated:
            return [], outdated
        builds = sorted(latest_builds(conn, project_id), key=build_label)
        if not (variant or image):
            return builds, None
        config_hash, refusal = resolve_build(conn, project_id, cfg, variant, image)
    finally:
        conn.close()
    if refusal:
        return [], refusal
    return [build for build in builds if build["config_hash"] == config_hash], None


def _reindex_one_build(
    conn: sqlite3.Connection,
    config_hash: str,
    cfg_data: sqlite3.Row,
    target: Path,
    cfg: Config,
    root: Path,
    db_path: Path,
    with_analysis: bool,
) -> dict | None:
    """Re-parse *target* in the build *cfg_data*, or remove it when it is gone from disk.

    Returns None when this build does not compile the file (or does not
    know a deleted file).  WRITE path: the executor is not read-only —
    writes are serialized with reads by the same lock.  A collision with a
    concurrently running CLI indexer is absorbed by the executor's 120 s
    busy_timeout (the CLI indexer keeps its own 10 s fail-fast).
    """
    if not target.exists():
        return _reindex_cleanup_deleted_file(conn, cfg_data, target, root, db_path)

    # The manifest of THIS build carries the vendor set the index run
    # applied.  reindex_file must write is_project with that same
    # boundary, or one file ends up on the other side of it.
    #
    # Loaded before the match because it answers a second question:
    # compile_commands.json lists no headers, so a header target finds
    # its translation unit only through the manifest.  One load serves
    # both — it costs 14 ms on a 9.3 MB manifest.
    from ...indexer.manifest import load as load_manifest

    build_manifest = load_manifest(db_path.parent, config_hash)

    matching, via_header, error = _reindex_match_tus(
        target, cfg_data, manifest=build_manifest, project_root=root,
    )
    if error:
        return error
    if not matching:
        return None

    vendor_patterns, project_patterns_list = _reindex_build_patterns(
        cfg, root, build_manifest
    )
    # The gaps before the re-parse, thus the embedding step can tell the
    # gaps that this re-parse makes from older ones (see
    # `_reindex_embeddings`).
    missing_before = (
        frozenset(symbols_without_embedding(conn, config_hash))
        if with_analysis and cfg.llm.enabled else frozenset()
    )
    total_symbols, result = _reindex_parse_and_store(
        conn, matching, cfg_data, cfg.index.index_refs,
        root, db_path, target, vendor_patterns, project_patterns_list,
        cfg.index.transient_defines,
    )
    if "error" in result:
        return result

    # Set before the with_analysis gate: the warning describes how much
    # of the header the re-parse covered, which does not depend on
    # whether the enrichment phases run.
    coverage_warning = _header_coverage_warning(target, matching, via_header)
    if coverage_warning:
        result["warning"] = coverage_warning

    _reindex_graph_phases(conn, config_hash, cfg, total_symbols, db_path.parent, result)
    if not with_analysis:
        return result

    return _reindex_post_write_phases(
        conn, config_hash, cfg, total_symbols, db_path.parent,
        target, result, root, missing_before=missing_before,
    )


def _reindex_answer(
    target: Path, answers: list[tuple[sqlite3.Row, dict | None]], elapsed_s: float,
) -> dict:
    """Join the result of each build into the answer of ``reindex_file``.

    WHY the top level of one build is its full result: the answer of a
    project of one build keeps each field that it had before builds were
    a part of it.  For several builds the top level holds the sums, and the
    detail of each build is in ``builds``.
    """
    done = [(build, result) for build, result in answers if result is not None]
    missing = [build_label(build) or "(no name)" for build, result in answers if result is None]
    if not done:
        if target.exists():
            return _not_built_error(target)
        return {"error": f"File not found on disk or in index: {target}"}

    entries = [
        {"variant": build["variant"] or "", "image": build["image"] or "",
         **{key: value for key, value in result.items() if key != "file"}}
        for build, result in done
    ]
    if len(done) == 1:
        answer = dict(done[0][1])
    else:
        answer = _reindex_sums(target, done, elapsed_s)
    answer["builds"] = entries
    if missing:
        answer["builds_without_file"] = missing
    return answer


def _reindex_sums(target: Path, done: list[tuple[sqlite3.Row, dict]], elapsed_s: float) -> dict:
    """Return the top level of an answer of several builds: the sums, and what each build shares.

    The sums come from the builds that did not fail.  WHY ``failed_builds``
    and the sum of ``skipped_count`` at the top level: a caller reads the
    top level first, and sums that left out a failed build, or the units
    that did not parse, look like a complete update.
    """
    good = [result for _, result in done if "error" not in result]
    failed = [build_label(build) or "(no name)" for build, result in done if "error" in result]
    if not good:
        answer: dict = {
            "file": str(target),
            "error": "fw-context could not update the file in each build that it tried. Each entry of builds holds its error.",
            "failed_builds": failed,
        }
        actions = {result.get("action") for _, result in done}
        if len(actions) == 1 and None not in actions:
            answer["action"] = actions.pop()
        return answer
    if not target.exists():
        answer = {
            "file": str(target),
            "symbols_removed": sum(result.get("symbols_removed", 0) for result in good),
            "action": "deleted",
        }
    else:
        answer = {
            "file": str(target),
            "translation_units": sum(result.get("translation_units", 0) for result in good),
            "symbols_updated": sum(result.get("symbols_updated", 0) for result in good),
            "elapsed_s": elapsed_s,
        }
        skipped = sum(result.get("skipped_count", 0) for result in good)
        if skipped:
            answer["skipped_count"] = skipped
        warnings = {result.get("warning") for result in good}
        if len(warnings) == 1 and None not in warnings:
            answer["warning"] = warnings.pop()
    if failed:
        answer["failed_builds"] = failed
    return answer


def _reindex_resolve_target(
    file_path: str, root: Path
) -> tuple[Path, dict | None]:
    """Resolve *file_path* to an absolute path relative to *root*.

    When the path is relative, it is joined with *root* and resolved.
    When absolute, it is resolved directly.  Resolution follows symlinks
    so the path matches the canonical form in compile_commands.json.

    Returns ``(target, None)`` on success or ``(target, error_dict)``
    when the path cannot be resolved.  The error_dict arm is reserved
    for future path-safety checks; currently this function never fails.
    """
    target = Path(file_path)
    if not target.is_absolute():
        target = (root / target).resolve()
    else:
        target = target.resolve()
    return target, None


def _tus_for_header(
    target: Path, manifest: dict | None, project_root: Path
) -> list[Path]:
    """Return EVERY translation unit that includes *target*.

    A header is never its own entry in compile_commands.json, thus the
    direct match cannot find it.  The manifest records each TU with the
    header paths it pulled in, which answers the reverse question.

    ALL units, not one of them.  Two compile actions of one file can include
    the header under different ``#define`` values, and re-parsing only the
    lowest path refreshed one context while the other kept its stale rows
    behind a canonical phantom (the single-TU header re-index defect).  A
    large header still fans out — ``sdk_config.h`` reaches 266 units on the
    Mbed project — but those units now re-parse per file, not per (file,
    context) pair, so the fan-out is bounded by the file count.

    The result is sorted by absolute path, not by manifest order: manifest
    order follows compile_commands.json, which a rebuild can reshuffle, and
    a tool that ordered differently on every call would be hard to reason
    about.

    Paths are compared as strings.  The manifest already stores resolved
    forms — relative to the project root when inside it, absolute
    otherwise (see ops.py) — and *target* arrives resolved from
    _reindex_resolve_target, thus another resolve() per header would only
    add a syscall for each of the tens of thousands of header references
    a large manifest holds.
    """
    if not manifest:
        return []
    root = project_root.resolve()
    target_str = str(target)
    includers: set[str] = set()
    for entry in manifest.get("entries") or ():
        tu_file = entry.get("file")
        if not tu_file:
            continue
        for header in entry.get("headers") or ():
            header_abs = header if Path(header).is_absolute() else str(root / header)
            if header_abs == target_str:
                tu_abs = tu_file if Path(tu_file).is_absolute() else str(root / tu_file)
                includers.add(tu_abs)
                break
    return [Path(p) for p in sorted(includers)]


def _tu_for_header(
    target: Path, manifest: dict | None, project_root: Path
) -> Path | None:
    """Return the lowest-path translation unit that includes *target*, or None.

    Deterministic single-unit view over :func:`_tus_for_header` — kept for
    callers and tests that reason about ONE including unit.  The reindex
    path uses :func:`_tus_for_header` directly: every context that includes
    the header must be re-parsed, not just the first file.
    """
    tus = _tus_for_header(target, manifest, project_root)
    return tus[0] if tus else None


def _reindex_match_tus(
    target: Path,
    cfg_data: sqlite3.Row,
    *,
    manifest: dict | None = None,
    project_root: Path | None = None,
) -> tuple[list, bool, dict | None]:
    """Find compilation units in compile_commands.json that build *target*.

    Parses the compile_commands.json file and matches by absolute,
    resolved file path.  A single source file may appear in more than one
    unit — the same .cpp built with two flag sets — and every match is
    returned so symbols from each context are updated.

    A header matches nothing this way, because compile_commands.json
    lists translation units and a header is not one.  For that case
    *manifest* supplies EVERY unit that includes the header — see
    _tus_for_header for the full set and why one is not enough.

    Returns ``(matching_tus, via_header, None)`` on success, where
    *via_header* says the units came from the manifest fallback and the
    result may therefore cover only the contexts that are still reachable
    from compile_commands.json.  On failure it returns an empty list with
    an ``error_dict`` — the compile_commands.json is missing, or nothing
    builds the target.
    """
    cc_path = Path(cfg_data["compile_commands_path"])
    if not cc_path.exists():
        return [], False, {"error": f"compile_commands.json not found: {cc_path}"}
    # list(), not the iterator parse_cc returns: the header fallback below
    # walks the units a second time, and a generator is empty by then.
    units = list(parse_cc(cc_path))
    matching = [u for u in units if Path(u.file).resolve() == target]
    if matching:
        return matching, False, None

    tu_paths = _tus_for_header(target, manifest, project_root or target.parent)
    if tu_paths:
        # The manifest can name a unit that a later build dropped from
        # compile_commands.json.  Those dropped files contribute nothing
        # here — their contexts stay untouched in the index, and the
        # coverage warning tells the caller how much was actually
        # refreshed.
        wanted = {str(p) for p in tu_paths}
        via = [u for u in units if str(Path(u.file).resolve()) in wanted]
        if via:
            return via, True, None

    return [], False, {"error": f"{target.name} not found in compile_commands.json — it may be a header-only file."}


def _effective_vendor_patterns(
    db_dir: Path, config_hash: str, root: Path
) -> list[str]:
    """Return the vendor set stored with THIS build, or [] when unknown.

    Read-only and best-effort: this feeds a status report, so a manifest that
    cannot be read must not make the report fail.
    """
    from ...indexer.manifest import load as load_manifest

    try:
        manifest = load_manifest(db_dir, config_hash)
    except OSError:
        log.debug("manifest unreadable for the status report", exc_info=True)
        return []
    if manifest is None:
        return []
    return list(manifest.get("vendor_patterns") or [])


def _reindex_build_patterns_for_generated(root: Path) -> tuple[list[str], None]:
    """Return build-output patterns for a manifest that stores none.

    Only for an index written before the manifest carried the key.  The
    manifest is the better source: it holds the value the index run applied,
    while this one re-detects and can answer differently after the build
    system changes.
    """
    from ...indexer.build import detect_build_system
    from ...indexer.builders import registry

    system = detect_build_system(root)
    builder_cls = registry.get(system) if system else None
    if builder_cls is None:
        return [], None
    from ...utils import build_dir_patterns_with_fw_context

    return build_dir_patterns_with_fw_context(
        list(builder_cls().get_build_dir_patterns(root))
    ), None


def _reindex_build_patterns(
    cfg, root: Path, manifest: dict | None = None
) -> tuple[list[str], list[str]]:
    """Collect vendor-exclude and project-include file path patterns.

    Returns two lists of SQL LIKE patterns:

    *vendor_patterns* — paths that the SDK detector and the user config
    mark as vendor/SDK code.  These patterns exclude symbols from the
    ``is_project`` flag and from ``project_only`` queries.

    *project_patterns_list* — paths explicitly listed as project code
    in the user config.  These override the auto-detected vendor
    boundary when the detector misclassifies a path.

    Pass *manifest* whenever the caller has it.  The set stored there is the
    one the index run applied, and to derive a second one here would give
    this build two different boundaries.
    """
    from ...indexer.sdk_detect import _normalize_patterns, vendor_patterns_for_build

    vendor_patterns = vendor_patterns_for_build(
        manifest, root, build_system=cfg.build.system
    )
    if cfg.index.vendor_paths:
        vendor_patterns.extend(_normalize_patterns(cfg.index.vendor_paths))
    project_patterns_list = (
        _normalize_patterns(list(cfg.index.project_paths))
        if cfg.index.project_paths
        else []
    )
    return vendor_patterns, project_patterns_list


def check_ollama(
    project_root: Annotated[
        str | None, Field(description="Project root. Auto-detected if omitted. "
        "Used to locate LLM config. Falls back to auto-detection when omitted.")
    ] = None,
) -> dict:
    """Check whether the LLM backend is running and the configured embedding/chat model is installed.

    Read-only: yes. No side effects. Call before smart_search,
    semantic_search, or explain_symbol (when on-demand fallback is
    expected — pre-computed analysis returns instantly without the LLM
    backend).

    Args:
        project_root: Project root. Auto-detected if omitted. Used to
            locate the project's LLM configuration.

    Returns:
        dict: {ollama_enabled (bool), status (str — "ok"|"disabled"|
        "not_configured"|"model_missing"|"embedding_unavailable"|"error"),
        ollama_running (bool), ollama_url (str), configured_model (str),
        num_ctx (int), installed_models (list[str]),
        chat_api (dict — ``{configured (bool), model (str)}``: whether an
        external chat API replaces Ollama, and the model it names),
        configured_embed_model (str), embedding_installed (bool),
        message (str, on error/disabled), model_details (list[dict], when
        Ollama running), suggest_cloud (bool), vec_available (bool),
        vec_error (str, optional), debug_log (str, optional — only when
        debug logging is enabled)}

        The chat model is looked for on the Ollama server of the chat
        calls: ``ollama_url``, or the Ollama server that ``chat_api_base``
        names.  An OpenAI-compatible ``chat_api_base`` runs the model
        itself, and no Ollama server is asked for it.  ``"model_missing"``:
        ``message`` names a command for each missing model, which sets
        ``OLLAMA_HOST`` to the server that must hold the model.
        ``"error"`` also means that the Ollama server of ``chat_api_base``
        gives no model list.  When the server of ``ollama_url`` does not
        answer, the chat model is not looked for.

        ollama_url, num_ctx, installed_models, model_details,
        configured_embed_model, and embedding_installed are present only
        when Ollama runs; configured_model also needs chat on Ollama.
        ``"disabled"`` gives only status, ollama_enabled,
        ollama_running, configured_model, num_ctx, and message;
        embedding_backend is present on every other status.
    """
    try:
        _, cfg, _, _ = _resolve_context(project_root)
    except RuntimeError:
        # Project has no index — irrelevant for check_ollama (only needs
        # LLM config).  Fall back to loading config directly without
        # requiring an active index.  WHY resolve_project_root: `load(None)`
        # reads the global config only, thus the [llm] keys of local.toml
        # of the project had no effect.  A project name went to `load` as a
        # path relative to the current directory.
        cfg = load_config(resolve_project_root(project_root))
    if not cfg.llm.enabled:
        # LLM is explicitly disabled in config.  Return immediately
        # with a message that explains the implications for tools that
        # depend on the LLM backend.
        return {
            "status": "disabled",
            "ollama_enabled": False,
            "ollama_running": False,
            "configured_model": cfg.llm.model,
            "num_ctx": cfg.llm.num_ctx,
            "message": (
                "LLM backend is disabled in config ([llm] enabled = false). "
                "explain_symbol with pre-computed analysis (default) returns instantly. "
                "Without analysis, it returns source + explain_prompt for the agent to answer. "
                "smart_search will use raw text queries."
            ),
        }
    # Delegates to check_setup which probes the Ollama server / chat API,
    # lists installed models, and determines the status string.
    result = check_setup(cfg.llm)
    result["ollama_enabled"] = True

    # sqlite-vec is needed for semantic_search embeddings.
    # Report availability so tools can degrade gracefully.
    from ...deps import _vec_available
    vec_ok, vec_err = _vec_available()
    result["vec_available"] = vec_ok
    if vec_err is not None:
        result["vec_error"] = vec_err

    return result


def _with_project(command: str, root: Path) -> str:
    """Add ``--project <root>`` to a ``fw-context doctor`` command; give each other command as it is.

    WHY: the caller runs the command in its own shell, and its current
    directory is not always the project of the call.  ``doctor`` then
    checked and repaired a different project, or none.  The quotes are the
    ones of PowerShell on Windows and of a POSIX shell on the other systems,
    as in ``ollama_pull_command``.
    """
    import shlex
    import sys

    if not command.startswith("fw-context doctor"):
        return command
    if sys.platform == "win32":
        quoted = "'" + str(root).replace("'", "''") + "'"
    else:
        quoted = shlex.quote(str(root))
    return f"{command} --project {quoted}"


def _dep_to_schema(r, root: Path | None = None) -> dict:
    """Map one ``DepCheckResult`` to the ``{name, status, critical, message, action}`` schema.

    The optional ``action`` carries a human-readable ``message`` (what is
    wrong or what to do) and an exact shell ``command`` (``fix_cmd``).
    When ``instructions`` merely repeats ``fix_cmd`` (the common pip/apt
    one-liner case), the status ``message`` is used as the human part so
    ``action.message`` is not a bare duplicate of ``action.command``.
    ``status="skipped"`` passes through unchanged — it signals a missing
    prerequisite, not a failure.
    """
    d: dict = {"name": r.name, "status": r.status, "message": r.message}
    if r.fix_cmd or r.instructions:
        human = r.instructions or r.message
        if r.fix_cmd and human.strip() == r.fix_cmd.strip():
            human = r.message
        d["action"] = {"message": human, "command": _with_project(r.fix_cmd, root) if r.fix_cmd else r.fix_cmd}
    return d


def _llm_to_schema(result: dict) -> dict:
    """Map ``check_ollama()`` output to the documented ``llm`` sub-schema.

    ``check_ollama`` returns ``ollama_enabled``/``configured_model``/
    ``configured_embed_model``/``embedding_installed`` — not the ``llm``
    sub-schema.  This adapter maps those keys.  The disabled path returns
    only a subset of keys, so every read uses ``.get(key, default)``.
    """
    llm: dict = {
        "enabled": bool(result.get("ollama_enabled")),
        "ollama_running": bool(result.get("ollama_running")),
        "chat_model": result.get("configured_model"),
        "embed_model": result.get("configured_embed_model"),
    }
    status = result.get("status")
    action: dict | None = None
    if status == "model_missing":
        model = (
            result.get("configured_embed_model")
            if not result.get("embedding_installed")
            else result.get("configured_model")
        )
        action = {"message": result.get("message", ""), "command": f"ollama pull {model}" if model else None}
    elif status in ("not_configured", "disabled"):
        action = {"message": result.get("message", ""), "command": "fw-context init"}
    elif status in ("embedding_unavailable", "error"):
        action = {"message": result.get("message", "")}
    if action is not None:
        llm["action"] = action
    return llm


def _cc_entry_count(cc: Path) -> int | None:
    """Count entries in a compile_commands.json (None on parse error).

    ``None`` signals "count unknown" to the LLM — a corrupt file must not
    crash this read-only status tool.  ``0`` is reserved for an
    empty-but-valid compile DB, so a parse failure is kept distinct.
    """
    import json

    try:
        return len(json.loads(cc.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return None


def get_environment_status(
    project_root: Annotated[
        str | None,
        Field(description="Project root. Auto-detected if omitted. Pass explicitly when the project is not the server cwd."),
    ] = None,
) -> dict:
    """Return the complete project environment status in one call.

    Read-only. Aggregates five domains into a single call so the LLM can
    see everything at session start without extra round-trips:

    - ``deps`` — dependency audit (``run_full_check``), each entry with an
      optional ``action`` (``message`` + shell ``command``).  ``status="skipped"``
      means a prerequisite is missing (e.g. ``libclang-so`` skipped because
      ``libclang-python`` is absent) — not a failure.
    - ``build_system`` — detected build system, ``None`` when unknown.
    - ``compile_db`` — whether compile_commands.json exists and its entry count.
      Reported as ``{"exists": false, ...}`` before init (no config to resolve
      the path from, and loading one would create empty config files).
    - ``index`` — the FULL ``get_active_build()`` result, unchanged (its action
      lives in ``index_message``).
    - ``llm`` — LLM backend status with an optional ``action``.

    When the project is not initialized (``index.status == "not_initialized"``),
    only the config-independent dependency subset runs (checks that do not need
    a project config) — Ollama/model/db/build checks are skipped.

    Args:
        project_root: Project root directory. Auto-detected from CWD if omitted.

    Returns:
        dict: {init_status (str — "initialized" or "not_initialized"),
        deps (list[dict] — name, status, message, and an optional action),
        build_system (str or None),
        compile_db (dict — {exists (bool), path (str or None),
        entry_count (int or None — None before init, and when fw-context
        cannot read the file)}),
        index (dict — the full ``get_active_build`` result),
        llm (dict — {enabled, ollama_running, chat_model, embed_model}, plus
        ``ollama_enabled`` when the LLM check ran, plus an optional
        ``action``)}.
    """
    from ...deps import run_full_check

    root = resolve_project_root(project_root)

    index = get_active_build(project_root=str(root))
    init_status = "initialized" if index.get("status") != "not_initialized" else "not_initialized"

    if index.get("status") == "not_initialized":
        # libclang-so is project-config-independent (only needs libclang-python),
        # so it belongs in the pre-init subset — the "missing libclang .so →
        # apt install libclang-18-dev" signal must work before the first init.
        pip_subset = {"pysqlite3", "sqlite-vec", "libclang-python", "libclang-so", "watchfiles", "tomli-w"}
        dep_results = run_full_check(project_root=str(root), subset=pip_subset)
        # No [index] compile_commands path exists pre-init, and load_config()
        # would create empty .fw-context/config.toml + local.toml.  Skip it so
        # this read-only tool has no file-creation side effect before init.
        compile_db: dict = {"exists": False, "path": None, "entry_count": None}
    else:
        dep_results = run_full_check(project_root=str(root))
        cfg = load_config(root)
        from ...indexer.build import resolve_reuse_compile_commands

        cc = resolve_reuse_compile_commands(root, cfg.index.compile_commands)
        compile_db = {"exists": cc.exists(), "path": str(cc) if cc.exists() else None, "entry_count": None}
        if cc.exists():
            compile_db["entry_count"] = _cc_entry_count(cc)
    deps = [_dep_to_schema(r) for r in dep_results]

    build_system_raw = index.get("build_system") or _detect_build_system(root)
    build_system = None if build_system_raw == "unknown" else build_system_raw

    if index.get("status") == "not_initialized":
        # No config exists yet — probe the LLM backend from defaults so this
        # read-only tool does NOT create .fw-context config files.  check_ollama
        # routes through load() (via _resolve_context), which has that
        # file-creation side effect.  Resolve the embed model first so the
        # report mirrors the initialized path (concrete embed_model, no pull).
        from ...config.settings import LLMConfig
        from ...llm.auto_model import resolve_embed_model

        default_cfg = LLMConfig()
        resolve_embed_model(default_cfg, skip_pull=True)
        llm_result = check_setup(default_cfg)
        llm_result["ollama_enabled"] = True
        llm = _llm_to_schema(llm_result)
    else:
        llm = _llm_to_schema(check_ollama(project_root=str(root)))

    return {
        "init_status": init_status,
        "deps": deps,
        "build_system": build_system,
        "compile_db": compile_db,
        "index": index,
        "llm": llm,
    }


def check_dependencies(
    project_root: Annotated[
        str | None,
        Field(description="Project root. Auto-detected if omitted. Pass explicitly when the project is not the server cwd."),
    ] = None,
) -> list[dict]:
    """Run the full dependency audit. Read-only. Returns structured results.

    Returns the raw per-check dicts (``name``, ``status``, ``message``,
    ``fix_cmd``, ``instructions``, ``critical``) — NOT the formatted
    ``doctor`` table.  Read ``status``/``fix_cmd``/``instructions`` per
    issue; ``status="skipped"`` means a prerequisite is missing.

    The check ``config`` names a config file that cannot be used.  The
    checks that read the config are then ``skipped``.  When a config file
    cannot be used, the tool cannot know if the project is initialized,
    thus it runs every check.

    Args:
        project_root: Project root directory. Auto-detected from CWD if omitted.

    Returns:
        list[dict]: one dict per check, ``DepCheckResult`` fields via ``asdict``.
    """
    from dataclasses import asdict

    from ...deps import run_full_check

    root = resolve_project_root(project_root)
    try:
        initialized = bool(load_config(root).project.id)
    except ConfigError:
        # The full audit: its check "config" names the file, and the
        # checks that need no config still run.
        initialized = True
    if initialized:
        results = run_full_check(project_root=str(root))
    else:
        results = run_full_check(project_root=str(root), subset=set(_PRE_INIT_CHECKS))
    return [_dep_to_schema(r, root) for r in results]


def get_project_info(
    project_id: Annotated[str, Field(description="Project ID (UUID4 hex) to look up.")] = "",
) -> dict:
    """Return project metadata (name, type, root_path) for a project ID.

    Looks up the global project registry at ``~/.fw-context/projects.db``.
    Use this to identify a project from its UUID4 — find out what build
    system it uses, its name, and where it was last indexed.

    Read-only. No side effects.

    Args:
        project_id: Project ID (UUID4 hex) to look up.

    Returns:
        dict: {project_id, name, project_type, root_path, created_at, updated_at}
        or {"error": "..."} when the project_id is not registered.

        On failure the dict holds only ``error`` with the reason.
    """
    from ...config.global_db import get_project_by_id

    if not project_id:
        # Empty string is the default — the LLM must be told that a
        # project_id is necessary before this tool can return anything.
        #
        # The message names the two tools that give the value.  It used to
        # say "from fw-context init", which is a command the caller cannot
        # run to answer a question about a project that is already indexed.
        return {
            "error": "project_id is necessary. Call list_projects for the id of every "
            "indexed project, or get_active_build for the id of this one."
        }
    result = get_project_by_id(project_id)
    if result is None:
        # The UUID was not found in the global registry (~/.fw-context/projects.db).
        # This can happen when the project was deleted or the registry was reset.
        return {"error": f"Project '{project_id}' not found in the global registry."}
    return result


def configure_llm(
    project_root: Annotated[str | None, Field(description="Project root. Auto-detected if omitted.")] = None,
    chat_api_base: Annotated[
        str | None,
        Field(
            description=(
                "Chat API URL. None = use local Ollama for chat. "
                "Auto-detects format: :11434 or /api/generate -> Ollama, "
                "/v1 or bare host -> OpenAI-compatible. "
                "Examples: 'https://api.deepseek.com/v1' (DeepSeek), "
                "'http://localhost:4000' (LiteLLM), "
                "'http://localhost:8080/v1' (llama.cpp). "
                "WARNING: external URLs send source code to that host."
            )
        ),
    ] = None,
    chat_api_key: Annotated[
        str | None,
        Field(description="API key for cloud/proxy APIs. None for local/no-auth."),
    ] = None,
    chat_api_format: Annotated[
        str,
        Field(description="Format override: 'auto' (default), 'ollama', or 'openai'."),
    ] = "auto",
    model: Annotated[
        str | None,
        Field(description="Chat model name. None = keep current."),
    ] = None,
    embed_model: Annotated[
        str | None,
        Field(description="Embedding model name (Ollama only). None = keep current."),
    ] = None,
    auto_pull: Annotated[
        bool,
        Field(description="Auto-pull models on 404 (Ollama only). False for intranet."),
    ] = False,
    stream: Annotated[
        bool | None,
        Field(
            description=(
                "Stream chat responses via SSE (both OpenAI-compatible and Ollama-native). "
                "True = send stream:true, consume SSE chunks — avoids reverse-proxy idle "
                "timeouts (nginx 60s, Cloudflare 100s). None = keep current setting."
            )
        ),
    ] = None,
) -> dict:
    """Configure LLM settings for the current project.

    Writes to ``<project>/.fw-context/local.toml`` ONLY (gitignored,
    per-developer). Does NOT modify the global config or the shared
    project ``config.toml``. After writing, tests the configuration
    by making a simple API call (skipped when LLM is disabled).

    IMPORTANT: When ``chat_api_base`` points to an external host, source
    code snippets in chat prompts will be sent to that endpoint. Ensure
    this complies with your organization's data security policies.
    Consider using local Ollama or an internal API proxy first.

    Args:
        project_root: Project root directory. Auto-detected if omitted.
        chat_api_base: Chat API URL (see description for format details).
        chat_api_key: Bearer token for cloud/proxy APIs.
        chat_api_format: Override auto-detection: "auto", "ollama", "openai".
        model: Chat model name.
        embed_model: Embedding model name (Ollama only).
        auto_pull: Whether to auto-pull models on 404.
        stream: Stream chat responses via SSE. True avoids reverse-proxy idle timeouts.

    Returns:
        dict: {status ("ok"|"error"), chat_api (dict — configured, endpoint,
        format, model), model (str), auto_pull (bool), stream (bool),
        test_latency_s (float, on success), test_response (str, on success),
        compliance_warning (str, when chat_api_base is external),
        message (str)}
    """
    from ...config.settings import ProjectNotInitializedError

    try:
        _, cfg, _, root = _resolve_context(project_root, skip_ready_check=True)
    except ProjectNotInitializedError as e:
        # configure_llm writes local.toml — the project must be initialized
        # so the config directory (.fw-context/) exists.
        return {
            "status": "error",
            "message": (
                f"Project is not initialized. Run 'fw-context init' in the "
                f"project root first. ({e.root})"
            ),
        }
    except ConfigError as exc:
        # The documented answer, not the list of the error boundary of
        # `_wrap_tool`: the caller reads `status`.  The tool cannot load the
        # config.  The message names the file that the user must correct.
        return {"status": "error", "message": str(exc)}

    # Collect only non-default/non-None values to avoid overwriting
    # existing settings with defaults.  The auto_pull comparison uses
    # the current config value (not a constant) to distinguish "user
    # explicitly set False" from "user passed the default arg False".
    updates: dict[str, object] = {}
    if chat_api_base is not None:
        updates["chat_api_base"] = chat_api_base
    if chat_api_key is not None:
        updates["chat_api_key"] = chat_api_key
    if chat_api_format != "auto":
        updates["chat_api_format"] = chat_api_format
    if model is not None:
        updates["model"] = model
    if embed_model is not None:
        updates["embed_model"] = embed_model
    if auto_pull != cfg.llm.auto_pull:
        updates["auto_pull"] = auto_pull
    if stream is not None:
        updates["stream"] = stream

    if not updates:
        return {
            "status": "error",
            "message": "No configuration changes to write — all parameters are None or default.",
        }

    # Delegate to the shared write+test implementation (llm/configure.py).
    # It writes only local.toml — never global or committed config —
    # reloads the config, and tests the endpoint.
    from ...llm.configure import configure_llm_core

    return configure_llm_core(Path(root), updates, chat_api_base=chat_api_base)


_INTERNAL_HEALTH_KEYS = frozenset({
    "_restart_reason", "_restart_format", "_analysis_advice", "_embeddings_missing",
})


@dataclass(frozen=True)
class _IndexTarget:
    """The index of one project, as the two status tools find it."""

    root: Path
    cfg: Config
    project_id: str
    db_path: Path
    # "reindexing" for an index run of the daemon or of the operator, and
    # for a `reindex_file` or a `reset_index` that holds `write.lock`.
    # "pending" for work of the daemon that is not complete.  See
    # `read_reindex_state`.
    # Read once for each call: a run is a fact of the project, and not of a
    # build.
    reindex_state: str | None

    @property
    def bg_running(self) -> bool:
        """Return True when an index run is active or the daemon has work that comes.

        WHY "pending" counts: the daemon starts its run, and the run holds
        the index only after the interpreter started and the config loaded.
        For a PlatformIO project, the build system also lists the variants
        before the run holds the index.  That can take some seconds.  A
        status that said "no run" in that time sent the caller to start a
        second index run of the same work.
        """
        return self.reindex_state is not None


def _index_target(project_root: str | None) -> _IndexTarget | dict:
    """Find the index of the project, or give the answer for a project without one.

    WHY the project ID comes from the config here and not from
    ``derive_project_id``: that call raises for a project that is not
    initialized, and these two tools report that state instead.  The other
    tools fail fast in that case, and these diagnostic tools explain it.

    A config file that ``load`` cannot use gives the status
    ``"config_error"``.  WHY its own status: without the file the project
    reads as not initialized, and the answer for that state tells the
    caller to run ``fw-context init``, which cannot repair the file.  An
    answer with ``error`` alone tells that the index holds no build.
    """
    root = resolve_project_root(project_root)
    try:
        fw_cfg = load_config(root)
    except ConfigError as exc:
        return {
            "status": "config_error",
            "project_root": str(root),
            "config_file": str(exc.path),
            "index_message": (
                f"{exc} Do not run `fw-context init` or `fw-context index`: they cannot "
                "repair the file."
            ),
        }
    if not fw_cfg.project.id:
        return _with_config_warnings({
            "status": "not_initialized",
            "project_root": str(root),
            "index_message": (
                f"Project at {root} is not initialized. "
                "Run `fw-context init` via bash to create a project ID and config, "
                "then call get_project_status() again."
            ),
        }, fw_cfg)
    db_path = fw_cfg.index.db_dir / fw_cfg.project.id / "index.db"
    if not db_path.exists():
        return _with_config_warnings({
            "status": "no_index",
            "project_root": str(root),
            "project_id": fw_cfg.project.id,
            "index_message": (
                f"No symbol index found for {root}. "
                "Run `fw-context index <path/to/compile_commands.json>` via bash "
                "to build the index, then call get_project_status() again."
            ),
        }, fw_cfg)
    # Read BEFORE the database: while a run is in progress the cache of
    # modified files can hold the counts from before it started.
    # The ID of this read of the config: a second read can meet a file
    # that an editor writes at this moment.
    return _IndexTarget(root, fw_cfg, fw_cfg.project.id, db_path, read_reindex_state(root))


def _build_missing_advice(reason: str, root: Path, proj_cfg, bg_running: bool) -> str:
    """Give *reason* with the command that brings the build back.

    The same two cases as the refusal of ``fw-context index``: a build that
    fw-context can run, and a build that only the IDE can run (a stub, see
    ``build.can_run_build``).  ``--build`` and not a plain reindex: a plain
    run builds the missing build too (``_build_if_missing`` in the CLI), but
    ``--build`` says what happens, and it is the same command that the
    refusal of a run names.

    WHY *bg_running*: an index run with ``--build`` removes the database of
    the build before the build writes it again.  Thus during the run of the
    daemon after a checkout, the build is missing, and the command of this
    reason is the command that runs.
    """
    from ...indexer.build import can_run_build

    system = proj_cfg.build.system or _detect_build_system(root)
    if can_run_build(proj_cfg.build, system):
        return _with_action(reason, "run `fw-context index --build`", bg_running)
    return _with_action(
        f"{reason}, and fw-context cannot run the build of this project",
        "run the build outside of fw-context (for example in the IDE), then run `fw-context index`",
        bg_running,
    )


def _build_row(build, health: dict) -> dict:
    """Return the entry of one build in the ``builds`` list of :func:`get_project_status`."""
    row = {
        "variant": build["variant"] or "",
        "image": build["image"] or "",
        "board": build["board"] or "",
        **{key: health[key] for key in _BUILD_ROW_FIELDS},
        "reasons": health["reindex_reasons"],
    }
    if health.get("_warning"):
        row["manifest_verification"] = health["manifest_verification"]
    return row


def _build_states(result: dict) -> list[str | None]:
    """Return the ``reindex_state`` of each build of a status answer.

    ``get_build_info`` answers for one build, and a project of several
    builds holds them in ``builds``.  A project of one build has both, with
    the same value.  WHY a function: `_with_project_notes` serves the two
    tools, and it must read the state from the field that each one has.
    """
    if "reindex_state" in result:
        return [result["reindex_state"]]
    return [row["reindex_state"] for row in result.get("builds", [])]


def _project_analysis_note(labels: list[str], healths: list[dict], bg_running: bool) -> str:
    """Return the analysis advice of a project of several builds, or "".

    Before, the message of such a project gave no advice: the list of
    builds computed no analysis, thus "Index is fully up to date" stood
    over builds whose symbols waited for the analysis.  One number for
    each build and no sum: a file that two builds compile is in the index
    of each.  The rules and the action are the ones of
    :func:`_analysis_advice`.  The action names the run of the message
    when any build of the project has a reason or a degraded index, also a
    build whose symbols do not wait: one index run indexes each build
    (``cli/_index.py``), and a plain ``fw-context index`` next to the
    action of a reason is not sufficient (the reason can need ``--build``).
    """
    waiting = [
        (label, health["_analysis_advice"])
        for label, health in zip(labels, healths, strict=True)
        if health["_analysis_advice"]["waiting"]
    ]
    if not waiting:
        return ""
    listing = ", ".join(f"{label}: {advice['waiting']}" for label, advice in waiting)
    vendor_note = next((advice["vendor_note"] for _, advice in waiting if advice["vendor_note"]), "")
    other_run = any(health["_analysis_advice"]["other_run"] for health in healths)
    return (
        f" Symbols wait for the LLM analysis in {len(waiting)} of {len(healths)} builds "
        f"({listing}){vendor_note}"
        + _analysis_action(bg_running, other_run=other_run, several=True)
        + "."
    )


def _build_health(
    conn,
    target: _IndexTarget,
    cfg,
    *,
    fast: bool,
    hash_cache: dict[str, str],
    facts: _ProjectFacts,
    detail: bool = True,
) -> dict:
    """Return the health of the build *cfg* (a ``build_configs`` row).

    Runs under the executor lock on the single shared connection, and must
    not open its own connection.

    *detail* False leaves out what only a report of this one build gives:
    ``reference_count`` and ``analysis`` (None), and the analysis part of
    ``index_message``.  WHY: the list of the builds of a project shows
    neither, and the count of references costs a query over every
    reference of the build.  The coverage of the LLM analysis is computed
    in the two modes: the message of a project names the builds whose
    symbols wait for it, and the queries are cheap (measured:
    ``_analysis_of`` takes 55 ms for the 7 builds of one project together).

    The result holds the fields of the build that the two status tools
    report, and the internal ones of ``_INTERNAL_HEALTH_KEYS``:
    ``_restart_reason`` (the index holds a NEWER row format than this
    process reads), ``_restart_format``, and ``_analysis_advice`` and
    ``_embeddings_missing`` (what the message of a project needs for the
    advice of :func:`_project_analysis_note` and
    :func:`_project_embedding_note`).
    ``index_message`` comes without the two leading notes that concern the
    project, a run in progress and a restart of the client:
    :func:`_with_project_notes` adds them one time.

    *hash_cache* is one cache of file hashes for the whole call: the count,
    the header check and the other builds read the same files of the SDK.
    Measured on a project of 7 builds, the header check took 3.6 s with one
    cache for each build.
    """
    root = target.root
    proj_cfg = target.cfg
    bg_running = target.bg_running
    config_hash = cfg["config_hash"]
    sym_count = conn.execute("SELECT COUNT(*) FROM symbols WHERE config_hash=?", (config_hash,)).fetchone()[0]
    file_count = conn.execute("SELECT COUNT(*) FROM files WHERE config_hash=?", (config_hash,)).fetchone()[0]
    ref_count = count_refs(conn, config_hash) if detail else None
    manifest_verification = cfg["manifest_verification"] or "none"
    # The scan runs in fast mode too.  Without it the status tool answered
    # "ready" while search_code on the same index warned about the same
    # file, and the caller had no way to tell which reading held.  It costs
    # about 41 ms on a project of 1900 files, against the 749 ms that the
    # header check below already spends.
    #
    # use_cache=False on purpose: the cache validates itself against
    # MAX(mtime) of the files table, which only a reindex moves, thus a
    # cached answer would miss the edit that just happened.
    modified_count = _count_modified_files(conn, config_hash, root, use_cache=False, hash_cache=hash_cache)
    # The header check is NOT cheap: it hashes every header of every
    # translation unit, which is 22,693 files and 749 ms on the Mbed
    # project.  `fast` only gives it a cache, and does not skip it.
    # "partial" has the manifest with the headers too: only the embeddings
    # are not complete.
    if manifest_verification in ("full", "partial"):
        header_affected_tus, _ = _check_header_staleness(
            conn, config_hash, root, use_cache=fast, hash_cache=hash_cache,
        )
    else:
        header_affected_tus = 0
    db_schema_ver = get_db_schema_version(conn)

    analysis, stored_analyze_vendor = _analysis_of(conn, config_hash)

    cc_changed, stale_reason = _is_stale(cfg, cfg["compile_commands_path"])
    schema_old = db_schema_ver < CURRENT_SCHEMA_VERSION
    # Does the stored text still mean what this version reads it to mean?
    # The schema version above cannot answer that: it hashes the column
    # SET, thus it moves only when a column appears or goes, while the
    # content of a column can change under an unchanged name.  See
    # CURRENT_ROW_FORMAT.
    # The test is ordinal, and not a plain inequality, because the two
    # directions need opposite answers.  An OLDER format asks for a
    # reindex, and the run repairs it.  A NEWER one cannot be repaired by
    # any command: the index is right and THIS PROCESS is the old reader,
    # thus the indexer writes the same new format again.
    stored_format = stored_row_format(cfg)
    row_format_old = row_format_is_older(stored_format)
    row_format_newer = row_format_is_newer(stored_format)

    # ── The build: what the last run found, and what is there now ──
    # The marker is the text of the last index run that stopped without
    # an index (``autobuild.record_problem``); each query tool answer
    # carries it.  `_project_facts` reads it one time for the project.
    # The check of the build runs here too, because the status tool is
    # the first call of a session: a build that went away after the last
    # run has no marker yet.
    #
    # The check reads the file that an index run checks
    # (``checked_compile_commands``), and only when the index came from
    # it: an index of an explicit file, or of build variants, gets no
    # check, as `fw-context index` does none, and its advice `--build`
    # would replace the explicit file.  A build of fw-context keeps its
    # database in its output directory, thus a missing database is a
    # missing build, and it gets the advice of one.
    checked_cc = checked_compile_commands(root, proj_cfg, Path(cfg["compile_commands_path"]))
    build_missing = build_missing_reason(checked_cc) if checked_cc is not None else ""
    if facts.marker_build_missing:
        # The marker says the same, with the command of the run.
        build_missing = ""
    build_problem = facts.build_problem
    new_sources, auto_state = facts.new_sources, facts.auto_state

    # A source file that the build system never saw has no translation
    # unit, and only a build adds it.  It forces a reindex only when
    # fw-context does not build it on its own.  When it does, the next
    # index run builds it, thus the status below is "reindexing" when no
    # other reason forces a reindex, and no reason names `--build`.
    blocked_sources = bool(new_sources) and auto_state is not AutobuildState.WILL_BUILD

    # ── The tree has another checkout than this index ──
    # A plain reindex cannot fix it.
    # compile_commands.json is a build artifact of the checkout it was
    # generated on: it carries its file list AND its compiler flags.
    # Measured on the Mbed project, a switch from 4.15.3 to 4.15.1 left two
    # generated zcbor sources listed in that file and absent from the
    # tree, and a reindex reads the same file again.  A branch, a tag and a
    # commit all count (`git_context.head_moved`), with HEAD read one time
    # for the project.
    indexed_checkout, live_checkout = head_moved(
        stored_head(cfg),
        str(cfg["description"] or "") if "description" in cfg.keys() else "",
        facts.live_head,
        root,
    )
    checkout_moved = bool(indexed_checkout)
    # Modified source files are handled per query and do NOT force a
    # reindex.
    needs_reindex = bool(
        cc_changed or schema_old or row_format_old or blocked_sources or checkout_moved
        or build_problem or build_missing
    )
    # A missing compile_commands.json needs the action that makes it again
    # (`_compile_commands_reason`), and a plain run reads the same missing
    # file.
    cc_missing = cc_changed and stale_reason == "compile_commands_missing"
    # Asked once, because the call reaches the build backend and is not
    # free.  Only the checkout reason below reads it.
    bg_build_allowed = checkout_moved and _background_build_allowed(root, proj_cfg)
    # The action that makes compile_commands.json again: one function for
    # the status tools and the query answers, thus the two name the same
    # command.  The function reads the config and the markers of the build
    # system, thus the code asks it only when a reason needs it.
    rebuild = build_advice(root, proj_cfg) if checkout_moved or new_sources else ""

    reindex_reasons: list[str] = []
    if build_problem:
        reindex_reasons.append(build_problem)
    if build_missing:
        reindex_reasons.append(_build_missing_advice(build_missing, root, proj_cfg, bg_running))
    checkout_text = ""
    if checkout_moved:
        # Never a plain reindex: that would reuse compile_commands.json,
        # which belongs to the checkout the index was built on, with its file
        # list and its compiler flags.  `rebuild` names the build that
        # makes it again: `--build`, or the build of the user.
        #
        # The daemon runs it on its own where the backend allows a
        # background build, so the advice says both — a checkout on a
        # project nobody is watching gets no burst, and then the manual
        # command is the only thing that fixes it.  A run already under way
        # changes the advice: the command named here is the command that
        # runs, thus naming it asks for the same work a second time.
        #
        # WHY the wording stays careful: `bg_running` says that AN INDEX
        # RUN exists, and nothing more.  The run belongs to the daemon or
        # to the operator, and its arguments are not readable from here.
        # A run that does not build leaves this reason standing.
        #
        # A run in progress comes first, also for a database of the user
        # (decision of the user, 2026-10-18): `rebuild` ends with "run
        # `fw-context index`", and during a run this text asks for a
        # second run.  The advice to wait is the one of
        # each other reason (a missing file, a new source file).
        #
        # A database of the user, or a build that fw-context cannot run (a
        # stub such as STM32CubeIDE): the user makes the file again, and the
        # next run records the checkout when the file is newer than the
        # checkout (`cli._index._records_checkout`).
        if bg_running:
            branch_advice = (
                "; an index run is in progress or scheduled — wait for it rather than "
                f"start a second one, and when this reason stays after the run, {rebuild}"
            )
        elif user_database(root, proj_cfg) is not None:
            branch_advice = f"; {rebuild}"
        elif bg_build_allowed:
            branch_advice = (
                "; a background reindex regenerates it with --build when "
                "the watcher sees a change, or run "
                "`fw-context index --build` now"
            )
        else:
            branch_advice = f"; {rebuild}"
        checkout_text = (
            f"checkout changed: indexed at {indexed_checkout!r}, "
            f"now at {live_checkout!r} — compile_commands.json belongs to "
            f"the old checkout" + branch_advice
        )
        reindex_reasons.append(checkout_text)
    # The schema and the row format name their command as each other
    # reason does, thus a caller that reads only `reindex_reasons` finds the
    # action of each one.  During a run the two tell the caller to wait:
    # each index run stamps the schema, and an index run of this build
    # writes its rows again.  A run of another build leaves the row format
    # old, and then the reason stays after the run with its command.
    if schema_old:
        reindex_reasons.append(_with_action(
            f"schema_mismatch: {db_schema_ver} < {CURRENT_SCHEMA_VERSION}",
            "run `fw-context index`",
            bg_running,
        ))
    if row_format_old:
        # Names the effect, not only the value: the caller has to know
        # what the answers it gets now are worth.  `row_format_effect`
        # picks the wording by the gap — the faults of the generations are
        # opposite, thus one fixed sentence would be false for every gap
        # but one.
        reindex_reasons.append(_with_action(
            f"row_format_mismatch: {stored_format or '(none)'} != "
            f"{CURRENT_ROW_FORMAT} — {row_format_effect(stored_format)}",
            "run `fw-context index`",
            bg_running,
        ))
    # A missing database of a build of fw-context is the missing build,
    # and the build reason above (or the marker of the run that found it)
    # already names it with its command.
    database_gone = checked_cc is not None and not checked_cc.exists()
    compile_commands_text = ""
    if cc_changed and not database_gone:
        compile_commands_text = _compile_commands_reason(stale_reason, cfg, root, proj_cfg, bg_running)
        reindex_reasons.append(compile_commands_text)
    if new_sources:
        listed = ", ".join(new_sources[:3])
        more = f" and {len(new_sources) - 3} more" if len(new_sources) > 3 else ""
        # "No command is needed" is false while the last run failed: the
        # build that adds them is the one that must be repaired first.
        advice = (
            "repair the build first, see the first reason"
            if build_problem or build_missing else _new_source_advice(auto_state, bg_running, rebuild)
        )
        reindex_reasons.append(
            f"{len(new_sources)} source file(s) not in compile_commands.json "
            f"({listed}{more}) — {advice}"
        )

    # One status that drives the decision of the caller.
    # Priority: reindex_needed > reindexing > ready.
    #
    # A file that fw-context builds on its own counts as "reindexing" and
    # not as "reindex_needed": the next index run builds it, and no other
    # command is necessary.  `reindex_state` below tells if that run comes:
    # without a run it is "not_scheduled", and the advice names
    # `fw-context index`.
    if needs_reindex:
        status = "reindex_needed"
    elif bg_running or (new_sources and auto_state is AutobuildState.WILL_BUILD):
        status = "reindexing"
    else:
        status = "ready"

    # ── Is the index of THIS build current, and what updates it ──
    # `status` mixes the build with the run of the project: "reindexing"
    # also when the run concerns another build.  `reindex_state` answers
    # for this build alone, see `index_state`.  A header that changed and
    # a unit that failed to parse count too: the rows of those units are
    # older than the files.  So does an index without a manifest: it cannot
    # see a changed header, and the warning below asks for a run.  An index
    # whose embeddings are not complete ("partial") is current: its symbols,
    # references and headers are complete, and the warning below asks the
    # caller to tell the user.
    behind = bool(
        needs_reindex or modified_count or header_affected_tus or new_sources
        or manifest_verification not in ("full", "partial")
    )
    reindex_state = index_state(behind, run_state=target.reindex_state, failed=bool(build_problem))

    if status == "ready" and modified_count:
        # The index is structurally sound, thus the status stays "ready"
        # and every query keeps working.  But files on disk have moved past
        # it, and the per-query warnings will say so — this message must
        # not contradict them by claiming full freshness.
        index_message = (
            f"Index is usable ({sym_count} symbols), but {modified_count} file(s) "
            f"changed since the last index run. Results about those files can be "
            f"out of date — run `fw-context index`."
        )
    elif status == "ready" and reindex_state is not None:
        # Stale headers, or an index without full header tracking: the
        # notes below name them.  "Fully up to date" would contradict
        # `reindex_state`.
        index_message = f"Index is usable ({sym_count} symbols), but it is not current"
    elif status == "ready":
        index_message = (
            f"Index is current ({sym_count} symbols), but its embeddings are not complete"
            if manifest_verification == "partial" else f"Index is fully up to date ({sym_count} symbols)"
        )
    elif status == "reindexing":
        if new_sources and auto_state is AutobuildState.WILL_BUILD:
            index_message = (
                f"{len(new_sources)} source file(s) are not in compile_commands.json "
                f"(first: {new_sources[0]}). {_new_source_advice(auto_state, bg_running, rebuild)}. "
                f"Queries work on existing data meanwhile."
            )
        elif modified_count:
            # Not "accurate": the answers about these files come from the
            # last run until the run ends.  `_with_project_notes` puts the
            # advice to wait in front.
            index_message = (
                f"Index is usable ({sym_count} symbols), but {modified_count} file(s) "
                f"changed since the last index run, and an index run updates them."
            )
        elif reindex_state is None:
            index_message = (
                "Index is usable — background reindex in progress or scheduled, and the "
                "index of this build is current. All queries return accurate results."
            )
        else:
            # Stale headers, or an index without full header tracking: the
            # run updates them, and the advice to wait comes from
            # `_with_project_notes`.
            index_message = "Index is usable — background reindex in progress or scheduled."
    elif build_problem or build_missing:
        # First among the reasons: without the build each unit gets no
        # answer of its compiler, and no other repair helps before it.
        index_message = (
            (build_problem or _build_missing_advice(build_missing, root, proj_cfg, bg_running))
            + " Queries still work on the data of the last finished index run."
        )
    elif blocked_sources:
        # Checked before the other reindex reasons: only a build repairs
        # this one, and a message that says plain `index` would send the
        # caller in a circle.  Only files that fw-context does not build on
        # its own: the next run builds the others, and a checkout next to
        # them needs its own message below.
        index_message = (
            f"{len(new_sources)} source file(s) are not in compile_commands.json "
            f"(first: {new_sources[0]}). A plain reindex cannot see them: "
            f"{_new_source_advice(auto_state, bg_running, rebuild)}. Queries still work on existing data."
        )
    elif checkout_moved:
        # Before the schema and the database: a plain `fw-context index`
        # reads compile_commands.json of the old checkout again.  The
        # reason names the build that makes it again.
        index_message = f"The {checkout_text}. Queries still work on existing data."
    elif cc_missing and compile_commands_text:
        # Before the schema and the row format: their plain
        # `fw-context index` reads the same missing file.  The reason names
        # the action that makes it again.
        index_message = f"{compile_commands_text} Queries still work on existing data."
    # The three take the action of their reason (`_with_action`).  WHY: a
    # bare "Run fw-context index" stood after the advice to wait that
    # `_with_project_notes` puts in front during a run, and a caller that
    # read the end of the message started a second run.
    elif schema_old and cc_changed:
        index_message = _with_action(
            f"Schema version mismatch ({db_schema_ver} < {CURRENT_SCHEMA_VERSION}) "
            "and compile_commands.json changed",
            "run `fw-context index`", bg_running,
        ) + " Queries still work on existing data."
    elif schema_old:
        index_message = _with_action(
            f"Schema version mismatch ({db_schema_ver} < {CURRENT_SCHEMA_VERSION})",
            "run `fw-context index`", bg_running,
        ) + " Queries still work on existing data."
    elif row_format_old:
        index_message = _with_action(
            "The rows of the index have an older meaning than this version reads (row_format_mismatch)",
            "run `fw-context index`", bg_running,
        ) + " Queries still work on existing data."
    elif compile_commands_text:
        # The reason text, thus the message and the reason name one action.
        index_message = f"{compile_commands_text} Queries still work on existing data."
    else:
        index_message = "Compile commands changed — run fw-context index. Queries still work on existing data."

    # When manifest verification is not full, warn the LLM.
    warning = None
    if manifest_verification == "partial":
        # Not a STOP and not a reason to wait: only the search by meaning
        # can miss symbols.  The text names the repair, and the user
        # decides.  WHY not `index_embeddings = false` as a repair: the run
        # then skips the step that deletes vectors, thus the incomplete
        # vectors stay, the stamp says "full", and the search by meaning
        # misses without a word.  WHY the full `doctor --fix` and not only
        # the model: a failed embedding step can have more than one cause,
        # and the full check finds each.  `auto_pull` is off by default,
        # thus a plain 'fw-context index' does not install the model.
        warning = (
            f"The embeddings of the last index run are not complete: {PARTIAL_EMBEDDINGS}. "
            "Tell the user. The repair: run 'fw-context doctor --fix', which does a check "
            "of each dependency and installs a missing embedding model, and then run "
            "'fw-context index'. 'fw-context doctor --fix' also installs the other missing "
            "dependencies, for example the chat model."
        )
        if not (status == "ready" and reindex_state is None):
            # The message of a current build already says it.
            index_message += " — manifest verification: partial (the embeddings are not complete)"
    elif manifest_verification != "full":
        if bg_running:
            # The same advice as `reindex_state`: the build is not current,
            # and a run comes.  A text that said "continue" here gave the
            # caller two opposite actions in one answer.
            warning = (
                f"The index is not complete: {manifest_reason(manifest_verification)}. "
                "An index run is in progress or scheduled, and it updates the "
                f"index when it ends. {WAIT_ADVICE}"
            )
            index_message += (
                f" — manifest verification: {manifest_verification} (reindex in progress or scheduled — wait for completion)"
            )
        else:
            # Each reason that sets `reindex_needed` names its own action,
            # and a plain 'fw-context index' next to it is not always
            # sufficient: a reason can name what to repair before the run,
            # or that the run needs `--build`.  The run that the reason
            # names also makes the index complete.
            if needs_reindex:
                command = _REASON_COMMAND
                tracking = "the run that reindex_reasons names makes the index complete"
            else:
                command = "'fw-context index'"
                tracking = "run 'fw-context index' to make the index complete"
            warning = (
                "⚠️ INDEX DEGRADED — STOP ALL C/C++ ANALYSIS "
                "IMMEDIATELY.\n\n"
                f"The index is not complete: {manifest_reason(manifest_verification)}. "
                "The index can hold STALE data, and an analysis with a "
                "stale index WILL give incorrect results.\n\n"
                f"REQUIRED: Tell the user to run {command} "
                "to make the index complete. Do NOT continue any C/C++ "
                "analysis until the user confirms that the index run ended."
            )
            index_message += f" — manifest verification: {manifest_verification} ({tracking})"

    if header_affected_tus:
        # A TU that failed to parse keeps its needs_reparse mark, so it
        # counts here run after run.  Without the second half of this
        # sentence the user is told to run 'fw-context index', and the
        # index does not clear it.
        index_message += (
            f" | {header_affected_tus} source file(s) have stale header dependencies "
            "— header changes since last index, or a TU that failed to "
            "parse (see the index log)"
        )
        if not bg_running and not needs_reindex:
            # No reason names this, thus without the command the caller of
            # a project of one build got "not_scheduled" and no action.  The
            # message of several builds names the same command.  A reason
            # names its own command, and a plain `fw-context index` next to
            # it is not sufficient: the reason names what to repair before
            # the run, or that the run needs `--build`.
            index_message += " — run `fw-context index` for a changed header"

    # Another action of this message comes first: the run of a reason or
    # of a degraded index also does the analysis and the embeddings, and a
    # newer row format needs a restart and no run.  Not "partial": its
    # warning is advice in `_warning`, and `index_message` names no run
    # for it.
    run_named_above = needs_reindex or manifest_verification not in ("full", "partial")
    # LLM-analysis coverage, split by project/vendor, so the LLM reads
    # unanalyzed vendor symbols as expected (analyze_vendor=false), not as
    # a defect.
    if detail:
        index_message += _analysis_message(analysis, stored_analyze_vendor)
        index_message += _analysis_advice(
            analysis, target.cfg, bg_running,
            other_run=run_named_above,
            restart=row_format_newer, stored_analyze_vendor=stored_analyze_vendor,
        )
    # Definitions without an embedding, in the two modes: the message of a
    # project names each build that has them (`_project_embedding_note`).
    embeddings_missing = _embeddings_missing(conn, cfg, proj_cfg, restart=row_format_newer)
    if detail and embeddings_missing:
        index_message += (
            f" | Embeddings: {embeddings_missing} definition(s) have no embedding, "
            "and the search by meaning does not find them"
            + _run_action(bg_running, other_run=run_named_above, plain=_EMBEDDING_PLAIN_ACTION)
        )

    # ── This process is older than the index it reads ──
    # No command repairs this, thus it is a warning and not a reindex
    # reason.
    restart_reason = ""
    if row_format_newer:
        restart_reason = (
            f"This session reads row format {CURRENT_ROW_FORMAT} and the index holds "
            f"{stored_format}. The index is the newer one and it is correct — do "
            f"NOT reindex, because the indexer writes {stored_format} again and "
            f"this message comes back. This server process runs older code. Tell the "
            f"operator to restart the LLM client (Claude Code, opencode, or the client "
            f"in use). The MCP server is a child process of that client, thus nobody "
            f"can restart the server alone."
        )

    health: dict = {
        "indexed_at": cfg["created_at"],
        "symbol_count": sym_count,
        "file_count": file_count,
        "reference_count": ref_count,
        "modified_files_count": modified_count,
        "header_affected_tus": header_affected_tus,
        "analysis": analysis if detail else None,
        "manifest_verification": manifest_verification,
        "description": cfg["description"] if "description" in cfg.keys() else "",
        "first_indexed_at": cfg["first_indexed_at"] if "first_indexed_at" in cfg.keys() else "",
        "status": status,
        "reindex_state": reindex_state,
        "reindex_needed": needs_reindex,
        "reindex_reasons": reindex_reasons,
        "stale": needs_reindex or header_affected_tus > 0,
        "index_message": index_message,
        "_restart_reason": restart_reason,
        "_restart_format": stored_format,
        "_analysis_advice": {
            "waiting": _analysis_waiting(analysis, proj_cfg, restart=row_format_newer),
            "vendor_note": _analysis_vendor_note(proj_cfg, stored_analyze_vendor),
            "other_run": run_named_above,
        },
        "_embeddings_missing": embeddings_missing,
    }
    if warning is not None:
        health["_warning"] = warning
    return health


def _outdated_state(target: _IndexTarget, reason: str) -> dict:
    """Return the state of an index that an older version wrote (`outdated_index_reason`).

    No other field: each one comes from a build row, and the rows lack the
    columns of this version.  The status is ``reindex_needed`` because
    only the index run that adds the columns repairs it.  When the daemon
    starts, it starts that run.

    WHY the marker of a failed run comes first: some refusals of
    ``fw-context index`` (no build, a retired key of the config) stop the
    run before `open_db` adds the columns.  The marker names the repair, and
    a plain run without it is refused again.  A run that is in progress or
    scheduled wins over the marker, as in `index_state`.
    """
    problem = read_build_problem(target.db_path.parent)
    failed = problem if problem is not None and not target.bg_running else None
    reasons = [failed.text, reason] if failed else [reason]
    message = failed.text if failed else reason
    return _with_config_warnings({
        "project_id": target.project_id,
        "project_root": str(target.root),
        "status": "reindex_needed",
        "reindex_needed": True,
        "reindex_reasons": reasons,
        "reindex_state": "failed" if failed else (target.reindex_state or "not_scheduled"),
        "index_message": f"{message} No tool reads a build of this index until a run adds the columns.",
    }, target.cfg)


def _public(health: dict) -> dict:
    """Return the fields of *health* that a tool gives, without the internal ones."""
    return {key: value for key, value in health.items() if key not in _INTERNAL_HEALTH_KEYS}


_BUILD_ROW_FIELDS = (
    "symbol_count", "file_count", "indexed_at", "status", "reindex_state", "reindex_needed", "stale",
    "modified_files_count", "header_affected_tus",
)


def _analysis_of(conn, config_hash: str) -> tuple[dict, bool]:
    """Return the LLM-analysis coverage of a build, and the stored ``analyze_vendor``.

    Split by project vs vendor, so the LLM can tell intentionally skipped
    vendor symbols apart from project symbols that still need analysis.
    """
    coverage = compute_analysis_coverage(conn, config_hash)
    # Exclude the skip:* sentinels (skip:toolarge, skip:unparseable) —
    # they are not model names and must never surface as analysis.model.
    analysis_model_row = conn.execute(
        """SELECT a.model FROM llm_analysis a
           JOIN symbols s ON s.id = a.symbol_id
           WHERE s.config_hash = ?
             AND a.model NOT LIKE 'skip:%' LIMIT 1""",
        (config_hash,),
    ).fetchone()
    model = analysis_model_row["model"] if analysis_model_row else None
    # The stored analyze_vendor: what was actually indexed.  False for a
    # database from before the column.
    try:
        stored_analyze_vendor = bool(
            conn.execute(
                "SELECT analyze_vendor FROM build_configs WHERE config_hash = ?",
                (config_hash,),
            ).fetchone()["analyze_vendor"]
        )
    except sqlite3.OperationalError:
        stored_analyze_vendor = False
    return make_analysis_summary(coverage, stored_analyze_vendor, model), stored_analyze_vendor


def _project_embedding_note(labels: list[str], healths: list[dict], bg_running: bool) -> str:
    """Return the embedding advice of a project of several builds, or "".

    The counterpart of :func:`_project_analysis_note`, with the same rules:
    one number for each build and no sum, and the run of the message when
    any build has a reason or a degraded index.  The rules of one build are
    in :func:`_embeddings_missing`.
    """
    missing = [
        (label, health["_embeddings_missing"])
        for label, health in zip(labels, healths, strict=True)
        if health["_embeddings_missing"]
    ]
    if not missing:
        return ""
    listing = ", ".join(f"{label}: {count}" for label, count in missing)
    other_run = any(health["_analysis_advice"]["other_run"] for health in healths)
    return (
        f" Definitions have no embedding in {len(missing)} of {len(healths)} builds ({listing}), "
        "and the search by meaning does not find them"
        + _run_action(bg_running, other_run=other_run, plain=_EMBEDDING_PLAIN_ACTION)
        + "."
    )


def _with_project_notes(result: dict, target: _IndexTarget, restart_reason: str, restart_format: str) -> dict:
    """Put the two notes of the project in front of ``index_message``, and add the project fields.

    The front of ``index_message`` is what a caller acts on, thus a note
    that changes the action goes there, one time for the call.

    * A run in progress or scheduled, while the index of a build is not
      current (``reindex_state`` "reindexing" or "pending").  A message
      can name a command, and a caller that follows it asks the operator
      for work that is being done.  ``status`` stays as it is on purpose:
      the answers of THIS moment come from the rows of the last finished
      run.  The wording promises nothing about the outcome: the run belongs
      to the daemon or to the operator, and its arguments cannot be read
      from here.
    * This process is older than the index it reads (a NEWER row format).
      No command repairs that, and a "fully up to date" message alone would
      hide the one action that helps.
    """
    if any(state in RUN_STATES for state in _build_states(result)):
        result["index_message"] = (
            "An index run is in progress or scheduled — wait for it rather than start "
            "a second one, and ask the operator for no command yet. It "
            "can clear the reasons below, but a reason that needs "
            "`--build` clears only if the run uses it. Answers meanwhile "
            f"come from the last finished run. {WAIT_ADVICE} " + result["index_message"]
        )
    if restart_reason:
        result["index_message"] = (
            f"Restart the LLM client — this session reads row format "
            f"{CURRENT_ROW_FORMAT} and the index holds {restart_format}. "
            f"Queries work meanwhile. {result['index_message']}"
        )
        # No leading underscore: this is an answer to act on, and not a
        # note about where an answer came from.
        result["client_restart_required"] = True
        result["client_restart_reason"] = restart_reason
    result["bg_reindex_running"] = target.bg_running
    # Only for "reindexing".  For "pending", the log holds the lines of the
    # run before until the daemon starts the next run, and the start
    # empties the log.  Thus a line in that time can come from the old run
    # or from the next run.
    if target.reindex_state == "reindexing":
        result["reindex_progress"] = read_reindex_progress(target.db_path.parent)
    # sqlite-vec is an optional C extension for vector embeddings.  Report
    # its availability so semantic_search can degrade gracefully.
    from ...deps import _vec_available

    vec_ok, vec_err = _vec_available()
    result["vec_available"] = vec_ok
    if vec_err is not None:
        result["vec_error"] = vec_err
    return _with_config_warnings(result, target.cfg)


def get_project_status(
    project_root: Annotated[
        str | None, Field(description="Project root directory. Auto-detected from CWD if omitted.")
    ] = None,
    fast: Annotated[
        bool, Field(description="When True (default), reuse the cached manifest "
        "hashes for the header check. Both modes count modified files. Pass "
        "False to recompute the header hashes, which is far slower.")
    ] = True,
) -> dict:
    """MANDATORY FIRST CALL for C/C++ projects. Return the health of the
    index of the project, and the list of its builds — check it before you
    use any other fw-context tool, and call it again while you wait for a
    reindex.

    Read-only with two exceptions, and it spawns no subprocess — the
    startup daemon thread and the file watcher own the background reindex.
    The exceptions: the last index run can leave a marker that says that
    the build is not there, and when the tool finds that build again, it
    removes the marker.  It also removes a ``reindex.pid`` or a
    ``reindex.pending`` of a process that no longer runs.  It creates no
    ``.fw-context`` file in a project that is not initialized.  It takes no
    ``variant`` and no ``image``: it reports every build.  For the detail
    of one build (its ``config_hash``, memory map, defines), call
    ``get_build_info``.

    ``builds`` lists every build of the index.  A project of more than one
    build has no active build: every query tool must get the parts of one
    entry that are not empty, its ``variant``, its ``image``, or the two.
    Select the build from the task, and
    ask the user when the task does not identify one.

    Act on ``reindex_state`` of the build that the task needs, in
    ``builds`` (a project of one build has it at the top level too):

    * ``null`` — the index of the build is current. Continue.
    * ``"reindexing"`` or ``"pending"`` — the index of the build is not
      current, and an index run is active or scheduled. Wait, and call
      this tool again after about 30 s. Continue with the old index only
      when the user tells you to.
    * ``"not_scheduled"`` — the index of the build is not current, and no
      run updates it. Tell the user the command that the reasons name.
    * ``"failed"`` — the last index run failed. Give its text to the user:
      it names the command that repairs it.

    Each query answer carries the state of its build as ``index_state``,
    from quick checks only. A query thus can miss a changed header, a
    changed file that its answer does not name, or a new source file when
    its answer is not empty. This tool does the full check.

    ``status`` gives the state of the project:

    * ``"ready"`` — up to date. Continue.
    * ``"reindexing"`` — background reindex running or scheduled, or fw-context builds
      new source files on its own. ``reindex_state`` of a build tells
      whether that build waits for the run.
      ``reindex_progress`` holds the last log line of a running reindex.
    * ``"reindex_needed"`` — the last index run failed, the build is not
      there, schema mismatch, an old row format, a changed or missing
      compile_commands.json, a checkout of another branch, tag or commit
      since the index, or a source file that compile_commands.json does
      not cover and fw-context will not build itself. A changed
      compile_commands.json includes one that fw-context cannot compare
      with the index. Queries still work on existing data. Read
      ``reindex_reasons``: each reason names the action that it needs.

      Read ``bg_reindex_running`` before you name any command here.  A run
      already under way does the work, thus a second one only waits on the
      same lock, and ``index_message`` and the checkout reason both say so.
      That field covers a run of the daemon AND a run the operator started,
      and also work that the daemon has scheduled and not yet started
      — the arguments of no run are readable, thus a reason that needs
      ``--build`` can outlive a run without it.  This status wins over
      ``"reindexing"`` deliberately: the answers of this moment come from
      the rows of the last FINISHED run.
    * ``"no_index"`` — initialized, never indexed. Run ``fw-context index``.
    * ``"not_initialized"`` — run ``fw-context init``.
    * ``"config_error"`` — a config file cannot be used: it cannot be read,
      it is not UTF-8 or not valid TOML, a section is not a table, a key
      that selects the data or where data goes has a value of the wrong
      type, or a URL is not valid.  ``config_file`` names it, and
      ``index_message`` gives the problem.  Correct the file, or run
      ``fw-context doctor --fix --only config`` when the message names
      it: it removes each value that cannot be used.  ``fw-context init``
      cannot repair it.

    A project of several builds gets the worst status of its builds.  A
    reason that every build gives comes one time, and any other reason
    names its build: ``build <variant>/<image>: <reason>``.

    A failure sets NO ``status``.  An index with no build config gives a
    dict that holds ``error`` alone.  Read that key first: a reader that
    waits for ``status == "error"`` waits for a value this tool does not
    produce.  A database that fw-context cannot read (damage, no access)
    makes the call fail with the error of SQLite.  The message of the
    check ``db-integrity`` of ``check_dependencies`` tells if it is
    damaged.

    Seven conditions set ``reindex_needed``: a failed last index run, a
    build that is not there, an outdated schema, an outdated ROW FORMAT, a
    changed or missing compile_commands.json, a checkout of another
    branch, tag or commit since the index, and a source file that is on
    disk but absent from compile_commands.json when fw-context will not
    build it on its own.  A missing build, a missing
    compile_commands.json, a checkout and such a source file need the
    action that makes compile_commands.json again: a build, or for a
    database of the user (``[index] compile_commands``) a new file from the
    user.  A plain reindex reads the same file again, and it has no
    translation unit for a new source file and skips it without a word.  A
    failed run needs the repair that its reason names first.  Modified
    source files are something else: they are handled per query, and
    never set it.

    ``row_format_mismatch`` means that the same columns hold text with an
    older meaning.  Take it seriously: an index written before
    ``fw-context-rows/1`` keeps every inactive ``#ifdef`` branch, thus a
    body or a file from it can show code that the compiler never sees.  An
    index written before ``fw-context-rows/3`` is wrong the other way: it
    ANSWERS LESS than it should.  A definition that begins and ends on one
    line has no stored body there, thus ``search_bodies`` cannot reach an
    inline accessor; no macro is marked function-like; and no instance links
    to its template, thus ``get_template_instances`` gives an empty list for
    every template.  Each of those looks like a legitimate empty answer.
    The ``value`` of a macro there also holds its parameter list glued to
    its replacement text, thus neither can be read out of it.  An index
    written before ``fw-context-rows/4`` parsed a GCC build with guessed
    toolchain headers and the macros of clang: an ESP32 or AVR index there
    holds host headers from ``/usr/include`` and takes the ``#if`` branches
    of the host, for example ``#if __XTENSA__`` as false, and an ARM index
    saw ``__GNUC__`` 4.  A build whose GCC driver does not answer the query
    keeps that fault also after ``/4``; the index run names such a compiler
    in a warning.

    ``client_restart_required`` is the OPPOSITE case, and no command repairs
    it.  This server process does not run the code that it must run.  Two
    causes set it, and ``client_restart_reason`` names each one that
    applies:

    * The index carries a NEWER row format than this process reads, thus
      the index is the correct one and this process is the old reader.  A
      reindex makes it worse than useless: the indexer writes the same new
      format again, and the message comes back over an index that had
      nothing wrong with it.
    * The operator installed a different fw-context after the LLM client
      started this process.  This cause does not need an index, thus it
      shows also for ``not_initialized``, ``no_index`` and
      ``config_error``.

    ``status`` therefore stays as it is and ``reindex_needed`` stays False
    — every query keeps working.  Do NOT run a reindex for this field.
    Tell the operator to restart the LLM client — Claude Code, opencode, or
    whichever one is in use.  The MCP server is a child process of that
    client, thus nobody can restart the server by itself.
    ``client_restart_reason`` holds the wording, and ``index_message``
    opens with it.

    ``update_notice`` is not a status either.  It shows when PyPI has a
    newer fw-context than the installed one.  Tell the operator one time,
    and do NOT upgrade fw-context yourself: the operator knows the tool
    that installed it.  After a request that succeeded, the server asks
    PyPI again only after 24 hours.  The request runs in a background
    thread, and no tool waits for it.  ``[updates] check = false``
    in the global config, or ``FW_CONTEXT_NO_UPDATE_CHECK=1``, stops it.

    ``indexed_at`` and ``first_indexed_at`` are UTC; file mtimes are local
    time.  Never compare the two directly — in UTC+2 a correctly indexed
    file looks 2 hours newer than ``indexed_at``.  Read
    ``modified_files_count`` to find modified files.

    ``analysis`` splits the LLM-analysis coverage into project and vendor
    symbols:

    * ``model`` — the model of the analysis, or None. One model only, even
      when several were used.
    * ``analyze_vendor`` — the value at index time, not the current config.
    * ``project`` / ``vendor`` — ``{analyzed, skipped, total}``.
      ``skipped`` = tried, but not analyzable (body larger than the model
      context, an unparseable answer, or a body that was not readable).
    * ``complete`` — no work left: every project symbol is analyzed or
      skipped, and every vendor symbol too when ``analyze_vendor`` is True.
      Vendor symbols excluded by ``analyze_vendor=False`` never block it,
      thus ``vendor.total`` large with ``vendor.analyzed=0`` is expected,
      not a defect.  ``index_message`` ends with the same counts as text.

    Args:
        project_root: Project root directory. Auto-detected from CWD if
            omitted.
        fast: When True (default), the header check reuses the cached
            manifest hashes.  Both modes run the per-file scan, thus
            ``modified_files_count`` is accurate either way — a tool that
            reported "ready" while the search tools warned about the same
            file gave the caller two readings and no way to choose.
            False recomputes the header hashes and costs several times more.

    Returns:
        dict: {project_id, project_root, build_system, schema_version (int
        — DB schema version), current_schema (int — code expects),
        vendor_paths (list[str] — config index.vendor_paths),
        project_paths (list[str] — config index.project_paths),
        status (str — "ready"|"reindexing"|"reindex_needed"|"no_index"|
        "not_initialized"|"config_error"; a failure sets no status and
        gives ``error`` alone), config_file (str — only with
        "config_error": the file that cannot be used), reindex_needed (bool), reindex_reasons (list[str] — why a
        reindex is needed, empty when False, except that a new source file
        that fw-context builds on its own is listed while
        ``reindex_needed`` is False.  Some of them ask for a build rather
        than a plain reindex.  For example, after a checkout of another
        branch, tag or commit, compile_commands.json belongs to the OLD
        checkout and carries its file list and its compiler flags, so only
        a build regenerates it.  Read the reason text — it names the
        command it needs), stale (bool — True when a
        build needs a reindex or has stale header dependencies),
        index_message (str — human-readable summary of index state),
        builds (list[dict] — every build: {variant, image, board,
        symbol_count, file_count, indexed_at, status, reindex_state (str
        or None — "reindexing", "pending", "not_scheduled", "failed", or
        None for a current index), reindex_needed, stale,
        modified_files_count, header_affected_tus, reasons}, and
        ``manifest_verification`` on a build whose manifest is not "full"),
        declared_not_indexed (list[dict], optional — each
        variant that the config declares and the index does not hold:
        {variant, command}; no query reaches it until the command ran),
        bg_reindex_running (bool), reindex_progress (str or
        None — last log line when reindex is running), _warning (str,
        optional — when the manifest verification of a build is not
        "full"), vec_available (bool), vec_error (str, optional),
        client_restart_required (bool, optional — only when True),
        client_restart_reason (str, optional — with the field above),
        update_notice (str, optional — a newer release is on PyPI)}

        A project of ONE build also gets the fields of that build at the
        top level: indexed_at (str — "YYYY-MM-DD HH:MM:SS" in UTC, the
        completion time of the last full index), first_indexed_at,
        symbol_count, file_count, reference_count, modified_files_count
        (int — files whose content no longer matches the index; counted in
        both modes), header_affected_tus (int — number of source files with
        stale header dependencies; a file that compile_commands.json lists
        more than once counts once), manifest_verification (str — "full"
        when manifest.json exists, "partial" when it exists and the
        embeddings are not complete, "none" without one), analysis (dict — LLM
        analysis coverage split by project/vendor: {model, analyze_vendor,
        project: {analyzed, skipped, total}, vendor: {analyzed, skipped,
        total}, complete}), description (str).  A project of several
        builds gets no sums: the counts of each build are in ``builds``,
        and ``reference_count``, ``analysis``, ``description`` and
        ``first_indexed_at`` of a build are in ``get_build_info``.

        For a project that is not initialized, the result holds only
        ``status``, ``project_root``, and ``index_message``.  When no index
        exists, the result adds ``project_id``.  For ``"config_error"``, it
        adds ``config_file``.  The two version fields
        (``client_restart_*`` for an upgrade, ``update_notice``) can come
        with every result, these two included.  ``config_warnings`` (a
        list) can come with each result that has a ``status`` other than
        ``"config_error"``: values of a config file that fw-context did not
        use.  Tell the user each line.
    """
    return _with_version_state(_project_state(project_root, fast))


def _with_version_state(result: dict) -> dict:
    """Add the version facts that do not depend on the index to *result*.

    One wrapper around every return path of :func:`_project_state`,
    because an upgrade of the package concerns the process and not the
    project: the caller must read it also when the project has no index.
    A restart reason of the index (a newer row format) and a restart reason
    of the package can apply together, thus the second one joins the text
    and does not replace it.
    """
    from ...config.settings import load_update_settings
    from ...version_check import restart_reason, update_notice

    reason = restart_reason()
    if reason:
        prior = result.get("client_restart_reason")
        result["client_restart_required"] = True
        result["client_restart_reason"] = f"{prior} {reason}" if prior else reason
        # A row-format reason already put the same action at the front of
        # index_message.  One action needs one sentence there.
        if "index_message" in result and not prior:
            result["index_message"] = (
                "Restart the LLM client — this server runs a different fw-context than "
                f"the installed one. Queries work meanwhile. {result['index_message']}"
            )
    notice = update_notice(load_update_settings().check)
    if notice:
        result["update_notice"] = notice
    return result


PARTIAL_EMBEDDINGS = "semantic_search and smart_search can miss symbols (manifest verification: partial)"


def _project_health(builds: list, healths: list[dict], bg_running: bool) -> dict:
    """Join the health of several builds into the health of the project.

    No sums: a file that two images compile is in the index of each, thus
    a sum counts shared code once for each build.  The counts are in the
    ``builds`` list.

    A reason that every build gives is a reason of the project, such as an
    old schema or a failed build, and it comes one time and without a
    label.  Any other reason gets the label of its build.
    """
    labels = [build_label(build) or "(no name)" for build in builds]
    common = set.intersection(*(set(health["reindex_reasons"]) for health in healths))
    reasons = [reason for reason in healths[0]["reindex_reasons"] if reason in common]
    for label, health in zip(labels, healths, strict=True):
        reasons += [f"build {label}: {reason}" for reason in health["reindex_reasons"] if reason not in common]
    if any(health["status"] == "reindex_needed" for health in healths):
        status = "reindex_needed"
    elif any(health["status"] == "reindexing" for health in healths):
        status = "reindexing"
    else:
        status = "ready"
    behind = [(label, words) for label, health in zip(labels, healths, strict=True)
              if (words := _build_state_words(health))]
    incomplete = [label for label, health in zip(labels, healths, strict=True)
                  if health["manifest_verification"] == "partial"]
    # A partial build is current, thus it is not in `behind`: without this
    # sentence, "fully up to date" stands next to a `_warning` about it.
    incomplete_note = (
        f" The embeddings of {', '.join(incomplete)} are not complete: read _warning." if incomplete else ""
    )
    listing = "; ".join(f"{label}: {words}" for label, words in behind)
    if not behind and status == "reindexing":
        # The words of a project of one build, see `_build_health`.
        message = (
            f"Index is usable — background reindex in progress or scheduled, and the "
            f"index of each build is current ({len(builds)} builds). All queries "
            f"return accurate results.{incomplete_note}"
        )
    elif not behind and incomplete:
        message = f"Index is current ({len(builds)} builds).{incomplete_note}"
    elif not behind:
        message = f"Index is fully up to date ({len(builds)} builds)."
    elif not reasons and status == "reindexing":
        # No reason to read, and nothing for the caller to do but to wait
        # for the run: `_with_project_notes` puts that advice in front.
        message = f"Index is usable — background reindex in progress or scheduled ({listing}).{incomplete_note}"
    elif not reasons:
        # `_warning` only when a build has one: a pointer to an absent field
        # sends the caller to look for nothing.
        read = "builds and _warning" if any(health.get("_warning") for health in healths) else "builds"
        message = (
            f"{len(behind)} of {len(builds)} builds are not up to date — {listing}. "
            f"Queries work, and their answers can be out of date — run `fw-context index`. "
            f"Read {read} for each build."
        )
    else:
        message = (
            f"{len(behind)} of {len(builds)} builds are not up to date — {listing}. "
            f"Read reindex_reasons for the action, and builds for each build.{incomplete_note}"
        )
    message += _project_analysis_note(labels, healths, bg_running)
    message += _project_embedding_note(labels, healths, bg_running)
    result = {
        "status": status,
        "reindex_needed": status == "reindex_needed",
        "reindex_reasons": reasons,
        "stale": any(health["stale"] for health in healths),
        "index_message": message,
    }
    texts = {label: health["_warning"] for label, health in zip(labels, healths, strict=True) if health.get("_warning")}
    partial = [label for label, health in zip(labels, healths, strict=True)
               if label in texts and health["manifest_verification"] == "partial"]
    missing = [label for label in texts if label not in partial]
    parts = []
    if missing:
        # One text for these builds.  The text that sends the caller to
        # `reindex_reasons` wins: a plain 'fw-context index' is wrong for a
        # build whose reason names its own command, and `reindex_reasons`
        # of the project holds the reasons of each build.
        pool = [texts[label] for label in missing]
        chosen = next((text for text in pool if _REASON_COMMAND in text), pool[0])
        parts.append(f"Builds without full header tracking: {', '.join(missing)}. {chosen}")
    if partial:
        # Apart: their headers are tracked, and the text only asks the
        # caller to tell the user.
        parts.append(f"Builds whose embeddings are not complete: {', '.join(partial)}. {texts[partial[0]]}")
    if parts:
        result["_warning"] = " ".join(parts)
    return result


def get_build_info(
    project_root: Annotated[
        str | None, Field(description="Project root directory. Auto-detected from CWD if omitted.")
    ] = None,
    variant: Annotated[str | None, Field(description=VARIANT_DESCRIPTION)] = None,
    image: Annotated[str | None, Field(description=IMAGE_DESCRIPTION)] = None,
    fast: Annotated[
        bool, Field(description="When True (default), reuse the cached manifest "
        "hashes for the header check. Pass False to recompute them, which is far slower.")
    ] = True,
) -> dict:
    """Return the detail of ONE build: its identity, its health, its memory
    map and its defines.

    Read-only with the two exceptions that ``get_project_status`` also
    has: it removes the marker of a missing build when it finds that build
    again, and a ``reindex.pid`` or a ``reindex.pending`` of a process that
    no longer runs.  It spawns no subprocess.  On a project of more than one
    build, give the arguments of one build as the refusal lists them: the
    ``variant``, the ``image``, or the two.  ``get_project_status`` lists
    the builds in ``builds``.  Without the arguments the answer is an
    error that lists the builds.  A project of one build
    needs no selector.

    The health fields (``status``, ``reindex_state``, ``reindex_needed``, ``reindex_reasons``,
    ``index_message``, ``modified_files_count`` and the others) have the
    meaning that ``get_project_status`` gives them, for this build only.
    The version fields (``update_notice``, a restart after an upgrade) are
    in ``get_project_status``.

    About ``memory``: ``origin`` and ``length`` hold the expression the
    script writes, thus they differ by platform — an mbed script writes
    `0xefe00` and a Zephyr script writes `((673792) - 0xe6)`.
    ``origin_value`` and ``length_value`` hold the number, and both are
    None for an expression that names a symbol, such as
    `ORIGIN(RAM) + LENGTH(RAM)`.  A name that the link command defines
    with ``--defsym`` counts as a number: the STM32 Arduino core writes
    `LENGTH = LD_MAX_DATA_SIZE` and gets ``length_value`` 98304.  The end
    of a region is ``origin_value + length_value``.

    ``memory`` is empty for a build system that records no linker script,
    such as Keil or IAR.  A PlatformIO project records its link only in
    ``fw-context index --build``: SCons writes no file with the link
    command, thus the build asks ``pio run -t envdump``.  An empty list for
    PlatformIO means that no build recorded the link yet, and
    ``fw-context index --build`` is the step.  An empty list means "not
    recorded", never "no memory".

    ``defines`` holds only the names that EVERY translation unit of the
    build carries with the same value, so the tool never shows the defines
    of one file as the defines of the build.  ``defines_varying`` counts
    the names left out, thus a name absent from ``defines`` is either not
    defined at all or not defined everywhere.  Measured on the Mbed
    project, 27 names are in all 881 units and 59 in only some, where the
    three assembly files get a shorter set.

    This is the configuration the BUILD states, not every macro the
    preprocessor saw.  The second is three orders of magnitude larger —
    27800 distinct names on the STM32 project — and almost all of it comes
    from the headers and the compiler.  A Zephyr build keeps its real
    configuration in ``autoconf.h`` (740 `CONFIG_*` names) and passes few
    `-D` flags, so ``defines`` says little there and a great deal on an
    mbed build, where it holds `APPLICATION_ADDR`, `APPLICATION_SIZE`, and
    `CMSIS_VECTAB_VIRTUAL`.

    Args:
        project_root: Project root directory. Auto-detected from CWD if
            omitted.
        variant: Variant of the build (a board, or a PlatformIO
            environment). On a project of more than one build, give the
            arguments of one build as the refusal lists them: the
            variant, the image, or the two. fw-context does not select a
            build. One query answers for ONE build.
        image: Image of the build: a separate program, such as the
            application or the bootloader. Give it when the build has an
            image name.
        fast: When True (default), the header check reuses the cached
            manifest hashes.  False recomputes them.

    Returns:
        dict: {variant, image, board, config_hash, project_id, project_root,
        build_system, compile_commands, indexed_at (str — UTC),
        first_indexed_at, symbol_count, file_count, reference_count,
        modified_files_count, header_affected_tus, manifest_verification,
        analysis, description, vendor_paths, project_paths,
        effective_vendor_patterns (list[str] — the SQL LIKE patterns that
        this build really used to mark vendor code, for example
        ``["mbed-os/%"]``; empty when the manifest cannot be read.  The two
        lists above hold what the config asks for, this one what the index
        did), status, reindex_state (str or None — see get_project_status),
        reindex_needed, reindex_reasons, stale,
        index_message, entry_point (str — the `ENTRY()` of the linker
        script of the build, empty when no script names one), memory
        (list[dict] — the `MEMORY` regions of the build: {name, attributes,
        origin, length, origin_value, length_value, file_path, line}),
        defines (dict — the `-D` flags of the build), defines_varying
        (int), bg_reindex_running (bool), reindex_progress (str or None),
        vec_available (bool), vec_error (str, optional), _warning (str,
        optional), client_restart_required (bool, optional),
        client_restart_reason (str, optional)}

        A refusal of the selector, or an index with no build config, gives
        a dict that holds ``error`` alone.  A project that is not
        initialized, that has no index, or whose config file cannot be used
        (``"config_error"``) gets the answer of ``get_project_status``.
        ``config_warnings`` (a list) comes as in ``get_project_status``.
    """
    target = _index_target(project_root)
    if isinstance(target, dict):
        return target
    from ..shared.variants import resolve_build

    conn = _quick_open_readonly(target.db_path)
    try:
        outdated = outdated_index_reason(conn, target.bg_running)
        if outdated:
            return _outdated_state(target, outdated)
        config_hash, refusal = resolve_build(conn, target.project_id, target.cfg, variant or "", image or "")
    finally:
        conn.close()
    if refusal:
        return {"error": refusal}
    if config_hash is None:
        return {"error": f"No build config indexed for project at {target.root}."}

    def _query(conn, config_hash):
        cfg = conn.execute(
            "SELECT * FROM build_configs WHERE project_id = ? AND config_hash = ?",
            (target.project_id, config_hash),
        ).fetchone()
        if cfg is None:
            # A reindex removed the build between the two reads.
            return {"error": f"No build config indexed for project at {target.root}."}
        health = _build_health(conn, target, cfg, fast=fast, hash_cache={}, facts=_project_facts(conn, target))
        compile_commands = cfg["compile_commands_path"]
        # The `-D` flags of this build, read from ITS compile_commands.json.
        # Computed here and not stored: the read costs 19 ms on the Mbed
        # project with 881 units, measured, so a column would buy nothing
        # and a stale one would mislead.
        defines, varying = command_line_defines(Path(compile_commands) if compile_commands else Path())
        result = {
            "variant": cfg["variant"] or "",
            "image": cfg["image"] or "",
            "board": cfg["board"] or "",
            "config_hash": config_hash,
            "project_id": target.project_id,
            "project_root": str(target.root),
            "build_system": target.cfg.build.system or _detect_build_system(target.root),
            "compile_commands": compile_commands,
            **_public(health),
            "vendor_paths": target.cfg.index.vendor_paths,
            "project_paths": target.cfg.index.project_paths,
            # What the INDEX RUN applied, which is not always what the
            # config says today: the builder derives part of it, a variant
            # adds to it, and the config can have changed since.
            "effective_vendor_patterns": _effective_vendor_patterns(
                target.db_path.parent, config_hash, target.root
            ),
            "entry_point": get_entry_point(conn, config_hash),
            "memory": get_memory_regions(conn, config_hash),
            "defines": defines,
            "defines_varying": varying,
        }
        return result, health["_restart_reason"], health["_restart_format"]

    answer = get_executor(target.db_path).execute_sync(_query, config_hash)
    if isinstance(answer, dict):
        return answer
    result, restart_reason, restart_format = answer
    return _with_project_notes(result, target, restart_reason, restart_format)


def _build_state_words(health: dict) -> str:
    """Return the state of one build in a few words, for the message of a project of several builds.

    "" for a build that is current, else its ``reindex_state`` and the
    counts that the reasons do not give.  WHY not ``status``: it is
    "reindexing" while a run of the project is active, also for a build
    that the run does not change, and for a new source file when no run
    comes.  A message that said "reindexing" then made the caller wait for
    a run that did not exist.
    """
    state = health["reindex_state"]
    if state is None:
        return ""
    parts = []
    if health.get("_warning") and health["manifest_verification"] != "partial":
        # A build without a manifest is not up to date: the warning of the
        # project says so, and the message must agree.  Not "partial": its
        # embeddings are not a reason why the build is behind.
        parts.append(f"manifest verification {health['manifest_verification']}")
    if health["modified_files_count"]:
        parts.append(f"{health['modified_files_count']} modified file(s)")
    if health["header_affected_tus"]:
        parts.append(f"{health['header_affected_tus']} file(s) with stale headers")
    return f"{state} ({', '.join(parts)})" if parts else state


def _project_facts(conn, target: _IndexTarget) -> _ProjectFacts:
    """Return the facts of the project, from the build that ``fw-context index`` checks.

    WHY one time and against that build, and not in the health of each
    build:

    * The marker of a failed run is one file for the project, and the index
      run writes it for the build that it checks (``active_build``: the
      application of a build of several images).  A check of another build
      found that build present and removed the marker while the
      application was still missing, and every query answer then lost its
      ``build_warning``.
    * ``find_unindexed_sources`` reads the TU extensions and the build
      directories of the build that it starts from.  From the bootloader, a
      new ``.cpp`` of the application was no candidate.
    * HEAD is the same for each build.
    """
    from ...indexer.git_context import read_head
    from ..shared.variants import checked_build

    checked = checked_build(conn, target.project_id, target.cfg, target.root)
    if checked is None:
        # Both callers read a completed build first, thus only a reindex
        # that removed it in between gets here.  No build, no facts.
        return _ProjectFacts([], AutobuildState.WILL_BUILD, "", False, "")
    db_dir = target.db_path.parent
    cc_path = Path(checked["compile_commands_path"])
    problem = read_build_problem(db_dir)
    if problem is not None and problem.build_missing:
        checked_cc = checked_compile_commands(target.root, target.cfg, cc_path)
        if checked_cc is not None and not build_missing_reason(checked_cc):
            # The check ran and found the build (built outside of
            # fw-context): the text is out of date for every answer, not
            # only here.  Without a check the marker stays.
            clear_build_problem(db_dir)
            problem = None
    new_sources = find_unindexed_sources(conn, checked["config_hash"], target.root, cc_path)
    return _ProjectFacts(
        new_sources=new_sources,
        auto_state=_autobuild_state(target.root, target.cfg, target.db_path, new_sources),
        build_problem=problem.text if problem is not None else "",
        marker_build_missing=problem is not None and problem.build_missing,
        live_head=read_head(target.root),
    )


def _project_state(project_root: str | None, fast: bool) -> dict:
    """Return the state of :func:`get_project_status`, before the version fields."""
    target = _index_target(project_root)
    if isinstance(target, dict):
        return target

    def _query(conn, _config_hash):
        # Before the first read of a build row: a row of an older version
        # lacks the columns that the reads below take.
        outdated = outdated_index_reason(conn, target.bg_running)
        if outdated:
            return _outdated_state(target, outdated)
        builds = sorted(latest_builds(conn, target.project_id), key=build_label)
        if not builds:
            return {"error": f"No build config indexed for project at {target.root}."}
        hash_cache: dict[str, str] = {}
        facts = _project_facts(conn, target)
        # One build gives its whole health at the top level, as get_build_info
        # does.  The list of several builds shows no analysis field and no
        # count of references, thus it does not pay for the count.  Its
        # message names the builds whose symbols wait for the analysis.
        detail = len(builds) == 1
        healths = [
            _build_health(conn, target, build, fast=fast, hash_cache=hash_cache, facts=facts, detail=detail)
            for build in builds
        ]
        result = (
            _public(healths[0]) if len(builds) == 1
            else _project_health(builds, healths, target.bg_running)
        )
        result["builds"] = [_build_row(build, health) for build, health in zip(builds, healths, strict=True)]
        # A variant that the config declares and the index does not hold: no
        # query reaches it, and nothing else says that it exists.
        indexed = {build["variant"] or "" for build in builds}
        missing = [v.name for v in target.cfg.build.variants if v.name not in indexed]
        if missing:
            result["declared_not_indexed"] = [
                {"variant": name, "command": f"fw-context index --build --variant {name}"} for name in missing
            ]
        restart = next((health for health in healths if health["_restart_reason"]), None)
        return result, restart, get_db_schema_version(conn)

    answer = get_executor(target.db_path).execute_sync(_query, "")
    if isinstance(answer, dict):
        return answer
    result, restart, schema_version = answer
    result.update({
        "project_id": target.project_id,
        "project_root": str(target.root),
        "build_system": target.cfg.build.system or _detect_build_system(target.root),
        "schema_version": schema_version,
        "current_schema": CURRENT_SCHEMA_VERSION,
        "vendor_paths": target.cfg.index.vendor_paths,
        "project_paths": target.cfg.index.project_paths,
    })
    return _with_project_notes(
        result, target,
        restart["_restart_reason"] if restart else "",
        restart["_restart_format"] if restart else "",
    )


_REASON_COMMAND = "the command that reindex_reasons names"


def _with_config_warnings(result: dict, cfg: Config) -> dict:
    """Add ``config_warnings`` to *result* when ``load`` did not use a value of a config file.

    WHY in the status: a warning only in the log was the defect of the
    old "warning + default" rule, because nobody read it.  The caller
    tells the user, and ``fw-context doctor --fix --only config`` removes
    the values.
    """
    if cfg.config_warnings:
        result["config_warnings"] = list(cfg.config_warnings)
    return result


_EMBEDDING_PLAIN_ACTION = ": run `fw-context index`"


_PRE_INIT_CHECKS = frozenset({"pysqlite3", "sqlite-vec", "libclang-python", "libclang-so", "watchfiles", "tomli-w"})


@dataclass(frozen=True)
class _ProjectFacts:
    """The facts of the project that the health of each build reads, computed one time for a call."""

    # Source files that no build covers, and what happens to them.
    new_sources: list[str]
    auto_state: AutobuildState
    # The text of the marker of the last index run that stopped without an
    # index, after the check below; "" without one.
    build_problem: str
    # True when that marker says that the build is missing: a missing
    # database of a build is then the same fact, and the marker names it
    # with the command of the run.
    marker_build_missing: bool
    # The text of the file HEAD of the tree (`git_context.read_head`): a
    # branch, or the commit of a detached HEAD; "" without git.
    live_head: str


def _not_built_error(target: Path) -> dict:
    """Return the error for *target* when no build of the project compiles it.

    The whole path, not target.name.  The basename made the refusal name
    something the caller never wrote: "../../../../etc/passwd" came back as
    "passwd not found in compile_commands.json", which reads as a fact about
    a file of the project and says nothing about the path that was refused.
    """
    return {"error": f"{target} is not built by this project — it has no entry in compile_commands.json, and no indexed translation unit includes it."}
