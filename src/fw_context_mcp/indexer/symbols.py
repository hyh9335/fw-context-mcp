"""Extract symbols, references, inheritance edges, and macros from a CompilationUnit using libclang.

This module is the single-pass extraction engine of the fw-context indexer.
For each translation unit it produces:

* **Symbols** — functions, methods, classes, enums, variables, fields,
  namespaces.  Variable declarations are split into ``varglobal`` (file/class
  scope) and ``varlocal`` (inside function bodies) based on semantic parent.
* **References** — direct calls, reads, member accesses, indirect
  (function-pointer) edges, and implicit constructor invocations.
* **Inheritance** — C++ base-class edges with access specifier and virtual
  flag for each class/struct definition.
* **Macros** — ``#define`` name, value, and function-like flag.
* **Indirect call sites** — locations where a function-pointer field or
  variable is invoked (``driver.onData(buf, len)``).
* **FnPointerAssignment** — links an assignment site (``obj.onData = &handler``)
  to the assigned function, storing the helper-pointer typedef for Phase 3
  linking of assignments to call sites.
* **PendingDispatch** — deferred dispatch-registration patterns (e.g.
  ``EventQueue::call_every``) for synthetic ``ref_kind='dispatch'`` edges.

**Key design decisions:**

* **Single TU walk** — ``extract_all`` parses once and walks the AST once.
  Symbols and references are extracted in separate sub-walks over the same
  cursor stream, trading memory for speed (no re-parse).

* **Skip-files optimisation** — when a header was already visited by an
  earlier TU, its subtree is skipped during subsequent walks.
  ``_iter_cursor_skip`` uses a manual stack (instead of native
  ``walk_preorder``) to support subtree omission.  The native method is ~3×
  faster and is used when *skip_files* is empty.

* **Three-phase function-pointer resolution** — Phase 1 detects assignments
  (``_emit_fn_ptr_targets``).  Phase 2 records indirect call sites
  (``_handle_indirect_invocations``).  Phase 3 links assignments to call
  sites via the ``fn_ptr_type`` column (performed downstream in the
  database layer).  Arguments to dispatch-registration APIs are additionally
  captured via ``PendingDispatch``.

* **Source-line fallback** — embedded C++ (mbed-os, Zephyr) relies heavily on
  templates.  ``callback(&Class::method, this)`` and ``_timeout.attach(...)``
  often produce ``UNEXPOSED_EXPR`` or empty CALL_EXPR cursors that libclang
  cannot resolve.  ``_run_source_line_fallback`` scans raw source text inside
  known function bodies and emits the missing call edges via regex matching.

* **Conservative disambiguation** — ``_resolve_method_usr`` returns ``None``
  when candidate methods are ambiguous rather than guessing.  A wrong edge
  is worse than a missing edge (previously ``_timeout.attach(...)`` was
  mis-resolved to ``SerialBase::attach``).

* **Declaration-to-definition promotion** — ``_process_one_symbol`` tracks
  ``seen_usrs`` as ``USR → bool(definition)``.  A forward declaration is
  recorded as ``False``; when the definition is later encountered in the
  same TU, the flag is promoted to ``True`` — the definition wins.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import clang.cindex as cx

from ._dispatch_bridges import _DISPATCH_ENTRY_POINTS, _DISPATCH_METHOD_NAMES, _TYPE_ERASED_ISR_FUNCTIONS, lookup_bridge_rule
from .compile_commands import CompilationUnit
from .models import (
    CallSite,
    FnPointerAssignment,
    IndirectCallSite,
    InheritanceRecord,
    Macro,
    PendingDispatch,
    Reference,
    Symbol,
)
from .nvic import runtime_vector_reference
from .skipped_ranges import collect_skipped_lines

_log = logging.getLogger(__name__)

_INDEX: cx.Index | None = None
_index_lock = threading.Lock()


def _get_index() -> cx.Index:
    """Return the libclang Index singleton.

    ``cx.Index.create()`` is lightweight and Index objects internally
    serialize concurrent ``parse()`` calls, so a single shared instance
    is sufficient.
    """
    global _INDEX
    if _INDEX is None:
        with _index_lock:
            if _INDEX is None:
                _INDEX = cx.Index.create()
    return _INDEX


# ――― T09/ADR-007 template C-API bindings ―――――――――――――――――――――――――――――――――
# The pinned libclang wheel registers the raw C exports
# ``clang_getSpecializedCursorTemplate`` / ``clang_getTemplateCursorKind``
# but defines no ``Cursor.specialized_template`` property, so the property
# call raised AttributeError on every cursor and ``template_usr`` stayed
# dead (research T09 RC1).  Bind the C functions once via ``conf.lib`` —
# the shared ctypes precedent is ``skipped_ranges._load_skip_range_fns``.

_TEMPLATE_API: tuple | None = None
_TEMPLATE_API_TRIED = False


def _bind_template_api() -> tuple | None:
    """Bind the template C API once; return ``(get_spec, get_kind)`` or None.

    ``get_spec(cursor)`` maps a specialization cursor to the template it
    instantiates (returns the input cursor for non-specializations —
    callers MUST filter self-links).  ``get_kind(cursor)`` returns the
    CXCursorKind of the template pattern (c_uint).  Returns ``None`` when
    the loaded wheel lacks either export — discovery then degrades to the
    pre-T09 behavior and doctor reports the missing surface.
    """
    global _TEMPLATE_API, _TEMPLATE_API_TRIED
    if _TEMPLATE_API_TRIED:
        return _TEMPLATE_API
    _TEMPLATE_API_TRIED = True
    try:
        import ctypes

        from clang import cindex as _cindex

        lib = _cindex.conf.lib
        get_spec = lib.clang_getSpecializedCursorTemplate
        get_kind = lib.clang_getTemplateCursorKind
    except AttributeError:
        _log.debug(
            "libclang gives no clang_getSpecializedCursorTemplate/"
            "clang_getTemplateCursorKind — template instance discovery disabled"
        )
        _TEMPLATE_API = None
        return _TEMPLATE_API
    # CXCursor is returned by value; the wheel's Cursor is a ctypes
    # Structure subclass, so ctypes reconstructs it (measured T09 EXP5/6).
    get_spec.argtypes = [_cindex.Cursor]
    get_spec.restype = _cindex.Cursor
    get_kind.argtypes = [_cindex.Cursor]
    get_kind.restype = ctypes.c_uint
    _TEMPLATE_API = (get_spec, get_kind)
    return _TEMPLATE_API


_INVALID_TEMPLATE_KINDS = frozenset({
    cx.CursorKind.NO_DECL_FOUND,
    cx.CursorKind.INVALID_CODE,
    cx.CursorKind.UNEXPOSED_DECL,
})


def _specialized_template(cursor: cx.Cursor) -> cx.Cursor | None:
    """Return the template cursor *cursor* instantiates, or None.

    Uses the bound ``clang_getSpecializedCursorTemplate`` (ADR-007).
    Returns None when the cursor is not a specialization — including the
    "returns the input cursor" behavior for plain declarations, which
    callers must never read as a self-link (T09 adversarial: no false
    instances from template parameters).
    """
    api = _bind_template_api()
    if api is None:
        return None
    get_spec, _get_kind = api
    try:
        spec = get_spec(cursor)
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return None
    if spec is None:
        return None
    try:
        if spec.kind in _INVALID_TEMPLATE_KINDS:
            return None
        if not spec.get_usr():
            return None
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return None
    return spec


# Cursor kinds whose reference may name a template instance (T09
# discovery candidates, collected during the symbol walk over
# already-walked cursors — no second AST pass).
_TEMPLATE_EXPR_CANDIDATES = frozenset({
    cx.CursorKind.CALL_EXPR,
    cx.CursorKind.DECL_REF_EXPR,
    cx.CursorKind.MEMBER_REF_EXPR,
    cx.CursorKind.TYPE_REF,
})
_TEMPLATE_TYPE_CANDIDATES = frozenset({
    cx.CursorKind.VAR_DECL,
    cx.CursorKind.FIELD_DECL,
    cx.CursorKind.PARM_DECL,
})

# Kinds an instance observation may carry (mirrors _SYMBOL_KINDS for
# instantiable decls; PARM_DECL locals are never instance rows).
_INSTANCE_EMIT_KINDS = frozenset({
    cx.CursorKind.CLASS_DECL,
    cx.CursorKind.STRUCT_DECL,
    cx.CursorKind.UNION_DECL,
    cx.CursorKind.FUNCTION_DECL,
    cx.CursorKind.CXX_METHOD,
    cx.CursorKind.CONSTRUCTOR,
    cx.CursorKind.DESTRUCTOR,
    cx.CursorKind.FIELD_DECL,
    cx.CursorKind.VAR_DECL,
})


def _iter_cursor_skip(
    root_cursor: cx.Cursor,
    skip_files: frozenset[str],
    resolve_fn: Callable[[str], Path],
) -> Generator[cx.Cursor, None, None]:
    """Pre-order AST walk that skips subtrees of already processed source files.

    When a cursor's source file is in *skip_files*, the cursor and its
    entire subtree are omitted from the traversal — children are never
    pushed onto the stack.

    Uses a manual stack instead of the native ``walk_preorder``, which
    does not support subtree skipping.  The native method is about 3×
    faster (implemented in C) and is used when *skip_files* is empty.

    Each yielded cursor is guaranteed NOT to originate from a file in
    *skip_files*, so the caller does not need to re-check.

    Args:
        root_cursor: The top-level ``TRANSLATION_UNIT`` cursor.
        skip_files: Frozen set of resolved file paths to skip.
            A frozen set avoids accidental mutation across call sites.
        resolve_fn: Path resolver (typically an ``@lru_cache``-decorated
            local function in ``extract_all``).  Each call resolves a
            libclang file name string to a canonical ``Path``.

    Yields:
        Libclang cursors in pre-order, skipping any whose source file
        is in *skip_files*.
    """
    stack: list[cx.Cursor] = [root_cursor]
    while stack:
        c = stack.pop()
        loc = c.location
        if loc.file:
            fname = str(resolve_fn(str(loc.file.name)))
            if fname in skip_files:
                continue  # omit cursor + subtree

        yield c

        # Push children in reverse order for pre-order semantics.
        # Materializing into a list is necessary — get_children()
        # returns a lazy iterable, and reversed() needs a sequence.
        children = list(c.get_children())
        for child in reversed(children):
            stack.append(child)


# Frozen set for O(1) containment check on cursor kind during the symbol
# walk.  A frozenset is hashable (can be used as a dict key) and immutable
# (no accidental mutation in a long-running indexing process).
_SYMBOL_KINDS = frozenset({
    cx.CursorKind.FUNCTION_DECL,
    cx.CursorKind.FUNCTION_TEMPLATE,
    cx.CursorKind.CXX_METHOD,
    cx.CursorKind.CONSTRUCTOR,
    cx.CursorKind.DESTRUCTOR,
    cx.CursorKind.CLASS_DECL,
    cx.CursorKind.CLASS_TEMPLATE,
    cx.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
    cx.CursorKind.STRUCT_DECL,
    cx.CursorKind.UNION_DECL,
    cx.CursorKind.ENUM_DECL,
    cx.CursorKind.ENUM_CONSTANT_DECL,
    cx.CursorKind.TYPEDEF_DECL,
    cx.CursorKind.TYPE_ALIAS_DECL,
    cx.CursorKind.VAR_DECL,
    cx.CursorKind.FIELD_DECL,
    cx.CursorKind.NAMESPACE,
})

# Kinds where we index declarations even without a definition.
# Forward declarations of functions/methods are recorded so that
# find_all_callers_recursive can resolve calls even when the definition
# is in a different TU.  Definitions win in seen_usrs promotion.
_DECL_KINDS = frozenset({
    cx.CursorKind.FUNCTION_DECL,
    cx.CursorKind.FUNCTION_TEMPLATE,
    cx.CursorKind.CXX_METHOD,
})

# Callable definitions that establish an "enclosing function" for references.
# When a cursor with one of these kinds is encountered during the ref walk
# and it is a definition, it is pushed onto fn_stack — subsequent references
# within its extent are attributed to this function as the caller (from_usr).
_CALLABLE_KINDS = frozenset({
    cx.CursorKind.FUNCTION_DECL,
    cx.CursorKind.FUNCTION_TEMPLATE,
    cx.CursorKind.CXX_METHOD,
    cx.CursorKind.CONSTRUCTOR,
    cx.CursorKind.DESTRUCTOR,
})

# Reference expression kinds → ref_kind label
_REF_KINDS = {
    cx.CursorKind.CALL_EXPR: "call",
    cx.CursorKind.DECL_REF_EXPR: "ref",
    cx.CursorKind.MEMBER_REF_EXPR: "member",
}


# Cursor kinds that are valid targets for an indirect call (function pointers)
_INDIRECT_TARGET_KINDS = frozenset({
    cx.CursorKind.FUNCTION_DECL,
    cx.CursorKind.FUNCTION_TEMPLATE,
    cx.CursorKind.CXX_METHOD,
})

# Semantic parents under which a variable lives at file / namespace scope.
# A VAR_DECL with such a parent and no initializer is a C tentative
# definition — it allocates storage even though libclang answers
# ``is_definition() == False`` (T06 F3a).
_TU_SCOPED_KINDS = frozenset({
    cx.CursorKind.TRANSLATION_UNIT,
    cx.CursorKind.NAMESPACE,
    cx.CursorKind.LINKAGE_SPEC,
})


def _cursor_kind_label(kind: cx.CursorKind) -> str:
    """Map a libclang ``CursorKind`` to a short string label.

    Returns the user-facing kind string (e.g. ``"function"``, ``"class"``,
    ``"method"``) used to populate ``Symbol.kind``.  Falls back to the
    lowercase cursor kind name for unrecognised kinds.
    """
    mapping = {
        cx.CursorKind.FUNCTION_DECL: "function",
        cx.CursorKind.FUNCTION_TEMPLATE: "function",
        cx.CursorKind.CXX_METHOD: "method",
        cx.CursorKind.CONSTRUCTOR: "constructor",
        cx.CursorKind.DESTRUCTOR: "destructor",
        cx.CursorKind.CLASS_DECL: "class",
        cx.CursorKind.CLASS_TEMPLATE: "class",
        cx.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION: "class",
        cx.CursorKind.STRUCT_DECL: "struct",
        cx.CursorKind.UNION_DECL: "union",
        cx.CursorKind.ENUM_DECL: "enum",
        cx.CursorKind.ENUM_CONSTANT_DECL: "enum_constant",
        cx.CursorKind.TYPEDEF_DECL: "typedef",
        cx.CursorKind.TYPE_ALIAS_DECL: "typedef",
        cx.CursorKind.VAR_DECL: "variable",
        cx.CursorKind.FIELD_DECL: "field",
        cx.CursorKind.NAMESPACE: "namespace",
    }
    return mapping.get(kind, kind.name.lower())


def _is_anonymous_struct_or_union(cursor: cx.Cursor) -> bool:
    """True when *cursor* is an anonymous (unnamed) struct or union.

    libclang reports the spelling as ``""`` for some compilers or as
    ``"struct (unnamed at file:line)"`` / ``"union (anonymous at file:line)"``
    on others.  Both are treated as anonymous.
    """
    if cursor.kind not in (cx.CursorKind.STRUCT_DECL, cx.CursorKind.UNION_DECL):
        return False
    spelling = cursor.spelling
    if not spelling:
        return True
    if "(unnamed" in spelling or "(anonymous" in spelling:
        return True
    return False


def _build_anon_usr_to_field(
    tu_cursor: cx.Cursor,
    skip_files: frozenset[str] | None = None,
    resolve_fn: Callable[[str], Path] | None = None,
) -> dict[str, str]:
    """Build a mapping from anonymous struct/union USR → enclosing field name.

    Walks struct/union/class/namespace bodies (skipping function bodies)
    and for each FIELD_DECL whose immediate child is an anonymous struct
    or union, records the anon USR → field name association.

    This is used as a pre-scan before symbol extraction so that anonymous
    structs defined as ``struct { ... } _payload;`` get indexed with the
    field name (``_payload``) instead of ``"(unnamed struct at ...)"``.

    When *skip_files* and *resolve_fn* are provided, subtrees rooted in
    already-processed header files are skipped via an early-return in
    the internal ``_walk`` function.  The skip check covers all cursor
    kinds — not just the ones ``_walk`` recurses into — keeping the
    guard consistent with ``_iter_cursor_skip`` semantics.
    """
    mapping: dict[str, str] = {}

    def _walk(cursor: cx.Cursor) -> None:
        if skip_files and resolve_fn:
            loc = cursor.location
            if loc.file:
                fpath = str(resolve_fn(str(loc.file.name)))
                if fpath in skip_files:
                    return  # omit subtree from already-processed header

        if cursor.kind == cx.CursorKind.FIELD_DECL:
            for child in cursor.get_children():
                if _is_anonymous_struct_or_union(child):
                    child_usr = child.get_usr()
                    if child_usr:
                        mapping[child_usr] = cursor.spelling
            return  # don't walk into field children (they're the anon struct members)

        if cursor.kind in (
            cx.CursorKind.STRUCT_DECL,
            cx.CursorKind.UNION_DECL,
            cx.CursorKind.CLASS_DECL,
            cx.CursorKind.NAMESPACE,
            cx.CursorKind.TRANSLATION_UNIT,
        ):
            for child in cursor.get_children():
                _walk(child)

    _walk(tu_cursor)
    return mapping


def _qualified_name(
    cursor: cx.Cursor,
    anon_map: dict[str, str] | None = None,
) -> str:
    """Build the fully qualified name of a cursor via semantic parent traversal.

    Walks up the semantic parent chain (skipping the translation unit root)
    and joins each ancestor's spelling with ``::``.  Anonymous namespaces
    and unnamed entities produce empty segments which are collapsed by
    the join.

    When *anon_map* is provided and *cursor* is an anonymous struct/union
    with a recorded field name, the field name is used in place of the
    anonymous marker (e.g. ``"_radio_cmd"`` instead of
    ``"struct (unnamed at ...)"``).

    Returns a string like ``"namespace::Class::method"``, or an empty
    string for the translation unit root.
    """
    parts: list[str] = []
    c = cursor
    while c and c.kind != cx.CursorKind.TRANSLATION_UNIT:
        spelling = c.spelling
        usr = c.get_usr() if anon_map else None
        field_name = anon_map.get(usr) if usr and anon_map else None
        if field_name:
            parts.append(field_name)
        elif spelling:
            parts.append(spelling)
        c = c.semantic_parent
    parts.reverse()
    return "::".join(parts)


def _signature(cursor: cx.Cursor) -> str:
    """Build a human-readable signature.

    For callables returns ``"return_type name(param1, param2)"``.
    For variables and fields returns ``"type name"``.
    Returns an empty string for other non-callable cursors.
    """
    # Variables and fields: return type + name
    if cursor.kind in (cx.CursorKind.VAR_DECL, cx.CursorKind.FIELD_DECL):
        try:
            return f"{cursor.type.spelling} {cursor.spelling}"
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("_signature: type.spelling failed for %s", cursor.spelling)
            return cursor.spelling

    # Callables only beyond this point
    if cursor.kind not in (
        cx.CursorKind.FUNCTION_DECL,
        cx.CursorKind.FUNCTION_TEMPLATE,
        cx.CursorKind.CXX_METHOD,
        cx.CursorKind.CONSTRUCTOR,
        cx.CursorKind.DESTRUCTOR,
    ):
        return ""
    try:
        result_type = cursor.result_type.spelling
        params = ", ".join(
            f"{p.type.spelling} {p.spelling}".strip()
            for p in cursor.get_arguments()
        )
        return f"{result_type} {cursor.spelling}({params})"
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_signature: get_arguments failed for %s", cursor.spelling)
        return cursor.displayname


def _end_line(cursor: cx.Cursor, loc) -> int:
    """Last source line of the cursor's extent (full body for definitions).

    Uses libclang's exact AST extent — handles multi-line signatures, braces in
    strings/comments, macros, and templates correctly. Guards against the extent
    ending in a different file (can happen with macro expansions): returns 0 so
    callers fall back to a heuristic.
    """
    try:
        ext = cursor.extent
        end = ext.end
        if end.file and loc.file and end.file.name == loc.file.name and end.line >= loc.line:
            return end.line
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_end_line: extent failed for %s", cursor.spelling)
        pass
    return 0


def _end_column(cursor: cx.Cursor, loc) -> int:
    """End column of the cursor's extent — 1-based BYTE column, HALF-OPEN.

    libclang's ``cursor.extent.end`` points ONE PAST the last byte of the
    extent (half-open range).  Columns are 1-based byte columns from
    ``clang_getInstantiationLocation`` (a multi-byte UTF-8 character counts
    as multiple columns).  Same-file guard as ``_end_line``: returns 0 when
    the extent ends in a different file (macro expansion) so callers treat
    the end as unknown.
    """
    try:
        ext = cursor.extent
        end = ext.end
        if end.file and loc.file and end.file.name == loc.file.name and end.line >= loc.line:
            return end.column
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_end_column: extent failed for %s", cursor.spelling)
        pass
    return 0


def _docstring(cursor: cx.Cursor) -> str:
    """Extract and clean the Doxygen/Javadoc comment attached to *cursor*.

    Strips leading comment markers (``/**``, ``//``, ``*``) and trailing
    whitespace from each line, then joins with newlines.  Returns an
    empty string when no raw comment is attached.

    libclang parses comments attached to the declaration — even when the
    comment appears before the declaration in source — via
    ``cursor.raw_comment``.
    """
    raw = cursor.raw_comment
    if not raw:
        return ""
    # Strip comment markers: /**, //, *, trailing whitespace
    lines = raw.splitlines()
    cleaned: list[str] = []
    for line in lines:
        line = re.sub(r"^\s*(/\*+|//+|\*+/?)\s?", "", line)
        line = line.rstrip()
        if line:
            cleaned.append(line)
    return "\n".join(cleaned)


def _find_fn_refs_in_expr(
    cursor: cx.Cursor,
    _skip_usr: str | None = None,
    _depth: int = 0,
) -> list[cx.Cursor]:
    """Recursively extract function/method declarations referenced inside an expression.

    Depth-limited to prevent stack overflow on deeply nested C++ expressions.
    """
    _FN_REF_MAX_DEPTH = 500
    if _depth > _FN_REF_MAX_DEPTH:
        _log.warning("_find_fn_refs_in_expr: max depth %d exceeded", _FN_REF_MAX_DEPTH)
        return []

    results: list[cx.Cursor] = []

    # Direct reference to a callable (bare function name, method ref, etc.)
    if cursor.kind in (cx.CursorKind.DECL_REF_EXPR, cx.CursorKind.MEMBER_REF_EXPR):
        ref = cursor.referenced
        if ref is not None and ref.kind in _INDIRECT_TARGET_KINDS:
            # Skip if this is the callee of the enclosing function call
            if _skip_usr and ref.get_usr() == _skip_usr:
                return results
            loc = ref.location
            if loc.file:
                results.append(ref)
        return results

    # Address-of operator (&) — peel and recurse into the operand
    if cursor.kind == cx.CursorKind.UNARY_OPERATOR:
        for child in cursor.get_children():
            results.extend(_find_fn_refs_in_expr(child, _skip_usr, _depth + 1))
        return results

    # Nested call expression — e.g. callback(&Class::method, this).
    # Always recurse into arguments to find function pointer targets.
    # When the nested callee is directly resolvable AND has a valid file
    # location (not a built-in), upgrade _skip_usr to that callee's USR.
    # This prevents emitting indirect edges for the callee's OWN children:
    # e.g. inside ``callback(&handler, this)``, ``callback`` itself is the
    # callee, so its parameters (handler's address) should NOT also be
    # recorded as if they were called by ``callback``.
    # If the nested callee cannot be resolved (template wrapper), keep the
    # outer _skip_usr — we still descend into arguments to find targets.
    if cursor.kind == cx.CursorKind.CALL_EXPR:
        nested_callee = cursor.referenced
        nested_skip = _skip_usr
        if nested_callee is not None:
            nested_callee_usr = nested_callee.get_usr()
            if nested_callee_usr:
                loc = nested_callee.location
                if loc.file:
                    nested_skip = nested_callee_usr
        for child in cursor.get_children():
            results.extend(_find_fn_refs_in_expr(child, nested_skip, _depth + 1))
        return results

    # Default: recurse into all children (handles implicit casts, parentheses, etc.)
    for child in cursor.get_children():
        results.extend(_find_fn_refs_in_expr(child, _skip_usr, _depth + 1))

    return results


# Known callback-wrapper template names used by _spelling_is_callback_wrapper
# and _is_fn_ptr_type to detect function-pointer types stored behind a
# template facade.  Tuple for O(N) linear scan over a small fixed set.
_CALLBACK_TYPE_PREFIXES = (
    "Callback<", "function<", "std::function<", "EventHandler<",
)


def _spelling_is_callback_wrapper(spelling: str) -> bool:
    """True when *spelling* contains a known callback-wrapper template name."""
    if "<" not in spelling or ">" not in spelling:
        return False
    for prefix in _CALLBACK_TYPE_PREFIXES:
        if spelling.startswith(prefix):
            return True
        if "::" in spelling and prefix in spelling:
            return True
    return False


def _is_fn_ptr_type(t: cx.Type) -> bool:
    """True when *t* is (or resolves via typedef to) a function pointer.

    Handles three cases:
    1. Direct pointer-to-function types (e.g. ``void (*)(int)``)
    2. Template-based callback wrappers via canonical spelling
       (``Callback<void()>``, ``std::function<…>``)
    3. Unresolved types on cross-compiled toolchains — falls back to
       the original spelling (before canonical resolution), which
       libclang preserves even when the underlying type is unknown.
    """
    try:
        canon = t.get_canonical()
        if canon.kind == cx.TypeKind.POINTER:
            pointee = canon.get_pointee()
            return pointee.kind in (cx.TypeKind.FUNCTIONPROTO, cx.TypeKind.FUNCTIONNOPROTO)
        if canon.kind == cx.TypeKind.MEMBERPOINTER:
            pointee = canon.get_pointee()
            return pointee.kind in (cx.TypeKind.FUNCTIONPROTO, cx.TypeKind.FUNCTIONNOPROTO)
        if _spelling_is_callback_wrapper(canon.spelling):
            return True
    except (ValueError, TypeError, RuntimeError, AttributeError):
        pass
    # Fallback: original spelling for cross-compiled toolchains where
    # canonical resolution yields an incomplete type
    try:
        if _spelling_is_callback_wrapper(t.spelling):
            return True
    except (ValueError, TypeError, RuntimeError, AttributeError):
        pass
    return False


def _first_child_unwrapped(cursor: cx.Cursor) -> cx.Cursor | None:
    """Return the first child of *cursor*, unwrapping ``UNEXPOSED_EXPR`` nodes.

    Used to extract the callee expression from a ``CALL_EXPR`` when
    ``cursor.referenced`` is ``None`` — the first child is the callee,
    subsequent children are arguments.  ``UNEXPOSED_EXPR`` wrappers
    (common in template instantiations and macro expansions) are
    unwrapped recursively until a concrete expression kind is reached.
    """
    try:
        children = list(cursor.get_children())
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_first_child_unwrapped: get_children failed")
        return None
    if not children:
        return None
    first = children[0]
    while first.kind == cx.CursorKind.UNEXPOSED_EXPR:
        try:
            grandkids = list(first.get_children())
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("_first_child_unwrapped: UNEXPOSED_EXPR children failed")
            break
        if not grandkids:
            break
        first = grandkids[0]
    return first


def _call_expr_text(cursor: cx.Cursor) -> str:
    """Extract callee expression text from a CALL_EXPR's token stream.

    Returns everything before the opening ``(``, space-joined.
    ``driver->onData(buf, len)`` → ``"driver->onData"``
    ``stored_callback(42)``      → ``"stored_callback"``

    A macro-generated call site is the exception: its token stream does not
    begin at the cursor's own extent (it starts mid-expansion or inside the
    macro definition), so joining it yields garbled text such as
    ``"mcb_slot , v )"`` (T06 F5).  For such a site the text is sliced from
    the source at the cursor extent instead — the macro use site itself,
    e.g. ``"INVOKE(mcb_slot, v)"``.
    """
    parts: list[str] = []
    try:
        tokens = list(cursor.get_tokens())
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_call_expr_text: get_tokens failed")
        return ""
    if tokens:
        ext = cursor.extent
        first = tokens[0].extent.start
        _aligned = (first.line, first.column) == (ext.start.line, ext.start.column)
        if (_aligned and first.file is not None and ext.start.file is not None
                and Path(first.file.name) != Path(ext.start.file.name)):
            _aligned = False
        if not _aligned:
            return _source_text_at_extent(ext)
    for tok in tokens:
        if tok.spelling == "(":
            break
        parts.append(tok.spelling)
    return " ".join(parts)


def _source_text_at_extent(ext) -> str:
    """Read the source text covered by a source range (macro use site).

    Falls back to an empty string when the file cannot be read — a missing
    expr_text is a smaller loss than an index run that stops.
    """
    try:
        if ext.start.file is None:
            return ""
        lines = Path(ext.start.file.name).read_text(
            encoding="utf-8", errors="replace").splitlines()
        if ext.start.line == ext.end.line:
            return lines[ext.start.line - 1][ext.start.column - 1:ext.end.column - 1].strip()
        head = lines[ext.start.line - 1][ext.start.column - 1:]
        mid = lines[ext.start.line:ext.end.line - 1]
        tail = lines[ext.end.line - 1][:max(ext.end.column - 1, 0)]
        return "\n".join([head, *mid, tail]).strip()
    except (IndexError, ValueError, TypeError, RuntimeError, AttributeError, OSError):
        _log.debug("_source_text_at_extent: could not read extent text")
        return ""


def _extract_lhs_field(
    expr_cursor: cx.Cursor,
) -> tuple[str | None, str]:
    """Extract the USR and name of the field or variable on the LHS.

    ``obj->onData = &handler`` returns ``(USR_FIELD_onData, "onData")``.
    ``global_cb = &handler`` returns ``(USR_VAR_global_cb, "global_cb")``.

    Walks children recursively so it works through ``UNEXPOSED_EXPR``
    wrappers (common in designated initializers like ``.field = &fn``
    inside ``INIT_LIST_EXPR``).

    Returns ``(None, "")`` when the LHS is not a recognizable field
    or variable.
    """
    def _walk(c: cx.Cursor) -> tuple[str | None, str]:
        if c.kind in (cx.CursorKind.MEMBER_REF_EXPR, cx.CursorKind.MEMBER_REF):
            ref = c.referenced
            if ref is not None and ref.kind == cx.CursorKind.FIELD_DECL:
                return (ref.get_usr(), ref.spelling)
        elif c.kind == cx.CursorKind.DECL_REF_EXPR:
            ref = c.referenced
            if ref is not None and ref.kind in (
                cx.CursorKind.VAR_DECL, cx.CursorKind.PARM_DECL,
            ):
                return (ref.get_usr(), ref.spelling)
        # Recurse into UNEXPOSED_EXPR and other wrappers
        for grandchild in c.get_children():
            result = _walk(grandchild)
            if result[0] is not None:
                return result
        return (None, "")

    return _walk(expr_cursor)


def _process_one_base_specifier(
    child: cx.Cursor,
    cls_usr: str,
    cls_spelling: str,
    access_map: dict[cx.AccessSpecifier, str],
) -> InheritanceRecord | None:
    """Build an InheritanceRecord from one CXX_BASE_SPECIFIER child cursor.

    Returns ``None`` for non-base-specifier cursors or when the base class
    cannot be resolved (e.g. forward-declared template base with no
    instantiation visible in this TU).

    Calls ``clang_isVirtualBase`` directly through the C-API to detect
    virtual inheritance — libclang does not expose a Python wrapper for
    this query.
    """
    if child.kind != cx.CursorKind.CXX_BASE_SPECIFIER:
        return None
    base_ref = child.referenced
    if base_ref is None:
        return None
    base_usr = base_ref.get_usr()
    if not base_usr:
        return None
    base_loc = base_ref.location
    if not base_loc.file:
        return None
    access = access_map.get(child.access_specifier, "public")
    try:
        from clang.cindex import conf as _ciconf
        is_virt = bool(_ciconf.lib.clang_isVirtualBase(child))
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("clang_isVirtualBase failed for %s", cls_spelling)
        is_virt = False
    return InheritanceRecord(
        derived_usr=cls_usr,
        base_usr=base_usr,
        access=access,
        is_virtual=is_virt,
    )


def _extract_inheritance(class_cursors: list[cx.Cursor]) -> list[InheritanceRecord]:
    """Extract C++ inheritance edges from collected class/struct definition cursors."""
    inheritance: list[InheritanceRecord] = []
    _access_map = {
        cx.AccessSpecifier.PUBLIC: "public",
        cx.AccessSpecifier.PROTECTED: "protected",
        cx.AccessSpecifier.PRIVATE: "private",
    }
    for cls_cursor in class_cursors:
        cls_usr = cls_cursor.get_usr()
        if not cls_usr:
            continue
        try:
            for child in cls_cursor.get_children():
                rec = _process_one_base_specifier(child, cls_usr, cls_cursor.spelling, _access_map)
                if rec is not None:
                    inheritance.append(rec)
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("base class traversal failed for %s", cls_cursor.spelling)
            continue
    return inheritance


def _extract_macros(tu_cursor: cx.Cursor, resolve_fn, skip_files=None) -> list[Macro]:
    """Extract ``#define`` macro definitions, skipping headers in *skip_files*.

    Command-line macros (``-D`` flags) have no source file — ``loc.file``
    is None — and used to be dropped wholesale, which hid the very flags
    that steer the build (T03 C4).  They are attributed through the
    presumed location, which libclang spells exactly (``<command line>``
    vs ``<built-in>``), stored with an empty ``file`` and ``line=0``, and
    carry ``def_source='command_line'``.  Builtins stay out of the list:
    the ordinary macros table must not fill up with ``__GNUC__`` and
    friends; conditional provenance attributes them separately through
    its own TU walk (``indexer.conditionals``).

    Uses the module-level ``_log`` logger — no logging changes needed
    when the *skip_files* parameter is supplied.
    """
    from .conditionals import presumed_macro_buffer

    macros: list[Macro] = []
    for child in tu_cursor.get_children():
        if child.kind != cx.CursorKind.MACRO_DEFINITION:
            continue
        loc = child.location
        if not loc.file:
            buffer_name = presumed_macro_buffer(loc)
            if buffer_name == "<built-in>":
                # Excluded from the ordinary macros table by default; the
                # conditional-provenance walk still attributes them.
                continue
            try:
                is_fn_like = child.is_macro_function_like()
            except (ValueError, TypeError, RuntimeError, AttributeError):
                is_fn_like = False
            value = ""
            try:
                tokens = list(child.get_tokens())
                if len(tokens) > 1:
                    value = " ".join(t.spelling for t in tokens[1:])
            except (ValueError, TypeError, RuntimeError, AttributeError):
                value = ""
            macros.append(Macro(
                name=child.spelling,
                value=value,
                line=0,  # the -D writer stores 0: no source line exists
                is_function_like=is_fn_like,
                file="",
                def_source="command_line" if buffer_name == "<command line>" else "",
            ))
            continue

        fpath = str(resolve_fn(loc.file.name))
        if skip_files and fpath in skip_files:
            continue  # macro from already processed header

        try:
            is_fn_like = child.is_macro_function_like()
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("is_macro_function_like failed for %s", child.spelling)
            is_fn_like = False

        value = ""
        try:
            tokens = list(child.get_tokens())
            if len(tokens) > 1:
                value = " ".join(t.spelling for t in tokens[1:])
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("macro token extraction failed for %s", child.spelling)
            value = ""

        macros.append(Macro(
            name=child.spelling,
            value=value,
            line=loc.line,
            is_function_like=is_fn_like,
            file=fpath,
        ))
    return macros


def _class_match_score(hint: str, class_part: str) -> int:
    """Score how well a field-name hint matches a class name.

    Multi-level matching designed for the fallback path where field
    names use snake_case (``_radio_msg_manager``) but class names use
    CamelCase (``RadioMsgManager``), or where the field name is a
    superset of the class name (``_uart_driver`` → ``UART_DRIVER``).

    Returns 0 = no match, 1 = weak (token-level), 2 = good.
    """
    if not hint or not class_part:
        return 0
    class_lower = class_part.lower()

    if hint in class_lower:
        return 2
    if class_lower in hint:
        return 1

    hint_flat = hint.replace("_", "")
    if hint_flat and hint_flat in class_lower:
        return 2
    if hint_flat and class_lower in hint_flat:
        return 1

    hint_tokens = [t for t in hint.split("_") if t]
    if not hint_tokens:
        return 0
    match_count = sum(1 for t in hint_tokens if t in class_lower)
    if match_count == len(hint_tokens):
        return 2
    if match_count > 0:
        return 1

    return 0


class _QnToUsrMap(dict):
    """Qualified name → USR, carrying the per-name overload candidates.

    The bare-name index (``qn_to_usr``) collapses overloads
    last-declared-wins — two same-qualified-name callables leave ONE key,
    so an exact-key lookup cannot tell whether it resolved THE callable or
    an arbitrary overload (T05 research; wrong picks fabricate
    variant-specific edges).  ``overloads`` records every candidate per
    qualified name, and the resolvers abstain (``None``) when an exact key
    is ambiguous.

    The source-line and token fallback paths pass ``qn_to_usr`` through
    many helpers; attaching the overload map to the map object threads the
    collision gate through every existing call site without widening their
    signatures.  The resolvers also accept an explicit *qn_overloads*
    parameter, which wins when given (T14 SLICE-E, exact-USR preference).
    """

    overloads: dict[str, list[str]]


def _exact_usr_of(qn: str, qn_overloads: dict[str, list[str]] | None) -> str | None:
    """The unique exact candidate's USR for *qn*, or ``None``.

    One distinct candidate resolves; several candidates sharing the
    qualified name abstain — never emit a possibly-wrong edge.  Repeated
    identical USRs (the same symbol observed twice) still count as one.
    """
    if not qn_overloads:
        return None
    candidates = qn_overloads.get(qn)
    if not candidates:
        return None
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    return None


def _is_unambiguous_qn(qn: str, qn_overloads: dict[str, list[str]] | None) -> bool:
    """True when *qn* carries at most one distinct overload candidate.

    Names absent from a (partial) overload map are trusted — the map is
    evidence, not a closed world.
    """
    if qn_overloads is None:
        return True
    candidates = qn_overloads.get(qn)
    if not candidates:
        return True
    return len(set(candidates)) == 1


def _resolve_partial_qn(
    partial: str,
    qn_to_usr: dict[str, str],
    qn_overloads: dict[str, list[str]] | None = None,
) -> str | None:
    """Resolve a possibly-partial qualified name (e.g. ``WDT::_timeout_interrupt``)
    against *qn_to_usr* by exact match, then by ``::<partial>`` suffix match.

    The exact-key step is collision-aware: when the overload map (explicit
    argument, or the map attached to *qn_to_usr*) shows several candidates
    for the exact qualified name, the resolution abstains instead of
    returning the last-declared overload's USR.  A partial map without the
    key keeps the historical exact-key behavior.
    """
    if qn_overloads is None:
        qn_overloads = getattr(qn_to_usr, "overloads", None)
    usr = qn_to_usr.get(partial)
    if usr:
        if qn_overloads is not None and partial in qn_overloads:
            return _exact_usr_of(partial, qn_overloads)
        return usr
    suffix = f"::{partial}"
    for qn, u in qn_to_usr.items():
        if qn.endswith(suffix):
            return u
    return None


def _resolve_method_usr(
    method_name: str,
    qn_to_usr: dict[str, str],
    field_name: str = "",
    caller_qn: str = "",
    qn_overloads: dict[str, list[str]] | None = None,
) -> str | None:
    """Find USR for *method_name* in *qn_to_usr*.

    Disambiguation order (mirrors C++ scoping rules):

    1. Exact qualified name match — collision-aware: an exact key with
       several overload candidates abstains (``None``) rather than
       returning the last-declared one (T14 SLICE-E exact-USR preference).
    2. Single ``::method_name`` suffix candidate.
    3. Receiver-class hint from *field_name* — for ``obj.method()`` the
       receiver's type is authoritative, so a field-name match wins over
       the caller's class.
    4. Caller-class match — bare ``method()`` calls inside a class method
       resolve to sibling methods of the enclosing class (fixes
       ``wdt_reset()`` inside ``WDT::swdt_check``).
    5. Ambiguous → ``None``.  Never emit a possibly-wrong edge: previously
       an arbitrary first candidate was returned, which mis-resolved
       ``_timeout.attach(...)`` to ``mbed::SerialBase::attach``.
    """
    if qn_overloads is None:
        qn_overloads = getattr(qn_to_usr, "overloads", None)
    usr = qn_to_usr.get(method_name)
    if usr:
        if qn_overloads is not None and method_name in qn_overloads:
            return _exact_usr_of(method_name, qn_overloads)
        return usr
    suffix = f"::{method_name}"
    candidates: list[tuple[str, str]] = [
        (qn, u) for qn, u in qn_to_usr.items() if qn.endswith(suffix)
    ]
    if qn_overloads is not None:
        # The dict collapsed overloads last-declared-wins, so a "single"
        # suffix candidate can hide several real callables behind one
        # qualified name.  Keep only exact-unambiguous candidates —
        # never emit a possibly-wrong edge.
        candidates = [
            (qn, u) for qn, u in candidates if _is_unambiguous_qn(qn, qn_overloads)
        ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][1]
    if field_name:
        _hint = field_name.lstrip("_").lower()
        _scored: list[tuple[int, str, str]] = []
        for qn, u in candidates:
            _class_part = qn.rsplit("::", 2)[-2] if "::" in qn else ""
            _score = _class_match_score(_hint, _class_part)
            _scored.append((_score, qn, u))
        _scored.sort(key=lambda x: -x[0])
        if _scored[0][0] > 0:
            return _scored[0][2]
    if caller_qn:
        _caller_class = caller_qn.rsplit("::", 1)[0]
        if _caller_class:
            for qn, u in candidates:
                if qn.rsplit("::", 1)[0] == _caller_class:
                    return u
    return None


def _resolve_method_usr_candidates(
    method_name: str,
    qn_to_usr: dict[str, str],
    field_name: str = "",
    caller_qn: str = "",
) -> list[str]:
    """Every honest candidate USR for a name-based method resolution (T05).

    Same gates as ``_resolve_method_usr`` — exact-key collision awareness,
    ``::name`` suffix matching, the overload map's unambiguous-qname filter —
    but instead of scoring hints down to ONE pick it returns the FULL
    candidate list.  A ``call_sites`` POSSIBLE row lists every alternative,
    so downstream never mistakes a guess for a proven edge (ADR-004 layer
    1: refs carry no fallback resolution).

    Empty list = nothing plausible found (the site is UNRESOLVED, not
    dropped).
    """
    qn_overloads = getattr(qn_to_usr, "overloads", None)
    usr = qn_to_usr.get(method_name)
    if usr:
        if qn_overloads is not None and method_name in qn_overloads:
            # Several real callables share the exact key: all of them are
            # candidates (the site resolves to one of them, unknown which).
            return list(dict.fromkeys(qn_overloads.get(method_name, [])))
        return [usr]
    suffix = f"::{method_name}"
    candidates: list[tuple[str, str]] = [
        (qn, u) for qn, u in qn_to_usr.items() if qn.endswith(suffix)
    ]
    if qn_overloads is not None:
        candidates = [
            (qn, u) for qn, u in candidates if _is_unambiguous_qn(qn, qn_overloads)
        ]
    return list(dict.fromkeys(u for _qn, u in candidates))


def _record_name_fallback_site(
    call_sites: list[CallSite],
    seen_cs: set[tuple] | None,
    loc: cx.SourceLocation,
    cur_fn: str | None,
    from_col: int | None,
    method_name: str,
    qn_to_usr: dict[str, str],
    field_name: str,
    caller_qn: str,
    in_template: bool,
    origin: str,
) -> None:
    """Record one name-fallback call site as POSSIBLE or UNRESOLVED (T05).

    ADR-004 layer 1: a name-based resolution is a guess, so it becomes a
    ``call_sites`` row (certainty='possible', every candidate listed) and
    never a refs edge.  Inside a template body span the dependent name is
    UNRESOLVED with NO candidates — an uninstantiated template has no
    receiver type, and guessing would fabricate the call graph (T05 F6).
    """
    if in_template:
        _add_call_site(
            call_sites, seen_cs, loc.file.name, loc.line, cur_fn,
            certainty="unresolved", resolution_kind="unresolved",
            origin=origin, from_col=from_col, target_usr=None,
            candidate_usrs=[],
        )
        return
    candidates = _resolve_method_usr_candidates(
        method_name, qn_to_usr, field_name, caller_qn,
    )
    if candidates:
        # A single-candidate list still stays 'possible': the name fallback
        # did not prove the edge, it guessed it from the name alone.
        _add_call_site(
            call_sites, seen_cs, loc.file.name, loc.line, cur_fn,
            certainty="possible", resolution_kind="name_fallback",
            origin=origin, from_col=from_col, target_usr=None,
            candidate_usrs=candidates,
        )
    else:
        _add_call_site(
            call_sites, seen_cs, loc.file.name, loc.line, cur_fn,
            certainty="unresolved", resolution_kind="unresolved",
            origin=origin, from_col=from_col, target_usr=None,
            candidate_usrs=[],
        )


def _lookup_dispatch_entry_qn(target_usr: str, qn_to_usr: dict[str, str]) -> str:
    """Reverse-lookup the dispatch entry-point QN that resolves to *target_usr*.

    Mirrors the inline scan of the legacy Phase-1 dispatch block: which
    ``_DISPATCH_ENTRY_POINTS`` qualified name has this USR.
    """
    for _dq in _DISPATCH_ENTRY_POINTS:
        if qn_to_usr.get(_dq) == target_usr:
            return _dq
    return ""


def _record_indirect(seen_ref: set, key: tuple, slot_index: int | None) -> bool:
    """Claim one indirect reference, and report whether it is new.

    *key* is the reference without its slot.  Two rules meet here:

    * Two slots of one table can hold the same function on the same line, as
      in ``{ reset, reset }``.  Those are two facts, so the slot joins the
      identity and both are kept.
    * The same reference can be reached twice — once by an enclosing array
      list that knows the slot, and again by the nested list inside it,
      which does not.  Those are one fact, and the slotted view is the
      better one.

    A slotted claim therefore marks the slotless key as well, which is what
    makes the later slotless claim for the same reference stand down.  The
    AST walk is preorder, so the enclosing list always claims first.
    """
    if slot_index is None:
        if key in seen_ref:
            return False
        seen_ref.add(key)
        return True
    slotted = (*key, slot_index)
    if slotted in seen_ref:
        return False
    seen_ref.add(slotted)
    seen_ref.add(key)
    return True


def _find_fnptr_value_refs(cursor: cx.Cursor, _depth: int = 0) -> list[cx.Cursor]:
    """Collect references to function-pointer-valued PARM_DECL/VAR_DECL/FIELD_DECL.

    ``_find_fn_refs_in_expr`` only surfaces real callable references, so an
    RHS that names a function-pointer *value* — a parameter, a variable, a
    field — produced no fp_assignments row at all (T11
    fp_assignment_parameter_rhs_gap).  This walks the same expression shape
    but returns the referenced declarations whose type is a function
    pointer.
    """
    if _depth > 50:
        return []
    results: list[cx.Cursor] = []
    if cursor.kind in (cx.CursorKind.DECL_REF_EXPR, cx.CursorKind.MEMBER_REF_EXPR):
        ref = cursor.referenced
        if ref is not None and ref.kind in (
                cx.CursorKind.PARM_DECL, cx.CursorKind.VAR_DECL,
                cx.CursorKind.FIELD_DECL):
            try:
                if _is_fn_ptr_type(ref.type):
                    results.append(ref)
            except (ValueError, TypeError, RuntimeError, AttributeError):
                _log.debug("_find_fnptr_value_refs: type check failed for %s",
                           ref.spelling)
        return results
    try:
        children = list(cursor.get_children())
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_find_fnptr_value_refs: get_children failed")
        return []
    for child in children:
        results.extend(_find_fnptr_value_refs(child, _depth + 1))
    return results


def _emit_fn_ptr_targets(
    expr_cursor: cx.Cursor,
    caller_usr: str | None,
    seen_ref: set,
    refs: list[Reference],
    fp_assignments: list[FnPointerAssignment],
    skip_usr: str | None = None,
    lhs_usr: str | None = None,
    lhs_name: str = "",
    method: str = "assignment",
    qn_to_usr: dict[str, str] | None = None,
    slot_index: int | None = None,
    lhs_type: str = "",
) -> None:
    """Emit indirect refs and FnPointerAssignment records for function pointer assignments.

    *lhs_type* is the type of the storage when the caller already knows
    it (``_init_list_element_field``); without it the type is read from
    the child of *expr_cursor* that names *lhs_name*.

    *slot_index* is the position of *expr_cursor* inside a positional init
    list, and reaches the stored reference unchanged.  Only the init-list
    caller sets it; every other caller has no position to give.

    Walks the children of *expr_cursor* looking for function declarations
    (including those nested inside unary operators and casts), then records:
    - An indirect reference via one of the *refs* / *seen_ref* lists.
    - An ``FnPointerAssignment`` when *lhs_usr* is provided (Phase 3 linking).
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    loc = expr_cursor.location
    if not loc.file:
        return

    # Pre-scan: for assignment/init_list, extract fn_ptr_type from the
    # LHS field/var (the storage location), not the RHS function.
    # This ensures fp_assignments.fn_ptr_type uses the function-pointer
    # typedef (e.g., nrfx_power_usb_event_handler_t), matching the type
    # stored in indirect_call_sites and enabling the Phase 3 USR join.
    lhs_fp_type = lhs_type
    if not lhs_fp_type and method in ("assignment", "init_list") and lhs_name:
        for _c in expr_cursor.get_children():
            _lhs_usr, _lhs_name = _extract_lhs_field(_c)
            if _lhs_name and _lhs_name == lhs_name:
                try:
                    lhs_fp_type = _c.type.spelling
                except (ValueError, TypeError, RuntimeError, AttributeError):
                    pass
                break

    for child in expr_cursor.get_children():
        targets = _find_fn_refs_in_expr(child, skip_usr)
        if not targets:
            continue
        for target in targets:
            target_usr = target.get_usr()
            if target_usr == skip_usr:
                continue
            target_loc = target.location
            if target_loc.file:
                key = (target_usr, loc.file.name, loc.line, caller_usr, "indirect")
                if _record_indirect(seen_ref, key, slot_index):
                    refs.append(Reference(
                        to_usr=target_usr,
                        from_file=loc.file.name,
                        from_line=loc.line,
                        from_usr=caller_usr,
                        ref_kind="indirect",
                        slot_index=slot_index,
                    ))
                if (lhs_usr and lhs_usr != target_usr) or lhs_name:
                    try:
                        _fp_type = lhs_fp_type or child.type.spelling
                    except (ValueError, TypeError, RuntimeError, AttributeError):
                        _log.debug(
                            "_emit_fn_ptr_targets: type.spelling failed for %s",
                            target.spelling,
                        )
                        _fp_type = ""
                    fp_assignments.append(FnPointerAssignment(
                        from_file=loc.file.name,
                        from_line=loc.line,
                        lhs_usr=lhs_usr or "",
                        lhs_name=lhs_name,
                        rhs_usr=target_usr,
                        rhs_name=target.spelling,
                        fn_ptr_type=_fp_type,
                        method=method,
                        from_usr=caller_usr,
                        certainty="exact", evidence="ast",
                    ))

    # Function-pointer-valued parameter/variable RHS (T11
    # fp_assignment_parameter_rhs_gap): an RHS that names a fn-ptr
    # PARAMETER, variable, or field is a documented assignment pattern
    # (``g_pipe.push = cb``) even though no real function reference is
    # involved.  Record the fp_assignments row WITHOUT an indirect ref —
    # refs stay AST-proven call targets (ADR-004).
    if method in ("assignment", "var_init", "init_list") and (lhs_usr or lhs_name):
        _recorded_values: set[str] = set()
        for child in expr_cursor.get_children():
            for decl in _find_fnptr_value_refs(child):
                decl_usr = decl.get_usr()
                if not decl_usr or decl_usr in _recorded_values:
                    continue
                if lhs_usr and decl_usr == lhs_usr:
                    # The LHS storage location itself is a fn-ptr variable
                    # (``slot_a = worker``) — it is not an RHS value.
                    continue
                _recorded_values.add(decl_usr)
                try:
                    _value_fp_type = lhs_fp_type or decl.type.spelling
                except (ValueError, TypeError, RuntimeError, AttributeError):
                    _log.debug(
                        "_emit_fn_ptr_targets: fn-ptr value type failed for %s",
                        decl.spelling,
                    )
                    _value_fp_type = ""
                fp_assignments.append(FnPointerAssignment(
                    from_file=loc.file.name,
                    from_line=loc.line,
                    lhs_usr=lhs_usr or "",
                    lhs_name=lhs_name,
                    rhs_usr=decl_usr,
                    rhs_name=decl.spelling,
                    fn_ptr_type=_value_fp_type,
                    method=method,
                    from_usr=caller_usr,
                    certainty="exact", evidence="ast",
                ))

    # Token fallback: when _find_fn_refs_in_expr found no targets in any child
    # (e.g. UNEXPOSED_EXPR wrapping a member function pointer inside a CALL_EXPR
    # like callback(&Class::method)), try token-based extraction on the whole expr.
    # This is the last-resort fallback — the libclang AST is opaque but the raw
    # token stream still contains the ``&Class::method`` pattern.  We scan for
    # it via _extract_fn_refs_from_unexposed which does not depend on AST
    # structure, only on a linear token sequence.
    if qn_to_usr is not None:
        any_found = False
        for child in expr_cursor.get_children():
            if _find_fn_refs_in_expr(child, skip_usr):
                any_found = True
                break
        if not any_found:
            pairs = _extract_fn_refs_from_unexposed(expr_cursor, qn_to_usr)
            for target_usr, rhs_name in pairs:
                if target_usr == skip_usr:
                    continue
                key = (target_usr, loc.file.name, loc.line, caller_usr, "indirect")
                if _record_indirect(seen_ref, key, slot_index):
                    refs.append(Reference(
                        to_usr=target_usr,
                        from_file=loc.file.name,
                        from_line=loc.line,
                        from_usr=caller_usr,
                        ref_kind="indirect",
                        slot_index=slot_index,
                    ))
                if (lhs_usr and lhs_usr != target_usr) or lhs_name:
                    fp_assignments.append(FnPointerAssignment(
                        from_file=loc.file.name,
                        from_line=loc.line,
                        lhs_usr=lhs_usr or "",
                        lhs_name=lhs_name,
                        rhs_usr=target_usr,
                        rhs_name=rhs_name,
                        fn_ptr_type="",
                        method=method,
                        from_usr=caller_usr,
                        # Token-derived inside a real AST node's extent: the
                        # USR was resolved via qn_to_usr, but the provenance
                        # is the token stream, not a cursor — 'possible'.
                        certainty="possible", evidence="unexposed_tokens",
                    ))
def _build_refs_and_fp_assignments(
    tu: cx.TranslationUnit,
    tu_path_str: str,
    symbols: list[Symbol],
    resolve_fn,
    anon_usr_to_field: dict[str, str],
    _log,
    skip_files: frozenset[str] | None = None,
) -> tuple[list[Reference], list[IndirectCallSite], list[FnPointerAssignment], list[PendingDispatch], list[CallSite]]:
    """Walk the TU AST extracting references, indirect call sites, and function pointer assignments.

    When *skip_files* is a non-empty frozenset, subtrees of already
    processed headers are skipped via :func:`_iter_cursor_skip` —
    references from those headers were already captured during the
    first TU that included them.

    **Phase 1 — AST walk**: traverses the translation unit in pre-order.
    A ``fn_stack`` tracks the enclosing function for each cursor — needed
    because references store the caller's USR (``from_usr``).  The stack
    is maintained by two pop conditions:
    * File-mismatch pop — when the cursor moves into a different source
      file and the top-of-stack function's span belongs to the TU's own
      file, it is popped (we left that function's body).
    * Line-overflow pop — when the cursor's line exceeds the top-of-stack
      function's ``end_line`` (which is non-zero only for TU-resident
      definitions), it is popped (we passed the closing brace).

    **Phase 2 — source-line fallback**: after the AST walk,
    :func:`_run_source_line_fallback` scans raw source text inside known
    function spans and emits call/indirect edges via regex matching.
    This catches ``obj.method()`` and ``&Class::method`` patterns that
    template-obscured AST cursors cannot resolve.

    Returns (refs, indirect_call_sites, fp_assignments, pending_dispatches,
    call_sites).

    ``call_sites`` (T05, ADR-004 layer 2) holds one row per syntactic call
    expression with its target-resolution evidence: AST-resolved sites are
    ``exact``, name-fallback sites are ``possible`` with the full
    ``candidate_usrs`` list, and sites nothing could resolve (undeclared
    members, dependent template calls) persist as ``unresolved`` — never
    dropped, never fabricated into ``refs``.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    _callable_kind_strs = frozenset({"function", "method", "constructor", "destructor"})
    qn_to_usr: dict[str, str] = {}
    qn_overloads: dict[str, list[str]] = {}
    for s in symbols:
        if s.qualified_name and s.kind in _callable_kind_strs:
            qn_to_usr[s.qualified_name] = s.usr
            qn_overloads.setdefault(s.qualified_name, []).append(s.usr)
    # Attach the overload candidates to the map itself (see _QnToUsrMap):
    # every fallback helper already receives qn_to_usr, so the exact-key
    # collision gate travels with it without widening six signatures.
    qn_map = _QnToUsrMap(qn_to_usr)
    qn_map.overloads = qn_overloads
    usr_to_qn: dict[str, str] = {u: q for q, u in qn_to_usr.items()}

    refs: list[Reference] = []
    indirect_call_sites: list[IndirectCallSite] = []
    fp_assignments: list[FnPointerAssignment] = []
    pending_dispatches: list[PendingDispatch] = []
    call_sites: list[CallSite] = []
    seen_ref: set[tuple] = set()
    # Call-site dedup within one parse: one site per (file, line, col,
    # caller).  First writer wins — an exact AST row beats a later
    # token/raw-text guess for the same (file, line, col) (T05).
    seen_cs: set[tuple] = set()
    # USRs whose extent is a template BODY span (FUNCTION_TEMPLATE).  A
    # dependent call inside one must never resolve by name — the template
    # is uninstantiated, so there is no receiver type (T05 F6, ADR-007).
    _template_fn_usrs: set[str] = set()
    _fn_spans: list[tuple[str, int, int]] = []
    # Stored definition line → USR, for the raw-text fallback's self-call
    # suppression.  Keyed on cursor.location.line (the signature line the
    # symbols row stores), NOT the extent start: an ``__attribute__`` above
    # the signature shifts the extent start up one line, which let a
    # fabricated self-call through on the real definition line (T07 C6).
    _fn_def_lines: dict[int, str] = {}

    fn_stack: list[tuple[str, int, str]] = []

    for cursor in (
        _iter_cursor_skip(tu.cursor, skip_files, resolve_fn)
        if skip_files
        else tu.cursor.walk_preorder()
    ):
        _cl = cursor.location
        _cl_file = str(Path(_cl.file.name).resolve()) if _cl and _cl.file else None
        _cl_line = _cl.line if _cl else -1
        # Pop condition 1 — file mismatch: the top-of-stack function was
        # defined in the TU's own file (tu_path_str) but the cursor is now
        # in a different file (e.g. an included header).  The function
        # body ended before the include; pop it.
        while fn_stack and fn_stack[-1][2] and _cl_file and fn_stack[-1][2] != _cl_file:
            fn_stack.pop()
        # Pop condition 2 — line overflow: the top-of-stack function has a
        # known end_line (>0, set only for TU-resident definitions) and the
        # cursor line has advanced past it.  We left the function body.
        while fn_stack and fn_stack[-1][1] > 0 and _cl is not None and _cl_line > fn_stack[-1][1]:
            fn_stack.pop()
        cur_fn = fn_stack[-1][0] if fn_stack else None

        if cursor.kind in _CALLABLE_KINDS and cursor.is_definition():
            own_usr = cursor.get_usr()
            if own_usr:
                cur_fn = own_usr
            if cursor.kind == cx.CursorKind.FUNCTION_TEMPLATE and own_usr:
                # Mark template bodies: dependent call sites inside them are
                # recorded but never resolved by name (no receiver type for
                # an uninstantiated template — T05 F6).
                _template_fn_usrs.add(own_usr)
            try:
                _ext = cursor.extent
                if _ext.start.file:
                    _ext_file = _ext.start.file.name
                    _resolved_file = str(resolve_fn(_ext_file))
                    if _resolved_file == tu_path_str:
                        _fn_spans.append((cur_fn or '', _ext.start.line, _ext.end.line))
                        _fn_def_lines[_cl_line] = cur_fn or ''
                        fn_stack.append((cur_fn or '', _ext.end.line, tu_path_str))
                    else:
                        # Header-resident definition (inline body in an
                        # included header).  Store the resolved file path so
                        # the file-mismatch pop (condition 1) fires when the
                        # walk leaves the header, and a guarded end_line so
                        # the line-overflow pop (condition 2) fires when the
                        # walk passes the definition within the header.
                        # Previously both were zeroed, so the definition
                        # never popped and its USR leaked into ``from_usr``
                        # for unrelated cursors (file-scope declarations and
                        # the bodies of following functions).
                        _end = 0
                        if (
                            _ext.end.file
                            and _ext.end.file.name == _ext_file
                            and _ext.end.line >= _ext.start.line
                        ):
                            _end = _ext.end.line
                        fn_stack.append((cur_fn or '', _end, _resolved_file))
                else:
                    fn_stack.append((cur_fn or '', 0, ""))
            except (ValueError, TypeError, RuntimeError, AttributeError):
                _log.debug(
                    "cursor.extent failed for %s",
                    cursor.spelling,
                )
                fn_stack.append((cur_fn or '', 0, ""))
        elif (cursor.kind == cx.CursorKind.FUNCTION_TEMPLATE
              and not cursor.is_definition()):
            # ADR-007 (T05 F6): on hosts where ``is_definition()`` is False
            # for an uninstantiated FUNCTION_TEMPLATE (measured T09), the
            # main branch above never runs and the template body produces
            # NO span — so the raw-text fallback never sees the dependent
            # call sites inside it and they silently vanish.  Collect the
            # span from ``cursor.extent`` instead (is_definition()-agnostic
            # per ADR-007).  The walk visits no cursors under the template
            # body, so nothing needs pushing onto fn_stack here.
            own_usr = cursor.get_usr()
            try:
                _ext = cursor.extent
                if own_usr and _ext.start.file:
                    _resolved_file = str(resolve_fn(_ext.start.file.name))
                    if _resolved_file == tu_path_str:
                        _fn_spans.append((own_usr, _ext.start.line, _ext.end.line))
                        _fn_def_lines[_cl_line] = own_usr
                        _template_fn_usrs.add(own_usr)
            except (ValueError, TypeError, RuntimeError, AttributeError):
                _log.debug(
                    "cursor.extent failed for FUNCTION_TEMPLATE %s",
                    cursor.spelling,
                )

        _process_ref_cursor(
            cursor, cur_fn, refs, indirect_call_sites, fp_assignments,
            pending_dispatches, seen_ref, qn_to_usr, usr_to_qn, tu_path_str,
            resolve_fn, _log, call_sites, seen_cs, _template_fn_usrs,
        )

    # Source-line fallback for template-obscured method calls
    _run_source_line_fallback(
        tu, refs, fp_assignments, pending_dispatches, seen_ref, _fn_spans,
        _fn_def_lines, qn_to_usr, usr_to_qn, _log,
        call_sites=call_sites, seen_cs=seen_cs,
        template_fn_usrs=_template_fn_usrs,
    )

    return refs, indirect_call_sites, fp_assignments, pending_dispatches, call_sites


# ── Symbol attribute evidence (T07 reachability roots, ADR-009) ─────────────

_ATTR_FLAG_TOKENS = frozenset({"used", "retain", "constructor", "weak"})
# Callables whose GNU attributes the extractor reads.  Attributes on
# variables/classes exist but feed no dead-code root today, so they are
# not scanned — the root set of find_dead_code is callables only.
_ATTR_CURSOR_KINDS = frozenset({
    cx.CursorKind.FUNCTION_DECL,
    cx.CursorKind.FUNCTION_TEMPLATE,
    cx.CursorKind.CXX_METHOD,
    cx.CursorKind.CONSTRUCTOR,
    cx.CursorKind.DESTRUCTOR,
})


def _strip_attr_string(token: str) -> str:
    """Strip the quotes of a string literal token inside an attribute."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        return token[1:-1]
    return token


def _parse_gnu_attributes(tokens: list[str]) -> tuple[str, set[str], str | None]:
    """Parse GNU ``__attribute__((...))`` regions out of a token list.

    Returns ``(section, flags, alias_target)``: the ``section("...")``
    name, the flag set (``used`` / ``retain`` / ``constructor`` / ``weak``),
    and the ``alias("...")`` target name (``None`` when absent).

    Why token parsing and not the AST: the pinned libclang wheel exposes
    GNU attributes as ``UNEXPOSED_ATTR`` children with an EMPTY spelling —
    there is nothing structured to read.  The token stream is the only
    surface, and parsing is anchored on ``__attribute__`` so a parameter
    or a function literally named ``section`` or ``alias`` can never
    false-positive: outside an attribute region those tokens are ignored.
    """
    section = ""
    flags: set[str] = set()
    alias_target: str | None = None
    n = len(tokens)
    i = 0
    while i < n:
        if tokens[i] != "__attribute__":
            i += 1
            continue
        # Expect the double-paren form.  The attribute list is the region
        # matching the FIRST '(' — a nested pair (alias("x")) is inside it.
        j = i + 1
        if j >= n or tokens[j] != "(":
            i += 1
            continue
        d = 1
        start = j + 1
        j = start
        while j < n and d:
            if tokens[j] == "(":
                d += 1
            elif tokens[j] == ")":
                d -= 1
                if d == 0:
                    break
            j += 1
        inner = tokens[start:j]
        m = len(inner)
        k = 0
        while k < m:
            t = inner[k]
            nxt = inner[k + 1] if k + 1 < m else ""
            if t == "section" and nxt == "(" and k + 2 < m:
                val = _strip_attr_string(inner[k + 2])
                if val and not section:
                    section = val
            elif t == "alias" and nxt == "(" and k + 2 < m:
                val = _strip_attr_string(inner[k + 2])
                if val:
                    alias_target = val
            elif t in _ATTR_FLAG_TOKENS:
                flags.add(t)
            k += 1
        i = j + 1
    return section, flags, alias_target


def _collect_symbol_attrs(cursor: cx.Cursor) -> tuple[str, set[str], str | None]:
    """Read the GNU attributes of one callable declaration.

    The scan window keeps the cost bounded: for a DEFINITION the
    attributes can only sit in the prefix (before the declarator), so the
    first four extent lines are scanned — a body is never tokenized.  For
    a body-less DECLARATION the full extent is scanned because the GNU
    attribute may also trail the declarator
    (``void f(void) __attribute__((weak, alias("g")));``).

    Known limitation (documented, honest miss — never a wrong answer):
    a definition whose attribute sits more than three lines above the
    signature line is not seen.
    """
    if cursor.kind not in _ATTR_CURSOR_KINDS:
        return "", set(), None
    try:
        tokens = list(cursor.get_tokens())
    except (ValueError, TypeError, RuntimeError, IndexError):
        return "", set(), None
    if not tokens:
        return "", set(), None
    if cursor.is_definition():
        max_line = cursor.extent.start.line + 3
        spellings = [t.spelling for t in tokens if t.location.line <= max_line]
    else:
        spellings = [t.spelling for t in tokens]
    return _parse_gnu_attributes(spellings)


def _append_attr_root_refs(
    refs: list[Reference],
    pending_ctors: list[tuple[str, str, int]],
    pending_aliases: list[tuple[str, str, int, str]],
    symbols: list[Symbol],
    _log,
) -> None:
    """Materialize the reachability root edges collected during the walk.

    * ``ref_kind='ctor_root'`` — ``__attribute__((constructor))``: the
      function runs from ``.init_array`` before ``main()``, with no AST
      caller.  ``from_usr`` is NULL, the same shape the global-ctor
      bridging uses for ``implicit_construct`` (file-scope evidence).
    * ``ref_kind='alias'`` — ``__attribute__((weak, alias("target")))``:
      a declaration whose alias attribute names the target as a STRING.
      The edge goes decl → target so the target is not reported dead
      (the linker keeps it via the alias relocation).

    The alias target is resolved against the symbols of THIS translation
    unit, preferring the definition, deterministically (definition wins,
    then file, then line).  A target that resolves to nothing is skipped —
    no USR is ever fabricated (ADR-004: refs stay AST-proven).
    """
    seen_ctor: set[str] = set()
    for usr, file_str, line in pending_ctors:
        if usr in seen_ctor:
            continue
        seen_ctor.add(usr)
        refs.append(Reference(
            to_usr=usr, from_file=file_str, from_line=line,
            from_usr=None, ref_kind="ctor_root", slot_index=None,
        ))
    if not pending_aliases:
        return
    by_name: dict[str, list[Symbol]] = {}
    for s in symbols:
        by_name.setdefault(s.name, []).append(s)
    seen_alias: set[str] = set()
    for decl_usr, file_str, line, target_name in pending_aliases:
        if decl_usr in seen_alias:
            continue
        seen_alias.add(decl_usr)
        candidates = by_name.get(target_name)
        if not candidates:
            _log.debug(
                "alias target %r not found in this TU — no alias edge",
                target_name,
            )
            continue
        target = sorted(
            candidates,
            key=lambda s: (not s.is_definition, s.file, s.line),
        )[0]
        refs.append(Reference(
            to_usr=target.usr, from_file=file_str, from_line=line,
            from_usr=decl_usr, ref_kind="alias", slot_index=None,
        ))


def _is_tentative_definition(cursor: cx.Cursor) -> bool:
    """True for a C tentative definition: file/namespace-scope VAR_DECL,
    no ``extern`` storage class (the initializer, if any, already made
    ``is_definition()`` true — only the initializer-less shape lands here).
    Class-scope members and function-locals are not tentative definitions.
    """
    try:
        sem_parent = cursor.semantic_parent
        if sem_parent is None or sem_parent.kind not in _TU_SCOPED_KINDS:
            return False
        return cursor.storage_class != cx.StorageClass.EXTERN
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_is_tentative_definition: attribute access failed")
        return False


def _process_one_symbol(
    cursor: cx.Cursor,
    symbols: list[Symbol],
    seen_usrs: dict[str, bool],
    class_cursors: list[cx.Cursor],
    anon_usr_to_field: dict[str, str],
    _log,
    pending_ctors: list[tuple[str, str, int]] | None = None,
    pending_aliases: list[tuple[str, str, int, str]] | None = None,
    row_by_usr: dict[str, int] | None = None,
) -> bool:
    """Process a single cursor during AST walk for symbol extraction.

    Returns True if the cursor was added as a new symbol (or replaced an
    earlier declaration row of the same USR — F3P: within one TU a
    definition subsumes the previously stored declaration row, mirroring
    the legacy ``insert_symbols_batch`` "definition wins" merge).

    **Kind classification** — ``VAR_DECL`` cursors are split into two
    sub-kinds based on their semantic parent:
    * ``varglobal`` — global, namespace, linkage-spec, or class-scope
      variables (including ``static`` class members).
    * ``varlocal`` — variables inside function/method/constructor bodies.

    **Template USR resolution** — for non-template declarations that are
    instantiations of a template (e.g. ``Callback<void(int)>``), the
    specializing template cursor is resolved through a three-level
    fallback chain (T09/ADR-007: via the bound
    ``clang_getSpecializedCursorTemplate`` — the wheel defines no
    ``Cursor.specialized_template`` property):
    1. Direct ``clang_getSpecializedCursorTemplate(cursor)``.
    2. The same call on ``cursor.semantic_parent`` — handles members of
       template classes.
    3. The same call on ``cursor.type.get_declaration()`` — handles
       class-like/callable declarations whose type resolves to a
       different template instance.  Variables/fields/params of instance
       type are deliberately excluded: an observation *of* an instance is
       not an instance (T09 adversarial).
    A partial specialization pattern row records its primary template's
    USR, which is the storage backing partial-spec USR-set resolution in
    ``get_template_instances``.

    **Deduplication** — ``seen_usrs`` maps USR → ``bool(definition)``.
    When a forward declaration is encountered first, it is recorded with
    ``False``.  When the definition is later met in the same TU, the flag
    is promoted to ``True``.  A duplicate declaration (with the same
    definition status) is silently skipped — returns ``False``.
    """
    loc = cursor.location
    if not loc.file:
        return False

    is_def = cursor.is_definition()
    if (not is_def and cursor.kind == cx.CursorKind.VAR_DECL
            and _is_tentative_definition(cursor)):
        # C tentative definition: a file/namespace-scope VAR_DECL without
        # initializer and without ``extern`` allocates storage, but
        # libclang answers ``is_definition() == False`` and VAR_DECL is
        # not in _DECL_KINDS — the row was silently dropped (T06 F3a).
        is_def = True
    elif not is_def and cursor.kind not in _DECL_KINDS:
        return False

    usr = cursor.get_usr()
    if not usr:
        return False

    enum_val: int | None = None
    if cursor.kind == cx.CursorKind.ENUM_CONSTANT_DECL:
        try:
            enum_val = cursor.enum_value
            # SQLite INTEGER is signed 64-bit: [-2^63, 2^63-1]. libclang
            # returns enum values as Python ints that may exceed this range
            # for enums with unsigned underlying types whose bit-flag values
            # reach or exceed 2^63 (e.g. fmt's is_unpacked_bit = 1ULL << 63).
            # Clamp to the signed 64-bit range so the row is storeable.
            # The exact value is preserved in the source; enum_value is a
            # best-effort numeric hint, not a fidelity-critical field.
            if enum_val is not None:
                _SQLITE_MAX = (1 << 63) - 1
                _SQLITE_MIN = -(1 << 63)
                if enum_val > _SQLITE_MAX:
                    enum_val = _SQLITE_MAX
                elif enum_val < _SQLITE_MIN:
                    enum_val = _SQLITE_MIN
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("enum_value failed for %s at %s:%d", cursor.spelling, loc.file.name, loc.line)

    is_virtual = bool(cursor.is_virtual_method()) if cursor.kind in (cx.CursorKind.CXX_METHOD, cx.CursorKind.DESTRUCTOR) else False
    is_pure = bool(cursor.is_pure_virtual_method()) if cursor.kind in (cx.CursorKind.CXX_METHOD, cx.CursorKind.DESTRUCTOR) else False

    sem_parent = cursor.semantic_parent
    kind = _cursor_kind_label(cursor.kind)
    # Split VAR_DECL into varglobal / varlocal based on semantic parent.
    # A variable declared at file, namespace, or class scope is a
    # varglobal (shared state); a variable inside a function body is a
    # varlocal (stack/register).  This split powers find_variables for
    # global-state discovery without flooding results with loop counters.
    if kind == "variable":
        if sem_parent and sem_parent.kind in (
            cx.CursorKind.TRANSLATION_UNIT,
            cx.CursorKind.NAMESPACE,
            cx.CursorKind.LINKAGE_SPEC,
            cx.CursorKind.CLASS_DECL,
            cx.CursorKind.STRUCT_DECL,
            cx.CursorKind.UNION_DECL,
            cx.CursorKind.CLASS_TEMPLATE,
            cx.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
        ):
            kind = "varglobal"
        else:
            kind = "varlocal"

    parent_usr = ""
    if sem_parent and sem_parent.kind in (
        cx.CursorKind.CLASS_DECL,
        cx.CursorKind.STRUCT_DECL,
        cx.CursorKind.UNION_DECL,
        cx.CursorKind.CLASS_TEMPLATE,
        cx.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
    ):
        parent_usr = sem_parent.get_usr() or ""

    if not parent_usr and kind in ("varlocal", "variable"):
        if sem_parent and sem_parent.kind in _CALLABLE_KINDS:
            parent_usr = sem_parent.get_usr() or ""

    is_template = cursor.kind in (
        cx.CursorKind.CLASS_TEMPLATE,
        cx.CursorKind.FUNCTION_TEMPLATE,
        cx.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
    )
    template_usr = ""
    # T09/ADR-007: template links resolve through the bound
    # ``clang_getSpecializedCursorTemplate`` — the wheel's
    # ``Cursor.specialized_template`` property does not exist and used to
    # raise AttributeError on every cursor (swallowed below, dead column).
    if is_template:
        if cursor.kind == cx.CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION:
            # A partial specialization is itself a pattern, but it
            # specializes the primary template: recording that link here
            # lets get_template_instances resolve the partial-spec USR
            # set {primary, partial specs} with the existing
            # idx_symbols_template index (T09 SLICE-K).
            spec = _specialized_template(cursor)
            if spec is not None:
                template_usr = spec.get_usr() or ""
    else:
        # Three-level fallback for template USR resolution.
        # Level 1: direct specialized_template (most common).
        # Level 2: parent's specialized_template — members of template
        #   classes (e.g. Callback<void()>::operator()) inherit their
        #   template USR from the enclosing class.
        # Level 3: type declaration lookup — declarations whose type is
        #   itself a template instance.  Deliberately restricted to
        #   class-like/callable kinds: a VAR_DECL of instance type is an
        #   *observation of* an instance, not one, and must not join the
        #   instance USR set (T09 adversarial: no false instances).
        _template_check_kinds = (
            cx.CursorKind.CLASS_DECL, cx.CursorKind.STRUCT_DECL,
            cx.CursorKind.UNION_DECL,
            cx.CursorKind.FUNCTION_DECL, cx.CursorKind.CXX_METHOD,
            cx.CursorKind.CONSTRUCTOR, cx.CursorKind.DESTRUCTOR,
            cx.CursorKind.FIELD_DECL,
        )
        if cursor.kind in _template_check_kinds:
            specialized = _specialized_template(cursor)          # level 1
            if specialized is None:
                parent = cursor.semantic_parent                   # level 2
                if parent is not None:
                    specialized = _specialized_template(parent)
            if specialized is None:                               # level 3
                try:
                    decl = cursor.type.get_declaration()
                except (ValueError, TypeError, RuntimeError, AttributeError):
                    decl = None
                if decl is not None and decl.get_usr() != usr:
                    specialized = _specialized_template(decl)
            if specialized is not None:
                spec_usr = specialized.get_usr() or ""
                # clang_getSpecializedCursorTemplate returns the input
                # cursor for non-specializations — a self-link means
                # "is a template pattern", never an instance link.
                if spec_usr and spec_usr != usr:
                    template_usr = spec_usr

    # Deduplication: seen_usrs[usr] = bool(is_definition).
    # Forward declaration (False) → definition (True) is promoted.
    # Duplicate declaration or definition with the same status is skipped.
    # F3P: when the definition arrives after its declaration row was
    # already appended, the stored row is REPLACED in place (same TU,
    # same USR — the definition subsumes the declaration; the legacy
    # insert path has always merged this way via ON CONFLICT).  Without
    # this, a header-declared + source-defined symbol kept the
    # declaration location and is_definition=0, losing the definition
    # span (F3P §5).
    replace_idx: int | None = None
    prev = seen_usrs.get(usr)
    if prev is not None:
        if is_def and not prev:
            seen_usrs[usr] = True
            if row_by_usr is not None:
                replace_idx = row_by_usr.get(usr)
            if replace_idx is None:
                return False
        else:
            return False
    else:
        seen_usrs[usr] = is_def

    is_anon = _is_anonymous_struct_or_union(cursor)
    field_name = anon_usr_to_field.get(usr) if is_anon else None
    symbol_name = field_name if field_name else cursor.spelling
    symbol_qname = _qualified_name(cursor, anon_usr_to_field)

    # GNU attribute evidence (T07/ADR-009): section name + attr flags go on
    # the symbol row; constructor/alias observations are queued here and
    # materialized as root refs after the walk (see _append_attr_root_refs).
    attr_section = ""
    attr_flags_str = ""
    if cursor.kind in _ATTR_CURSOR_KINDS:
        a_section, a_flags, a_alias = _collect_symbol_attrs(cursor)
        if a_section or a_flags or a_alias:
            attr_section = a_section
            attr_flags_str = ",".join(sorted(a_flags))
            if a_alias and pending_aliases is not None:
                pending_aliases.append(
                    (usr, loc.file.name, cursor.extent.start.line, a_alias)
                )
            if "constructor" in a_flags and pending_ctors is not None:
                pending_ctors.append(
                    (usr, loc.file.name, cursor.extent.start.line)
                )

    new_sym = Symbol(
        name=symbol_name,
        qualified_name=symbol_qname,
        kind=kind,
        file=loc.file.name,
        line=loc.line,
        column=loc.column,
        is_definition=is_def,
        signature=_signature(cursor),
        docstring=_docstring(cursor),
        usr=usr,
        end_line=_end_line(cursor, loc),
        end_column=_end_column(cursor, loc),
        enum_value=enum_val,
        is_virtual=is_virtual,
        is_pure_virtual=is_pure,
        parent_usr=parent_usr,
        is_template=is_template,
        template_usr=template_usr,
        section=attr_section,
        attr_flags=attr_flags_str,
    )
    if replace_idx is not None:
        symbols[replace_idx] = new_sym
        if row_by_usr is not None:
            row_by_usr[usr] = replace_idx
    else:
        symbols.append(new_sym)
        if row_by_usr is not None:
            row_by_usr[usr] = len(symbols) - 1

    if cursor.kind in (cx.CursorKind.CLASS_DECL, cx.CursorKind.STRUCT_DECL) and is_def:
        class_cursors.append(cursor)

    return True


def _process_ref_cursor(
    cursor: cx.Cursor,
    cur_fn: str | None,
    refs: list[Reference],
    indirect_call_sites: list[IndirectCallSite],
    fp_assignments: list[FnPointerAssignment],
    pending_dispatches: list[PendingDispatch],
    seen_ref: set[tuple],
    qn_to_usr: dict[str, str],
    usr_to_qn: dict[str, str],
    tu_path_str: str,
    resolve_fn: Callable[..., object],
    _log: logging.Logger,
    call_sites: list[CallSite] | None = None,
    seen_cs: set[tuple] | None = None,
    template_fn_usrs: set[str] | None = None,
) -> None:
    """Process a single cursor for references and indirect calls — dispatcher."""
    loc = cursor.location
    if not loc.file:
        return

    _handle_direct_refs(cursor, cur_fn, refs, seen_ref, tu_path_str, resolve_fn,
                        call_sites, seen_cs)
    _handle_indirect_invocations(cursor, cur_fn, refs, indirect_call_sites,
                                 fp_assignments, pending_dispatches, seen_ref, tu_path_str, resolve_fn,
                                 qn_to_usr, _log)
    _handle_fn_ptr_cases(cursor, cur_fn, refs, fp_assignments, seen_ref, tu_path_str, _log)
    _handle_implicit_constructors(cursor, cur_fn, refs, seen_ref, _log)
    _handle_token_fallbacks(cursor, cur_fn, refs, fp_assignments, seen_ref, qn_to_usr,
                            usr_to_qn, tu_path_str, _log, call_sites, seen_cs,
                            template_fn_usrs)


def _add_call_site(
    call_sites: list[CallSite],
    seen_cs: set[tuple] | None,
    from_file: str,
    from_line: int,
    from_usr: str | None,
    certainty: str,
    resolution_kind: str,
    origin: str,
    from_col: int | None = None,
    target_usr: str | None = None,
    candidate_usrs: list[str] | None = None,
) -> None:
    """Deduplicated append to the call-sites list (T05, ADR-004 layer 2).

    Key is (from_file, from_line, from_col, from_usr): the same syntactic
    site visited twice (token fallback, then the raw-text fallback) keeps
    the FIRST row — an exact AST row recorded earlier always wins over a
    later name-based guess for the same column.
    """
    key = (from_file, from_line, from_col, from_usr)
    if seen_cs is not None and key in seen_cs:
        return
    if seen_cs is not None:
        seen_cs.add(key)
    call_sites.append(
        CallSite(
            from_file=from_file,
            from_line=from_line,
            from_col=from_col,
            from_usr=from_usr,
            certainty=certainty,
            resolution_kind=resolution_kind,
            target_usr=target_usr,
            candidate_usrs=candidate_usrs or [],
            origin=origin,
        )
    )


def _add_ref(refs: list[Reference], seen_ref: set[tuple], to_usr: str,
             from_file: str, from_line: int, from_usr: str | None,
             ref_kind: str) -> bool:
    """Deduplicated append to refs list."""
    key = (to_usr, from_file, from_line, from_usr, ref_kind)
    if key not in seen_ref:
        seen_ref.add(key)
        refs.append(Reference(to_usr=to_usr, from_file=from_file, from_line=from_line,
                              from_usr=from_usr, ref_kind=ref_kind))
        return True
    return False


def _callee_has_file(referenced: cx.Cursor | None) -> bool:
    """Check a referenced cursor has a valid file location."""
    if referenced is None:
        return False
    try:
        rl = referenced.location
        return rl is not None and rl.file is not None
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return False


def _handle_direct_refs(cursor: cx.Cursor, cur_fn: str | None, refs: list[Reference],
                        seen_ref: set[tuple], tu_path_str: str,
                        resolve_fn: Callable[..., object],
                        call_sites: list[CallSite] | None = None,
                        seen_cs: set[tuple] | None = None) -> None:
    """Direct call/ref/member references + field-access fallback + constructor fallback."""
    loc = cursor.location
    ref_kind = _REF_KINDS.get(cursor.kind)
    if ref_kind is not None:
        referenced = cursor.referenced
        if referenced is not None:
            to_usr = referenced.get_usr()
            ref_loc = referenced.location
            if to_usr and ref_loc.file:
                _add_ref(refs, seen_ref, to_usr, loc.file.name, loc.line, cur_fn, ref_kind)

        if cursor.kind == cx.CursorKind.CALL_EXPR:
            _handle_field_call_fallback(cursor, cur_fn, refs, seen_ref, tu_path_str,
                                        call_sites, seen_cs)
            _handle_constructor_fallback(cursor, cur_fn, refs, seen_ref, tu_path_str, resolve_fn)
            _handle_runtime_vector(cursor, referenced, cur_fn, refs, seen_ref)
            # ADR-004 layer 2 (T05): every AST-resolved call expression is
            # an EXACT call site.  Sites libclang could NOT resolve are
            # left to the token fallback (which records possible/unresolved
            # rows) so a later possible row is not shadowed by a premature
            # unresolved one at the same key.
            if (referenced is not None and call_sites is not None
                    and ref_kind == "call"):
                to_usr = referenced.get_usr()
                ref_loc = referenced.location
                if to_usr and ref_loc.file:
                    _add_call_site(
                        call_sites, seen_cs, loc.file.name, loc.line, cur_fn,
                        certainty="exact", resolution_kind="ast_direct",
                        origin="ast", from_col=loc.column, target_usr=to_usr,
                    )


def _handle_runtime_vector(cursor: cx.Cursor, referenced: cx.Cursor | None,
                           cur_fn: str | None, refs: list[Reference],
                           seen_ref: set[tuple]) -> None:
    """Record a vector table entry this call installs at run time.

    A CMSIS target that moves its table into RAM fills it with
    ``NVIC_SetVector``, and the static table then says nothing about
    which handler services the interrupt.  See ``nvic`` for what makes a
    call qualify and why the IRQ number is stored unshifted.

    Deduplicated on the same key as every other reference, so one call
    site emitted twice by the preprocessor stays one edge.
    """
    found = runtime_vector_reference(cursor, referenced, cur_fn)
    if found is None:
        return
    key = (found.to_usr, found.from_file, found.from_line, found.from_usr,
           found.ref_kind)
    if key in seen_ref:
        return
    seen_ref.add(key)
    refs.append(found)


def _handle_field_call_fallback(cursor: cx.Cursor, cur_fn: str | None,
                                refs: list[Reference], seen_ref: set[tuple],
                                tu_path_str: str,
                                call_sites: list[CallSite] | None = None,
                                seen_cs: set[tuple] | None = None) -> None:
    """Field-access call fallback: obj.method() where method is not directly resolved."""
    loc = cursor.location
    if _callee_has_file(cursor.referenced):
        return
    for child in cursor.get_children():
        if child.kind == cx.CursorKind.MEMBER_REF_EXPR:
            child_ref = child.referenced
            if child_ref is not None and child_ref.kind in _CALLABLE_KINDS:
                child_usr = child_ref.get_usr()
                child_loc = child_ref.location
                if child_usr and child_loc.file:
                    _add_ref(refs, seen_ref, child_usr, loc.file.name, loc.line, cur_fn, "call")
                    # The child member cursor IS the libclang resolution of
                    # this call — record the exact call site next to the
                    # edge (T05: every resolved site lands in call_sites).
                    if call_sites is not None:
                        _add_call_site(
                            call_sites, seen_cs, loc.file.name, loc.line, cur_fn,
                            certainty="exact", resolution_kind="ast_direct",
                            origin="ast", from_col=loc.column, target_usr=child_usr,
                        )
                    return


def _collect_ctor_refs_from_type(
    child_type: cx.Type, loc_file: str, loc_line: int, cur_fn: str | None,
    refs: list[Reference], seen_ref: set[tuple],
) -> None:
    """Emit a ``call`` reference for every constructor of a RECORD type.

    Called from ``_handle_constructor_fallback`` when a variable or field
    declaration has a class type whose constructor call was not resolved
    by libclang (common with implicit/default constructors).

    Iterates all child constructors of the class — the specific overload
    is not distinguished; each constructor gets a call reference at the
    declaration site.
    """
    if child_type.kind != cx.TypeKind.RECORD:
        return
    try:
        class_cursor = child_type.get_declaration()
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("get_declaration failed for record type")
        return
    if class_cursor is None:
        return
    for ctor in class_cursor.get_children():
        if ctor.kind != cx.CursorKind.CONSTRUCTOR:
            continue
        ctor_usr = ctor.get_usr()
        if not ctor_usr:
            continue
        ctor_loc = ctor.location
        if not ctor_loc.file:
            continue
        _add_ref(refs, seen_ref, ctor_usr, loc_file, loc_line, cur_fn, "call")


def _handle_constructor_fallback(cursor: cx.Cursor, cur_fn: str | None,
                                 refs: list[Reference], seen_ref: set[tuple],
                                 tu_path_str: str,
                                 resolve_fn: Callable[..., object]) -> None:
    """Constructor call fallback: detecting implicit constructor invocations."""
    loc = cursor.location
    if _callee_has_file(cursor.referenced):
        return
    for child in cursor.get_children():
        if child.kind != cx.CursorKind.DECL_REF_EXPR:
            continue
        child_ref = child.referenced
        if child_ref is None:
            continue
        if child_ref.kind not in (cx.CursorKind.FIELD_DECL, cx.CursorKind.VAR_DECL):
            continue
        try:
            child_type = child_ref.type.get_canonical()
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("get_canonical failed for %s", child_ref.spelling)
            continue
        _collect_ctor_refs_from_type(
            child_type, loc.file.name, loc.line, cur_fn, refs, seen_ref,
        )


def _handle_indirect_invocations(cursor: cx.Cursor, cur_fn: str | None,
                                 refs: list[Reference],
                                 indirect_call_sites: list[IndirectCallSite],
                                 fp_assignments: list[FnPointerAssignment],
                                 pending_dispatches: list[PendingDispatch],
                                 seen_ref: set[tuple], tu_path_str: str,
                                 resolve_fn: Callable[..., object],
                                 qn_to_usr: dict[str, str],
                                 _log: logging.Logger) -> None:
    """Indirect calls: function pointers invoked directly or passed as call arguments.

    Two resolution strategies for indirect invocations:

    1. **Direct callee** — ``cursor.referenced`` resolves to a FIELD_DECL,
       VAR_DECL, or PARM_DECL whose type is a function pointer.  This
       covers the common case ``driver.onData(buf, len)`` where libclang
       resolves ``onData`` directly.  An IndirectCallSite is recorded.

    2. **Callee-expression fallback** — when ``cursor.referenced`` is
       ``None`` or does not match, the first child of the CALL_EXPR is
       unwrapped (through ``UNEXPOSED_EXPR`` layers) and its type is
       checked.  This covers ``stored_callback(42)`` where the callee is
       a variable rather than a field, and template-wrapped cases.
       ``_extract_lhs_field`` walks the callee expression to extract the
       target USR and name.

    After recording the call site, ``_handle_fn_ptr_as_argument`` scans
    the call arguments for function-pointer targets passed as parameters.
    """
    if cursor.kind != cx.CursorKind.CALL_EXPR:
        return
    loc = cursor.location

    callee = cursor.referenced
    if (callee is not None
            and callee.kind in (cx.CursorKind.FIELD_DECL, cx.CursorKind.VAR_DECL, cx.CursorKind.PARM_DECL)
            and _is_fn_ptr_type(callee.type)):
        target_usr = callee.get_usr()
        if target_usr:
            indirect_call_sites.append(IndirectCallSite(
                from_file=loc.file.name, from_line=loc.line, from_usr=cur_fn,
                expr_text=_call_expr_text(cursor), target_usr=target_usr,
                target_name=callee.spelling, fn_ptr_type=callee.type.spelling,
                certainty="exact", evidence="ast",
            ))
    else:
        callee_expr = _first_child_unwrapped(cursor)
        if callee_expr is not None and _is_fn_ptr_type(callee_expr.type):
            target_usr, target_name = _extract_lhs_field(callee_expr)
            if target_usr:
                try:
                    _fn_ptr_spelling = callee_expr.type.spelling
                except (ValueError, TypeError, RuntimeError, AttributeError):
                    _log.debug("type.spelling failed for indirect call callee expr")
                    _fn_ptr_spelling = ""
                indirect_call_sites.append(IndirectCallSite(
                    from_file=loc.file.name, from_line=loc.line, from_usr=cur_fn,
                    expr_text=_call_expr_text(cursor), target_usr=target_usr,
                    target_name=target_name, fn_ptr_type=_fn_ptr_spelling,
                    certainty="exact", evidence="ast",
                ))

    _handle_fn_ptr_as_argument(cursor, cur_fn, refs, fp_assignments, pending_dispatches,
                               seen_ref, tu_path_str, qn_to_usr, _log)



def _extract_fn_refs_from_unexposed(
    cursor: cx.Cursor,
    qn_to_usr: dict[str, str],
) -> list[tuple[str, str]]:
    """Extract ``(USR, rhs_name)`` pairs from UNEXPOSED_EXPR tokens.

    Parses token stream for ``&Class::method`` patterns and resolves
    them against *qn_to_usr*.  Returns ``[(usr, name), ...]`` tuples
    suitable for direct ``FnPointerAssignment`` creation.
    """
    results: list[tuple[str, str]] = []
    try:
        tokens = list(cursor.get_tokens())
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_extract_fn_refs_from_unexposed: get_tokens failed")
        return []
    for i, tok in enumerate(tokens):
        if tok.spelling == "&" and i + 3 < len(tokens):
            t1, t2, t3 = tokens[i + 1], tokens[i + 2], tokens[i + 3]
            if (t1.kind.name == "IDENTIFIER" and t2.spelling == "::"
                    and t3.kind.name == "IDENTIFIER"):
                partial = f"{t1.spelling}::{t3.spelling}"
                target_usr = qn_to_usr.get(partial)
                if not target_usr:
                    suffix = f"::{partial}"
                    for qn, usr in qn_to_usr.items():
                        if qn.endswith(suffix):
                            target_usr = usr
                            break
                if target_usr:
                    results.append((target_usr, t3.spelling))
    return results


def _object_usr_from_arg(arg: cx.Cursor) -> str:
    """Resolve the object USR behind a handle argument (registry v2).

    ``k_work_submit(&work)`` / ``xTimerStart(timer, 0)``: the argument is a
    UNARY_OPERATOR ``&``, a plain DECL_REF_EXPR, or a parenthesized/cast
    form of either.  Unwrap up to four such wrappers and return the USR of
    the referenced declaration; empty string when the argument does not
    name an indexable object (registry v2 keeps those sites skippable —
    recorded, not guessed).
    """
    cand = arg
    for _ in range(4):
        try:
            if cand.kind in (cx.CursorKind.UNARY_OPERATOR,
                             cx.CursorKind.PAREN_EXPR,
                             cx.CursorKind.CSTYLE_CAST_EXPR):
                children = list(cand.get_children())
                if not children:
                    return ""
                cand = children[0]
                continue
        except (ValueError, TypeError, RuntimeError, AttributeError):
            return ""
        break
    try:
        ref = cand.referenced
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return ""
    if ref is None:
        return ""
    try:
        return ref.get_usr() or ""
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return ""


def _handle_fn_ptr_as_argument(cursor: cx.Cursor, cur_fn: str | None,
                               refs: list[Reference],
                               fp_assignments: list[FnPointerAssignment],
                               pending_dispatches: list[PendingDispatch],
                               seen_ref: set[tuple], tu_path_str: str,
                               qn_to_usr: dict[str, str],
                               _log: logging.Logger) -> None:
    """Function pointers passed as call arguments.

    Emits an ``indirect`` reference for every callable target found in the
    arguments.  When the callee is directly resolvable AND its parameter at
    the matching index is a function-pointer type, an ``FnPointerAssignment``
    is also recorded (Phase 3 linking).  The indirect reference is emitted
    even when the callee cannot be resolved (e.g. ``_timeout.attach(
    callback(&WDT::_timeout_interrupt), delay)`` where ``attach`` is
    template-obscured) — previously an unresolved callee meant ``callee_params``
    was empty and the callback target was silently dropped.

    Additionally detects dispatch-registration patterns (e.g. EventQueue::
    call_every with a callback target) and records ``PendingDispatch`` records
    for deferred resolution into ``ref_kind='dispatch'`` synthetic edges.
    """
    loc = cursor.location
    direct_callee = cursor.referenced
    direct_callee_usr = direct_callee.get_usr() if direct_callee else None

    # ── Fallback callee resolution via callee expression ────────────────
    # When cursor.referenced is None (template-obscured), try to resolve
    # the callee through the first child of the CALL_EXPR (the callee expression).
    # E.g. _timeout.attach(...) where attach is a template — the CALL_EXPR
    # doesn't resolve it, but the MEMBER_REF_EXPR child might.
    callee_expr_text: str = ""
    if direct_callee is None:
        callee_expr = _first_child_unwrapped(cursor)
        callee_expr_text = _call_expr_text(cursor) if callee_expr else ""
        if callee_expr is not None:
            if callee_expr.kind == cx.CursorKind.MEMBER_REF_EXPR:
                for child in callee_expr.get_children():
                    if child.kind in _CALLABLE_KINDS:
                        direct_callee = child
                        break
            elif callee_expr.referenced is not None:
                direct_callee = callee_expr.referenced

    # ── Dispatch API detection (registry v2, ADR-010) ───────────────────
    _callee_dispatch_entry_qn: str | None = None
    _bridge_rule = None
    if direct_callee is not None:
        _callee_qn = _qualified_name(direct_callee)
        _bridge_rule = lookup_bridge_rule(_callee_qn)
        if _bridge_rule is not None and _bridge_rule.entry_qn:
            _callee_dispatch_entry_qn = _bridge_rule.entry_qn
    # ── End dispatch API detection ──────────────────────────────────────
    callee_params: list = []
    if direct_callee is not None:
        try:
            callee_params = list(direct_callee.get_arguments())
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("get_arguments failed for %s", direct_callee.spelling)
            callee_params = []
    callee_args = list(cursor.get_arguments())

    # ── Registry v2 (ADR-010) argument gating ───────────────────────────
    # handler_arg: pin the pending to the indexed handler argument when the
    # callee signature is visible and that parameter really is a function
    # pointer; template-erased callees (mbed call_every) keep the v1
    # any-argument behavior so existing detection cannot regress.
    _dispatch_arg_pinned = -1
    if _bridge_rule is not None and _bridge_rule.handler_arg is not None:
        try:
            if _bridge_rule.handler_arg < len(callee_params) and _is_fn_ptr_type(
                callee_params[_bridge_rule.handler_arg].type
            ):
                _dispatch_arg_pinned = _bridge_rule.handler_arg
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _dispatch_arg_pinned = -1
    # object_arg: resolve the object/handle USR once per call — object-
    # mediated triggers (k_work_submit(&w)) resolve their handler through
    # this linkage in post-processing, and REGISTER rules (k_work_init)
    # carry it alongside the handler.
    _dispatch_obj_usr = ""
    if _bridge_rule is not None and _bridge_rule.object_arg is not None:
        try:
            if _bridge_rule.object_arg < len(callee_args):
                _dispatch_obj_usr = _object_usr_from_arg(
                    callee_args[_bridge_rule.object_arg])
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _dispatch_obj_usr = ""
    _dispatch_pending_made = False

    def _param_info(i: int, arg: cx.Cursor | None = None) -> tuple[str | None, str]:
        """Return (param_usr, fn_ptr_type_spelling) for argument index *i*.

        When the callee parameter is a template type (e.g. ``F &&func``),
        falls back to the argument expression's type — so factory wrappers
        like ``callback(&func)`` are detected via their return type
        (e.g. ``Callback<void()>``).
        """
        if i >= len(callee_params):
            return None, ""
        try:
            param = callee_params[i]
        except (ValueError, TypeError, RuntimeError, AttributeError):
            return None, ""
        param_usr: str | None = None
        _fp_type: str = ""
        if _is_fn_ptr_type(param.type):
            param_usr = param.get_usr()
            if param_usr:
                try:
                    _fp_type = param.type.spelling
                except (ValueError, TypeError, RuntimeError, AttributeError):
                    _fp_type = ""
                return param_usr, _fp_type
        if arg is not None:
            try:
                arg_type = arg.type.get_canonical()
                if _is_fn_ptr_type(arg_type):
                    _fp_type = arg_type.spelling
                    return None, _fp_type
            except (ValueError, TypeError, RuntimeError, AttributeError):
                pass
        return None, ""

    for i, arg in enumerate(callee_args):
        targets = _find_fn_refs_in_expr(arg, direct_callee_usr)
        # Fallback: token-based extraction for opaque args
        # (UNEXPOSED_EXPR, unresolved CALL_EXPR like callback(...), etc.)
        if not targets:
            unexposed_pairs = _extract_fn_refs_from_unexposed(arg, qn_to_usr)
            if unexposed_pairs:
                param_usr, _fp_type = _param_info(i, arg)
                for target_usr, rhs_name in unexposed_pairs:
                    if target_usr != direct_callee_usr:
                        _add_ref(refs, seen_ref, target_usr, loc.file.name, loc.line, cur_fn, "indirect")
                        fp_assignments.append(FnPointerAssignment(
                            from_file=loc.file.name, from_line=loc.line,
                            lhs_usr=param_usr or "",
                            lhs_name=callee_expr_text or rhs_name,
                            rhs_usr=target_usr, rhs_name=rhs_name,
                            fn_ptr_type=_fp_type, method="call_arg",
                            from_usr=cur_fn,
                            # Token-derived (opaque argument node): not a
                            # cursor-resolved reference — 'possible'.
                            certainty="possible", evidence="unexposed_tokens",
                        ))
                        if _bridge_rule is not None and (
                            _dispatch_arg_pinned < 0
                            or i == _dispatch_arg_pinned
                        ):
                            pending_dispatches.append(PendingDispatch(
                                callee_qn=_callee_qn,
                                target_qn_partial="",
                                target_name=rhs_name,
                                target_usr=target_usr,
                                file=tu_path_str,
                                line=loc.line,
                                caller_usr=cur_fn,
                                object_usr=_dispatch_obj_usr or None,
                            ))
                            _dispatch_pending_made = True
        if targets:
            param_usr, _fp_type = _param_info(i, arg)
            for target in targets:
                target_usr = target.get_usr()
                if target_usr and target_usr != direct_callee_usr:
                    _add_ref(refs, seen_ref, target_usr, loc.file.name, loc.line, cur_fn, "indirect")
                    _lhs_name = callee_expr_text or target.spelling
                    fp_assignments.append(FnPointerAssignment(
                        from_file=loc.file.name, from_line=loc.line,
                        lhs_usr=param_usr or "", lhs_name=_lhs_name,
                        rhs_usr=target_usr, rhs_name=target.spelling,
                        fn_ptr_type=_fp_type, method="call_arg", from_usr=cur_fn,
                        certainty="exact", evidence="ast",
                    ))
                    if _bridge_rule is not None and (
                        _dispatch_arg_pinned < 0
                        or i == _dispatch_arg_pinned
                    ):
                        pending_dispatches.append(PendingDispatch(
                            callee_qn=_callee_qn,
                            target_qn_partial=_qualified_name(target),
                            target_name=target.spelling,
                            target_usr=target_usr,
                            file=tu_path_str,
                            line=loc.line,
                            caller_usr=cur_fn,
                            object_usr=_dispatch_obj_usr or None,
                        ))
                        _dispatch_pending_made = True
    # Object-mediated trigger with no handler argument at this call site
    # (``k_work_submit(&work)``): one pending carrying the object linkage;
    # the handler is resolved in post-processing via the REGISTER map.
    if (_bridge_rule is not None and _bridge_rule.object_arg is not None
            and _dispatch_obj_usr and not _dispatch_pending_made):
        pending_dispatches.append(PendingDispatch(
            callee_qn=_callee_qn,
            target_qn_partial="",
            target_name="",
            file=tu_path_str,
            line=loc.line,
            caller_usr=cur_fn,
            object_usr=_dispatch_obj_usr,
        ))
    _emit_fn_ptr_targets(cursor, cur_fn, seen_ref, refs, fp_assignments, direct_callee_usr, qn_to_usr=qn_to_usr)


def _init_list_is_positional(cursor: cx.Cursor) -> bool:
    """Report whether every element of an init list is given by position.

    A POSITIONAL list writes its elements in the order of the array, so the
    position of an element IS its index::

        void (*table[])(void) = { reset, nmi, hard_fault };   // 0, 1, 2

    A DESIGNATED list names the destination of each element, so the source
    order tells you nothing about the index::

        void (*table[])(void) = { [11] = svc, [3] = hard_fault };

    Only a positional list can give a slot number.  One designated element
    makes the whole list unsafe to count, because the elements after it
    continue from the index it named — not from their own position.

    The test reads the first token of each element: an array designator
    starts with ``[`` and a field designator with ``.``.  Two notes on why
    this is a token test and not an AST test:

    * The AST cannot tell the two apart here.  A designator ``[5] = &fn``
      exposes an INTEGER_LITERAL with two children as its first child — and
      so does the plain value ``1 + 2``.  A measurement over 12 lists put
      the AST rule wrong on 1 of them and this rule wrong on none.
    * A float element such as ``{ .5, &fn }`` does NOT read as a designator,
      because clang emits ``.5`` as one FLOATING_LITERAL token, not as ``.``
      followed by ``5``.  This was measured, not assumed.

    Returns False when the tokens cannot be read.  A slot number that is
    wrong is worse for a reader than a slot number that is absent, so an
    unreadable list gives up its positions instead of guessing them.
    """
    try:
        for element in cursor.get_children():
            tokens = list(element.get_tokens())
            if tokens and tokens[0].spelling in ("[", "."):
                return False
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return False
    return True


# Every way libclang spells an array type.  A slot number is an array index,
# so a list that initialises anything else has no slot to give.
_ARRAY_TYPE_KINDS = frozenset({
    cx.TypeKind.CONSTANTARRAY,
    cx.TypeKind.INCOMPLETEARRAY,
    cx.TypeKind.VARIABLEARRAY,
    cx.TypeKind.DEPENDENTSIZEDARRAY,
})


def _init_list_gives_slots(cursor: cx.Cursor) -> bool:
    """Report whether the positions in this init list are array indices.

    A position is only a slot number when all four hold:

    1. **The list is the outermost one.**  libclang reports a
       ``semantic_parent`` for the list that belongs to a declaration and
       None for a list nested inside another list.  This matters because an
       enclosing list already covers the whole subtree: for the Zephyr shape
       ``struct entry table[] = { {0, isr_a}, {0, isr_b} }`` the outer pass
       finds ``isr_a`` at outer position 0, which is the true slot, while
       the inner list would offer position 1 — the position of the ``fn``
       FIELD.  Recording both leaves two contradicting slots for one
       reference.  Measured over 18 lists in C and C++: the None test agreed
       with the true nesting every time.
    2. **The list initialises an array.**  A struct list numbers fields.
    3. **The array is one-dimensional.**  A table such as
       ``void (*fsm[2][2])(void)`` has a row and a column, and no single
       number is its slot.  The element type is read through
       ``get_canonical`` so a typedef cannot hide the second dimension.
    4. **The elements are positional** — see ``_init_list_is_positional``.

    Returns False when any type or parent query fails, on the same ground as
    the positional test: an absent slot beats a wrong one.
    """
    try:
        if cursor.semantic_parent is None:
            return False
        list_type = cursor.type
        if list_type.kind not in _ARRAY_TYPE_KINDS:
            return False
        element = list_type.get_array_element_type().get_canonical()
        if element.kind in _ARRAY_TYPE_KINDS:
            return False
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return False
    return _init_list_is_positional(cursor)


# The cursor kinds that wrap a value without changing what it is: the
# value of a designated element can sit inside each of them.
_VALUE_WRAPPERS = frozenset({
    cx.CursorKind.UNEXPOSED_EXPR,
    cx.CursorKind.PAREN_EXPR,
    cx.CursorKind.UNARY_OPERATOR,
    cx.CursorKind.CSTYLE_CAST_EXPR,
})
# The cursor kinds of a struct value that holds an init list of its own.
_LIST_VALUES = frozenset({
    cx.CursorKind.COMPOUND_LITERAL_EXPR,
    cx.CursorKind.CXX_FUNCTIONAL_CAST_EXPR,
})


def _holds_nested_list(value: cx.Cursor) -> bool:
    """Tell if *value* is a struct value that an init list of its own gives.

    ``{ ... }``, ``(struct ops){ ... }``, ``&(struct ops){ ... }`` and the
    C++ ``ops{ ... }`` all give their fields through an INIT_LIST_EXPR of
    their own, which the walk reaches and reads.  The last child of a
    wrapper is the wrapped value: a cast gives its TYPE_REF first.

    Only a list of a struct (or union, or an array of them) counts.  A
    list of an array of function pointers, ``.handlers = { f, g }``, or a
    scalar in braces, ``.cb = { f }``, has no designators of its own: its
    functions go to THIS field, and the element gives the row.
    """
    while value.kind in _VALUE_WRAPPERS:
        inner = list(value.get_children())
        if not inner:
            return False
        value = inner[-1]
    if value.kind in _LIST_VALUES:
        inner = list(value.get_children())
        if not inner:
            return False
        value = inner[-1]
    if value.kind != cx.CursorKind.INIT_LIST_EXPR:
        return False
    try:
        return _strip_arrays(value.type.get_canonical()).kind == cx.TypeKind.RECORD
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return False


def _strip_arrays(type_: cx.Type) -> cx.Type:
    """The element type of *type_* after every array level, or *type_* itself.

    A typedef of an array (``typedef cb_t cb_arr_t[4]``) is an array too:
    its declared type is read, and not its canonical type, thus the
    element keeps its own name (``cb_t``), the name that a call site has.
    The loop stops after a fixed number of steps: each step removes one
    level of sugar or of array, and a real type has few of them.
    """
    for _ in range(32):
        if type_.kind in _ARRAY_TYPE_KINDS:
            type_ = type_.element_type
        elif type_.kind == cx.TypeKind.ELABORATED:
            type_ = type_.get_named_type()
        elif type_.kind == cx.TypeKind.TYPEDEF and type_.get_canonical().kind in _ARRAY_TYPE_KINDS:
            type_ = type_.get_declaration().underlying_typedef_type
        else:
            break
    return type_


def _field_type_spelling(field_ref: cx.Cursor) -> str:
    """The type of the storage that receives the function: the field, or its element.

    A function can go into an array field only as an element, through an
    index (``.handlers[2] = f``, ``.h[1][2] = f``) or a brace list
    (``.handlers = { f, g }``).  Thus each array level comes off.  The
    type of the field (``cb_t[4]``) would never join on the type of a
    call site (``cb_t``).
    """
    try:
        return _strip_arrays(field_ref.type).spelling
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return ""


def _init_list_element_field(element: cx.Cursor) -> tuple[str | None, str, str]:
    """The field that one element of an INIT_LIST_EXPR assigns: (USR, name, type).

    Gives ``(None, "", "")`` when the element assigns no function to a
    field of this list.  The type is ``""`` when the caller must read it
    from the element, as before.

    libclang gives a designated element as an UNEXPOSED_EXPR whose children
    are the designators (MEMBER_REF, or an index for ``[n] =``), and then
    the value.  ``_extract_lhs_field``, which the element used before,
    takes the FIRST field reference anywhere in the element.  That gave
    these wrong rows, measured on the local indexes and on probes:

    * A value that is a nested struct (``.ops = { .init = f }``,
      ``.ops = (struct ops){ .init = f }``, ``{ .cmd = X, .handler = h }``
      in an array) wrote ``ops = f`` or ``cmd = h`` next to the correct
      row of the nested list.  Such an element now gives no row.  The
      nested list is an INIT_LIST_EXPR of its own, and the walk gives its
      fields.  The element still gives its indirect references, with the
      slot of an array (see ``_record_indirect``).
    * A chain of designators wrote its FIRST field: ``.ops.init = f`` gave
      ``ops = f``.  The chain now gives its LAST field, and the type of
      that field, because a name can repeat along the chain
      (``.cb.cb = f``).  An array field gives the type of its element
      (see ``_field_type_spelling``).

    An element that is neither of the two keeps the old reading.
    """
    if _holds_nested_list(element):
        return (None, "", "")
    children = list(element.get_children())
    if len(children) >= 2:
        if _holds_nested_list(children[-1]):
            return (None, "", "")
        designators = children[:-1]
        member_at = [i for i, c in enumerate(designators) if c.kind == cx.CursorKind.MEMBER_REF]
        if member_at:
            last = member_at[-1]
            ref = designators[last].referenced
            if ref is not None and ref.kind == cx.CursorKind.FIELD_DECL:
                return (ref.get_usr(), ref.spelling, _field_type_spelling(ref))
    usr, name = _extract_lhs_field(element)
    return (usr, name, "")


def _handle_fn_ptr_cases(cursor: cx.Cursor, cur_fn: str | None,
                        refs: list[Reference],
                        fp_assignments: list[FnPointerAssignment],
                        seen_ref: set[tuple], tu_path_str: str,
                        _log: logging.Logger) -> None:
    """Function pointers in assignments, variable initializers, and init lists.

    Three recognised patterns for function-pointer assignments:

    * **Binary assignment** — ``obj.onData = &handler`` or ``global_cb = &fn``.
      The LHS field/variable is extracted via ``_extract_lhs_field`` and
      passed to ``_emit_fn_ptr_targets`` for ``FnPointerAssignment`` creation.

    * **Variable initializer** — ``static void (*fp)(int) = &handler``.
      The variable declaration itself is the LHS; its USR and spelling
      become the assignment target for Phase 3 linking.

    * **Init-list member** — ``{.on_data = &handler, .cb = &fallback}``.
      Each init-list child that assigns a function pointer is processed
      individually, with the designated field name used as the LHS.

    Does NOT check return value of ``_emit_fn_ptr_targets`` — indirect
    references are always emitted even when no ``FnPointerAssignment`` is
    created (i.e. the assigned function has no call site yet).
    """

    if cursor.kind == cx.CursorKind.BINARY_OPERATOR:
        lhs_usr, lhs_name = _extract_lhs_field(cursor)
        _emit_fn_ptr_targets(cursor, cur_fn, seen_ref, refs, fp_assignments,
                             lhs_usr=lhs_usr, lhs_name=lhs_name, method="assignment")
    elif cursor.kind == cx.CursorKind.VAR_DECL and _is_fn_ptr_type(cursor.type):
        _emit_fn_ptr_targets(cursor, cur_fn, seen_ref, refs, fp_assignments,
                             lhs_usr=cursor.get_usr(), lhs_name=cursor.spelling, method="var_init")
    elif cursor.kind == cx.CursorKind.INIT_LIST_EXPR:
        # libclang gives the children in source order, and for a positional
        # array list that order is the array index — so the enumeration
        # counter is the slot number.  A hole keeps its child (`{ a, 0, c }`
        # has three), which is what keeps the count aligned with the array.
        gives_slots = _init_list_gives_slots(cursor)
        # Object-initializer fallback (T08 static linkage, registry v2):
        # a non-array init list initializing a VAR_DECL — e.g. the
        # K_WORK_DEFINE macro expansion ``struct k_work static_work =
        # { handler }`` — sits at file scope where _extract_lhs_field has
        # no field to name, so the object being initialized IS the LHS.
        # Array/slot lists keep their child-wise extraction unchanged.
        var_parent = None
        if not gives_slots:
            try:
                sp = cursor.semantic_parent
                if sp is not None and sp.kind == cx.CursorKind.VAR_DECL:
                    var_parent = sp
            except (ValueError, TypeError, RuntimeError, AttributeError):
                var_parent = None
        for position, child in enumerate(cursor.get_children()):
            child_usr, child_name, child_type = _init_list_element_field(child)
            if (child_usr is None and child_name == "" and not _holds_nested_list(child)
                    and var_parent is not None):
                child_usr = var_parent.get_usr()
                child_name = var_parent.spelling
            _emit_fn_ptr_targets(child, cur_fn, seen_ref, refs, fp_assignments,
                                 lhs_usr=child_usr, lhs_name=child_name, method="init_list",
                                 lhs_type=child_type,
                                 slot_index=position if gives_slots else None)


def _handle_implicit_constructors(cursor: cx.Cursor, cur_fn: str | None,
                                  refs: list[Reference], seen_ref: set[tuple],
                                  _log: logging.Logger) -> None:
    """Implicit constructor calls from global/static objects and member fields.

    Detects ``ref_kind='implicit_construct'`` edges: when a VAR_DECL or
    FIELD_DECL has a RECORD type (class/struct), iterates all constructors
    of that class and emits a call reference at the declaration site.
    This is necessary because libclang does not emit CALL_EXPR for implicit
    or default constructor invocations (e.g. ``Foo obj;`` or
    ``static Bar _bar;`` at file scope).

    The specific constructor overload is NOT distinguished — every
    constructor of the class gets a reference.  This is sufficient for
    call-graph dead-code detection and hotspot analysis, where the
    caller count should reflect all construction sites.
    """
    if cursor.kind not in (cx.CursorKind.VAR_DECL, cx.CursorKind.FIELD_DECL):
        return
    loc = cursor.location
    try:
        canon = cursor.type.get_canonical()
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("implicit_construct: get_canonical failed for %s", cursor.spelling)
        return
    if canon is None:
        return
    try:
        is_record = canon.kind == cx.TypeKind.RECORD
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("implicit_construct: canon.kind lookup failed for %s", cursor.spelling)
        return
    if not is_record:
        return
    try:
        class_cursor = canon.get_declaration()
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("implicit_construct: get_declaration failed")
        return
    if class_cursor is None:
        return
    for child in class_cursor.get_children():
        if child.kind != cx.CursorKind.CONSTRUCTOR:
            continue
        ctor_usr = child.get_usr()
        if not ctor_usr:
            continue
        ctor_loc = child.location
        if not ctor_loc.file:
            continue
        _add_ref(refs, seen_ref, ctor_usr, loc.file.name, loc.line, cur_fn, "implicit_construct")


def _token_stream_aligned(cursor: cx.Cursor, tokens: list) -> bool:
    """True when the token stream begins at the cursor's own extent start.

    A well-formed call starts at its extent: ``b.send(1)`` has extent start
    11:24 and first token ``b`` at 11:24.  A macro-generated call site does
    not: ``REGISTER(mcb)`` reports tokens that start inside the macro
    definition (line 8) while the extent sits at the use site (line 11), and
    ``INVOKE(mcb_slot, v)`` starts mid-expansion (column 32 vs extent column
    25).  Scanning such a stream fabricates call edges from neighboring
    identifiers — ``do_register → via_macro``, ``do_register → do_register``
    (T06 F4) — so a misaligned stream must emit no call edges.
    """
    if not tokens:
        return False
    try:
        ext = cursor.extent
        first = tokens[0].extent.start
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _log.debug("_token_stream_aligned: extent access failed")
        return False
    if first.file is not None and ext.start.file is not None:
        if Path(first.file.name) != Path(ext.start.file.name):
            return False
    return (first.line, first.column) == (ext.start.line, ext.start.column)


def _site_has_ast_call_edge(cursor: cx.Cursor, cur_fn: str | None,
                            refs: list[Reference]) -> bool:
    """True when this exact call site already carries an AST-resolved call edge.

    Per-site gate for the token fallback (T05 F1): when the AST resolved
    ``b.send(1)`` to ``Base::send(int)``, the bare-name token re-resolution
    must not add a second edge to a sibling overload — the qualified-name
    index keeps only the last declared overload, so the fabricated edge
    would point at ``send(double)``.  Two sources count as resolved: the
    CALL_EXPR's own ``referenced`` cursor, or an already-captured ``call``
    edge at the same (file, line, enclosing function).
    """
    try:
        referenced = cursor.referenced
    except (ValueError, TypeError, RuntimeError, AttributeError):
        referenced = None
    if referenced is not None and _callee_has_file(referenced):
        return True
    try:
        loc = cursor.location
    except (ValueError, TypeError, RuntimeError, AttributeError):
        return False
    for r in refs:
        if (r.ref_kind == "call" and r.from_file == loc.file.name
                and r.from_line == loc.line
                and (r.from_usr or None) == (cur_fn or None)):
            return True
    return False


def _handle_token_fallbacks(cursor: cx.Cursor, cur_fn: str | None,
                            refs: list[Reference],
                            fp_assignments: list[FnPointerAssignment],
                            seen_ref: set[tuple], qn_to_usr: dict[str, str],
                            usr_to_qn: dict[str, str], tu_path_str: str,
                            _log: logging.Logger,
                            call_sites: list[CallSite] | None = None,
                            seen_cs: set[tuple] | None = None,
                            template_fn_usrs: set[str] | None = None) -> None:
    """Token-based fallback detection: UNEXPOSED_EXPR and template-obscured calls.

    Operates on the raw token stream, not the libclang AST — necessary when
    template expansion produces ``UNEXPOSED_EXPR`` wrappers that hide the
    actual call/ref structure.

    Three detection patterns:

    1. **UNEXPOSED_EXPR** → extracts ``&Class::method`` via
       ``_extract_fn_refs_from_unexposed`` from any opaque expression node.

    2. **Token-level obj.method()** — scans CALL_EXPR tokens for
       ``IDENTIFIER DOT/ARROW IDENTIFIER LPAREN`` sequences and resolves
       the candidate list via ``_resolve_method_usr_candidates`` with the
       field name as hint.  ADR-004 layer 1 (T05): the resolution is
       recorded as a ``call_sites`` POSSIBLE row (every candidate listed),
       never as a refs ``call`` edge -- refs stay AST-proven.

    3. **Token-level bare call** — matches ``IDENTIFIER LPAREN`` where the
       identifier is not preceded by ``.`` or ``->`` (avoids double-counting
       with pattern 2).  Resolves via ``_resolve_method_usr`` with the
       enclosing class as hint.

    4. **Callback-style indirect targets** — ``&Class::method`` inside a
       CALL_EXPR argument list.  Emits ``ref_kind='indirect'`` and an
       ``FnPointerAssignment`` with ``method='call_arg'``.  (Function-
       pointer domain -- stays in refs, out of call-site scope.)

    A call inside a template body span (``template_fn_usrs``) is recorded
    as UNRESOLVED without any name resolution: an uninstantiated template
    has no receiver type, so a dependent name must never guess (T05 F6).
    """
    loc = cursor.location
    caller_qn = usr_to_qn.get(cur_fn or "") or ""

    if cursor.kind == cx.CursorKind.UNEXPOSED_EXPR:
        pairs = _extract_fn_refs_from_unexposed(cursor, qn_to_usr)
        for _target_usr, _rhs_name in pairs:
            _add_ref(refs, seen_ref, _target_usr, loc.file.name, loc.line, cur_fn, "indirect")

    if cursor.kind == cx.CursorKind.CALL_EXPR:
        try:
            tokens = list(cursor.get_tokens())
        except (ValueError, TypeError, RuntimeError, AttributeError):
            _log.debug("_handle_token_fallbacks: get_tokens failed")
            return
        in_template = bool(template_fn_usrs) and cur_fn in template_fn_usrs
        # Call-edge patterns below run only on sites that are (a) aligned —
        # the token stream begins at the cursor's own extent — and (b) not
        # already resolved by the AST.
        if _token_stream_aligned(cursor, tokens) \
                and not _site_has_ast_call_edge(cursor, cur_fn, refs):
            for i, tok in enumerate(tokens):
                if (tok.kind.name == "IDENTIFIER"
                        and i + 3 < len(tokens)
                        and tokens[i + 1].spelling in (".", "->")
                        and tokens[i + 2].kind.name == "IDENTIFIER"
                        and tokens[i + 3].spelling == "("):
                    if call_sites is not None:
                        _record_name_fallback_site(
                            call_sites, seen_cs, loc, cur_fn, tok.location.column,
                            tokens[i + 2].spelling, qn_to_usr, tok.spelling,
                            caller_qn, in_template, origin="token_fallback",
                        )
                    else:
                        target_usr = _resolve_method_usr(
                            tokens[i + 2].spelling, qn_to_usr, tok.spelling, caller_qn,
                        )
                        if target_usr:
                            _add_ref(refs, seen_ref, target_usr, loc.file.name, loc.line, cur_fn, "call")
                        else:
                            _log.debug("Token fallback: no USR for %s.%s() at %s:%d",
                                       tok.spelling, tokens[i + 2].spelling, loc.file.name, loc.line)
                if (tok.kind.name == "IDENTIFIER"
                        and i + 1 < len(tokens)
                        and tokens[i + 1].spelling == "("
                        and (i == 0 or tokens[i - 1].spelling not in (".", "->"))):
                    if call_sites is not None:
                        _record_name_fallback_site(
                            call_sites, seen_cs, loc, cur_fn, tok.location.column,
                            tok.spelling, qn_to_usr, "", caller_qn,
                            in_template, origin="token_fallback",
                        )
                    else:
                        target_usr = _resolve_method_usr(tok.spelling, qn_to_usr, caller_qn=caller_qn)
                        if target_usr:
                            _add_ref(refs, seen_ref, target_usr, loc.file.name, loc.line, cur_fn, "call")
        # Callback-style indirect targets: `&Class::method` passed as a call
        # argument (e.g. `_timeout.attach(callback(&WDT::_timeout_interrupt), delay)`).
        # libclang often resolves the outer call (attach) but leaves the
        # nested `&Class::method` opaque — emit the target as an indirect ref
        # so find_references / find_all_callers_recursive can see it.
        for i, tok in enumerate(tokens):
            if (tok.spelling == "&" and i + 3 < len(tokens)
                    and tokens[i + 1].kind.name == "IDENTIFIER"
                    and tokens[i + 2].spelling == "::"
                    and tokens[i + 3].kind.name == "IDENTIFIER"):
                partial = f"{tokens[i + 1].spelling}::{tokens[i + 3].spelling}"
                target_usr = _resolve_partial_qn(partial, qn_to_usr)
                if target_usr:
                    _add_ref(refs, seen_ref, target_usr, loc.file.name, loc.line, cur_fn, "indirect")
                    fp_assignments.append(FnPointerAssignment(
                        from_file=loc.file.name, from_line=loc.line,
                        lhs_usr="", lhs_name=tokens[i + 3].spelling,
                        rhs_usr=target_usr, rhs_name=tokens[i + 3].spelling,
                        fn_ptr_type="", method="call_arg",
                        from_usr=cur_fn,
                        # Token-stream provenance (extent-gated by SLICE-A):
                        # not cursor-resolved — 'possible'.
                        certainty="possible", evidence="token_fallback",
                    ))

# Keywords and built-in operators that the bare-call regex in
# _run_source_line_fallback must not emit as call edges.
# A frozenset gives O(1) containment check and prevents accidental
# mutation across the module's lifetime.
_BARE_CALL_KEYWORD_DENYLIST: frozenset[str] = frozenset({
    'if', 'while', 'for', 'return', 'switch', 'catch',
    'sizeof', 'decltype', 'typeof', 'alignof', 'noexcept',
    'static_cast', 'dynamic_cast', 'const_cast', 'reinterpret_cast',
})

def _run_source_line_fallback(
    tu: cx.TranslationUnit,
    refs: list[Reference],
    fp_assignments: list[FnPointerAssignment],
    pending_dispatches: list[PendingDispatch],
    seen_ref: set,
    _fn_spans: list[tuple[str, int, int]],
    _fn_def_lines: dict[int, str],
    qn_to_usr: dict[str, str],
    usr_to_qn: dict[str, str],
    _log,
    call_sites: list[CallSite] | None = None,
    seen_cs: set[tuple] | None = None,
    template_fn_usrs: set[str] | None = None,
) -> None:
    """Post-processing: scan source lines inside known function bodies for
    template-obscured patterns and emit missing references.

    Operates in four sequential phases per source line:

    **Phase 1 — obj.method() fallback**: regex-matches ``obj.method(`` patterns
    on lines NOT already covered by AST-extracted call/indirect edges.  With
    *call_sites* (T05, ADR-004 layer 2) the resolution lands as a call-site
    row -- POSSIBLE with every candidate (``_resolve_method_usr_candidates``)
    or UNRESOLVED -- never as a refs ``call`` edge; refs stay AST-proven.
    Inside a template body span the dependent name is UNRESOLVED with no
    candidates (an uninstantiated template has no receiver type).  When the
    matched method name is in ``_DISPATCH_METHOD_NAMES``, the single-pick
    resolution still runs to record ``PendingDispatch`` (dispatch rows are
    the proven-domain synthesizer's input, not call edges).  Without
    *call_sites* the legacy behavior (refs edges) is preserved.

    **Phase 2 — bare call fallback**: matches ``function(args)`` and
    ``function<T>(args)`` patterns (excluding language keywords in
    ``_BARE_CALL_KEYWORD_DENYLIST``).  Same call-site semantics as Phase 1.
    Suppresses self-references on the
    function's own definition line — the regex would otherwise match the
    declaration signature as a self-call.

    **Phase 3 — type-erased ISR registration**: detects patterns like
    ``NVIC_SetVector(IRQn, (uint32_t)handler)`` where the integer cast
    obliterates the function-pointer type.  Uses ``_TYPE_ERASED_ISR_FUNCTIONS``
    (a registry of known ISR-registration function names mapped to their
    handler-argument position) and attempts to resolve the handler name
    through both ``&Class::method`` regex and bare-name lookup.

    **Phase 4 — &Class::method on source text**: a final blanket scan for
    ``&Class::method`` patterns.  This runs on raw source text, not AST
    cursors, so it fires even when the TU is too degraded for libclang to
    produce ``CALL_EXPR`` cursors (common in embedded mbed-os builds where
    the AST-based ``_handle_token_fallbacks`` cannot see empty bodies).

    Phases 3/4 are the function-pointer domain -- they keep emitting refs
    ``indirect`` edges and ``FnPointerAssignment`` rows (out of call-site
    scope).

    Each phase uses ``seen_ref`` for deduplication -- a reference already
    found by an earlier phase or the AST walk is never re-emitted; call
    sites dedup on ``seen_cs`` (first writer wins).
    """
    import re as _re

    _tu_file = tu.spelling
    _lines_with_calls: set[int] = {
        r.from_line for r in refs
        if r.ref_kind in ("call", "indirect") and r.from_file == _tu_file
    }
    # Lines the preprocessor itself skipped (``clang_getAllSkippedRanges``,
    # the record of the active configuration).  A raw-text scan is
    # preprocessor-blind: without this filter an inactive ``#if`` branch
    # yields ghost call edges (T04 F04, T06 F1, T07 TC07-02) and phantom
    # dispatch rows (T08 F10).  The map is keyed by resolved path.
    try:
        _skipped_map = collect_skipped_lines(tu)
        _skipped_lines: set[int] = _skipped_map.get(Path(_tu_file).resolve(), set())
    except (ValueError, TypeError, RuntimeError, AttributeError, OSError):
        _skipped_lines = set()
    # Self-reference suppression keyed on the STORED definition line —
    # ``_fn_def_lines`` is built during the AST walk from
    # cursor.location.line (the signature line the symbols row stores),
    # not from the extent start, which an ``__attribute__`` above the
    # signature shifts up (T07 C6).
    try:
        _source_text = Path(_tu_file).read_text(encoding="utf-8", errors="replace")
        _source_lines = _source_text.splitlines()
    except (ValueError, TypeError, RuntimeError, AttributeError):
        _source_lines = []
    for _lineno_0, _line in enumerate(_source_lines):
        _lineno = _lineno_0 + 1
        if _lineno in _skipped_lines:
            # Inactive under the active build's ifdef filter — the line
            # never compiles, so no edge may originate here.
            continue
        if _lineno in _lines_with_calls:
            continue
        _line_fn = None
        for _fn_usr, _fn_start, _fn_end in _fn_spans:
            if _fn_start <= _lineno <= _fn_end:
                _line_fn = _fn_usr
                break
        if _line_fn is None:
            continue
        _line_qn = usr_to_qn.get(_line_fn or "") or ""
        # ── Phase 1: obj.method() fallback ─────────────────────────────
        # Matches ``field.method(...)`` or ``field->method(...)`` patterns.
        # Runs only on lines NOT already covered by AST-extracted call/indirect
        # edges (checked via _lines_with_calls fast-path above).
        # _resolve_method_usr uses the field name hint for receiver-class
        # disambiguation — e.g. ``_timeout.attach(...)`` resolves to
        # ``Timeout::attach`` because the field is named ``_timeout``.
        for _m in _re.finditer(r'(\w+)(?:\.|->)(\w+)\s*\(', _line):
            _field_name = _m.group(1)
            _method_name = _m.group(2)
            if call_sites is not None:
                # ADR-004 layer 2 (T05): the raw-text name resolution is a
                # guess -- record the call site (POSSIBLE with every
                # candidate, or UNRESOLVED), never a refs edge.
                _cs_col = _m.start() + 1
                if template_fn_usrs and _line_fn in template_fn_usrs:
                    _add_call_site(
                        call_sites, seen_cs, _tu_file, _lineno, _line_fn,
                        certainty="unresolved", resolution_kind="unresolved",
                        origin="raw_text_fallback", from_col=_cs_col,
                    )
                else:
                    _cands = _resolve_method_usr_candidates(
                        _method_name, qn_to_usr, _field_name, _line_qn,
                    )
                    if _cands:
                        _add_call_site(
                            call_sites, seen_cs, _tu_file, _lineno, _line_fn,
                            certainty="possible", resolution_kind="name_fallback",
                            origin="raw_text_fallback", from_col=_cs_col,
                            candidate_usrs=_cands,
                        )
                    else:
                        _add_call_site(
                            call_sites, seen_cs, _tu_file, _lineno, _line_fn,
                            certainty="unresolved", resolution_kind="unresolved",
                            origin="raw_text_fallback", from_col=_cs_col,
                        )
                # Dispatch-API detection still needs the single-pick
                # resolution: PendingDispatch rows feed the dispatch
                # synthesizer (proven domain), they are not call edges.
                if _method_name in _DISPATCH_METHOD_NAMES:
                    _target_usr = _resolve_method_usr(
                        _method_name, qn_to_usr, _field_name, _line_qn,
                    )
                    if _target_usr:
                        # Find callback targets in &Class::method arguments
                        for _cm in _re.finditer(r'&(\w+)::(\w+)', _line):
                            _partial = f"{_cm.group(1)}::{_cm.group(2)}"
                            _cb_usr = _resolve_partial_qn(_partial, qn_to_usr)
                            if _cb_usr:
                                pending_dispatches.append(PendingDispatch(
                                    callee_qn=_lookup_dispatch_entry_qn(
                                        _target_usr, qn_to_usr),
                                    target_qn_partial=_partial,
                                    target_name=_cm.group(2),
                                    target_usr=_cb_usr,
                                    file=_tu_file,
                                    line=_lineno,
                                    caller_usr=_line_fn,
                                ))
                continue
            _target_usr = _resolve_method_usr(_method_name, qn_to_usr, _field_name, _line_qn)
            if _target_usr:
                _key = (_target_usr, _tu_file, _lineno, _line_fn, "call")
                if _key not in seen_ref:
                    seen_ref.add(_key)
                    refs.append(Reference(
                        to_usr=_target_usr,
                        from_file=_tu_file,
                        from_line=_lineno,
                        from_usr=_line_fn,
                        ref_kind="call",
                    ))
                # ── Dispatch API detection ──────────────────────────────
                if _method_name in _DISPATCH_METHOD_NAMES:
                    # Look up the matched method's QN to check against
                    # _DISPATCH_ENTRY_POINTS (which maps callee QN → entry QN)
                    _dispatch_callee_qn = ""
                    for _dq in _DISPATCH_ENTRY_POINTS:
                        if qn_to_usr.get(_dq) == _target_usr:
                            _dispatch_callee_qn = _dq
                            break
                    if _dispatch_callee_qn:
                        # Find callback targets in &Class::method arguments
                        for _cm in _re.finditer(r'&(\w+)::(\w+)', _line):
                            _partial = f"{_cm.group(1)}::{_cm.group(2)}"
                            _cb_usr = _resolve_partial_qn(_partial, qn_to_usr)
                            if _cb_usr:
                                pending_dispatches.append(PendingDispatch(
                                    callee_qn=_dispatch_callee_qn,
                                    target_qn_partial=_partial,
                                    target_name=_cm.group(2),
                                    target_usr=_cb_usr,
                                    file=_tu_file,
                                    line=_lineno,
                                    caller_usr=_line_fn,
                                ))
            else:
                _log.debug(
                    "Source-line fallback: no USR for %s.%s() at %s:%d",
                    _field_name, _method_name, _tu_file, _lineno,
                )
        # ── Phase 2: bare call fallback ────────────────────────────────
        # Matches ``func(args)`` or ``func<T>(args)`` — direct function
        # calls without a receiver object.  Filters out C/C++ keywords via
        # _BARE_CALL_KEYWORD_DENYLIST (e.g. ``if(x)`` is not a call to
        # function ``if``).  Suppresses the self-reference false-positive:
        # a bare match of the enclosing function's own name on its
        # definition line is the declaration signature, not a recursive
        # call (e.g. ``void wdt_reset(void)`` matches ``wdt_reset(``).
        # Bare call fallback: function(args) or function<T>(args)
        # V-2 twin-row repair: the lookbehind also excludes '>', so a
        # member call the Phase-1 pattern already owns (``f->work(`` --
        # 'work' is preceded by '>') is not matched here a second time
        # under a different column.  Phase 1 and the token fallback own
        # member calls; Phase 2 owns bare calls.
        for _m in _re.finditer(r'(?<![.\w>])(\w+)(<[^>]+>)?\s*\(', _line):
            _method_name = _m.group(1)
            if _method_name in _BARE_CALL_KEYWORD_DENYLIST:
                continue
            if call_sites is not None:
                # ADR-004 layer 2 (T05): same call-site semantics as Phase 1.
                _cs_col = _m.start() + 1
                _cands = _resolve_method_usr_candidates(
                    _method_name, qn_to_usr, "", _line_qn,
                )
                # Suppress the declaration-signature self-match: a bare
                # match of the enclosing function's own name on its
                # definition start line is the signature, not a call.
                if (_cands == [_line_fn]
                        and _fn_def_lines.get(_lineno) == _line_fn):
                    continue
                if template_fn_usrs and _line_fn in template_fn_usrs:
                    _add_call_site(
                        call_sites, seen_cs, _tu_file, _lineno, _line_fn,
                        certainty="unresolved", resolution_kind="unresolved",
                        origin="raw_text_fallback", from_col=_cs_col,
                    )
                elif _cands:
                    _add_call_site(
                        call_sites, seen_cs, _tu_file, _lineno, _line_fn,
                        certainty="possible", resolution_kind="name_fallback",
                        origin="raw_text_fallback", from_col=_cs_col,
                        candidate_usrs=_cands,
                    )
                else:
                    _add_call_site(
                        call_sites, seen_cs, _tu_file, _lineno, _line_fn,
                        certainty="unresolved", resolution_kind="unresolved",
                        origin="raw_text_fallback", from_col=_cs_col,
                    )
                continue
            _target_usr = _resolve_method_usr(_method_name, qn_to_usr, caller_qn=_line_qn)
            if not _target_usr:
                continue
            # Suppress the false self-reference: a bare match of the enclosing
            # function's own name on its definition start line is the
            # declaration signature, not a call to itself.
            if (_target_usr == _line_fn and _fn_def_lines.get(_lineno) == _line_fn):
                continue
            _key = (_target_usr, _tu_file, _lineno, _line_fn, "call")
            if _key not in seen_ref:
                seen_ref.add(_key)
                refs.append(Reference(
                    to_usr=_target_usr, from_file=_tu_file,
                    from_line=_lineno, from_usr=_line_fn, ref_kind="call",
                ))
        # ── Phase 3: Type-erased ISR registration detection ────────────
        # APIs like NVIC_SetVector(IRQn, (uint32_t)handler) where
        # libclang cannot see the fn-ptr because of the integer cast.
        # _TYPE_ERASED_ISR_FUNCTIONS maps known ISR-registration function
        # names to their handler-argument position (currently unused in
        # the detection itself — the handler is found by regex, not by
        # argument indexing).  Two sub-strategies for handler resolution:
        #   a) ``&Class::method`` pattern in the argument text.
        #   b) Bare-name lookup of the last identifier in the arg list
        #      (after stripping parenthesized sub-expressions).
        for _isr_fn, _arg_pos in _TYPE_ERASED_ISR_FUNCTIONS.items():
            for _m in _re.finditer(rf'\b{_isr_fn}\s*\(', _line):
                _call_start = _m.end()
                _depth = 0
                _call_end = _call_start
                for _i in range(_call_start, len(_line)):
                    if _line[_i] == '(':
                        _depth += 1
                    elif _line[_i] == ')':
                        if _depth == 0:
                            _call_end = _i
                            break
                        _depth -= 1
                _args_text = _line[_call_start:_call_end]
                _isr_cm = _re.search(r'&(\w+)::(\w+)', _args_text)
                if _isr_cm:
                    _partial = f"{_isr_cm.group(1)}::{_isr_cm.group(2)}"
                    _handler_usr = _resolve_partial_qn(_partial, qn_to_usr)
                    _handler_name = _isr_cm.group(2)
                else:
                    _cleaned = _re.sub(r'\([^)]*\)', ' ', _args_text)
                    _ids = _re.findall(r'\b([A-Za-z_]\w*)\b', _cleaned)
                    if len(_ids) < 2:
                        continue
                    _handler_name = _ids[-1]
                    _handler_usr = _resolve_method_usr(
                        _handler_name, qn_to_usr, caller_qn=_line_qn,
                    )
                if not _handler_usr:
                    continue
                _key = (_handler_usr, _tu_file, _lineno, _line_fn, "indirect")
                if _key not in seen_ref:
                    seen_ref.add(_key)
                    refs.append(Reference(
                        to_usr=_handler_usr, from_file=_tu_file,
                        from_line=_lineno, from_usr=_line_fn,
                        ref_kind="indirect",
                    ))
                    fp_assignments.append(FnPointerAssignment(
                        from_file=_tu_file, from_line=_lineno,
                        lhs_usr="", lhs_name=_isr_fn,
                        rhs_usr=_handler_usr, rhs_name=_handler_name,
                        fn_ptr_type="", method="isr_vec",
                        from_usr=_line_fn,
                        # Raw-text provenance (type-erased ISR): not
                        # AST-proven — 'possible'.
                        certainty="possible", evidence="source_line_fallback",
                    ))
        # ── Phase 4: &Class::method on source text ─────────────────────
        # Blanket scan for ``&Class::method`` patterns on every source
        # line inside a known function body.  This runs on raw text, not
        # AST cursors — when a TU is too degraded for libclang to produce
        # CALL_EXPR cursors (embedded mbed-os builds with unrecoverable
        # template errors), the AST-based _handle_token_fallbacks cannot
        # see the empty body, but the source text still has the patterns.
        for _m in _re.finditer(r'&(\w+)::(\w+)', _line):
            _partial = f"{_m.group(1)}::{_m.group(2)}"
            _target_usr = _resolve_partial_qn(_partial, qn_to_usr)
            if not _target_usr:
                continue
            _key = (_target_usr, _tu_file, _lineno, _line_fn, "indirect")
            if _key not in seen_ref:
                seen_ref.add(_key)
                refs.append(Reference(
                    to_usr=_target_usr, from_file=_tu_file,
                    from_line=_lineno, from_usr=_line_fn, ref_kind="indirect",
                ))
                fp_assignments.append(FnPointerAssignment(
                    from_file=_tu_file, from_line=_lineno,
                    lhs_usr="", lhs_name=_m.group(2),
                    rhs_usr=_target_usr, rhs_name=_m.group(2),
                    fn_ptr_type="", method="call_arg",
                    from_usr=_line_fn,
                    # Raw-text provenance: not AST-proven — 'possible'.
                    certainty="possible", evidence="source_line_fallback",
                ))





@dataclass
class ExtractionResult:
    """Result of a single extract_all() call on one translation unit."""
    tu: cx.TranslationUnit | None = None
    symbols: list = field(default_factory=list)
    references: list = field(default_factory=list)
    inheritance: list = field(default_factory=list)
    indirect_call_sites: list = field(default_factory=list)
    fp_assignments: list = field(default_factory=list)
    macros: list = field(default_factory=list)
    pending_dispatches: list = field(default_factory=list)
    call_sites: list = field(default_factory=list)
    newly_seen_files: set[str] = field(default_factory=set)

def _discover_template_instances(
    candidates: list[cx.Cursor],
    symbols: list[Symbol],
    seen_usrs: dict[str, bool],
    _log,
) -> int:
    """T09/ADR-007 instance-discovery pass over already-walked cursors.

    The default libclang walk never yields implicit template
    instantiation decls (research T09 RC2), but instance cursors are
    reachable from expression/type cursors the walk DOES visit.  For each
    candidate:

    * ``CALL_EXPR``/``DECL_REF_EXPR``/``MEMBER_REF_EXPR``/``TYPE_REF`` →
      ``cursor.referenced``;
    * ``VAR_DECL``/``FIELD_DECL``/``PARM_DECL`` whose type spelling
      carries template arguments → ``cursor.type.get_declaration()``.

    A resolved cursor becomes a symbol observation (ADR-002) when the
    bound ``clang_getSpecializedCursorTemplate`` links it to a DIFFERENT
    template cursor: one row per instance USR, ``is_template=False``,
    ``template_usr`` = the template it instantiates.  The one-level child
    walk of each emitted instance adds member instances (methods, fields,
    implicit ctors/dtors) the expression paths never name; recursion is
    depth-capped per the ADR-007 partial-spec-chain risk note.

    Returns the number of instance symbols appended.
    """
    api = _bind_template_api()
    if api is None:
        return 0
    get_spec, _get_kind = api
    emitted = 0
    seen_inst: set[str] = set(seen_usrs)

    def _link(decl: cx.Cursor, own_usr: str) -> str:
        spec = _specialized_template(decl)
        if spec is None:
            return ""
        spec_usr = spec.get_usr() or ""
        # The C API returns the input cursor for non-specializations —
        # a self-link is a pattern, never an instance (T09 adversarial).
        if not spec_usr or spec_usr == own_usr:
            return ""
        return spec_usr

    def _resolve_link(decl: cx.Cursor, own_usr: str) -> str:
        link = _link(decl, own_usr)
        if link:
            return link
        # Implicit members of an implicit specialization (e.g. the
        # default ctor of Pair<double,int>) carry no
        # specialized-template link of their own — inherit the enclosing
        # instance's link.  A pattern member's parent is the pattern
        # itself, whose self-link never inherits, so pattern rows stay
        # unlinked (T09 adversarial).
        try:
            parent = decl.semantic_parent
        except (ValueError, TypeError, RuntimeError, AttributeError):
            return ""
        if parent is None:
            return ""
        try:
            p_usr = parent.get_usr() or ""
        except (ValueError, TypeError, RuntimeError, AttributeError):
            return ""
        if not p_usr or p_usr == own_usr:
            return ""
        return _link(parent, p_usr)

    def _emit(decl: cx.Cursor, depth: int) -> None:
        nonlocal emitted
        if depth > 2:  # instance → members; never deeper
            return
        if decl.kind not in _INSTANCE_EMIT_KINDS:
            return
        try:
            usr = decl.get_usr() or ""
        except (ValueError, TypeError, RuntimeError, AttributeError):
            return
        if not usr or usr in seen_inst:
            return
        spec_usr = _resolve_link(decl, usr)
        if not spec_usr:
            return
        loc = decl.location
        if not loc.file:
            return
        seen_inst.add(usr)
        try:
            is_def = bool(decl.is_definition())
        except (ValueError, TypeError, RuntimeError, AttributeError):
            is_def = False
        seen_usrs[usr] = is_def
        sem_parent = decl.semantic_parent
        parent_usr = ""
        if sem_parent is not None and sem_parent.kind in (
            cx.CursorKind.CLASS_DECL,
            cx.CursorKind.STRUCT_DECL,
            cx.CursorKind.UNION_DECL,
        ):
            parent_usr = sem_parent.get_usr() or ""
        symbols.append(Symbol(
            name=decl.spelling,
            qualified_name=_qualified_name(decl),
            kind=_cursor_kind_label(decl.kind),
            file=loc.file.name,
            line=loc.line,
            column=loc.column,
            is_definition=is_def,
            signature=_signature(decl),
            docstring=_docstring(decl),
            usr=usr,
            end_line=_end_line(decl, loc),
            end_column=_end_column(decl, loc),
            is_template=False,
            template_usr=spec_usr,
            parent_usr=parent_usr,
        ))
        emitted += 1
        try:
            children = list(decl.get_children())
        except (ValueError, TypeError, RuntimeError, AttributeError):
            children = []
        for child in children:
            _emit(child, depth + 1)

    for cand in candidates:
        if cand.kind in _TEMPLATE_EXPR_CANDIDATES:
            try:
                ref = cand.referenced
            except (ValueError, TypeError, RuntimeError, AttributeError):
                ref = None
            if ref is not None:
                _emit(ref, 0)
        elif cand.kind in _TEMPLATE_TYPE_CANDIDATES:
            try:
                spelling = cand.type.spelling
            except (ValueError, TypeError, RuntimeError, AttributeError):
                spelling = ""
            if "<" not in spelling:
                continue  # cheap pre-filter: plain types pay one spelling
            try:
                decl = cand.type.get_declaration()
            except (ValueError, TypeError, RuntimeError, AttributeError):
                decl = None
            if decl is not None:
                _emit(decl, 0)
    if emitted:
        _log.debug("template instance discovery: %d observations", emitted)
    return emitted


def extract_all(
    unit: CompilationUnit,
    with_refs: bool = False,
    *,
    return_tu: bool = False,
    skip_files: set[str] | frozenset[str] | None = None,
) -> ExtractionResult:
    """Parse one translation unit and return extracted symbols, references, and inheritance.

    This is the single-pass entry point for all data extraction from a
    ``CompilationUnit``.  It parses the file with libclang, walks the AST
    once, and produces multiple outputs simultaneously.

    When *skip_files* is a non-empty ``set[str]``, subtrees rooted in
    already-processed header files are skipped during all AST walks
    (symbols, references, macros, anon-field mapping, and content fill).
    This eliminates 91–97 % of duplicate work for headers already
    visited by earlier translation units in the same indexing run.

    No filtering is done at the extraction level — all symbols and references
    from all included files are extracted.  Filtering (project vs vendor)
    happens downstream in ``store_symbols_for_unit`` via ``is_project``.

    Args:
        unit: The translation unit to parse (file path + compiler flags).
        with_refs: When True, also extract call, reference, and member-access
            edges and embed a source-line token fallback pass for
            template-obscured expressions.  Inheritance is always extracted
            regardless of this flag.
        skip_files: Optional set of resolved file paths whose subtrees
            should be skipped.  Passed through to all internal walkers.
            When ``None`` or empty, the native ``walk_preorder`` is used
            (faster for the first TU with an empty skip set).

    Returns:
        An ``ExtractionResult`` with symbols, references, inheritance,
        indirect_call_sites, fp_assignments, macros, pending_dispatches,
        and newly_seen_files (the set of all source file paths encountered
        during the symbol walk — used to accumulate the skip set for
        subsequent translation units).
    """

    import logging
    import time as _time
    _log = logging.getLogger(__name__)
    _t_start = _time.monotonic()
    tu = _get_index().parse(
        str(unit.file),
        args=unit.clang_args,
        options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
    )
    _t_parse = _time.monotonic() - _t_start

    cwd = unit.directory

    # maxsize=None — the cache lives only for this extract_all() call
    # (one TU).  Even 10 000 unique header paths (~2 MB) is negligible,
    # and unbounded cache avoids eviction churn during the AST walk.
    @cache
    def _resolve(path: str) -> Path:
        p = Path(path)
        return (cwd / p).resolve() if not p.is_absolute() else p.resolve()

    # Convert mutable set to frozen for hash-consistency across call sites.
    # The mutable set lives in runner.py; each extract_all call gets a
    # stable frozen view that cannot be accidentally mutated.
    _skip_frozen: frozenset[str] | None = (
        frozenset(skip_files) if skip_files else None
    )

    # --- Symbols ---
    symbols: list[Symbol] = []
    seen_usrs: dict[str, bool] = {}  # USR → is_definition (allows decl→def promotion)
    class_cursors: list[cx.Cursor] = []  # class/struct def cursors for inheritance extraction
    # T07/ADR-009 root observations queued during the walk, materialized as
    # refs after the alias targets can be resolved against the full symbol
    # list (the alias declaration may precede its target in the file).
    pending_ctors: list[tuple[str, str, int]] = []
    pending_aliases: list[tuple[str, str, int, str]] = []

    # Pre-scan: map anonymous struct/union USRs to their field names
    # (e.g. struct { ... } _radio_cmd;  →  anon_usr → "_radio_cmd")
    anon_usr_to_field = _build_anon_usr_to_field(
        tu.cursor, skip_files=_skip_frozen, resolve_fn=_resolve,
    )

    tu_path_str = str(_resolve(tu.spelling))

    # Collect every source file path seen during the symbol walk.
    # Includes ALL files, not just those with _SYMBOL_KINDS cursors —
    # a header with only forward declarations still contributes to the
    # skip set so subsequent TUs can skip its subtree entirely.
    _newly_seen: set[str] = set()

    # T09/ADR-007: expression/type cursors that may name a template
    # instance, queued during this same walk (no second AST pass).
    _template_candidates: list[cx.Cursor] = []

    if _skip_frozen:
        _skipper = _iter_cursor_skip(tu.cursor, _skip_frozen, _resolve)
    else:
        _skipper = tu.cursor.walk_preorder()

    # F3P: USR → index of this TU's stored symbol row, so a definition
    # arriving after its declaration replaces the row (definition wins).
    _row_by_usr: dict[str, int] = {}

    for cursor in _skipper:
        if cursor.location.file:
            fpath = str(_resolve(str(cursor.location.file.name)))
            _newly_seen.add(fpath)

        if cursor.kind in _TEMPLATE_EXPR_CANDIDATES or cursor.kind in _TEMPLATE_TYPE_CANDIDATES:
            _template_candidates.append(cursor)

        if cursor.kind not in _SYMBOL_KINDS:
            continue
        _process_one_symbol(cursor, symbols, seen_usrs, class_cursors, anon_usr_to_field, _log,
                            pending_ctors=pending_ctors, pending_aliases=pending_aliases,
                            row_by_usr=_row_by_usr)

    _t_symwalk = _time.monotonic() - _t_start - _t_parse  # subtract parse time

    # --- Inheritance: examine base specifiers of collected class cursors ---
    inheritance = _extract_inheritance(class_cursors)

    # --- Macro definitions (#define) ---
    macros = _extract_macros(tu.cursor, _resolve, skip_files=_skip_frozen)

    if not with_refs:
        # T09/ADR-007 instance discovery also runs on the no-refs path
        # (index_refs=False keeps full template coverage).
        _t_pre_discovery = _time.monotonic()
        _discover_template_instances(_template_candidates, symbols, seen_usrs, _log)
        _t_discovery = _time.monotonic() - _t_pre_discovery
        _log.info(
            "  parse=%.1fs symwalk=%.1fs discovery=%.3fs syms=%d macros=%d",
            _t_parse, _t_symwalk, _t_discovery, len(symbols), len(macros),
        )
        result = ExtractionResult(
            tu=tu if return_tu else None, symbols=symbols, references=[],
            inheritance=inheritance, indirect_call_sites=[], fp_assignments=[],
            macros=macros, newly_seen_files=_newly_seen,
        )
        return result

    refs, indirect_call_sites, fp_assignments, pending_dispatches, call_sites = _build_refs_and_fp_assignments(
        tu, tu_path_str, symbols, _resolve, anon_usr_to_field, _log,
        skip_files=_skip_frozen,
    )
    # Root evidence edges (T07/ADR-009) — appended after the ref walk so
    # alias targets resolve against the COMPLETE symbol list of this TU.
    _append_attr_root_refs(refs, pending_ctors, pending_aliases, symbols, _log)

    # T09/ADR-007 instance discovery runs AFTER the ref walk: instance
    # rows join refs/target USRs at query time, and keeping them out of
    # the name-fallback maps built inside _build_refs_and_fp_assignments
    # leaves SLICE-G resolution semantics untouched.
    _t_pre_discovery = _time.monotonic()
    _discover_template_instances(_template_candidates, symbols, seen_usrs, _log)
    _t_discovery = _time.monotonic() - _t_pre_discovery

    _t_total = _time.monotonic() - _t_start
    _t_refwalk = _t_total - _t_parse - _t_symwalk - _t_discovery
    _log.debug(
        "  parse=%.1fs symwalk=%.1fs refwalk=%.1fs discovery=%.3fs syms=%d refs=%d macros=%d",
        _t_parse, _t_symwalk, _t_refwalk, _t_discovery, len(symbols), len(refs), len(macros),
    )
    result = ExtractionResult(
        tu=tu if return_tu else None, symbols=symbols, references=refs,
        inheritance=inheritance, indirect_call_sites=indirect_call_sites,
        fp_assignments=fp_assignments, macros=macros,
        pending_dispatches=pending_dispatches, call_sites=call_sites,
        newly_seen_files=_newly_seen,
    )
    return result
