"""R11 — stress surface, explicitly NON-GATING (accepted design W7, R11).

W0 audit item R11: the closeout promised a stress suite that never
materialized.  The accepted design implements it as an OPT-IN suite:

- every test carries ``pytest.mark.stress``;
- the default pytest addopts exclude stress (``-m "... and not stress"``),
  so the slice suite and CI gates never run them;
- running them is ``pytest -m stress`` — on demand, when a change touches
  the fan-out surfaces (raw scan, read fan, executor contention).

What is stressed here are the paths this closeout touched or audited:
the raw-mode disk scan (AUD-R10: walks indexed paths until ``limit``
results), the raw read path (AUD-R9: byte-exact) and the shared executor
under concurrent tool calls.  Assertions stay FUNCTIONAL (results correct,
no errors, no cross-thread corruption); wall-clock timings are recorded as
best-effort JSON artifacts under ``.fw-closeout/artifacts/arch/w7/`` for
trend eyeballing — never asserted against a threshold (a timing assertion
would make this suite flaky BY CONSTRUCTION, defeating non-gating).
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.stress

WORKTREE = Path(__file__).resolve().parents[1]
# parents: [0]=tests [1]=worktree [2]=worktrees [3]=.fw-closeout
DEFAULT_ARTIFACTS = (
    Path(__file__).resolve().parents[3] / "artifacts" / "arch" / "w7"
)

STRESS_FILES = 120
STRESS_QUERIES = 30
STRESS_CONCURRENCY = 8


def _record(name: str, payload: dict) -> None:
    """Best-effort timing artifact; a write failure never fails a test."""
    out_dir = Path(os.environ.get("FWCTX_STRESS_ARTIFACTS", str(DEFAULT_ARTIFACTS)))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"stress_{name}.json"
        out.write_text(
            json.dumps({"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **payload},
                       indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _seed_fanout_db(tmp_path: Path, n_files: int) -> tuple[Path, Path]:
    """A hand-seeded index over *n_files* real C files (no libclang needed)."""
    from fw_context_mcp.indexer.db import (
        CURRENT_ROW_FORMAT,
        open_db,
        transaction,
        upsert_build_config,
        upsert_file,
        upsert_project,
    )

    pid, ch = "stress-fanout", "cfgstress0001"
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
    conn = open_db(db_path)
    try:
        with transaction(conn):
            upsert_project(conn, pid, "stress-fanout", str(root))
            upsert_build_config(
                conn, ch, pid, str(root / "compile_commands.json"),
                row_format=CURRENT_ROW_FORMAT,
            )
            for i in range(n_files):
                p = root / f"unit_{i:04d}.c"
                p.write_text(
                    f"/* marker_{i % 10} */\n"
                    f"static int helper_{i}(int v) {{ return v + {i}; }}\n"
                    f"int entry_{i}(void) {{ return helper_{i}(0); }}\n",
                    encoding="utf-8",
                )
                upsert_file(conn, ch, str(p), "c", mtime=os.path.getmtime(p))
    finally:
        conn.close()
    return root, db_path


class TestRawScanFanoutStress:
    def test_repeated_raw_scans_over_wide_index(self, tmp_path: Path):
        from fw_context_mcp.mcp.handlers.search import search_content

        root, _ = _seed_fanout_db(tmp_path, STRESS_FILES)
        timings: list[float] = []
        errors: list[str] = []
        for q in range(STRESS_QUERIES):
            t0 = time.perf_counter()
            results = search_content(
                query=f"marker_{q % 10}", project_root=str(root), mode="raw", limit=5,
            )
            timings.append(time.perf_counter() - t0)
            if any("error" in r for r in results):
                errors.append(str(results[0]))
                continue
            assert len(results) == 5, (
                f"query {q}: raw scan must fill the limit over a wide index, "
                f"got {len(results)}"
            )
        assert not errors, errors[:3]
        _record("raw_scan_fanout", {
            "files": STRESS_FILES, "queries": STRESS_QUERIES,
            "wall_s": round(sum(timings), 3),
            "median_ms": round(sorted(timings)[len(timings) // 2] * 1000, 2),
            "max_ms": round(max(timings) * 1000, 2),
        })


class TestReadFanStress:
    def test_repeated_raw_reads_stay_byte_exact(self, tmp_path: Path):
        from fw_context_mcp.mcp.handlers.source import read_file

        root, _ = _seed_fanout_db(tmp_path, STRESS_FILES)
        t0 = time.perf_counter()
        errors: list[str] = []
        for i in range(0, STRESS_FILES, 2):
            p = root / f"unit_{i:04d}.c"
            result = read_file(file_path=str(p), project_root=str(root), mode="raw")
            if "error" in result:
                errors.append(str(result))
                continue
            disk = p.read_bytes()
            got = result["content"].encode("utf-8", errors="replace")
            if got != disk:
                errors.append(f"{p.name}: raw read is not byte-exact (AUD-R9)")
        assert not errors, errors[:3]
        _record("read_fan", {
            "reads": STRESS_FILES // 2,
            "wall_s": round(time.perf_counter() - t0, 3),
        })


class TestConcurrentExecutorStress:
    def test_concurrent_raw_searches_do_not_corrupt(self, tmp_path: Path):
        from fw_context_mcp.mcp.handlers.search import search_content

        root, _ = _seed_fanout_db(tmp_path, STRESS_FILES)

        def one(q: int) -> int:
            results = search_content(
                query=f"marker_{q % 10}", project_root=str(root), mode="raw", limit=5,
            )
            assert not any("error" in r for r in results), results
            return len(results)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=STRESS_CONCURRENCY) as pool:
            counts = list(pool.map(one, range(STRESS_CONCURRENCY * 10)))
        assert counts and all(c == 5 for c in counts), counts[:10]
        _record("concurrent_executor", {
            "workers": STRESS_CONCURRENCY, "calls": len(counts),
            "wall_s": round(time.perf_counter() - t0, 3),
        })
