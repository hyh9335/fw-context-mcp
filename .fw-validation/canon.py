"""Canonicalization + comparison helpers for validation runs.

Rules (from prompts/V3_CROSS_PLATFORM.md):
MAY remove   : absolute root prefix, timing, volatile database integer IDs
MUST NOT     : context/build identity, symbol identity, relative location,
               edge kind/certainty, condition evaluation, variant/config
               provenance, semantic fingerprint
"""
import hashlib
import json
import re
from pathlib import Path

REPO = Path(r"D:\github\fw-context-mcp")

VOLATILE_KEYS = {"elapsed_s", "indexed_at", "first_indexed_at", "mtime", "db",
                 "project_id", "config_hash", "duration_ms", "timestamp"}


def strip_root(value, roots=()):
    """Replace absolute workspace-ish path prefixes with <ROOT>."""
    prefixes = [str(roots[i]) if roots else str(REPO) for i in range(max(1, len(roots)))]
    if not isinstance(value, str):
        return value
    out = value
    for p in prefixes:
        out = out.replace(p + "\\", "<ROOT>\\").replace(p + "/", "<ROOT>/").replace(p, "<ROOT>")
    return out


def canonicalize(obj, roots=()):
    """Recursively drop volatile fields and normalize path prefixes."""
    if isinstance(obj, dict):
        return {k: (None if k in VOLATILE_KEYS else canonicalize(v, roots))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [canonicalize(v, roots) for v in obj]
    if isinstance(obj, str):
        return strip_root(obj, roots)
    return obj


def canon_json_bytes(data: bytes) -> bytes:
    """Stable canonical form used for semantic stability checks (A6/A8)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return hashlib.sha256(data).hexdigest().encode()
    return json.dumps(canonicalize(obj), sort_keys=True, separators=(",", ":")).encode()


def load_expected(fixture_dir, name="expected.json"):
    return json.loads((Path(fixture_dir) / name).read_text(encoding="utf-8-sig"))


def record_result(out_dir, case_id, *, command, exit_code, expected, observed,
                  comparison, status, notes=None):
    """Persist one case result per templates/case_result.template.json spirit."""
    out = {
        "case_id": case_id,
        "command": command,
        "exit_code": exit_code,
        "expected": expected,
        "observed": observed,
        "comparison": comparison,
        "status": status,  # PASS | FAIL_PRODUCT | FAIL_ORACLE | EXPECTED_UNSUPPORTED | BLOCKED_ENVIRONMENT
        "notes": notes or [],
    }
    p = Path(out_dir) / f"{case_id}.result.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
