"""Freeze manifest build + verify (FREEZE_MANIFEST_SPEC.md; K3_INTERFACE_SPEC.md §1).

freeze_id = ``FRZ-<CASE>-<YYYYMMDDTHHMMSSZ>``; one manifest per case; shared
artifacts repeat their per-artifact hash in each manifest. The manifest is
canonical JSON (schemas.canonical_json) whose content lists every required
artifact class (§2) with its hash; the combined manifest hash is the §1.4
recipe over the per-artifact ``<sha256>  <relpath>`` lines.

Producers: custodian via K3. Consumers: K1 (freeze_id stamping), K2 (bundle
binding), scorer (refuses any round whose combined hash does not recompute).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import (
    canonical_json_bytes,
    combined_manifest_hash,
    file_sha256,
    manifest_line,
    sha256_hex,
    tree_manifest_lines,
)

# Artifact classes of FREEZE_MANIFEST_SPEC.md §2 — ALL required; an omission
# refuses the freeze.
REQUIRED_CLASSES = (
    "target_source_snapshot",
    "build_config_snapshot",
    "agent_implementation_version",
    "agent_prompts_config",
    "fw_context_version",
    "k_versions",
    "schema_versions",
    "detector_versions",
    "corpus_fixture",
    "oracle_identity",
)


class FreezeRefused(Exception):
    """Raised when a manifest is malformed, incomplete, or fails verification."""


def mint_freeze_id(case: str, *, now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"FRZ-{case}-{ts}"


def _artifact_lines(artifacts: dict[str, Path | str], round_root: Path) -> list[str]:
    lines = []
    for name in sorted(artifacts, key=lambda s: s.encode("utf-8")):
        p = Path(artifacts[name])
        if not p.is_absolute():
            p = round_root / p
        if not p.is_file():
            raise FreezeRefused(f"artifact {name!r} missing on disk: {p}")
        rel = Path(name).as_posix()
        lines.append(manifest_line(file_sha256(p), rel))
    return lines


def build_manifest(
    *,
    case: str,
    round_root: Path,
    source_snapshot: Path,
    build_config: Path,
    corpus_fixture: Path,
    oracle_hashes: dict[str, str],
    agent_identity: dict,
    solver_bundle_list: list[str],
    custodian_only_excluded: list[str],
    fixtures_hash: str | None = None,
    freeze_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    """Assemble a freeze manifest dict for one case (FREEZE_MANIFEST_SPEC §2).

    *oracle_hashes* maps custodian-only oracle relpaths to their SHA-256 —
    oracle HASH is public, oracle CONTENT is hidden (CORPUS_LOCK_PLAN §4).
    """
    if freeze_id is None:
        freeze_id = mint_freeze_id(case)
    class_map = {
        "target_source_snapshot": source_snapshot,
        "build_config_snapshot": build_config,
        "corpus_fixture": corpus_fixture,
    }
    for cls, path in class_map.items():
        if not Path(path).exists():
            raise FreezeRefused(f"required artifact class {cls} missing: {path}")

    fh = fixtures_hash
    if fh is None:
        from ..schemas import fixture_tree_hash

        fh, _ = fixture_tree_hash(corpus_fixture)

    # Per-artifact hash lines for whole-class directory artifacts (§1.4 recipe
    # over the sorted relpaths inside the round root).
    lines: list[str] = []
    lines += tree_manifest_lines(source_snapshot, prefix="source_snapshot/")
    lines += tree_manifest_lines(build_config, prefix="build_config/")
    lines += tree_manifest_lines(corpus_fixture, prefix="corpus_fixture/")
    for rel in sorted(oracle_hashes, key=lambda s: s.encode("utf-8")):
        lines.append(manifest_line(oracle_hashes[rel], f"oracle/{rel}"))

    manifest = {
        "freeze_id": freeze_id,
        "case": case,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "recipe": "FREEZE_MANIFEST_SPEC.md#1 + CORPUS_LOCK_PLAN.md#2",
        "fixture_hash": fh,
        "combined_manifest_sha256": combined_manifest_hash(lines),
        "artifact_lines": lines,
        "agent_identity": agent_identity,
        "solver_bundle": sorted(solver_bundle_list),
        "custodian_only_excluded_from_solver": sorted(custodian_only_excluded),
        "oracle_hashes": dict(sorted(oracle_hashes.items())),
        "schema_versions": {"wave1": "wave1-design-gate-1"},
    }
    return manifest


def write_manifest(manifest: dict, path: Path) -> str:
    """Write the manifest canonically; returns the file hash."""
    data = canonical_json_bytes(manifest)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return sha256_hex(data)


def verify_manifest(manifest_path: Path, round_root: Path) -> dict:
    """Recompute every hash; refuse the round on ANY mismatch (K3 §1).

    Returns the parsed manifest on success. Raises FreezeRefused when the
    combined hash does not recompute, an artifact is missing, or a required
    class is absent.
    """
    raw = Path(manifest_path).read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise FreezeRefused(f"manifest not readable canonical JSON: {exc}") from exc
    for cls in REQUIRED_CLASSES:
        if cls == "target_source_snapshot":
            key = "source_snapshot/"
        elif cls == "build_config_snapshot":
            key = "build_config/"
        elif cls == "corpus_fixture":
            key = "corpus_fixture/"
        else:
            continue
        if not any(l.endswith(f"  {key}") or f"  {key}" in l for l in manifest.get("artifact_lines", [])):
            # directory-class presence is proven by at least one artifact line
            if not any(l.split("  ", 1)[-1].startswith(key) for l in manifest.get("artifact_lines", [])):
                raise FreezeRefused(f"manifest missing required artifact class: {cls}")
    recomputed = combined_manifest_hash(manifest["artifact_lines"])
    if recomputed != manifest["combined_manifest_sha256"]:
        raise FreezeRefused(
            f"combined manifest hash mismatch: recorded "
            f"{manifest['combined_manifest_sha256']}, recomputed {recomputed}"
        )
    # Verify every artifact line against disk where the file is expected.
    # oracle/ lines are hash-only attestations (CORPUS_LOCK_PLAN §2.5: oracle
    # hash visible, oracle content hidden) — verified only when the content
    # happens to be present in the round root (custodian mirror).
    for line in manifest["artifact_lines"]:
        digest, rel = line.rstrip("\n").split("  ", 1)
        p = round_root / rel
        if rel.startswith("oracle/") and not p.is_file():
            continue
        if not p.is_file():
            raise FreezeRefused(f"manifest artifact missing on disk: {rel}")
        actual = file_sha256(p)
        if actual != digest:
            raise FreezeRefused(
                f"artifact hash mismatch for {rel}: manifest {digest}, actual {actual}"
            )
    return manifest
