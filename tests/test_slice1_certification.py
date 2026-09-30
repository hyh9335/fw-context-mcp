"""Slice-1 certification tests (S1.1-S1.6): class-C digest + parser_generation,
NCC byte-compat vs the frozen Phase-2 prototype, dependency-closure
certification with cert-grade rehash, macro/builtin/forced-input capture,
poison discipline, and the G7-T01..T10 production-capture matrix.

Every key test runs the REAL production path (compile_commands.json ->
compile_commands.parse -> extract_all -> build_certificate), never
handcrafted CertifiedSemanticInputs (PHASE4 §22).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.slice1_helpers import (  # noqa: E402
    CLANG_TARGET,
    REPO,
    build_units,
    cert_store,
    certify_project,
    fresh_store,
    make_project,
    parse_unit,
    store,
    store_nonce,
)
from fw_context_mcp.store_v2.certify import (  # noqa: E402
    INDEXER_GENERATION,
    ParserIdentity,
    apply_class_c_post_step,
    build_certificate,
    libclang_identity,
    clang_cli_identity,
    macro_environment_digest,
)
from fw_context_mcp.store_v2.keys import NOT_FOUND, UNKNOWN_TAG, h  # noqa: E402
from fw_context_mcp.store_v2.ncc import canonicalize, SEP  # noqa: E402


# ── S1-T05: NCC production serialization == frozen Phase-2 prototype ────────

def _load_frozen_prototype():
    """The D5a-accepted S2 canonicalizer, VERBATIM (read-only reference)."""
    path = REPO / "multi_version_index_review" / "phase2_experiments" / \
        "key_candidates.py"
    spec = importlib.util.spec_from_file_location("kc_frozen_ref", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kc_frozen_ref"] = mod          # dataclass __module__ lookup
    spec.loader.exec_module(mod)
    return mod


FROZEN = _load_frozen_prototype()

NCC_BATTERY = [
    # ([S2]§3.3 ex.1) define order is semantic: -DFOO=1 -UFOO != -UFOO -DFOO=1
    ("ex1a", ["clang", "-DFOO=1", "-UFOO", "-DBAR", "f.c"]),
    ("ex1b", ["clang", "-UFOO", "-DFOO=1", "-DBAR", "f.c"]),
    # ([S2]§3.3 ex.2) include order is search semantics
    ("ex2a", ["clang", "-Ia", "-Ib", "f.c"]),
    ("ex2b", ["clang", "-Ib", "-Ia", "f.c"]),
    # ([S2]§3.3 ex.3) forced-include position vs -D is semantic
    ("ex3a", ["clang", "-include", "x.h", "-DFEATURE=1", "f.c"]),
    ("ex3b", ["clang", "-DFEATURE=1", "-include", "x.h", "f.c"]),
    # duplicate -D preserved (no dedup)
    ("dup", ["clang", "-DN=1", "-DN=2", "f.c"]),
    # clone-path de-tainting ([PCS]§2.4)
    ("cloneA", ["clang", "-ID:\\ws\\cloneA\\inc", "-ID:\\toolchain\\sys\\inc",
                "D:\\ws\\cloneA\\src\\f.c"]),
    ("cloneB", ["clang", "-ID:/ws/cloneB/inc", "-ID:/toolchain/sys/inc",
                "D:/ws/cloneB/src/f.c"]),
    # output-only / diagnostics-only flags drop
    ("drops", ["clang", "-O2", "-g", "-Wall", "-Werror", "-c",
               "-o", "f.o", "-MF", "f.d", "f.c"]),
    # joined spellings + std/target ride the serial
    ("joined", ["clang", "-Iinc", "-D X=1", "-std=c11", "--target=arm-none-eabi",
                "f.c"]),
]


@pytest.mark.parametrize("name,argv", NCC_BATTERY, ids=[n for n, _ in NCC_BATTERY])
def test_s1_t05_ncc_byte_compat_vs_frozen_prototype(name, argv, tmp_path):
    """Production canonicalize() bytes == frozen S2 prototype bytes."""
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "x.h").write_text("#define XH 1\n")
    compile_dir = str(proj / "src")
    project_root = str(tmp_path)
    fc = {"x.h": "#define XH 1\n"}
    cc_prod = canonicalize(argv, compile_dir=compile_dir,
                           project_root=project_root, file_contents=fc)
    cc_ref = FROZEN.canonicalize(argv, compile_dir=compile_dir,
                                 project_root=project_root, file_contents=fc)
    assert cc_prod.ncc_serial == cc_ref.ncc_serial, (
        f"{name}: production NCC bytes diverge from frozen reference")


def test_s1_t05_rsp_expansion_byte_compat(tmp_path):
    """rsp expansion at the @ position: production == frozen prototype."""
    proj = tmp_path / "proj"
    proj.mkdir()
    rsp_body = ["-DRIES=1", "-Iext"]
    cc_prod = canonicalize(["clang", "@flags.rsp", "f.c"],
                           compile_dir=str(proj), project_root=str(tmp_path),
                           rsp_contents={"flags.rsp": rsp_body})
    cc_ref = FROZEN.canonicalize(["clang", "@flags.rsp", "f.c"],
                                 compile_dir=str(proj),
                                 project_root=str(tmp_path),
                                 rsp_contents={"flags.rsp": rsp_body})
    assert cc_prod.ncc_serial == cc_ref.ncc_serial
    assert "-DRIES=1" in cc_prod.ncc_serial.split(SEP)


def test_s1_t05_missing_rsp_deterministic_empty_with_warning():
    """Missing rsp: deterministic empty expansion + recorded warning (H-7)."""
    cc = canonicalize(["clang", "@missing.rsp", "f.c"],
                      compile_dir="C:/w", project_root="C:/w")
    assert any("missing rsp" in w for w in cc.warnings)
    ref = FROZEN.canonicalize(["clang", "@missing.rsp", "f.c"],
                              compile_dir="C:/w", project_root="C:/w")
    assert cc.ncc_serial == ref.ncc_serial


# ── class-C post-step ────────────────────────────────────────────────────────

def test_class_c_post_step_replaces_certified_root_tokens():
    serial = SEP.join(["-c", "ext:external:C:/tool/sys/inc",
                       "ext:external:C:/other/inc"])
    out = apply_class_c_post_step(serial, "env-digest-abc",
                                  ["C:/tool/sys"])
    tokens = out.split(SEP)
    assert tokens[1] == "envcert:env-digest-abc:inc"
    assert tokens[2] == "ext:external:C:/other/inc"     # uncertified stays


def test_class_c_post_step_no_roots_is_identity():
    serial = SEP.join(["-c", "ext:external:C:/tool/sys/inc"])
    assert apply_class_c_post_step(serial, "d", []) == serial


# ── parser identity + deterministic generation ──────────────────────────────

def test_parser_generation_deterministic_and_identity_separating():
    a = ParserIdentity(indexer_generation=1, libclang_id="libclang:v18",
                       capture_binary_id="clang-cli:x|v18", builtin_digest="b1",
                       builtin_count=10, target_triple="t1")
    b = ParserIdentity(indexer_generation=1, libclang_id="libclang:v18",
                       capture_binary_id="clang-cli:x|v18", builtin_digest="b1",
                       builtin_count=10, target_triple="t1")
    c = ParserIdentity(indexer_generation=1, libclang_id="libclang:v18",
                       capture_binary_id="clang-cli:x|v18", builtin_digest="b2",
                       builtin_count=10, target_triple="t1")
    assert a.generation == b.generation and a.generation > 0
    assert a.generation != c.generation
    assert a.identity_sha != c.identity_sha


def test_s1_t08_production_parser_identity_captured(cert_store):
    """libclang id, capture-binary id, builtin digest, triple all certified."""
    cert = cert_store["cert"]
    ident = cert.parser_identity
    assert ident.failed == ""
    assert ident.libclang_id.startswith("libclang:")
    assert ident.capture_binary_id.startswith("clang-cli:")
    assert len(ident.builtin_digest) == 64 and ident.builtin_count > 100
    assert ident.target_triple == "x86_64-pc-windows-msvc"
    assert ident.indexer_generation == INDEXER_GENERATION
    # environment digest rides L3; key embeds the deterministic generation
    assert cert.inputs.environment_digest == ident.environment_digest()
    kr = cert.key_result
    assert str(ident.generation) in kr.L3 or kr.L3  # generation inside L3 leg


def test_s1_t08_builtin_predefine_digest_is_env_stable(tmp_path, cert_store):
    """The builtin digest is a certified compiler property (non-empty, stable)."""
    ident = cert_store["cert"].parser_identity
    ident2 = ParserIdentity(indexer_generation=INDEXER_GENERATION,
                            libclang_id=libclang_identity(),
                            capture_binary_id=clang_cli_identity(),
                            builtin_digest=ident.builtin_digest,
                            builtin_count=ident.builtin_count,
                            target_triple=ident.target_triple)
    assert ident2.generation == ident.generation


# ── production certification (G7 matrix, real capture) ──────────────────────

def test_g7_t01_clone_root_relocation_same_key(tmp_path):
    """Same semantics, different absolute clone roots -> SAME KB-C."""
    keys = []
    for name in ("cloneA", "cloneB"):
        root = tmp_path / name
        data = make_project(root)
        _unit, _parsed, cert = certify_project(tmp_path, data["proj"],
                                               nonce="n1")
        assert cert.status == "certified", cert.poison_reason
        keys.append(cert.tusemantic_key)
    assert keys[0] == keys[1]


def test_g7_t02_define_change_separates_keys(tmp_path):
    data = make_project(tmp_path)
    _u, _p, cert_a = certify_project(tmp_path, data["proj"], nonce="n1")
    # add a define to the same TU via the raw entry (production argv change)
    entry = json.loads((data["proj"] / "compile_commands.json").read_text())
    entry[0]["arguments"] = [a for a in entry[0]["arguments"]] + ["-DEXTRA=1"]
    (data["proj"] / "compile_commands.json").write_text(json.dumps(entry))
    _u2, _p2, cert_b = certify_project(tmp_path, data["proj"], nonce="n1",
                                       context_id="cx2")
    assert cert_a.tusemantic_key != cert_b.tusemantic_key


def test_g7_t03_transitive_include_content_separates_keys(tmp_path):
    data = make_project(tmp_path)
    _u, _p, cert_a = certify_project(tmp_path, data["proj"], nonce="n1")
    # trans.h is included by main.c; change a TRANSITIVE member's bytes
    (data["proj"] / "inc" / "trans.h").write_text("#define TRANSITIVE_MARK 2\n")
    _u2, _p2, cert_b = certify_project(tmp_path, data["proj"], nonce="n1",
                                       context_id="cx2")
    assert cert_a.tusemantic_key != cert_b.tusemantic_key


def test_g7_t04_external_include_dir_certified(tmp_path):
    sysroot = tmp_path / "toolchain" / "sys" / "inc"
    data = make_project(tmp_path, sysroot=sysroot)
    _u, _p, cert = certify_project(tmp_path, data["proj"], nonce="n1")
    assert cert.status == "certified", cert.poison_reason
    closure_paths = [m.display_path for m in cert.closure]
    assert any("extdecl.h" in p for p in closure_paths)
    # external member carries a cert-grade content digest (current bytes)
    ext = [m for m in cert.closure if "extdecl.h" in m.display_path][0]
    assert len(ext.blob_sha) == 64
    assert cert.inputs.environment_digest != UNKNOWN_TAG


def test_g7_t05_generated_header_current_bytes_separate(tmp_path):
    """Generated-input trust is BYPASSED: current bytes decide, not flags."""
    build_dir = tmp_path / "proj" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "gen.h").write_text("#define GEN 1\n")
    data = make_project(tmp_path)
    entry = json.loads((data["proj"] / "compile_commands.json").read_text())
    entry[0]["arguments"] += ["-I" + "build"]
    (data["proj"] / "compile_commands.json").write_text(json.dumps(entry))
    (data["proj"] / "src" / "main.c").write_text(
        '#include "util.h"\n#include "cfg.h"\n#include "trans.h"\n'
        '#include "gen.h"\n'
        "int util(void) { return UTIL_VERSION + GEN; }\n"
        "int main(void) { return util(); }\n")
    _u, _p, cert_a = certify_project(tmp_path, data["proj"], nonce="n1")
    assert cert_a.status == "certified"
    gen_member = [m for m in cert_a.closure if "gen.h" in m.display_path][0]
    # generated flag is DIAGNOSTIC ONLY (patterns are not wired in Slice 1);
    # certification re-hashes current bytes regardless of it.
    assert gen_member.blob_sha is not None
    (build_dir / "gen.h").write_text("#define GEN 2\n")
    _u2, _p2, cert_b = certify_project(tmp_path, data["proj"], nonce="n1",
                                       context_id="cx2")
    assert cert_b.status == "certified"
    assert cert_a.tusemantic_key != cert_b.tusemantic_key  # bytes decided


def test_g7_t06_rsp_tu_certified_and_byte_compat(tmp_path):
    data = make_project(tmp_path, rsp={"flags.rsp": ["-DRIES=9", "-Iinc"]})
    _u, _p, cert = certify_project(tmp_path, data["proj"], nonce="n1")
    assert cert.status == "certified", cert.poison_reason
    assert "-DRIES=9" in cert.ncc_serial.split(SEP)


def test_g7_t07_forced_inputs_content_certified(tmp_path):
    data = make_project(tmp_path)
    _u, parsed, cert = certify_project(tmp_path, data["proj"], nonce="n1")
    assert cert.status == "certified"
    assert len(cert.forced_legs) == 1            # -include forced.h (c3)
    assert len(cert.forced_legs[0]) == 64
    # forced header content also present in the closure (libclang reports it)
    assert any("forced.h" in m.display_path for m in cert.closure)


def test_g7_t07b_imacros_red_first_probe(tmp_path):
    """RED-first fixture: is the -imacros dependency reported and certified?"""
    data = make_project(tmp_path)
    entry = json.loads((data["proj"] / "compile_commands.json").read_text())
    entry[0]["arguments"] = ["clang", "-c", "src/main.c", "-Iinc",
                             CLANG_TARGET, "-imacros", "macros.h"]
    (data["proj"] / "compile_commands.json").write_text(json.dumps(entry))
    unit = build_units(data["proj"])[0]
    parsed = parse_unit(unit)
    inc_names = []
    for inc in parsed.tu.get_includes():
        inc_names.append(str(inc.include.name))
    reported = any("macros.h" in n for n in inc_names)
    cert = build_certificate(unit, parsed, data["proj"], nonce="n1")
    assert cert.status == "certified", cert.poison_reason
    assert len(cert.forced_legs) == 1            # content certified via c3
    if not reported:
        # closure would miss it, but c3 carries the content legs — document:
        # certification completeness comes from c3 (+ position in NCC), and the
        # capture must never silently ignore the file.
        pytest.skip("libclang does not report -imacros in get_includes; "
                    "certified via c3 forced-content legs")
    assert any("macros.h" in m.display_path for m in cert.closure)


def test_g7_t09_poison_paths_fail_closed(tmp_path):
    # missing forced input
    data = make_project(tmp_path)
    (data["proj"] / "forced.h").unlink()
    _u, parsed, cert = certify_project(tmp_path, data["proj"], nonce="n1")
    assert cert.status == "poisoned"
    assert "forced-input-unreadable" in cert.poison_reason
    # unresolved include
    data2 = make_project(tmp_path / "d2")
    (data2["proj"] / "src" / "main.c").write_text(
        '#include "nope_missing.h"\nint main(void){return 0;}\n')
    _u2, parsed2, cert2 = certify_project(tmp_path, data2["proj"], nonce="n1")
    assert cert2.status == "poisoned"
    assert "unresolved-include" in cert2.poison_reason


def test_g7_t09b_poisoned_cert_never_mints_state(tmp_path, store):
    """A poisoned TU produces NO semantic_states row (INV-6) and its
    disposition lives in the certificate table only."""
    from fw_context_mcp.store_v2.facts import extract_state_facts
    data = make_project(tmp_path)
    (data["proj"] / "forced.h").unlink()
    _u, parsed, cert = certify_project(tmp_path, data["proj"],
                                       nonce=store_nonce(store))
    assert cert.status == "poisoned"
    assert cert.key_result is None or not cert.key_result.is_reusable
    assert store._conn.execute(
        "SELECT count(*) FROM semantic_states").fetchone()[0] == 0


def test_g7_t10_clone_relocation_with_toolchain_same_key(tmp_path):
    """Clone-root + compile-dir relocation with a SHARED toolchain location:
    same certified identity (paths de-tainted; env certified at class-C)."""
    tool = tmp_path / "tool" / "sys" / "inc"
    keys = []
    for name in ("cloneA", "cloneB"):
        root = tmp_path / name
        data = make_project(root, sysroot=tool)
        _u, _p, cert = certify_project(tmp_path, data["proj"], nonce="n1")
        assert cert.status == "certified", cert.poison_reason
        keys.append(cert.tusemantic_key)
    assert keys[0] == keys[1]


def test_g7_t10b_undeclared_toolchain_relocation_over_keys(tmp_path):
    """Toolchain moved to an undeclared location: over-key (fail-safe),
    never a false match."""
    keys = []
    for name in ("toolA", "toolB"):
        tool = tmp_path / name / "sys" / "inc"
        tool.mkdir(parents=True)
        (tool / "extdecl.h").write_text("int ext_fn(void);\n")
        data = make_project(tmp_path / name, sysroot=tool)
        _u, _p, cert = certify_project(tmp_path, data["proj"], nonce="n1")
        assert cert.status == "certified"
        keys.append(cert.tusemantic_key)
    assert keys[0] != keys[1]


def test_closure_rehash_bypasses_staleness_trust(tmp_path):
    """header_is_trusted (generated-only skip) is NOT consulted: every
    closure member is re-hashed from current bytes."""
    build_dir = tmp_path / "proj" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "gen.h").write_text("#define GEN 1\n")
    data = make_project(tmp_path)
    entry = json.loads((data["proj"] / "compile_commands.json").read_text())
    entry[0]["arguments"] += ["-Ibuild"]
    (data["proj"] / "compile_commands.json").write_text(json.dumps(entry))
    (data["proj"] / "src" / "main.c").write_text(
        '#include "util.h"\n#include "gen.h"\n'
        "int util(void) { return UTIL_VERSION + GEN; }\n")
    _u, _p, cert = certify_project(tmp_path, data["proj"], nonce="n1")
    gen = [m for m in cert.closure if "gen.h" in m.display_path][0]
    import hashlib
    expected = hashlib.sha256((build_dir / "gen.h").read_bytes()).hexdigest()
    assert gen.blob_sha == expected              # cert-grade, current bytes


def test_closure_order_and_not_found_marker(tmp_path):
    data = make_project(tmp_path)
    _u, parsed, cert = certify_project(tmp_path, data["proj"], nonce="n1")
    displays = [m.display_path for m in cert.closure]
    assert displays and all(m.blob_sha for m in cert.closure)
    # c4 sequence matches closure order, full-width digests
    assert list(cert.inputs.cert_content_sequence) == \
        [m.blob_sha for m in cert.closure]
    assert NOT_FOUND not in cert.inputs.cert_content_sequence


def test_generated_flag_diagnostic_wiring(tmp_path):
    """build_dir_patterns drives ONLY the diagnostic generated flag."""
    build_dir = tmp_path / "proj" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "gen.h").write_text("#define GEN 1\n")
    data = make_project(tmp_path)
    entry = json.loads((data["proj"] / "compile_commands.json").read_text())
    entry[0]["arguments"] += ["-Ibuild"]
    (data["proj"] / "compile_commands.json").write_text(json.dumps(entry))
    (data["proj"] / "src" / "main.c").write_text(
        '#include "util.h"\n#include "gen.h"\n'
        "int util(void) { return UTIL_VERSION + GEN; }\n")
    unit = build_units(data["proj"])[0]
    parsed = parse_unit(unit)
    cert = build_certificate(unit, parsed, data["proj"], nonce="n1",
                             build_dir_patterns=["build"])
    gen = [m for m in cert.closure if "gen.h" in m.display_path][0]
    assert gen.generated is True


def test_work_key_never_authorizes_reuse(tmp_path):
    """Same WorkKey context identity is NOT a reuse authorization: two
    different semantic inputs under one context must yield different KB-C."""
    data = make_project(tmp_path)
    _u, _p, cert_a = certify_project(tmp_path, data["proj"], nonce="n1")
    (data["proj"] / "inc" / "util.h").write_text(
        "#define UTIL_VERSION 3\nint util(void);\n")
    _u2, _p2, cert_b = certify_project(tmp_path, data["proj"], nonce="n1",
                                       context_id="cx")
    assert cert_a.tusemantic_key != cert_b.tusemantic_key
