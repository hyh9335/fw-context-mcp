"""K1 independent channels (FR-8): raw-disk grep, native clang -E, build manifest diff.

These channels are OUTSIDE the substrate on purpose. Their output is captured
the same way as substrate output (raw text + sha256) and minted as FactRecords
with evidence_surface independent_grep / independent_clang_e /
build_manifest_diff.

WSL clang is UNAVAILABLE in this environment (recorded: WSL E_ACCESSDENIED);
the preprocessor channel uses the native LLVM clang driver
(C:\\Program Files\\LLVM\\bin\\clang.exe) — an independent instrument of the
same recipe (clang -E). K3 records the channel substitution.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

CLANG_CANDIDATES = (
    r"C:\Program Files\LLVM\bin\clang.exe",
    r"C:\Program Files\LLVM\bin\clang-cl.exe",
)


class ChannelError(RuntimeError):
    pass


# ── independent_grep ─────────────────────────────────────────────────────────
def grep(
    pattern: str,
    root: Path,
    *,
    glob: str | None = None,
    regex: bool = False,
    max_matches: int = 200,
) -> dict:
    """Raw-disk text search OUTSIDE the substrate (FR-8). Returns matches with
    file:line and the raw line text. Pure python re — no substrate involved."""
    root = Path(root)
    rx = re.compile(pattern) if regex else re.compile(re.escape(pattern))
    matches: list[dict] = []
    files_scanned = 0
    for p in sorted(root.rglob(glob or "*")):
        if not p.is_file():
            continue
        files_scanned += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                matches.append({"file": p.relative_to(root).as_posix(), "line": i, "text": line})
                if len(matches) >= max_matches:
                    return {
                        "matches": matches,
                        "truncated": True,
                        "files_scanned": files_scanned,
                    }
    return {"matches": matches, "truncated": False, "files_scanned": files_scanned}


# ── independent_clang_e ──────────────────────────────────────────────────────
def _find_clang() -> Path:
    for c in CLANG_CANDIDATES:
        if Path(c).is_file():
            return Path(c)
    raise ChannelError("no clang driver found for independent_clang_e channel")


def clang_e(source: Path, include_dirs: list[Path], defines: list[str], workdir: Path) -> dict:
    """clang -E preprocessor instrument (FR-8): emits the translation unit
    AFTER preprocessing — the authoritative active-branch view, independent of
    any indexer. Returns preprocessed text + the exact command."""
    clang = _find_clang()
    cmd = [str(clang), "-E", str(Path(source).resolve())]
    for inc in include_dirs:
        cmd += ["-I", str(Path(inc).resolve())]
    for d in defines:
        cmd.append(d)
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChannelError(f"clang -E failed: {exc}") from exc
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "wsl_unavailable_note": "WSL blocked in this environment; native clang driver used (recorded channel substitution)",
    }


def pp_active_branches(clang_e_result: dict, markers: dict[str, tuple[str, str]]) -> dict:
    """From a clang -E result, decide which marked regions SURVIVE preprocessing.

    *markers* maps region_name -> (active_marker, inactive_marker): a region
    counts ACTIVE iff its active_marker text appears in the preprocessed
    output and its inactive_marker does not.
    """
    text = clang_e_result["stdout"]
    out = {}
    for name, (active_m, inactive_m) in markers.items():
        out[name] = (active_m in text) and (inactive_m not in text)
    return {"regions": out, "cmd": clang_e_result["cmd"]}


# ── build_manifest_diff ──────────────────────────────────────────────────────
def build_manifest_diff(cc_a: Path, cc_b: Path) -> dict:
    """G1 out-of-band instrument: diff two compile_commands.json files' -D flag
    sets and include maps per source file (K1 §4 build_manifest_diff)."""
    import json

    def _load(p: Path) -> dict[str, dict]:
        entries = json.loads(Path(p).read_text(encoding="utf-8-sig"))
        out = {}
        for e in entries:
            file_key = e["file"]
            args = e.get("arguments") or []
            defines = sorted(a for a in args if a.startswith("-D"))
            includes = sorted(a for a in args if a.startswith("-I"))
            out[file_key] = {"defines": defines, "includes": includes}
        return out

    a, b = _load(cc_a), _load(cc_b)
    files = sorted(set(a) | set(b), key=lambda s: s.encode("utf-8"))
    diffs = []
    for f in files:
        da, db = a.get(f, {}).get("defines", []), b.get(f, {}).get("defines", [])
        ia, ib = a.get(f, {}).get("includes", []), b.get(f, {}).get("includes", [])
        if da != db or ia != ib:
            diffs.append({
                "file": f,
                "defines_only_in_a": sorted(set(da) - set(db)),
                "defines_only_in_b": sorted(set(db) - set(da)),
                "includes_changed": ia != ib,
            })
    return {"files_compared": len(files), "diffs": diffs, "identical": not diffs}
