"""store_v2.attest — Slice-4A A5′ directory attestation (R3 §3, accepted).

Capture face (cert time): :func:`dir_attestation_rows` computes one row per
directory in the candidate's shadowing surface D(C) — member dirs + main-file
dir (i), include roots (ii), source dir + compile dir (iii) — and the caller
writes them in the SAME transaction as the certificate row.

Admission face (:func:`observe_attested_dirs`): freshly readdir/stat every
attested directory (per-run memoization by normcase) and compare

* the identity pin (``st_dev``, ``st_ino``) — junction/subst/retarget class,
* ``os.path.realpath`` — mapping changes,
* the folded/sorted ``(name, kind)`` listing digest — name-set equality.

mtime is NEVER authority.  Any unreadable directory or NULL pin ⇒ fail
closed (miss).  The digest is the authority for name-set equality; the pin
is the authority for target identity; both always run (R3 §3.4).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

DIRLIST_DOMAIN = "fwctx-dirlist/v1"

WINDOWS = os.name == "nt"


def _normcase_posix(p: str) -> str:
    """The exact normalization the class-C post-step uses (certify.py:498)."""
    import posixpath
    return posixpath.normcase(posixpath.normpath(p.replace("\\", "/")))


@dataclass(frozen=True)
class Attestation:
    dir_normcase: str
    dir_realpath: str
    st_dev: int | None
    st_ino: int | None
    listing_sha: str
    entry_count: int


def attest_dir(d: str) -> Attestation | None:
    """Freshly observe one directory.  ``None`` ⇒ no attestation (fail closed).

    Names + kind only, never child metadata (R3 §3.2): shadowing is about
    name presence; the kind bit closes the file↔directory swap hole.
    """
    try:
        st = os.stat(d)
        rp = os.path.realpath(d)
        entries: list[tuple[str, str]] = []
        with os.scandir(d) as it:
            for e in it:
                name = e.name.casefold() if WINDOWS else e.name
                try:
                    kind = "d" if e.is_dir(follow_symlinks=False) else "f"
                except OSError:
                    return None
                entries.append((name, kind))
        entries.sort()
        names = [n + "\x00" + k for n, k in entries]
        digest = hashlib.sha256(
            "\x1f".join([DIRLIST_DOMAIN, _normcase_posix(d), *names])
            .encode("utf-8", "surrogatepass")).hexdigest()
        return Attestation(
            dir_normcase=_normcase_posix(d),
            dir_realpath=rp,
            st_dev=int(st.st_dev) if st.st_dev is not None else None,
            st_ino=int(st.st_ino) if getattr(st, "st_ino", None) is not None else None,
            listing_sha=digest,
            entry_count=len(names),
        )
    except OSError:
        return None


# ── capture face (cert time) ────────────────────────────────────────────────

def _root_dirs_from_include_list(include_list: list[str],
                                 project_root: str) -> list[str]:
    """Absolute directories for the NCC ordered include list (``-I=…`` etc.).

    ``ncc.canonicalize`` records roots as ``{flag}={path}`` with ``path``
    project-relative when under the root, else absolute (ncc.py:176-201).
    """
    dirs: list[str] = []
    for tok in include_list:
        if "=" not in tok:
            continue
        val = tok.split("=", 1)[1]
        if not val or val.startswith("ext:"):
            continue
        p = Path(val)
        if not p.is_absolute():
            p = Path(project_root) / val
        dirs.append(str(p))
    return dirs


def dir_attestation_rows(*, cert_id: int, closure_display_paths: list[str],
                         include_list: list[str], source_dir: str,
                         compile_dir: str, project_root: str,
                         implicit_dirs: list[str] | None = None) -> list[dict]:
    """Build the D(C) attestation rows for one certified candidate (R3 §3.1).

    D(C) = member dirs + the main file's dir + every argv include root +
    source dir + compile dir + (Slice-4B C1) implicit compiler include
    chain dirs.  Over-attestation costs false misses only, never false
    reuse.

    Slice-4B C1: ``implicit_dirs`` (the compiler's ``#include <...>`` search
    chain from ``clang -v -E``) are now attested as ``implicit-dir`` rows so
    that J1(b) (member-less implicit-directory mutation) is caught without
    A-c5.  A ``None`` or empty ``implicit_dirs`` means "C1 evidence not
    captured" — admission will MISS honestly against any certificate that
    SHOULD have had them (cert_version v4+).
    """
    now = _ts()
    seen: dict[str, dict] = {}

    def add(kind: str, d: str) -> None:
        if not d:
            return
        a = attest_dir(d)
        if a is None:
            return                      # unobservable dir ⇒ no row ⇒ admission miss
        key = (a.dir_normcase, kind)
        if key in seen:
            return
        seen[key] = {
            "cert_id": cert_id, "kind": kind,
            "dir_normcase": a.dir_normcase, "dir_realpath": a.dir_realpath,
            "st_dev": a.st_dev, "st_ino": a.st_ino,
            "listing_sha": a.listing_sha, "entry_count": a.entry_count,
            "captured_at": now,
        }

    ordinal = 0
    rows: list[dict] = []

    def flush(kind: str) -> None:
        nonlocal ordinal
        for key in [k for k in seen if k[1] == kind]:
            row = seen.pop(key)
            row["dir_ordinal"] = ordinal
            ordinal += 1
            rows.append(row)

    for disp in closure_display_paths:
        p = Path(disp)
        if not p.is_absolute():
            p = Path(project_root) / disp
        add("member-dir", str(p.parent))
    flush("member-dir")

    for d in _root_dirs_from_include_list(include_list, project_root):
        add("include-root", d)
    flush("include-root")

    add("source-dir", source_dir)
    flush("source-dir")
    add("compile-dir", compile_dir)
    flush("compile-dir")

    # Slice-4B C1: implicit compiler include chain (clang -v -E).
    for d in (implicit_dirs or []):
        add("implicit-dir", d)
    flush("implicit-dir")
    return rows


def record_dir_attestations(store, rows: list[dict]) -> None:
    """Insert attestation rows (caller's transaction; called with the cert)."""
    if not rows:
        return
    conn = store._conn
    conn.executemany(
        "INSERT OR IGNORE INTO cert_dir_attestations(cert_id, dir_ordinal, "
        "kind, dir_normcase, dir_realpath, st_dev, st_ino, listing_sha, "
        "entry_count, captured_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(r["cert_id"], r["dir_ordinal"], r["kind"], r["dir_normcase"],
          r["dir_realpath"], r["st_dev"], r["st_ino"], r["listing_sha"],
          r["entry_count"], r["captured_at"]) for r in rows])


# ── admission face (fresh observation, per-run memoized) ────────────────────

def observe_attested_dirs(rows: list, memo: dict) -> list[str]:
    """Re-observe every attested dir; return a list of miss reasons (empty ⇒ pass).

    Two-step defense in depth (R3 §3.4): the identity pin is compared before
    the digest so a retargeted directory is blamed precisely, and the digest
    is skipped for nothing.  ``memo`` maps dir_normcase → Attestation|None
    within one admission run (identical fresh observation reused).
    """
    misses: list[str] = []
    for row in rows:
        key = row["dir_normcase"]
        if key not in memo:
            memo[key] = attest_dir(key)
        cur = memo[key]
        if cur is None:
            misses.append(f"A5':dir-unreadable:{key}")
            continue
        r_dev, r_ino = row["st_dev"], row["st_ino"]
        if r_ino is not None and cur.st_ino is not None:
            if (cur.st_dev, cur.st_ino) != (r_dev, r_ino):
                misses.append(f"A5':dir-identity-changed:{key}")
                continue
        elif r_ino is None or cur.st_ino is None:
            # pin unavailable on either side ⇒ fail closed (R3 §3.2)
            misses.append(f"A5':pin-unavailable:{key}")
            continue
        if _normcase_posix(cur.dir_realpath) != _normcase_posix(row["dir_realpath"]):
            misses.append(f"A5':dir-realpath-changed:{key}")
            continue
        if cur.listing_sha != row["listing_sha"]:
            misses.append(f"A5':listing-changed:{key}")
            continue
    return misses


def _ts() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
