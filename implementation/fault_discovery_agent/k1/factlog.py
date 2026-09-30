"""Append-only K1 fact log + refusal log (JSONL, immutable by convention).

Every K1 surface call lands here exactly once:
- an allowed call -> one FactRecord row (schema-valid, freeze_id stamped);
- a refused call  -> one refusal row (machine-readable, taxonomy-coded).
Rows are appended; corrections are NEW rows, never edits. The fact log is the
replayable query chain required by E3.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ..schemas import canonical_json_bytes, validate
from ..schemas.models import new_fact_record

# Error taxonomy (Wave-1 mandate: every failure carries a machine code).
ERROR_TAXONOMY = {
    "E-TIER-SCOPE": "T0 out-of-scope use (auto-downgrade or refusal)",
    "E-TIER-FALLBACK": "fallback/suggestion provenance rejected (G6)",
    "E-TIER-TRUNCATED": "truncation => lower_bound only (G7)",
    "E-TIER-MACRO-VALUE": "macro-value semantics degraded (G8)",
    "E-TIER-FRAGMENT": "single-line/fragment discipline (G4)",
    "E-BUILD-SNAPSHOT": "build snapshot not the round's pinned build (C1/GA-4)",
    "E-CHANNEL-UNAVAILABLE": "independent channel instrument unavailable",
    "E-COVERAGE": "index coverage unproven — empty substrate answer is NOT absence evidence",
    "E-SCHEMA": "fact candidate failed frozen schema validation",
    "E-SUBSTRATE": "substrate invocation error",
}


class FactLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._index: dict[str, dict] = {}
        self._seq = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                self._seq = max(self._seq, row.get("seq", 0))
                if row.get("kind") == "fact":
                    self._index[row["record"]["fact_id"]] = row["record"]

    def _append(self, row: dict) -> None:
        with self._lock:
            self._seq += 1
            row["seq"] = self._seq
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json_bytes(row).decode("utf-8") + "\n")

    # ── fact minting (sole path) ────────────────────────────────────────────
    def mint(self, **kwargs) -> dict:
        """Create + validate + append one FactRecord. Returns the record."""
        record = new_fact_record(**kwargs)
        validate(record, "fact_record")
        self._append({"kind": "fact", "record": record})
        self._index[record["fact_id"]] = record
        return record

    # ── refusal rows ────────────────────────────────────────────────────────
    def refuse(
        self,
        *,
        tool: str,
        args: dict,
        code: str,
        detail: str,
        freeze_id: str,
        detector_findings: list | None = None,
    ) -> dict:
        if code not in ERROR_TAXONOMY:
            raise ValueError(f"refusal code {code!r} outside the taxonomy")
        row = {
            "kind": "refusal",
            "tool": tool,
            "args": args,
            "code": code,
            "detail": detail,
            "freeze_id": freeze_id,
            "detector_findings": detector_findings or [],
        }
        self._append(row)
        return row

    # ── accessors ───────────────────────────────────────────────────────────
    def get(self, fact_id: str) -> dict | None:
        return self._index.get(fact_id)

    def all_facts(self) -> list[dict]:
        return list(self._index.values())

    def facts_for_freeze(self, freeze_id: str) -> list[dict]:
        return [r for r in self._index.values() if r["freeze_id"] == freeze_id]

    def refusals(self, freeze_id: str | None = None) -> list[dict]:
        out = []
        if not self.path.exists():
            return out
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == "refusal" and (freeze_id is None or row["freeze_id"] == freeze_id):
                out.append(row)
        return out
