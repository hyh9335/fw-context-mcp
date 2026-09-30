"""Slice-0 identity tests — T01/T02/T03, determinism, domain separation, and
the golden-vector reproduction of the recorded 20-scenario D5a matrix.

Golden-vector rule (PHASE4 task §23): the recorded experiment outputs are the
oracle. The frozen D5a composition verdicts are reproduced by re-running the
verbatim reference harness (tests/store_v2_ref) in a scratch workspace and
comparing per-scenario expected/observed/verdict to the recorded
p3p1_matrix.json. The PRODUCT canonical composition (DECISION L52
``fwctx-l2c/v1``) is then validated verdict-level on the same scenario pairs —
no recorded artifact is ever mutated to go GREEN.
"""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import store_v2_bridge as B   # noqa: E402  (tests is a package)
import kb_realchannel as K               # noqa: E402  (bridge put REF_DIR on path)
from fw_context_mcp.store_v2.keys import (
    KEY_FORMAT,
    NOT_FOUND,
    ROOT_DOMAIN,
    UNKNOWN_TAG,
    CertifiedSemanticInputs,
    compute_tu_semantic_key,
    h,
    poison,
)

RECORDED_JSON = (B.REPO / "multi_version_index_review" / "phase3_experiments"
                 / "p3p1" / "out" / "p3p1_matrix.json")
MATRIX_PY = (B.REPO / "multi_version_index_review" / "phase3_experiments"
             / "p3p1" / "p3p1_matrix.py")
REF_DIR = B.REPO / "tests" / "store_v2_ref"
# scratch harness dir MUST be exactly three levels under the repo root
# (p3p1_matrix.py derives REPO as dirname^3 of its own location).
GOLDEN_DIR = (B.REPO / "multi_version_index_review" / "phase4"
              / "_golden_run")   # MUST stay exactly 3 levels under the repo root

requires_git = pytest.mark.skipif(shutil.which("git") is None,
                                  reason="git not available (S02 needs commit plumbing)")


@pytest.fixture(scope="module")
def golden():
    """Run the verbatim matrix harness in a scratch dir; return its module."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # p3p1_matrix.py (verbatim harness) + its already-verbatim imports
        shutil.copy2(MATRIX_PY, GOLDEN_DIR / "p3p1_matrix.py")
        for fname in ("kb_realchannel.py", "key_candidates_s2copy_d5a.py"):
            shutil.copy2(REF_DIR / fname, GOLDEN_DIR / fname)
        if str(GOLDEN_DIR) not in sys.path:
            sys.path.insert(0, str(GOLDEN_DIR))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "p3p1_matrix", GOLDEN_DIR / "p3p1_matrix.py")
        mat = importlib.util.module_from_spec(spec)
        sys.modules["p3p1_matrix"] = mat
        try:
            spec.loader.exec_module(mat)   # runs the 20 scenarios
        except SystemExit as e:            # the harness ends with sys.exit(0/1)
            if e.code not in (0, None):
                raise
        yield mat
    finally:
        if str(GOLDEN_DIR) in sys.path:
            sys.path.remove(str(GOLDEN_DIR))
        for mod in [m for m in list(sys.modules)
                    if m in ("p3p1_matrix", "kb_realchannel",
                             "key_candidates_s2copy_d5a", "key_candidates")]:
            del sys.modules[mod]
        shutil.rmtree(GOLDEN_DIR, ignore_errors=True)


# ══════════════════════ reference self-tests (Layer A) ══════════════════════

@pytest.mark.parametrize("script", ["key_candidates.py", "kb_realchannel.py"])
def test_reference_self_tests_pass(script):
    """The verbatim reference copies still pass their own self-test suites."""
    r = subprocess.run([sys.executable, script], cwd=REF_DIR,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


@requires_git
def test_golden_matrix_fresh_run_matches_recorded(golden):
    """Layer B: the fresh re-run reproduces the recorded 20/20 verdicts,
    observed maps and layer checks (byte-for-boolean on every recorded field)."""
    recorded = json.loads(RECORDED_JSON.read_text(encoding="utf-8"))
    rec_by_id = {r["id"]: r for r in recorded["results"]}
    fresh = {r["id"]: r for r in golden.RESULTS}
    assert set(rec_by_id) == set(fresh), "scenario id set changed"
    for sid, rec in rec_by_id.items():
        f = fresh[sid]
        assert f["verdict"] == "MATCH" == rec["verdict"], f"{sid}: {f}"
        assert f["expected"] == rec["expected"], sid
        assert f["observed"] == rec["observed"], sid
        assert f.get("layers", {}) == rec.get("layers", {}), sid


@requires_git
def test_golden_canonical_composition_matches_recorded_expectations(golden):
    """Layer C: the PRODUCT canonical composition (DECISION L52, content legs
    only) satisfies every recorded scenario expectation. Expectation mapping:
    the recorded KB_C verdict when present, else KB_F (S02b/S09 record KB_F
    only; the canonical composition is at least as sensitive on the legs those
    scenarios vary — L1/NCC or closure content)."""
    assert len(golden.RESULTS) == 20
    for sid, (va, vb) in B.SCENARIO_PAIRS.items():
        wa, wb = getattr(golden, va), getattr(golden, vb)
        rec = next(r for r in golden.RESULTS if r["id"] == sid)
        exp_map = rec["expected"]
        expect = exp_map["KB_C"] if "KB_C" in exp_map else exp_map["KB_F"]
        got = B.canonical_equal(wa, wb)
        assert got == expect, (
            f"{sid}: canonical composition {'equal' if got else 'differs'} "
            f"but recorded expectation {expect} ({rec['scenario']})")


@requires_git
def test_product_l0_l1_byte_identical_to_reference(golden):
    """Layer C(b): the product L0/L1 layer digests are byte-identical to the
    D5a reference layers for the same certified world (same formulas)."""
    for var in ("wA", "gA", "kA", "mA"):
        w = getattr(golden, var)
        prod = B.canonical_key(w)
        ref_l0 = h("L0", w.blob(), w.language)
        ref_l1 = h("L1", w.ncc())
        assert prod.L0 == ref_l0
        assert prod.L1 == ref_l1


# ══════════════════════ T01 — clone-root equivalence ═════════════════════════

def test_t01_clone_root_equivalence(tmp_path):
    """Same semantics under two absolute clone roots ⇒ byte-identical key."""
    a = str(tmp_path / "cloneA" / "fw").replace("\\", "/")
    b = str(tmp_path / "cloneB" / "fw").replace("\\", "/")
    wa = K.World("t01-a", a, a, B.tf_argv(), B.TF_FILES, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t01a")
    wb = K.World("t01-b", b, b, B.tf_argv(), B.TF_FILES, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t01b")
    ka, kb = B.canonical_key(wa), B.canonical_key(wb)
    assert ka.root == kb.root
    assert not ka.poisoned and not kb.poisoned
    assert ka.L0 == kb.L0 and ka.L1 == kb.L1 and ka.L2 == kb.L2 and ka.L3 == kb.L3
    # determinism: byte-identical on double compute
    assert B.canonical_key(wa) == ka


# ══════════════════════ T02 — semantic input difference ══════════════════════

def test_t02_semantic_input_difference_changes_key(tmp_path):
    """A semantic input difference (added -D) ⇒ different key, attributable:
    L1 (NCC) moves, L0 (blob) stays, and L2 moves too — the macro-state leg
    (c5) covers the -D/-U macro state by design (DECISION L52)."""
    root = str(tmp_path / "w").replace("\\", "/")
    wa = K.World("t02-a", root, root, B.tf_argv(), B.TF_FILES, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t02a")
    wb = K.World("t02-b", root, root,
                 B.tf_argv(extra=["-DTAP_MODE=1"]), B.TF_FILES, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t02b")
    ka, kb = B.canonical_key(wa), B.canonical_key(wb)
    assert ka.root != kb.root
    assert ka.L1 != kb.L1          # NCC layer carries the flag change
    assert ka.L0 == kb.L0          # blob unchanged
    assert ka.L2 != kb.L2          # macro-state leg (c5) covers the -D


def test_t02_output_only_flag_never_moves_any_layer(tmp_path):
    """Non-semantic flag (-g) ⇒ byte-identical key on every layer (S04)."""
    root = str(tmp_path / "w").replace("\\", "/")
    wa = K.World("t02d-a", root, root, B.tf_argv(), B.TF_FILES, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t02da")
    wb = K.World("t02d-b", root, root, B.tf_argv(extra=["-g"]), B.TF_FILES,
                 "src/driver.c", sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS,
                 nonce="t02db")
    ka, kb = B.canonical_key(wa), B.canonical_key(wb)
    # layers + root byte-identical (world_fp differs by design — it carries the
    # per-world nonce and participates only in poison digests)
    assert (ka.L0, ka.L1, ka.L2, ka.L3, ka.root) == \
           (kb.L0, kb.L1, kb.L2, kb.L3, kb.root)


def test_t02_header_content_difference_changes_closure(tmp_path):
    """A changed header byte moves the closure content leg (L2), not L0."""
    root = str(tmp_path / "w").replace("\\", "/")
    hdr = dict(B.TF_FILES)
    hdr_changed = dict(B.TF_FILES)
    hdr_changed["include/driver.h"] += "\n/* changed */\n"
    wa = K.World("t02c-a", root, root, B.tf_argv(), hdr, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t02ca")
    wb = K.World("t02c-b", root, root, B.tf_argv(), hdr_changed, "src/driver.c",
                 sysenv=B.SYS_STDINT, tc=B.TC, ps=B.PS, nonce="t02cb")
    ka, kb = B.canonical_key(wa), B.canonical_key(wb)
    assert ka.root != kb.root
    assert ka.L0 == kb.L0 and ka.L1 == kb.L1
    assert ka.L2 != kb.L2


# ══════════════════════ T03 — UNKNOWN fail-closed ════════════════════════════

def _base_inputs(**over) -> CertifiedSemanticInputs:
    legs = (h("hdr", "x"),)
    fields = dict(
        source_blob_sha=h("blob", "main.c"),
        language="c",
        ncc_serial="-std=c11",
        forced_content_legs=legs,
        cert_content_sequence=legs,
        macro_state_digest=h("c5", "map"),
        environment_digest=h("env", "certified"),
        parser_generation=1,
        toolchain_salt="tc-salt",
        parser_salt="ps-salt",
        nonce="w1",
    )
    fields.update(over)
    return CertifiedSemanticInputs(**fields)


def test_t03_unknown_environment_poisons():
    r = compute_tu_semantic_key(_base_inputs(environment_digest=UNKNOWN_TAG))
    assert r.poisoned == "environment-unknown"
    assert not r.is_reusable
    # poison binds nonce AND world components (H-2): two UNKNOWN worlds differ
    r2 = compute_tu_semantic_key(_base_inputs(environment_digest=UNKNOWN_TAG,
                                              nonce="w2"))
    assert r2.root != r.root
    # same world ⇒ deterministic poison
    assert compute_tu_semantic_key(
        _base_inputs(environment_digest=UNKNOWN_TAG)).root == r.root
    # UNKNOWN never equals the certified-empty environment (H-5)
    r_empty = compute_tu_semantic_key(_base_inputs(environment_digest=h("env", "")))
    assert r_empty.is_reusable and r_empty.root != r.root
    # poison never equals any well-formed layer/root value of the same world
    r_ok = compute_tu_semantic_key(_base_inputs())
    assert r.root not in (r_ok.L0, r_ok.L1, r_ok.L2, r_ok.L3, r_ok.root)


def test_t03_incomplete_closure_poisons_whole_key():
    r = compute_tu_semantic_key(_base_inputs(closure_complete=False))
    assert r.poisoned == "closure-incomplete"
    assert not r.is_reusable
    r2 = compute_tu_semantic_key(_base_inputs(closure_complete=False, nonce="w2"))
    assert r2.root != r.root


def test_t03_empty_salt_poisons():
    for field, comp in (("toolchain_salt", "empty-salt:toolchain"),
                        ("parser_salt", "empty-salt:parser")):
        r = compute_tu_semantic_key(_base_inputs(**{field: ""}))
        assert r.poisoned == comp
        assert not r.is_reusable


def test_t03_malformed_input_fails_closed():
    with pytest.raises(ValueError):
        _base_inputs(source_blob_sha="abc123")            # truncated hash
    with pytest.raises(ValueError):
        _base_inputs(nonce="")                             # no nonce
    with pytest.raises(ValueError):
        _base_inputs(macro_state_digest="zz" * 32)         # non-hex
    with pytest.raises(ValueError):
        _base_inputs(parser_generation=-1)                 # unanchored
    with pytest.raises(ValueError):
        _base_inputs(forced_content_legs=("deadbeef",))    # not full-width


def test_t03_rnf_marker_participates():
    """The RESOLVED-NOT-FOUND marker is hashed INTO the c4 content sequence
    (fail-closed marker never silently dropped)."""
    legs_ok = (h("h1", "a"), h("h2", "b"))
    legs_rnf = (h("h1", "a"), NOT_FOUND)
    a = compute_tu_semantic_key(_base_inputs(cert_content_sequence=legs_ok))
    b = compute_tu_semantic_key(_base_inputs(cert_content_sequence=legs_rnf))
    assert a.L2 != b.L2 and a.root != b.root


# ══════════════════════ determinism & world independence ═════════════════════

def test_well_formed_keys_are_world_independent():
    """The per-world nonce participates ONLY in poison digests: identical
    certified inputs under different nonces share the well-formed key."""
    a = compute_tu_semantic_key(_base_inputs(nonce="world-1"))
    b = compute_tu_semantic_key(_base_inputs(nonce="world-2"))
    assert a.root == b.root and a.world_fp != b.world_fp


def test_double_compute_byte_identical():
    i1 = _base_inputs()
    i2 = _base_inputs()
    assert compute_tu_semantic_key(i1) == compute_tu_semantic_key(i2)
    assert compute_tu_semantic_key(i1).root == h(
        ROOT_DOMAIN,
        h("L0", i1.source_blob_sha, i1.language),
        h("L1", i1.ncc_serial),
        h("fwctx-l2c/v1",
          h("c3", *i1.forced_content_legs),
          h("c4-content-seq", *i1.cert_content_sequence),
          i1.macro_state_digest, i1.key_format),
        h("L3", i1.toolchain_salt, i1.parser_salt,
          str(i1.parser_generation), i1.environment_digest),
    )


# ══════════════════════ domain separation ════════════════════════════════════

def test_key_format_separates_frozen_kbF_from_kbC():
    a = compute_tu_semantic_key(_base_inputs(key_format="fwctx-tukb/v1"))
    b = compute_tu_semantic_key(_base_inputs(key_format=KEY_FORMAT))
    assert a.root != b.root and a.L2 != b.L2
    assert a.L0 == b.L0 and a.L1 == b.L1   # layers unaffected, composition moves


def test_forced_leg_order_matters():
    x = compute_tu_semantic_key(_base_inputs(forced_content_legs=(h("x", "1"), h("y", "2"))))
    y = compute_tu_semantic_key(_base_inputs(forced_content_legs=(h("y", "2"), h("x", "1"))))
    assert x.L2 != y.L2 and x.root != y.root


def test_parser_generation_gate():
    """Same key requires the same anchored parser_generation (INV-3 co-column):
    pg moves ⇒ L3/root move; equal roots under differing pg are impossible."""
    a = compute_tu_semantic_key(_base_inputs(parser_generation=1))
    b = compute_tu_semantic_key(_base_inputs(parser_generation=2))
    assert a.root != b.root and a.L3 != b.L3
    assert a.parser_generation == 1 and b.parser_generation == 2


def test_layer_equality_never_implies_reuse():
    """Equal L0/L1 alone is NOT shared identity: no API returns a reuse verdict
    from layer equality, and equal layers with different compositions yield
    different roots (documenting [SKC]§3 anti-rule)."""
    a = compute_tu_semantic_key(_base_inputs())
    b = compute_tu_semantic_key(_base_inputs(macro_state_digest=h("c5", "other")))
    assert a.L0 == b.L0 and a.L1 == b.L1 and a.root != b.root


def test_poison_domain_never_collides_with_wellformed():
    """Every poison digest differs from the world's well-formed layers, and
    poison() output never equals a plain h() of the same parts (tag-carried)."""
    wf = "wf-fp"
    p = poison("some-component", "nonce-1", wf)
    direct = h("some-component", "nonce-1", wf)
    assert p != direct                      # UNKNOWN tag hashed in (R-K1(d))
    r = compute_tu_semantic_key(_base_inputs(environment_digest=UNKNOWN_TAG))
    ok = compute_tu_semantic_key(_base_inputs())
    assert len(r.root) == 64 and r.root != ok.root
    assert r.root not in (ok.L0, ok.L1, ok.L2, ok.L3)
