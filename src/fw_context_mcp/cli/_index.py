"""``fw-context index`` — build or rebuild the libclang symbol index.

This command parses ``compile_commands.json`` with libclang and stores
function/class/enum metadata, cross-references, embeddings, and optional
LLM analysis in an SQLite database.

Indexing phases run in order:
1. **Build** (if ``--build`` or missing cc.json) — generate
   compile_commands.json from the detected build system.
2. **Validate** — check cc.json completeness, detect stale entries,
   fix path separators and other common issues.
3. **Parse** — libclang extraction of symbols from every translation unit.
4. **Refs** — cross-reference indexing: callers, callees, indirect edges.
5. **Embeddings** — generate vector embeddings for semantic search.
6. **Analyze** (optional) — LLM-generated summaries for each symbol.

WHY: The index is the core data structure of fw-context.  Without a
complete and accurate index, every search, call-graph query, and
semantic search returns wrong or empty results.  This command must
handle incremental updates, stale detection, background conflict
resolution, and graceful degradation when optional phases fail.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..indexer import autobuild
from ..mcp.shared.pid_file import PidFile
from ..platform import ProcessIdentity, StopOutcome, stop_marker_process
from ..platform import process as _process
from ..platform import write_pid_marker
from ..utils import (
    CC_OUTPUT_REL,
    SAFE_EXCEPT,
    autobuild_dir,
    build_dir_patterns_with_fw_context,
)
from . import VerboseFormatter

if TYPE_CHECKING:
    from ..indexer.build import BuildConfig

log = logging.getLogger(__name__)


# ── Automatic build for a source file the build system never saw ──
# A new .c file has no translation unit: it is absent from
# compile_commands.json, and only a build puts it there.  A plain reindex
# skips it without a word, thus fw-context runs the build itself.
#
# The markers and the state machine live in indexer/autobuild.py, because
# mcp/handlers/maintenance.py needs the same answers to tell the caller what
# will happen, and it cannot import from cli/.


def _plan_auto_build(
    project_root: Path,
    db_path: Path,
    cfg,
    detected_system: str | None,
    *,
    background: bool,
    args: argparse.Namespace | None = None,
) -> tuple[list[str], BuildConfig | None, str]:
    """Return ``(the keys of this build, its config, a line to log)``.

    An empty list means "do not build", and the config is then None.  The
    list is non-empty only when all of these hold:

    1. An index exists.  Without one there is nothing to compare against.
    2. Something a build can repair and a reindex cannot.  Three such
       things, and each one is enough:

       * The build is gone: compile_commands.json or a ``directory`` of its
         entries does not exist (``autobuild.build_missing_reason``).  Only
         for a *background* run: a run of the user builds as ``--build``
         does (``_build_if_missing`` and the default path of
         ``_resolve_compile_commands``).
       * Source files sit on disk that compile_commands.json does not cover.
         Such a file has no translation unit, so a reindex skips it.
       * The tree has a different checkout than a build of the index (a
         branch, a tag or a commit, ``_checkout_moves``).
         compile_commands.json is a build artifact of the checkout it was
         generated on and carries its file list AND its compiler flags.
         Measured on the Mbed project, a switch from 4.15.3 to 4.15.1 left
         two generated zcbor sources listed in it and absent from the tree;
         a reindex reads the same file again.

    3. The backend may build in the background — it writes its output into
       the build tree of fw-context, or it compiles nothing.  See
       ``builders.background_build_safe``.

    The KEYS name the trigger: ``"build-missing"``, the source paths, or
    ``"checkout:<label>"``.  An earlier failure does not block the build: the
    run that fails again writes the error for the MCP answers
    (``autobuild.record_problem``), and that is what makes the user repair
    it.

    Condition 3 is the important one.  The build runs while the user works,
    possibly while an IDE builds the same project, and fw-context cannot
    lock the build of the IDE.

    The config that comes back is ``cfg.build``, the config the build uses.
    *args* limits the checkout check to the builds that the run indexes
    (``_checkout_moves``).
    """
    if not db_path.exists():
        return [], None, ""

    from ..indexer.build import checked_compile_commands
    from ..indexer.build_layout import BuildLayout
    from ..mcp.shared.stale import find_unindexed_sources

    if not _may_build_in_background(cfg, detected_system):
        return [], None, ""
    candidate = cfg.build
    # Where the build goes, for the log line.  A project with variants
    # builds each of them, each into its own directory.
    target = BuildLayout(project_root).root if candidate.variants else BuildLayout(project_root).out_dir("")

    from ..indexer.db import open_db

    conn = open_db(db_path)
    try:
        active = _checked_build(conn, project_root, cfg)
        if not active or not active["compile_commands_path"]:
            return [], None, ""
        # The build is gone as a whole: no file list and no compiler answer
        # can come from it, thus it outranks the two other reasons.  The
        # file is the one that this run reads and that the index came from,
        # as `get_project_status` checks it (`build.checked_compile_commands`).
        checked = (
            checked_compile_commands(project_root, cfg, Path(active["compile_commands_path"]))
            if background else None
        )
        missing = autobuild.build_missing_reason(checked) if checked is not None else ""
        if missing:
            return ["build-missing"], candidate, (
                f"{missing} — running a build into {target}"
            )
        new_sources = find_unindexed_sources(
            conn,
            active["config_hash"],
            project_root,
            Path(active["compile_commands_path"]),
        )
        moves = _checkout_moves(conn, str(active["project_id"]), project_root, args)
    finally:
        conn.close()

    # The checkout first: it makes compile_commands.json wrong as a whole,
    # while an uncovered source makes it incomplete.  A build repairs both,
    # so the more complete reason is the one worth logging.  Each build is
    # checked: one run builds every build, and a build that the run did not
    # check kept the old checkout.
    if moves:
        _label, indexed, live = moves[0]
        return [f"checkout:{live}"], candidate, (
            f"checkout changed from {indexed} to {live} — "
            f"compile_commands.json belongs to the old checkout, "
            f"running a build into {target}"
        )

    if not new_sources:
        return [], None, ""
    listed = ", ".join(new_sources[:3])
    more = f" and {len(new_sources) - 3} more" if len(new_sources) > 3 else ""
    return new_sources, candidate, (
        f"{len(new_sources)} source file(s) are missing from "
        f"compile_commands.json ({listed}{more}) — running a build into "
        f"{target}"
    )


def _checked_build(conn, project_root: Path, cfg):
    """Return the build that the checks of an index run read, or None without one.

    One build (``active_build``): the application of a build of several
    images, else the newest build.  The newest build only when no build
    system names the application: the newest one is the program that the
    last run did last, a bootloader after a failed application.  The MCP
    server checks every build (``get_project_status``).
    """
    from ..config import derive_project_id
    from ..indexer.db import get_active_config
    from ..mcp.shared.variants import active_build

    project_id = derive_project_id(project_root)
    row, _refusal = active_build(conn, project_id, cfg, project_root)
    return row if row is not None else get_active_config(conn, project_id)


def _indexed_compile_commands(project_root: Path, db_path: Path) -> Path | None:
    """Give the compile_commands.json of the active index, or None without one.

    ``build.checked_compile_commands`` needs it: only the file that the
    index came from is checked.  A database that cannot be read counts as no
    index; the run that opens it next gives the real error.
    """
    if not db_path.exists():
        return None
    from ..config import load as load_config
    from ..indexer.db import open_db

    try:
        conn = open_db(db_path)
    except SAFE_EXCEPT:
        log.debug("could not read the active compile_commands.json", exc_info=True)
        return None
    try:
        active = _checked_build(conn, project_root, load_config(project_root=project_root))
    finally:
        conn.close()
    if not active or not active["compile_commands_path"]:
        return None
    return Path(active["compile_commands_path"])


def _build_if_missing(
    args: argparse.Namespace, project_root: Path, cfg, detected_system: str | None,
    indexed: Path | None,
) -> None:
    """Turn ``--build`` on when a build directory of the default compile_commands.json is gone.

    *indexed* is the file of the active index (``_indexed_compile_commands``).
    A missing compile_commands.json is not handled here: the default path of
    ``_resolve_compile_commands`` builds then, in the build directory of the
    user, and it gives its own error when it cannot.  When fw-context cannot
    run the build of the project (a stub such as STM32CubeIDE, see
    ``build.can_run_build``), nothing is turned on, and
    ``_refuse_without_build`` stops the run.
    """
    from ..indexer.build import can_run_build, checked_compile_commands

    cc = checked_compile_commands(project_root, cfg, indexed)
    if cc is None or not cc.exists():
        return
    reason = autobuild.build_missing_reason(cc)
    if reason and can_run_build(cfg.build, cfg.build.system or detected_system):
        args.build = True
        print(f"{reason} — running the build (--build)", file=sys.stderr)


def _refuse_without_build(
    project_root: Path, cfg, detected_system: str | None, indexed: Path | None,
    *, at_the_end: bool = False,
) -> str:
    """Give the error that stops a run whose build is not there, or "".

    Called when the run builds nothing, and at the end of a run that ended
    well (*at_the_end*).  *indexed* is the file of the active index.  Before
    the run, a missing compile_commands.json is left to
    ``_resolve_compile_commands``, which builds or gives its own error.  At
    the end, nothing builds any more, and a build of fw-context keeps its
    database in its output directory, thus a missing database is a build
    that went away during the run.  The text names what the user must do: a
    build that fw-context can run is ``fw-context index --build``; any other
    build runs outside of fw-context (an IDE), and ``fw-context index``
    follows it.
    """
    from ..indexer.build import can_run_build, checked_compile_commands

    cc = checked_compile_commands(project_root, cfg, indexed)
    if cc is None or (not at_the_end and not cc.exists()):
        return ""
    reason = autobuild.build_missing_reason(cc)
    if not reason:
        return ""
    if can_run_build(cfg.build, cfg.build.system or detected_system):
        return (f"{reason}. fw-context does not index without the build, because each unit would get "
                "no answer of its compiler. Run 'fw-context index --build'.")
    return (f"{reason}, and fw-context cannot run the build of this project. fw-context does not "
            "index without the build. Run the build outside of fw-context (for example in the "
            "IDE), then run 'fw-context index'.")


def _run_selects(row, args: argparse.Namespace | None, has_variants: bool) -> bool:
    """Say whether a run with *args* indexes the build of *row* (``--variant``, ``--variants``, ``--image``, ``--exclude-image``).

    The same filters as ``_select_variants`` and ``_filter_images``:
    ``--variant`` wins over ``--variants``, and a project without variants
    (*has_variants* False) ignores both, as its run does.
    """
    if args is None:
        return True
    variant = str(row["variant"] or "")
    image = str(row["image"] or "")
    requested: set[str] | None = None
    if has_variants and getattr(args, "variant", None):
        requested = {args.variant}
    elif has_variants and getattr(args, "variants", None):
        requested = {v.strip() for v in args.variants.split(",") if v.strip()}
    if requested is not None and variant not in requested:
        return False
    if getattr(args, "image", None) and image != args.image:
        return False
    return not (image and image in set(getattr(args, "exclude_image", None) or []))


def _checkout_moves(
    conn: sqlite3.Connection, project_id: str, project_root: Path, args: argparse.Namespace | None = None,
) -> list[tuple[str, str, str]]:
    """Return ``(build label, indexed checkout, live checkout)`` for each build of another checkout.

    Each completed build that a run with *args* indexes (all of them
    without *args*), compared with the HEAD of the tree
    (``git_context.head_moved``).  WHY only those: a run of one image
    builds and records that image, and a build that the run leaves out
    kept the old checkout.  It started the build of the next run of that
    image again, and again, without end.
    """
    from ..indexer.git_context import head_moved, read_head, stored_head
    from ..mcp.shared.stale import build_label, latest_builds

    live = read_head(project_root)
    rows = latest_builds(conn, project_id)
    # A build with a variant name means a project with variants.
    has_variants = any(row["variant"] for row in rows)
    moves = []
    for row in rows:
        if not _run_selects(row, args, has_variants):
            continue
        description = str(row["description"] or "") if "description" in row.keys() else ""
        indexed, now = head_moved(stored_head(row), description, live, project_root)
        if indexed:
            moves.append((build_label(row), indexed, now))
    return moves


def _records_checkout(
    args: argparse.Namespace, cfg, detected_system: str | None, project_root: Path, project_id: str,
    db_path: Path, explicit_cc: bool,
) -> bool:
    """Say whether this run records the checkout of the tree on its builds (``runner.run``).

    True when one of these is true:

    * the run builds (``--build``, or the plan turned it on);
    * no build of the run has another checkout;
    * fw-context does not make the database (the user gives it, or
      fw-context cannot run the build: a stub such as STM32CubeIDE, or a
      build system that no builder knows), and the user started the run.
      No run of fw-context can build that database, thus only the user
      can give the database of the new checkout, and a run of the user
      says that the user did.  A database from CI or from git keeps an
      older time, thus the time of the file cannot decide for the user;
    * fw-context does not make the database, the run is in the background
      (the daemon), and the database is newer than the checkout
      (:func:`_database_after_checkout`).
      The daemon starts a run for each change after a checkout, also
      before the user builds again.

    False after a checkout that this run does not build, for a database
    that fw-context makes: compile_commands.json still belongs to the old
    checkout, and the
    recorded checkout keeps the reason in each answer until a build.
    Before this rule a plain run recorded the new branch and the reason
    went away, while the index still read the old database.  That was also
    true for a database that fw-context does not make: each plain run, the
    run of the daemon too, recorded the checkout.  And for a build system
    that no builder knows, no run recorded it, thus the reason never went
    away.

    A project of variants records only the builds that the run made
    (``built_pairs`` in ``_run_multi``): each variant reads its own
    database, and a file of the user is not one of them.  A run that builds
    on another path (a missing or stale database) records the build that it
    made (``args.build_ran``, see ``_run_single`` and ``_run_multi``).
    """
    from ..indexer.build import can_run_build, default_compile_commands

    if getattr(args, "build", False) or not db_path.exists():
        return True
    from ..indexer.db import open_db

    conn = open_db(db_path)
    try:
        moved = _checkout_moves(conn, project_id, project_root, args)
    finally:
        conn.close()
    if not moved:
        return True
    if cfg.build.variants:
        return False
    fw_context_makes_database = not explicit_cc and can_run_build(
        cfg.build, cfg.build.system or detected_system,
    )
    if fw_context_makes_database:
        return False
    if not getattr(args, "background", False):
        return True
    given = getattr(args, "compile_commands", None)
    if given:
        database = Path(given) if Path(given).is_absolute() else (project_root / given).resolve()
    else:
        database = default_compile_commands(project_root, cfg)
    return _database_after_checkout(database, project_root)


def _database_after_checkout(database: Path, project_root: Path) -> bool:
    """Say whether *database* is newer than the last checkout of the tree at *project_root*.

    The time of the file HEAD is the time of the last checkout: git writes
    HEAD for a checkout, and a commit on a branch changes the branch and not
    HEAD.  A rebase, a commit on a detached HEAD and a checkout of the same
    branch also write HEAD: the answer is then False until the user builds
    again, and the reason stays longer than necessary, never shorter.

    WHY ``>`` and not ``>=``: on a file system with a coarse time (FAT has 2
    s), a database written just before the checkout can have the same time.
    WHY ``lstat``: with ``core.preferSymlinkRefs`` HEAD is a link to the ref
    of the branch, and each commit changes the time of that ref.

    A tree without git has no checkout, thus True.  A database or a HEAD
    that cannot be read answers False, thus the reason stays until the next
    run.
    """
    from ..indexer.git_context import head_path

    head = head_path(project_root)
    if head is None:
        return True
    try:
        return database.stat().st_mtime > head.lstat().st_mtime
    except OSError:
        return False


def _note_build(args: argparse.Namespace) -> None:
    """Remember that this run built the database that it indexes, on a path other than ``--build``.

    The run then records the checkout of the tree (``_records_checkout``):
    the database belongs to it.
    """
    args.build_ran = True


def _may_build_in_background(cfg, detected_system: str | None) -> bool:
    """Say if a background run may start the build of this project.

    The backend decides, see ``builders.background_build_safe``: it must
    write its output into the build tree of fw-context, or compile nothing.
    A ``[build] command`` never may: it runs a build that the user wrote,
    and fw-context does not know where that build puts its output.
    """
    from ..indexer.builders import background_build_safe, registry

    if cfg.build.command:
        return False
    system = cfg.build.system or detected_system
    builder_cls = registry.get(system) if system else None
    return builder_cls is not None and background_build_safe(builder_cls(), cfg.build)


def _resolve_compile_commands(
    args: argparse.Namespace,
    project_root: Path,
    cfg,
    detected_system: str | None,
    bg: bool,
) -> tuple[Path | None, bool]:
    """Resolve the compile_commands.json path.

    Returns (path, is_explicit) or (None, False) on fatal error.
    Caller checks for None → return 1.

    Three resolution strategies, tried in order:
    1. ``--build`` — force a fresh build, ignoring any existing file.
    2. Explicit path (positional arg) — use the provided file.
    3. Default — reuse existing cc.json from config; build if missing.

    WHY: Users should not need to know where their build system stores
    compile_commands.json.  The default path is discovered from the
    detected build system and stored in config.  The explicit path
    option exists for edge cases (CI pipelines, unsupported build
    systems) where the user manages cc.json themselves.
    """
    explicit_cc = bool(args.compile_commands)

    if args.build:
        # A background run builds only with a backend that may build on its
        # own (`builders.background_build_safe`): fw-context turned the build
        # on itself, after it found a missing build, a source file that
        # compile_commands.json does not cover, or another checkout, and the
        # user did not ask for it.
        if bg and not _may_build_in_background(cfg, detected_system):
            print("error: --build and --background are mutually exclusive", file=sys.stderr)
            return None, False
        from ..indexer.build import generate_compile_commands

        build_cfg = cfg.build
        if args.no_clean:
            build_cfg.clean = False
        try:
            return generate_compile_commands(project_root, build_cfg), False
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None, False

    if explicit_cc:
        cc = Path(args.compile_commands)
        if not cc.is_absolute():
            cc = (project_root / cc).resolve()
        if not cc.exists():
            print(f"error: {cc} not found", file=sys.stderr)
            print("  Run 'fw-context index --build' to build and index automatically.", file=sys.stderr)
            return None, False
        from ..indexer.build import check_completeness
        for warning in check_completeness(cc, project_root):
            print(f"warning: {warning}", file=sys.stderr)
        return cc, True

    # Default: reuse existing, build only if missing
    from ..indexer.build import (
        check_completeness,
        generate_compile_commands,
        resolve_reuse_compile_commands,
    )

    cc = resolve_reuse_compile_commands(project_root, cfg.index.compile_commands)
    if not cc.exists():
        if bg:
            print(f"error: compile_commands.json not found at {cc}", file=sys.stderr)
            print("  Background reindex requires an existing compile_commands.json.", file=sys.stderr)
            print("  Run 'fw-context index --build' first to set up the project.", file=sys.stderr)
            return None, False
        print("compile_commands.json not found, running build...", file=sys.stderr)
        try:
            return generate_compile_commands(project_root, cfg.build), False
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return None, False

    for warning in check_completeness(cc, project_root):
        print(f"warning: {warning}", file=sys.stderr)
    return cc, False


def _validate_and_fix_artifacts(
    compile_commands: Path,
    project_root: Path,
    detected_system: str | None,
    cfg,
    bg: bool,
    explicit_cc: bool,
    args: argparse.Namespace,
) -> tuple[Path | None, list[str] | None, bool]:
    """Validate build artifacts and optionally auto-fix.

    Returns (compile_commands, build_dir_patterns, ok).
    compile_commands may be updated if a rebuild was triggered.
    Caller returns 1 when ok is False.

    Checks performed:
    * Staleness — cc.json older than project sources.
    * Completeness — all source files present in cc.json entries.
    * Path correctness — Windows backslashes, mixed separators.

    WHY: A stale compile_commands.json produces an index that silently
    misses new or renamed files.  Auto-detection and auto-rebuild
    prevent the user from debugging "symbol not found" errors caused
    by an outdated cc.json rather than actual code issues.
    """
    if not detected_system:
        return compile_commands, None, True

    from ..indexer.build import generate_compile_commands
    from ..indexer.builders import registry as builder_registry
    from ..indexer.validator import is_compile_commands_stale, validate_and_fix

    builder_cls = builder_registry.get(detected_system)
    if builder_cls is None:
        return compile_commands, None, True

    builder_instance = builder_cls()
    build_dir_patterns = build_dir_patterns_with_fw_context(
        builder_instance.get_build_dir_patterns(project_root)
    )

    if not bg:
        stale, stale_reasons = is_compile_commands_stale(compile_commands, project_root)
        if stale:
            if explicit_cc:
                print(f"warning: explicit compile_commands.json is stale ({'; '.join(stale_reasons)})", file=sys.stderr)
            else:
                print(f"compile_commands.json is stale ({'; '.join(stale_reasons)}), rebuilding...")
                try:
                    compile_commands_new = generate_compile_commands(project_root, cfg.build)
                    print(f"Generated: {compile_commands_new}")
                    compile_commands = compile_commands_new
                except RuntimeError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    print("  Run 'fw-context index --build' to retry.", file=sys.stderr)
                    return compile_commands, None, False

    issues = validate_and_fix(compile_commands, project_root, builder_instance, cfg.build, fix=not bg)
    errors = [i for i in issues if i.severity == "error"]
    for w in [i for i in issues if i.severity == "warning"]:
        print(f"warning: {w.message}", file=sys.stderr)

    if errors:
        for e in errors:
            print(f"error: {e.message}", file=sys.stderr)
        if not bg and not explicit_cc and not args.build:
            print("Rebuilding to fix issues...")
            try:
                compile_commands_new = generate_compile_commands(project_root, cfg.build)
                print(f"Generated: {compile_commands_new}")
                issues = validate_and_fix(compile_commands_new, project_root, builder_instance, cfg.build, fix=True)
                errors = [i for i in issues if i.severity == "error"]
                for i in issues:
                    if i.severity == "warning":
                        print(f"warning: {i.message}", file=sys.stderr)
            except RuntimeError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return compile_commands, None, False
        if errors:
            for e in errors:
                print(f"error: {e.message}", file=sys.stderr)
            if bg:
                print("Run 'fw-context index' to resolve issues.", file=sys.stderr)
            else:
                print("Run 'fw-context index --build' to rebuild and fix issues.", file=sys.stderr)
            return compile_commands, None, False

    return compile_commands, build_dir_patterns, True


def _pid_is_fw_context_reindexer(pid: int) -> bool:
    """Verify that *pid* is a BACKGROUND fw-context index run.

    WHY the identity check at all: operating systems recycle PIDs.  A stale
    ``reindex.pid`` may name a process that has nothing to do with
    fw-context, and terminating it would take out an unrelated service.

    WHY the argv and not the process name: this used to compare
    ``/proc/<pid>/comm`` against ``("fw-context", "python", "python3")``.  A
    virtualenv interpreter is named for its version — ``python3.14`` here —
    so the comparison never matched and the function always returned False.
    The takeover it guards therefore never happened, and two index runs could
    write the same database side by side.  Observed exactly that: a run
    started at 16:42 was still going when a second one started at 17:04.

    W1: the argv read goes through the platform ``ProcessService`` —
    ``/proc/<pid>/cmdline`` on POSIX, the PEB on win32 (where ``/proc``
    does not exist and the check used to fail closed for EVERY pid,
    including real reindexers).  ``None`` (cannot read) means False:
    an unreadable process is never claimed to be a reindexer.

    Only ``--background`` runs are killable.  The daemon spawns those and
    will start another when it needs one; a foreground run belongs to a
    person and is excluded through :func:`index_run_lock` instead of being
    terminated under them.
    """
    argv = _process.command_line(pid)
    if not argv:
        return False
    if not any(arg.endswith(("fw-context", "fw_context_mcp.cli", "fw_context_mcp")) for arg in argv):
        return False
    if "index" not in argv:
        return False
    return "--background" in argv

def _kill_bg_reindex(db_path: Path) -> None:
    """Terminate a running BACKGROUND index run so that its lock drops.

    Routes through the platform's ONE marker-named kill sequence
    (:func:`fw_context_mcp.platform.stop_marker_process` — read-marker →
    verify-identity → terminate → wait, W5) and terminates through the
    ``ProcessService`` — POSIX SIGTERM→SIGKILL, win32 TerminateProcess
    (win32 has no SIGKILL attribute; the pre-W1 ``os.kill(pid,
    signal.SIGKILL)`` here was a latent AttributeError).  Two concurrent
    writers on one SQLite database corrupt it, and SQLITE_BUSY retries
    are not mutual exclusion on a long write.

    Authorization rules (D2, enforced by the shared helper and the
    service):

    - a JSON marker whose ``create_token`` matches the live process
      authorizes the kill — pid reuse cannot match;
    - a legacy bare-int marker (pre-W1) carries no token: coordination
      only, never kill-authorizing — logged and ignored;
    - a marker naming THIS process or its parent is refused;
    - the background-argv recognition stays as the call-site policy
      (``authorize``): only ``--background`` runs are killable.  There is
      deliberately NO role filter on this marker: ``_claim_index``
      labels every CLI run ``foreground-index`` (including the
      daemon-spawned ``--background`` child, which claims its own marker
      through the same flow), so the argv gate — not the role string —
      is what keeps kill authority narrow.

    Writes nothing.  To claim the index is a separate step, and only the
    process that won index_run_lock may do it — see _claim_index().  A run
    that writes the pause marker and then LOSES the lock leaves a marker
    that names a live process, and the run that WON reads that marker as
    "somebody took the index over" and abandons itself.

    Deliberately does NOT clean up the pause marker of the run it just
    killed.  PidFile.is_active answers False for a dead PID and
    raise_if_superseded ignores such a marker — its own docstring says so.
    To unlink a marker that belongs to another process is the same defect
    this function exists to remove, seen from the other side.
    """
    reindex_pid_file = db_path.parent / "reindex.pid"
    outcome = stop_marker_process(
        reindex_pid_file,
        service=_process,
        grace_s=5.0,
        # The argv gate is pid-based policy; the helper hands it the full
        # marker identity (so a future gate can also weigh role/token).
        authorize=lambda marker: _pid_is_fw_context_reindexer(marker.pid),
    )
    if outcome is not StopOutcome.STOPPED:
        log.debug(
            "background reindex not terminated via %s (%s)",
            reindex_pid_file, outcome.value,
        )


def _claim_index(db_dir: Path) -> None:
    """Write the pause marker and the pid marker.

    Call this function ONLY while the caller holds index_run_lock.  The pause
    marker stops the file-watcher daemon from starting a background run, and
    the pid marker tells every other process which run owns the index.  Both
    are claims, and a process that lost the lock owns nothing.

    The pid marker carries this process's start-time identity (D2) — the
    token a concurrent ``_kill_bg_reindex`` must verify before terminating.
    The pause marker stays a bare pid: it is coordination-only by design.

    Creates no directory: index_run_lock already does that before it takes
    the lock.
    """
    PidFile(db_dir / "reindex.pause").write()
    identity = _process.current_identity("foreground-index")
    write_pid_marker(db_dir / "reindex.pid", ProcessIdentity(
        pid=identity.pid,
        create_token=identity.create_token,
        role="foreground-index",
        recorded_at=identity.recorded_at,
    ))


def _release_index(db_dir: Path) -> None:
    """Drop the markers this process wrote, and only those.

    unlink_if_ours checks the PID, so a marker another run owns survives.
    """
    PidFile(db_dir / "reindex.pid").unlink_if_ours()
    PidFile(db_dir / "reindex.pause").unlink_if_ours()


def _post_index_optimize(
    db_path: Path,
    project_root: Path,
    project_id: str,
    detected_system: str | None,
    args: argparse.Namespace,
) -> None:
    """Run PRAGMA optimize and update the global project registry."""
    try:
        import sqlite3 as _sqlite3

        _opt_conn = _sqlite3.connect(str(db_path))
        _opt_conn.execute("PRAGMA optimize")
        _opt_conn.close()
    except _sqlite3.Error:
        log.debug("PRAGMA optimize failed for %s", db_path, exc_info=True)

    from ..config.global_db import open_global_db, upsert_project_registry

    _ptype = detected_system or "unknown"
    _gconn = open_global_db()
    try:
        upsert_project_registry(
            _gconn, project_id, getattr(args, "name", None) or project_root.name, _ptype, str(project_root)
        )
    finally:
        _gconn.close()


def _select_variants(variants: list, args: argparse.Namespace) -> list:
    """Return the variants selected by --variant/--variants CLI filters."""
    if not variants:
        return []
    requested: set[str] | None = None
    if getattr(args, "variant", None):
        requested = {args.variant}
    elif getattr(args, "variants", None):
        requested = {v.strip() for v in args.variants.split(",") if v.strip()}
    if requested is None:
        return list(variants)
    selected = [v for v in variants if v.name in requested]
    missing = requested - {v.name for v in selected}
    for m in sorted(missing):
        print(f"error: variant '{m}' not found in [[build.variants]]", file=sys.stderr)
    return selected


def _find_variant(variants: list, name: str):
    for v in variants:
        if v.name == name:
            return v
    return None


def _effective_board(build_cfg, variant, image: str) -> str:
    """Resolve the concrete board per-(variant, image).

    Per-image override wins (FLPR → cpuflpr), else the variant default board,
    else the shared ``[build] board``.
    """
    for img in variant.images:
        if img.name == image and img.board:
            return img.board
    return variant.board or build_cfg.board or ""


def _variant_build_dir(variant, build_cfg) -> str:
    """Return the per-variant build output directory (relative)."""
    return variant.build_dir or build_cfg.build_dir or f"build/{variant.name}"


def _filter_images(cc_list: list, args: argparse.Namespace) -> list:
    """Apply --image (inclusive) and --exclude-image (exclusive) index filters."""
    include = getattr(args, "image", None)
    exclude = set(getattr(args, "exclude_image", None) or [])
    out = []
    for item in cc_list:
        _, image, _, _ = item
        if image and image in exclude:
            continue
        if include and image != include:
            continue
        out.append(item)
    return out


def _discover_existing_cc(project_root, variants: list, build_cfg) -> list:
    """Discover existing ``compile_commands.<variant>[.<image>].json`` copies."""
    found: list = []
    for variant in variants:
        build_dir = _variant_build_dir(variant, build_cfg)
        bd = project_root / build_dir
        # per-image layout: <build_dir>/<image>/compile_commands.json (NCS 3.2.3)
        if bd.is_dir():
            for sub in sorted(bd.iterdir()):
                cc = sub / "compile_commands.json"
                if cc.exists():
                    found.append((variant.name, sub.name, cc, _effective_board(build_cfg, variant, sub.name)))
        # non-sysbuild single-image layout: compile_commands.<variant>.json
        single = (project_root / CC_OUTPUT_REL).with_name(f"compile_commands.{variant.name}.json")
        if single.exists():
            found.append((variant.name, "", single, _effective_board(build_cfg, variant, "")))
        if not bd.is_dir() and not single.exists():
            print(f"error: no build artifacts for variant '{variant.name}' — run 'fw-context index --build'", file=sys.stderr)
    return found


def _run_multi(
    args,
    cfg,
    project_root,
    project_id,
    db_path,
    detected_system,
    run_kwargs,
) -> int:
    """Orchestrate indexing of all (variant, image) builds.

    Builds each variant (sysbuild via ``build_multi``, else per-variant
    ``build()``), indexes each (variant, image) with deferred FTS/cleanup,
    then runs the FTS rebuild and per-(variant, image) retention once at the
    end (§5.8).  A failure in one build does not abort the others.
    """
    from ..exit_codes import EXIT_SUPERSEDED
    from ..indexer._postprocess import cleanup_old_builds_multi
    from ..indexer.build import build_variant_config, generate_compile_commands
    from ..indexer.builders import registry as builder_registry
    from ..indexer.db import open_db, rebuild_files_fts, rebuild_fts, rebuild_macros_fts
    from ..indexer.runner import IndexSuperseded, run

    build_cfg = cfg.build
    variants = _select_variants(build_cfg.variants, args)
    if not variants:
        print("error: no variants selected", file=sys.stderr)
        return 1

    system = build_cfg.system or detected_system
    builder_cls = builder_registry.get(system) if system else None
    builder = builder_cls() if builder_cls else None

    cc_list: list[tuple[str, str, Path, str]] = []

    if args.build:
        if builder is not None and hasattr(builder, "build_multi") and build_cfg.sysbuild:
            for variant, image, path in builder.build_multi(project_root, build_cfg):
                v = _find_variant(variants, variant)
                board = _effective_board(build_cfg, v, image) if v else ""
                cc_list.append((variant, image, path, board))
        else:
            for variant in variants:
                vcfg = build_variant_config(build_cfg, variant)
                if build_cfg.isolated_build_dir:
                    # One directory per variant.  Every variant has its own
                    # output directory, thus a shared one would make the
                    # variants overwrite each other.
                    vcfg.isolated_build_dir = autobuild_dir(variant.name)
                try:
                    path = generate_compile_commands(project_root, vcfg)
                except RuntimeError as exc:
                    print(f"error: variant '{variant.name}': {exc}", file=sys.stderr)
                    continue
                cc_list.append((variant.name, "", path, _effective_board(build_cfg, variant, "")))
    else:
        cc_list = _discover_existing_cc(project_root, variants, build_cfg)

    cc_list = _filter_images(cc_list, args)

    if args.no_index:
        print(f"Built {len(cc_list)} build(s) — skipping indexing (--no-index)")
        return 0

    if not cc_list:
        print("error: no builds to index", file=sys.stderr)
        return 1

    touched_pairs: list[tuple[str, str]] = []
    for variant_name, image, cc_path, board in cc_list:
        v = _find_variant(variants, variant_name)
        env = dict(build_cfg.env)
        if v is not None:
            env.update(v.env)
        build_dir_patterns = (
            build_dir_patterns_with_fw_context([f"{_variant_build_dir(v, build_cfg)}/"])
            if v is not None else None
        )
        # The path layers are summed per build, not once for the command:
        # two variants may vendor different in-tree trees.  run_kwargs holds
        # the [index] layer (or the CLI flag, which replaces it).
        per_build = dict(run_kwargs)
        idx = v.index_overrides if v is not None else {}
        for key in ("vendor_paths", "project_paths"):
            extra = list(idx.get(key) or [])
            per_build[key] = _layered_paths(list(run_kwargs[key]), extra)
            if extra:
                print(
                    f"  {variant_name}: {key} = "
                    f"{len(run_kwargs[key])} from [index] + {len(extra)} from variant"
                )
        try:
            ch = run(
                compile_commands=cc_path,
                db_path=db_path,
                variant=variant_name,
                image=image,
                board=board,
                build_env=env,
                build_dir_patterns=build_dir_patterns,
                defer_fts=True,
                defer_cleanup=True,
                **per_build,
            )
        except IndexSuperseded as exc:
            # Another process owns the index now.  The remaining builds would
            # abort on the same marker, so stop and let the whole command be
            # retried — a partial sweep would leave some builds indexed
            # against a database the other process is changing.
            print(f"Superseded: {exc}", file=sys.stderr)
            return EXIT_SUPERSEDED
        except Exception as exc:  # noqa: BLE001 — best-effort per-build
            print(f"error: indexing variant={variant_name} image={image}: {exc}", file=sys.stderr)
            continue
        touched_pairs.append((variant_name, image))
        print(f"Indexed variant={variant_name} image={image} config_hash={ch[:16]}…")

    # Final: rebuild FTS once and run per-(variant, image) retention once.
    conn = open_db(db_path)
    fts_problems: list[str] = []
    try:
        rebuild_fts(conn)
        rebuild_files_fts(conn)
        rebuild_macros_fts(conn)
        # Each build skipped its own FTS check because the rebuild was
        # deferred to here — see _step_verify_integrity.  This is the one
        # place that can run it, and it has to: the FTS index backs
        # search_code, search_content and the macro search, and an index that
        # disagrees with its content table serves rows that are not there.
        from ..indexer._postprocess import fts_inconsistencies

        fts_problems = fts_inconsistencies(conn)
        deleted = cleanup_old_builds_multi(conn, project_id, db_path.parent, touched_pairs)
        if deleted:
            print(f"Cleaned up {deleted} stale build(s)")
    finally:
        conn.close()

    if fts_problems:
        for problem in fts_problems:
            print(f"error: {problem}", file=sys.stderr)
        print(
            "error: the full-text index disagrees with its content after a "
            "rebuild — reindex with --force",
            file=sys.stderr,
        )
        return 1

    _post_index_optimize(db_path, project_root, project_id, system, args)
    return 0


def _layered_paths(base: list[str], variant_extra: list[str]) -> list[str]:
    """Return the ``[index]`` layer PLUS this variant's, in a stable order.

    Adds, never replaces.  A user with a value in ``[index]`` and in the
    variant would otherwise lose the shared entries without a word, which is
    the same class of silent wrongness this series of changes is about.  The
    precedent is already in the repo: ``_DICT_FIELDS`` in build.py merges
    ``env`` per key, and says so.

    The cost, and it is real: a variant cannot NARROW the set.  The channels
    for that are ``project_paths``, which wins over every vendor pattern, or
    a fix to the detection.  No escape hatch is added.

    ``dict.fromkeys`` and not ``set``: the order shows up in the LIKE filters,
    in the log and in the manifest, and a stable order diffs cleanly.
    """
    return list(dict.fromkeys([*base, *variant_extra]))


def _build_run_kwargs(
    args, cfg, project_root, project_id, vendor_paths, project_paths, cs_config,
) -> dict:
    """Build the shared ``runner.run()`` kwargs from config + CLI flags."""
    from ..indexer.build import detect_build_system

    return dict(
        vendor_paths=vendor_paths,
        project_paths=project_paths,
        project_name=args.name or cfg.project.name,
        index_refs=False if args.no_refs else cfg.index.index_refs,
        index_embeddings=(
            False
            if getattr(args, "no_embeddings", False)
            else getattr(args, "embeddings", None) or cfg.index.index_embeddings
        ),
        analyze_symbols=(
            False
            if getattr(args, "no_analyze", False)
            else getattr(args, "analyze", False) or cfg.llm.analyze_symbols
        ),
        analyze_overrides=True,
        project_root=project_root,
        project_id=project_id,
        llm_config=cfg.llm,
        cache_server_config=cs_config,
        config_header=cfg.index.config_header,
        force=args.force,
        analyze_vendor=(
            False
            if getattr(args, "no_analyze_vendor", False)
            else getattr(args, "analyze_vendor", False) or cfg.llm.analyze_vendor
        ),
        purge_max_missing_percent=cfg.index.purge_max_missing_percent,
        build_system=cfg.build.system or detect_build_system(project_root),
    )


def _ensure_watcher_after_index(project_root: Path) -> None:
    """Start the watcher daemon after an index run that was successful.

    WHY: an MCP server spawns the daemon only at startup, and only when
    ``index.db`` already exists.  A server that starts before the first
    index takes an early return and spawns no daemon.  The daemon is the
    only component that starts a background reindex, thus that project
    gets no automatic reindex.

    This function closes that window.  After the index run, ``index.db``
    exists, which is the only precondition of ``_ensure_daemon_running``.
    The call does nothing when a daemon already runs.
    """
    try:
        from ..mcp.background import _ensure_daemon_running

        _ensure_daemon_running(project_root)
    except (RuntimeError, OSError):
        # Not fatal — the index is complete and usable.  Only the
        # automatic reindex of changed files is not available.  The
        # command `fw-context watch restart` corrects this.
        log.debug("Could not start the watcher daemon for %s", project_root, exc_info=True)


def cmd_index(args: argparse.Namespace) -> int:
    """Build or rebuild the symbol index from compile_commands.json.

    By default, reuses an existing ``compile_commands.json`` for fast
    incremental indexing.  When the file is missing, a clean build is
    triggered automatically (auto-detecting Mbed OS / Zephyr / PlatformIO).

    Pass ``--build`` to force a fresh build and full re-index.

    WHY: Most invocations are incremental (user edits a file and runs
    ``fw-context index``).  Forcing a build on every run would be
    wasteful — Mbed/zephyr builds can take minutes.  The auto-detect
    logic only triggers a build when necessary.
    """
    from ..config import derive_project_id
    from ..config import load as load_config
    from ..exit_codes import EXIT_ALREADY_RUNNING, EXIT_SUPERSEDED
    from ..indexer.build import detect_build_system
    from ..indexer.db._locking import IndexRunLocked, index_run_lock
    from ..indexer.runner import IndexSuperseded, run
    from ..utils import resolve_project_root

    if args.verbose:
        handler = logging.StreamHandler()
        handler.setFormatter(VerboseFormatter())
        logging.basicConfig(level=logging.DEBUG, handlers=[handler])
    else:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )

    # Suppress httpx INFO logs (one per HTTP request — extremely noisy during
    # embedding generation, LLM analysis, and cache server communication).
    logging.getLogger("httpx").setLevel(logging.WARNING)

    project_root = resolve_project_root(args.project)
    cfg = load_config(project_root=project_root)
    bg = getattr(args, "background", False)
    # The config wins over the markers, the same form _run_multi already
    # uses.  The two can disagree: a freestanding NCS application has
    # CMakeLists.txt and no west.yml, so a marker scan calls it a CMake
    # project.  This value also decides which builder generates and
    # validates compile_commands.json, not only the vendor patterns.
    detected_system = cfg.build.system or detect_build_system(project_root)

    if detected_system:
        print(f"Project: {project_root.name}  path={project_root}  build={detected_system}")
    elif bg:
        cc_fallback = project_root / CC_OUTPUT_REL
        if cc_fallback.exists():
            print(f"Project: {project_root.name}  path={project_root}  build=unknown (bg, reusing cc)")
        else:
            print(f"error: No build system detected and no compile_commands.json for {project_root}.", file=sys.stderr)
            print("  Run 'fw-context index --build' first.", file=sys.stderr)
            return 1
    else:
        print(f"Project: {project_root.name}  path={project_root}  build=unknown")

    project_id = derive_project_id(project_root)
    db_path = cfg.index.db_dir / project_id / "index.db"

    # A retired key stops the run before any build.  Each build goes to
    # .fw-context/build/<variant>/out, and a config that still names another
    # directory says something that the build does not do.  The marker gives
    # the same text to each MCP answer, because a background run has no
    # terminal.
    from ..indexer.build import retired_build_dir_keys

    retired = retired_build_dir_keys(cfg.build)
    if retired:
        text = (
            f"{', '.join(retired)} in .fw-context/config.toml has no effect: each build "
            "goes to .fw-context/build/<variant>/out. Remove the key, then run "
            "'fw-context index --build'."
        )
        print(f"error: {text}", file=sys.stderr)
        autobuild.record_problem(db_path.parent, text)
        return 1

    # A wrong [index] transient_defines also stops the run before any build:
    # an entry that matches no macro makes each build parse each unit again.
    from ..config.settings import transient_defines_problems

    problems = transient_defines_problems(cfg)
    if problems:
        # Each text of the marker names the command that comes after the
        # repair: the status tools and the query answers give it as is.
        text = (
            "; ".join(problems)
            + ". Correct .fw-context/config.toml or the global config, then run 'fw-context index'."
        )
        print(f"error: {text}", file=sys.stderr)
        autobuild.record_problem(db_path.parent, text)
        return 1

    # Each variant builds its own database, thus [index] compile_commands
    # with a database of the user would have no effect for them: an error,
    # and not a file that the run ignores without a word.
    from ..indexer.build import explicit_compile_commands, user_database

    if cfg.build.variants and user_database(project_root, cfg) is not None:
        text = (
            "[index] compile_commands names a database of your own, and [[build.variants]] build "
            "their own databases. Remove one of the two from .fw-context/config.toml, then run "
            "'fw-context index'."
        )
        print(f"error: {text}", file=sys.stderr)
        autobuild.record_problem(db_path.parent, text)
        return 1

    # A project file that holds more than one build (each [env:<name>] of
    # platformio.ini) gives one variant for each, when the config declares
    # none.  The build system answers, thus the set is the set that its
    # build makes.  An explicit compile_commands.json (on the command line,
    # or in [index] compile_commands) and a [build] command name their build
    # themselves.
    if (
        not cfg.build.variants and not cfg.build.command
        and not getattr(args, "compile_commands", None)
        and explicit_compile_commands(project_root, cfg) is None
    ):
        from ..indexer.builders import implicit_variants
        from ..indexer.builders import registry as builder_registry

        builder_cls = builder_registry.get(detected_system) if detected_system else None
        try:
            found = implicit_variants(builder_cls() if builder_cls else None, project_root, cfg.build)
        except (RuntimeError, OSError) as exc:
            # OSError: the program of [build] python or of pio is not there.
            # The command comes before the error: the error can hold the
            # output of the build system, and the command must not read as
            # a part of it.
            text = (
                "cannot list the builds of the project. Repair the cause, then run "
                f"'fw-context index'. The error:\n{exc}"
            )
            print(f"error: {text}", file=sys.stderr)
            autobuild.record_problem(db_path.parent, text)
            return 1
        if found:
            cfg.build.variants = found
            print(f"The project builds {len(found)} variants: {', '.join(v.name for v in found)}")

    # A compile_commands.json that the user names is the input of this run.
    # No automatic build may replace it: `--build` comes before the explicit
    # file in `_resolve_compile_commands`, thus a build would drop the file.
    # The user names it on the command line, or in [index] compile_commands
    # for a build that fw-context can run (`build.user_database`).
    explicit_cc = bool(getattr(args, "compile_commands", None)) or user_database(project_root, cfg) is not None
    # The file of the active index: only the build of that file is checked
    # (`build.checked_compile_commands`).
    indexed_cc = _indexed_compile_commands(project_root, db_path)

    # ── A run of the user without a build: build first ──
    # The run asks the compiler of each unit in the build directory, thus a
    # missing build gives units without the answer of the compiler.  The
    # user started this run, thus it builds as `--build` does.  A background
    # run takes the path below, which builds only with a backend that may
    # build on its own.
    if not bg and not getattr(args, "build", False) and not explicit_cc and not cfg.build.variants:
        _build_if_missing(args, project_root, cfg, detected_system, indexed_cc)

    # ── Automatic build: a missing build, a source that the build never saw,
    # or another checkout ──
    # A plain reindex cannot repair any of them.  `_plan_auto_build` returns
    # a non-empty list only when the backend may build on its own.
    auto_build_sources: list[str] = []
    if not getattr(args, "build", False):
        auto_build_sources, auto_build_cfg, auto_build_reason = _plan_auto_build(
            project_root, db_path, cfg, detected_system, background=bg, args=args
        )
        # The two always arrive together; the second test is what lets the
        # type checker see that, and it costs nothing.
        if auto_build_sources and auto_build_cfg is not None:
            args.build = True
            # The candidate that _plan_auto_build asked the backend about.
            # Applying it here, and nowhere else, keeps every "do not build"
            # path from leaving an isolated directory behind on cfg.  It also
            # carries `isolated_build_dir`, which is what makes `--build`
            # legal on a `--background` run — see the check in
            # `_resolve_compile_commands`.
            cfg.build = auto_build_cfg
            log.info("%s", auto_build_reason)

    # The CLI flag REPLACES the [index] layer.  A variant's own [index] keys
    # are added on top of whichever of the two won — see _layered_paths().
    vendor_paths = list(getattr(args, "vendor_paths", None) or cfg.index.vendor_paths)
    project_paths = list(getattr(args, "project_paths", None) or cfg.index.project_paths)

    cs_config = cfg.cache_server
    if cs_config is not None and getattr(args, "force", False):
        from dataclasses import replace
        cs_config = replace(cs_config, force=True)

    # ── Multi-variant: dispatch BEFORE single-build resolution ──
    # WHY: [[build.variants]] replaces the single [build] board/source flow.
    # build_multi() builds each variant; the single-build path below
    # (generate_compile_commands → builder.build()) would fail on a project
    # whose board lives per-variant (Zephyr "requires a board name").
    if cfg.build.variants:
        # Takes over from a background run by killing it, which also drops
        # its index lock; the lock below then only refuses another
        # FOREGROUND run.  The markers are claimed AFTER the lock — see
        # _kill_bg_reindex().
        _kill_bg_reindex(db_path)
        run_kwargs = _build_run_kwargs(
            args, cfg, project_root, project_id, vendor_paths, project_paths, cs_config,
        )
        try:
            # Held across every (variant, image) build, not per build: a
            # second run slipping in between two builds would index against a
            # database this one is still changing.
            with index_run_lock(db_path.parent):
                _claim_index(db_path.parent)
                try:
                    exit_code = _run_multi(
                        args, cfg, project_root, project_id, db_path,
                        detected_system, run_kwargs,
                    )
                finally:
                    _release_index(db_path.parent)
        except IndexRunLocked as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_ALREADY_RUNNING

        # Outside the index lock, and only after a run that was
        # successful — see the same call at the end of this function.
        if exit_code == 0:
            if auto_build_sources:
                autobuild.clear_failure(db_path.parent)
            _ensure_watcher_after_index(project_root)
        elif auto_build_sources:
            autobuild.record_failure(db_path.parent, auto_build_sources)
        return exit_code

    # The run ended well, but the check is done again and not taken from
    # its start: another run can refuse, and write its marker, while this
    # one runs, and the build can go away meanwhile.  A plain clear would
    # then remove a true warning.  The file of the index is read again,
    # because this run wrote it.
    refusal = "" if explicit_cc else _refuse_without_build(
        project_root, cfg, detected_system, _indexed_compile_commands(project_root, db_path),
        at_the_end=True,
    )
    if refusal:
        autobuild.record_problem(db_path.parent, refusal, build_missing=True)
    else:
        autobuild.clear_problem(db_path.parent)

    multi = bool(cfg.build.variants)
    if not multi and getattr(args, "build", False):
        _record_still_uncovered(project_root, db_path, cfg)

    _ensure_watcher_after_index(project_root)
    return 0



def _run_single(
    args: argparse.Namespace,
    cfg,
    project_root: Path,
    project_id: str,
    db_path: Path,
    detected_system: str | None,
    bg: bool,
    run_kwargs: dict,
) -> int:
    """Build (when needed), validate and index the single ``[build]``.

    Called ONLY inside _owned_index — the build writes the files the index
    run reads, so it has to be excluded the same way the indexing is.
    """
    from ..indexer.runner import IndexStopped, run

    # ── Resolve compile_commands.json ──
    cc_result = _resolve_compile_commands(args, project_root, cfg, detected_system, bg)
    if cc_result[0] is None:
        if auto_build_sources:
            # The build that fw-context started failed.  Remember it, or the
            # next daemon cycle repeats it: a failed build leaves
            # compile_commands.json untouched, thus the trigger stays armed.
            autobuild.record_failure(db_path.parent, auto_build_sources)
        return 1
    compile_commands, explicit_cc = cc_result
    assert compile_commands is not None  # checked above via cc_result[0]

    # ── Validate build artifacts ──
    compile_commands, build_dir_patterns, ok = _validate_and_fix_artifacts(
        compile_commands, project_root, detected_system, cfg, bg, explicit_cc, args
    )
    if not ok:
        return 1
    assert compile_commands is not None  # _validate_and_fix_artifacts returns Path|None, but ok=True → non-None

    # ── Manage background reindex ──
    # Takes over from a background run by killing it, which also drops its
    # index lock; the lock below then only refuses another FOREGROUND run.
    # The markers are claimed AFTER the lock — see _kill_bg_reindex().
    _kill_bg_reindex(db_path)

    run_kwargs = _build_run_kwargs(
        args, cfg, project_root, project_id, vendor_paths, project_paths, cs_config,
    )

    try:
        with index_run_lock(db_path.parent):
            _claim_index(db_path.parent)
            try:
                config_hash = run(
                    compile_commands=compile_commands,
                    db_path=db_path,
                    build_dir_patterns=build_dir_patterns,
                    **run_kwargs,
                )
                print(f"Indexed. config_hash={config_hash[:16]}…  db={db_path}")

                _post_index_optimize(
                    db_path, project_root, project_id, detected_system, args
                )
            except IndexSuperseded as exc:
                # Not a failure — see IndexSuperseded.  The distinct exit code
                # lets the daemon retry instead of treating it as a broken run.
                print(f"Superseded: {exc}", file=sys.stderr)
                return EXIT_SUPERSEDED
            finally:
                _release_index(db_path.parent)
    except IndexRunLocked as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ALREADY_RUNNING

    if auto_build_sources:
        # The build worked, thus the backoff marker of an earlier failure is
        # obsolete.
        autobuild.clear_failure(db_path.parent)

    if getattr(args, "build", False):
        _record_still_uncovered(project_root, db_path)

    # Outside the index lock.  The daemon does a staleness check when it
    # starts, thus a daemon that starts inside the lock could start a
    # second index run against the database that this run still changes.
    _ensure_watcher_after_index(project_root)
    return 0


def _record_still_uncovered(project_root: Path, db_path: Path, cfg) -> None:
    """Remember the sources a build ran for and still did not cover.

    Called only after a run that built.  What is left uncovered then is not
    a file waiting for a build — the build system does not want it: a test
    outside the build, an old experiment, a variant that is not compiled.
    Reporting it again would tell the caller to run `fw-context index
    --build`, which changes nothing, and would hold get_active_build on
    "reindex_needed" for as long as the file stays edited.

    Recomputing the scan here is simpler than remembering the list from
    before the build, and it covers an explicit `--build` from the user just
    as well as an automatic one.  ``apply_exclusions=False`` is required, or
    the scan would filter out the files this is about to record, and
    ``limit=None`` because the marker has to describe the WHOLE set.

    Best-effort: a marker that cannot be written costs one repeated report,
    never a wrong answer, thus no failure here may break the index run.

    The build is the one that the status tools check for new sources
    (`_checked_build`, the application of a build of several images).  The
    newest build was the program that the run did last, for example the
    bootloader of `--image bootloader`: a file of the bootloader that the
    application does not compile then stayed out of the marker, and the
    status reported it as a new source after each build.
    """
    from ..indexer.db import open_db
    from ..mcp.shared.stale import find_unindexed_sources
    from ..utils import compute_source_hash

    try:
        conn = open_db(db_path)
        try:
            active = _checked_build(conn, project_root, cfg)
            if not active or not active["compile_commands_path"]:
                return
            # limit=None, and that is not a detail.  record_excluded
            # replaces the marker wholesale, thus a list the report cap
            # truncated would silently drop every entry past it — the
            # marker could never hold more than the cap, and always the
            # same first twenty in sort order.  A file past that point was
            # reported again on every edit, armed a build that changed
            # nothing, and never reached the suppression this marker is for.
            uncovered = find_unindexed_sources(
                conn,
                active["config_hash"],
                project_root,
                Path(active["compile_commands_path"]),
                limit=None,
                apply_exclusions=False,
            )
        finally:
            conn.close()
    except SAFE_EXCEPT:
        log.debug("could not recompute the uncovered sources", exc_info=True)
        return

    if uncovered:
        log.info(
            "%d source file(s) stay outside compile_commands.json after the "
            "build — recorded so they are not reported again", len(uncovered),
        )
    autobuild.record_excluded(
        db_path.parent,
        {path: compute_source_hash(project_root / path) for path in uncovered},
    )
