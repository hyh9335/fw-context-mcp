"""Loader + validator for the three frozen Wave-1 schemas.

The schema files under ``fault_discovery_goal_architecture_agent_team/outputs/``
are the FROZEN contracts (design gate). This module loads them read-only and
validates instances with jsonschema (draft-07).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

# Repo root = two levels above this package's implementation/ directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_DIR = _REPO_ROOT / "fault_discovery_goal_architecture_agent_team" / "outputs"

SCHEMA_FILES = {
    "fact_record": "FACT_RECORD_SCHEMA.json",
    "mechanism_hypothesis": "MECHANISM_HYPOTHESIS_SCHEMA.json",
    "rulecard": "RULECARD_SCHEMA.json",
}

# Wave-1.1 semantic-gate schemas (package-local; the frozen design contracts
# under fault_discovery_goal_architecture_agent_team/outputs/ stay untouched).
PACKAGE_SCHEMA_FILES = {
    "rulecard_v1_1": Path(__file__).parent / "RULECARD_SCHEMA_V1_1.json",
}


class SchemaValidationError(ValueError):
    """Raised when an instance fails its frozen schema."""


@lru_cache(maxsize=None)
def load_schema(kind: str) -> dict:
    if kind in PACKAGE_SCHEMA_FILES:
        return json.loads(PACKAGE_SCHEMA_FILES[kind].read_text(encoding="utf-8"))
    try:
        filename = SCHEMA_FILES[kind]
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"unknown schema kind: {kind}") from exc
    return json.loads((SPEC_DIR / filename).read_text(encoding="utf-8"))


def validate(instance, kind: str) -> dict:
    """Validate *instance* against the frozen schema *kind*.

    Returns the instance on success; raises SchemaValidationError with the
    full validator report on failure.
    """
    schema = load_schema(kind)
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        msgs = []
        for e in errors[:10]:
            path = "/".join(str(p) for p in e.absolute_path) or "<root>"
            msgs.append(f"  {path}: {e.message}")
        raise SchemaValidationError(
            f"{kind} validation failed with {len(errors)} error(s):\n" + "\n".join(msgs)
        )
    return instance
