"""store_v2.identity_capture — production capture of repository/snapshot identity.

Phase-4 Slice 2 (task §4–§6). Implements the frozen Phase-2 contract
[``_contrib/S1_snapshot_repository_identity.md``] over a REAL worktree:

* ``RepositoryKey`` — digest over canonical remotes → root commits → UNKNOWN
  ([S1]§1.2–§1.3).  Grouping/routing ONLY; never a reuse gate.
* ``SourceSnapshotID = H(namespace_tag, base_commit|∅, dirty_delta_digest)``
  ([S1]§2.2) where the dirty delta is the CONTENT-hash pair list
  ``(canon_path, marker, sha256(bytes))`` — sorted, deterministic, mtime-free,
  clone-root independent.  Non-git workspaces use the ``'tree'`` namespace
  (whole-tree content scan).  Any capture failure ⇒ ``verification='UNVERIFIED'``
  (F-9 fail-closed marker).

Exclusions (dirty set AND tree scan): build-output dirs (the same patterns the
legacy ``compute_config_hash`` receives), ``.fw-context/`` own state, VCS
metadata directories.  Git-ignored paths are excluded implicitly by
``git status`` (ignored files are not reported without ``--ignored``).

Absolute clone paths NEVER enter any digest: paths are stored project-relative
posix ([PCS] canonicalization); remote URLs are normalized per [PCS]§3
(lowercase scheme+host, credentials stripped, ``.git``/trailing slashes
stripped, SCP syntax rewritten).

Loaded ONLY when the v2 shadow flag is on (Slice-0 inertness gate holds).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .keys import UNKNOWN_TAG, h
from .world_id import (
    SNAPSHOT_STUB_DOMAIN,
    RepositoryIdentity,
    repository_key,
    snapshot_id,
)

#: Where the capture identity comes from (INV-9 provenance).
CAPTURE_VERSION = "fwctx-snapshot-capture/v1"

# [S1]§2.3 dirty-delta markers (content-based, NOT timestamp-based).
MARKER_MODIFIED = "MODIFIED"      # tracked, content differs from HEAD
MARKER_ADDED = "ADDED"            # untracked/new (or staged add)
MARKER_DELETED = "DELETED"        # tracked file gone (blob leg = ∅)
MARKER_TYPECHANGE = "TYPECHANGE"  # mode/symlink change

_VCS_DIRS = {".git", ".hg", ".svn"}
_OWN_STATE_DIR = ".fw-context"

_STATUS_RE = re.compile(r"^([ ARCMDT?!]{1,2}) (.*)$", re.DOTALL)


def normalize_remote_url(url: str) -> str:
    """Normalize one git remote URL per [PCS]§3.

    lowercase scheme+host, strip credentials, strip trailing ``.git`` and
    slashes, rewrite SCP syntax ``git@host:path`` → ``ssh://host/path``.
    """
    url = (url or "").strip()
    if not url:
        return ""
    m = re.match(r"^([A-Za-z0-9._~-]+)@([^:/]+):(.*)$", url)
    if m:  # scp-like
        url = f"ssh://{m.group(2)}/{m.group(3)}"
    if "://" not in url:  # bare local path: keep only the basename
        url = url.replace("\\", "/")
        return url.rstrip("/").rsplit("/", 1)[-1]
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/@]*@)?([^/:]+)(?::(\d+))?(/.*)?$",
                 url)
    if not m:
        return url
    scheme, _, host, port, rest = m.groups()
    rest = (rest or "/").rstrip("/")
    if rest.endswith(".git"):
        rest = rest[:-4]
    out = f"{scheme.lower()}://{host.lower()}"
    if port:
        out += f":{port}"
    return out + rest


def _git(args: list[str], cwd: Path, timeout: float = 60.0
         ) -> tuple[int, str]:
    """Run one read-only git query; return (returncode, stdout)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0",
                 "GIT_CONFIG_NOSYSTEM": "1"})
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 128, ""
    return proc.returncode, proc.stdout or ""


def _is_excluded(rel_posix: str, build_dir_patterns: list[str] | None) -> bool:
    """Exclusion test shared by the dirty scan and the tree scan ([S1]§2.3)."""
    if rel_posix.endswith("/"):
        return True  # porcelain directory entry
    parts = rel_posix.split("/")
    if parts[0] in _VCS_DIRS or parts[0] == _OWN_STATE_DIR:
        return True
    for pat in build_dir_patterns or []:
        pat = (pat or "").strip()
        if not pat:
            continue
        pat = pat.replace("\\", "/").strip("/")
        base = pat.rstrip("/**").rstrip("/")
        if rel_posix == base or rel_posix.startswith(base + "/"):
            return True
        if any(pat in p or p == pat for p in parts):
            return True
    return False


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _filtered_same_as_head(project_root: Path, base_commit: str, path: str,
                           abs_path: Path) -> bool:
    """True when git's own clean-filtered comparison says content is
    unchanged vs HEAD (line-ending-only differences are not dirty)."""
    rc, filt = _git(["hash-object", f"--path={path}", str(abs_path)],
                    project_root)
    if rc != 0 or not filt.strip():
        return False  # cannot verify → keep the status-reported entry
    rc2, blob = _git(["rev-parse", f"{base_commit}:{path}"], project_root)
    if rc2 != 0:
        return False  # not in HEAD ⇒ treat as genuinely new content
    return filt.strip() == blob.strip()


@dataclass(frozen=True)
class SnapshotCapture:
    """Result of one production snapshot capture ([S1]§2.2 inputs)."""

    snapshot_id: str
    namespace_tag: str                 # 'git' | 'tree'
    base_commit: str | None            # HEAD sha; None for bare tree
    dirty_delta_digest: str            # '' = clean tree
    verification: str                  # 'verified' | 'UNVERIFIED'
    dirty_entries: tuple[tuple[str, str, str], ...]
    branch: str                        # provenance ONLY (never identity)
    git_error: str                     # non-empty ⇒ why UNVERIFIED
    capture_detail: dict = field(default_factory=dict)


def capture_repository_identity(project_root: Path) -> RepositoryIdentity:
    """RepositoryKey inputs from the real worktree; no-git ⇒ UNKNOWN legs.

    Namespace routing id (outside this function): ``r:<key>`` for git
    repositories, ``t:<store_token>`` for no-git workspaces — RepositoryKey is
    NEVER a reuse gate, and no-git RepositoryKey is UNKNOWN by contract
    ([S1]§1.4), so the store token carries the routing distinction.
    """
    rc_remotes, remotes_out = _git(["remote", "-v"], project_root)
    rc_root, roots_out = _git(["rev-list", "--max-parents=0", "HEAD"],
                              project_root)
    is_git = rc_remotes == 0 and rc_root == 0
    detail: dict = {"capture_version": CAPTURE_VERSION}
    if is_git:
        url_set = {
            normalize_remote_url(line.split("\t", 1)[1].rsplit(" ", 1)[0])
            for line in remotes_out.splitlines()
            if "\t" in line and line.rsplit(" ", 1)[-1] == "(fetch)"
        }
        remotes: tuple[str, ...] | None = tuple(sorted(url_set)) or None
        roots = sorted({ln.strip() for ln in roots_out.splitlines()
                        if ln.strip()})
        root_commit = "\x1f".join(roots) if roots else None
        detail["remote_count"] = len(remotes or ())
        detail["root_commit_count"] = len(roots)
    else:
        remotes, root_commit = None, None
        detail["git"] = "absent-or-unreadable"
    key = repository_key(remotes, root_commit)
    detail["remotes_leg"] = sorted(remotes) if remotes else "UNKNOWN"
    detail["root_commit_leg"] = root_commit or "UNKNOWN"
    return RepositoryIdentity(
        repository_key_sha=key,
        namespace_tag="git" if is_git else "tree",
        derivation_detail=json.dumps(detail, sort_keys=True),
    )


def capture_snapshot(project_root: Path,
                     build_dir_patterns: list[str] | None = None,
                     ) -> SnapshotCapture:
    """Production SourceSnapshotID capture ([S1]§2.2–§2.4).

    Git present: dirty set from ``git status --porcelain -z
    --untracked-files=all`` (content-hash legs per dirty file; DELETED files
    carry the empty blob leg) minus build-output dirs and ``.fw-context/``.
    Clean tree ⇒ empty delta digest.  No git: 'tree' namespace — whole-tree
    content scan.  ANY failure ⇒ deterministic UNVERIFIED snapshot
    (fail-closed, F-9) with an explicit UNKNOWN leg.
    """
    t0 = time.monotonic()
    project_root = Path(project_root).resolve()
    patterns = list(build_dir_patterns or [])
    rc_head, head_out = _git(["rev-parse", "HEAD"], project_root)
    is_git = rc_head == 0 and head_out.strip()

    if not is_git:
        return _capture_tree_snapshot(project_root, patterns, t0)

    detail: dict = {"capture_version": CAPTURE_VERSION, "mode": "git"}
    base_commit = head_out.strip().split()[0]
    rc_br, br_out = _git(["rev-parse", "--abbrev-ref", "HEAD"], project_root)
    branch = br_out.strip() if rc_br == 0 else ""

    rc_st, st_out = _git(
        ["status", "--porcelain", "-z", "--untracked-files=all"],
        project_root)
    if rc_st != 0:
        return _unverified_capture("git", base_commit, branch,
                                   f"status-rc={rc_st}", t0, detail)

    entries: list[tuple[str, str, str]] = []
    tokens = [tok for tok in st_out.split("\0") if tok]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = re.match(r"^(..) (.*)$", tok)
        if not m:
            i += 1
            continue
        xy, path = m.group(1), m.group(2)
        i += 1
        if xy[0] in "RC":  # rename/copy: porcelain emits the NEW path then
            i += 1        # the original path as the next NUL token
        path = path.strip('"').replace("\\", "/")
        rel = Path(path).as_posix() if not path.startswith("/") else path
        if _is_excluded(rel, patterns):
            continue
        abs_path = project_root / path
        if "D" in xy or (not abs_path.exists()):
            if xy == "??":
                continue  # deleted untracked file: nothing to hash
            entries.append((rel, MARKER_DELETED, ""))
            continue
        if xy == "??":
            marker = MARKER_ADDED
        elif "T" in xy:
            marker = MARKER_TYPECHANGE
        else:
            marker = MARKER_MODIFIED
        sha = _file_sha256(abs_path)
        if sha is None:
            return _unverified_capture("git", base_commit, branch,
                                       f"unreadable:{rel}", t0, detail)
        if marker == MARKER_MODIFIED:
            # Content truth over stat/convert artifacts: ambient
            # line-ending/text-conversion configs can make ``git status``
            # report phantom modifications.  A MODIFIED entry stands only if
            # the clean-filtered worktree content differs from the HEAD blob
            # — i.e. git's own conversion semantics say the content changed
            # ([S1]§2.3 content-based dirty set).
            if _filtered_same_as_head(project_root, base_commit, path,
                                      abs_path):
                continue
        entries.append((rel, marker, sha))

    detail["dirty_count"] = len(entries)
    return _finish_capture("git", base_commit, branch, entries, t0, detail)


def _capture_tree_snapshot(project_root: Path, patterns: list[str],
                           t0: float) -> SnapshotCapture:
    """Non-git fallback: 'tree' namespace whole-content scan ([S1]§2.2)."""
    detail: dict = {"capture_version": CAPTURE_VERSION, "mode": "tree"}
    entries: list[tuple[str, str, str]] = []
    try:
        for dirpath, dirnames, filenames in os.walk(project_root):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in _VCS_DIRS and d != _OWN_STATE_DIR)
            for name in sorted(filenames):
                abs_path = Path(dirpath) / name
                try:
                    rel = abs_path.relative_to(project_root).as_posix()
                except ValueError:  # pragma: no cover
                    continue
                if _is_excluded(rel, patterns):
                    continue
                sha = _file_sha256(abs_path)
                if sha is None:
                    raise OSError(rel)
                try:
                    mode = os.stat(abs_path).st_mode & 0o777
                except OSError:
                    mode = 0
                entries.append((rel, f"f:{oct(mode)}", sha))
    except OSError as exc:
        return _unverified_capture("tree", None, "", f"walk:{exc}", t0, detail)
    detail["tree_count"] = len(entries)
    return _finish_capture("tree", None, "", entries, t0, detail)


def _finish_capture(tag: str, base: str | None, branch: str,
                    entries: list[tuple[str, str, str]], t0: float,
                    detail: dict) -> SnapshotCapture:
    """Compose the [S1]§2.2 digest over the sorted pair list (or clean ∅)."""
    ordered = sorted(entries)
    if ordered:
        payload = "\x1e".join(f"{p}\x1f{m}\x1f{b}" for p, m, b in ordered)
        delta = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    else:
        delta = ""
    sid = snapshot_id(tag, base, ordered) if ordered else \
        snapshot_id(tag, base, None)
    detail["elapsed_s"] = round(time.monotonic() - t0, 6)
    return SnapshotCapture(
        snapshot_id=sid, namespace_tag=tag, base_commit=base,
        dirty_delta_digest=delta or None, verification="verified",
        dirty_entries=tuple(ordered), branch=branch, git_error="",
        capture_detail=detail)


def _unverified_capture(tag: str, base: str | None, branch: str,
                        why: str, t0: float, detail: dict) -> SnapshotCapture:
    """F-9 fail-closed marker: deterministic UNVERIFIED snapshot id."""
    detail["git_error"] = why
    detail["elapsed_s"] = round(time.monotonic() - t0, 6)
    entries = [("!capture", "UNVERIFIED", UNKNOWN_TAG)]
    sid = snapshot_id(tag, base, entries)
    return SnapshotCapture(
        snapshot_id=sid, namespace_tag=tag, base_commit=base,
        dirty_delta_digest="UNVERIFIED", verification="UNVERIFIED",
        dirty_entries=tuple(entries), branch=branch, git_error=why,
        capture_detail=detail)


def toolchain_identity_sha(*, kind: str, version_string: str,
                           reported_target: str) -> str:
    """ToolchainIdentity digest ([S2]§2.2): ``H(kind, version, target)``.

    Consumed by :func:`store_v2.world_id.build_identity` as its first leg.
    """
    return h("fwctx-tool/v1", kind, version_string, reported_target)
