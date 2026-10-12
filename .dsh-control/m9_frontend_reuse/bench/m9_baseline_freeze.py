"""M9 BASELINE_FREEZE — end-to-end frontend wall-clock baselines.

M8 measured PARSE COST (per-TU) and CERTIFICATE SAFETY (L1->L2 predictor).
M9 must measure END-TO-END WALL-CLOCK for the multi-build index path so
later prototype work can measure real frontend-skip savings against a
frozen threshold.

This script measures the CURRENT architecture (no cross-build reuse):
for each (build, TU) pair, libclang parses the TU and the script extracts
symbols by walking the AST — the same work fw-context's frontend does
upstream of M6. NO M6 materialization (no DB writes, no fact
canonicalization) — we want frontend cost only.

Two workloads, matching the M8 certificate_savings.py scenarios:

  WORKLOAD 1 — NEGATIVE CONTROL (global config, FreeRTOS model):
    5 builds, each changes FreeRTOSConfig.h (seen by every TU).
    5 sources x 5 builds = 25 parses, 0 certificate skips.
    Certificate saves 0% here (every L1 unique). M9 must NOT regress
    this workload's wall-clock by more than 5% once the certificate
    gate is added (the gate's overhead must be cheap).

  WORKLOAD 2 — POSITIVE CONTROL (per-file config, realistic multi-variant):
    5 builds, each changes -D for ONE source; the other 4 sources are
    compiled identically across builds. 5 sources x 5 builds = 25 parses
    WITHOUT reuse; the certificate WOULD skip 16/25 (64%) here.
    M9 must beat 30% real wall-clock savings on this workload after
    cert overhead to be production-worthy.

For each workload this script records:
  - total wall-clock (parse + extract, summed over all builds/TUs)
  - per-build wall-clock
  - per-TU parse time (mean/min/max)
  - per-TU extract time (mean/min/max)
  - total parses
  - total symbols extracted
  - the certificate's theoretical skip count (for reference; NOT applied
    to the measured baseline — the baseline is the no-reuse cost)

Output: .dsh-control/m9_frontend_reuse/evidence/baseline_freeze.json
        with `head` (git SHA) and `frozen_at` (UTC timestamp).

NO production code is modified. Standalone measurement only, mirroring
the M8 bench scripts' direct libclang approach.
"""
from __future__ import annotations
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

import clang.cindex as cx  # noqa: E402

FREERTOS_DIR = REPO / ".dsh-control" / "m6_research" / "corpus" / "FreeRTOS-Kernel"
EVIDENCE_DIR = REPO / ".dsh-control" / "m9_frontend_reuse" / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

CLANG_TARGET = "--target=x86_64-pc-windows-msvc"

# Same FreeRTOSConfig.h template as M8 (byte-identical, so L1/L2 keys match).
BASE_CONFIG = """#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H
#define configUSE_PREEMPTION {preempt}
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configUSE_TICKLESS_IDLE 0
#define configCPU_CLOCK_HZ 20000000UL
#define configTICK_RATE_HZ 1000
#define configMAX_PRIORITIES 5
#define configMINIMAL_STACK_SIZE 128
#define configTOTAL_HEAP_SIZE 10240
#define configMAX_TASK_NAME_LEN 16
#define configUSE_16_BIT_TICKS 0
#define configIDLE_SHOULD_YIELD 1
#define configUSE_TASK_NOTIFICATIONS {task_notif}
#define configUSE_MUTEXES {mutexes}
#define configUSE_RECURSIVE_MUTEXES {rec_mutexes}
#define configUSE_COUNTING_SEMAPHORES {counting_sem}
#define configUSE_QUEUE_SETS 0
#define configUSE_NEWLIB_REENTRANT 0
#define configENABLE_BACKWARD_COMPATIBILITY 0
#define configSUPPORT_STATIC_ALLOCATION {static_alloc}
#define configSUPPORT_DYNAMIC_ALLOCATION {dynamic_alloc}
#define configSTACK_DEPTH_TYPE uint16_t
#define configMESSAGE_BUFFER_LENGTH_TYPE size_t
#endif
"""

# 5-build global-config matrix (matches M8 baseline_parse / extended_benchmark).
BUILDS_GLOBAL = {
    "BASE":         {"preempt": "1", "task_notif": "1", "mutexes": "1", "rec_mutexes": "1", "counting_sem": "0", "static_alloc": "1", "dynamic_alloc": "1"},
    "NO_PREEMPT":   {"preempt": "0", "task_notif": "1", "mutexes": "1", "rec_mutexes": "1", "counting_sem": "0", "static_alloc": "1", "dynamic_alloc": "1"},
    "NO_MUTEXES":   {"preempt": "1", "task_notif": "1", "mutexes": "0", "rec_mutexes": "0", "counting_sem": "0", "static_alloc": "1", "dynamic_alloc": "1"},
    "STATIC_ONLY":  {"preempt": "1", "task_notif": "1", "mutexes": "1", "rec_mutexes": "1", "counting_sem": "0", "static_alloc": "1", "dynamic_alloc": "0"},
    "FULL_FEATURE": {"preempt": "1", "task_notif": "1", "mutexes": "1", "rec_mutexes": "1", "counting_sem": "1", "static_alloc": "1", "dynamic_alloc": "1"},
}

# Per-file-config matrix (matches M8 certificate_savings.py Scenario B).
# Shared base config + one source gets an extra -D per build.
BASE_PARAMS = {"preempt": "1", "task_notif": "1", "mutexes": "1", "rec_mutexes": "1", "counting_sem": "0", "static_alloc": "1", "dynamic_alloc": "1"}
BUILDS_PERFILE = {
    "BASE":     None,
    "V_list":   {"list.c":        ["-DEXTRA_LIST_FLAG=1"]},
    "V_queue":  {"queue.c":       ["-DEXTRA_QUEUE_FLAG=1"]},
    "V_tasks":  {"tasks.c":       ["-DEXTRA_TASKS_FLAG=1"]},
    "V_timers": {"timers.c":      ["-DEXTRA_TIMERS_FLAG=1"]},
}

SOURCES = ["list.c", "queue.c", "tasks.c", "timers.c", "event_groups.c"]


def _git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO), stderr=subprocess.DEVNULL
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _write_config_header(params: dict, cfg_dir: Path) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    h = cfg_dir / "FreeRTOSConfig.h"
    h.write_text(BASE_CONFIG.format(**params), encoding="utf-8")
    return h


def _build_clang_args(source: str, include_dirs: list[str], extra: list[str] | None = None) -> list[str]:
    args = [CLANG_TARGET]
    args.append("-xc++" if source.endswith((".cpp", ".cc", ".cxx")) else "-xc")
    for inc in include_dirs:
        args.append(f"-I{inc}")
    if extra:
        args.extend(extra)
    return args


def _parse(source: Path, args: list[str]) -> tuple[cx.TranslationUnit, float] | None:
    """Frontend parse step — mirrors fw-context's tolerance (catch TULoadError)."""
    idx = cx.Index.create()
    t0 = time.monotonic()
    try:
        tu = idx.parse(
            str(source), args=args,
            options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
        )
    except cx.TranslationUnitLoadError:
        return None
    return tu, time.monotonic() - t0


def _extract_symbols(tu: cx.TranslationUnit) -> tuple[list[dict], float, int]:
    """Frontend extract step — walk AST and collect symbol records.

    This is the work fw-context's frontend does upstream of M6: it turns
    the clang AST into symbol records (kind, spelling, usr, location). NO
    M6 materialization (no DB writes, no fact canonicalization). Returns
    (records, extract_seconds, total_cursor_walk).
    """
    records: list[dict] = []
    t0 = time.monotonic()
    cursors = 0
    for cur in tu.cursor.walk_preorder():
        cursors += 1
        kind = str(cur.kind).replace("CursorKind.", "")
        if kind in ("TRANSLATION_UNIT", "MACRO_DEFINITION", "MACRO_INSTANTIATION",
                    "INCLUSION_DIRECTIVE"):
            # The real frontend skips these for symbol records (they are
            # tracked separately as files / macros). We still pay the
            # walk cost; we just don't emit a record.
            continue
        loc = cur.location
        rec = {
            "kind": kind,
            "spelling": cur.spelling or "",
            "usr": cur.get_usr() or "",
            "file": loc.file.name if loc and loc.file else "",
            "line": loc.line if loc else 0,
        }
        records.append(rec)
    return records, time.monotonic() - t0, cursors


def _l1_key(source_bytes: bytes, config_bytes: bytes, extra_flags: list[str] | None = None) -> str:
    """Certificate L1 leg: source bytes + config bytes + per-source extra flags.

    Same construction as M8 certificate_savings.py — so L1 keys are
    byte-compatible with M8 and the theoretical skip count reproduces
    the M8 64% number exactly.
    """
    parts = [source_bytes, b"\x1f", config_bytes]
    if extra_flags:
        parts.append(b"\x1f")
        parts.append("\x1f".join(extra_flags).encode())
    return hashlib.sha256(b"".join(parts)).hexdigest()[:16]


def _warmup() -> None:
    """One throwaway parse per source to load the libclang shared lib."""
    tmpcfg = Path(tempfile.mkdtemp(prefix="m9_warmup_"))
    _write_config_header(BASE_PARAMS, tmpcfg)
    inc = [str(FREERTOS_DIR / "include"), str(tmpcfg)]
    for src in SOURCES:
        try:
            _parse(FREERTOS_DIR / src, _build_clang_args(src, inc))
        except Exception:
            pass
    import shutil
    shutil.rmtree(tmpcfg, ignore_errors=True)


def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean_s": 0.0, "min_s": 0.0, "max_s": 0.0, "median_s": 0.0}
    return {
        "n": len(values),
        "mean_s": round(statistics.mean(values), 6),
        "min_s": round(min(values), 6),
        "max_s": round(max(values), 6),
        "median_s": round(statistics.median(values), 6),
        "sum_s": round(sum(values), 6),
    }


def run_workload_global() -> dict:
    """Workload 1 — NEGATIVE CONTROL: global config (FreeRTOS model).

    Every build changes FreeRTOSConfig.h, seen by all 5 sources.
    Current architecture: 5 builds x 5 sources = 25 full frontend runs,
    0 reuse. Certificate WOULD skip 0/25 (0%) here — every L1 unique.
    """
    print("\n=== WORKLOAD 1: GLOBAL config (negative control) ===")
    include_base = str(FREERTOS_DIR / "include")
    per_build_wall: dict[str, float] = {}
    per_tu_parse: list[float] = []
    per_tu_extract: list[float] = []
    total_symbols = 0
    total_cursors = 0
    total_parses = 0
    theoretical_skips = 0
    l1_seen: set[str] = set()
    per_build_detail: dict[str, dict] = {}

    for bname, params in BUILDS_GLOBAL.items():
        tmpcfg = Path(tempfile.mkdtemp(prefix=f"m9g_{bname}_"))
        cfg_path = _write_config_header(params, tmpcfg)
        cfg_bytes = cfg_path.read_bytes()
        inc_dirs = [include_base, str(tmpcfg)]
        build_t0 = time.monotonic()
        build_parses = 0
        build_symbols = 0
        for src in SOURCES:
            source = FREERTOS_DIR / src
            src_bytes = source.read_bytes()
            # Theoretical certificate check (NOT applied — baseline is no-skip).
            l1 = _l1_key(src_bytes, cfg_bytes)
            if l1 in l1_seen:
                theoretical_skips += 1
            else:
                l1_seen.add(l1)
            parsed = _parse(source, _build_clang_args(src, inc_dirs))
            if parsed is None:
                print(f"  {bname:12s} {src:16s} PARSE FAIL")
                continue
            tu, parse_s = parsed
            records, extract_s, cursors = _extract_symbols(tu)
            per_tu_parse.append(parse_s)
            per_tu_extract.append(extract_s)
            total_parses += 1
            build_parses += 1
            total_symbols += len(records)
            build_symbols += len(records)
            total_cursors += cursors
            print(f"  {bname:12s} {src:16s} parse={parse_s:.4f}s extract={extract_s:.4f}s "
                  f"symbols={len(records):>5} cursors={cursors:>5}", flush=True)
        build_wall = time.monotonic() - build_t0
        per_build_wall[bname] = round(build_wall, 6)
        per_build_detail[bname] = {
            "wall_s": round(build_wall, 6),
            "parses": build_parses,
            "symbols": build_symbols,
        }
        import shutil
        shutil.rmtree(tmpcfg, ignore_errors=True)

    total_wall = sum(per_build_wall.values())
    result = {
        "workload": "global_config_negative_control",
        "model": "FreeRTOS global config (FreeRTOSConfig.h seen by all TUs)",
        "builds": len(BUILDS_GLOBAL),
        "sources": len(SOURCES),
        "total_parses": total_parses,
        "total_symbols_extracted": total_symbols,
        "total_cursors_walked": total_cursors,
        "total_wall_s": round(total_wall, 6),
        "per_build_wall_s": per_build_wall,
        "per_build_detail": per_build_detail,
        "per_tu_parse_s": _stats(per_tu_parse),
        "per_tu_extract_s": _stats(per_tu_extract),
        "certificate_theoretical_skips": theoretical_skips,
        "certificate_theoretical_savings_pct": round(100 * theoretical_skips / (len(BUILDS_GLOBAL) * len(SOURCES)), 1),
        "applied_reuse": False,
        "note": ("Baseline = current architecture (no cross-build reuse). "
                 "The certificate theoretical skip is recorded for reference; "
                 "it is NOT applied to the measured wall-clock."),
    }
    print(f"  -> total_wall={total_wall:.4f}s parses={total_parses} "
          f"symbols={total_symbols} theoretical_skips={theoretical_skips}/"
          f"{len(BUILDS_GLOBAL)*len(SOURCES)} ({result['certificate_theoretical_savings_pct']}%)")
    return result


def run_workload_perfile() -> dict:
    """Workload 2 — POSITIVE CONTROL: per-file config (realistic multi-variant).

    Each build changes -D for ONE source; the other 4 sources are compiled
    identically across builds. Current architecture: 5 builds x 5 sources =
    25 full frontend runs, 0 reuse. Certificate WOULD skip 16/25 (64%) here.
    """
    print("\n=== WORKLOAD 2: PER-FILE config (positive control) ===")
    include_base = str(FREERTOS_DIR / "include")
    per_build_wall: dict[str, float] = {}
    per_tu_parse: list[float] = []
    per_tu_extract: list[float] = []
    total_symbols = 0
    total_cursors = 0
    total_parses = 0
    theoretical_skips = 0
    l1_seen: set[str] = set()
    per_build_detail: dict[str, dict] = {}

    for bname, variations in BUILDS_PERFILE.items():
        tmpcfg = Path(tempfile.mkdtemp(prefix=f"m9p_{bname}_"))
        cfg_path = _write_config_header(BASE_PARAMS, tmpcfg)
        cfg_bytes = cfg_path.read_bytes()
        inc_dirs = [include_base, str(tmpcfg)]
        build_t0 = time.monotonic()
        build_parses = 0
        build_symbols = 0
        for src in SOURCES:
            source = FREERTOS_DIR / src
            src_bytes = source.read_bytes()
            extra = (variations or {}).get(src, [])
            l1 = _l1_key(src_bytes, cfg_bytes, extra)
            if l1 in l1_seen:
                theoretical_skips += 1
            else:
                l1_seen.add(l1)
            parsed = _parse(source, _build_clang_args(src, inc_dirs, extra))
            if parsed is None:
                print(f"  {bname:12s} {src:16s} PARSE FAIL")
                continue
            tu, parse_s = parsed
            records, extract_s, cursors = _extract_symbols(tu)
            per_tu_parse.append(parse_s)
            per_tu_extract.append(extract_s)
            total_parses += 1
            build_parses += 1
            total_symbols += len(records)
            build_symbols += len(records)
            total_cursors += cursors
            print(f"  {bname:12s} {src:16s} parse={parse_s:.4f}s extract={extract_s:.4f}s "
                  f"symbols={len(records):>5} cursors={cursors:>5}", flush=True)
        build_wall = time.monotonic() - build_t0
        per_build_wall[bname] = round(build_wall, 6)
        per_build_detail[bname] = {
            "wall_s": round(build_wall, 6),
            "parses": build_parses,
            "symbols": build_symbols,
        }
        import shutil
        shutil.rmtree(tmpcfg, ignore_errors=True)

    total_wall = sum(per_build_wall.values())
    result = {
        "workload": "per_file_config_positive_control",
        "model": "Per-file -D variation (realistic multi-variant: one source differs per build)",
        "builds": len(BUILDS_PERFILE),
        "sources": len(SOURCES),
        "total_parses": total_parses,
        "total_symbols_extracted": total_symbols,
        "total_cursors_walked": total_cursors,
        "total_wall_s": round(total_wall, 6),
        "per_build_wall_s": per_build_wall,
        "per_build_detail": per_build_detail,
        "per_tu_parse_s": _stats(per_tu_parse),
        "per_tu_extract_s": _stats(per_tu_extract),
        "certificate_theoretical_skips": theoretical_skips,
        "certificate_theoretical_savings_pct": round(100 * theoretical_skips / (len(BUILDS_PERFILE) * len(SOURCES)), 1),
        "applied_reuse": False,
        "note": ("Baseline = current architecture (no cross-build reuse). "
                 "The certificate WOULD skip 16/25 (64%) here; that skip is NOT "
                 "applied to the measured wall-clock — this is the no-reuse cost "
                 "the M9 prototype must beat."),
    }
    print(f"  -> total_wall={total_wall:.4f}s parses={total_parses} "
          f"symbols={total_symbols} theoretical_skips={theoretical_skips}/"
          f"{len(BUILDS_PERFILE)*len(SOURCES)} ({result['certificate_theoretical_savings_pct']}%)")
    return result


def run() -> dict:
    print("M9 BASELINE_FREEZE — end-to-end frontend wall-clock baselines")
    print(f"  repo: {REPO}")
    print(f"  head: {_git_head()}")
    _warmup()
    w1 = run_workload_global()
    w2 = run_workload_perfile()

    results = {
        "head": _git_head(),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "fixture": {
            "corpus": "FreeRTOS-Kernel",
            "pinned_commit": "8be86d4a24fd4091f8f4192018423ab590f408db",
            "sources": SOURCES,
            "builds_global": list(BUILDS_GLOBAL.keys()),
            "builds_perfile": list(BUILDS_PERFILE.keys()),
            "tooling": "libclang 18.1.1 (python wheel) — direct idx.parse + walk_preorder",
            "frontend_only": True,
            "m6_materialization": False,
            "applied_reuse": False,
        },
        "workload_1_global_config_negative_control": w1,
        "workload_2_per_file_config_positive_control": w2,
        "summary": {
            "baseline_mean_parse_time_per_tu_s": round(
                (w1["per_tu_parse_s"]["sum_s"] + w2["per_tu_parse_s"]["sum_s"]) /
                max(w1["total_parses"] + w2["total_parses"], 1), 6),
            "baseline_positive_control_wall_clock_s": w2["total_wall_s"],
            "baseline_negative_control_wall_clock_s": w1["total_wall_s"],
            "negative_control_certificate_savings_pct": w1["certificate_theoretical_savings_pct"],
            "positive_control_certificate_savings_pct": w2["certificate_theoretical_savings_pct"],
        },
    }
    out = EVIDENCE_DIR / "baseline_freeze.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\n  M9 baseline_freeze written to {out}")
    print(f"\n  === M9 BASELINE SUMMARY ===")
    for k, v in results["summary"].items():
        print(f"  {k}: {v}")
    return results


if __name__ == "__main__":
    run()
