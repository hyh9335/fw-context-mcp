"""Conditional compilation provenance (T03 / ADR-009 / ADR-011 / ADR-013).

WHY this module exists: the index must answer "which branch of the
preprocessor took this conditional, and which macro values decide it" —
per context (ADR-013), because the same file under two ``-D`` sets sees
different answers.  The preprocessor record that ``skipped_ranges``
already holds gives the ground truth of the taken branch: a branch the
preprocessor kept has at least one line no read of the file skipped, and
a branch it skipped has none.  The ordering rule of the frozen plan
(T03, derived from clang ``-E`` on the F03 fixture) turns that into
branch states:

- the FIRST branch whose body holds a live line is TAKEN (+1);
- branches BEFORE it were evaluated and are FALSE (0);
- branches AFTER it were never evaluated (-1);
- a nested group whose enclosing group took a different branch is never
  reached: every branch -1 (the C-shape of the frozen fixture).

Live content looks through nested groups: a branch whose body is only a
nested conditional is taken when that nested group has a taken branch.
The recursion bottoms out in the skipped map — every line of a group
inside a false or not-evaluated branch is skipped, so it cannot make an
earlier branch look live.

LIMITATION (documented, T03): symbol bodies keep the older semantics —
they subtract only the skipped lines and do not restore boundary
directive lines, because ``extract_all`` computes its own skipped map.
Only ``files.content`` uses the boundary-keep predicate.  A taken empty
``#else`` is decided by the directive-line rule (its directive ends the
previous skipped range); a taken empty ``#elif`` inside a MERGED range
cannot be decided and must be surfaced as inexact (``exact=False`` plus
a ``limitation`` note), never silently claimed exact.

Macro provenance (T03 C4): the identifiers of an evaluated condition
resolve against the macro map of the same parse.  A definition with a
source file is ``file``; one without a file is attributed through the
presumed location, which libclang spells exactly: ``<command line>`` for
``-D`` flags and ``<built-in>`` for compiler macros.  An identifier with
no definition anywhere gets an empty ``def_source`` — reported as
unknown, never fabricated.
"""

from __future__ import annotations

import ctypes
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache

log = logging.getLogger(__name__)

_DIRECTIVE_RE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b[ \t]*(.*)$")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")

GROUP_ROOTS = ("if", "ifdef", "ifndef")


def is_conditional_directive_line(text: str) -> bool:
    """True when *text* is a preprocessor conditional directive line."""
    return _DIRECTIVE_RE.match(text) is not None


# ── Directive parsing ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConditionalDirective:
    """One conditional directive line (T03 A03: lineage is directional)."""

    line: int            # 1-based source line
    kind: str            # if | ifdef | ifndef | elif | else | endif
    condition: str | None  # raw expression text; None for #else/#endif


def parse_conditional_directives(lines: Sequence[str]) -> list[ConditionalDirective]:
    """Parse the conditional directives of one file, in line order."""
    out: list[ConditionalDirective] = []
    for number, text in enumerate(lines, start=1):
        match = _DIRECTIVE_RE.match(text)
        if match is None:
            continue
        kind = match.group(1).lower()
        rest = match.group(2).strip()
        # #else/#endif carry no condition; lineage only (T03 A03).
        condition = None if kind in ("else", "endif") else (rest or None)
        out.append(ConditionalDirective(line=number, kind=kind, condition=condition))
    return out


# ── Nesting tree ──────────────────────────────────────────────────────────


@dataclass
class ConditionalBranch:
    """One branch arm of a conditional group."""

    directive: ConditionalDirective
    kind: str  # if | elif | else
    body_lines: tuple[int, ...] = ()


@dataclass
class ConditionalGroup:
    """One ``#if``/``#ifdef``/``#ifndef`` group with its branches."""

    root: ConditionalDirective
    branches: list[ConditionalBranch] = field(default_factory=list)
    nested: list["ConditionalGroup"] = field(default_factory=list)
    parent: "ConditionalGroup | None" = None
    end_line: int = 0    # line of the closing #endif (0 = unterminated)


def build_conditional_tree(directives: Sequence[ConditionalDirective]) -> list[ConditionalGroup]:
    """Build the nesting tree with a directive stack.

    ``#elif``/``#else`` append a branch to the innermost open group; the
    ``#endif`` closes it and records the group's end line.  A stray
    ``#elif``/``#else``/``#endif`` with no open group is ignored.
    """
    roots: list[ConditionalGroup] = []
    stack: list[ConditionalGroup] = []
    for directive in directives:
        if directive.kind in GROUP_ROOTS:
            group = ConditionalGroup(root=directive, parent=stack[-1] if stack else None)
            if stack:
                stack[-1].nested.append(group)
            else:
                roots.append(group)
            group.branches.append(ConditionalBranch(directive, kind="if"))
            stack.append(group)
        elif directive.kind in ("elif", "else"):
            if not stack:
                continue
            stack[-1].branches.append(ConditionalBranch(directive, kind=directive.kind))
        else:  # endif
            if stack:
                stack[-1].end_line = directive.line
                stack.pop()
    # Fill branch bodies: the lines between one branch directive and the
    # next branch directive of the same group, or the closing #endif.
    for root in roots:
        _fill_bodies(root)
    return roots


def _fill_bodies(group: ConditionalGroup) -> None:
    bounds = [b.directive.line for b in group.branches] + [group.end_line + 1]
    for index, branch in enumerate(group.branches):
        start = bounds[index] + 1
        stop = bounds[index + 1] - 1
        branch.body_lines = tuple(range(start, stop + 1)) if start <= stop else ()
    for nested in group.nested:
        _fill_bodies(nested)


# ── Evaluation (the P2 ordering rule) ─────────────────────────────────────


@dataclass(frozen=True)
class BranchState:
    """Evaluation of one branch under one context (ADR-011)."""

    line: int
    kind: str  # if | elif | else
    condition: str | None
    taken: int  # 1 TAKEN | 0 FALSE | -1 NOT_EVALUATED


@dataclass
class GroupEvaluation:
    """Evaluation of one conditional group under one context."""

    root_line: int
    states: list[BranchState]
    taken_branch_line: int | None = None
    exact: bool = True
    limitation: str | None = None


def evaluate_conditional_tree(
    groups: Sequence[ConditionalGroup],
    skipped: set[int],
    kept: set[int] | None = None,
) -> dict[int, GroupEvaluation]:
    """Evaluate every group of a file against its skipped/kept line sets.

    Args:
        groups: the tree of :func:`build_conditional_tree` for ONE file.
        skipped: the lines every read of the file skipped.
        kept: boundary directive lines the skip filter restores (the
            ends of skipped ranges that start no range).  Only used by
            the empty-body fallback.

    Returns:
        A map from the root line of each group to its evaluation.
    """
    out: dict[int, GroupEvaluation] = {}

    def branch_has_live_content(group: ConditionalGroup, index: int) -> bool:
        branch = group.branches[index]
        if any(line not in skipped for line in branch.body_lines):
            return True
        stop = (
            group.branches[index + 1].directive.line
            if index + 1 < len(group.branches)
            else group.end_line
        )
        for nested in group.nested:
            if branch.directive.line < nested.root.line < stop:
                if group_has_live_content(nested):
                    return True
        return False

    def group_has_live_content(group: ConditionalGroup) -> bool:
        return any(branch_has_live_content(group, i) for i in range(len(group.branches)))

    def evaluate(group: ConditionalGroup, reached: bool) -> None:
        states: list[BranchState] = []
        taken_line: int | None = None
        exact = True
        limitation: str | None = None
        if reached:
            taken_index = None
            for index in range(len(group.branches)):
                if branch_has_live_content(group, index):
                    taken_index = index
                    break
            if taken_index is None and kept:
                # Empty-body fallback: a branch whose body holds no line
                # the preprocessor kept can still be the taken one when
                # its own directive line ended the previous skipped range.
                for index, branch in enumerate(group.branches):
                    if branch.directive.line in kept:
                        taken_index = index
                        # An #else kept at a range boundary is provably
                        # taken; a kept #elif inside a MERGED range is
                        # ambiguous (T03 documented limitation).
                        exact = branch.kind == "else"
                        if not exact:
                            limitation = (
                                "taken branch has an empty body inside a merged "
                                "skipped range; the directive-line fallback is "
                                "not exact here (T03 documented limitation)"
                            )
                        break
            if taken_index is None and any(
                not group.branches[i].body_lines for i in range(len(group.branches))
            ):
                # No live line anywhere and an empty body present: the
                # all-FALSE default may be wrong for the empty branch.
                exact = False
                limitation = (
                    "no branch has a live body line and an empty branch body "
                    "cannot be attributed from the skipped-range record "
                    "(T03 documented limitation)"
                )
            for index, branch in enumerate(group.branches):
                if taken_index is None:
                    taken = 0
                elif index < taken_index:
                    taken = 0
                elif index == taken_index:
                    taken = 1
                    taken_line = branch.directive.line
                else:
                    taken = -1
                states.append(BranchState(
                    line=branch.directive.line, kind=branch.kind,
                    condition=branch.directive.condition, taken=taken,
                ))
        else:
            # The enclosing group took another branch: this group is
            # never reached — every branch NOT_EVALUATED (ADR-011).
            for branch in group.branches:
                states.append(BranchState(
                    line=branch.directive.line, kind=branch.kind,
                    condition=branch.directive.condition, taken=-1,
                ))
        evaluation = GroupEvaluation(
            root_line=group.root.line, states=states,
            taken_branch_line=taken_line, exact=exact, limitation=limitation,
        )
        out[group.root.line] = evaluation
        for nested in group.nested:
            child_reached = (
                reached
                and taken_line is not None
                and taken_line < nested.root.line < _taken_branch_end(group, taken_line)
            )
            evaluate(nested, child_reached)

    for group in groups:
        evaluate(group, True)
    return out


def _taken_branch_end(group: ConditionalGroup, taken_line: int) -> int:
    """First line AFTER the body of the taken branch (its next sibling)."""
    for index, branch in enumerate(group.branches):
        if branch.directive.line == taken_line:
            if index + 1 < len(group.branches):
                return group.branches[index + 1].directive.line
            return group.end_line
    return group.end_line


# ── Macro provenance ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class MacroProvenance:
    """Where the definition of one macro came from (T03 C4)."""

    name: str
    def_source: str   # file | command_line | builtin | '' (undefined)
    def_file: str     # absolute path for file definitions, '' otherwise
    def_line: int | None


_COMMAND_LINE = "<command line>"
_BUILT_IN = "<built-in>"


@cache
def _presumed_location_fn():
    """Bind ``clang_getPresumedLocation`` once, or None when unavailable.

    The python bindings of libclang do not expose it, so the binding is
    hand-made with ctypes — the same shape ``skipped_ranges`` uses for
    ``clang_getAllSkippedRanges``.  A binding failure must not stop an
    index run: the caller then reports an empty def_source instead.
    """
    try:
        from clang import cindex

        lib = cindex.conf.lib
        fn = lib.clang_getPresumedLocation
        fn.argtypes = [
            cindex.SourceLocation,
            ctypes.POINTER(ctypes.c_char_p),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint),
        ]
        fn.restype = None

        def presumed(location):
            buf = ctypes.c_char_p()
            line = ctypes.c_uint()
            column = ctypes.c_uint()
            fn(location, ctypes.byref(buf), ctypes.byref(line), ctypes.byref(column))
            name = buf.value.decode("utf-8", "replace") if buf.value else ""
            return name, int(line.value)

        return presumed
    except (AttributeError, OSError, ValueError, TypeError, ImportError):
        log.debug("clang_getPresumedLocation unavailable — macro attribution degrades", exc_info=True)
        return None


def presumed_macro_buffer(location) -> str | None:
    """The presumed buffer name of a source location, or None.

    ``<command line>`` and ``<built-in>`` are the two spellings libclang
    uses for definitions that have no file; a plain path means the
    definition has a source file.
    """
    fn = _presumed_location_fn()
    if fn is None:
        return None
    try:
        name, _line = fn(location)
    except (ctypes.ArgumentError, AttributeError, TypeError, ValueError):
        return None
    return name or None


def collect_macro_provenance(tu_cursor) -> dict[str, MacroProvenance]:
    """Map every macro the parse saw to where it was defined.

    Walks the MACRO_DEFINITION cursors of the unit WITHOUT any
    ``skip_files`` filter — provenance must attribute a macro no matter
    which header claimed it.  Definitions with a file keep their file
    and line; ``loc.file is None`` resolves through the presumed
    location.
    """
    provenance: dict[str, MacroProvenance] = {}
    presumed = _presumed_location_fn()
    from clang.cindex import CursorKind

    for child in tu_cursor.get_children():
        if child.kind != CursorKind.MACRO_DEFINITION:
            continue
        loc = child.location
        name = child.spelling
        if loc.file is not None:
            provenance[name] = MacroProvenance(
                name=name, def_source="file", def_file=str(loc.file), def_line=loc.line
            )
            continue
        if presumed is None:
            # No attribution possible — record the name with an empty
            # source so consumers see "unknown", never a fabricated file.
            provenance.setdefault(name, MacroProvenance(name=name, def_source="", def_file="", def_line=None))
            continue
        try:
            buffer_name, buffer_line = presumed(loc)
        except (ctypes.ArgumentError, AttributeError, TypeError, ValueError):
            provenance.setdefault(name, MacroProvenance(name=name, def_source="", def_file="", def_line=None))
            continue
        if buffer_name == _COMMAND_LINE:
            source = "command_line"
        elif buffer_name == _BUILT_IN:
            source = "builtin"
        else:
            source = ""
        provenance[name] = MacroProvenance(
            name=name, def_source=source, def_file="", def_line=None
        )
    return provenance


def collect_macro_inputs(
    condition_texts: Sequence[str | None],
    provenance: dict[str, MacroProvenance],
) -> list[MacroProvenance]:
    """Attribute the identifiers of evaluated conditions against *provenance*.

    ``defined`` is excluded; every other identifier of the condition
    text is a macro candidate.  An identifier with no definition gets an
    empty ``def_source`` (unknown), never a fabricated location.
    """
    names: set[str] = set()
    for text in condition_texts:
        if not text:
            continue
        for ident in _IDENT_RE.findall(text):
            if ident == "defined":
                continue
            names.add(ident)
    known = provenance.get
    return [known(n) or MacroProvenance(name=n, def_source="", def_file="", def_line=None) for n in sorted(names)]
