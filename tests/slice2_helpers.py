"""slice2_helpers — fixture builders for Phase-4 Slice-2 tests.

Extends :mod:`slice1_helpers` with REAL git worktree fixtures (clone /
dirty / restore), world-context construction over the production capture
path, and v2-row query helpers.  Every identity capture goes through
``store_v2.identity_capture`` — no toy calculator.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from slice1_helpers import make_project  # noqa: E402,F401

from fw_context_mcp.store_v2.identity_capture import (  # noqa: E402
    capture_repository_identity,
    capture_snapshot,
)


def git(*args: str, cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stderr[-300:]}")
    return proc.stdout


def make_git_project(tmp_path: Path, *, feature: bool = True,
                     util_version: int = 2, commit: bool = True) -> dict:
    """A real C project under git (init + config + initial commit)."""
    data = make_project(tmp_path, feature=feature, util_version=util_version)
    proj = data["proj"]
    # exclude-fw-context patterns so the v2 store dir is not part of the
    # snapshot: capture excludes .fw-context/ and index dirs anyway.
    git("init", "-q", cwd=proj)
    git("config", "user.email", "slice2@test", cwd=proj)
    git("config", "user.name", "slice2", cwd=proj)
    # conversion-free fixture: committed blobs are the exact worktree bytes,
    # so identity capture is stable under any ambient line-ending config
    git("config", "core.autocrlf", "false", cwd=proj)
    git("config", "core.filemode", "false", cwd=proj)
    git("add", "-A", cwd=proj)
    if commit:
        git("commit", "-q", "-m", "slice2 fixture", cwd=proj)
    data["git_root"] = proj
    return data


def clone_project(src: Path, dst: Path) -> Path:
    """Physical copy (same bytes, different absolute root)."""
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "index",
                                                            "__pycache__"))
    # .git IS part of the snapshot-independent clone: copy it too, quietly.
    if (src / ".git").exists():
        shutil.copytree(src / ".git", dst / ".git", dirs_exist_ok=True)
    return dst


def dirty(proj: Path, edit: str, content: str) -> None:
    """Apply one dirty tracked change (content-based identity input).

    The mtime is pushed forward past the indexer's Tier-1 tolerance so the
    change is never mistaken for an untouched file (a real edit between two
    index runs is always temporally separated)."""
    import os
    import time
    p = proj / edit
    p.write_text(content, encoding="utf-8")
    future = time.time() + 30.0
    os.utime(p, (future, future))


def restore(proj: Path, edit: str, content: str) -> None:
    dirty(proj, edit, content)


def build_world_context(proj: Path, *, build_system: str = "cmake",
                        project_id: str = "pid-fixture"):
    from fw_context_mcp.store_v2.worlds import build_world_context
    return build_world_context(project_root=proj, build_system=build_system,
                               project_id=project_id)


def v2_rows(v2_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Read-only row fetcher over the v2 store."""
    import sqlite3
    conn = sqlite3.connect(f"file:{v2_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def world_table(v2_path: Path) -> list[dict]:
    return v2_rows(v2_path, "SELECT * FROM snapshot_builds ORDER BY world_id")


def binding_table(v2_path: Path) -> list[dict]:
    return v2_rows(v2_path, "SELECT * FROM tu_bindings ORDER BY binding_id")


def state_table(v2_path: Path) -> list[dict]:
    return v2_rows(v2_path, "SELECT * FROM semantic_states ORDER BY state_id")


def metric_rows(v2_path: Path) -> dict:
    out: dict = {}
    for r in v2_rows(v2_path, "SELECT * FROM slice2_run_metrics"):
        out.setdefault(r["run_id"], {})[r["metric"]] = r["value"]
    return out


def counts(v2_path: Path) -> dict:
    """One-glance store shape: worlds / bindings / states."""
    return {
        "worlds": len(world_table(v2_path)),
        "bindings": len(binding_table(v2_path)),
        "states": len(state_table(v2_path)),
    }
