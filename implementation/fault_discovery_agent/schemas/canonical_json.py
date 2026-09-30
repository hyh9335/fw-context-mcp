"""Canonical JSON + hash recipes (FREEZE_MANIFEST_SPEC.md §1; CORPUS_LOCK_PLAN.md §2).

Recipe constants:
- canonical JSON: UTF-8 (no BOM), LF, keys sorted byte-wise lexicographically,
  no insignificant whitespace, arrays keep order, shortest round-trip numbers.
- non-JSON artifacts: raw bytes as-is.
- per-artifact hash: lowercase hex SHA-256 over canonical bytes.
- combined manifest hash: SHA-256 over UTF-8 (no BOM, LF) text of one
  ``<sha256>  <relpath>\\n`` line per artifact — TWO spaces — paths relative to
  the round root, sorted byte-wise ascending, no extra terminator.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_json_bytes(obj) -> bytes:
    """Serialize *obj* to canonical JSON bytes (FREEZE_MANIFEST_SPEC.md §1.1)."""
    text = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """Lowercase hex SHA-256 over raw file bytes (CORPUS_LOCK_PLAN.md §2.1)."""
    return sha256_hex(Path(path).read_bytes())


def manifest_line(digest: str, relpath: str) -> str:
    """One ``<sha256>  <relpath>\\n`` line (two-space separator, LF).

    This is the CORPUS_LOCK_PLAN.md §2.3 / FREEZE_MANIFEST_SPEC.md §1.4 form:
    digest FIRST. (The PLANT_SPEC fixture-hash recipe uses the inverse order
    ``<relpath>  <sha256>`` — see fixture_tree_hash.)
    """
    return f"{digest}  {relpath}\n"


def _sort_key_by_relpath(line: str) -> bytes:
    """Sort key extracting the relpath of a digest-first manifest line.

    Both frozen specs sort by rel-path (byte order). With a fixed-width digest
    column (64 hex + 2 spaces) sorting by relpath equals sorting the whole
    line; we key on the relpath explicitly so the two readings can never
    diverge.
    """
    body = line.rstrip("\n")
    if len(body) > 66 and body[64:66] == "  ":
        return body[66:].encode("utf-8")
    return body.encode("utf-8")


def combined_manifest_hash(lines: list[str]) -> str:
    """SHA-256 over the concatenation of manifest lines sorted by rel-path (§1.4).

    *lines* are full digest-first manifest lines (already LF-terminated).
    """
    ordered = sorted(lines, key=_sort_key_by_relpath)
    return sha256_hex("".join(ordered).encode("utf-8"))


def fixture_tree_hash(root: Path) -> tuple[str, list[tuple[str, str]]]:
    """Fixture hash recipe of CORPUS_LOCK_PLAN.md §2 and every PLANT_SPEC.md.

    FROZEN recipe (byte-exact per PLANT_SPEC.md "Fixture hash recipe"):
    1. per file: SHA-256 over raw file bytes, lowercase hex;
    2. ``line = "<rel-path>  " + hex + "\\n"`` — RELPATH FIRST (inverse of the
       manifest line);
    3. sort lines lexicographically by rel-path (byte order);
    4. fixture_hash = lowercase hex SHA-256 over the concatenation of the
       sorted lines (no extra terminator).

    Returns (fixture_hash, per_file_list) where per_file_list is
    [(relpath, digest), ...] sorted by relpath.
    """
    root = Path(root)
    entries: list[tuple[str, str]] = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            entries.append((rel, file_sha256(p)))
    entries.sort(key=lambda e: e[0].encode("utf-8"))
    blob = "".join(f"{rel}  {d}\n" for rel, d in entries).encode("utf-8")
    return sha256_hex(blob), entries


def tree_manifest_lines(root: Path, prefix: str = "") -> list[str]:
    """Manifest lines for every file under *root*, relpaths prefixed with *prefix*."""
    lines: list[str] = []
    base = Path(root)
    for p in sorted(base.rglob("*")):
        if p.is_file():
            rel = prefix + p.relative_to(base).as_posix()
            lines.append(manifest_line(file_sha256(p), rel))
    return lines


def git_blob_sha1(data: bytes) -> str:
    """Git blob object id for upstream-snapshot integrity verification (C4/C6)."""
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()
