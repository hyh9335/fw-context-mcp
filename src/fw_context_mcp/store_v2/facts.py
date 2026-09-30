"""store_v2.facts — Tier-T fact extraction + canonical serialization (Slice 1).

Extracts the per-TU SemanticState fact set from the EXISTING parse output
(:class:`ExtractionResult`) — no second libclang parse (task §19):

* **symbols_core** — every extracted symbol, canonical ``local_key`` ordinal
  (1-based, deterministic sort; NEVER a rowid/USR/path — schema §15.1).
* **edges** — references, indirect call sites, fp assignments, inheritance
  (parse-derived).  Symbol-rooted only (schema FK): a reference whose
  enclosing ``from_usr`` does not resolve inside this state is not an edge
  (documented adjudication; file-scope refs have no root symbol).
  Override edges are cross-TU derived facts (post-index) — deferred, see
  SLICE1_CERTIFICATION_CONTRACT.md.
* **condition_index** — directive regions of the certified closure files via
  the production ``indexer.conditionals`` text parser (cheap text pass, no
  re-parse); branch STATES are per-world Tier-B (no world exists in Slice 1).
* **file_table** — main file + closure members with cert-grade blob digests.
* **cold payload (normalized)** — macro definition rows (``value`` only —
  ``expanded_value`` stays B7-quarantined) + docstrings.

``fact_set_digest`` = ``H("fwctx-fsd/v1\\n" + canonical_json)`` over the
ordered hot rows + cold-payload digest.  It is the COMPLETENESS certificate
(D2 §6) and the W-1 tripwire comparison value — NEVER a reuse key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .keys import NOT_FOUND

FSD_DOMAIN = "fwctx-fsd/v1\n"
COLD_FORMAT = "fwctx-cold-normalized/v1"
FACT_SCHEMA_VERSION = 1


@dataclass
class StateFacts:
    """Canonical, ordered fact rows of one SemanticState (Slice-1 scope)."""

    file_table: list[tuple[int, str, str]] = field(default_factory=list)
    symbols: list[tuple] = field(default_factory=list)
    edges: list[tuple] = field(default_factory=list)
    conditions: list[tuple] = field(default_factory=list)
    # F3 (Slice 6C-F3): per-symbol source positions for historical retrieval.
    # (file_ordinal, fact_ordinal, symbol_local_key, line, col, end_line, end_col)
    positions: list[tuple] = field(default_factory=list)
    # Conditional-compilation branch states (T03/ADR-011/ADR-013).
    # Per-TU evaluation: (region_ordinal, branch_ordinal, taken) where
    # region_ordinal matches semantic_condition_index, branch_ordinal is
    # 1-based within each group, taken is 1 (TAKEN), 0 (FALSE), -1 (NOT_EVAL).
    branch_states: list[tuple[int, int, int]] = field(default_factory=list)
    # G5 (Dead-Code): reachability root edges with no source symbol
    # (ctor_root, implicit_construct with from_usr=None).  Stored separately
    # because semantic_edges requires a non-NULL local_key FK.
    # (edge_class, to_local_key|None, to_raw_usr)
    root_edges: list[tuple[str, int | None, str]] = field(default_factory=list)
    # G5 (Dead-Code): per-symbol GNU attributes (attr_flags) and linker
    # section.  Deterministic from the parse, so NOT in fact_set_digest —
    # supplementary Tier-A facts for reachability root discovery.
    # (local_key, attr_flags, section)
    symbol_attrs: list[tuple[int, str, str]] = field(default_factory=list)
    skipped_from_refs: int = 0

    @property
    def fact_row_count(self) -> int:
        return len(self.file_table) + len(self.symbols) + len(self.edges) \
            + len(self.conditions) + len(self.positions) \
            + len(self.branch_states)


# ── extraction ───────────────────────────────────────────────────────────────

def _display_path(path: str, project_root) -> str:
    from pathlib import Path as _P
    try:
        resolved = _P(path).resolve()
        return resolved.relative_to(_P(project_root)).as_posix()
    except (ValueError, OSError):
        try:
            return _P(path).resolve().as_posix()
        except OSError:
            return path


def _canon_posix(path: str) -> str:
    """Canonical file-identity lookup form (F3P).

    libclang reports location file names in whatever spelling the
    preprocessor recorded — on Windows a MIXED-SEPARATOR string such as
    ``'D:/dir/sub\\header.h'`` or a fully backslash path
    ``'D:\\dir\\main.c'``.  The certified file table (``_closure_walk``)
    stores ``Path(...).resolve().as_posix()`` forms.  Canonicalizing the
    captured file through the same ``Path.resolve().as_posix()`` makes
    the two sides comparable; anything that still fails to match is NOT
    in the certified file table and must fail closed (UNMAPPED_ORDINAL),
    never default to the main file."""
    from pathlib import Path as _P
    try:
        return str(_P(path).resolve().as_posix())
    except OSError:
        return str(path).replace("\\", "/")


#: file_ordinal persisted for a position whose captured file identity is
#: NOT in the certified state file table (fail closed — F3P).  Consumers
#: (F1 body admission, position presentation) must treat it as "no
#: trusted file", never as the main file and never repair it from disk.
UNMAPPED_ORDINAL = -1


def extract_state_facts(parsed, cert) -> StateFacts:
    """Build the canonical fact set from the parse output + certificate."""
    facts = StateFacts()

    # file table: main (ordinal 0) + closure members in first-inclusion order.
    facts.file_table.append((0, "main", cert.source_blob_sha))
    # F3P: the lookup keys must be in the SAME canonical form as the
    # captured symbol file identity.  Closure members already store
    # resolve().as_posix() forms (certify._closure_walk); the main file
    # is keyed by both its canonical absolute form and the cert's
    # project-relative display path.
    ordinal_by_path: dict[str, int] = {}
    main_abs = getattr(cert, "main_file_abs_path", "") or ""
    if main_abs:
        ordinal_by_path[_canon_posix(main_abs)] = 0
    if cert.main_file_display:
        ordinal_by_path[cert.main_file_display] = 0
    for i, member in enumerate(cert.closure, start=1):
        facts.file_table.append((i, member.role, member.blob_sha or NOT_FOUND))
        ordinal_by_path[member.display_path] = i
        ordinal_by_path[member.abs_path] = i

    # symbols: deterministic canonical order → 1-based local_key.
    def _sym_sort_key(s):
        return (s.usr, s.name, s.qualified_name, s.kind, s.file, s.line,
                s.column, bool(s.is_definition), s.signature)

    ordered = sorted(parsed.symbols, key=_sym_sort_key)
    local_by_usr: dict[str, int] = {}
    rows: list[tuple] = []
    # F3 (Slice 6C-F3): positions are non-keyed facts (INV-3), stored once
    # per (state, file_ordinal, fact_ordinal).  file_ordinal is resolved
    # against the cert's file_table (main=0, closure members 1..N).
    pos_rows: list[tuple] = []
    attr_rows: list[tuple] = []
    for idx, s in enumerate(ordered, start=1):
        rows.append((idx, s.usr, s.name, s.kind, int(bool(s.is_definition)),
                     int(bool(s.is_virtual)), int(bool(s.is_pure_virtual)),
                     int(bool(s.is_template)), s.signature))
        local_by_usr.setdefault(s.usr, idx)
        # G5: capture GNU attr_flags + section (supplementary Tier-A facts
        # for dead-code reachability root discovery — used/retain/constructor
        # attributes and KEEP-protected linker sections).
        attr_flags = getattr(s, 'attr_flags', '') or ''
        section = getattr(s, 'section', '') or ''
        if attr_flags or section:
            attr_rows.append((idx, attr_flags, section))
        # F3: capture source positions (file_ordinal, fact_ordinal, local_key,
        # line, col, end_line, end_col).  file_ordinal resolves the
        # CAPTURED file identity (libclang location file, canonicalized
        # by _canon_posix) against the certified file table:
        # 0 = main TU file, 1..N = closure members, UNMAPPED_ORDINAL (-1)
        # = file not in the certified table → FAIL CLOSED (F3P).  The
        # pre-F3P behavior defaulted unmatched paths to 0, attributing
        # every header symbol to the main file's blob.
        try:
            sym_path = getattr(s, 'file', '') or ''
            if sym_path:
                file_ord = ordinal_by_path.get(_canon_posix(sym_path),
                                               UNMAPPED_ORDINAL)
            else:
                file_ord = UNMAPPED_ORDINAL
        except Exception:
            file_ord = UNMAPPED_ORDINAL
        #
        # Coordinate semantics (probe-confirmed, F3R hardening):
        #   line      1-based
        #   col       1-based BYTE column (clang_getInstantiationLocation)
        #   end_line  extent end line (same-file guard; 0 if unavailable)
        #   end_col   extent end column, 1-based BYTE, HALF-OPEN (one past
        #             the last extent byte); 0 if unavailable
        end_line = getattr(s, 'end_line', 0) or getattr(s, 'line', 0)
        end_col = getattr(s, 'end_column', 0) or 0
        pos_rows.append((file_ord, idx, idx, getattr(s, 'line', 0),
                         getattr(s, 'column', 0), end_line, end_col))
    facts.symbols = rows
    facts.positions = pos_rows
    facts.symbol_attrs = attr_rows

    # edges: canonical tuples, deduped, ordinals per from-symbol.
    raw_edges: list[tuple] = []

    def _from_local(usr) -> int | None:
        if not usr:
            return None
        return local_by_usr.get(usr)

    def _add(local_key: int, edge_class: str, to_local: int | None,
             to_usr: str, to_name: str, certainty: str, resolution: str) -> None:
        raw_edges.append((local_key, edge_class, to_local, to_usr or "",
                          to_name or "", certainty or "", resolution or ""))

    # G5: rootless edges — refs with from_usr=None have no source symbol
    # (file-scope struct init lists, function pointer table entries, GNU
    # constructor attributes, implicit C++ global constructors).  These are
    # critical for dead-code analysis: a function referenced ONLY through a
    # struct init list (e.g. .tp_dealloc = foo) is "registered" but may not
    # be "called" through any resolved call site → possibly_dead.
    # Store as root_edges (edge_class, to_local_key|None, to_raw_usr).
    root_edges: list[tuple[str, int | None, str]] = []

    for ref in parsed.references:
        rk = getattr(ref, "ref_kind", "")
        from_usr = getattr(ref, "from_usr", None)
        src = _from_local(from_usr)
        if src is None:
            # G5: capture ALL rootless refs (ctor_root, implicit_construct,
            # ref, indirect, call) as root_edges.  The edge_class preserves
            # the original ref_kind so the dead-code adapter can distinguish
            # constructor roots (definitely live) from struct-init refs
            # (registered, possibly_dead) from plain refs (referenced).
            tgt = local_by_usr.get(ref.to_usr)
            root_edges.append((rk or "ref", tgt, ref.to_usr or ""))
            facts.skipped_from_refs += 1
            continue
        tgt = local_by_usr.get(ref.to_usr)
        _add(src, rk, tgt, ref.to_usr, "", "exact", "")
    for ics in parsed.indirect_call_sites:
        src = _from_local(getattr(ics, "from_usr", None))
        if src is None:
            # G5: rootless indirect call sites (struct init list function
            # pointer entries) — capture as root_edges.
            tgt = local_by_usr.get(ics.target_usr)
            root_edges.append(("indirect", tgt, ics.target_usr or ""))
            facts.skipped_from_refs += 1
            continue
        tgt = local_by_usr.get(ics.target_usr)
        _add(src, "indirect", tgt, ics.target_usr, ics.target_name,
             ics.certainty, ics.evidence)
    for fpa in parsed.fp_assignments:
        src = _from_local(getattr(fpa, "lhs_usr", None))
        if src is None:
            facts.skipped_from_refs += 1
            continue
        tgt = local_by_usr.get(fpa.rhs_usr)
        _add(src, "fp_assign", tgt, fpa.rhs_usr, fpa.rhs_name, "exact",
             getattr(fpa, "method", ""))
    for inh in parsed.inheritance:
        src = _from_local(getattr(inh, "derived_usr", None))
        if src is None:
            facts.skipped_from_refs += 1
            continue
        tgt = local_by_usr.get(inh.base_usr)
        _add(src, "inherit", tgt, inh.base_usr, "", inh.access,
             "virtual" if inh.is_virtual else "")

    seen_edges: set[tuple] = set()
    canon_edges: list[tuple] = []
    # Slice-1 hotfix (Slice-3 audit finding): to_local may be None for an
    # unresolved reference target (e.g. a parameter USR that has no symbol
    # row), and a plain tuple sort then raises TypeError when two edges of
    # the same (local_key, edge_class) meet a None against an int.  The
    # explicit key keeps the previous total order for every input that
    # sorted() already accepted (None sorts after all resolved targets) and
    # merely makes the previously-crashing shapes deterministic.
    for e in sorted(raw_edges, key=lambda e: (
            e[0], e[1], (e[2] is None, e[2] if e[2] is not None else 0),
            e[3], e[4], e[5], e[6])):
        if e in seen_edges:
            continue
        seen_edges.add(e)
        canon_edges.append(e)
    counters: dict[int, int] = {}
    final_edges: list[tuple] = []
    for (local_key, edge_class, to_local, to_usr, to_name, certainty,
         resolution) in canon_edges:
        counters[local_key] = counters.get(local_key, 0) + 1
        final_edges.append((local_key, counters[local_key], edge_class,
                            to_local, to_usr, to_name, certainty, resolution))
    facts.edges = final_edges
    facts.root_edges = root_edges

    # condition regions: production text parser over certified closure files.
    facts.conditions, _region_meta = _condition_regions(cert, ordinal_by_path)
    # branch states: evaluate each group against the preprocessor's
    # skipped_ranges (T03/ADR-011).  Per-TU because the macro environment
    # decides which branches are taken — the SAME condition structure can
    # have different evaluations under different -D sets.
    facts.branch_states = _evaluate_branch_states(parsed, cert, _region_meta)
    # Attach branch_states to the cert so bind_tu → _insert_branch_states
    # can write them to tu_branch_states.  Without this, build scripts that
    # use the lower-level API (build_certificate + extract_state_facts +
    # publish_one_shot + bind_tu) would leave tu_branch_states empty.
    cert.branch_states = list(facts.branch_states)
    return facts


def _condition_regions(cert, ordinal_by_path: dict[str, int]
                        ) -> tuple[list[tuple], dict]:
    """Directive regions (no branch states — those are per-world Tier-B).

    Returns (rows, region_meta) where region_meta carries the internal
    structure needed by ``_evaluate_branch_states``:
    ``per_file`` (file_ordinal, groups, blob) and ``region_ordinal`` map
    ``(file_ordinal, line) -> region_ordinal``.
    """
    from fw_context_mcp.indexer.conditionals import (
        build_conditional_tree,
        parse_conditional_directives,
    )

    # main file text: a cheap text read (no parse); closure members already
    # carry their text from the cert-grade rehash.
    from pathlib import Path as _P
    try:
        main_text = _P(_resolve_main(cert)).read_text(encoding="utf-8",
                                                      errors="replace")
    except OSError:
        main_text = None
    sources: list[tuple[int, str | None, str]] = [(0, main_text,
                                                   cert.source_blob_sha)]
    for i, member in enumerate(cert.closure, start=1):
        sources.append((i, member.text, member.blob_sha or NOT_FOUND))

    per_file: list[tuple[int, list, str]] = []
    for file_ordinal, text, blob in sources:
        if not text:
            continue
        dirs = parse_conditional_directives(text.splitlines())
        if not dirs:
            continue
        groups = build_conditional_tree(dirs)
        per_file.append((file_ordinal, groups, blob))

    # deterministic region ordinals: sort every directive line by
    # (file_ordinal, line) across the whole state.
    flat: list[tuple[int, int]] = []
    for file_ordinal, groups, _blob in per_file:
        for group in groups:
            _collect_lines(group, file_ordinal, flat)
    flat.sort()
    region_ordinal = {key: idx for idx, key in enumerate(flat, start=1)}

    rows: list[tuple] = []
    for file_ordinal, groups, blob in per_file:
        def _walk(group, parent_ord: int | None) -> None:
            my = region_ordinal.get((file_ordinal, group.root.line))
            rows.append((my, parent_ord, group.root.kind,
                         group.root.condition or "", blob))
            for branch in group.branches:
                bline = branch.directive.line
                if bline == group.root.line:
                    continue
                bord = region_ordinal.get((file_ordinal, bline))
                rows.append((bord, my, branch.kind,
                             branch.directive.condition or "", blob))
            for nested in group.nested:
                _walk(nested, my)
        for group in groups:
            _walk(group, None)
    rows.sort(key=lambda r: (r[0] if r[0] is not None else 1 << 60))

    region_meta = {"per_file": per_file, "region_ordinal": region_ordinal}
    return rows, region_meta


def _evaluate_branch_states(parsed, cert, region_meta: dict
                             ) -> list[tuple[int, int, int]]:
    """Evaluate conditional branch states from clang skipped_ranges (T03).

    Uses the SAME region_ordinal mapping as ``_condition_regions`` so the
    results join cleanly with ``semantic_condition_index``.  Returns a
    list of ``(region_ordinal, branch_ordinal, taken)`` tuples where
    ``branch_ordinal`` is 1-based within each conditional group.

    When skipped_ranges are unavailable (libclang lacks the symbol, or
    ``parsed.tu`` is None), returns an empty list — callers must
    fail-open (show all branches as active) rather than fabricate states.
    """
    if not region_meta or not region_meta.get("per_file"):
        return []
    tu = getattr(parsed, "tu", None)
    if tu is None:
        return []

    from fw_context_mcp.indexer.conditionals import evaluate_conditional_tree
    from fw_context_mcp.indexer.skipped_ranges import (
        collect_skipped_and_kept,
    )

    try:
        skipped_map, kept_map = collect_skipped_and_kept(tu)
    except Exception:
        return []

    region_ordinal = region_meta["region_ordinal"]
    out: list[tuple[int, int, int]] = []

    # Build a reverse map: (file_ordinal) → set of skipped lines.
    # The skipped_map is keyed by Path; we need to match cert paths.
    from pathlib import Path as _P
    # Build path → file_ordinal mapping (same as _condition_regions).
    path_to_ord: dict[str, int] = {}
    main_abs = getattr(cert, "main_file_abs_path", "") or ""
    if main_abs:
        path_to_ord[_P(main_abs).resolve().as_posix()] = 0
    for i, member in enumerate(cert.closure, start=1):
        if member.abs_path:
            try:
                path_to_ord[_P(member.abs_path).resolve().as_posix()] = i
            except OSError:
                pass

    for file_ordinal, groups, _blob in region_meta["per_file"]:
        # Find the skipped lines for this file.
        skipped_set: set[int] = set()
        kept_set: set[int] = set()
        for path_obj, lines in skipped_map.items():
            try:
                posix_path = path_obj.resolve().as_posix()
            except OSError:
                posix_path = str(path_obj).replace("\\", "/")
            if path_to_ord.get(posix_path) == file_ordinal:
                skipped_set = lines
                break
        for path_obj, lines in kept_map.items():
            try:
                posix_path = path_obj.resolve().as_posix()
            except OSError:
                posix_path = str(path_obj).replace("\\", "/")
            if path_to_ord.get(posix_path) == file_ordinal:
                kept_set = lines
                break

        if not skipped_set and not kept_set:
            # No skipped ranges for this file → all branches TAKEN.
            # Still need to record branch states (all taken).
            pass

        try:
            evaluations = evaluate_conditional_tree(
                groups, skipped=skipped_set, kept=kept_set)
        except Exception:
            continue

        # Walk ALL groups (top-level + nested) — the evaluations dict
        # has entries for every group, including those nested inside a
        # FALSE or NOT_EVALUATED enclosing group (their branches are all
        # NOT_EVALUATED in that case, which is the correct answer).
        def _emit_group_states(group) -> None:
            ev = evaluations.get(group.root.line)
            if ev is not None:
                for b_idx, state in enumerate(ev.states, start=1):
                    r_ord = region_ordinal.get(
                        (file_ordinal, state.line))
                    if r_ord is not None:
                        out.append((r_ord, b_idx, int(state.taken)))
            for nested in group.nested:
                _emit_group_states(nested)

        for group in groups:
            _emit_group_states(group)

    out.sort()
    return out


def _collect_lines(group, file_ordinal: int, out: list) -> None:
    out.append((file_ordinal, group.root.line))
    for branch in group.branches:
        if branch.directive.line != group.root.line:
            out.append((file_ordinal, branch.directive.line))
    for nested in group.nested:
        _collect_lines(nested, file_ordinal, out)


def _resolve_main(cert) -> str:
    # Prefer the absolute path captured at certification time (F3).
    main_abs = getattr(cert, "main_file_abs_path", "") or ""
    if main_abs:
        return main_abs
    for m in cert.closure:            # pragma: no cover — defensive
        if m.display_path == cert.main_file_display:
            return m.abs_path
    return cert.main_file_display


# ── cold payload (normalized) ────────────────────────────────────────────────

def build_cold_payload(parsed, cert, facts: StateFacts) -> dict:
    """Normalized-only cold payload: macro definitions + docstrings."""
    from pathlib import Path as _P
    # F3P: same canonical-identity rule as positions — key the main file
    # by its canonical absolute form too (macro rows already carry
    # resolved posix paths from the extraction's resolve_fn).
    ordinal_by_path: dict[str, int] = {}
    main_abs = getattr(cert, "main_file_abs_path", "") or ""
    if main_abs:
        ordinal_by_path[_canon_posix(main_abs)] = 0
    ordinal_by_path[cert.main_file_display] = 0
    for i, member in enumerate(cert.closure, start=1):
        ordinal_by_path[member.display_path] = i
        ordinal_by_path[member.abs_path] = i

    macro_rows: list[list] = []
    for m in parsed.macros:
        f_ord = ordinal_by_path.get(_canon_posix(m.file), -1)
        if f_ord is None:
            f_ord = -1
        macro_rows.append([m.name, m.value, int(bool(m.is_function_like)),
                           m.def_source, f_ord, int(m.line)])
    macro_rows.sort()

    docstrings: list[list] = []
    for idx, s in enumerate(sorted(parsed.symbols,
                                   key=lambda s: (s.usr, s.name, s.kind,
                                                  s.file, s.line)), start=1):
        if s.docstring:
            docstrings.append([idx, s.docstring])
    docstrings.sort()

    return {"format": COLD_FORMAT, "macro_defs": macro_rows,
            "docstrings": docstrings}


# ── canonical serialization + digest ─────────────────────────────────────────

def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def cold_payload_digest(cold: dict) -> str:
    return hashlib.sha256(
        ("fwctx-cold/v1\n" + _canonical_json(cold)).encode("utf-8")).hexdigest()


def fact_set_digest(facts: StateFacts, cold: dict) -> str:
    """Completeness certificate over the canonical state payload (D2 §6)."""
    payload = {
        "algorithm": "fwctx-fsd/v1",
        "file_table": [list(r) for r in facts.file_table],
        "symbols": [list(r) for r in facts.symbols],
        "edges": [list(r) for r in facts.edges],
        "conditions": [list(r) for r in facts.conditions],
        "cold_payload_digest": cold_payload_digest(cold),
    }
    return hashlib.sha256((FSD_DOMAIN + _canonical_json(payload))
                          .encode("utf-8")).hexdigest()
