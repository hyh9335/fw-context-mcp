"""test_slice2_identity — S2.1/S2.2 production identity capture oracles.

Covers SLICE2 task §4–§8:
* S2-T01 clone-independent snapshot identity (same clean source at ROOT_A /
  ROOT_B → same SourceSnapshotID).
* S2-T02 dirty snapshot separation (A → different B → original A returns;
  no revert special case — S2-T16).
* Dirty-delta determinism: content-based (not timestamps), stable ordering,
  explicit file identity, modification/addition/deletion distinguished.
* Non-git fallback ('tree' namespace) + UNVERIFIED fail-closed.
* BuildIdentity is NOT a legacy config_hash alias; components preserved.
* §8 axis separation: same snapshot + different builds ⇒ different worlds,
  while equal KB-C may still share one SemanticState (worlds store-level;
  see test_slice2_worlds).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slice1_helpers import make_project  # noqa: E402
from slice2_helpers import (  # noqa: E402
    clone_project,
    dirty,
    git,
    make_git_project,
    restore,
)

from fw_context_mcp.store_v2.identity_capture import (  # noqa: E402
    capture_repository_identity,
    capture_snapshot,
    normalize_remote_url,
    toolchain_identity_sha,
)
from fw_context_mcp.store_v2.world_id import build_identity  # noqa: E402


# ── S2-T01: clone independence ───────────────────────────────────────────────

def test_s2_t01_clone_independent_snapshot_identity(tmp_path):
    """Same clean repository at two absolute roots ⇒ same SourceSnapshotID."""
    data = make_git_project(tmp_path / "a")
    proj_a: Path = data["proj"]
    proj_b = clone_project(proj_a, tmp_path / "deeper" / "clone_B")
    snap_a = capture_snapshot(proj_a)
    snap_b = capture_snapshot(proj_b)
    assert snap_a.verification == "verified"
    assert snap_a.snapshot_id == snap_b.snapshot_id, (
        "clone path leaked into SourceSnapshotID")
    assert snap_a.base_commit == snap_b.base_commit
    assert snap_a.dirty_delta_digest is None  # clean tree
    # RepositoryKey also clone-independent (normalized remotes; both have none
    # → UNKNOWN legs) — grouping only, never a reuse gate.
    key_a = capture_repository_identity(proj_a).repository_key_sha
    key_b = capture_repository_identity(proj_b).repository_key_sha
    assert key_a == key_b


def test_s2_t01b_snapshot_id_shape_and_stability(tmp_path):
    """The digest is H('fwctx-snapstub/v1', tag, base, delta); recompute is
    deterministic and does not depend on scan wall time."""
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    s1 = capture_snapshot(proj)
    time.sleep(0.05)
    (proj / "src" / "main.c").stat()  # touch reads do NOT change identity
    s2 = capture_snapshot(proj)
    assert s1.snapshot_id == s2.snapshot_id
    assert s1.snapshot_id and len(s1.snapshot_id) == 64


# ── S2-T02 / S2-T16: dirty separation + natural restoration ─────────────────

def test_s2_t02_dirty_changes_id_and_restore_returns_original(tmp_path):
    """clean A → dirty B (different id) → restore ⇒ original id again.
    No revert special case: identity equality recovers naturally (S2-T16)."""
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    id_clean = capture_snapshot(proj).snapshot_id

    dirty(proj, "inc/util.h", "#define UTIL_VERSION 3\nint util(void);\n")
    snap_dirty = capture_snapshot(proj)
    assert snap_dirty.snapshot_id != id_clean
    assert snap_dirty.dirty_delta_digest is not None
    assert snap_dirty.dirty_entries[0][1] == "MODIFIED"

    restore(proj, "inc/util.h",
            "#define UTIL_VERSION 2\nint util(void);\n")
    assert capture_snapshot(proj).snapshot_id == id_clean


def test_s2_t02b_dirty_kinds_and_order_independence(tmp_path):
    """Modification / addition / deletion are distinct, content-based, and
    file order + mtimes cannot move the digest."""
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    base = capture_snapshot(proj)

    dirty(proj, "inc/util.h",
          "#define UTIL_VERSION 2\nint util(void);\n// x\n")
    dirty(proj, "new_file.h", "#define NEW_MARK 1\n")
    (proj / "macros.h").unlink()
    t0 = time.monotonic()
    snap = capture_snapshot(proj)
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0
    kinds = {marker: path for path, marker, _ in snap.dirty_entries}
    assert kinds["MODIFIED"] == "inc/util.h"
    assert kinds["ADDED"] == "new_file.h"
    assert kinds["DELETED"] == "macros.h"
    deleted_leg = [b for p, m, b in snap.dirty_entries if m == "DELETED"]
    assert deleted_leg == [""]  # deletion carries the empty blob leg

    # Timestamp-only changes must NOT alter identity (content-based).
    old = time.time() - 9999
    import os
    os.utime(proj / "inc" / "cfg.h", (old, old))
    assert capture_snapshot(proj).snapshot_id == snap.snapshot_id

    # Restore exact original bytes ⇒ original id (deletion reversal).
    (proj / "macros.h").write_text("#define IMACROS_MARK 9\n")
    (proj / "new_file.h").unlink()
    dirty(proj, "inc/util.h",
          "#define UTIL_VERSION 2\nint util(void);\n")
    assert capture_snapshot(proj).snapshot_id == base.snapshot_id


def test_s2_t02c_dirty_excludes_build_outputs_and_own_state(tmp_path):
    """Build-output dirs and .fw-context/ never enter the dirty delta."""
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    base = capture_snapshot(proj, build_dir_patterns=["build/"])
    (proj / "build").mkdir()
    (proj / "build" / "gen.h").write_text("#define GEN 1\n")
    (proj / ".fw-context").mkdir()
    (proj / ".fw-context" / "cache.bin").write_text("x")
    assert capture_snapshot(
        proj, build_dir_patterns=["build/"]).snapshot_id == base.snapshot_id


# ── §4 non-git fallback + fail-closed ────────────────────────────────────────

def test_s2_non_git_tree_namespace_fallback(tmp_path):
    """No git ⇒ 'tree' namespace; whole-content scan; clone-independent."""
    data = make_project(tmp_path)          # no git init
    proj: Path = data["proj"]
    snap = capture_snapshot(proj)
    assert snap.namespace_tag == "tree"
    assert snap.base_commit is None
    assert snap.verification == "verified"
    clone = clone_project(proj, tmp_path / "clone")
    assert capture_snapshot(clone).snapshot_id == snap.snapshot_id
    # dirty content changes the tree id too
    dirty(proj, "inc/cfg.h", "#ifndef CFG_H\n#define CFG_H\n#endif\n")
    assert capture_snapshot(proj).snapshot_id != snap.snapshot_id


def test_s2_capture_failure_marks_unverified(tmp_path, monkeypatch):
    """Any capture failure ⇒ deterministic UNVERIFIED snapshot (F-9)."""
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    healthy = capture_snapshot(proj)
    assert healthy.verification == "verified"

    import fw_context_mcp.store_v2.identity_capture as ic
    # a genuinely dirty tree forces the content-hash leg, whose failure must
    # fail the whole capture CLOSED (F-9)
    dirty(proj, "inc/util.h", "#define UTIL_VERSION 3\nint util(void);\n")
    monkeypatch.setattr(ic, "_file_sha256", lambda p: None)
    snap = capture_snapshot(proj)
    assert snap.verification == "UNVERIFIED"
    assert snap.git_error
    assert snap.dirty_delta_digest == "UNVERIFIED"
    # deterministic: the second failing capture yields the SAME id
    assert capture_snapshot(proj).snapshot_id == snap.snapshot_id
    # and it differs from the healthy capture (fail-closed ≠ silent clean)
    assert snap.snapshot_id != healthy.snapshot_id


# ── §4/§6 remote normalization + RepositoryKey provenance ────────────────────

def test_s2_remote_normalization_and_repokey_unification(tmp_path):
    assert normalize_remote_url("git@host.example:org/repo.git") == \
        "ssh://host.example/org/repo"
    assert normalize_remote_url("HTTPS://User@Host.example/org/repo.git/") == \
        "https://host.example/org/repo"
    assert normalize_remote_url("https://user:pw@host.example/r.git") == \
        "https://host.example/r"
    # Remotes enter the RepositoryKey; a local path must not.
    data = make_git_project(tmp_path)
    proj: Path = data["proj"]
    git("remote", "add", "origin", "git@srv.example:firm/x.git", cwd=proj)
    key1 = capture_repository_identity(proj).repository_key_sha
    clone = clone_project(proj, tmp_path / "c2")
    assert capture_repository_identity(clone).repository_key_sha == key1
    git("remote", "set-url", "origin", "https://srv.example/firm/x.git",
        cwd=proj)
    key2 = capture_repository_identity(proj).repository_key_sha
    assert key2 != key1  # derived key: adding a rekey is a documented once-cost


# ── §7 BuildIdentity: real composition, NOT config_hash ─────────────────────

def _cert_components():
    from slice1_helpers import (  # local import: heavy fixture helpers
        make_project, certify_project, fresh_store)
    from fw_context_mcp.store_v2.publish import store_nonce
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        data = make_project(tmp)
        store = fresh_store(tmp)
        nonce = store_nonce(store)
        try:
            _, _, cert = certify_project(tmp, data["proj"], nonce=nonce)
            return cert
        finally:
            store.close()


def test_s2_t02d_build_identity_not_config_hash_alias():
    """BuildIdentity is composed from certified components and is disjoint
    from the legacy config_hash (task §7)."""
    cert = _cert_components()
    ident = cert.parser_identity
    tool = toolchain_identity_sha(kind="clang",
                                  version_string=ident.capture_binary_id,
                                  reported_target=ident.target_triple)
    b1 = build_identity(toolchain_identity=tool,
                        parser_salt=cert.inputs.parser_salt,
                        parser_generation=cert.inputs.parser_generation,
                        environment_digest=cert.inputs.environment_digest,
                        declared_env_salt="",
                        declared_env_provenance="{}",
                        builder_family="cmake")
    # any component change moves the identity
    b2 = build_identity(toolchain_identity=tool,
                        parser_salt=cert.inputs.parser_salt,
                        parser_generation=cert.inputs.parser_generation + 1,
                        environment_digest=cert.inputs.environment_digest,
                        declared_env_salt="",
                        declared_env_provenance="{}",
                        builder_family="cmake")
    b3 = build_identity(toolchain_identity=tool,
                        parser_salt=cert.inputs.parser_salt,
                        parser_generation=cert.inputs.parser_generation,
                        environment_digest=cert.inputs.environment_digest,
                        declared_env_salt="user-salt",
                        declared_env_provenance='{"declared_by":"test"}',
                        builder_family="cmake")
    b4 = build_identity(toolchain_identity=tool,
                        parser_salt=cert.inputs.parser_salt,
                        parser_generation=cert.inputs.parser_generation,
                        environment_digest=cert.inputs.environment_digest,
                        declared_env_salt="",
                        declared_env_provenance="{}",
                        builder_family="make")
    assert len({b1, b2, b3, b4}) == 4
    # the certified toolchain identity carries the parser environment
    assert ident.capture_binary_id and ident.target_triple
    # and the identity is NOT the legacy config hash label (the Slice-1
    # certificate carries the legacy config hash as an unrelated label)
    assert b1 != cert.config_hash
    assert len(b1) == 64


def test_s2_t03b_toolchain_identity_path_clean():
    """Toolchain identity has no absolute-root component."""
    t1 = toolchain_identity_sha(kind="clang", version_string="clang version 22",
                                reported_target="x86_64-pc-windows-msvc")
    t2 = toolchain_identity_sha(kind="clang", version_string="clang version 22",
                                reported_target="x86_64-pc-windows-msvc")
    assert t1 == t2
    assert toolchain_identity_sha(kind="gcc", version_string="clang version 22",
                                  reported_target="x86_64-pc-windows-msvc") != t1
