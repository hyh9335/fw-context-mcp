"""Unit-level processing for the incremental indexing pipeline.

Position in the pipeline
------------------------
1. The **runner** (:mod:`runner`) iterates all translation units (TUs)
   and delegates each to this module for staleness checking, parsing,
   and persistence.  This module is the hot path — every TU passes
   through it.
2. **Phase 1** (parallel): :func:`_check_and_parse_unit` runs in a
   thread pool — no database writes.  It determines whether each TU
   is unchanged (skip) or changed (re-parse with libclang).
3. **Phase 2** (serialised): :func:`_handle_unchanged` and
   :func:`_process_unit` serialise via ``write_lock`` and persist
   the results.

Design principles
-----------------
* **Avoid libclang when possible** — three-tier staleness check
  (mtime → content-hash → libclang parse).  mtime is ~100× cheaper
  than content-hashing; content-hashing is ~50× cheaper than
  libclang parsing.
* **No cross-build import** — a TU is either up to date under the
  current ``config_hash`` or it is re-parsed.  Rows are never copied
  from another build: ``config_hash`` identifies the compilation
  dialect, so another build's rows were produced by different macros
  and say nothing about this one.  Adding or removing a source file no
  longer changes ``config_hash``, so the case this used to optimise
  does not arise.
* **Parallel parse, serialise writes** — libclang parsing is
  CPU-bound and runs in a thread pool without any lock.  Only
  database writes are serialised, maximising throughput on
  multi-core machines.
* **Thread-safe connection management** — callers can supply a
  persistent per-worker connection (avoiding per-TU open/close
  overhead) or let this module manage its own.

Key decisions
-------------
* Shared headers (included by multiple TUs) are claimed by exactly one
  TU per run via ``skip_files`` — the first TU to walk a header owns
  its rows, and later TUs skip its subtree entirely.
* Overrides are rebuilt from scratch in post-processing by
  ``_build_overrides`` because the override graph depends on the full
  set of indexed classes.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from collections.abc import Collection
from dataclasses import dataclass
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from .config_hash import DEFAULT_TRANSIENT_DEFINES

try:
    from clang.cindex import TranslationUnitLoadError
except ImportError:
    TranslationUnitLoadError = RuntimeError  # clang not available — use fallback

# ── Slice-1 v2 shadow flag (local, cache-carrying; NO store_v2 import) ──────
# The flag decision stays in this module so that with the flag OFF nothing
# from store_v2 is ever imported (Slice-0 inertness gate, T19).  The heavy
# module is loaded lazily inside the hook only when the flag is on.
_V2_FLAG_CACHE: bool | None = None


def _v2_shadow_flag_on() -> bool:
    """``FW_CONTEXT_SHADOW_V2=1`` env override, else ``cfg.index`` flag.

    Also returns True when v2 write-authority is on (M4): write-authority
    requires the v2 write path to be active, so it implies the shadow flag.
    """
    global _V2_FLAG_CACHE
    if _V2_FLAG_CACHE is not None:
        return _V2_FLAG_CACHE
    env = os.environ.get("FW_CONTEXT_SHADOW_V2")
    if env is not None:
        enabled = env == "1"
    else:
        try:
            from fw_context_mcp.config import load
            enabled = bool(load().index.shadow_v2_enabled)
        except Exception:
            enabled = False
    # M4: write-authority implies shadow (v2 write path must be active).
    if not enabled:
        enabled = _v2_write_authority_on()
    _V2_FLAG_CACHE = enabled
    return enabled


def _reset_v2_flag_cache() -> None:
    """Test seam: forget the cached flag decision."""
    global _V2_FLAG_CACHE
    _V2_FLAG_CACHE = None


# ── M4: v2 write authority ──
# Distinct from the shadow flag: when write-authority is ON, v2 World
# finalization is the AUTHORITATIVE success criterion for an indexing run
# (CASE A–D of the M4 success matrix).  A failed or incomplete v2 finalize
# ⇒ INDEX FAILURE, even if legacy succeeded.  Legacy postprocess failure ⇒
# INDEX SUCCESS + LEGACY_COMPATIBILITY_DEGRADED (not abort).
#
# Write-authority implies the shadow flag: setting FW_CONTEXT_V2_WRITE_AUTHORITY=1
# automatically enables the v2 shadow write path as well.
_V2_WRITE_AUTHORITY_CACHE: bool | None = None


def _v2_write_authority_on() -> bool:
    """``FW_CONTEXT_V2_WRITE_AUTHORITY=1`` env override, else ``cfg.index`` flag.

    When True, v2 World finalization gates the indexing run's success.
    Implies ``_v2_shadow_flag_on()`` is also True (write-authority requires
    the v2 write path to be active).
    """
    global _V2_WRITE_AUTHORITY_CACHE
    if _V2_WRITE_AUTHORITY_CACHE is not None:
        return _V2_WRITE_AUTHORITY_CACHE
    env = os.environ.get("FW_CONTEXT_V2_WRITE_AUTHORITY")
    if env is not None:
        enabled = env == "1"
    else:
        try:
            from fw_context_mcp.config import load
            enabled = bool(getattr(load().index, "v2_write_authority", False))
        except Exception:
            enabled = False
    _V2_WRITE_AUTHORITY_CACHE = enabled
    return enabled


def _reset_v2_write_authority_cache() -> None:
    """Test seam: forget the cached write-authority decision."""
    global _V2_WRITE_AUTHORITY_CACHE
    _V2_WRITE_AUTHORITY_CACHE = None


# ── M5-1: legacy compatibility mode ──
# When True, the normal index path also writes legacy semantic storage
# (index.db, postprocess, mirror). When False (default), normal operation
# is v2-only: no legacy DB opened, no legacy writes, no legacy postprocess.
_LEGACY_COMPAT_CACHE: bool | None = None


def _legacy_compat_on() -> bool:
    """``FW_CONTEXT_LEGACY_COMPATIBILITY_MODE=1`` env override, else config flag.

    When True, the normal index path generates legacy semantic storage as a
    compatibility mirror. When False (default), normal operation is v2-only.
    """
    global _LEGACY_COMPAT_CACHE
    if _LEGACY_COMPAT_CACHE is not None:
        return _LEGACY_COMPAT_CACHE
    env = os.environ.get("FW_CONTEXT_LEGACY_COMPATIBILITY_MODE")
    if env is not None:
        enabled = env == "1"
    else:
        try:
            from fw_context_mcp.config import load
            enabled = bool(getattr(load().index, "legacy_compatibility_mode", False))
        except Exception:
            enabled = False
    _LEGACY_COMPAT_CACHE = enabled
    return enabled


def _reset_legacy_compat_cache() -> None:
    """Test seam: forget the cached legacy-compat decision."""
    global _LEGACY_COMPAT_CACHE
    _LEGACY_COMPAT_CACHE = None


from ..utils import MTIME_TOLERANCE_S, SAFE_EXCEPT, compute_source_hash, is_fatal
from .certificate import (
    compute_bucket_key,
    extract_closure_from_parsed,
    get_certificate_cache,
    m9_reuse_enabled,
    verify_closure_content,
)
from .config_hash import compute_flags_hash, compute_tu_content_hash
from .db import (
    open_db,
    transaction,
    upsert_file,
    write_lock,
)
from .ops import _build_filtered_file_content, _normalize_file_path, store_symbols_for_unit

log = logging.getLogger(__name__)


def _check_and_parse_unit(
    unit,
    config_hash,
    project_root,
    index_refs,
    existing_files,
    force=False,
    manifest=None,
    skip_files: set[str] | None = None,
    header_stale_tus: frozenset[str] = frozenset(),
    hash_cache: dict[str, str] | None = None,
    header_table: dict[str, dict] | None = None,
    context_id: str = "",
    pre_decision=None,
    *,
    transient_defines: Collection[str] = DEFAULT_TRANSIENT_DEFINES,
):
    """Check whether *unit* needs re-parsing and parse it if so.

    Uses a three-tier staleness check:
    1. **mtime fast-path** — unchanged mtime → skip (no I/O).
    2. **content-hash check** — mtime differs but hashes match → skip
       (the source, flags, and header dependencies have not changed).
       Uses ``manifest.json`` for header hashes when available — no libclang needed.
    3. **libclang parse** — content hashes differ → parse.

    Tier 1 does not look at the flags.  A build whose flags changed but
    whose sources did not moves no mtime, so the check stops here.

    Does NOT write to the database — the caller is responsible for
    acquiring ``write_lock`` and calling ``_process_unit(pre_parsed=...)``
    to persist the result.

    All translation units are indexed — no exclusion filtering.

    Args:
        manifest: Optional ``{file_path: entry}`` lookup dict built from
            ``manifest.load()`` entries.  When provided, header staleness
            is checked via hash comparison against the manifest (fast —
            file reads + SHA-256 only).  When ``None``, falls back to
            source-hash-only comparison.
        header_stale_tus: Normalized paths of TUs that include a header
            whose content changed (from ``manifest.collect_stale_headers``).
            For those TUs both cheap tiers are skipped: a header change
            never moves the TU's own mtime, so Tier 1 would report
            "unchanged" and the header's symbols would never be refreshed.
        hash_cache: Shared ``{resolved path: sha256}`` memo for project
            header hashes, so a header included by many TUs is read once
            per index run.

    Returns:
        * ``("unchanged", None, None, None)`` — no re-parse needed (Tier 1 mtime match).
        * ``("unchanged", None, None, hashes)`` — no re-parse needed (Tier 2 content-hash match).
        * ``("skipped", None, None, None)`` — parse failed.
        * ``("updated", parsed, (t_start, t_end), hashes)`` — parsed
          successfully, ready for ``_process_unit(pre_parsed=parsed)``.
          *hashes* is ``(source_hash, flags_hash, manifest_entry_hash)``.
    """
    resolved_tu = unit.file.resolve()

    # ── Three-tier staleness strategy ──
    # Each tier is 1-2 orders of magnitude cheaper than the next:
    #   Tier 1 — mtime comparison (stat syscall, no hash computation)
    #   Tier 2 — content-hash comparison (SHA-256 of source + flags +
    #            headers)
    #   Tier 3 — libclang parse + full AST walk (seconds per TU)
    # Tiers 1 and 2 are lock-free — no DB write contention.  Only
    # Tier 3 results require serialised persistence in Phase 2.

    file_path = _normalize_file_path(str(resolved_tu), project_root)
    force_refs = force or os.environ.get("FW_CONTEXT_FORCE_REFINDEX") == "1"

    # A changed header leaves the TU's own mtime and source hash untouched,
    # so both cheap tiers would wave this TU through.  Skip them and let
    # Tier 3 re-parse — that is the only way the header's symbols and its
    # ifdef-filtered content get refreshed.
    tu_header_stale = file_path in header_stale_tus

    # ── Wave fusion: the decision is by hash, not by file time ──
    # Upstream removed the mtime fast-path: a copy that keeps its time
    # (cp -p) changes the file and not the time, and only the content
    # hashes below see it.  Every candidate now pays one source-hash
    # read; the unchanged answer then comes from Tier 2 alone.

    # ── Wave: the upfront decision is authoritative ──
    # decide_units ran BEFORE the loop: a save that lands after the
    # decision is a change (the row stores the decision's hashes, not
    # ones read at write time), and the sibling rule needs every
    # answer upfront.  A CHANGED verdict skips the local tiers — a
    # local re-check could undo the sibling rule with its own, later,
    # read of an unchanged sibling.
    if pre_decision is not None and not force_refs:
        _entry_hash = _get_manifest_entry_hash_for_unit(
            unit, project_root, manifest,
            hash_cache=hash_cache, header_table=header_table,
            context_id=context_id,
        )
        if pre_decision.unchanged:
            return (
                "unchanged", None, None,
                (pre_decision.hashes[0], pre_decision.hashes[1], _entry_hash),
            )
        hashes = (pre_decision.hashes[0], pre_decision.hashes[1], _entry_hash)
    else:
        # ── Tier 2: content-hash check (mtime differs) ──
        # Compute source hash first — cheap, no libclang.
        try:
            source_hash = compute_source_hash(unit.file)
        except OSError:
            source_hash = ""

        if unit.raw_entry is not None:
            flags_hash = compute_flags_hash(unit.raw_entry, transient_defines=transient_defines)
        else:
            flags_hash = ""

        # Determine manifest entry hash for Tier 2 comparison.
        # When manifest.json exists, use check_tu_staleness() — fast hash comparison
        # against stored values.  When not, fall back to source-only hash.
        manifest_entry_hash = _get_manifest_entry_hash_for_unit(
            unit,
            project_root,
            manifest,
            hash_cache=hash_cache,
            header_table=header_table,
            context_id=context_id,
        )

        content_hash = compute_tu_content_hash(source_hash, flags_hash, manifest_entry_hash)
        hashes = (source_hash, flags_hash, manifest_entry_hash)

        if not force_refs and not tu_header_stale and file_path in existing_files:
            if manifest is not None:
                # Wave fusion: the manifest speaks for the listing.  The entry
                # the runner pre-selected for this (file, context) proves the
                # unit unchanged only when its flags hash is the flags hash of
                # THIS listing and neither its source nor any header changed
                # (:func:`_unchanged_entry`).  A DB content-hash comparison
                # cannot see a header edit, so with a manifest present the
                # entry alone decides — no entry proves nothing, and the unit
                # is parsed.
                try:
                    tu_rel = unit.file.resolve().relative_to(project_root).as_posix()
                except ValueError:
                    tu_rel = unit.file.resolve().as_posix()
                entry = manifest.get((tu_rel, context_id)) if isinstance(
                    next(iter(manifest.keys()), None), tuple
                ) else manifest.get(tu_rel)
                if entry is not None and _unchanged_entry(
                    [entry], source_hash, flags_hash, project_root,
                    hash_cache=hash_cache, header_table=header_table,
                ) is not None:
                    return ("unchanged", None, None, hashes)
            else:
                # No manifest: the v2 DB content-hash comparison decides.
                rec = existing_files[file_path]
                # rec is a FileHashRecord from get_file_hashes() — attribute access
                if rec.content_hash and rec.content_hash == content_hash:
                    # Content unchanged — just the mtime was bumped by a rebuild.
                    return ("unchanged", None, None, hashes)

    # ── Tier 3: libclang parse ──
    # Wave fusion: the parse itself runs through ``runner._parse_unit`` —
    # the wave's module-level seam; the save-during and save-after-parse
    # contracts wrap it.  The M9 certificate cache travels in
    # :func:`_parse_unit_merged`.
    from . import runner as _runner_mod

    return _runner_mod._parse_unit(
        unit, index_refs, skip_files, hashes,
        config_hash=config_hash, project_root=project_root,
    )


def _parse_unit_merged(
    unit,
    index_refs,
    skip_files: set[str] | None,
    hashes,
    config_hash: str = "",
    project_root=None,
):
    """The Tier-3 parse of :func:`_check_and_parse_unit`, as its own step.

    Does NOT write to the database — the caller persists via
    ``_process_unit(pre_parsed=...)``.  Returns the upstream 4-tuple
    shape; ``runner._parse_unit`` delegates here.
    """
    from .symbols import extract_all

    bucket_key = ""
    if m9_reuse_enabled():
        try:
            bucket_key = compute_bucket_key(
                unit, project_root, skip_files=skip_files, with_refs=index_refs,
            )
        except Exception:
            bucket_key = ""  # any failure → safe MISS (no caching)

    t_parse_start = time.monotonic()
    try:
        parsed = extract_all(
            unit,
            with_refs=index_refs,
            return_tu=True,
            skip_files=skip_files,
        )
    except sqlite3.Error:
        log.error("Fatal DB error parsing %s — stopping indexer", unit.file.name)
        raise
    # TranslationUnitLoadError is named next to SAFE_EXCEPT and not inside
    # it: the class derives straight from Exception, so the tuple
    # `(ValueError, TypeError, RuntimeError, AttributeError, sqlite3.Error,
    # OSError)` does not hold it.  Without the name here the exception left
    # the run, and `ops.py` — which handles the same failure by skipping —
    # never saw the unit at all.
    #
    # Measured on the Mbed project: a branch switch removed two generated zcbor
    # sources that compile_commands.json still listed, and those two files
    # out of 881 ended the run at translation unit 39.  The 842 units behind
    # them were never read.
    except (*SAFE_EXCEPT, TranslationUnitLoadError) as exc:
        if is_fatal(exc):
            raise
        msg = str(exc)
        if "unable to open database file" in msg:
            log.error("Fatal DB error parsing %s: %s — stopping indexer", unit.file.name, exc)
            raise
        log.warning("skip TU %s: %s", unit.file.name, exc)
        return ("skipped", None, None, None)
    t_parse_end = time.monotonic()

    # ── Tier 2.5 (M9): cache the parse result for future reuse ──
    if m9_reuse_enabled() and bucket_key and not bucket_key.startswith("FORCE_MISS"):
        try:
            closure_files, closure_hash = extract_closure_from_parsed(parsed)
            cache = get_certificate_cache()
            cache.put(
                key=bucket_key,
                parsed=parsed,
                full_kb_c_root="",  # not needed for reuse decision
                closure_file_list=closure_files,
                closure_content_hash=closure_hash,
                extraction_params=(frozenset(skip_files) if skip_files else frozenset(), index_refs),
                build_name=str(config_hash),
            )
        except Exception:
            pass  # caching failure must not break indexing

    return ("updated", parsed, (t_parse_start, t_parse_end), hashes)


def _get_manifest_entry_hash_for_unit(
    unit,
    project_root: Path,
    manifest_lookup: dict | None,
    *,
    hash_cache: dict[str, str] | None = None,
    header_table: dict[str, dict] | None = None,
    context_id: str = "",
) -> str:
    """Return the manifest entry hash for a TU for Tier 2 staleness comparison.

    When *manifest_lookup* is available (``{file_path: entry}`` dict from
    ``manifest.json``), checks header staleness via ``check_tu_staleness()``
    — fast file reads + SHA-256 only, no libclang.

    When *manifest_lookup* is ``None``, falls back to a source-only hash
    (no header tracking possible).

    *header_table* is the manifest's shared ``headers`` map.  The entries hold
    header paths only; without the table they resolve to empty hashes and
    every TU reads as stale.
    """
    from .manifest import check_tu_staleness, compute_current_entry_hash, resolve_headers
    from .manifest import get_manifest_entry_hash as _entry_hash

    # ── Manifest path (fast — no libclang, O(1) lookup) ──
    if manifest_lookup is not None:
        try:
            tu_rel = unit.file.resolve().relative_to(project_root).as_posix()
        except ValueError:
            tu_rel = unit.file.resolve().as_posix()

        # Per-(file, context) key: one file with two flag sets has two
        # entries, and a file-keyed dict resolved both contexts to the
        # same — the last — entry.  Legacy file keys still resolve.
        entry = manifest_lookup.get((tu_rel, context_id))
        if entry is None:
            entry = manifest_lookup.get(tu_rel)
        if entry is not None:
            headers = resolve_headers(entry, header_table)
            stale, current_source_hash = check_tu_staleness(
                entry, project_root,
                hash_cache=hash_cache, headers=headers,
            )
            if not stale:
                return _entry_hash(entry, headers)
            # Stale — compute hash from CURRENT disk content (both source and headers)
            return compute_current_entry_hash(
                entry,
                project_root,
                new_source_hash=current_source_hash,
                hash_cache=hash_cache,
                headers=headers,
            )

    # ── Fallback: source-only hash (no manifest, no header tracking) ──
    try:
        source_hash = hashlib.sha256(unit.file.resolve().read_bytes()).hexdigest()
    except OSError:
        source_hash = ""
    return source_hash


def _process_unit(
    unit,
    config_hash,
    project_root,
    vendor_patterns,
    project_patterns,
    index_refs,
    db_path,
    existing_files,
    lock=None,
    conn=None,
    force=False,
    pre_parsed=None,
    parse_timing=(0.0, 0.0),
    hashes=None,
    build_dir_patterns=None,
    skip_files: frozenset[str] | None = None,
    context_id: str = "",
    world_ctx=None,
):
    """Process one translation unit: check staleness, parse, store.

    ``world_ctx`` is the Slice-2 per-run ``V2WorldContext`` (orchestration
    only, task §35); ``None`` keeps the Slice-1 behavior byte-for-byte.

    Opens its own DB connection when *conn* is ``None``, otherwise reuses
    the caller-supplied connection (persistent per-worker connection).

    Serializes DB writes via *lock* when supplied (``threading.Lock`` for
    intra-process synchronization).  When *lock* is ``None``, the caller
    is responsible for serialisation (sequential path with fcntl wrap).

    When *pre_parsed* is not ``None``, the staleness check and libclang
    parsing are skipped — the caller already performed them and the lock
    is only held for the DB write.  *parse_timing* provides the
    ``(t_start, t_end)`` values for the summary statistics.

    All TUs are indexed — no exclusion filtering.

    Args:
        unit: The ``CompilationUnit`` to parse (file path + clang flags).
        config_hash: Content-addressable build fingerprint for scoping
            all DB operations to the current build configuration.
        project_root: Root directory used for path resolution.
        vendor_patterns: LIKE patterns for vendor/SDK directories.
        project_patterns: LIKE patterns for user-declared project directories.
        index_refs: When True, extract call-graph references.
        db_path: Path to the SQLite database — used to open a connection
            when *conn* is ``None``.
        existing_files: Dictionary mapping file paths to ``(file_id, mtime)``
            tuples, used to skip unchanged translation units.
        lock: Optional ``threading.Lock`` used as a context manager to
            serialise DB writes between workers (intra-process).
        conn: Optional persistent SQLite connection — when provided, the
            caller manages its lifecycle (open once per worker thread,
            close after all TUs).  When ``None``, a connection is opened
            and closed for this call.
        pre_parsed: When not ``None``, the result of ``extract_all()``
            from a prior parse.  Staleness check, parse, and exception
            handling on the parsing step are skipped — the caller already
            decided the TU needs storing.
        parse_timing: ``(t_start, t_end)`` tuple from the caller's
            ``time.monotonic()`` measurements around the parse step.
            Ignored when *pre_parsed* is ``None``.

    Returns:
        A tuple ``(status, symbols_added, refs_added, timing, headers)`` where
        *status* is ``"updated"`` (new or modified symbols stored),
        ``"unchanged"`` (mtime matched — no work needed), or ``"skipped"``
        (failed during parsing), and
        *headers* is a list of ``{path, hash, generated}`` dicts for included
        header files (empty list for unchanged/skipped).
    """

    if pre_parsed is not None:
        parsed = pre_parsed
        t_parse_start = parse_timing[0]
        t_parse_end = parse_timing[1]
    else:
        file_path = _normalize_file_path(str(unit.file.resolve()), project_root)
        force_refs = force or os.environ.get("FW_CONTEXT_FORCE_REFINDEX") == "1"
        if not force_refs and file_path in existing_files:
            rec = existing_files[file_path]
            # get_file_hashes returns FileHashRecord (attribute access),
            # get_file_mtimes returns tuple[int, float] (positional).
            stored_mtime = rec.mtime if hasattr(rec, "mtime") else rec[1]
            try:
                current_mtime = unit.file.stat().st_mtime if unit.file.exists() else 0.0
            except OSError:
                current_mtime = 0.0
            if current_mtime <= stored_mtime + MTIME_TOLERANCE_S:
                return ("unchanged", 0, 0, (0.0, 0.0, 0.0), [])

        # Parse with libclang outside any lock — this is the expensive
        # CPU-bound step.  Only serialise DB writes, not parsing.
        from .symbols import extract_all

        t_parse_start = time.monotonic()
        try:
            parsed = extract_all(
                unit,
                with_refs=index_refs,
                return_tu=True,
                skip_files=skip_files,
            )
        # Same reason as in `_check_and_parse_unit`: the parse error is not
        # inside SAFE_EXCEPT, thus it has to be named.
        except (*SAFE_EXCEPT, TranslationUnitLoadError) as exc:
            if is_fatal(exc):
                raise
            log.warning("skip TU %s: %s", unit.file.name, exc)
            return ("skipped", 0, 0, (0.0, 0.0, 0.0), [])
        t_parse_end = time.monotonic()

    # ── v2 shadow per-TU certification + publication (Phase-4 Slice 1) ──
    # Flag-gated and fully exception-isolated: with the v2 flag off this is
    # one cheap check; on any v2 failure legacy indexing continues untouched
    # (task §16).  Runs BEFORE the legacy write lock: expensive work stays
    # out of every writer transaction (task §23), and publication uses its
    # OWN short transaction on the separate v2 store.  The store_v2 module
    # is imported LAZILY (dynamic, only when the flag is on) so the Slice-0
    # inertness gate holds: flag off ⇒ store_v2 is never even loaded.
    # ── v2 per-TU certification + publication ──
    # M5: v2 is the DEFAULT semantic engine. Certification always runs
    # (it is the sole semantic write path in v2-only mode, and runs
    # alongside legacy in compat mode). Runs BEFORE the legacy write lock.
    try:
        import importlib
        _cert_mod = importlib.import_module(
            "fw_context_mcp.store_v2.certify")
        _cert_mod.certify_and_publish_for_unit(
            unit=unit, parsed=parsed, project_root=project_root,
            config_hash=config_hash, context_id=context_id,
            db_path=db_path, world=world_ctx)
    except Exception as _v2_exc:                  # pragma: no cover
        log.debug("v2 certify hook returned error: %s",
                   _v2_exc)

    # ── M7-FINAL: M6 DIRECT MATERIALIZATION (Issue A remediation) ──
    # Materialize canonical M6 facts DIRECTLY from the libclang
    # ExtractionResult, WITHOUT requiring a completed legacy DB.
    # This makes M6 the production semantic-materialization authority
    # (§5: normal indexing can produce a correct M6 production semantic
    # view without requiring a completed old semantic DB).
    # The legacy DB conversion path (index_legacy_db_into_m6) remains as
    # an explicit compat/benchmark mode, NOT a normal prerequisite.
    _m6_write_authority = os.environ.get(
        "FW_CONTEXT_M6_WRITE_AUTHORITY", "1"
    ).strip() in ("1", "true", "yes", "on")
    if _m6_write_authority and world_ctx is not None:
        try:
            from fw_context_mcp.store_v2.m6_direct_materialization import (
                materialize_unit_into_m6,
            )
            # Derive real World identity — NOT hardcoded 1.
            # Prefer the v2-derived world_id (set by _ensure_world_rows
            # during v2 certify). If v2 didn't set it, derive a
            # deterministic world_id from the build_name hash (stable
            # across restarts for the same build).
            _m6_world_id = getattr(world_ctx, "world_id", None)
            if not _m6_world_id:
                import hashlib
                _m6_build_name_fallback = (
                    getattr(world_ctx, "build_name", None) or "default")
                _m6_world_id = (
                    int(hashlib.sha256(
                        _m6_build_name_fallback.encode()
                    ).hexdigest()[:8], 16) % 1000000) + 1
                world_ctx.world_id = _m6_world_id
            _m6_build_name = getattr(world_ctx, "build_name", None) or "default"
            _m6_store = getattr(world_ctx, "m6_store", None)
            if _m6_store is not None:
                materialize_unit_into_m6(
                    store=_m6_store,
                    parsed=parsed,
                    project_root=project_root,
                    build_name=_m6_build_name,
                    world_id=_m6_world_id,
                    config_hash=config_hash,
                    context_id=context_id,
                )
        except Exception as _m6_exc:                # pragma: no cover
            log.debug("M6 direct materialization hook error: %s",
                       _m6_exc)

    # ── M5-1: legacy DB writes are gated behind explicit compat mode ──
    # When _legacy_compat_on() is False (the M5 default), normal indexing is
    # v2-only: the v2 certification/publication/binding above is the sole
    # semantic write path. No legacy index.db is opened, no legacy symbols
    # are stored. Legacy storage is generated ONLY when the operator opts in
    # via FW_CONTEXT_LEGACY_COMPATIBILITY_MODE=1.
    if not _legacy_compat_on():
        # v2-only path: v2 certify/publish/bind already ran above. Return
        # a no-legacy-write result. Timing reflects parse only (no legacy
        # write occurred).
        t_parse = t_parse_end - t_parse_start
        return ("updated", 0, 0, (t_parse, 0.0, 0.0), [])

    # Resolve connection: caller-supplied or own.
    # Persistent per-worker connections avoid open()+close() per TU
    # (~0.5 ms each).  When the caller manages the lifecycle (open
    # once per thread, close after all TUs), total indexing time
    # drops by 5-10 % on large projects with many small TUs.
    if conn is not None:
        own_conn = False  # caller-supplied, don't close
    else:
        conn = open_db(db_path)
        own_conn = True

    t_lock_start = time.monotonic()
    try:
        # threading.Lock (intra-process) or nullcontext (sequential path
        # where the caller holds fcntl write_lock across all TUs)
        lock_ctx: AbstractContextManager = lock if lock is not None else nullcontext()
        with lock_ctx:
            t_write_start = time.monotonic()
            with transaction(conn, checkpoint=False):
                syms_added, refs_added, headers = store_symbols_for_unit(
                    conn,
                    unit,
                    config_hash,
                    project_root,
                    vendor_patterns=vendor_patterns,
                    project_patterns=project_patterns,
                    index_refs=index_refs,
                    pre_parsed=parsed,
                    existing_files=existing_files,
                    hashes=hashes,
                    build_dir_patterns=build_dir_patterns,
                    skip_files=skip_files,
                    context_id=context_id,
                )
            t_write_end = time.monotonic()
            t_parse = t_parse_end - t_parse_start
            t_lock = t_write_start - t_lock_start
            t_write = t_write_end - t_write_start
            log.debug(
                "  TU %s: parse=%.1fs lock_wait=%.2fs write=%.1fs syms=%d refs=%d",
                unit.file.name,
                t_parse,
                t_lock,
                t_write,
                syms_added,
                refs_added,
            )
        timing = (t_parse, t_lock, t_write)
        return ("updated", syms_added, refs_added, timing, headers)
    except sqlite3.Error:
        log.error("Fatal DB error storing %s — stopping indexer", unit.file.name)
        raise
    except SAFE_EXCEPT as exc:
        if is_fatal(exc):
            raise
        msg = str(exc)
        if "unable to open database file" in msg:
            log.error("Fatal DB error storing %s: %s — stopping indexer", unit.file.name, exc)
            raise
        log.warning("skip TU %s: %s", unit.file.name, exc)
        return ("skipped", 0, 0, (0.0, 0.0, 0.0), [])
    finally:
        if own_conn:
            conn.close()




def _handle_unchanged(
    unit,
    check_status: str,
    hashes: tuple | None,
    conn: sqlite3.Connection,
    config_hash: str,
    project_root: Path,
    build_dir_patterns: list[str] | None,
    db_path: Path,
    existing_files: dict,
    skip_files: frozenset[str] | None = None,
    manifest_lookup: dict[str, dict] | None = None,
    content_backfill_needed: bool = True,
    context_id: str = "",
) -> dict:
    """Handle a TU that needs no re-parse — file-record and content bookkeeping.

    Updates the TU's file record and fills ifdef-filtered content.  The caller
    is responsible for applying the returned counters to its own state.

    Args:
        unit: The compilation unit being processed.
        check_status: Always ``"unchanged"`` (from
            :func:`_check_and_parse_unit`).  Kept as a parameter so the
            caller's tier dispatch stays explicit.
        hashes: ``(source_hash, flags_hash, manifest_entry_hash)`` tuple
            from the Tier 2 content-hash check, or ``None`` for Tier 1.
        conn: Open SQLite connection to the index database.
        config_hash: Active build config hash.
        project_root: Project root directory.
        build_dir_patterns: Build directory exclusion patterns.
        db_path: Path to the index database file.
        existing_files: Dict mapping file paths to ``FileHashRecord``.
        skip_files: Headers already processed by earlier TUs in this run.
        manifest_lookup: ``{tu path: manifest entry}`` from ``manifest.json``.
            A usable entry (real ``source_hash`` and ``flags_hash``) lets an
            unchanged TU skip the header/content pass entirely — see the
            comment below.
        content_backfill_needed: True when some file in the index still has
            an empty ``content`` column.  Computed once per run by the
            caller; when True the header/content pass runs even for
            unchanged TUs so the backfill can complete.

    Returns:
        dict with keys:
        - ``file_id`` (int | None): File record ID for use in Phase 2.
        - ``headers`` (dict | None): Collected headers for manifest update.
        - ``status`` (str): ``"unchanged (content)"`` or ``"unchanged"``.
        - ``content_filled`` (int): 1 if ifdef content was filled, 0 otherwise.
    """
    file_path_str = _normalize_file_path(str(unit.file.resolve()), project_root)
    try:
        tu_key = unit.file.resolve().relative_to(project_root).as_posix()
    except ValueError:
        tu_key = unit.file.resolve().as_posix()

    # An unchanged TU has nothing to contribute to the manifest: its stored
    # entry is still accurate.  Running the header/content pass anyway would
    # cost a full libclang parse per TU AND stamp current header hashes onto
    # a TU that was never re-parsed — erasing the only signal that its
    # headers are stale.  A preliminary or pre-flags_hash entry does not
    # qualify: those must be regenerated with real hashes.
    #
    # "reuse" is excluded — that path migrates a TU to a new config_hash and
    # the new manifest still needs its entry.
    # Per-(file, context) key: one file compiled with two flag sets has two
    # manifest entries, and a file-keyed dict resolved both contexts to the
    # same — the last — entry.  Legacy file keys still resolve.
    manifest_entry = None
    if manifest_lookup:
        manifest_entry = manifest_lookup.get((tu_key, context_id))
        if manifest_entry is None:
            manifest_entry = manifest_lookup.get(tu_key)
    manifest_entry_usable = bool(
        manifest_entry
        and manifest_entry.get("source_hash")
        and manifest_entry.get("flags_hash")
    )
    skip_header_pass = (
        check_status == "unchanged" and manifest_entry_usable and not content_backfill_needed
    )
    rec = existing_files.get(file_path_str)
    file_id = rec.file_id if rec else None
    try:
        current_mtime = unit.file.stat().st_mtime if unit.file.exists() else 0.0
    except OSError:
        current_mtime = 0.0

    content_filled = 0
    headers = None

    # Serialise via write_lock with 120 s timeout.
    # 120 s allows the slowest TU to finish its Phase 2 write before
    # this TU acquires the lock.  Shorter timeouts risk false
    # failures on CI machines with contended I/O.
    with write_lock(db_path.parent, timeout=120.0):
        with transaction(conn, checkpoint=False):
            if hashes is not None:
                # Tier 2 / Tier 2b: content-hash match — update or create file record
                source_hash, flags_hash, manifest_entry_hash = hashes
                content_hash_val = compute_tu_content_hash(source_hash, flags_hash, manifest_entry_hash)
                if file_id is not None:
                    conn.execute(
                        """UPDATE files SET mtime=?, content_hash=?, source_hash=?,
                           flags_hash=?
                           WHERE id=?""",
                        (current_mtime, content_hash_val, source_hash, flags_hash, file_id),
                    )
                else:
                    # New config_hash — create file record for this TU
                    file_id = upsert_file(
                        conn,
                        config_hash,
                        file_path_str,
                        unit.language,
                        mtime=current_mtime,
                        content_hash=content_hash_val,
                        source_hash=source_hash,
                        flags_hash=flags_hash,
                        context_id=context_id,
                    )
            elif file_id is not None:
                # Tier 1: mtime match — just refresh stored mtime
                conn.execute(
                    "UPDATE files SET mtime=? WHERE id=?",
                    (current_mtime, file_id),
                )
            if not skip_header_pass:
                # Fill ifdef-filtered file content via tokenization
                fc, hdrs = _build_filtered_file_content(
                    conn, unit, config_hash, project_root, build_dir_patterns=build_dir_patterns,
                    skip_files=skip_files,
                    context_id=context_id,
                )
                content_filled = fc
                if hdrs:
                    # Per-context key — the manifest updater writes one
                    # entry per compile action, each with its own header
                    # list (a file-keyed dict kept only the last
                    # context's list).
                    headers = {(tu_key, context_id): hdrs}

    return {
        "file_id": file_id,
        "headers": headers,
        "status": "unchanged (content)" if hashes is not None else "unchanged",
        "content_filled": content_filled,
    }


@dataclass
class UnitDecision:
    """What :func:`decide_units` says about one listing of compile_commands.json.

    *hashes* is ``(source_hash, flags_hash)`` of the listing, for its
    ``files`` row.  *mtime* is the time of the source file, read BEFORE its
    hash, thus the two describe the same text or the time is older (0.0
    when the file is gone).  *unchanged* is True when the index holds what a
    parse would give, and no parse is necessary.
    """

    hashes: tuple[str, str]
    mtime: float
    unchanged: bool


def decide_units(
    units: list,
    project_root: Path,
    existing_files,
    manifest: dict[str, list[dict]] | None,
    header_stale_tus: frozenset[str] = frozenset(),
    *,
    force: bool,
    hash_cache: dict[str, str] | None,
    header_table: dict[str, dict] | None,
    transient_defines: Collection[str],
) -> list[UnitDecision]:
    """Decide for each unit, in order, if it must be parsed again.

    A listing is unchanged when all of these are true:

    * No ``force`` and no ``FW_CONTEXT_FORCE_REFINDEX=1``.
    * The file is not in *header_stale_tus*.
    * The index holds a row for the file.
    * *manifest* holds an entry for the file with the flags hash of THIS
      listing (see :func:`_unchanged_entry`), and that entry is not stale:
      the source file and each header have the hash of the entry, and the
      entry has no ``needs_reparse`` mark.
    * No other listing of the same file must be parsed.

    No file time takes part.  Before, the first test was "the source file
    is not newer than the stored time", which did not see a copy with an
    older time, nor a change of the flags or of a header (the header pass
    covered the last one).

    The last rule is why all units are decided before the first parse.
    compile_commands.json can list one file more than once with other
    flags, and the index holds ONE set of rows for a file: the rows of the
    listing that the run parsed last.  A run that parsed only the listing
    whose flags changed left rows that depend on the order of the listings,
    and a full run gave other rows.  Thus each listing of the file is parsed.

    Args:
        manifest: ``{file: [entry, ...]}`` from ``manifest.load()``, one
            entry for each listing of the file in compile_commands.json.
            ``None`` (no manifest) parses every unit.
        header_stale_tus: Normalized paths of TUs that include a header
            whose content changed, or whose entry carries ``needs_reparse``
            (from ``runner._tus_to_requeue``).
        hash_cache: Shared ``{resolved path: sha256}`` memo for header
            hashes, so a header included by many TUs is read once per index
            run.
        header_table: The ``headers`` map of the manifest.  An entry holds
            header paths only, and their hashes are in this map.
        transient_defines: ``[index] transient_defines``, for the flags hash
            of the listing.  The manifest entries hold hashes of the same list.
    """
    force_all = force or os.environ.get("FW_CONTEXT_FORCE_REFINDEX") == "1"
    paths: list[str] = []
    hashes: list[tuple[str, str]] = []
    mtimes: list[float] = []
    own: list[bool] = []
    for unit in units:
        resolved_tu = unit.file.resolve()
        file_path = _normalize_file_path(str(resolved_tu), project_root)
        # The time first, then the hash: a save between the two leaves the
        # row with an older time than the text, and the staleness check of
        # the query side then hashes the file again.
        try:
            mtime = unit.file.stat().st_mtime
        except OSError:
            mtime = 0.0
        # compute_source_hash gives "" for a file it cannot read (a source
        # that the build lists and the disk lost): no entry holds "".
        source_hash = compute_source_hash(unit.file)
        flags_hash = (
            compute_flags_hash(unit.raw_entry, transient_defines=transient_defines)
            if unit.raw_entry is not None else ""
        )
        unchanged = False
        if (
            not force_all
            and file_path not in header_stale_tus
            and file_path in existing_files
            and manifest is not None
        ):
            try:
                # Windows adjudication: manifest keys are posix.
                tu_rel = resolved_tu.relative_to(project_root).as_posix()
            except ValueError:
                tu_rel = resolved_tu.as_posix()
            unchanged = _unchanged_entry(
                manifest.get(tu_rel, ()), source_hash, flags_hash, project_root,
                hash_cache=hash_cache, header_table=header_table,
            ) is not None
        paths.append(file_path)
        hashes.append((source_hash, flags_hash))
        mtimes.append(mtime)
        own.append(unchanged)

    behind = {path for path, unchanged in zip(paths, own, strict=True) if not unchanged}
    decisions = [
        UnitDecision(hashes=h, mtime=mtime, unchanged=unchanged and path not in behind)
        for path, h, mtime, unchanged in zip(paths, hashes, mtimes, own, strict=True)
    ]
    siblings = sum(1 for unchanged, d in zip(own, decisions, strict=True) if unchanged and not d.unchanged)
    if siblings:
        log.info("%d unit(s) parsed because another listing of the same file changed", siblings)
    return decisions


def _parse_unit(unit, index_refs, skip_files: set[str] | None, hashes: tuple[str, str]):
    """Parse a unit that :func:`decide_units` did not find unchanged.

    Does NOT write to the database — the caller is responsible for
    acquiring ``write_lock`` and calling ``_process_unit(pre_parsed=...)``
    to persist the result.

    Returns:
        * ``("skipped", None, None, None)`` — parse failed.
        * ``("updated", parsed, (t_start, t_end), hashes)`` — parsed
          successfully, ready for ``_process_unit(pre_parsed=parsed)``.
    """
    from .symbols import extract_all

    t_parse_start = time.monotonic()
    try:
        parsed = extract_all(
            unit,
            with_refs=index_refs,
            return_tu=True,
            skip_files=skip_files,
        )
    except sqlite3.Error:
        log.error("Fatal DB error parsing %s — stopping indexer", unit.file.name)
        raise
    # TranslationUnitLoadError is named next to SAFE_EXCEPT and not inside
    # it: the class derives straight from Exception, so the tuple
    # `(ValueError, TypeError, RuntimeError, AttributeError, sqlite3.Error,
    # OSError)` does not hold it.  Without the name here the exception left
    # the run, and `ops.py` — which handles the same failure by skipping —
    # never saw the unit at all.
    #
    # Measured on the Mbed project: a branch switch removed two generated zcbor
    # sources that compile_commands.json still listed, and those two files
    # out of 881 ended the run at translation unit 39.  The 842 units behind
    # them were never read.
    except (*SAFE_EXCEPT, TranslationUnitLoadError) as exc:
        if is_fatal(exc):
            raise
        msg = str(exc)
        if "unable to open database file" in msg:
            log.error("Fatal DB error parsing %s: %s — stopping indexer", unit.file.name, exc)
            raise
        log.warning("skip TU %s: %s", unit.file.name, exc)
        return ("skipped", None, None, None)
    t_parse_end = time.monotonic()
    return ("updated", parsed, (t_parse_start, t_parse_end), hashes)


def _unchanged_entry(
    entries,
    source_hash: str,
    flags_hash: str,
    project_root: Path,
    *,
    hash_cache: dict[str, str] | None,
    header_table: dict[str, dict] | None,
) -> dict | None:
    """Return the manifest entry that proves a listing unchanged, or None.

    *entries* are the entries of one file, one for each listing in
    compile_commands.json.  The entry of this listing is the one with
    *flags_hash*: Zephyr compiles ``misc/empty_file.c`` three times in one
    image with two sets of flags, and the entry of one set says nothing
    about the other.  Two listings with the same flags have entries that
    say the same, thus the first one answers.

    *source_hash* is the hash of the source file that the caller read, so
    that check_tu_staleness does not read the file a second time.

    An empty *flags_hash* (a unit without a compile_commands entry) or an
    entry without a source hash (a preliminary manifest) proves nothing,
    and the unit is parsed.
    """
    from .manifest import check_tu_staleness, resolve_headers

    if not flags_hash:
        return None
    for entry in entries:
        if entry.get("flags_hash") != flags_hash or not entry.get("source_hash"):
            continue
        stale, _ = check_tu_staleness(
            entry, project_root,
            hash_cache=hash_cache, headers=resolve_headers(entry, header_table),
            source_hash=source_hash,
        )
        return None if stale else entry
    return None
