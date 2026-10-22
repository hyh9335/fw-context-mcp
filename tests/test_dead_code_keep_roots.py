"""W4 (AUD-R4) — find_dead_code must honor KEEP roots of ENTRY+KEEP-only
linker scripts.

Ledger issue AUD-R4: a function protected ONLY by a ``KEEP()`` in the
build's linker script was reported ``status='dead'`` (L1) when the script
has neither MEMORY regions nor ``ld:`` symbols — the dead-code handler
discovered scripts from exactly those two DB surfaces
(``memory_regions.file_path`` and ``symbols.usr LIKE 'ld:%'``), so an
ENTRY + KEEP-only script was invisible to it and its ``KEEP()`` roots were
dropped.  The W0 sharpened root cause
(``.fw-closeout/artifacts/W0/R4_dead_code.log``) shows the parser itself is
correct (``keep_patterns`` reads ``.keep_sec``); the defect is
handler-layer discovery.

Accepted design: ``.fw-closeout/artifacts/arch/accepted_design_W4.md``

- Fix: KEEP roots consumed independently of MEMORY/assignment presence —
  the ``files`` table (every script the build's backend discovered is
  upserted there by ``store_scripts``) is the third discovery surface;
  ``KEEP()`` sections synthesize ``keep:`` reachability roots.
- /DISCARD/ honesty: a ``KEEP()`` inside a ``/DISCARD/`` output section is
  a REMOVAL promise, not a reachability one — such a section roots
  nothing; a dead symbol landing there is reported with
  ``binary_status='gc_removed'`` (+ ``linker_root_evidence``), never as
  reachable.
- O8: the linker script content IS the oracle — assertions cite the
  script's ``KEEP``/``ENTRY`` lines; the binary half links the checked-in
  fixture with ``--gc-sections`` (capability-guarded: clang+ld.lld+llvm-nm
  first, then gcc+ld+nm; honest skip when neither toolchain exists).

Tests:
- ``test_w4_keep_only_script_keep_fn_not_dead`` — the RED→GREEN behavior
  test: RED pre-fix (keep_fn reported dead L1), GREEN post-fix (keep_fn
  absent; dead_fn control stays dead).
- ``test_w4_discovery_finds_entry_keep_only_script`` — the discovery unit:
  the ENTRY+KEEP-only script is surfaced from the ``files`` table and its
  patterns root keep_fn on the reachability-roots surface.
- ``test_w4_keep_in_discard_is_gc_removed_not_rooted`` — the /DISCARD/
  honesty contract.
- ``test_w4_no_over_retention_when_script_unknown_to_build`` — guard
  (passes pre- and post-fix): when the build never stored a script, there
  is no KEEP root and keep_fn must stay dead; no ``binary_status`` field
  is fabricated.
- ``test_w4_o8_linker_gc_binary_oracle`` — real linker truth, independent
  of the index fix (passes at RED and GREEN by design, like the T07
  binary anchor).

Fixture: the checked-in ``tests/fixtures/keep_only/`` project, materialized
verbatim under pytest ``tmp_path``; every index DB lives under that tmp
tree via the project ``config.toml`` ``db_dir`` (the repo ``.fw-context``
and the global ``~/.fw-context`` registry are never touched).

NOTE: deliberately NOT marked ``libclang`` — the conftest auto-skip keys on
gcc, which is absent on some hosts; the pip libclang wheel is
self-sufficient (same choice as the SLICE-I suite).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from fw_context_mcp.indexer import linker_script as ls
from fw_context_mcp.indexer.db import open_db
from fw_context_mcp.indexer.runner import run

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "keep_only"
PROJECT_ID = "w4keeponlyfixture000000000000000000"

LLVM_DIR = Path(r"C:\Program Files\LLVM\bin")

# Checked-in fixture content, read once — the fixture files are the single
# source of truth for both the index assertions and the O8 binary oracle.
MAIN_C_TEXT = (FIXTURE_DIR / "main.c").read_text(encoding="utf-8")
LD_TEXT = (FIXTURE_DIR / "linker.ld").read_text(encoding="utf-8")

# /DISCARD/ variant (test_w4_keep_in_discard_is_gc_removed_not_rooted):
# .keep_sec is genuinely kept, .gone_sec is KEEP()ed but inside /DISCARD/ —
# the linker removes it despite the KEEP.
DISCARD_LD = """\
ENTRY(main)

SECTIONS
{
  .keep_sec : { KEEP(*(.keep_sec)) }
  /DISCARD/ : { KEEP(*(.gone_sec)) *(.junk*) }
}
"""

DISCARD_C = """\
/* W4 discard variant: .gone_sec is KEEP()ed INSIDE /DISCARD/ — the linker
 * removes it despite the KEEP, so it must NOT become a reachability root. */
static void entry_live_fn(void);

__attribute__((section(".keep_sec")))
void keep_fn(void) { }

__attribute__((section(".gone_sec")))
void gone_fn(void) { }          /* KEEP inside /DISCARD/: removed */

void dead_fn(void) { }

static void entry_live_fn(void) { }

int main(void) {
    entry_live_fn();
    return 0;
}
"""


# ── Harness (same isolation pattern as the SLICE-I suite) ───────────────────

CONFIG_TMPL = """\
[project]
name = "w4-keep-only"
id = "{pid}"

[index]
db_dir = "{db_dir}"
index_refs = true
index_embeddings = false

[llm]
enabled = false
"""


def _materialize_cc(root: Path) -> Path:
    """Copy the checked-in compile DB, rewriting paths to the copy."""
    payload = json.loads(
        (FIXTURE_DIR / "compile_commands.json").read_text(encoding="utf-8"))
    for entry in payload:
        entry["directory"] = str(root)
        entry["file"] = str(root / "main.c")
    cc = root / "compile_commands.json"
    cc.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return cc


def _make_project(tmp_path: Path, name: str, *,
                  main_c: str = MAIN_C_TEXT, ld_text: str = LD_TEXT,
                  store_linker_script: bool = True):
    """Fixture project + isolated index dir + full index → (root, db, hash).

    The runner skips linker scripts for build-unknown projects (the
    documented C7 limitation), so the test drives ``store_scripts``
    directly — the exact pattern of the SLICE-I suite — to reach the
    real-world AUD-R4 state: the script IS stored (``files`` row +
    ``build_configs.entry_point``) but registers neither a
    ``memory_regions`` row nor an ``ld:`` symbol.
    """
    root = tmp_path / name
    root.mkdir()
    index_dir = tmp_path / f"{name}_index"
    (root / "main.c").write_text(main_c, encoding="utf-8")
    (root / "linker.ld").write_text(ld_text, encoding="utf-8")
    cc = _materialize_cc(root)
    (root / ".fw-context").mkdir()
    (root / ".fw-context" / "config.toml").write_text(
        CONFIG_TMPL.format(pid=PROJECT_ID, db_dir=str(index_dir).replace("\\", "/")),
        encoding="utf-8",
    )
    db_path = index_dir / PROJECT_ID / "index.db"
    ch = run(
        cc,
        db_path,
        project_root=root,
        project_id=PROJECT_ID,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
    )
    if store_linker_script:
        conn_rw = sqlite3.connect(str(db_path))
        conn_rw.row_factory = sqlite3.Row
        try:
            ls.store_scripts(conn_rw, ch, [root / "linker.ld"], root)
            conn_rw.commit()
        finally:
            conn_rw.close()
    return root, db_path, ch


def _assert_aud_r4_preconditions(conn: sqlite3.Connection, ch: str) -> None:
    """Pin the audited DB state: script stored, but neither discovery
    surface of the pre-fix handler can see it."""
    sym = conn.execute(
        "SELECT section FROM symbols WHERE config_hash=? AND name='keep_fn' "
        "AND is_definition=1", (ch,),
    ).fetchone()
    assert sym is not None, "keep_fn definition missing from symbols"
    assert sym["section"] == ".keep_sec", (
        f"fixture must store the .keep_sec section on the DEFINITION row "
        f"(W4 setup): {sym['section']!r}")
    ep = conn.execute(
        "SELECT entry_point FROM build_configs WHERE config_hash=?", (ch,),
    ).fetchone()
    assert ep is not None and ep["entry_point"] == "main", (
        f"fixture must store entry_point='main' (W4 setup): {ep}")
    regions = conn.execute(
        "SELECT COUNT(*) FROM memory_regions WHERE config_hash=?", (ch,),
    ).fetchone()[0]
    ld_syms = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE config_hash=? AND usr LIKE 'ld:%'",
        (ch,),
    ).fetchone()[0]
    assert regions == 0 and ld_syms == 0, (
        "AUD-R4 precondition broken: an ENTRY+KEEP-only script must register "
        f"neither memory_regions rows (got {regions}) nor ld: symbols "
        f"(got {ld_syms})")


def _handler_rows(root: Path) -> dict[str, dict]:
    from fw_context_mcp.mcp.handlers import callgraph as cg

    out = cg.find_dead_code(project_root=str(root), limit=200, project_only=False)
    assert isinstance(out, list)
    return {r["name"]: r for r in out if "info" not in r}


# ── The RED→GREEN behavior test ─────────────────────────────────────────────

def test_w4_keep_only_script_keep_fn_not_dead(tmp_path):
    """keep_fn — protected ONLY by the script's ``KEEP(*(.keep_sec))`` line
    (O8 oracle), zero references, no ``used`` attribute — must never be
    reported dead.  Pre-fix the handler discovers no script (neither a
    memory_regions row nor an ld: symbol exists) and reports keep_fn
    ``status='dead'`` L1 — the exact AUD-R4 false positive.  dead_fn, the
    unprotected control, must stay dead in both runs (no over-retention)."""
    root, db_path, ch = _make_project(tmp_path, "keep")
    conn = open_db(db_path)
    try:
        _assert_aud_r4_preconditions(conn, ch)

        # O8 discipline: the script content is the oracle — cite its lines.
        ld_text = (root / "linker.ld").read_text(encoding="utf-8")
        assert "ENTRY(main)" in ld_text, f"O8 oracle line missing: {ld_text!r}"
        assert "KEEP(*(.keep_sec))" in ld_text, f"O8 oracle line missing: {ld_text!r}"
        clean_ld = ls.strip_comments(ld_text)
        assert "MEMORY" not in clean_ld, (
            "fixture drift: the audited shape has NO MEMORY block "
            f"(comment-stripped script holds one): {clean_ld!r}")

        by_name = _handler_rows(root)
        keep_row = by_name.get("keep_fn")
        assert keep_row is None, (
            "keep_fn must NOT be reported dead — the script's "
            f"KEEP(*(.keep_sec)) line is a reachability root (AUD-R4/W4); "
            f"got {keep_row}")
        control = by_name.get("dead_fn")
        assert control is not None and control["status"] == "dead", (
            f"dead_fn control must stay dead (no over-retention): {control}")
        assert control.get("evidence_level") == "L1", (
            f"dead_fn control must keep its L1 dead evidence: {control}")
    finally:
        conn.close()


# ── The discovery unit (the accepted design's chosen mechanism) ─────────────

def test_w4_discovery_finds_entry_keep_only_script(tmp_path):
    """The handler's linker-script discovery must surface the ENTRY+KEEP-only
    script through the ``files`` table — the third build-attested surface the
    accepted design adds — and the resulting patterns must root keep_fn on
    the reachability-roots surface with visible ``keep:`` evidence."""
    root, db_path, ch = _make_project(tmp_path, "disc")
    conn = open_db(db_path)
    try:
        _assert_aud_r4_preconditions(conn, ch)

        from fw_context_mcp.mcp.handlers import callgraph as cg

        scripts = cg._discover_linker_scripts(conn, ch, root)
        assert (root / "linker.ld") in scripts, (
            "discovery must surface the ENTRY+KEEP-only script via the files "
            f"table (AUD-R4/W4): {scripts}")

        patterns = ls.keep_patterns(root / "linker.ld")
        assert ".keep_sec" in patterns, (
            f"the parser must read the KEEP pattern (parser-truth anchor): {patterns}")

        from fw_context_mcp.indexer.db import find_reachability_roots

        roots = {r["name"]: r for r in find_reachability_roots(
            conn, ch, keep_patterns=sorted(patterns))}
        assert "keep_fn" in roots, (
            f"keep_fn must be a reachability root under its KEEP pattern: "
            f"{sorted(roots)}")
        evidence = " ".join(roots["keep_fn"].get("evidence") or [])
        assert "keep" in evidence, (
            f"keep_fn root evidence must name the KEEP root: {roots.get('keep_fn')}")
    finally:
        conn.close()


# ── /DISCARD/ honesty (accepted design §Risks) ──────────────────────────────

def test_w4_keep_in_discard_is_gc_removed_not_rooted(tmp_path):
    """A ``KEEP()`` inside a ``/DISCARD/`` output section is a REMOVAL
    promise: the linker drops the section despite the KEEP.  Such a section
    must root nothing; a dead symbol landing there stays reported, with
    ``binary_status='gc_removed'`` and ``linker_root_evidence`` naming the
    section — never converted into a reachability claim."""
    root, db_path, ch = _make_project(
        tmp_path, "discard", main_c=DISCARD_C, ld_text=DISCARD_LD)
    conn = open_db(db_path)
    try:
        from fw_context_mcp.mcp.handlers import callgraph as cg

        kept, discarded = cg._split_keep_by_discard([root / "linker.ld"])
        assert ".keep_sec" in kept, (
            f"the genuinely-kept section must stay a root pattern: kept={kept}")
        assert ".gone_sec" in discarded, (
            f"the KEEP inside /DISCARD/ must be classified as removal: "
            f"discarded={discarded}")
        assert ".gone_sec" not in kept, (
            f"a /DISCARD/-only KEEP must not be a root pattern: kept={kept}")

        by_name = _handler_rows(root)
        gone = by_name.get("gone_fn")
        assert gone is not None and gone["status"] == "dead", (
            "a KEEP() inside /DISCARD/ is not a root — gone_fn must stay "
            f"reported dead: {gone}")
        assert gone.get("binary_status") == "gc_removed", (
            f"gone_fn binary_status must be gc_removed (the script removes "
            f"its section despite the KEEP): {gone}")
        assert gone.get("linker_root_evidence") == "keep_section:.gone_sec", (
            f"gone_fn must carry the linker root evidence naming its section: "
            f"{gone}")
        # The genuinely kept function is rooted and never reported.
        assert "keep_fn" not in by_name, (
            f"keep_fn must stay unreported in the discard variant too: {by_name}")
        # Controls: the unprotected dead function reports unknown presence.
        control = by_name.get("dead_fn")
        assert control is not None and control["status"] == "dead", (
            f"dead_fn control must stay dead (no over-retention): {control}")
        assert control.get("binary_status") == "unknown", (
            f"an unprotected dead function has no derivable binary status: "
            f"{control}")
    finally:
        conn.close()


# ── No-over-retention guard (passes pre- and post-fix) ──────────────────────

def test_w4_no_over_retention_when_script_unknown_to_build(tmp_path):
    """When the build never stored a linker script, no ``files`` row and no
    entry point exist — the fix must NOT manufacture KEEP roots from
    nothing: keep_fn stays dead, and no ``binary_status`` evidence is
    fabricated for a build with no linker-evidence context at all."""
    root, db_path, ch = _make_project(
        tmp_path, "noscript", store_linker_script=False)
    conn = open_db(db_path)
    try:
        stored = conn.execute(
            "SELECT COUNT(*) FROM files WHERE config_hash=? AND path LIKE '%linker.ld'",
            (ch,),
        ).fetchone()[0]
        assert stored == 0, (
            f"precondition: the build must not know the script here ({stored} rows)")
        ep = conn.execute(
            "SELECT entry_point FROM build_configs WHERE config_hash=?", (ch,),
        ).fetchone()
        assert ep is not None and ep["entry_point"] == "", (
            f"precondition: no entry point without a stored script: {ep}")

        by_name = _handler_rows(root)
        keep_row = by_name.get("keep_fn")
        assert keep_row is not None and keep_row["status"] == "dead", (
            "with the script unknown to the build there is no KEEP root — "
            f"keep_fn must stay dead (no over-retention): {keep_row}")
        assert "binary_status" not in keep_row, (
            "no linker evidence context — no binary_status field may be "
            f"fabricated: {keep_row}")
        control = by_name.get("dead_fn")
        assert control is not None and control["status"] == "dead", (
            f"dead_fn control must stay dead: {control}")
    finally:
        conn.close()


# ── O8: the real linker is the oracle (capability-guarded) ──────────────────

def _llvm_tool(name: str) -> str | None:
    p = LLVM_DIR / f"{name}.exe"
    if p.exists():
        return str(p)
    return shutil.which(name)


def _find_linker_toolchain():
    """clang+ld.lld+llvm-nm first, then gcc+ld+nm; None when neither exists."""
    clang, lld, llvm_nm = (
        _llvm_tool("clang"), _llvm_tool("ld.lld"), _llvm_tool("llvm-nm"))
    if clang and lld and llvm_nm:
        return ("llvm", clang, lld, llvm_nm)
    gcc, ld, nm = (shutil.which("gcc"), shutil.which("ld"), shutil.which("nm"))
    if gcc and ld and nm:
        return ("gnu", gcc, ld, nm)
    return None


def test_w4_o8_linker_gc_binary_oracle(tmp_path):
    """O8 binary truth for the checked-in fixture: under ``--gc-sections``
    with the checked-in linker.ld, keep_fn SURVIVES (the script's
    ``KEEP(*(.keep_sec))`` line) and dead_fn is REMOVED (no protection — the
    negative control).  Independent of the index fix; passes at RED and
    GREEN by design.  Skipped honestly when no linker toolchain exists."""
    toolchain = _find_linker_toolchain()
    if toolchain is None:
        pytest.skip(
            "capability: neither clang+ld.lld+llvm-nm nor gcc+ld+nm on this "
            "host — linker-run validation not possible here")
    kind, cc_bin, ld_bin, nm_bin = toolchain
    # The freestanding target pin and the no-hosted-headers flag are clang
    # spellings; the GNU fallback uses its native ones (the WSL path).
    target_flags = ["--target=x86_64-unknown-linux-gnu"] if kind == "llvm" else []
    nostdinc_flags = ["-nostdlibinc"] if kind == "llvm" else ["-nostdinc"]

    fix = tmp_path / "o8"
    fix.mkdir()
    (fix / "main.c").write_text(MAIN_C_TEXT, encoding="utf-8")
    (fix / "linker.ld").write_text(LD_TEXT, encoding="utf-8")

    comp = subprocess.run(
        [cc_bin, *target_flags, "-std=c11", "-ffreestanding", *nostdinc_flags,
         "-O0", "-ffunction-sections", "-fdata-sections",
         "-c", "main.c", "-o", "main.o"],
        cwd=str(fix), capture_output=True, text=True,         encoding="utf-8", errors="replace",
    )
    assert comp.returncode == 0, f"compile failed ({kind}): {comp.stderr[-800:]}"
    link = subprocess.run(
        [ld_bin, "main.o", "-o", "main.elf", "--gc-sections",
         "--print-gc-sections", "-Map=main.map", "-T", "linker.ld",
         "-nostdlib", "--noinhibit-exec"],
        cwd=str(fix), capture_output=True, text=True,         encoding="utf-8", errors="replace",
    )
    assert link.returncode == 0 and (fix / "main.elf").exists(), (
        f"gc link failed ({kind}): {link.stderr[-800:]}")
    nm = subprocess.run(
        [nm_bin, "main.elf"], cwd=str(fix), capture_output=True,
        text=True,         encoding="utf-8", errors="replace",
    )
    symbols = set(nm.stdout.split())

    # O8 hard truth — nm presence against the script's own oracle lines.
    assert "keep_fn" in symbols, (
        "linker truth: keep_fn must SURVIVE --gc-sections — the checked-in "
        f"script promises KEEP(*(.keep_sec)); nm={sorted(symbols)}")
    assert "dead_fn" not in symbols, (
        "linker truth: dead_fn must be gc-REMOVED — no KEEP, no refs "
        f"(negative control); nm={sorted(symbols)}")

    # Corroboration from the GC log when the toolchain's format matches.
    # ld.lld:   removing unused section main.o:(.text.dead_fn)
    # GNU ld:   removing unused section '.text.dead_fn' in file 'dead.o'
    gc_log = link.stdout + link.stderr
    removed = {
        part.split(":")[-1].strip("'\"()")
        for part in re.findall(r"[Rr]emoving unused section (\S+)", gc_log)
    }
    if removed:
        assert any(
            ".text.dead_fn" in sec for sec in removed
        ), (f"gc log must name .text.dead_fn among removed sections: {sorted(removed)}")
