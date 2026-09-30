"""SLICE-L — T10 vector architecture decoders, the frozen oracles (ADR-008).

The oracle ids are the ones TEST_PLAN §12 cites for SLICE-L:

* RED:   T10_C6 (ELF-first Cortex-M extraction), T10_C7 (RISC-V mtvec mode
  model), T10_C8 (empty-result message lists the branch-table architectures),
  T10_C10 (a vector-slot handler must not read as an unresolved fn pointer).
* GREEN: T10_C1 (Cortex-M C array end to end), T10_C2 (declared coverage
  gaps), T10_C3 (a struct callback array is NOT a vector table), T10_C4
  (assembly word-table positions), T10_C9 (build-unknown entry point).

The ELF fixture is BUILT BY THE TEST (the generator follows
``artifacts/T10/reproduction/r4_elf_first_prototype.py`` verbatim), so no
cross-compiler is required anywhere: the assembly cases run
``asm.extract_vectors`` on hand-built ``AsmSource`` objects and the ELF cases
run the decoder on a hand-crafted ELF32 image.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

import fw_context_mcp  # noqa: F401  — must precede sqlite3
from fw_context_mcp.indexer.compile_commands import CompilationUnit
from fw_context_mcp.indexer.db import (
    find_dead_code,
    get_function_address_arrays,
    get_table_coverage,
    get_vector_table,
    get_entry_point,
    open_db,
)
from fw_context_mcp.indexer.runner import run

# No pytest.mark.libclang here, matching the other slice oracles: the pip
# libclang package is used directly and no gcc binary is needed — the
# assembly cases run extract_vectors on hand-built AsmSource objects and
# the ELF cases on a hand-crafted image, exactly as F11's capability note
# prescribes for a host without a cross-compiler.

CONFIG = "sliceL"


def _run(tmp_path, sources: dict[str, str], build_markers: bool = True):
    """Index *sources* through the real runner and return (conn, config_hash)."""
    work = tmp_path
    (work / "db").mkdir(exist_ok=True)
    commands = []
    for file_name, text in sources.items():
        (work / file_name).write_text(text, encoding="utf-8")
        commands.append({
            "directory": str(work),
            "file": file_name,
            "arguments": ["cc", "-c", file_name],
        })
    (work / "compile_commands.json").write_text(
        json.dumps(commands), encoding="utf-8")
    h = run(
        compile_commands=work / "compile_commands.json",
        db_path=work / "db" / "index.db",
        project_root=work,
        project_id=CONFIG,
        project_name=CONFIG,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
        analyze_overrides=False,
    )
    return open_db(work / "db" / "index.db"), h


CORTEX_M_VECTORS_C = """\
void Reset_Handler(void);
void Timer_Handler(void);
void Default_Handler(void);

__attribute__((section(".isr_vector"), used))
void (* const vectors[])(void) = {
    Reset_Handler,
        Default_Handler,
    Timer_Handler
};
"""

HANDLERS_C = """\
void Reset_Handler(void)    { for (;;) {} }
void Timer_Handler(void)    { for (;;) {} }
void Default_Handler(void)  { for (;;) {} }
"""

GAPPED_C = """\
void Reset_Handler(void) { for (;;) {} }
void Timer_Handler(void) { for (;;) {} }

__attribute__((section(".isr_vector"), used))
void (* const vectors[5])(void) = {
    Reset_Handler,
    0,
    Timer_Handler,
    0,
    Reset_Handler,
};
"""

STRUCT_TABLE_C = """\
void handler_a(void) { for (;;) {} }
void handler_b(void) { for (;;) {} }

struct cb { void (*fn)(void); const char *name; };

struct cb table[2] = {
    { handler_a, "a" },
    { handler_b, "b" },
};
"""


def _asource(text: str):
    """An AsmSource with a 1:1 line map (no preprocessor subprocess)."""
    from fw_context_mcp.indexer.asm import AsmSource

    lines = text.splitlines()
    path = r"D:\fixture\startup.S"
    return AsmSource(
        text=text,
        line_map=[(path, i + 1) for i in range(len(lines))],
        active={path: set(range(1, len(lines) + 1))},
    )


# ── T10_C4 ────────────────────────────────────────────────────────────────

CORTEX_M_STARTUP_S = """\
.section .isr_vector
.global g_pfnVectors
g_pfnVectors:
    .word _estack
    .word Reset_Handler
    .word NMI_Handler
    .word 0
    .word HardFault_Handler
    .word __StackTop + 0x100
.section .text
    .word _sidata
    .word _sdata
"""


def test_T10_C4_asm_word_table_positions():
    """A CMSIS word table: positions are counted, reserved zeros are not returned."""
    from fw_context_mcp.indexer.asm import extract_vectors
    from fw_context_mcp.indexer import vector_decoder

    entries = extract_vectors(_asource(CORTEX_M_STARTUP_S))
    positions = [e.index for e in entries]
    assert positions == [0, 1, 2, 4, 5], (
        f"the reserved .word 0 at position 3 is counted but not returned; "
        f"got {positions}")
    by_position = {e.index: e for e in entries}
    assert by_position[0].name == "_estack"
    assert by_position[1].name == "Reset_Handler"
    assert by_position[2].name == "NMI_Handler"
    assert by_position[4].name == "HardFault_Handler"
    assert by_position[5].name == "__StackTop"
    assert by_position[5].is_data is True, (
        "an expression slot is an address built from the symbol, not the "
        "symbol itself")
    names = {e.name for e in entries}
    assert "_sidata" not in names and "_sdata" not in names, (
        "a .word in an ordinary section is not a table slot")
    # The wiring: extract_from_source is the ADR-008 interface over the
    # same source-side word-table decoder.
    assert vector_decoder.extract_from_source(_asource(CORTEX_M_STARTUP_S)) \
        == entries


# ── T10_C1 / T10_C2 / T10_C9 / T10_C10 — end-to-end on real fixtures ─────

def test_T10_C1_cortex_m_c_array_end_to_end(tmp_path):
    conn, h = _run(tmp_path, {
        "cortex_m_vectors.c": CORTEX_M_VECTORS_C,
        "handlers.c": HANDLERS_C,
    })
    try:
        rows = get_vector_table(conn, h)
        assert len(rows) == 3, rows
        assert [r["source"] for r in rows] == ["c", "c", "c"]
        assert all(r["table_name"] == "vectors" for r in rows)
        assert [r["slot"] for r in rows] == [0, 1, 2]
        assert [r["name"] for r in rows] == [
            "Reset_Handler", "Default_Handler", "Timer_Handler"]
        assert all(r["status"] == "c" for r in rows)
        assert all(r["file"].endswith("handlers.c") for r in rows)
        refs = conn.execute(
            "SELECT slot_index FROM refs WHERE ref_kind = 'indirect' "
            "AND slot_index IS NOT NULL ORDER BY slot_index"
        ).fetchall()
        assert [r["slot_index"] for r in refs] == [0, 1, 2]
    finally:
        conn.close()


def test_T10_C2_declared_coverage_gaps(tmp_path):
    conn, h = _run(tmp_path, {"gapped.c": GAPPED_C})
    try:
        coverage = get_table_coverage(conn, h)
        assert len(coverage) == 1, coverage
        entry = coverage[0]
        assert entry["declared"] == 5
        assert entry["named"] == 3
        assert entry["missing"] == "1, 3"
        assert entry["missing_slots"] == [1, 3]
    finally:
        conn.close()


def test_T10_C9_build_unknown_entry_point_unavailable(tmp_path):
    """A fixture with no build-system markers: no entry point is invented."""
    conn, h = _run(tmp_path, {
        "cortex_m_vectors.c": CORTEX_M_VECTORS_C,
        "handlers.c": HANDLERS_C,
    })
    try:
        assert get_entry_point(conn, h) == ""
        ld_symbols = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE usr LIKE 'ld:%'"
        ).fetchone()[0]
        assert ld_symbols == 0
    finally:
        conn.close()


def test_T10_C10_vector_slot_handler_dead_code_reason(tmp_path):
    """A vector-slot handler never reads as an unresolved fn pointer.

    Consistency with SLICE-I: the reason names vector-table reachability
    and root_evidence keeps I's ``vector_slot:<n>`` shape.
    """
    conn, h = _run(tmp_path, {
        "cortex_m_vectors.c": CORTEX_M_VECTORS_C,
        "handlers.c": HANDLERS_C,
    })
    try:
        rows = find_dead_code(conn, h, project_only=True)
        by_name = {r["name"]: r for r in rows}
        for slot, handler in enumerate(
                ["Reset_Handler", "Default_Handler", "Timer_Handler"]):
            assert handler not in by_name or (
                "vector table" in str(by_name[handler].get("reason", ""))
                and by_name[handler].get("root_evidence")
                == f"vector_slot:{slot}"
            ), f"{handler}: {by_name.get(handler)}"
            if handler in by_name:
                assert ("assigned as function pointer but call sites "
                        "unresolved") not in by_name[handler]["reason"]
    finally:
        conn.close()


# ── T10_C3 — RC2: a struct callback array is not a vector table ──────────

def test_T10_C3_struct_callback_array_not_a_vector_table(tmp_path):
    conn, h = _run(tmp_path, {"table.c": STRUCT_TABLE_C})
    try:
        arrays = get_function_address_arrays(conn, h)
        assert not [r for r in arrays if r["table_name"] == "table"], arrays
        rows = get_vector_table(conn, h)
        assert not [r for r in rows if r.get("table_name") == "table"], rows
    finally:
        conn.close()


# ── T10_C6 / T10_C7 — the ELF-first decoder (ADR-008) ────────────────────

def build_minimal_elf32(path: Path) -> None:
    """A tiny ELF32-LE with a .isr_vector of 8 slots and a symtab.

    Verbatim from ``artifacts/T10/reproduction/r4_elf_first_prototype.py`` —
    Thumb handlers carry the odd st_value, exactly as arm-none-eabi emits.
    """
    SHT_NULL, SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB = 0, 1, 2, 3

    handlers = {
        "Reset_Handler": 0x08000401,     # odd: Thumb bit set
        "NMI_Handler": 0x08000480,
        "HardFault_Handler": 0x08000490,
        "Timer_Handler": 0x08000500,
    }

    # .isr_vector: slot0 = initial SP (no symbol), slot3 = reserved 0,
    # slot7 = an address nothing names (linker-resolved).
    vector_words = [
        0x20005000,                      # 0: initial stack pointer
        handlers["Reset_Handler"],       # 1: reset
        handlers["NMI_Handler"],         # 2: NMI
        0,                               # 3: reserved
        handlers["HardFault_Handler"],   # 4: hard fault
        0,                               # 5: reserved
        handlers["Timer_Handler"],       # 6: timer IRQ
        0x08001234,                      # 7: unnamed (symbol dropped)
    ]
    isr_vector = b"".join(struct.pack("<I", w) for w in vector_words)

    strtab = b"\x00"
    sym_offsets: dict[str, int] = {}
    for name in ["Reset_Handler", "NMI_Handler", "HardFault_Handler",
                 "Timer_Handler"]:
        sym_offsets[name] = len(strtab)
        strtab += name.encode() + b"\x00"

    syms = [struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)]  # null symbol
    for name, addr in handlers.items():
        syms.append(struct.pack(
            "<IIIBBH", sym_offsets[name], addr, 0x10, (1 << 4) | 2, 0, 1))
    symtab = b"".join(syms)

    shstrtab = b"\x00"
    sec_name_off: dict[str, int] = {}
    for sec in [".isr_vector", ".symtab", ".strtab", ".shstrtab"]:
        sec_name_off[sec] = len(shstrtab)
        shstrtab += sec.encode() + b"\x00"

    ehsize, phentsize, shentsize = 52, 0, 40
    off = ehsize
    isr_off, off = off, off + len(isr_vector)
    symtab_off = off + (-(off % 4)) % 4
    off = symtab_off + len(symtab)
    strtab_off = off
    off = strtab_off + len(strtab)
    shstr_off = off
    off = shstr_off + len(shstrtab)
    shoff = off

    ehdr = struct.pack(
        "<4s5B7x2HI3I I 6H",
        b"\x7fELF", 1, 1, 1, 0, 0,      # ELF32, LE, v1, SysV
        0,                               # e_type REL
        40,                              # e_machine EM_ARM
        1,                               # e_version
        0x08000000, 0,                   # entry, phoff
        shoff, 0,                        # shoff, flags
        ehsize, phentsize, 0, shentsize, 5, 4,
    )

    def sh(name, typ, flags, addr, offset, size, link, info, align, entsize):
        return struct.pack(
            "<10I", sec_name_off[name], typ, flags, addr,
            offset, size, link, info, align, entsize,
        )

    shdrs = [
        struct.pack("<10I", 0, SHT_NULL, 0, 0, 0, 0, 0, 0, 0, 0),
        sh(".isr_vector", SHT_PROGBITS, 0x2, 0x08000000,
           isr_off, len(isr_vector), 0, 0, 4, 4),
        sh(".symtab", SHT_SYMTAB, 0, 0, symtab_off, len(symtab),
           3, 1, 4, 16),
        sh(".strtab", SHT_STRTAB, 0, 0, strtab_off, len(strtab),
           0, 0, 1, 0),
        sh(".shstrtab", SHT_STRTAB, 0, 0, shstr_off, len(shstrtab),
           0, 0, 1, 0),
    ]

    blob = bytearray(ehdr)
    blob += isr_vector
    blob += b"\x00" * (symtab_off - ehsize - len(isr_vector))
    blob += symtab
    blob += strtab
    blob += shstrtab
    blob += b"".join(shdrs)
    path.write_bytes(bytes(blob))


def _elf64(path: Path) -> None:
    """A minimal ELFCLASS64 header — what a 64-bit toolchain emits."""
    ehdr = struct.pack(
        "<4s5B7x2HI3I I 6H",
        b"\x7fELF", 2, 1, 1, 0, 0,      # ELF64, LE, v1
        2, 40, 1,                        # e_type EXEC, EM_ARM, version
        0, 0, 0, 0,                      # entry, phoff, shoff, flags
        64, 0, 0, 0, 0, 0,               # ehsize 64, the rest zero
    )
    path.write_bytes(ehdr)


def test_T10_C6_elf_first_cortex_m_extraction(tmp_path):
    """The linked image carries the slots the source index cannot name."""
    from fw_context_mcp.indexer import vector_decoder

    elf = tmp_path / "cortex_m.elf"
    build_minimal_elf32(elf)

    meta, entries = vector_decoder.extract_from_elf(elf)
    assert meta["supported"] is True
    assert meta["arch"] == "arm"
    assert meta["e_machine"] == 40
    by_slot = {e.slot: e for e in entries}
    assert len(entries) == 8
    assert by_slot[1].confidence == "named"
    assert by_slot[1].name == "Reset_Handler"
    assert by_slot[2].name == "NMI_Handler"
    assert by_slot[4].name == "HardFault_Handler"
    assert by_slot[6].name == "Timer_Handler"
    for slot in (3, 5):
        assert by_slot[slot].confidence == "reserved"
        assert by_slot[slot].name is None
    for slot in (0, 7):
        assert by_slot[slot].confidence == "unresolved_address"
        assert by_slot[slot].name is None

    # A 64-bit image is refused with an explicit error, never silently empty.
    elf64 = tmp_path / "elf64.elf"
    _elf64(elf64)
    with pytest.raises(ValueError):
        vector_decoder.extract_from_elf(elf64)

    # A RISC-V image answers with an explicit reason, not empty silence.
    riscv = tmp_path / "riscv.elf"
    blob = bytearray((tmp_path / "cortex_m.elf").read_bytes())
    struct.pack_into("<H", blob, 18, 243)  # EM_RISCV
    riscv.write_bytes(bytes(blob))
    meta, entries = vector_decoder.extract_from_elf(riscv)
    assert meta["supported"] is False
    assert meta["unsupported_reason"]
    assert entries == []


def test_T10_C7_riscv_mtvec_mode_model():
    """Direct mode has zero per-cause slots; vectored slots sit at base+4*cause."""
    from fw_context_mcp.indexer import vector_decoder

    direct = vector_decoder.mtvec_mode_model(0)
    assert direct["mode"] == "direct"
    assert direct["per_cause_slots"] == 0
    # Every trap goes to BASE; there is no per-cause slot to report.
    assert vector_decoder.mtvec_slot_address(0, 0x08000100, 7) is None
    assert vector_decoder.mtvec_exception_address(0, 0x08000100) == 0x08000100

    vectored = vector_decoder.mtvec_mode_model(1)
    assert vectored["mode"] == "vectored"
    base = 0x08000100
    for cause in (0, 3, 11):
        assert vector_decoder.mtvec_slot_address(1, base, cause) \
            == base + 4 * cause
    # Every synchronous exception goes to BASE — never into a pointer table.
    for cause in (0, 3, 11):
        assert vector_decoder.mtvec_exception_address(1, base) == base
    assert vectored["unsupported_reason"], (
        "vectored branch tables need disassembly — the decoder says so "
        "instead of guessing slots")


# ── T10_C8 — the empty result names the branch-table architectures ───────

def test_T10_C8_empty_result_message_lists_branch_table_archs(tmp_path):
    from fw_context_mcp.mcp.handlers.callgraph import _vector_rows

    conn = open_db(tmp_path / "index.db")
    from fw_context_mcp.indexer.db import (
        transaction, upsert_build_config, upsert_project)
    with transaction(conn):
        upsert_project(conn, "pid", "p", str(tmp_path))
        upsert_build_config(conn, CONFIG, "pid", "")
    try:
        rows = _vector_rows(conn, CONFIG, unhandled_only=False, limit=400)
        assert len(rows) == 1 and "info" in rows[0]
        info = rows[0]["info"]
        assert info.startswith("No vector table in this build")
        for arch in ("arm64", "Xtensa", "RISC-V", "MIPS"):
            assert arch in info, f"{arch} missing from: {info}"
    finally:
        conn.close()


# ── The third source, wired end to end (SLICES.md SLICE-L) ───────────────

def _index_with_elf(tmp_path, elf_name="cortex_m.elf"):
    """An index whose build tree holds the hand-built Cortex-M image."""
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    build_minimal_elf32(build / elf_name)
    (build / "compile_commands.json").write_text(
        json.dumps([{"directory": str(build),
                     "file": "main.c",
                     "arguments": ["cc", "-c", "main.c"]}]),
        encoding="utf-8")
    conn = open_db(tmp_path / "index.db")
    from fw_context_mcp.indexer.db import (
        transaction, upsert_build_config, upsert_project)
    with transaction(conn):
        upsert_project(conn, "pid", "p", str(tmp_path))
        upsert_build_config(
            conn, CONFIG, "pid", str(build / "compile_commands.json"))
    return conn


def test_the_elf_image_answers_where_the_source_index_cannot(tmp_path):
    """source='elf' rows arrive with their own slot numbering, never joined."""
    from fw_context_mcp.mcp.handlers.callgraph import _vector_rows

    conn = _index_with_elf(tmp_path)
    try:
        rows = _vector_rows(conn, CONFIG, unhandled_only=False, limit=400)
        elf_rows = [r for r in rows if r.get("source") == "elf"]
        assert [(r["slot"], r["name"]) for r in elf_rows] == [
            (1, "Reset_Handler"), (2, "NMI_Handler"),
            (4, "HardFault_Handler"), (6, "Timer_Handler"),
        ], elf_rows
        assert all(r["status"] == "c" for r in elf_rows)
        assert all(r["table_name"] == ".isr_vector" for r in elf_rows)
        assert {r["slot"] for r in elf_rows}.isdisjoint({0, 3, 5, 7}), (
            "reserved and unnamed slots are not reported as code")
        # Slots are positions in their own table — never renumbered onto
        # the assembly/C numbering.
        assert {(r["source"], r["slot"]) for r in elf_rows} == {
            ("elf", 1), ("elf", 2), ("elf", 4), ("elf", 6)}
    finally:
        conn.close()


def test_the_elf_source_does_not_duplicate_a_slot_the_index_named(tmp_path):
    """A slot the C source already named identically is not reported twice."""
    from fw_context_mcp.indexer.db import (
        insert_refs_batch, insert_symbols_batch, transaction, upsert_file)
    from fw_context_mcp.mcp.handlers.callgraph import _vector_rows

    conn = _index_with_elf(tmp_path)
    try:
        with transaction(conn):
            fid = upsert_file(conn, CONFIG, "isr_tables.c", "c")
            insert_symbols_batch(conn, [
                (CONFIG, fid, "isr_tables.c", "_irq_vector_table",
                 "c:@_irq_vector_table", "_irq_vector_table",
                 "_irq_vector_table", "varglobal", 10, 1, 20, 1,
                 "const uintptr_t[8] _irq_vector_table", "", None,
                 0, 0, "", 0, "", 1, 0.0, "", 0),
                (CONFIG, fid, "handlers.c", "Reset_Handler",
                 "c:@F@Reset_Handler", "Reset_Handler", "Reset_Handler",
                 "function", 100, 1, 102, 1, "", "", None,
                 0, 0, "", 0, "", 1, 0.0, "", 0),
            ])
            insert_refs_batch(conn, [
                (CONFIG, "c:@F@Reset_Handler", "isr_tables.c", 11, None,
                 "indirect", 1),
            ])
        rows = _vector_rows(conn, CONFIG, unhandled_only=False, limit=400)
        elf_slot1 = [
            r for r in rows
            if r.get("source") == "elf" and r["slot"] == 1
        ]
        assert elf_slot1 == [], (
            "the index already names slot 1 with the same handler; the elf "
            "row would be a duplicate")
        assert any(
            r.get("source") == "elf" and r["slot"] == 2 for r in rows)
    finally:
        conn.close()
