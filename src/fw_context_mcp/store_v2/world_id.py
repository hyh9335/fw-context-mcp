"""store_v2.world_id — world-identity primitives (Slice 0).

Grouping/routing identity primitives from the frozen Phase-2 contract
(`phase3/PHASE3_BASELINE.md` L11) and the D5a experiment implementations.
Pure functions; no I/O; no store access.

Classification (carried verbatim from the contract):
* ``RepositoryKey``  — grouping/routing only, NEVER a reuse gate (SAFE_REUSE §1;
  the legacy ``project_id`` is a store/routing token).
* ``SourceSnapshotID`` — [S1]§2.2 formula STUB (verbatim D5a port
  ``snapshot_id``); grouping evidence only, never a reuse gate; commit SHAs
  appear ONLY here and NEVER in any TUSemanticKey input.
* ``BuildIdentity``  — ``H(ToolchainIdentity, parser_salt, environment_digest,
  declared_env_salt+provenance, builder_family)`` (BASELINE L11), computed with
  the anchored ``parser_generation`` folded in as an additional domain input
  (DECISION L52 normative fix rows 8/13: parser_generation must be an anchored
  value at bind; the build identity must not be reusable across generations).

All digest conventions match :mod:`store_v2.keys` (full-width SHA-256,
``SEP``-joined, domain-tagged).
"""

from __future__ import annotations

from dataclasses import dataclass

from .keys import UNKNOWN_TAG, h
from .ncc import SEP, sha

#: Domain tags (documented, versioned).
REPO_KEY_DOMAIN = "fwctx-repokey/v1"
SNAPSHOT_STUB_DOMAIN = "fwctx-snapstub/v1"     # [S1]§2.2 stub, verbatim D5a
BUILD_ID_DOMAIN = "fwctx-buildid/v1"


def repository_key(remotes: tuple[str, ...] | None,
                   root_commit: str | None) -> str:
    """RepositoryKey digest — grouping/routing only, NEVER a reuse gate.

    [S1] derivation: digest over canonical remotes → root commits → UNKNOWN.
    ``None`` remotes ⇒ the ``UNKNOWN_TAG`` leg is hashed in (R-K1(d): UNKNOWN
    is an explicit, digested state, never a silent empty).
    """
    remotes_leg = SEP.join(sorted(remotes)) if remotes else UNKNOWN_TAG
    commit_leg = root_commit if root_commit else UNKNOWN_TAG
    return h(REPO_KEY_DOMAIN, remotes_leg, commit_leg)


@dataclass(frozen=True)
class RepositoryIdentity:
    """Namespace-level routing identity (schema §2.1 inputs).

    Attributes:
        repository_key_sha: digest from :func:`repository_key`.
        namespace_tag: snapshot namespace tag (``'git'`` | ``'tree'``).
        derivation_detail: JSON-serializable provenance of the inputs (INV-9).
    """

    repository_key_sha: str
    namespace_tag: str
    derivation_detail: str


def snapshot_id(ns_tag: str, base_commit: str | None,
                dirty: list[tuple[str, str, str]] | None = None) -> str:
    """SourceSnapshotID — [S1]§2.2 formula STUB (verbatim D5a port).

    ``H(namespace_tag, base_commit|∅, dirty_delta_digest)``. Grouping evidence
    only (never a reuse gate). ``dirty`` entries are ``(path, mode, blob_sha)``
    and are order-insensitive (sorted before digesting), mirroring the D5a
    stub. A commit SHA may enter ONLY this grouping id — never a TUSemanticKey
    (frozen Phase-2 contract).
    """
    dirty = dirty or []
    if dirty:
        payload = SEP.join(sorted(f"{p}\x1e{m}\x1e{b}" for p, m, b in dirty))
        delta = sha(payload)
    else:
        delta = ""
    return h(SNAPSHOT_STUB_DOMAIN, ns_tag, base_commit or "", delta)


def build_identity(*, toolchain_identity: str, parser_salt: str,
                   parser_generation: int, environment_digest: str,
                   declared_env_salt: str, declared_env_provenance: str,
                   builder_family: str) -> str:
    """BuildIdentity digest (PHASE3_BASELINE L11 formula) with the anchored
    ``parser_generation`` folded in.

    Inputs: toolchain identity, parser salt, environment digest, the declared
    environment salt + its provenance, and the builder family. Uncertified
    environment ⇒ pass :data:`UNKNOWN_TAG` as the environment digest; it is
    hashed in (R-K1(d)) and the resulting build identity is world-bound
    through the caller's certification discipline at Slice 1 (G7).
    """
    return h(BUILD_ID_DOMAIN, toolchain_identity, parser_salt,
             str(parser_generation), environment_digest,
             declared_env_salt, declared_env_provenance, builder_family)
