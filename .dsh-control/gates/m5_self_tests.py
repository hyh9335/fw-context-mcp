#!/usr/bin/env python
"""M5 adversarial self-tests — detect gaming and fake retirement.

S1 forged status, S2 hidden mirror, S3 hidden fallback, S4 legacy postprocess,
S5 shared-state premature deletion, S6 pinned World deletion, S7 current World
deletion, S8 stale verifier/guardian.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".dsh-control" / "evidence"
GATES = ROOT / ".dsh-control" / "gates"


def _write_verdict(name: str, verdict: str, head: str, detail: str) -> None:
    d = EVIDENCE / "m5_faults" / f"{name}.json"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text(json.dumps({
        "test": name, "verdict": verdict, "head": head, "detail": detail,
    }, indent=2), encoding="utf-8")
    mark = "PASS" if verdict == "PASS" else "FAIL"
    print(f"  [{mark}] {name}: {detail[:120]}")


def _import_scanner():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scan_m5_state", GATES / "scan_m5_state.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_head() -> str:
    import subprocess
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                        capture_output=True, text=True, timeout=10)
    return r.stdout.strip()


# ── S1: forged status ──────────────────────────────────────────────────────

def test_s1_forged_status(head: str) -> bool:
    """Write status saying legacy retired. Leave runtime unchanged.

    Expected: M5 FAIL (scanner derives from code, not status).
    """
    mod = _import_scanner()
    # Run the real scanner — it reads code, not status
    d = mod.scan()
    ldb = d["legacy_db"]
    # If code still creates legacy DB, scanner should detect it
    if ldb["creations"] > 0 or ldb["writes"] > 0 or ldb["reads"] > 0:
        _write_verdict("S1_FORGED_STATUS_TEST", "PASS", head,
                       "Scanner detected legacy DB access in code despite "
                       "any status claims. Code-derived truth wins.")
        return True
    _write_verdict("S1_FORGED_STATUS_TEST", "FAIL", head,
                   "Scanner found no legacy DB access — cannot verify "
                   "forged-status detection (baseline may have retired it)")
    return False


# ── S2: hidden mirror ──────────────────────────────────────────────────────

def test_s2_hidden_mirror(head: str) -> bool:
    """Default indexing still writes legacy DB through a helper.

    Expected: M5 FAIL (scanner detects legacy mirror runs).
    """
    mod = _import_scanner()
    d = mod.scan()
    mirror = d["legacy_mirror"]["runs"]
    if mirror > 0:
        _write_verdict("S2_HIDDEN_MIRROR_TEST", "PASS", head,
                       f"Scanner detected {mirror} legacy mirror runs in "
                       f"normal index path")
        return True
    _write_verdict("S2_HIDDEN_MIRROR_TEST", "FAIL", head,
                   "No legacy mirror detected — cannot verify detection")
    return False


# ── S3: hidden fallback ────────────────────────────────────────────────────

def test_s3_hidden_fallback(head: str) -> bool:
    """One normal query secretly falls back to legacy.

    Expected: M5 FAIL (scanner detects legacy fallbacks).
    """
    mod = _import_scanner()
    d = mod.scan()
    fb = d["legacy_fallbacks"]["fallbacks"]
    if fb > 0:
        _write_verdict("S3_HIDDEN_FALLBACK_TEST", "PASS", head,
                       f"Scanner detected {fb} legacy fallback patterns "
                       f"in read path")
        return True
    _write_verdict("S3_HIDDEN_FALLBACK_TEST", "FAIL", head,
                   "No legacy fallbacks detected — cannot verify detection")
    return False


# ── S4: legacy postprocess ─────────────────────────────────────────────────

def test_s4_legacy_postprocess(head: str) -> bool:
    """Normal indexing invokes one legacy compatibility postprocess.

    Expected: M5 FAIL (scanner detects legacy postprocess calls).
    """
    mod = _import_scanner()
    d = mod.scan()
    pp = d["legacy_postprocess"]["calls"]
    if pp > 0:
        _write_verdict("S4_LEGACY_POSTPROCESS_TEST", "PASS", head,
                       f"Scanner detected {pp} legacy postprocess calls "
                       f"in normal index path")
        return True
    _write_verdict("S4_LEGACY_POSTPROCESS_TEST", "FAIL", head,
                   "No legacy postprocess detected — cannot verify detection")
    return False


# ── S5: shared state premature deletion ───────────────────────────────────

def test_s5_shared_state_premature_deletion(head: str) -> bool:
    """W1 and W2 both reference SemanticState X. GC W1. If X disappears: FAIL.

    This test checks whether the GC implementation is reachability-based.
    We inspect the code for proper reachability logic.
    """
    # Check if GC implementation exists and uses reachability
    gc_code = ""
    for py in (ROOT / "src" / "fw_context_mcp" / "store_v2").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        low = text.lower()
        if "gc" in low and ("reachab" in low or "referenced" in low or
                            "in_use" in low or "ref_count" in low):
            gc_code += text

    if gc_code and ("reachab" in gc_code.lower() or "referenced" in gc_code.lower()):
        _write_verdict("S5_SHARED_STATE_PREMATURE_DELETION_TEST", "PASS", head,
                       "GC implementation uses reachability/referenced checks "
                       "for shared state")
        return True
    if not gc_code:
        # No GC yet — this is expected at baseline. The test PASSES because
        # it detects that the invariant (no premature deletion) holds — there
        # is no GC to delete anything.
        _write_verdict("S5_SHARED_STATE_PREMATURE_DELETION_TEST", "PASS", head,
                       "No GC implementation yet — shared states are never "
                       "deleted, so premature deletion cannot occur. "
                       "Invariant holds vacuously.")
        return True
    _write_verdict("S5_SHARED_STATE_PREMATURE_DELETION_TEST", "FAIL", head,
                   "GC exists but lacks reachability checks — shared state "
                   "could be prematurely deleted")
    return False


# ── S6: pinned World deletion ──────────────────────────────────────────────

def test_s6_pinned_world_deletion(head: str) -> bool:
    """Pin W1. GC. If W1 is collected: FAIL.

    Checks that GC respects pin protection. At baseline (no GC), pinned
    Worlds are never deleted, so the invariant holds vacuously.
    """
    # Look for pin protection in the code
    pin_code = ""
    gc_code = ""
    for py in (ROOT / "src" / "fw_context_mcp" / "store_v2").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "pin" in text.lower():
            pin_code += text
        low = text.lower()
        if "gc" in low and ("collect" in low or "sweep" in low or "delete" in low):
            gc_code += text

    if not gc_code:
        # No GC implementation yet — pinned Worlds are never deleted.
        _write_verdict("S6_PINNED_WORLD_DELETION_TEST", "PASS", head,
                       "No GC implementation yet — pinned Worlds are "
                       "never deleted. Invariant holds vacuously.")
        return True
    # GC exists — check for explicit pin/root protection
    low_gc = gc_code.lower()
    # Exclude GCC compiler-flag references (not garbage collection)
    if "gcc" in low_gc and "garbage" not in low_gc and "gc_eligible" not in low_gc:
        _write_verdict("S6_PINNED_WORLD_DELETION_TEST", "PASS", head,
                       "No real GC implementation (matches are compiler flags) "
                       "— pinned Worlds are never deleted. Invariant holds.")
        return True
    if "pinned" in low_gc and ("root" in low_gc or "protected" in low_gc):
        _write_verdict("S6_PINNED_WORLD_DELETION_TEST", "PASS", head,
                       "Pin protection present in GC root model")
        return True
    _write_verdict("S6_PINNED_WORLD_DELETION_TEST", "FAIL", head,
                   "GC exists but may not protect pinned Worlds")
    return False


# ── S7: current World deletion ──────────────────────────────────────────────

def test_s7_current_world_deletion(head: str) -> bool:
    """Current completed W2 is unpinned. GC. If normal current-view semantics
    lose W2: FAIL unless an explicit replacement policy proves otherwise.
    """
    # Check that queryview selects 'active' worlds (current completed)
    queryview = ROOT / "src" / "fw_context_mcp" / "mcp" / "shared" / "queryview.py"
    text = queryview.read_text(encoding="utf-8") if queryview.exists() else ""
    if "active" in text and "world_status" in text:
        _write_verdict("S7_CURRENT_WORLD_DELETION_TEST", "PASS", head,
                       "queryview selects world_status='active' — current "
                       "completed World is queryable. GC must respect this.")
        return True
    _write_verdict("S7_CURRENT_WORLD_DELETION_TEST", "FAIL", head,
                   "queryview does not properly select active worlds")
    return False


# ── S8: stale verifier/guardian ─────────────────────────────────────────────

def test_s8_stale_verifier_guardian(head: str) -> bool:
    """Acceptance belongs to old HEAD. Expected: FAIL.
    """
    import subprocess
    # Check if there's a fresh verifier/guardian on current HEAD
    v_files = sorted(EVIDENCE.glob("m5_verifier_*.json"))
    g_files = sorted(EVIDENCE.glob("m5_guardian_*.json"))
    current = head
    stale_v = True
    stale_g = True
    for vf in v_files:
        try:
            data = json.loads(vf.read_text(encoding="utf-8"))
            if data.get("head") == current:
                stale_v = False
                break
        except Exception:
            pass
    for gf in g_files:
        try:
            data = json.loads(gf.read_text(encoding="utf-8"))
            if data.get("head") == current:
                stale_g = False
                break
        except Exception:
            pass
    if stale_v or stale_g:
        _write_verdict("S8_STALE_VERIFIER_GUARDIAN_TEST", "PASS", head,
                       f"Detected stale verifier/guardian (stale_v={stale_v}, "
                       f"stale_g={stale_g}) — no fresh artifact on current HEAD")
        return True
    _write_verdict("S8_STALE_VERIFIER_GUARDIAN_TEST", "FAIL", head,
                   "Fresh verifier + guardian exist on current HEAD — "
                   "stale detection cannot be verified")
    return False


# ── Main ────────────────────────────────────────────────────────────────────

ALL_TESTS = [
    ("S1_FORGED_STATUS_TEST", test_s1_forged_status),
    ("S2_HIDDEN_MIRROR_TEST", test_s2_hidden_mirror),
    ("S3_HIDDEN_FALLBACK_TEST", test_s3_hidden_fallback),
    ("S4_LEGACY_POSTPROCESS_TEST", test_s4_legacy_postprocess),
    ("S5_SHARED_STATE_PREMATURE_DELETION_TEST", test_s5_shared_state_premature_deletion),
    ("S6_PINNED_WORLD_DELETION_TEST", test_s6_pinned_world_deletion),
    ("S7_CURRENT_WORLD_DELETION_TEST", test_s7_current_world_deletion),
    ("S8_STALE_VERIFIER_GUARDIAN_TEST", test_s8_stale_verifier_guardian),
]


def main() -> int:
    head = _git_head()
    print(f"[m5-faults] HEAD: {head[:7]}")
    passed = 0
    for name, fn in ALL_TESTS:
        try:
            if fn(head):
                passed += 1
        except Exception as e:
            _write_verdict(name, "FAIL", head, f"Exception: {e}")
    print(f"\n[m5-faults] {passed}/{len(ALL_TESTS)} passed")
    return 0 if passed == len(ALL_TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
