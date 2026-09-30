"""k2 subpackage — hypothesis-and-rule loop (deterministic; no LLM)."""
from .evidence_gate import GateDecision, GateReport, run_evidence_gate
from .loop import FAMILY_LIB_PATH, K2Loop, K2Outcome, load_family_library

__all__ = [
    "FAMILY_LIB_PATH",
    "GateDecision",
    "GateReport",
    "K2Loop",
    "K2Outcome",
    "load_family_library",
    "run_evidence_gate",
]
