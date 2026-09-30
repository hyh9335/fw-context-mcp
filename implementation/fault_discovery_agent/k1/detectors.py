"""K1 detector hooks G1/G4/G6/G7/G8 (DETECTOR_ADMISSION_MATRIX.md; K1 §4).

Pure functions over (query, raw response) pairs. Each detector returns a list
of finding dicts: {detector, verdict: PASS|FAIL|DOWNGRADE|FLAG, detail}.
K1 runs them on EVERY surface response before fact minting; findings are
recorded into detector_checks and drive tier/refusal decisions. Never silent
(Wave-1 mandate): a triggered detector always lands in the record.
"""
from __future__ import annotations

from typing import Any

from . import substrate as _substrate

FALLBACK_MARKERS = ("_fallback", "_did_you_mean", "resolution_kind")
TRUNCATION_MARKERS = ("_source_truncated", "truncated", "_fallback")


def _iter_rows(payload: Any):
    """Yield dict rows from common substrate payload shapes."""
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                yield row
    elif isinstance(payload, dict):
        for key in ("results", "rows", "symbols", "chain", "macro_inputs"):
            sub = payload.get(key)
            if isinstance(sub, list):
                for row in sub:
                    if isinstance(row, dict):
                        yield row


def detect_g1_substrate_pin(head_commit: str | None, git_status_output: str | None) -> list[dict]:
    """G1: the running substrate must BE the pinned build (F-V1-003).

    Version strings (distribution metadata AND module __version__) are stale
    in this environment — that staleness IS the hazard class this detector
    exists for. The honest pin identity is therefore the git commit of the
    substrate checkout + a clean src tree:
      - head_commit == SUBSTRATE_COMMIT (byte-pinned implementation);
      - `git status --porcelain src` empty (no local mutations).
    """
    findings = []
    ok_pin = head_commit == _substrate.SUBSTRATE_COMMIT
    ok_tree = git_status_output is not None and git_status_output.strip() == ""
    verdict = "PASS" if (ok_pin and ok_tree) else "FAIL"
    findings.append({
        "detector": "G1",
        "verdict": verdict,
        "detail": {
            "head_commit": head_commit,
            "pinned_commit": _substrate.SUBSTRATE_COMMIT,
            "pinned_commit_ok": ok_pin,
            "src_tree_clean": ok_tree if git_status_output is not None else "git-unavailable",
        },
    })
    return findings


def detect_g4_fragment(tool: str, args: dict, payload: Any) -> list[dict]:
    """G4: single-line / fragment body discipline (F-V1-007).

    A definition window of one line, or a single-line cited region, routes to
    T2 discipline: load-bearing use requires a >=2-surface cross-check (E3).
    The detector FLAGS; the gateway enforces.
    """
    findings = []
    single_line = False
    detail: dict = {}
    if tool == "read_file":
        start = args.get("start_line") or 0
        end = args.get("end_line") or 0
        if start and end and end == start:
            single_line = True
            detail["window"] = [start, end]
    if tool in ("get_file_map", "lookup_symbol", "search_bodies"):
        for row in _iter_rows(payload):
            line = row.get("line")
            end_line = row.get("end_line")
            if line and end_line and end_line == line:
                single_line = True
                detail.setdefault("single_line_symbols", []).append(row.get("name"))
    if single_line:
        findings.append({
            "detector": "G4",
            "verdict": "FLAG",
            "detail": {**detail, "duty": "T2 discipline; load-bearing requires >=2-surface cross-check (E3)"},
        })
    return findings


def detect_g6_fallback(tool: str, payload: Any) -> list[dict]:
    """G6: fallback/suggestion provenance rejected (F-V2-002).

    ANY row carrying fallback/_did_you_mean provenance is NON-EVIDENCE: the
    gateway refuses to mint a fact from it. An explicit absence with an
    enumerated near-miss list is the required shape.
    """
    findings = []
    flagged = []
    for row in _iter_rows(payload):
        for marker in FALLBACK_MARKERS:
            if marker in row:
                flagged.append(row.get("name") or row.get("qualified_name") or "?")
                break
    # top-level fallback markers (whole-payload repair)
    if isinstance(payload, dict):
        for marker in ("_fallback", "_did_you_mean"):
            if payload.get(marker):
                flagged.append(f"<payload:{marker}>")
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and (row.get("_fallback") or row.get("_did_you_mean")):
                flagged.append(f"<row:{row.get('name', '?')}>")
    if flagged:
        findings.append({
            "detector": "G6",
            "verdict": "FAIL",
            "detail": {"flagged_rows": flagged, "duty": "reject as evidence; absence rows must carry explicit no-match + NON-EVIDENCE near-miss list"},
        })
    return findings


def detect_g7_truncation(tool: str, args: dict, payload: Any) -> list[dict]:
    """G7: truncation => incomplete, lower_bound only (F-V1-008).

    Any truncation marker, or a result count equal to the requested limit,
    forces completeness_status=truncated_lower_bound and refuses
    complete-enumeration claims.
    """
    findings = []
    truncated = False
    detail: dict = {}
    if isinstance(payload, dict) and payload.get("_source_truncated"):
        truncated = True
    for row in _iter_rows(payload):
        if row.get("_source_truncated"):
            truncated = True
            detail.setdefault("symbols", []).append(row.get("name"))
    if isinstance(payload, list):
        limit = args.get("limit")
        if limit and len(payload) >= int(limit):
            truncated = True
            detail["limit_reached"] = limit
    if truncated:
        findings.append({
            "detector": "G7",
            "verdict": "FLAG",
            "detail": {**detail, "duty": "completeness_status=truncated_lower_bound; complete-enumeration claims refused"},
        })
    return findings


def detect_g8_macro_value(tool: str, args: dict, payload: Any, intent: str | None = None) -> list[dict]:
    """G8: macro-VALUE semantics from condition text are degraded (F-V1-001).

    explain_condition carries condition TEXT and macro INPUTS, not evaluated
    values. Using it to claim what a macro expands to (or which branch
    compiled) is a T2 degraded claim, never load-bearing (E6).
    """
    findings = []
    if tool == "explain_condition":
        wants_value = (intent or "").lower() in (
            "macro_value", "branch_state", "expansion", "value",
        )
        if wants_value:
            findings.append({
                "detector": "G8",
                "verdict": "DOWNGRADE",
                "detail": {
                    "duty": "certainty=degraded, trust_class=T2, never load-bearing (E6)",
                    "intent": intent,
                },
            })
    return findings


def run_all_detectors(tool: str, args: dict, payload: Any, *, intent: str | None = None) -> list[dict]:
    findings: list[dict] = []
    findings += detect_g4_fragment(tool, args, payload)
    findings += detect_g6_fallback(tool, payload)
    findings += detect_g7_truncation(tool, args, payload)
    findings += detect_g8_macro_value(tool, args, payload, intent=intent)
    return findings
