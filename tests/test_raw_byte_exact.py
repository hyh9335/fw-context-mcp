"""AUD-R9 — byte-exact ``read_file`` raw mode (D8).

W0 (R9_crlf.json) reproduced the drift: a 113-byte CRLF file came back as
108 bytes with zero CRs — ``read_file_lines`` opens with Python's default
universal-newline translation, so ``mode="raw"`` returned LF-normalized
text while its docstring promised "the file exactly as on disk".

The accepted fix (D8) is byte-exact raw: the raw path reads the file with
``newline=""`` so the CRLF bytes survive the round trip —
``content.encode("utf-8", errors="replace")`` is identical to the disk
bytes for UTF-8 input.  Scope confined to the ``read_file`` raw path; the
``search.py`` raw scan keeps its text normalization (design note).

The fixture bytes are written EXPLICITLY as a bytes literal (``b"...\r\n..."``)
so the test cannot be silently repaired by a text-mode write on either
platform.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fw_context_mcp.indexer.db import (
    CURRENT_ROW_FORMAT,
    open_db,
    transaction,
    upsert_build_config,
    upsert_file,
    upsert_project,
)

# CRLF bytes, written explicitly — the exact W0 R9 shape (5 CRLFs).
CRLF_SOURCE = (
    b"/* W7-R9 CRLF fixture */\r\n"
    b"#ifndef R9_FIXTURE_H\r\n"
    b"#define R9_FIXTURE_H\r\n"
    b"int r9_fn(int v);\r\n"
    b"#endif\r\n"
)


@pytest.fixture()
def crlf_project(tmp_path: Path) -> Path:
    """A project whose index knows one CRLF file (bytes written explicitly)."""
    pid, ch = "r9-crlf-proj", "cfgr9crlf0001"
    root = tmp_path / "proj"
    (root / ".fw-context").mkdir(parents=True)
    index_dir = tmp_path / "index"
    (root / ".fw-context" / "config.toml").write_text(
        "[project]\n"
        f'id = "{pid}"\n'
        "\n"
        "[index]\n"
        f'db_dir = "{str(index_dir).replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )
    (root / "compile_commands.json").write_text("[]", encoding="utf-8")
    target = root / "crlf_fixture.c"
    target.write_bytes(CRLF_SOURCE)
    # The bytes on disk are the truth under test — assert the write too.
    assert target.read_bytes() == CRLF_SOURCE

    db_path = index_dir / pid / "index.db"
    db_path.parent.mkdir(parents=True)
    import os

    conn = open_db(db_path)
    try:
        with transaction(conn):
            upsert_project(conn, pid, "r9-crlf-proj", str(root))
            upsert_build_config(
                conn, ch, pid, str(root / "compile_commands.json"),
                row_format=CURRENT_ROW_FORMAT,
            )
            # Store the row in the spelling the product's writer contract
            # uses (POSIX on every platform) — the query normalizer spells
            # the lookup the same way (D-N5-1b).
            upsert_file(conn, ch, Path(target).as_posix(), "c", mtime=os.path.getmtime(target))
    finally:
        conn.close()
    return root


class TestRawModeByteExact:
    def test_raw_content_bytes_identical_to_disk(self, crlf_project: Path):
        from fw_context_mcp.mcp.handlers.source import read_file

        target = crlf_project / "crlf_fixture.c"
        result = read_file(file_path=str(target), project_root=str(crlf_project), mode="raw")
        assert "error" not in result, result
        assert result["mode"] == "raw"
        content = result["content"]
        assert isinstance(content, str)
        assert content.encode("utf-8", errors="replace") == CRLF_SOURCE, (
            "raw mode must return byte-identical text: the CRLF bytes must "
            "survive the round trip (AUD-R9, D8)"
        )
        assert content.count("\r\n") == 5, (
            f"all 5 CRLFs must survive, got {content.count(chr(13) + chr(10))}"
        )

    def test_raw_lines_count_survives_crlf(self, crlf_project: Path):
        from fw_context_mcp.mcp.handlers.source import read_file

        target = crlf_project / "crlf_fixture.c"
        result = read_file(file_path=str(target), project_root=str(crlf_project), mode="raw")
        assert result["lines"] == 5, (
            "the CRLF file has 5 lines; the count must not change with EOL "
            "preservation"
        )

    def test_active_mode_stays_filtered_not_byte_exact(self, crlf_project: Path):
        """Guard the SCOPE: only the raw path goes byte-exact (design D8)."""
        from fw_context_mcp.mcp.handlers.source import read_file

        target = crlf_project / "crlf_fixture.c"
        result = read_file(file_path=str(target), project_root=str(crlf_project), mode="active")
        # No index content stored for this hand-seeded row → the active mode
        # serves the no-content warning; it must NOT silently return raw text.
        assert "error" not in result, result
        assert result.get("all_lines_inactive") is not True
        stored = result.get("content", "")
        assert "\r\n" not in stored, (
            "active mode is the ifdef-filtered stored view — it must keep its "
            "text normalization (only the raw path is byte-exact)"
        )
