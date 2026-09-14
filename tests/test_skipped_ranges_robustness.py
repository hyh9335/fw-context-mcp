"""``collect_skipped_lines`` must degrade, and must never blank a wrong line.

The module promises that a libclang which cannot answer costs an index run
nothing: the answer is then empty, and the stored text keeps every branch.
That promise covered the two symbol lookups alone, thus a failure of the
call itself still stopped a run that takes hours on a large project.

The second class here covers a range whose two ends lie in different files.
An unterminated ``#if`` at the end of a header makes clang report one, and
the line numbers of the end then belong to another file.  Blanking the lines
between them removes live code from the header, and nothing shows it.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from fw_context_mcp.indexer.skipped_ranges import collect_skipped_lines


def _location(path: str | None, line: int) -> SimpleNamespace:
    """One end of a range: the file it names, and the line in it."""
    return SimpleNamespace(file=None if path is None else SimpleNamespace(name=path), line=line)


def _fake_bind(monkeypatch, ranges: list[tuple], *, reads: dict | None = None):
    """Make the module answer from *ranges* instead of from libclang.

    Each entry is ``(start_path, start_line, end_path, end_line)``.  The
    real structure is a C array behind a pointer, and the code reads it by
    attribute, thus a namespace stands in for it exactly.
    """
    from fw_context_mcp.indexer import skipped_ranges as mod

    items = [
        SimpleNamespace(start=_location(sp, sl), end=_location(ep, el))
        for sp, sl, ep, el in ranges
    ]
    pointer = SimpleNamespace(contents=SimpleNamespace(count=len(items), ranges=items))
    monkeypatch.setattr(mod, "_bind", lambda: (lambda tu: pointer, lambda p: None))
    monkeypatch.setattr(mod, "_count_reads", lambda tu: reads or {})


class TestARangeThatSpansTwoFiles:
    """Only the file of the START is known, thus only it may lose lines."""

    def test_the_range_is_dropped(self, monkeypatch, tmp_path: Path) -> None:
        """An unterminated #if reports an end in the file that included it."""
        header = str(tmp_path / "api.h")
        main = str(tmp_path / "main.c")
        _fake_bind(monkeypatch, [(header, 3, main, 90)])

        skipped = collect_skipped_lines(object())

        assert skipped == {}, (
            "the end line counts lines of another file, thus the range says "
            "nothing about the header and must blank no line of it"
        )

    def test_a_normal_range_still_works(self, monkeypatch, tmp_path: Path) -> None:
        """The guard must not drop the ordinary case."""
        header = str(tmp_path / "api.h")
        _fake_bind(monkeypatch, [(header, 3, header, 5)])

        skipped = collect_skipped_lines(object())

        assert skipped == {Path(header).resolve(): {3, 4, 5}}

    def test_a_range_with_no_start_file_is_dropped(self, monkeypatch) -> None:
        """A location inside a macro expansion buffer names no file."""
        _fake_bind(monkeypatch, [(None, 3, None, 5)])

        assert collect_skipped_lines(object()) == {}


class TestTheModuleDegrades:
    """A libclang that cannot answer must not stop the run."""

    def test_a_failure_of_the_call_gives_an_empty_answer(self, monkeypatch) -> None:
        """`get_all(tu)` raises when *tu* is not the type it declares."""
        from fw_context_mcp.indexer import skipped_ranges as mod

        def _raise(tu):
            raise ctypes.ArgumentError("argument 1: wrong type")

        monkeypatch.setattr(mod, "_bind", lambda: (_raise, lambda p: None))

        assert collect_skipped_lines(object()) == {}, (
            "the module promises that a libclang which cannot answer costs "
            "the run nothing, thus this must not reach the caller"
        )

    def test_a_failure_of_the_layout_gives_an_empty_answer(self, monkeypatch) -> None:
        """A divergent CXSourceRangeList raises when the code reads it."""
        from fw_context_mcp.indexer import skipped_ranges as mod

        class _Bad:
            def __bool__(self) -> bool:
                return True

            @property
            def contents(self):
                raise ValueError("layout does not match")

        disposed: list = []
        monkeypatch.setattr(
            mod, "_bind", lambda: (lambda tu: _Bad(), lambda p: disposed.append(p))
        )

        assert collect_skipped_lines(object()) == {}
        assert disposed, "the C API owns the memory, thus it must be given back"

    def test_a_real_error_of_ours_is_not_swallowed(self, monkeypatch) -> None:
        """The guard must cover the binding, not every defect behind it."""
        from fw_context_mcp.indexer import skipped_ranges as mod

        def _raise(tu):
            raise KeyboardInterrupt

        monkeypatch.setattr(mod, "_bind", lambda: (_raise, lambda p: None))

        with pytest.raises(KeyboardInterrupt):
            collect_skipped_lines(object())


class TestTheMapIsComputedOncePerUnit:
    """``store_symbols_for_unit`` already has the map that the content pass needs."""

    def test_a_given_map_is_used_as_it_is(self, monkeypatch, tmp_path: Path) -> None:
        """The parameter must replace the call, not only add to it."""
        from fw_context_mcp.indexer import ops

        calls: list = []
        monkeypatch.setattr(
            ops, "collect_skipped_lines", lambda tu: calls.append(tu) or {}
        )
        # A parse of a real unit is out of scope here: the call must not
        # happen at all, thus the function raises before it needs one.
        with pytest.raises(AttributeError):
            ops._build_filtered_file_content(
                None, SimpleNamespace(file=tmp_path / "main.c"), "ch", tmp_path,
                existing_tu=object(), skipped={Path("/x"): {1}},
            )

        assert calls == [], (
            "the caller handed over the map of this unit, thus collecting it "
            "again walks every range and resolves every path twice"
        )
