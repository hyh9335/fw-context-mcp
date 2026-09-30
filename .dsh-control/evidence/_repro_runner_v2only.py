#!/usr/bin/env python
"""Repro: run runner.run() in DEFAULT v2-only mode against keep_only fixture.

Purpose: verify whether the default (compat OFF) path actually completes an
index without crashing on conn=None usage outside the _legacy_compat guard.
"""
from __future__ import annotations
import os
import sys
import shutil
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# CRITICAL: clear every env override so we hit SHIPPED defaults.
for key in ("FW_CONTEXT_LEGACY_COMPATIBILITY_MODE",
            "FW_CONTEXT_V2_WRITE_AUTHORITY",
            "FW_CONTEXT_SHADOW_V2"):
    os.environ.pop(key, None)

FIX = ROOT / "tests" / "fixtures" / "keep_only"

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    work = td / "proj"
    work.mkdir()
    # Copy fixture into the temp project so relative paths resolve.
    shutil.copy(FIX / "main.c", work / "main.c")
    shutil.copy(FIX / "compile_commands.json", work / "compile_commands.json")
    (work / "build").mkdir(exist_ok=True)

    db_dir = td / "db"
    db_dir.mkdir()
    db_path = db_dir / "index.db"

    # Reset caches so the cleared env is honored.
    from fw_context_mcp.indexer._unit_processor import (
        _reset_legacy_compat_cache, _reset_v2_flag_cache,
        _reset_v2_write_authority_cache, _legacy_compat_on)
    _reset_legacy_compat_cache()
    _reset_v2_flag_cache()
    _reset_v2_write_authority_cache()

    print(f"[repro] _legacy_compat_on() = {_legacy_compat_on()} (must be False)")

    from fw_context_mcp.indexer import runner

    print(f"[repro] calling runner.run() on {work}")
    try:
        cfg_hash = runner.run(
            compile_commands=work / "compile_commands.json",
            db_path=db_path,
            project_root=work,
            project_name="repro_keep_only",
            project_id="0" * 32,
            index_refs=False,
            index_embeddings=False,
            analyze_symbols=False,
            analyze_overrides=False,
        )
        print(f"[repro] runner.run() RETURNED config_hash={cfg_hash[:12]}")
        # Did a legacy index.db get created?
        legacy_exists = db_path.exists()
        print(f"[repro] legacy index.db exists at {db_path}: {legacy_exists}")
        print("[repro] RESULT=PASS (no crash)")
    except Exception as exc:
        print(f"[repro] runner.run() RAISED: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print("[repro] RESULT=CRASH")
