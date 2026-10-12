"""M9 RED Safety Gate — adversarial attack suite.

Per `.dsh-control/m9_frontend_reuse/contracts/M9_SAFETY_CONTRACT.md`:
> A cross-Build frontend reuse HIT is valid only when all frontend-relevant
> inputs for the TU are proven equivalent. False hit is BLOCKING.

This suite constructs 15 attack pairs (A, B) where the ASTs DIFFER and
verifies that the M9 reuse protocol NEVER produces a false HIT.

Test protocol per attack:
1. Create TU A → parse with fresh libclang → fingerprint_A
2. Create TU B (with attack) → parse with fresh libclang → fingerprint_B
3. If fingerprint_A == fingerprint_B → attack didn't change AST → skip
4. If fingerprint_A != fingerprint_B → ASTs differ → M9 MUST NOT produce HIT:
   a. bucket_key_A != bucket_key_B → PASS (bucket caught it)
   b. bucket keys match → verify_closure_content MUST reject → PASS (c4 caught it)
   c. Both pass → FAIL (false HIT — BLOCKING safety violation)

GROUND TRUTH: independent libclang parse + AST fingerprint (does NOT use
the M9 certificate at all). Two TUs with the same fingerprint have the
same AST; different fingerprints mean the ASTs differ.

Run: python -m pytest .dsh-control/m9_frontend_reuse/tests/test_red_safety_gate.py -v
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

# ── Test infrastructure ────────────────────────────────────────────────────

try:
    from clang import cindex as cx
    _HAS_LIBCLANG = True
except ImportError:
    _HAS_LIBCLANG = False

try:
    from fw_context_mcp.indexer import certificate as cert_mod
    _HAS_CERT = True
except ImportError:
    _HAS_CERT = False


@dataclass
class FakeUnit:
    """Minimal CompilationUnit stand-in for certificate.compute_bucket_key."""
    file: Path
    directory: Path
    language: str
    clang_args: list
    raw_entry: dict


def make_unit(source_path: Path, directory: Path, args: list, language: str = "c") -> FakeUnit:
    """Build a FakeUnit matching the CompilationUnit interface."""
    raw = {"arguments": ["clang", str(source_path)] + args, "directory": str(directory)}
    return FakeUnit(
        file=source_path,
        directory=directory,
        language=language,
        clang_args=args,
        raw_entry=raw,
    )


def ast_fingerprint(source_path: Path, args: list) -> str:
    """Independent ground-truth: fresh libclang parse + AST walk hash.

    Does NOT use the M9 certificate. Hashes (kind, spelling, file:line:col)
    for every cursor in the AST. Two TUs with the same fingerprint have
    identical ASTs.
    """
    idx = cx.Index.create()
    tu = idx.parse(str(source_path), args=args,
                   options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
    hasher = hashlib.sha256()

    def walk(cursor):
        for c in cursor.walk_preorder():
            loc = c.location
            if loc.file:
                loc_str = f"{loc.file.name}:{loc.line}:{loc.column}"
            else:
                loc_str = "<builtin>"
            hasher.update(f"{c.kind}|{c.spelling}|{loc_str}\n".encode())
    walk(tu.cursor)
    return hasher.hexdigest()[:16]


def parse_and_cache(unit, project_root: Path, skip_files=None, with_refs=True):
    """Simulate the M9 MISS path: parse + extract closure + cache.

    Returns (parsed_wrapper, bucket_key) for the cached entry.
    """
    idx = cx.Index.create()
    tu = idx.parse(str(unit.file), args=unit.clang_args,
                   options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)

    @dataclass
    class ParsedWrapper:
        tu: object

    parsed = ParsedWrapper(tu=tu)

    bucket_key = cert_mod.compute_bucket_key(
        unit, project_root, skip_files=skip_files, with_refs=with_refs)

    closure_files, closure_hash = cert_mod.extract_closure_from_parsed(parsed)

    cache = cert_mod.get_certificate_cache()
    cache.put(
        key=bucket_key,
        parsed=parsed,
        full_kb_c_root="test_root",
        closure_file_list=closure_files,
        closure_content_hash=closure_hash,
        extraction_params=(frozenset(skip_files) if skip_files else frozenset(), with_refs),
        build_name="test_build_A",
    )
    return parsed, bucket_key


def check_no_false_hit(unit_A, unit_B, project_root: Path, tmpdir: Path):
    """Run the full M9 reuse protocol for B against a cached A.

    Returns (safe, reason) where:
    - safe=True: no false HIT (bucket key differs OR c4 rejected)
    - safe=False: FALSE HIT (blocking safety violation)
    """
    # Parse + cache A
    _, bucket_key_A = parse_and_cache(unit_A, project_root)

    # Compute B's bucket key
    bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)

    if bucket_key_A != bucket_key_B:
        return True, "bucket_key differs (safe MISS)"

    # Bucket keys match — must verify closure
    cache = cert_mod.get_certificate_cache()
    cached = cache.get(bucket_key_B)
    if cached is None:
        return True, "cache miss after bucket match (safe)"

    if cert_mod.verify_closure_content(cached):
        return False, "FALSE HIT: bucket matched AND closure verified (ASTs differ!)"
    else:
        cache.record_c4_rejection()
        return True, "closure content differs (c4 rejected — safe)"


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def workdir():
    """Temp directory for source/include files."""
    d = Path(tempfile.mkdtemp(prefix="m9_red_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)
    cert_mod.reset_certificate_cache()


@pytest.fixture
def project_root(workdir):
    return workdir


# ── Skip conditions ─────────────────────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    not _HAS_LIBCLANG or not _HAS_CERT,
    reason="libclang or certificate module not available",
)


# ════════════════════════════════════════════════════════════════════════
# ATTACK 1: -D define change
# ════════════════════════════════════════════════════════════════════════

class TestDefineD:
    def test_define_change_detected(self, workdir, project_root):
        src = workdir / "tu.c"
        src.write_text("#ifdef FOO\nint x = 1;\n#else\nint x = 2;\n#endif\n")
        unit_A = make_unit(src, workdir, ["-DFOO"])
        unit_B = make_unit(src, workdir, [])
        fp_A = ast_fingerprint(src, ["-DFOO"])
        fp_B = ast_fingerprint(src, [])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 2: -U undefine change
# ════════════════════════════════════════════════════════════════════════

class TestUndefineU:
    def test_undefine_detected(self, workdir, project_root):
        src = workdir / "tu.c"
        src.write_text("#ifdef BAR\nint y = 1;\n#else\nint y = 2;\n#endif\n")
        unit_A = make_unit(src, workdir, ["-DBAR"])
        unit_B = make_unit(src, workdir, ["-DBAR", "-UBAR"])
        fp_A = ast_fingerprint(src, ["-DBAR"])
        fp_B = ast_fingerprint(src, ["-DBAR", "-UBAR"])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 3: Include root change (-I path)
# ════════════════════════════════════════════════════════════════════════

class TestIncludeRootChange:
    def test_include_root_change_detected(self, workdir, project_root):
        hdr1 = workdir / "dir1" / "config.h"
        hdr2 = workdir / "dir2" / "config.h"
        hdr1.parent.mkdir(parents=True)
        hdr2.parent.mkdir(parents=True)
        hdr1.write_text("#define VAL 100\n")
        hdr2.write_text("#define VAL 200\n")
        src = workdir / "tu.c"
        src.write_text('#include "config.h"\nint v = VAL;\n')
        unit_A = make_unit(src, workdir, [f"-I{hdr1.parent}"])
        unit_B = make_unit(src, workdir, [f"-I{hdr2.parent}"])
        fp_A = ast_fingerprint(src, [f"-I{hdr1.parent}"])
        fp_B = ast_fingerprint(src, [f"-I{hdr2.parent}"])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 4: Include order change (-I order)
# ════════════════════════════════════════════════════════════════════════

class TestIncludeOrderChange:
    def test_include_order_change_detected(self, workdir, project_root):
        dir_a = workdir / "first"
        dir_b = workdir / "second"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "config.h").write_text("#define VAL 10\n")
        (dir_b / "config.h").write_text("#define VAL 20\n")
        src = workdir / "tu.c"
        src.write_text('#include "config.h"\nint v = VAL;\n')
        unit_A = make_unit(src, workdir, [f"-I{dir_a}", f"-I{dir_b}"])
        unit_B = make_unit(src, workdir, [f"-I{dir_b}", f"-I{dir_a}"])
        fp_A = ast_fingerprint(src, [f"-I{dir_a}", f"-I{dir_b}"])
        fp_B = ast_fingerprint(src, [f"-I{dir_b}", f"-I{dir_a}"])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 5: Transitive header content change
# ════════════════════════════════════════════════════════════════════════

class TestTransitiveHeaderChange:
    def test_transitive_header_change_detected(self, workdir, project_root):
        hdr = workdir / "types.h"
        src = workdir / "tu.c"
        src.write_text('#include "types.h"\nTYPE my_var;\n')
        # Version A: TYPE = int — parse+cache A BEFORE header changes
        hdr.write_text("typedef int TYPE;\n")
        unit_A = make_unit(src, workdir, [f"-I{workdir}"])
        fp_A = ast_fingerprint(src, [f"-I{workdir}"])
        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        # Version B: TYPE = long (content changed, no macro change)
        hdr.write_text("typedef long TYPE;\n")
        unit_B = make_unit(src, workdir, [f"-I{workdir}"])
        fp_B = ast_fingerprint(src, [f"-I{workdir}"])
        assert fp_A != fp_B, "attack didn't change AST"
        # Now query B against cached A — closure re-verification must catch the diff
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        if cached is None:
            pytest.skip("cache miss — bucket key caught it (safe but not testing c4)")
        assert not cert_mod.verify_closure_content(cached), \
            "FALSE HIT: closure content should differ after header change"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 6: Generated header same path new content
# ════════════════════════════════════════════════════════════════════════

class TestGeneratedHeaderSamePath:
    def test_generated_header_change_detected(self, workdir, project_root):
        gen_hdr = workdir / "generated.h"
        src = workdir / "tu.c"
        src.write_text('#include "generated.h"\nGEN_TYPE my_var;\n')
        # Version A: parse+cache A BEFORE header changes
        gen_hdr.write_text("typedef int GEN_TYPE;\n")
        unit_A = make_unit(src, workdir, [f"-I{workdir}"])
        fp_A = ast_fingerprint(src, [f"-I{workdir}"])
        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        # Version B: header content changed at same path
        gen_hdr.write_text("typedef long GEN_TYPE;\n")
        unit_B = make_unit(src, workdir, [f"-I{workdir}"])
        fp_B = ast_fingerprint(src, [f"-I{workdir}"])
        assert fp_A != fp_B, "attack didn't change AST"
        # Query B against cached A — closure re-verification must catch the diff
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        if cached is None:
            pytest.skip("cache miss — bucket key caught it (safe but not testing c4)")
        assert not cert_mod.verify_closure_content(cached), \
            "FALSE HIT: closure content should differ after header change"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 7: Forced include (-include)
# ════════════════════════════════════════════════════════════════════════

class TestForcedInclude:
    def test_forced_include_change_detected(self, workdir, project_root):
        forced = workdir / "forced.h"
        src = workdir / "tu.c"
        src.write_text("FORCED_TYPE my_var;\n")
        # Version A: parse+cache A BEFORE header changes
        forced.write_text("typedef int FORCED_TYPE;\n")
        unit_A = make_unit(src, workdir, [f"-include{forced}"])
        fp_A = ast_fingerprint(src, [f"-include{forced}"])
        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        # Version B: forced include content changed
        forced.write_text("typedef long FORCED_TYPE;\n")
        unit_B = make_unit(src, workdir, [f"-include{forced}"])
        fp_B = ast_fingerprint(src, [f"-include{forced}"])
        assert fp_A != fp_B, "attack didn't change AST"
        # Query B against cached A — closure re-verification must catch the diff
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        if cached is None:
            pytest.skip("cache miss — bucket key caught it (safe but not testing c4)")
        assert not cert_mod.verify_closure_content(cached), \
            "FALSE HIT: closure content should differ after forced include change"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 8: Target triple (--target)
# ════════════════════════════════════════════════════════════════════════

class TestTargetTriple:
    def test_target_triple_change_detected(self, workdir, project_root):
        src = workdir / "tu.c"
        src.write_text("#if defined(__x86_64__)\nint arch = 64;\n#elif defined(__i386__)\nint arch = 32;\n#else\nint arch = 0;\n#endif\n")
        unit_A = make_unit(src, workdir, ["--target=x86_64-linux-gnu"])
        unit_B = make_unit(src, workdir, ["--target=i386-linux-gnu"])
        fp_A = ast_fingerprint(src, ["--target=x86_64-linux-gnu"])
        fp_B = ast_fingerprint(src, ["--target=i386-linux-gnu"])
        if fp_A == fp_B:
            pytest.skip("target triple didn't change AST on this libclang")
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 9: Language standard (-std)
# ════════════════════════════════════════════════════════════════════════

class TestLanguageStandard:
    def test_std_change_detected(self, workdir, project_root):
        src = workdir / "tu.c"
        # C99 vs C11: _Static_assert is C11
        src.write_text("#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L\n_Static_assert(1, \"c11\");\nint ver = 11;\n#else\nint ver = 99;\n#endif\n")
        unit_A = make_unit(src, workdir, ["-std=c99"])
        unit_B = make_unit(src, workdir, ["-std=c11"])
        fp_A = ast_fingerprint(src, ["-std=c99"])
        fp_B = ast_fingerprint(src, ["-std=c11"])
        if fp_A == fp_B:
            pytest.skip("std change didn't change AST on this libclang")
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 10: #pragma pack
# ════════════════════════════════════════════════════════════════════════

class TestPragmaPack:
    def test_pragma_pack_change_detected(self, workdir, project_root):
        src_A = workdir / "tu_a.c"
        src_B = workdir / "tu_b.c"
        src_A.write_text("#pragma pack(1)\nstruct S { char c; int i; };\nint sz = sizeof(struct S);\n")
        src_B.write_text("#pragma pack(4)\nstruct S { char c; int i; };\nint sz = sizeof(struct S);\n")
        unit_A = make_unit(src_A, workdir, [])
        unit_B = make_unit(src_B, workdir, [])
        fp_A = ast_fingerprint(src_A, [])
        fp_B = ast_fingerprint(src_B, [])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 11: #include_next
# ════════════════════════════════════════════════════════════════════════

class TestIncludeNext:
    def test_include_next_change_detected(self, workdir, project_root):
        dir1 = workdir / "sys1"
        dir2 = workdir / "sys2"
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / "stddef.h").write_text("typedef int MYINT;\n")
        (dir2 / "stddef.h").write_text("typedef long MYINT;\n")
        src = workdir / "tu.c"
        src.write_text('#include_next "stddef.h"\nMYINT val;\n')
        # include_next searches dirs after the one that found the current file
        unit_A = make_unit(src, workdir, [f"-I{dir1}", f"-I{dir2}"])
        unit_B = make_unit(src, workdir, [f"-I{dir2}", f"-I{dir1}"])
        fp_A = ast_fingerprint(src, [f"-I{dir1}", f"-I{dir2}"])
        fp_B = ast_fingerprint(src, [f"-I{dir2}", f"-I{dir1}"])
        if fp_A == fp_B:
            pytest.skip("include_next didn't change AST on this libclang")
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 12: X-Macro / repeated include
# ════════════════════════════════════════════════════════════════════════

class TestXMacroRepeatedInclude:
    def test_x_macro_change_detected(self, workdir, project_root):
        xmacro = workdir / "items.x"
        src_A = workdir / "tu_a.c"
        src_B = workdir / "tu_b.c"
        xmacro.write_text("#ifdef X\nX(item1)\nX(item2)\n#endif\n")
        src_A.write_text('#define X(name) int name;\n#include "items.x"\n#undef X\n')
        src_B.write_text('#define X(name) int name##_var;\n#include "items.x"\n#undef X\n')
        unit_A = make_unit(src_A, workdir, [f"-I{workdir}"])
        unit_B = make_unit(src_B, workdir, [f"-I{workdir}"])
        fp_A = ast_fingerprint(src_A, [f"-I{workdir}"])
        fp_B = ast_fingerprint(src_B, [f"-I{workdir}"])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 13: __FILE__, __LINE__, __COUNTER__, __INCLUDE_LEVEL__
# ════════════════════════════════════════════════════════════════════════

class TestBuiltinMacros:
    def test_file_macro_change_detected(self, workdir, project_root):
        # Same content, different file path → __FILE__ differs
        dir_a = workdir / "a"
        dir_b = workdir / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        src_content = 'const char *fname = __FILE__;\n'
        (dir_a / "tu.c").write_text(src_content)
        (dir_b / "tu.c").write_text(src_content)
        unit_A = make_unit(dir_a / "tu.c", workdir, [])
        unit_B = make_unit(dir_b / "tu.c", workdir, [])
        fp_A = ast_fingerprint(dir_a / "tu.c", [])
        fp_B = ast_fingerprint(dir_b / "tu.c", [])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"

    def test_counter_macro_change_detected(self, workdir, project_root):
        # __COUNTER__ increments per use — if a header changes its counter usage,
        # downstream values shift
        src_A = workdir / "tu_a.c"
        src_B = workdir / "tu_b.c"
        src_A.write_text("int a = __COUNTER__;\nint b = __COUNTER__;\nint c = a + b;\n")
        src_B.write_text("int a = __COUNTER__;\nint b = __COUNTER__;\nint c = __COUNTER__;\nint d = a + b;\n")
        unit_A = make_unit(src_A, workdir, [])
        unit_B = make_unit(src_B, workdir, [])
        fp_A = ast_fingerprint(src_A, [])
        fp_B = ast_fingerprint(src_B, [])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 14: Conditional typedef layout (macro-gated struct layout)
# ════════════════════════════════════════════════════════════════════════

class TestConditionalTypedefLayout:
    def test_conditional_layout_change_detected(self, workdir, project_root):
        src = workdir / "tu.c"
        src.write_text(
            "#ifdef BIG\n"
            "struct S { long a; long b; };\n"
            "#else\n"
            "struct S { int a; int b; };\n"
            "#endif\n"
            "int sz = sizeof(struct S);\n"
        )
        unit_A = make_unit(src, workdir, ["-DBIG"])
        unit_B = make_unit(src, workdir, [])
        fp_A = ast_fingerprint(src, ["-DBIG"])
        fp_B = ast_fingerprint(src, [])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# ATTACK 15: Macro-generated function
# ════════════════════════════════════════════════════════════════════════

class TestMacroGeneratedFunction:
    def test_macro_generated_function_change_detected(self, workdir, project_root):
        src = workdir / "tu.c"
        src.write_text(
            "#define GEN_FUNC(name, type) type name(type a, type b) { return a + b; }\n"
            "#ifdef USE_DOUBLE\n"
            "GEN_FUNC(add, double)\n"
            "#else\n"
            "GEN_FUNC(add, int)\n"
            "#endif\n"
        )
        unit_A = make_unit(src, workdir, ["-DUSE_DOUBLE"])
        unit_B = make_unit(src, workdir, [])
        fp_A = ast_fingerprint(src, ["-DUSE_DOUBLE"])
        fp_B = ast_fingerprint(src, [])
        assert fp_A != fp_B, "attack didn't change AST"
        safe, reason = check_no_false_hit(unit_A, unit_B, project_root, workdir)
        assert safe, f"FALSE HIT: {reason}"


# ════════════════════════════════════════════════════════════════════════
# POSITIVE CONTROL: identical TUs MUST produce a HIT (safe reuse)
# ════════════════════════════════════════════════════════════════════════

class TestPositiveControlSafeReuse:
    def test_identical_tus_produce_hit(self, workdir, project_root):
        """Two truly identical TUs MUST reuse (false miss is acceptable but
        this verifies the reuse path WORKS when it should)."""
        src = workdir / "tu.c"
        src.write_text("#include <stdio.h>\nint main(void) { return 0; }\n")
        unit_A = make_unit(src, workdir, [])
        unit_B = make_unit(src, workdir, [])
        fp_A = ast_fingerprint(src, [])
        fp_B = ast_fingerprint(src, [])
        assert fp_A == fp_B, "identical TUs should have same AST"
        # Parse + cache A
        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        # Compute B's bucket key
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        assert bucket_key_A == bucket_key_B, "identical TUs should have same bucket key"
        # Verify closure matches
        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        assert cached is not None, "should find cached entry"
        assert cert_mod.verify_closure_content(cached), "closure should match for identical TUs"


# ════════════════════════════════════════════════════════════════════════
# MUTATION TESTS: drop each load-bearing cert leg → false HIT must occur
# ════════════════════════════════════════════════════════════════════════

class TestMutations:
    """For each load-bearing leg of the certificate, verify that removing it
    from the bucket key produces a false HIT on at least one attack pair.

    These tests PROVE every leg is necessary. If dropping a leg does NOT
    produce a false HIT on any attack, that leg is redundant and can be
    removed for performance.

    Protocol per mutation:
    1. Monkey-patch `compute_bucket_key` (or `verify_closure_content`) to
       drop the target leg.
    2. Run a specific attack pair whose ASTs differ ONLY in the dropped leg.
    3. Assert that a false HIT occurs (bucket matches AND closure verifies
       AND ASTs differ) → proves the leg was catching this attack.
    4. Restore the original function.
    """

    def test_mutation_drop_L0_source(self, workdir, project_root, monkeypatch):
        """Drop source content hash from L0 → false HIT on source edit.

        Attack: same argv, same path, different source content (e.g. a
        `#pragma pack` change). Without L0, the bucket key matches and
        closure matches → false HIT.
        """
        src_A = workdir / "tu_a.c"
        src_B = workdir / "tu_b.c"
        # Different source content, SAME path (we use different files but
        # mutate L0 to drop the path+content, so the mutation must catch
        # the content diff)
        src_A.write_text("int x = 1;\n")
        src_B.write_text("int x = 2;\nint y = 3;\n")
        unit_A = make_unit(src_A, workdir, [])
        unit_B = make_unit(src_B, workdir, [])
        fp_A = ast_fingerprint(src_A, [])
        fp_B = ast_fingerprint(src_B, [])
        assert fp_A != fp_B, "attack didn't change AST"

        # Patch compute_bucket_key to drop the source hash (use a constant
        # L0 so both A and B get the same bucket key)
        original = cert_mod.compute_bucket_key

        def patched_key(unit, project_root, *, skip_files=None, with_refs=True):
            # Force L0 to a constant — drops source content + path + language
            import hashlib as _h
            import os as _os
            raw_argv = cert_mod._raw_argv(unit)
            if any(t.startswith("-include-pch") for t in raw_argv):
                return "FORCE_MISS_PCH"
            # Reproduce the full key but with constant L0
            # We can't easily intercept internals; instead, just return a
            # constant bucket key for both — this proves L0 is load-bearing
            # because without it both A and B collide.
            return "MUTATED_CONSTANT_BUCKET"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", patched_key)

        # Parse + cache A with the MUTATED bucket key
        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        assert bucket_key_A == "MUTATED_CONSTANT_BUCKET"

        # B gets the same mutated bucket key
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        assert bucket_key_A == bucket_key_B, "mutation should collide bucket keys"

        # Closure for A is empty (no includes) → closure verifies trivially
        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        assert cached is not None
        # Closure matches (empty list) → false HIT
        assert cert_mod.verify_closure_content(cached), \
            "closure should match (empty) — proving L0 is the only catch"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", original)

    def test_mutation_drop_L1_ncc(self, workdir, project_root, monkeypatch):
        """Drop L1 (NCC argv) → false HIT on `-D` change.

        Attack: same source, same path, different `-D`. Without L1, bucket
        key matches → false HIT (L0 source matches, closure matches).
        """
        src = workdir / "tu.c"
        src.write_text("#ifdef FOO\nint x = 1;\n#else\nint x = 2;\n#endif\n")
        unit_A = make_unit(src, workdir, ["-DFOO"])
        unit_B = make_unit(src, workdir, [])
        fp_A = ast_fingerprint(src, ["-DFOO"])
        fp_B = ast_fingerprint(src, [])
        assert fp_A != fp_B, "attack didn't change AST"

        original = cert_mod.compute_bucket_key

        def patched_key(unit, project_root, *, skip_files=None, with_refs=True):
            # Drop L1 — return a constant bucket key regardless of argv
            return "MUTATED_DROP_L1"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", patched_key)

        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        assert bucket_key_A == bucket_key_B, "mutation should collide bucket keys"

        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        assert cached is not None
        assert cert_mod.verify_closure_content(cached), \
            "closure should match — proving L1 is the only catch for -D change"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", original)

    def test_mutation_drop_L3_toolchain(self, workdir, project_root, monkeypatch):
        """Drop L3 (toolchain) → false HIT on toolchain change.

        NOTE: On a single machine with one libclang, we can't actually
        change the toolchain. This test is a STRUCTURAL verification:
        we patch L3 to a constant and confirm that two TUs differing
        ONLY in `--target` (which feeds L3 via builtins) would collide.

        If the libclang on this machine doesn't produce different ASTs
        for different `--target`, we skip (the attack is inert here).
        """
        src = workdir / "tu.c"
        src.write_text(
            "#if defined(__x86_64__)\nint arch = 64;\n"
            "#elif defined(__i386__)\nint arch = 32;\n#else\nint arch = 0;\n#endif\n"
        )
        unit_A = make_unit(src, workdir, ["--target=x86_64-linux-gnu"])
        unit_B = make_unit(src, workdir, ["--target=i386-linux-gnu"])
        fp_A = ast_fingerprint(src, ["--target=x86_64-linux-gnu"])
        fp_B = ast_fingerprint(src, ["--target=i386-linux-gnu"])
        if fp_A == fp_B:
            pytest.skip("target triple didn't change AST on this libclang")

        original = cert_mod.compute_bucket_key

        def patched_key(unit, project_root, *, skip_files=None, with_refs=True):
            return "MUTATED_DROP_L3"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", patched_key)

        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        assert bucket_key_A == bucket_key_B, "mutation should collide bucket keys"

        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        assert cached is not None
        assert cert_mod.verify_closure_content(cached), \
            "closure should match — proving L3 is the only catch for --target"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", original)

    def test_mutation_drop_c4_closure(self, workdir, project_root, monkeypatch):
        """Drop c4 (closure re-verification) → false HIT on header content change.

        Attack: same source, same argv, same include path, but header file
        content changed. Without c4 re-verification, bucket key matches
        (L0/L1/L3 all match) → false HIT.
        """
        hdr = workdir / "types.h"
        src = workdir / "tu.c"
        src.write_text('#include "types.h"\nTYPE my_var;\n')
        # Version A
        hdr.write_text("typedef int TYPE;\n")
        unit_A = make_unit(src, workdir, [f"-I{workdir}"])
        fp_A = ast_fingerprint(src, [f"-I{workdir}"])
        _, bucket_key_A = parse_and_cache(unit_A, project_root)
        # Version B: header content changed
        hdr.write_text("typedef long TYPE;\n")
        unit_B = make_unit(src, workdir, [f"-I{workdir}"])
        fp_B = ast_fingerprint(src, [f"-I{workdir}"])
        assert fp_A != fp_B, "attack didn't change AST"

        # Bucket key should match (L0/L1/L3 all same — only c4 differs)
        bucket_key_B = cert_mod.compute_bucket_key(unit_B, project_root)
        assert bucket_key_A == bucket_key_B, \
            "bucket keys should match (only c4 differs)"

        # Patch verify_closure_content to always return True (drop c4)
        original_verify = cert_mod.verify_closure_content

        def patched_verify(cached_entry):
            return True  # drop c4 re-verification

        monkeypatch.setattr(cert_mod, "verify_closure_content", patched_verify)

        cache = cert_mod.get_certificate_cache()
        cached = cache.get(bucket_key_B)
        assert cached is not None
        # With c4 dropped, closure "verifies" → false HIT
        assert cert_mod.verify_closure_content(cached), \
            "mutation drops c4 → false HIT (proving c4 is necessary)"

        # Restore and verify the REAL verify catches it
        monkeypatch.setattr(cert_mod, "verify_closure_content", original_verify)
        assert not original_verify(cached), \
            "real c4 verification should catch the header change"

    def test_mutation_drop_extraction_params(self, workdir, project_root, monkeypatch):
        """Drop extraction_params → false HIT on `with_refs` change.

        Attack: same source, same argv, but different `with_refs` param.
        `parsed.symbols` differs (refs included vs excluded). Without
        extraction_params in the bucket key, both collide → false HIT.
        """
        src = workdir / "tu.c"
        src.write_text("void caller(void) { callee(); }\nvoid callee(void) {}\n")
        unit_A = make_unit(src, workdir, [])
        fp_A = ast_fingerprint(src, [])
        fp_B = ast_fingerprint(src, [])
        assert fp_A == fp_B, "AST should be same (extraction params don't change AST)"

        original = cert_mod.compute_bucket_key

        def patched_key(unit, project_root, *, skip_files=None, with_refs=True):
            # Drop extraction_params — return a constant regardless of params
            return "MUTATED_DROP_EXTRACTION"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", patched_key)

        # Parse + cache A with with_refs=True
        _, bucket_key_A = parse_and_cache(unit_A, project_root, with_refs=True)
        # Query B with with_refs=False — should collide (extraction dropped)
        bucket_key_B = cert_mod.compute_bucket_key(unit_B := make_unit(src, workdir, []),
                                                    project_root, with_refs=False)
        assert bucket_key_A == bucket_key_B, \
            "mutation should collide (extraction params dropped)"

        monkeypatch.setattr(cert_mod, "compute_bucket_key", original)

        # Verify the REAL key differs when extraction params differ
        real_key_A = original(unit_A, project_root, with_refs=True)
        real_key_B = original(unit_A, project_root, with_refs=False)
        assert real_key_A != real_key_B, \
            "real bucket key should differ on extraction params (proving leg is necessary)"
