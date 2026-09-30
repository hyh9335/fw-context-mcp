"""Architecture-aware interrupt/vector decoding (ADR-008, T10 Option B).

A vector table can live in three places, and each needs its own reader:

* an assembly file, written as address words — ``asm.extract_vectors`` reads
  that at index time;
* a generated C array of function addresses — the range join in
  ``db._callgraph.get_function_address_arrays`` reads that at query time;
* the linked IMAGE itself, whose vector section holds one resolved word per
  slot — this module reads that at query time, ELF-first.

The image is the one source a build without a source-level table still has:
when the assembly pass never ran (no ``clang`` CLI for preprocessing) or the
table is written by the linker, the words are still there, and ``.symtab``
names the addresses they hold.  The reader reuses ``intlist._Elf`` — the
same 100-line reader that reads ``.intList`` registrations — and adds only a
vector-section walk.

**The interface** is the one blueprint A10 named: ``detect`` decides whether
an image's architecture is decodable here, ``extract_from_elf`` returns one
entry per slot with a per-slot confidence, and ``extract_from_source`` is the
source-side word-table decoder.  A non-decodable image answers with an
explicit ``unsupported_reason`` — never with a silent empty list, and never
with guessed slots.

**Architecture honesty.**  Only the Cortex-M form is decoded today: an
ARM ELF32 image whose vector section is a table of address words.  arm64,
Xtensa, RISC-V and MIPS write their tables as branch instructions (or, on
RISC-V, address them through ``mtvec``), so no word table exists to read —
``BRANCH_TABLE_ARCHS`` names them, and ``asm.extract_vectors`` already says
the same thing: a miss, never a wrong answer.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .intlist import _Elf

log = logging.getLogger(__name__)

# ── ELF e_machine values (the architectures that matter here) ────────────

EM_ARM = 40
EM_AARCH64 = 183        # arm64
EM_XTENSA = 94
EM_RISCV = 243
EM_MIPS = 8

# A target of one of these writes its vector table as branch instructions
# (or dispatches through a base register), so there is no table of address
# words to read.  The order matches the wording asm.extract_vectors has
# carried since the assembly reader was written — one source of truth for
# the sentence both places say.
BRANCH_TABLE_ARCHS = ("arm64", "Xtensa", "RISC-V", "MIPS")

_ARCH_NAMES = {
    EM_ARM: "arm",
    EM_AARCH64: "arm64",
    EM_XTENSA: "Xtensa",
    EM_RISCV: "RISC-V",
    EM_MIPS: "MIPS",
    0: "none",
}

# v1 decodes the Cortex-M form only; see ``detect``.
SUPPORTED_MACHINES = frozenset({EM_ARM})

# Where a vector table of address words lives, by the names builds actually
# use: CMSIS startup files write ``.isr_vector``, ARM Keil/IAR images write
# ``.Vectors``, and GCC-based trees write ``.vectors``.
DEFAULT_VECTOR_SECTIONS = (".isr_vector", ".Vectors", ".vectors")

_E_MACHINE_OFFSET = 18  # ELF32: e_ident(16) + e_type(2) → e_type, e_machine


def _unsupported_reason(arch: str) -> str:
    return (
        f"{arch} images are not decoded: their vectors are not a table of "
        f"address words, and reading them needs disassembly"
    )


@dataclass(frozen=True)
class VectorEntryElf:
    """One slot of a vector section read out of the linked image.

    Attributes:
        slot: The slot's POSITION in its table, counting from zero and
            counting reserved entries — never renumbered onto another
            source's numbering, the same rule the assembly and C sources
            follow.
        name: The symbol the slot names, or None when the word is reserved
            or names nothing in ``.symtab``.
        confidence: ``"named"`` (the word is a symtab address), ``"reserved"``
            (the word is zero — counted, like the assembly reader's reserved
            ``.word 0`` slots, and never reported as code) or
            ``"unresolved_address"`` (the word holds an address the symtab
            does not name — the build resolved it; the build-registration
            source, not a guess, is what may name it).
        section: The vector section the slot was read from.
        address: The raw word, for a reader that needs the address itself.
    """

    slot: int
    name: str | None
    confidence: str
    section: str
    address: int = 0


def detect(elf: _Elf) -> dict:
    """Report what architecture *elf* is, and whether it can be decoded.

    Reads ``e_machine`` straight out of the ELF32 header at its fixed
    offset — the ``_Elf`` reader exposes sections and symbols but not the
    machine, and the offset is part of the format, not of the file.

    Returns a dict with ``arch``, ``e_machine``, ``supported`` and, when
    not supported, an explicit ``unsupported_reason``.  An unknown machine
    is reported as such rather than guessed at.
    """
    machine, = struct.unpack_from(
        elf.byte_order + "H", elf._data, _E_MACHINE_OFFSET)
    arch = _ARCH_NAMES.get(machine, f"e_machine={machine}")
    supported = machine in SUPPORTED_MACHINES
    return {
        "arch": arch,
        "e_machine": machine,
        "supported": supported,
        "unsupported_reason": None if supported else _unsupported_reason(arch),
    }


def extract_from_elf(
    path: Path | str,
    section_names: tuple[str, ...] = DEFAULT_VECTOR_SECTIONS,
) -> tuple[dict, list[VectorEntryElf]]:
    """Read every slot of the vector sections in the linked image at *path*.

    The image is opened with ``intlist._Elf``, which refuses a 64-bit file
    and a non-ELF file with a ``ValueError`` — those answers stay explicit:
    an image the decoder cannot open is an answer, not an empty list.

    Returns ``(meta, entries)`` where *meta* is ``detect``'s dict.  A
    detected-but-unsupported image returns ``(meta, [])`` — with the reason
    in *meta*, so the caller can say WHY instead of staying silent.
    """
    elf = _Elf(Path(path))
    meta = detect(elf)
    if not meta["supported"]:
        return meta, []

    names = elf.addresses_to_names()
    entries: list[VectorEntryElf] = []
    for section in section_names:
        raw = elf.section(section)
        if not raw or len(raw) % 4:
            continue
        for slot in range(len(raw) // 4):
            word, = struct.unpack_from(elf.byte_order + "I", raw, slot * 4)
            name = names.get(word)
            if word == 0:
                confidence = "reserved"
            elif name is not None:
                confidence = "named"
            else:
                confidence = "unresolved_address"
            entries.append(VectorEntryElf(
                slot=slot, name=name, confidence=confidence,
                section=section, address=word))
    return meta, entries


def extract_from_source(source):
    """Read the vector table a SOURCE file holds, one entry per slot.

    The source-side word-table decoder is ``asm.extract_vectors``: a table
    is data directives inside a non-ordinary section, and its entries carry
    the position each symbol was written at.  This is the same function the
    indexer has always run — the interface name exists so a caller that
    speaks ADR-008 does not have to know which module holds which source.

    The C-array source is NOT here: an array of function addresses is read
    by a query-time range join over the index (``db._callgraph``), not by
    re-parsing a file, because the index is the thing that knows which
    elements hold addresses of functions.
    """
    from .asm import extract_vectors  # local: asm is the source-side decoder

    return extract_vectors(source)


# ── RISC-V mtvec: the mode model a RISC-V decoder would decode to ────────

_MTVEC_FORM = 0x3  # low two MODE bits of mtvec; BASE holds the rest


def mtvec_mode_model(mode: int) -> dict:
    """Model what an RISC-V ``mtvec`` MODE says about per-cause slots.

    The privileged ISA defines two forms.  ``MODE=Direct`` (0): every trap
    goes to ``BASE`` — there are no per-cause slots at all, so a decoder
    must report zero slots rather than invent one per cause.
    ``MODE=Vectored`` (1): interrupt cause *i* vectors to ``BASE + 4*i``,
    while every SYNCHRONOUS EXCEPTION still goes to ``BASE`` — exceptions
    are never vectored, so a table that claims an exception slot is reading
    the wrong ISA.  Values above 1 are reserved (WARL).

    Returns a dict with ``mode``, ``per_cause_slots`` and, when reading
    real slots would need disassembly of the branch instructions at the
    target addresses, an explicit ``unsupported_reason``.  This model is
    what a future RISC-V extractor (T10 Option A) would decode to; today it
    is the honest answer to "what would the mode allow".
    """
    form = mode & _MTVEC_FORM
    base = "BASE"
    if form == 0:
        return {
            "mode": "direct",
            "per_cause_slots": 0,
            "trap_target": base,
            "exception_target": base,
            "supported": True,
            "unsupported_reason": None,
        }
    if form == 1:
        return {
            "mode": "vectored",
            "per_cause_slots": "one per interrupt cause, at BASE + 4*cause",
            "slot_address": "BASE + 4*cause",
            "trap_target": base,
            "exception_target": base,
            "supported": True,
            # Reading the slots needs the branch instructions at those
            # addresses — a word-table reader has nothing to read there.
            "unsupported_reason": (
                "vectored branch tables need disassembly of the "
                "instructions at BASE + 4*cause"),
        }
    return {
        "mode": f"reserved({form})",
        "per_cause_slots": 0,
        "trap_target": base,
        "exception_target": base,
        "supported": False,
        "unsupported_reason": (
            f"mtvec MODE bits {form} are reserved (WARL); the hardware's "
            "choice is not knowable from the value alone"),
    }


def mtvec_slot_address(mode: int, base: int, cause: int) -> int | None:
    """Address the hardware vectors interrupt *cause* to, or None.

    None means the mode defines no per-cause slot for the cause — the
    Direct answer for every cause, and the only honest one: reporting
    ``base`` as cause 0's slot would be a guess the ISA does not make.
    """
    if mode & _MTVEC_FORM == 1:
        return base + 4 * cause
    return None


def mtvec_exception_address(mode: int, base: int) -> int:
    """Address a synchronous exception traps to — always BASE.

    Vectored mode vectors INTERRUPTS only; an exception never enters the
    pointer table, whatever a table-shaped reader might want to believe.
    """
    return base
