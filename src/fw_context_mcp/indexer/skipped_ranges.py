"""Line ranges that the preprocessor skipped, from the record of libclang.

WHY this module exists: the index must show only the code that compiles for
the active build.  Two earlier sources of that answer both let dead code
through, because each one only guessed at it:

1. ``tu.cursor.get_tokens()`` — ``clang_tokenize`` is a raw lexer over a
   range of characters.  It does not do preprocessing, thus it gives tokens
   for an inactive ``#if`` branch also.
2. The extent of a cursor — an extent is one continuous range of lines.  A
   dead block inside the body of a function is inside the extent of that
   function, thus the extent keeps it.

``clang_getAllSkippedRanges`` is not a guess.  It is the record that the
preprocessor itself made of the ranges it skipped.  The assembly path
(``asm.py``) already takes its active lines from the output of the
preprocessor.  This module gives the C path the same class of answer.

The python bindings of libclang do not give this function, thus the module
binds it with ``ctypes``.  ``clang_getAllSkippedRanges`` is in libclang from
version 5.0, and the project needs libclang 18.1.1 or later, thus the
function is always available.  The code degrades to an empty answer all the
same, because a build of libclang that lacks the symbol must not stop an
index run.
"""

from __future__ import annotations

import ctypes
import logging
from functools import cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@cache
def _bind() -> tuple[Any, Any] | None:
    """Bind the two C functions once, or give None when libclang lacks them.

    Returns ``(get_all_skipped_ranges, dispose_source_range_list)``, or None.

    The ``CXSourceRangeList`` structure is local to this function because it
    needs the ``SourceRange`` of the python bindings as its element type, and
    the bindings load lazily — every other module of the indexer imports
    ``clang.cindex`` inside a function for the same reason.  A local copy of
    the layout of ``CXSourceRange`` would go stale against the bindings.

    WHY a cache: the binding writes ``argtypes`` and ``restype`` on the
    shared library object of the bindings, and that work is the same every
    time.  One call for each process is enough.  Two threads that arrive
    together can run the body two times, but the writes are idempotent, thus
    the second run does no damage.
    """
    from clang import cindex

    class _SourceRangeList(ctypes.Structure):
        _fields_ = [
            ("count", ctypes.c_uint),
            ("ranges", ctypes.POINTER(cindex.SourceRange)),
        ]

    lib = cindex.conf.lib
    try:
        get_all = lib.clang_getAllSkippedRanges
        dispose = lib.clang_disposeSourceRangeList
    except AttributeError:
        log.debug(
            "libclang gives no clang_getAllSkippedRanges — inactive #if "
            "branches stay in files.content and in symbols.source"
        )
        return None

    get_all.argtypes = [cindex.TranslationUnit]
    get_all.restype = ctypes.POINTER(_SourceRangeList)
    dispose.argtypes = [ctypes.POINTER(_SourceRangeList)]
    dispose.restype = None
    return get_all, dispose


def collect_skipped_lines(tu: Any) -> dict[Path, set[int]]:
    """Give the lines that the preprocessor skipped, for each file of *tu*.

    Args:
        tu: A ``TranslationUnit`` of the python bindings of libclang.  The
            parse **must** use ``PARSE_DETAILED_PROCESSING_RECORD``.  Without
            that option the preprocessor keeps no record, and libclang gives
            zero ranges — a silent answer of "no dead code" for every file.
            Both parse sites of the indexer pass the option.

    Returns:
        A map from the resolved path of a file to the set of its skipped
        lines.  A file with no skipped line is not in the map.

        The path is resolved because libclang spells one file more than one
        way — ``./inc/api.h`` at the point of the ``#include`` and an
        absolute path in the extent of a cursor.  A caller must resolve its
        own path also, or the two never agree.

        An empty map is the correct answer for a translation unit that has
        no inactive branch.  It is also what a libclang without these
        symbols gives, thus a caller cannot tell the two apart — and does
        not need to, because both mean "blank out no line".

    A range covers the directive lines at its two ends: it starts on the
    ``#if`` (or ``#elif`` / ``#else``) line and ends on the line of the
    directive that closes the branch.  A conditional directive is always
    alone on its line, thus a caller can blank out every line of the range
    and lose no live code.
    """
    bound = _bind()
    if bound is None:
        return {}
    get_all, dispose = bound

    pointer = get_all(tu)
    if not pointer:
        return {}

    skipped: dict[Path, set[int]] = {}
    try:
        source_range_list = pointer.contents
        for index in range(source_range_list.count):
            source_range = source_range_list.ranges[index]
            start = source_range.start
            end = source_range.end
            if start.file is None:
                continue
            key = Path(start.file.name).resolve()
            skipped.setdefault(key, set()).update(range(start.line, end.line + 1))
    finally:
        # The C API owns this memory until the caller gives it back.  An
        # index run parses thousands of translation units, thus a leak here
        # grows without a limit.
        dispose(pointer)

    return skipped
