"""``_detect_moved_symbols`` must compare two bodies of the same kind.

A move keeps the LLM analysis of a symbol: the step finds the old row by
USR, compares a hash of the body, and updates the old row in place when the
two agree.  A hash that disagrees throws that analysis away and makes the
symbol wait for the model again.

The two bodies come from two files, and only one of them belongs to the
translation unit that runs.  The map of skipped lines covers that unit
alone, thus the old file — a different ``.cpp`` — has no entry in it.  The
old side therefore hashed the raw text of the disk, and the new side hashed
the filtered text.  Every symbol that holds an inactive branch then looked
changed, and no move of such a symbol was ever detected.

The index already holds the filtered body of the old row in
``symbols.source``.  That column is what the old side must use.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

CONFIG_HASH = "ch"

_BODY_ON_DISK = [
    "int host(void)\n",       # 1
    "{\n",                    # 2
    "#ifdef FEATURE_OFF\n",   # 3
    "    dead_call();\n",     # 4
    "#endif\n",               # 5
    "    return 0;\n",        # 6
    "}\n",                    # 7
]
# What the content pass stores for that extent: the dead lines are blank.
_BODY_FILTERED = "int host(void)\n{\n\n\n\n    return 0;\n}\n"
_DEAD_LINES = frozenset({3, 4, 5})


@pytest.fixture
def moved_project(tmp_path: Path):
    """One analyzed symbol in ``a.cpp``, and the same body written to ``b.cpp``.

    Both files hold the identical text, thus the symbol only moved.  The
    stored body of the old row is the filtered one, which is what the
    indexer writes.
    """
    from fw_context_mcp.indexer.db import (
        insert_symbols_batch,
        open_db,
        transaction,
        upsert_build_config,
        upsert_file,
        upsert_project,
    )

    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    for name in ("a.cpp", "b.cpp"):
        (root / "src" / name).write_text("".join(_BODY_ON_DISK), encoding="utf-8")

    conn = open_db(tmp_path / "index.db")
    with transaction(conn):
        upsert_project(conn, "pid", "p", str(root))
        upsert_build_config(conn, CONFIG_HASH, "pid", str(root / "compile_commands.json"))
        old_file_id = upsert_file(conn, CONFIG_HASH, "src/a.cpp", "cpp", mtime=1.0)
        new_file_id = upsert_file(conn, CONFIG_HASH, "src/b.cpp", "cpp", mtime=1.0)
        insert_symbols_batch(
            conn,
            [
                (CONFIG_HASH, old_file_id, "src/a.cpp", "host", "usr-host", "host",
                 "host", "function", 1, 1, 7, 1, "int host()",
                 "", None, 0, 0, "", 0, "", 1, 0.0, _BODY_FILTERED, 0),
            ],
        )
        # Only an analyzed symbol is worth moving — the step skips the rest.
        conn.execute(
            "INSERT INTO llm_analysis(symbol_id, summary, inputs, outputs, model, "
            "content_hash) SELECT id, 'a summary', '', '', 'm', 'h' FROM symbols "
            "WHERE usr = 'usr-host'"
        )

    new_symbol = SimpleNamespace(
        usr="usr-host",
        file=str(root / "src" / "b.cpp"),
        line=1,
        column=1,
        end_line=7,
        signature="int host()",
        qualified_name="host",
        docstring="",
    )
    try:
        yield conn, root, new_symbol, {"src/a.cpp": old_file_id, "src/b.cpp": new_file_id}
    finally:
        conn.close()


def _run(conn, root: Path, symbol, file_ids: dict[str, int]) -> None:
    """Run the step for the unit of ``b.cpp``, which is the only unit here."""
    from fw_context_mcp.indexer.ops import _detect_moved_symbols

    _detect_moved_symbols(
        conn,
        CONFIG_HASH,
        [symbol],
        set(),                       # no old USR matched this batch
        file_ids,
        root,
        # The map covers this unit alone.  `a.cpp` is another unit, thus it
        # has no entry — the condition that produced the defect.
        {Path(symbol.file).resolve(): set(_DEAD_LINES)},
    )


def _row_of_the_symbol(conn) -> dict:
    return conn.execute(
        "SELECT file_path, line FROM symbols WHERE usr = 'usr-host' AND config_hash = ?",
        (CONFIG_HASH,),
    ).fetchone()


class TestAMovedSymbolWithADeadBranch:
    def test_the_move_is_detected(self, moved_project) -> None:
        """The body did not change, thus the old row must follow the symbol."""
        conn, root, symbol, file_ids = moved_project

        _run(conn, root, symbol, file_ids)

        row = _row_of_the_symbol(conn)
        assert row["file_path"] == "src/b.cpp", (
            "the old side hashed the raw disk text and the new side the "
            "filtered text, thus the two never agreed and the move was read "
            "as a modification"
        )

    def test_the_analysis_survives_the_move(self, moved_project) -> None:
        """The whole reason the step exists."""
        conn, root, symbol, file_ids = moved_project

        _run(conn, root, symbol, file_ids)

        kept = conn.execute(
            "SELECT a.summary FROM llm_analysis a JOIN symbols s ON s.id = a.symbol_id "
            "WHERE s.usr = 'usr-host'"
        ).fetchone()
        assert kept is not None and kept["summary"] == "a summary"


class TestARealEditIsStillAModification:
    """The fix must not make every symbol look moved."""

    def test_a_changed_live_line_is_not_a_move(self, moved_project) -> None:
        conn, root, symbol, file_ids = moved_project
        edited = list(_BODY_ON_DISK)
        edited[5] = "    return 1;\n"          # a live line, thus a real change
        (root / "src" / "b.cpp").write_text("".join(edited), encoding="utf-8")

        _run(conn, root, symbol, file_ids)

        row = _row_of_the_symbol(conn)
        assert row["file_path"] == "src/a.cpp", (
            "the body really changed, thus the old row must stay where it is "
            "and the new insert must win"
        )
