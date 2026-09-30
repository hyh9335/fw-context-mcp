"""Docs-vs-behavior conformance oracles — AUD-R8, AUD-R10 (+ O10 sweep lock).

W0 reproduced two doc-vs-behavior drifts and this module keeps them closed:

- AUD-R8 (README-MCP.md staleness section): the doc claimed the query tools
  "re-index up to 5 stale files, with a 30-second timeout".  W0 (R8 log)
  proved the real behavior: per-file on-demand detection against the results,
  a bounded background daemon reindex, stale results returned as-is with
  ``stale``/``stale_warning`` markers — no 5-file reindex cap and no
  30-second reindex timer anywhere in ``stale.py`` (the 30 s constants that
  DO exist are the mtime-scan cache TTL and the bounded ``git ls-files``
  probe; the 200 cap is the manifest header-scan bound).  The fix rewrites
  the doc to the behavior; these tests pin BOTH directions: the doc may not
  claim a cap the code lacks, and the code's real bounds must stay named in
  the doc's neighborhood (a future doc edit that reintroduces the claim
  fails here, and so does a code change that adds the cap silently — the
  behavior half asserts ``_ensure_daemon_running`` carries no count cap).

- AUD-R10 (search.py raw scan): two docstrings claimed the raw disk scan
  reads "``limit * 3`` candidate files at most" / "within a budget of
  ``limit * 3`` candidate files".  W0 (R10 log) proved ``_do_search_raw``
  has NO scanned-file counter: the loop walks the indexed paths until
  ``limit`` results.  The fix conforms the docstrings; a behavior test
  builds a hand-seeded index where the matches sit BEYOND the 3× boundary —
  under the claimed cap the scan would return fewer than ``limit`` results,
  so the pin also guards against reintroducing the cap.

- claim 964 (search_bodies over-fetch): this claim is TRUE (the FTS5 path
  computes ``row_limit = limit if project_only else limit * 3``); the test
  locks the docstring to the SQL so the pair cannot drift apart again (O10
  discipline: docs conform to behavior, never the reverse).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[1]
README_MCP = WORKTREE / "README-MCP.md"
SEARCH_PY = WORKTREE / "src" / "fw_context_mcp" / "mcp" / "handlers" / "search.py"
STALE_PY = WORKTREE / "src" / "fw_context_mcp" / "mcp" / "shared" / "stale.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── AUD-R8 — README staleness section ────────────────────────────────────────


class TestReadmeStalenessTruth:
    """The staleness section must describe the real per-file/background flow."""

    def test_no_false_five_file_thirty_second_claim(self):
        text = _read(README_MCP)
        assert "re-index up to 5 stale files" not in text, (
            "README-MCP.md must not claim a 5-stale-file re-index cap: stale.py "
            "has no such cap (W0 R8 log) — describe the real background-daemon "
            "flow instead"
        )
        assert "with a 30-second timeout" not in text, (
            "README-MCP.md must not claim a 30-second reindex timeout: the only "
            "30 s bounds in stale.py are the mtime-scan cache TTL and the "
            "git ls-files probe timeout (W0 R8 log)"
        )

    def test_section_names_the_real_flow(self):
        text = _read(README_MCP)
        marker = "### Staleness and auto-reindex"
        assert marker in text, "the staleness section must stay present"
        section = text.split(marker, 1)[1].split("\n### ", 1)[0]
        assert "background" in section.lower(), (
            "the section must say the repair runs in the background daemon "
            "(_with_stale_recovery -> _ensure_daemon_running), not inside the query"
        )
        assert "stale_warning" in section, (
            "annotate_stale marks results with stale/stale_warning; the doc "
            "should name the field the LLM actually reads"
        )

    def test_bounds_that_do_exist_are_described(self):
        """The REAL bounds must stay documented (O10: no silent drift either way)."""
        section = _read(README_MCP).split("### Staleness and auto-reindex", 1)[1]
        section = section.split("\n### ", 1)[0]
        assert "_GIT_LS_TIMEOUT_S" in _read(STALE_PY), (
            "the git-probe timeout is a real bound in stale.py"
        )
        assert "30" in section, (
            "the section must name the 30-second bounds that DO exist "
            "(mtime-scan cache TTL, git ls-files probe) without claiming they "
            "budget a reindex"
        )

    def test_daemon_start_carries_no_reindex_count_cap(self):
        """Behavior half: the auto-reindex entry point has no 5-file cap to document."""
        from fw_context_mcp.mcp import background

        sig = inspect.signature(background._ensure_daemon_running)
        for banned in ("max_files", "max_stale", "limit", "cap"):
            assert banned not in sig.parameters, (
                f"_ensure_daemon_running grew a {banned!r} parameter — if this is "
                "a reindex count cap, README-MCP.md's staleness section must be "
                "updated in the same commit (O10)"
            )


# ── AUD-R10 — raw-scan budget claim ──────────────────────────────────────────


class TestRawScanBudgetTruth:
    """The raw-mode docstrings must match _do_search_raw's real scan shape."""

    def test_raw_scan_docstrings_do_not_claim_limit_x3_cap(self):
        src = _read(SEARCH_PY)
        assert not re.search(r"limit \* 3.{0,60}candidate", src), (
            "search.py still claims a 'limit * 3 candidate files' scan budget — "
            "_do_search_raw walks the indexed paths until `limit` results with no "
            "scanned-file counter (W0 R10 log); conform the docstring, do not add "
            "the cap (the cap would change raw-scan recall and must ship with its "
            "own evidence)"
        )
        assert "budget of ``limit * 3``" not in src

    def test_raw_scan_docstring_states_the_real_shape(self):
        src = _read(SEARCH_PY)
        assert "until ``limit`` results" in src, (
            "the raw-scan docstring must state the real loop shape: walk the "
            "indexed paths until `limit` results"
        )

    def test_search_bodies_overfetch_claim_still_matches_the_sql(self):
        """Claim 964 is TRUE — lock it to the SQL so the pair cannot drift (O10)."""
        src = _read(SEARCH_PY)
        assert "row_limit = limit if project_only else limit * 3" in src, (
            "search_bodies' FTS5 path must keep the documented over-fetch shape"
        )
        assert "just\n        ``limit`` for project_only" in src, (
            "the search_bodies docstring must keep stating the project_only "
            "single-fetch special case while the SQL implements it"
        )


class TestRawScanWalksPastThreeXLimit:
    """Behavior pin: matches beyond the 3× boundary are still found.

    7 non-matching files are indexed before the 2 matching ones.  With
    limit=2 the claimed 'limit * 3 candidate files' budget would stop the
    scan after 6 files — zero results.  The real loop (walk until `limit`
    results) reads 9 files and returns both matches.
    """

    def test_matches_beyond_three_x_boundary_are_found(self, tmp_path: Path):
        import os

        from fw_context_mcp.indexer.db import (
            CURRENT_ROW_FORMAT,
            open_db,
            transaction,
            upsert_build_config,
            upsert_file,
            upsert_project,
        )

        pid, ch = "rawscan-budget-pin", "cfgrawscan0001"
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

        db_path = index_dir / pid / "index.db"
        db_path.parent.mkdir(parents=True)
        paths: list[Path] = []
        for i in range(7):
            p = root / f"filler_{i}.c"
            p.write_text(f"int filler_{i}(int v) {{ return v + {i}; }}\n", encoding="utf-8")
            paths.append(p)
        matches: list[Path] = []
        for i in range(2):
            p = root / f"hit_{i}.c"
            p.write_text(f"/* roosevelt marker {i} */\nint hit_{i}(void) {{ return {i}; }}\n", encoding="utf-8")
            matches.append(p)
        conn = open_db(db_path)
        try:
            with transaction(conn):
                upsert_project(conn, pid, "rawscan-budget-pin", str(root))
                upsert_build_config(
                    conn, ch, pid, str(root / "compile_commands.json"),
                    row_format=CURRENT_ROW_FORMAT,
                )
                for p in paths + matches:
                    upsert_file(conn, ch, str(p), "c", mtime=os.path.getmtime(p))
        finally:
            conn.close()

        from fw_context_mcp.mcp.handlers.search import search_content

        results = search_content(query="roosevelt", project_root=str(root), mode="raw", limit=2)
        assert not any("error" in r for r in results), results
        assert not any("warning" in r for r in results), results
        hit_files = {r["file"] for r in results}
        assert hit_files == {str(m) for m in matches}, (
            f"the raw scan must walk past the claimed 3x boundary and return both "
            f"matches, got {sorted(hit_files)}"
        )
