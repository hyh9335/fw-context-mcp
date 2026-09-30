"""K3 custody log (K3_INTERFACE_SPEC.md §1, §7; CORPUS_LOCK_PLAN §3-4).

Append-only JSONL of custody events. The author (solving context) is
mechanically separated from the custodian (controller) and the scorer (fresh
context): the bundle assembler records exactly which paths the solver received,
and ANY attempted access to a custodian-only path is logged as a breach event
and refuses the round. Oracle hash visibility with content hiding is the
design default.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

EVENT_KINDS = (
    "freeze_verified",
    "bundle_assembled",
    "solving_started",
    "solving_finished",
    "scoring_started",
    "oracle_accessed",
    "freeze_breach",
    "round_refused",
    # wave1-1-semantic-gate-1: round-level Faultness Gate adjudication
    "faultness_gate_applied",
    "semantic_coherence_checked",
)


class CustodyBreach(RuntimeError):
    pass


class CustodyLog:
    def __init__(self, path: Path, oracle_prefixes: tuple[str, ...] = ("oracle/",)):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.oracle_prefixes = oracle_prefixes
        self._lock = threading.Lock()
        self._seq = 0

    def event(self, kind: str, **detail) -> dict:
        if kind not in EVENT_KINDS:
            raise ValueError(f"custody event kind {kind!r} outside the vocabulary")
        from ..schemas import canonical_json_bytes
        from datetime import datetime, timezone

        row = {
            "seq": None,
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(),
            **detail,
        }
        with self._lock:
            self._seq += 1
            row["seq"] = self._seq
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json_bytes(row).decode("utf-8") + "\n")
        return row

    def check_path_allowed_for_solver(self, relpath: str) -> None:
        """Refuse + record any solver access to a custodian-only path."""
        if any(relpath.startswith(p) for p in self.oracle_prefixes):
            self.event("freeze_breach", path=relpath, actor="solver")
            raise CustodyBreach(
                f"solver attempted access to custodian-only path {relpath!r} — "
                "round refused (oracle isolation, S0_GOAL_ADR §4)"
            )

    def assemble_bundle(self, bundle_dir: Path, file_list: list[tuple[str, Path]], freeze_id: str) -> dict:
        """Materialize the solver bundle: ONLY solver-visible files, copied
        byte-for-byte from the frozen round tree. Records the bundle manifest
        (relpath -> sha256) as a custody event."""
        from ..schemas import file_sha256

        bundle_dir = Path(bundle_dir)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for rel, src in file_list:
            self.check_path_allowed_for_solver(rel)
            dst = bundle_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(Path(src).read_bytes())
            manifest[rel] = file_sha256(dst)
        row = self.event(
            "bundle_assembled",
            freeze_id=freeze_id,
            bundle_dir=str(bundle_dir),
            files=manifest,
        )
        return row

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def breaches(self) -> list[dict]:
        return [r for r in self.rows() if r["kind"] == "freeze_breach"]
