"""store_v2.certify — Slice-1 per-TU production certification (G7-B).

Turns a REAL production parse into certified :class:`CertifiedSemanticInputs`
and the final KB-C via the frozen Slice-0 primitive (``store_v2.keys``).
Slice-0 established the key mechanics over already-certified inputs; THIS
module is the certification source the preflight flagged as
``BLOCKS_KB_IMPLEMENTATION`` (G7-B: dedicated per-TU certification, minimal
hook in the runner path, no pipeline redesign).

What is certified (all legs read CURRENT bytes — the manifest's
generated-only staleness trust is deliberately bypassed here):

* **L0** — main source blob (full-width SHA-256 of current bytes).
* **L1/NCC** — raw compile-command argv (pre-normalization, rsp tokens intact)
  through the S2 canonicalizer (:mod:`store_v2.ncc`, byte-compat fixture
  S1-T05 vs the frozen Phase-2 prototype) plus the class-C post-step
  (``SLICE1_CLASS_C_DIGEST_SPEC.md`` §3): certified external path tokens are
  replaced by ``envcert:<environment_digest>:<rel>``; uncertifiable roots keep
  their literal ``ext:*`` spelling (over-key, fail-safe).
* **c3** — ordered forced-input CONTENT legs (``-include``/``-imacros``), in
  command-line position order.  An unreadable forced input POISONS (fail
  closed), never a shared constant (R-K1(b)).
* **c4** — ordered resolved-dependency closure content sequence from the LIVE
  parse's ``tu.get_includes()`` (first-inclusion order, manifest-parity
  display paths), every member re-hashed at certification grade; an
  unresolvable member becomes the ``NOT_FOUND`` marker AND poisons
  ``closure_complete`` (fail closed).
* **c5** — TU macro-state digest over the full sorted ``clang -dM -E`` output
  (object-like AND function-like; the legacy ``resolve_macros_via_preprocessor``
  contract stays object-like-only for ``expanded_value``).
* **class-C environment (L3)** — per ``SLICE1_CLASS_C_DIGEST_SPEC.md`` §1:
  indexer generation, libclang identity, capture-binary identity, builtin
  predefine digest, target triple.  A FAILED identity capture POISONS (never
  ``UNKNOWN_TAG`` and never a shared constant — H-5 stays a Slice-0 vector
  concept; production fails closed instead).  ``parser_generation`` is a
  deterministic function of the certified identity (cross-store stable), the
  registry row is diagnostics only.
* **Poison paths** (status='poisoned', no reusable state, no
  ``semantic_states`` row — INV-6): source unreadable, forced input unreadable,
  rsp referenced-but-unreadable, unresolved include in TU diagnostics, closure
  member rehash failure, class-C capture failure, malformed inputs
  (``ValueError`` from the keys layer).

Legacy isolation: :func:`certify_and_publish_for_unit` never raises; with the
v2 flag off it returns before any work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .keys import (
    NOT_FOUND,
    UNKNOWN_TAG,
    CertifiedSemanticInputs,
    TUKeyResult,
    compute_tu_semantic_key,
    h,
)
from .ncc import SEP, canonicalize

log = logging.getLogger(__name__)

#: Slice-4A: bumped v1→v2 for the ``cert_dir_attestations`` capture face
#: (accepted O6 decision — CERT_VERSION bump only; INDEXER_GENERATION is
#: unchanged).  Historical v1 certificates have no attestation rows, so
#: A5′ misses them honestly (task §46 ramp): the full path re-certifies
#: them under v2 and back-fills the face.
#: P2A: bumped v2→v3 — the parser identity representation changed
#: (per-process pointer artifact → stable decoded CXString text, see
#: P2A_BUG_ANALYSIS.md).  No compatibility logic equates old pointer-derived
#: identities with new stable ones: v2 certificates are simply ineligible
#: (A3 cert_version filter) and the full path re-certifies them honestly.
#: Slice-4B: bumped v3→v4 for C6 (-Wp, NCC correctness) + C2 (joined
#:   -iquote/-idirafter include_list coverage).  Old certs with the
#:   buggy NCC serial are honestly ineligible.
CERT_VERSION = "fwctx-cert/v4"
CLASS_C_DOMAIN = "fwctx-classc/v1"
MACRO_ENV_DOMAIN = "fwctx-macroenv/v1"
PGEN_DOMAIN = "fwctx-pgen/v1"

#: Bump whenever certification CAPTURE semantics change (never per-run).
#: F3P (Slice-6C F3P/F1.1): 1→2 — position capture now resolves the
#: captured libclang file identity against the certified file table
#: (canonicalized, fail-closed UNMAPPED_ORDINAL instead of defaulting to
#: the main file) and a within-TU definition subsumes its declaration
#: row.  Old generation-1 states keep their immutable (badly anchored)
#: positions; the new salt changes L3, so re-certified TUs form NEW
#: states instead of tripping the W-1 same-key/different-facts poison.
INDEXER_GENERATION = 2


# ── evidence containers ──────────────────────────────────────────────────────

@dataclass
class ClosureMember:
    """One resolved dependency-closure member (c4 leg), manifest-parity."""

    abs_path: str
    display_path: str            # project-relative posix, else absolute
    blob_sha: str | None         # cert-grade content digest; None ⇒ NOT_FOUND
    generated: bool              # diagnostic only (cert rehashes regardless)
    role: str = "header"
    text: str | None = None      # decoded current bytes (condition regions)


@dataclass
class ParserIdentity:
    """Certified class-C identity components (SLICE1_CLASS_C_DIGEST_SPEC §1)."""

    indexer_generation: int
    libclang_id: str
    capture_binary_id: str
    builtin_digest: str
    builtin_count: int
    target_triple: str
    failed: str = ""             # non-empty ⇒ capture failure (poison reason)

    @property
    def identity_sha(self) -> str:
        return h(CLASS_C_DOMAIN,
                 str(self.indexer_generation), self.libclang_id,
                 self.capture_binary_id, self.builtin_digest,
                 self.target_triple)

    @property
    def generation(self) -> int:
        """Deterministic across stores/clones (rides L3 + the identity pair)."""
        digest = h(PGEN_DOMAIN,
                   str(self.indexer_generation), self.libclang_id,
                   self.capture_binary_id, self.builtin_digest,
                   self.target_triple)
        return int(digest[:16], 16) & ((1 << 63) - 1)

    def environment_digest(self) -> str:
        return h(CLASS_C_DOMAIN,
                 str(self.indexer_generation), self.libclang_id,
                 self.capture_binary_id, self.builtin_digest,
                 self.target_triple)

    def as_dict(self) -> dict:
        return {
            "indexer_generation": self.indexer_generation,
            "libclang_id": self.libclang_id,
            "capture_binary_id": self.capture_binary_id,
            "builtin_digest": self.builtin_digest,
            "builtin_count": self.builtin_count,
            "target_triple": self.target_triple,
            "identity_sha": self.identity_sha,
            "generation": self.generation,
        }


@dataclass
class TUCertificate:
    """Per-TU production certification outcome (stored in ``tu_certificates``)."""

    cert_version: str
    status: str                  # "certified" | "poisoned"
    poison_reason: str
    inputs: CertifiedSemanticInputs | None
    key_result: TUKeyResult | None
    closure: list[ClosureMember]
    forced_legs: tuple[str, ...]
    ncc_serial: str              # class-C post-step applied
    ncc_warnings: list[str]
    rsp_warnings: list[str]
    macro_env_digest: str
    macro_env_count: int
    parser_identity: ParserIdentity
    source_blob_sha: str
    language: str
    context_id: str
    config_hash: str
    main_file_display: str
    main_file_abs_path: str = ""  # F3: absolute path for content capture
    path_spelling_sensitive: bool = False   # A-2 (QRP §14.3): token stream
                                            #   references __FILE__-class builtins
    include_list: list[str] = field(default_factory=list)  # Slice-4A A5′:
                                            #   ordered NCC include roots
                                            #   ({flag}={path} tokens) for the
                                            #   dir-attestation capture face
    timings: dict[str, float] = field(default_factory=dict)
    # Conditional-compilation branch states (per-TU evaluation, T03/ADR-011).
    # Evaluated from clang skipped_ranges at certification time, copied to
    # tu_branch_states at bind time.  Per-binding because the same
    # SemanticState can be bound to worlds with different macro
    # environments — the state captures shared semantic facts, while
    # branch states resolve the active/inactive ambiguity.
    # Format: list of (region_ordinal, branch_ordinal, taken) tuples
    # where region_ordinal matches semantic_condition_index and
    # taken is 1 (TAKEN), 0 (FALSE), or -1 (NOT_EVALUATED).
    branch_states: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def tusemantic_key(self) -> str:
        return self.key_result.root if self.key_result else ""

    @property
    def parser_generation(self) -> int:
        return self.parser_identity.generation

    def closure_rows(self) -> list[dict]:
        return [{"path": m.display_path, "blob_sha": m.blob_sha,
                 "generated": m.generated, "role": m.role}
                for m in self.closure]


# ── parser / capture identity (lru-cached, subprocess-backed) ────────────────

_libclang_lock = threading.Lock()
_libclang_cache: dict[str, str] = {}
_cli_cache: dict[str, str] = {}
_triple_cache: dict[str, str] = {}


def _libclang_version_text() -> str:
    """P2A: extract ``clang_getClangVersion()`` text via the binding's
    canonical CXString→str path.  Returns "" when extraction fails (the
    caller falls back).  NEVER derives identity from object ``repr``,
    ctypes pointers, or addresses: the pre-P2A code called the unregistered
    binding function with ctypes' default ``restype=c_int``, which on the
    Windows x64 ABI routed a 16-byte struct return through an uninitialized
    hidden sret pointer (undefined behavior) and surfaced a per-process
    pointer artifact as the "identity" (P2A_BUG_ANALYSIS.md §3-5)."""
    from clang import cindex as cx
    lib = cx.conf.lib
    cxstring_ty = getattr(cx, "_CXString", None) or cx.CXString
    # Register the TRUE prototype once per process: CXString struct by
    # value, no arguments.  The binding never registers this function, and
    # nothing else in the binding or fw-context calls it, so this is
    # side-effect free for the rest of the process.
    lib.clang_getClangVersion.argtypes = []
    lib.clang_getClangVersion.restype = cxstring_ty
    ver = lib.clang_getClangVersion()
    # clang_getCString IS registered by the binding ([_CXString] ->
    # c_interop_string with to_python_string), i.e. the canonical CXString
    # contents extraction for this binding.  Do NOT dispose the CXString
    # manually: the binding's _CXString.__del__ already calls
    # clang_disposeString, and a manual dispose would double-free
    # (heap-corruption crash at interpreter exit, seen in P2A debugging).
    return lib.clang_getCString(ver) or ""


def libclang_identity() -> str:
    """Identity of the libclang library the parse actually ran on.

    P2A: content-derived ONLY — the decoded version text returned by
    libclang through ``clang_getCString`` (the binding's canonical CXString
    conversion).  Same parser environment ⇒ same string in every process;
    a failed extraction falls back to the deterministic loaded-library path
    (per-machine stable, conservative across machines) and ultimately to a
    constant — never a per-process address.

    Slice-4A boundary (deliberately preserved): a stable version TEXT does
    NOT prove binary equality after an IN-PLACE toolchain replacement —
    that residual risk stays fail-closed behind the mandatory A-c5 macro
    probe.  This function only makes the identity restart-stable; it does
    not weaken J2 and must not be read as license to skip A-c5 (that
    remains Slice-4B territory)."""
    with _libclang_lock:
        if _libclang_cache:
            return _libclang_cache["id"]
        try:
            text = _libclang_version_text()
        except Exception:                                     # pragma: no cover
            text = ""
        if not text:
            try:
                from clang import cindex as cx
                text = f"libclang-file:{cx.conf.get_filename()}"
            except Exception:
                text = "libclang:unknown"
        _libclang_cache["id"] = f"libclang:{text.strip()}"
        return _libclang_cache["id"]


def _find_clang() -> str:
    from fw_context_mcp.indexer.macros import _find_clang_binary
    return _find_clang_binary()


def clang_cli_identity() -> str:
    """Identity of the binary used for ``-dM -E`` capture: path + version."""
    with _libclang_lock:
        cached = _cli_cache.get("id")
        if cached:
            return cached
    binary = _find_clang()
    version_line = ""
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=30)
        version_line = (out.stdout or "").strip().splitlines()[0] if out.stdout else ""
    except Exception:
        version_line = ""
    if not version_line:
        raise RuntimeError(f"clang capture binary identity unavailable: {binary!r}")
    ident = f"clang-cli:{binary}|{version_line}"
    with _libclang_lock:
        _cli_cache["id"] = ident
    return ident


def resolve_target_triple(clang_args: list[str]) -> str:
    """Explicit ``--target=``/``-target`` value, else the compiler default."""
    for i, tok in enumerate(clang_args):
        if tok.startswith("--target="):
            return tok[len("--target="):].strip()
        if tok == "-target" and i + 1 < len(clang_args):
            return clang_args[i + 1].strip()
    with _libclang_lock:
        cached = _triple_cache.get("default")
        if cached is not None:
            return cached
    binary = _find_clang()
    try:
        out = subprocess.run([binary, "-dumpmachine"], capture_output=True,
                             text=True, timeout=30)
        triple = (out.stdout or "").strip().splitlines()[0] if out.stdout else ""
    except Exception:
        triple = ""
    if not triple:
        raise RuntimeError("target triple unavailable (no --target and "
                           "'clang -dumpmachine' failed)")
    with _libclang_lock:
        _triple_cache["default"] = triple
    return triple


def _run_dm_e(flags: list[str], source: Path,
              cwd: str | Path | None = None) -> list[str]:
    """Run ``clang -dM -E`` and return the raw ``#define`` lines (verbatim)."""
    binary = _find_clang()
    cmd = [binary, "-dM", "-E", *flags, str(source)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                              cwd=None if cwd is None else str(cwd))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"-dM -E timeout for {source.name}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"-dM -E failed rc={proc.returncode} for {source.name}: "
            f"{(proc.stderr or '')[-200:]}")
    return [ln.rstrip("\r\n") for ln in (proc.stdout or "").splitlines()
            if ln.startswith("#define")]


def macro_environment_digest(lines: list[str]) -> tuple[str, int]:
    """Digest over the SORTED raw ``-dM -E`` lines (object- AND function-like)."""
    ordered = sorted(lines)
    digest = h(MACRO_ENV_DOMAIN, *ordered)
    return digest, len(ordered)


# ── argv / forced-input / rsp capture ────────────────────────────────────────

def _raw_argv(unit) -> list[str]:
    """The TRUE compile-command argv (compile_commands.json, pre-normalization)."""
    entry = unit.raw_entry or {}
    args = entry.get("arguments")
    if args:
        return [str(a) for a in args]
    command = entry.get("command")
    if command:
        return shlex.split(command)
    # No raw entry (synthetic unit): fall back to the normalized clang args
    # with the unit file re-appended (documented fallback; byte-compat tests
    # always supply raw_entry).
    return [str(unit.file), *unit.clang_args]


def _expand_rsp(raw_argv: list[str], directory: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Expand ``@rsp`` tokens once; return contents map + unreadable names."""
    from .ncc import _posix
    from fw_context_mcp.indexer.compile_commands import expand_response_file
    contents: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for tok in raw_argv:
        if tok.startswith("@"):
            name = tok[1:]
            expanded = expand_response_file(tok, directory)
            if not expanded:
                unreadable.append(name)
            contents[_posix(name)] = expanded
            contents[name.split("/")[-1]] = expanded
    return contents, unreadable


def _collect_forced_inputs(expanded_argv: list[str]) -> list[str]:
    """Ordered ``-include``/``-imacros`` file arguments (both spellings)."""
    forced: list[str] = []
    i, n = 0, len(expanded_argv)
    while i < n:
        tok = expanded_argv[i]
        for flag in ("-include", "-imacros"):
            if tok == flag and i + 1 < n:
                forced.append(expanded_argv[i + 1])
                i += 2
                break
            if tok.startswith(flag) and len(tok) > len(flag) and tok[len(flag)] in "=:":
                forced.append(tok[len(flag) + 1:])
                i += 1
                break
        else:
            i += 1
    return forced


def _strip_for_builtin(flags: list[str]) -> list[str]:
    """Drop input-affecting flags for the pure-builtin predefine run."""
    drop_with_arg = {"-D", "-U", "-include", "-imacros", "-I", "-isystem",
                     "-iquote", "-idirafter", "-F", "-include-pch"}
    drop_prefix = ("-D", "-U", "-I", "-isystem", "-iquote", "-idirafter",
                   "-F", "-include", "-imacros")
    out: list[str] = []
    i, n = 0, len(flags)
    while i < n:
        tok = flags[i]
        if tok in drop_with_arg and i + 1 < n:
            i += 2
            continue
        if tok.startswith(drop_prefix):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


# ── C1 (Slice-4B): implicit include-chain discovery ──────────────────────────

_implicit_chain_cache: dict[str, list[str]] = {}
_implicit_chain_lock = threading.Lock()


def discover_implicit_include_dirs(binary: str,
                                    target_flags: list[str]) -> list[str]:
    """C1 (Slice-4B §4/§5): discover the compiler's implicit include
    search chain via ``clang -v -E`` on an empty TU.

    The implicit chain depends on: the compiler binary, the target triple,
    the sysroot, and the language mode — NOT on TU source or closure.

    The cache key is ``(binary, tuple(sorted(target_flags)))`` — process-local
    only (amortization, not authority).  The first call runs the probe;
    subsequent calls with the same key reuse the result.  The authority is
    the actual probe output, not the key.

    Returns a list of absolute directory paths.  Returns ``[]`` on any
    failure (admission will MISS honestly — fail closed).
    """
    key = (binary, tuple(sorted(target_flags)))
    with _implicit_chain_lock:
        if key in _implicit_chain_cache:
            return _implicit_chain_cache[key]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # Match the TU language: -std=c++.. requires a .cpp source.
            is_cpp = any(f.startswith(("-std=c++", "-std=gnu++")) for f in target_flags)
            empty = Path(tmp) / ("empty.cpp" if is_cpp else "empty.c")
            empty.write_text("/* implicit chain probe */\n", encoding="utf-8")
            cmd = [binary, "-v", "-E", *target_flags, str(empty)]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=30)
        dirs = _parse_implicit_search_dirs(proc.stderr or "")
    except Exception:
        dirs = []
    with _implicit_chain_lock:
        _implicit_chain_cache[key] = dirs
    return dirs


def _parse_implicit_search_dirs(stderr_text: str) -> list[str]:
    """Parse ``#include <...> search starts here:`` ... ``End of search list.``
    from ``clang -v -E`` stderr.  Returns absolute directory paths."""
    dirs: list[str] = []
    in_list = False
    for line in stderr_text.splitlines():
        stripped = line.strip()
        if "#include <...> search starts here:" in stripped:
            in_list = True
            continue
        if "End of search list." in stripped:
            break
        if in_list and stripped:
            # clang prefixes with a space; strip whitespace.
            d = stripped.strip()
            if d.startswith("(frame directory"):
                continue
            dirs.append(d)
    return dirs


# ── C5 (Slice-4B): content-derived builtin/toolchain proof ───────────────────

_builtin_digest_cache: dict[str, str | None] = {}
_builtin_digest_lock = threading.Lock()


def rederive_builtin_digest(expanded_argv: list[str]) -> str | None:
    """C5 (Slice-4B §10-§13): re-derive the compiler's builtin predefine
    digest from the CURRENT toolchain and return it.

    This is the content-derived proof that closes J2 (in-place toolchain
    replacement with stable version strings).  It runs ``clang -dM -E`` on
    an EMPTY TU with target/sysroot flags only — the same probe the
    certifier used (``_certify_parser_identity``) — so the result is
    TU-independent and safe to amortize per ``(binary, target_flags)``.

    Cache safety (task §12):
      * lookup key: ``(binary, tuple(sorted(target_flags)))`` — a
        content-derived key, NOT a string-only key.  The binary identity
        is established by ``clang_cli_identity()`` (content-derived path +
        version), and the target_flags select the predefine surface.
      * trust boundary: the cache holds the RESULT of a fresh content
        probe run in the CURRENT process.  It is NOT authority across
        processes — each fresh process re-probes.  The persistent
        authority is the ``builtin_digest`` column in
        ``tu_certificates`` / ``parser_generations``.
      * content digest: the cached value IS the content digest
        (``macro_environment_digest`` over the ``-dM -E`` output).
      * lifetime: process-local.  Invalidated by process exit.
      * invalidation: a different (binary, target_flags) key ⇒ cache miss
        ⇒ fresh probe.  An in-place binary replacement with the SAME
        path+version but DIFFERENT builtin output ⇒ the cache key is the
        same BUT the probe re-runs only if the cache is cold; on a warm
        cache the STALE value would be returned.

    IMPORTANT — warm-cache J2 caveat:
      The C5 cache is process-local.  If a toolchain is replaced IN-PLACE
      (same path, same version string, different builtin output) DURING a
      single process's lifetime, the warm cache would return the stale
      digest.  This is acceptable because:
        1. The certifier (full path) ALWAYS re-probes fresh — the
           certified ``builtin_digest`` reflects the toolchain at cert
           time.
        2. Admission (fast path) compares the cached current digest to
           the certified digest.  If the toolchain changed between
           certification (process A) and admission (process B), the
           admission process's cache is COLD (fresh process) ⇒ it
           re-probes ⇒ catches the change.
        3. An in-place change DURING a single admission process is not a
           realistic attack vector for a build indexer (the toolchain is
           installed once and not mutated mid-build).
      The cross-process restart scenario (task §29) is the authoritative
      test: cert in process A, admit in process B (cold cache) ⇒ C5
      catches the change.

    Returns the builtin digest string, or ``None`` if the probe could not
    establish evidence (admission treats ``None`` as UNKNOWN ⇒ FAST_MISS).
    """
    try:
        binary = _find_clang()
        from fw_context_mcp.indexer.macros import _find_clang_binary, _sanitize_flags
        _san = _sanitize_flags([_find_clang_binary(), *expanded_argv])
        flags = _san[1:] if _san and not _san[0].startswith("-") else _san
    except Exception:
        return None
    std_target_flags = [f for f in flags
                        if f.startswith(("-std=", "-m", "-target",
                                         "--target=", "--sysroot="))]
    key = (binary, tuple(sorted(std_target_flags)))
    with _builtin_digest_lock:
        if key in _builtin_digest_cache:
            return _builtin_digest_cache[key]
    try:
        stripped = _strip_for_builtin(std_target_flags)
        with tempfile.TemporaryDirectory() as tmp:
            # Match the TU language: -std=c++.. requires a .cpp source.
            is_cpp = any(f.startswith(("-std=c++", "-std=gnu++"))
                         for f in std_target_flags)
            empty = Path(tmp) / ("empty.cpp" if is_cpp else "empty.c")
            empty.write_text("/* C5 builtin probe */\n", encoding="utf-8")
            lines = _run_dm_e(stripped, empty)
        digest, _ = macro_environment_digest(lines)
    except Exception:
        digest = None
    with _builtin_digest_lock:
        _builtin_digest_cache[key] = digest
    return digest


# ── closure walk + cert-grade rehash ─────────────────────────────────────────

def _closure_walk(parsed, project_root: Path,
                  build_dir_patterns: list[str] | None = None
                  ) -> tuple[list[ClosureMember], list[str]]:
    """First-inclusion-ordered unique closure from the LIVE parse (manifest parity).

    ``build_dir_patterns`` feeds ONLY the diagnostic ``generated`` flag
    (manifest parity); certification re-hashes every member's current bytes
    regardless of it.  Slice 1 passes None (no pattern source in the hook)."""
    from fw_context_mcp.indexer.manifest import _is_generated_header
    members: list[ClosureMember] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for inc in parsed.tu.get_includes():
        try:
            abs_path = str(inc.include.name)
        except Exception:
            continue
        if not abs_path or abs_path in seen:
            continue
        seen.add(abs_path)
        try:
            resolved = Path(abs_path).resolve()
        except OSError:
            resolved = Path(abs_path)
        try:
            display = resolved.relative_to(project_root).as_posix()
        except ValueError:
            display = resolved.as_posix()
        members.append(ClosureMember(
            abs_path=resolved.as_posix(),
            display_path=display,
            blob_sha=None,
            generated=_is_generated_header(display, build_dir_patterns),
        ))
    return members, unresolved


def _diagnose_unresolved_includes(parsed) -> list[str]:
    """Error-class diagnostics naming missing files ⇒ closure incomplete."""
    bad: list[str] = []
    try:
        diags = list(parsed.tu.diagnostics)
    except Exception:
        return bad
    for d in diags:
        try:
            if d.severity < d.Error:
                continue
            text = d.spelling or ""
        except Exception:
            continue
        # clang: "'x.h' file not found"; gcc: "x.h: No such file or directory"
        if "file not found" in text or "No such file or directory" in text:
            bad.append(text)
    return bad


def _rehash_member(member: ClosureMember) -> tuple[str | None, str | None]:
    """Cert-grade re-read: current bytes → sha256 + decoded text. No trust. (push sentinel)"""
    try:
        raw = Path(member.abs_path).read_bytes()
    except OSError:
        return None, None
    digest = hashlib.sha256(raw).hexdigest()
    return digest, raw.decode("utf-8", errors="replace")


def _tu_path_spelling_sensitive(parsed) -> bool:
    """A-2 parse-output guard (QRP_READ_PATH_DESIGN §14.3): does the TU's
    token stream reference the path-sensitive builtin family
    (``__FILE__`` / ``__BASE_FILE__`` / ``__INCLUDE_LEVEL__``)?

    Failure to scan fails CLOSED (flag = True — false non-reuse accepted;
    false sharing never).  The ``-dM`` macro table is NOT consulted: clang
    never dumps dynamic builtins (probed: no ``__FILE__`` line in
    ``clang -dM -E`` output), so macro capture is rename-invariant.
    """
    try:
        tu = getattr(parsed, "tu", None)
        if tu is None:
            return True
        family = {"__FILE__", "__BASE_FILE__", "__INCLUDE_LEVEL__"}
        # Cursor.get_tokens() is the portable entry point: the TranslationUnit
        # variant of some clang bindings mishandles positional extents.
        for tok in tu.cursor.get_tokens():
            if tok.spelling in family:
                return True
        return False
    except Exception:
        return True


def _project_rel(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except (ValueError, OSError):
        return path.resolve().as_posix()


# ── class-C post-step ────────────────────────────────────────────────────────

def _ext_token_parts(token: str) -> tuple[str, str] | None:
    """Split ``ext:<role>:<path>`` (canonicalizer stand-in) into (role, path)."""
    if not token.startswith("ext:"):
        return None
    rest = token[len("ext:"):]
    role, sep, path = rest.partition(":")
    if not sep or not path:
        return None
    return role, path


def certified_roots_from_argv(expanded_argv: list[str]) -> list[str]:
    """Certified environment roots (Slice 1: the ``--sysroot=`` value)."""
    roots: list[str] = []
    for i, tok in enumerate(expanded_argv):
        if tok.startswith("--sysroot=") and tok[len("--sysroot="):]:
            roots.append(os.path.abspath(tok[len("--sysroot="):]))
        elif tok == "--sysroot" and i + 1 < len(expanded_argv):
            roots.append(os.path.abspath(expanded_argv[i + 1]))
    return roots


def apply_class_c_post_step(ncc_serial: str, environment_digest: str,
                            certified_roots: list[str]) -> str:
    """Replace certified ``ext:*`` tokens with logical env descriptors
    (SLICE1_CLASS_C_DIGEST_SPEC §3); uncertified tokens stay literal."""
    import posixpath
    if not certified_roots:
        return ncc_serial
    norm_roots = [posixpath.normcase(posixpath.normpath(r.replace("\\", "/")))
                  for r in certified_roots]
    out: list[str] = []
    for token in ncc_serial.split(SEP):
        parts = _ext_token_parts(token)
        if parts is None:
            out.append(token)
            continue
        role, path = parts
        np = posixpath.normcase(posixpath.normpath(path.replace("\\", "/")))
        replaced = False
        for root in norm_roots:
            if np == root:
                out.append(f"envcert:{environment_digest}:<root>")
                replaced = True
                break
            if np.startswith(root + "/"):
                out.append(f"envcert:{environment_digest}:{np[len(root) + 1:]}")
                replaced = True
                break
        if not replaced:
            out.append(token)
    return SEP.join(out)


# ── the certification build ──────────────────────────────────────────────────

def build_certificate(unit, parsed, project_root: Path, *, nonce: str,
                      context_id: str = "", config_hash: str = "",
                      build_dir_patterns: list[str] | None = None) -> TUCertificate:
    """Production per-TU certification. Never raises; failures poison."""
    t = {"total": time.monotonic()}
    main_path = Path(unit.file)
    project_root = Path(project_root)
    # Resolve to absolute: unit.file may be relative to the compile
    # command's directory.  An absolute main_path is required for path
    # matching against clang's skipped-range file paths (which are
    # always absolute) and for reading the main file's content.
    try:
        if not main_path.is_absolute():
            main_path = (unit.directory / main_path).resolve() if \
                getattr(unit, "directory", None) else main_path.resolve()
        else:
            main_path = main_path.resolve()
    except OSError:
        pass  # keep original on resolution failure (rare; e.g. network path)
    display_main = _project_rel(main_path, project_root)

    # A-2 parse-output guard (Slice 2): token-stream detection of the
    # path-sensitive builtin family.  Compute once; both certified and
    # poisoned certificates carry it (only published states bind).
    spelling_flag = _tu_path_spelling_sensitive(parsed)

    def _poison(reason: str, **kw) -> TUCertificate:
        t["total"] = time.monotonic() - t["total"]
        return TUCertificate(
            cert_version=CERT_VERSION, status="poisoned", poison_reason=reason,
            inputs=None, key_result=None, closure=kw.get("closure", []),
            forced_legs=(), ncc_serial=kw.get("ncc_serial", ""),
            ncc_warnings=kw.get("ncc_warnings", []),
            rsp_warnings=kw.get("rsp_warnings", []),
            macro_env_digest=kw.get("macro_env_digest", ""),
            macro_env_count=kw.get("macro_env_count", 0),
            parser_identity=kw.get("parser_identity") or _failed_identity(),
            source_blob_sha=kw.get("source_blob_sha", ""), language=unit.language,
            context_id=context_id, config_hash=config_hash,
            main_file_display=display_main,
            main_file_abs_path=str(main_path),
            path_spelling_sensitive=spelling_flag, timings=t)

    # 0. main source (L0) — current bytes, no trust, no cache.
    t0 = time.monotonic()
    try:
        source_blob = hashlib.sha256(main_path.read_bytes()).hexdigest()
    except OSError as exc:
        return _poison(f"source-unreadable:{display_main}:{exc}")
    t["source_hash"] = time.monotonic() - t0

    # 1. raw argv + rsp expansion.
    t0 = time.monotonic()
    raw_argv = _raw_argv(unit)
    rsp_contents, rsp_unreadable = _expand_rsp(raw_argv, Path(unit.directory))
    if rsp_unreadable:
        return _poison("rsp-unreadable:" + ",".join(sorted(rsp_unreadable)),
                       source_blob_sha=source_blob)
    # Post-rsp-expansion argv (canonicalize re-expands deterministically from
    # the map; this expansion is for forced-input collection + roots).
    expanded: list[str] = []
    for tok in raw_argv:
        if tok.startswith("@") and rsp_contents.get(tok[1:]) is not None:
            expanded.extend(rsp_contents[tok[1:]])
        elif tok.startswith("@"):
            expanded.extend(rsp_contents.get(tok[1:].split("/")[-1], []))
        else:
            expanded.append(tok)
    # argv[0] is the compiler driver ([S2]§3.2): not a flag, never an input.
    if expanded and not expanded[0].startswith("-"):
        expanded = expanded[1:]
    t["argv_rsp"] = time.monotonic() - t0

    # 2. forced inputs (c3) — content read at certification grade.
    t0 = time.monotonic()
    forced_names = _collect_forced_inputs(expanded)
    file_contents: dict[str, str] = {}
    forced_legs: list[str] = []
    for name in forced_names:
        fpath = Path(name)
        if not fpath.is_absolute():
            fpath = Path(unit.directory) / name
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return _poison(f"forced-input-unreadable:{name}:{exc}",
                           source_blob_sha=source_blob)
        from .ncc import _posix
        file_contents[_posix(name)] = text
        file_contents[fpath.name] = text
        forced_legs.append(hashlib.sha256(
            fpath.read_bytes()).hexdigest())
    t["forced_inputs"] = time.monotonic() - t0

    # 3. NCC via the frozen S2 canonicalizer (byte-compat surface = PRE post-step).
    t0 = time.monotonic()
    try:
        cc = canonicalize(raw_argv, compile_dir=str(unit.directory),
                          project_root=str(project_root),
                          file_contents=file_contents, rsp_contents=rsp_contents)
    except Exception as exc:                                   # pragma: no cover
        return _poison(f"ncc-canonicalization-failed:{exc}",
                       source_blob_sha=source_blob)
    t["ncc"] = time.monotonic() - t0

    # 4. dependency closure from the SAME live parse + cert-grade rehash.
    t0 = time.monotonic()
    members, _ = _closure_walk(parsed, project_root, build_dir_patterns)
    unresolved = _diagnose_unresolved_includes(parsed)
    closure_complete = True
    poison_reason = ""
    if unresolved:
        closure_complete = False
        poison_reason = ("unresolved-include:" +
                         unresolved[0][:160].replace("\n", " "))
    cert_sequence: list[str] = []
    for member in members:
        sha, text = _rehash_member(member)
        member.blob_sha = sha
        member.text = text
        cert_sequence.append(sha if sha else NOT_FOUND)
        if sha is None:
            closure_complete = False
            poison_reason = poison_reason or f"closure-member-unreadable:{member.display_path}"
    t["closure_rehash"] = time.monotonic() - t0
    if poison_reason:
        return _poison(poison_reason, source_blob_sha=source_blob,
                       closure=members, ncc_serial=cc.ncc_serial,
                       ncc_warnings=list(cc.warnings))

    # 5. macro environment (c5) — full -dM -E over the CERTIFIED argv (the
    # post-rsp-expansion compile command), function-like macros included.
    t0 = time.monotonic()
    from fw_context_mcp.indexer.macros import _find_clang_binary, _sanitize_flags
    # _sanitize_flags keeps cmd[0] (the binary) — prepend then drop it.
    sanitized = _sanitize_flags([_find_clang_binary(), *expanded])
    flags = sanitized[1:]
    try:
        lines = _run_dm_e(flags, main_path, cwd=unit.directory)
        macro_digest, macro_count = macro_environment_digest(lines)
    except RuntimeError as exc:
        return _poison(f"macro-capture-failed:{exc}", source_blob_sha=source_blob,
                       closure=members, ncc_serial=cc.ncc_serial,
                       ncc_warnings=list(cc.warnings))
    t["macro_env"] = time.monotonic() - t0

    # 6. class-C parser identity + generation + salts.
    t0 = time.monotonic()
    identity = _certify_parser_identity(expanded, flags)
    if identity.failed:
        return _poison(f"class-c-capture-failed:{identity.failed}",
                       source_blob_sha=source_blob, closure=members,
                       ncc_serial=cc.ncc_serial, ncc_warnings=list(cc.warnings),
                       macro_env_digest=macro_digest, macro_env_count=macro_count)
    t["parser_identity"] = time.monotonic() - t0

    # 7. class-C post-step on the NCC + final inputs → KB-C (Slice-0 primitive).
    t0 = time.monotonic()
    env_digest = identity.environment_digest()
    ncc_serial = apply_class_c_post_step(
        cc.ncc_serial, env_digest, certified_roots_from_argv(expanded))
    toolchain_salt = h("fwctx-tc/v1", identity.capture_binary_id)[:32]
    parser_salt = h("fwctx-ps/v1", identity.libclang_id,
                    str(identity.indexer_generation))[:32]
    try:
        inputs = CertifiedSemanticInputs(
            source_blob_sha=source_blob,
            language=unit.language,
            ncc_serial=ncc_serial,
            forced_content_legs=tuple(forced_legs),
            cert_content_sequence=tuple(cert_sequence),
            macro_state_digest=macro_digest,
            environment_digest=env_digest,
            parser_generation=identity.generation,
            toolchain_salt=toolchain_salt,
            parser_salt=parser_salt,
            closure_complete=closure_complete,
            nonce=nonce,
        )
        key_result = compute_tu_semantic_key(inputs)
    except ValueError as exc:
        return _poison(f"malformed-certified-inputs:{exc}",
                       source_blob_sha=source_blob, closure=members,
                       ncc_serial=ncc_serial, ncc_warnings=list(cc.warnings),
                       macro_env_digest=macro_digest, macro_env_count=macro_count,
                       parser_identity=identity)
    t["key_compute"] = time.monotonic() - t0
    if not key_result.is_reusable:
        t["total"] = time.monotonic() - t["total"]
        return TUCertificate(
            cert_version=CERT_VERSION, status="poisoned",
            poison_reason=f"key-poisoned:{key_result.poisoned}",
            inputs=inputs, key_result=key_result, closure=members,
            forced_legs=tuple(forced_legs), ncc_serial=ncc_serial,
            ncc_warnings=list(cc.warnings), rsp_warnings=[],
            macro_env_digest=macro_digest, macro_env_count=macro_count,
            parser_identity=identity, source_blob_sha=source_blob,
            language=unit.language, context_id=context_id,
            config_hash=config_hash, main_file_display=display_main,
            main_file_abs_path=str(main_path),
            path_spelling_sensitive=spelling_flag, timings=t)

    t["total"] = time.monotonic() - t["total"]
    return TUCertificate(
        cert_version=CERT_VERSION, status="certified", poison_reason="",
        inputs=inputs, key_result=key_result, closure=members,
        forced_legs=tuple(forced_legs), ncc_serial=ncc_serial,
        ncc_warnings=list(cc.warnings), rsp_warnings=[],
        macro_env_digest=macro_digest, macro_env_count=macro_count,
        parser_identity=identity, source_blob_sha=source_blob,
        language=unit.language, context_id=context_id,
        config_hash=config_hash, main_file_display=display_main,
        main_file_abs_path=str(main_path),
        path_spelling_sensitive=spelling_flag,
        include_list=list(cc.include_list), timings=t)


def _failed_identity() -> ParserIdentity:
    return ParserIdentity(indexer_generation=INDEXER_GENERATION,
                          libclang_id="", capture_binary_id="",
                          builtin_digest="", builtin_count=0,
                          target_triple="", failed="identity-capture-failed")


def _certify_parser_identity(expanded_argv: list[str],
                             sanitized_flags: list[str]) -> ParserIdentity:
    """Certified class-C identity; any capture failure sets ``failed``."""
    try:
        libclang_id = libclang_identity()
        capture_binary_id = clang_cli_identity()
    except Exception as exc:
        ident = _failed_identity()
        ident.failed = f"identity:{exc}"
        return ident
    try:
        std_target_flags = [f for f in sanitized_flags
                            if f.startswith(("-std=", "-m", "-target",
                                             "--target=", "--sysroot="))]
        # The builtin predefine probe runs ``clang -dM -E`` on an empty
        # source file.  When the TU is C++ (``-std=c++..`` / ``-std=gnu++..``)
        # the empty file MUST be ``.cpp`` — clang rejects ``-std=c++17`` on
        # a ``.c`` file with "invalid argument '-std=c++17' not allowed with
        # 'C'", which would poison every C++ TU's class-C identity.
        is_cpp = any(f.startswith(("-std=c++", "-std=gnu++")) for f in std_target_flags)
        with tempfile.TemporaryDirectory() as tmp:
            empty_name = "empty.cpp" if is_cpp else "empty.c"
            empty = Path(tmp) / empty_name
            empty.write_text("/* builtin predefine probe */\n", encoding="utf-8")
            builtin_lines = _run_dm_e(_strip_for_builtin(std_target_flags), empty)
        builtin_digest, builtin_count = macro_environment_digest(builtin_lines)
        triple = resolve_target_triple(expanded_argv)
    except Exception as exc:
        ident = _failed_identity()
        ident.libclang_id, ident.capture_binary_id = libclang_id, capture_binary_id
        ident.failed = f"builtin-or-triple:{exc}"
        return ident
    return ParserIdentity(indexer_generation=INDEXER_GENERATION,
                          libclang_id=libclang_id,
                          capture_binary_id=capture_binary_id,
                          builtin_digest=builtin_digest,
                          builtin_count=builtin_count,
                          target_triple=triple)


# ── the production hook (legacy-isolated) ────────────────────────────────────

_flag_lock = threading.Lock()
_flag_cache: bool | None = None


def v2_shadow_enabled() -> bool:
    """Flag gate: env override ``FW_CONTEXT_SHADOW_V2`` or cfg.index flag."""
    global _flag_cache
    with _flag_lock:
        if _flag_cache is not None:
            return _flag_cache
    env = os.environ.get("FW_CONTEXT_SHADOW_V2")
    if env is not None:
        enabled = env == "1"
    else:
        try:
            from fw_context_mcp.config import load
            enabled = bool(load().index.shadow_v2_enabled)
        except Exception:
            enabled = False
    with _flag_lock:
        _flag_cache = enabled
    return enabled


def reset_flag_cache() -> None:
    global _flag_cache
    with _flag_lock:
        _flag_cache = None


def v2_store_path(db_path) -> Path:
    """The shadow v2 store path NEXT to the legacy store (same directory)."""
    db_path = Path(db_path)
    return db_path.parent.parent / db_path.parent.name / "index-v2.db"


def certify_and_publish_for_unit(*, unit, parsed, project_root,
                                 config_hash: str = "",
                                 context_id: str = "", db_path=None,
                                 world=None):
    """The ONE hook call site (``_unit_processor`` post-parse, pre-lock).

    Slice 1: per-TU certification + one-shot SemanticState publication.
    Slice 2 adds (task §33): after a successful publication, bind the TU into
    the run's World (``world`` is the :class:`V2WorldContext` constructed
    once per index run); a poisoned cert becomes an explicit poison
    disposition.  A W-1 store refusal (same key, different facts) records a
    poison disposition too — the TU's facts were refused admission, so no
    normal binding may point at the existing state.

    Returns the :class:`TUCertificate` (or None when disabled / not
    certifiable / failed).  NEVER raises — a v2 failure must not fail legacy
    indexing (task §16); legacy behavior is untouched.  NO parse-skip: the
    hook runs AFTER the normal parse; no state is ever probed pre-parse
    (task §30).
    """
    if not v2_shadow_enabled():
        return None
    if parsed is None or getattr(parsed, "tu", None) is None:
        return None
    if db_path is None:
        return None
    try:
        from .publish import open_v2_store_for_write, store_nonce
        from .facts import extract_state_facts, fact_set_digest, build_cold_payload

        db_path = Path(db_path)
        v2_path = v2_store_path(db_path)
        store = open_v2_store_for_write(v2_path)
        try:
            if world is not None:
                world.inc("parse_count")
            from .publish import record_parser_identity
            nonce = store_nonce(store)
            cert = build_certificate(unit, parsed, Path(project_root),
                                     nonce=nonce, context_id=context_id,
                                     config_hash=config_hash)
            if world is not None:
                world.inc("certification_count")
            if cert.status == "certified":
                record_parser_identity(store, cert.parser_identity)
            from .publish import insert_certificate_row
            if cert.status != "certified":
                insert_certificate_row(store, cert, outcome="poisoned",
                                       fact_set_digest="")
                if world is not None:
                    _v2_record_poison(store, world, cert, context_id)
                return cert
            facts = extract_state_facts(parsed, cert)
            # Copy per-TU branch states to the cert so bind_tu can write
            # them to tu_branch_states (T03/ADR-011/ADR-013).
            cert.branch_states = list(facts.branch_states)
            cold = build_cold_payload(parsed, cert, facts)
            digest = fact_set_digest(facts, cold)
            from .publish import publish_one_shot
            outcome = publish_one_shot(store, cert=cert, facts=facts,
                                       cold_payload=cold, fact_digest=digest,
                                       owner=_owner_id())
            # Slice-4A A5′ capture face: directory attestations written in
            # the SAME transaction as the certificate row.  A capture
            # failure yields NO rows (fail closed: future admissions miss
            # honestly, task §46); it never fails certification.
            #
            # Slice-4B C1: discover the compiler's implicit include search
            # chain (clang -v -E) so that J1(b) mutations are caught
            # without A-c5.  Discovery is best-effort at certification: a
            # failure yields no implicit-dir rows, and admission will MISS
            # honestly (the certified row has no C1 evidence to match).
            dir_attestations = None
            implicit_dirs: list[str] = []
            try:
                from .attest import dir_attestation_rows
                # C1: probe the implicit include chain using the SAME
                # toolchain/target flags used for the builtin predefine
                # probe (certify.py:_certify_parser_identity).  The chain
                # depends on (binary, target, sysroot, language mode) —
                # not on TU source — so it is safe to amortize per
                # (binary, target_flags) within the process.
                try:
                    binary = _find_clang()
                    # Derive target/sysroot flags from the unit's clang args
                    # (same source as build_certificate's `expanded` argv).
                    from fw_context_mcp.indexer.macros import _find_clang_binary, _sanitize_flags
                    _san = _sanitize_flags([_find_clang_binary(), *unit.clang_args])
                    _flags = _san[1:] if _san and not _san[0].startswith("-") else _san
                    std_target_flags = [f for f in _flags
                                        if f.startswith(("-std=", "-m", "-target",
                                                         "--target=", "--sysroot="))]
                    implicit_dirs = discover_implicit_include_dirs(
                        binary, std_target_flags)
                except Exception as exc:                      # pragma: no cover
                    log.debug("C1 implicit-chain discovery failed "
                              "(admission will MISS honestly): %s", exc)
                    implicit_dirs = []
                dir_attestations = dir_attestation_rows(
                    cert_id=0,
                    closure_display_paths=[m.display_path
                                           for m in cert.closure],
                    include_list=cert.include_list,
                    source_dir=str(unit.file.resolve().parent),
                    compile_dir=str(unit.directory),
                    project_root=str(project_root),
                    implicit_dirs=implicit_dirs)
            except Exception as exc:                      # pragma: no cover
                log.debug("Slice-4A attestation capture failed "
                          "(certification unaffected): %s", exc)
                dir_attestations = None
            insert_certificate_row(store, cert, outcome=outcome.outcome,
                                   fact_set_digest=digest,
                                   dir_attestations=dir_attestations)
            if world is not None:
                if outcome.outcome in ("published_new", "converged"):
                    _v2_bind_tu(store, world, cert, context_id,
                                is_new_state=outcome.outcome == "published_new")
                else:
                    # W-1 refusal / quarantine / foreign staging claim: the
                    # TU's state was refused admission — record an honest
                    # poison disposition; the world then cannot look complete.
                    _v2_poison_refusal(store, world, cert, context_id,
                                       outcome)
            return cert
        finally:
            store.close()
    except Exception as exc:                                   # pragma: no cover
        log.warning("v2 shadow certification/publication failed "
                    "(legacy indexing unaffected): %s", exc, exc_info=True)
        return None


def _v2_bind_tu(store, world, cert, context_id: str, *, is_new_state: bool):
    """Slice-2 binding leg (exception-isolated; task §32)."""
    try:
        from .worlds import bind_tu
        result = bind_tu(store, world, cert, tu_context_id=context_id,
                         canon_source_path=cert.main_file_display,
                         state_key=cert.tusemantic_key,
                         state_generation=cert.parser_generation,
                         is_new_state=is_new_state)
        log.debug("v2 bind %s ctx=%s -> %s", cert.main_file_display,
                  context_id[:12], result.status)
    except Exception as exc:                              # pragma: no cover
        log.warning("v2 binding failed (legacy unaffected): %s", exc)


def _v2_record_poison(store, world, cert, context_id: str):
    """Slice-2 poison disposition leg (exception-isolated; task §32)."""
    try:
        from .worlds import record_poison
        result = record_poison(store, world, cert, tu_context_id=context_id,
                               canon_source_path=cert.main_file_display)
        log.debug("v2 poison %s ctx=%s -> %s", cert.main_file_display,
                  context_id[:12], result.status)
    except Exception as exc:                              # pragma: no cover
        log.warning("v2 poison disposition failed (legacy unaffected): %s", exc)


def _v2_poison_refusal(store, world, cert, context_id: str, outcome):
    """A refused/failed publication becomes an explicit degraded disposition."""
    try:
        from .worlds import record_poison
        cert.poison_reason = (f"publication-refused:{outcome.outcome}:"
                              f"{outcome.detail}"[:200])
        result = record_poison(store, world, cert, tu_context_id=context_id,
                               canon_source_path=cert.main_file_display)
        log.debug("v2 refusal poison ctx=%s -> %s", context_id[:12],
                  result.status)
    except Exception as exc:                              # pragma: no cover
        log.warning("v2 refusal disposition failed (legacy unaffected): %s",
                    exc)


_OWNER = f"host:{os.uname().nodename if hasattr(os, 'uname') else 'win'}|" \
         f"pid:{os.getpid()}"
_owner_lock = threading.Lock()
_owner_suffix = f"|uuid:{os.urandom(8).hex()}"


def _owner_id() -> str:
    with _owner_lock:
        return _OWNER + _owner_suffix
