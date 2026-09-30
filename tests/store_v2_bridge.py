"""store_v2_bridge — D5a reference Worlds -> product canonical KB-C inputs.

TEST-TREE ONLY (rules in tests/store_v2_ref/README.md). This module is the
composition bridge required by the Slice-0 golden-vector contract:

* the D5a reference modules (verbatim copies) produce the certified fixture
  worlds and the FROZEN-composition verdicts (experiment truth);
* this bridge derives the PRODUCT composition inputs (DECISION L52
  ``fwctx-l2c/v1``: content legs only, paths excluded per A-1) from the SAME
  certified structures, so the product key mechanics can be validated
  verdict-level on the same 20-scenario matrix.

Nothing here runs production indexing: every input is fixture-certified, and
the macro-state leg carries the D5a scanner STUB (``c5-stub-scan``), honestly
labeled (G7 = production certification source, Slice 1).
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parent
REF_DIR = TESTS_DIR / "store_v2_ref"
# tests/ is a package (tests/__init__.py), so pytest puts the repo root on
# sys.path — the bridge re-adds tests/ and the ref dir so the verbatim
# experiment modules import exactly as they do in their home directories
# (kb_realchannel's top-level `from key_candidates_s2copy_d5a import ...`
# must keep working unmodified).
for _p in (str(TESTS_DIR), str(REF_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kb_realchannel as K  # noqa: E402  (verbatim D5a oracle, top-level import)

from fw_context_mcp.store_v2.keys import (  # noqa: E402
    CertifiedSemanticInputs,
    UNKNOWN_TAG,
    compute_tu_semantic_key,
)

TC = "gcc|13.2.0|x86_64-w64-mingw32"
PS = "libclang-18.1.1|fw-context-rows/3|keys-v1"

# STUB parser-generation marker (c7 rides L3 in the product composition as the
# anchored parser_generation; the D5a c7 stub is NOT an input to the canonical L2)
C7_STUB = "p3p1-c7-stub"


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


TF_FILES = {
    "src/driver.c": _read("test-firmware/src/driver.c"),
    "src/main.c": _read("test-firmware/src/main.c"),
    "include/driver.h": _read("test-firmware/include/driver.h"),
}
G01_FILES = {
    "master.c": _read("fwctx_validation_benchmark_pack/golden/G01_multi_tu_header/master.c"),
    "slave.c": _read("fwctx_validation_benchmark_pack/golden/G01_multi_tu_header/slave.c"),
    "common.h": _read("fwctx_validation_benchmark_pack/golden/G01_multi_tu_header/common.h"),
}
KO_FILES = {"main.c": _read("tests/fixtures/keep_only/main.c")}

SYS_STDINT = {
    "stdint.h": "typedef unsigned int uint32_t;\n"
                "typedef unsigned char uint8_t;\n"
                "#define UINT32_MAX 4294967295U\n",
}


def tf_argv(tu: str = "src/driver.c", *, o: str = "build/driver.o",
            extra: list[str] | None = None) -> list[str]:
    return ["gcc", "-std=c11", "-Iinclude", "-c", tu, "-o", o] + (extra or [])


def certified_inputs(w: "K.World", *, parser_generation: int = 1
                     ) -> CertifiedSemanticInputs:
    """Build the product's canonical KB-C inputs from a certified D5a world.

    Mapping (documented in SLICE0_SCHEMA_MAPPING.md §KB-C):
      source_blob_sha   = w.blob()                        (c1 content leg / L0)
      ncc_serial        = w.ncc()  (class-C post-step applied, certified)
      forced_content_legs = ordered content hashes of cert rows role=='forced'
                            (content legs only — resolved-path leg dropped, A-1)
      cert_content_sequence = ordered content legs of ALL cert rows in
                            directive order (marker preserved when unresolved;
                            spelling/resolved-path/role legs excluded, A-1)
      macro_state_digest = the D5a c5 STUB digest (scanner map; production
                            -dM -E capture is G7, Slice 1)
      environment_digest = certified env digest, or UNKNOWN_TAG when
                            env_unknown (H-5: UNKNOWN never equals certified)
      parser_generation  = anchored value (DECISION L52: rides L3, not L2)
    """
    cert = w.certificate()
    env_cert = w.env_cert()
    comp = K.dcf_components(rel_main=w.rel_main, blob=w.blob(), cert=cert,
                            argv=w.argv, rsp=w.rsp, env_cert=env_cert,
                            c7=C7_STUB)
    return CertifiedSemanticInputs(
        source_blob_sha=w.blob(),
        language=w.language,
        ncc_serial=w.ncc(),
        forced_content_legs=tuple(r.content for r in cert.rows if r.role == "forced"),
        cert_content_sequence=tuple(r.content for r in cert.rows),
        macro_state_digest=comp["c5"],
        environment_digest=UNKNOWN_TAG if env_cert is None else K.env_digest(env_cert),
        parser_generation=parser_generation,
        toolchain_salt=w.tc,
        parser_salt=w.ps,
        nonce=w.nonce,
    )


def canonical_key(w: "K.World", *, parser_generation: int = 1):
    """Product canonical KB-C key for a certified fixture world."""
    return compute_tu_semantic_key(certified_inputs(w, parser_generation=parser_generation))


def canonical_equal(a: "K.World", b: "K.World", *, parser_generation: int = 1) -> bool:
    """True iff the product canonical composition shares the identity value
    (same root AND same anchored parser_generation — INV-3 pair equality)."""
    ka = canonical_key(a, parser_generation=parser_generation)
    kb = canonical_key(b, parser_generation=parser_generation)
    return ka.root == kb.root and ka.parser_generation == kb.parser_generation


#: scenario id -> (matrix-global var A, matrix-global var B) — the exact pairs
#: the recorded p3p1 matrix compared (verified against p3p1_matrix.py source).
SCENARIO_PAIRS: dict[str, tuple[str, str]] = {
    "S01": ("wA", "wB"),
    "S01b": ("wA2", "wB2"),
    "S01c": ("gA", "gB"),
    "S01d": ("kA", "kB"),
    "S01e": ("mA", "mB"),
    "S02": ("wC1", "wC2"),
    "S02b": ("wC2", "wDirty"),
    "S03": ("wA", "wO"),
    "S04": ("wA", "wG"),
    "S04b": ("mA", "mF"),
    "S05": ("wA", "wR"),
    "S06": ("wA1", "wA3"),
    "S07": ("wS7a", "wS7b"),
    "S08": ("wS8a", "wS8b"),
    "S09": ("wN1", "wN2"),
    "C1": ("wD1", "wD2"),
    "C2": ("wA", "wH2"),
    "C3": ("wA", "wT2"),
    "C4": ("gM", "gS"),
    "C5": ("gHm", "gHs"),
}
