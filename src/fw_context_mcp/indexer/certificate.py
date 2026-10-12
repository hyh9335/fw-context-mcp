"""M9 Certificate — cross-Build TU frontend reuse layer.

Per `.dsh-control/m9_frontend_reuse/contracts/M9_SAFETY_CONTRACT.md` and
the CERTIFICATE_ARCHAEOLOGY report (`evidence/certificate_archaeology.md`):

> The production codebase already contains the certificate key:
> `store_v2.keys.compute_tu_semantic_key` (KB-C TUSemanticKey root).
> M9 does NOT reinvent it — it wires the existing key into a cross-Build
> reuse cache.

Safety principle: SAFE REUSE > MAXIMAL REUSE. False miss is acceptable.
False hit is blocking. When uncertain: MISS -> full parse.

═══════════════════════════════════════════════════════════════════════
REUSE MODEL (chicken-and-egg resolution)
═══════════════════════════════════════════════════════════════════════

The full KB-C root = H(L0, L1, L2, L3) where L2 = H(c3, c4, c5).
Leg c4 (closure content) needs `parsed.tu.get_includes()`, so the full
root can only be computed POST-parse. M9 resolves this with a two-phase
protocol:

Phase 1 — PRE-PARSE BUCKET KEY (cheap, ~0.3ms, no main-file subprocess):
    bucket_key = H(L0, L1, L3, extraction_params)
    - L0: source content hash + language
    - L1: canonical NCC argv (ORDER-preserved: -D, -U, -I order,
          -include, -std, --target, -m*)
    - L3: toolchain + libclang identity + builtins + resource-dir
          (CACHED by (libclang_id, cli_id, std_target_flags, target))
    - extraction_params: (skip_files, with_refs) — gap #1

    This is NECESSARY (if it differs, the full root differs → safe MISS)
    but NOT SUFFICIENT (if it matches, c4 might still differ → need
    closure re-verification).

Phase 2 — HIT VERIFICATION (on bucket_key match, ~0.5ms, file reads only):
    Re-read each file in the cached entry's closure_file_list, hash,
    compare to the cached closure_content_hash (c4). If all match:

    - L0 matches (source content)
    - L1 matches (argv: defines, undefines, include paths, forced includes)
    - L3 matches (toolchain: libclang, clang binary, builtins, resource-dir)
    - c4 matches (all included file contents)

    Then c5 (macro state) MUST match — macros are a deterministic
    function of (source + -D/-U + included files + builtins). And c3
    (forced-include content) is a subset of c4 (forced includes appear
    in the closure). So the full KB-C root matches → safe HIT.

═══════════════════════════════════════════════════════════════════════
RESIDUAL GAPS (from archaeology §4)
═══════════════════════════════════════════════════════════════════════
1. skip_files/with_refs extraction params: included in bucket_key.
2. -include-pch: forces MISS (PCH is build-output, unsafe to reuse).
3. discover_implicit_include_dirs: verified via L3's builtin_digest
   sensitivity (builtins change if implicit dirs change). Low risk.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SEP = "\x1f"


# ════════════════════════════════════════════════════════════════════════
# L3 CACHE — toolchain/parser identity (process-level, avoids redundant
# clang -dM -E builtin probes)
# ════════════════════════════════════════════════════════════════════════

_L3_CACHE: dict[tuple, str] = {}
_L3_CACHE_LOCK = threading.Lock()


# ════════════════════════════════════════════════════════════════════════
# Process-global certificate cache
# ════════════════════════════════════════════════════════════════════════

@dataclass
class CachedParse:
    """A cached frontend result, keyed by pre-parse bucket key."""
    parsed: Any                              # ExtractionResult from extract_all()
    full_kb_c_root: str                     # full KB-C TUSemanticKey root (post-parse)
    closure_file_list: tuple[str, ...]      # absolute paths of included files (for c4 re-verification)
    closure_content_hash: str               # c4 leg hash (for re-verification)
    extraction_params: tuple                 # (skip_files_frozenset, with_refs)
    stored_at_monotonic: float
    stored_by_build: str = ""


class CertificateCache:
    """Process-global cache of parsed ExtractionResults.

    Thread-safe. NOT persisted to disk — libclang TranslationUnit objects
    cannot be safely serialized (M8 proved cross-build PCH silently wrong).
    Lives for the indexer process lifetime.

    Safety: the bucket key captures L0+L1+L3+extraction_params. On HIT,
    c4 (closure content) is re-verified by re-reading the cached closure
    file list. If all legs match, the full KB-C root matches → safe HIT.
    """

    def __init__(self, max_entries: int = 4096):
        self._cache: dict[str, CachedParse] = {}
        self._lock = threading.Lock()
        self._max = max_entries
        self.hits = 0
        self.misses = 0
        self.bucket_hits_c4_rejected = 0  # bucket matched but c4 differed

    def get(self, key: str) -> CachedParse | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                self.hits += 1
            else:
                self.misses += 1
            return entry

    def put(self, key: str, parsed: Any, full_kb_c_root: str,
            closure_file_list: tuple[str, ...], closure_content_hash: str,
            extraction_params: tuple, build_name: str = "") -> None:
        with self._lock:
            if len(self._cache) >= self._max and key not in self._cache:
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[key] = CachedParse(
                parsed=parsed,
                full_kb_c_root=full_kb_c_root,
                closure_file_list=closure_file_list,
                closure_content_hash=closure_content_hash,
                extraction_params=extraction_params,
                stored_at_monotonic=time.monotonic(),
                stored_by_build=build_name,
            )

    def record_c4_rejection(self) -> None:
        with self._lock:
            self.bucket_hits_c4_rejected += 1

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "bucket_hits_c4_rejected": self.bucket_hits_c4_rejected,
                "entries": len(self._cache),
                "hit_rate": (self.hits / total if total > 0 else 0.0),
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0
            self.bucket_hits_c4_rejected = 0


_GLOBAL_CACHE: CertificateCache | None = None
_GLOBAL_CACHE_LOCK = threading.Lock()


def get_certificate_cache() -> CertificateCache:
    """Return the process-global certificate cache."""
    global _GLOBAL_CACHE
    if _GLOBAL_CACHE is None:
        with _GLOBAL_CACHE_LOCK:
            if _GLOBAL_CACHE is None:
                _GLOBAL_CACHE = CertificateCache()
    return _GLOBAL_CACHE


def reset_certificate_cache() -> None:
    """Test seam: clear the global cache between tests."""
    global _GLOBAL_CACHE
    with _GLOBAL_CACHE_LOCK:
        if _GLOBAL_CACHE is not None:
            _GLOBAL_CACHE.clear()
        _GLOBAL_CACHE = None
    with _L3_CACHE_LOCK:
        _L3_CACHE.clear()
        _L3_BUILTIN_CACHE.clear()
        _L3_FULL_CACHE.clear()


# ════════════════════════════════════════════════════════════════════════
# Pre-parse bucket key computation
# ════════════════════════════════════════════════════════════════════════

def _raw_argv(unit) -> list[str]:
    """Extract the raw argv from a CompilationUnit."""
    raw = getattr(unit, "raw_entry", None)
    if raw is None:
        return list(getattr(unit, "clang_args", []))
    if "arguments" in raw:
        return list(raw["arguments"])
    if "command" in raw:
        import shlex
        return shlex.split(raw["command"])
    return list(getattr(unit, "clang_args", []))


def _expand_rsp_safe(raw_argv: list[str], compile_dir: Path) -> tuple[dict, list[str]]:
    """Expand response files safely (best-effort)."""
    from fw_context_mcp.store_v2.certify import _expand_rsp
    try:
        return _expand_rsp(raw_argv, compile_dir)
    except Exception:
        return {}, []


def _expand_rsp_tokens(raw_argv: list[str], rsp_contents: dict) -> list[str]:
    """Expand @response_file tokens using resolved rsp_contents."""
    expanded: list[str] = []
    for tok in raw_argv:
        if tok.startswith("@") and rsp_contents.get(tok[1:]) is not None:
            expanded.extend(rsp_contents[tok[1:]])
        elif tok.startswith("@") and rsp_contents.get(tok[1:].split("/")[-1]) is not None:
            expanded.extend(rsp_contents[tok[1:].split("/")[-1]])
        else:
            expanded.append(tok)
    if expanded and not expanded[0].startswith("-"):
        expanded = expanded[1:]
    return expanded


def compute_bucket_key(
    unit,
    project_root: Path,
    *,
    skip_files: set[str] | None = None,
    with_refs: bool = True,
) -> str:
    """Compute the pre-parse bucket key for one TU.

    This is the cache-lookup key. It captures every frontend-relevant
    input that can be computed WITHOUT parsing:
    - L0: source content hash + language
    - L1: canonical NCC argv (ORDER-preserved)
    - L3: toolchain + libclang identity + builtins (CACHED)

    NOT included (verified post-HIT via closure re-read):
    - c4: resolved closure content (re-verified by re-reading cached file list)
    - c5: macro state (redundant given L0+L1+c4+L3)
    - c3: forced-include content (subset of c4 — forced includes are in closure)

    Gap #2: if `-include-pch` is present, returns a sentinel forcing MISS.
    """
    # Gap #2: -include-pch forces MISS (PCH is build-output, varies per build)
    raw_argv = _raw_argv(unit)
    if any(tok.startswith("-include-pch") for tok in raw_argv):
        return "FORCE_MISS_PCH"

    # L0: source content
    try:
        source_blob = hashlib.sha256(Path(unit.file).read_bytes()).hexdigest()
    except OSError:
        return "FORCE_MISS_SOURCE_UNREADABLE"

    # Expand rsp files + prepare for NCC canonicalization
    rsp_contents, _ = _expand_rsp_safe(raw_argv, Path(unit.directory))
    expanded = _expand_rsp_tokens(raw_argv, rsp_contents)

    # L1: canonical NCC argv (ORDER-preserved) — pre-class-C-post-step
    file_contents: dict[str, str] = {}
    from fw_context_mcp.store_v2.certify import _collect_forced_inputs
    from fw_context_mcp.store_v2.ncc import _posix, canonicalize
    forced_names = _collect_forced_inputs(expanded)
    for name in forced_names:
        fpath = Path(name)
        if not fpath.is_absolute():
            fpath = Path(unit.directory) / name
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            file_contents[_posix(name)] = text
            file_contents[fpath.name] = text
        except OSError:
            pass
    try:
        cc = canonicalize(raw_argv, compile_dir=str(unit.directory),
                          project_root=str(project_root),
                          file_contents=file_contents, rsp_contents=rsp_contents)
        ncc_serial_pre = cc.ncc_serial
    except Exception:
        return "FORCE_MISS_NCC_FAILED"

    # L3: toolchain + parser identity (cached) + class-C post-step on NCC
    # The class-C post-step needs env_digest from parser identity, so L3
    # and the post-class-C NCC serial are computed together and cached.
    from fw_context_mcp.indexer.macros import _find_clang_binary, _sanitize_flags
    sanitized = _sanitize_flags([_find_clang_binary(), *expanded])
    flags = sanitized[1:]
    l3_result = _compute_l3_with_env(expanded, flags, ncc_serial_pre, project_root)
    if l3_result is None:
        return "FORCE_MISS_L3_FAILED"
    l3, ncc_serial = l3_result  # ncc_serial is now post-class-C

    # Gap #1: extraction params
    extraction_params = (frozenset(skip_files) if skip_files else frozenset(), with_refs)

    # Compose bucket key
    # L0 includes the main file PATH (not just content) to catch __FILE__-
    # dependent code. Two files with identical content at different paths
    # produce different bucket keys — safe MISS (acceptable per safety
    # principle: false miss OK; false hit blocking). The M8 positive
    # control uses the same file paths across builds, so this doesn't
    # affect the benchmark hit rate.
    main_file_path = str(Path(unit.file).resolve())
    l0 = hashlib.sha256(
        _SEP.join([source_blob, unit.language, main_file_path]).encode()
    ).hexdigest()
    l1 = hashlib.sha256(ncc_serial.encode()).hexdigest()

    composition = _SEP.join([
        "m9-bucket-v1",
        l0,
        l1,
        l3,
        _SEP.join(sorted(repr(p) for p in extraction_params[0])),
        str(extraction_params[1]),
    ])
    return hashlib.sha256(composition.encode("utf-8")).hexdigest()[:32]


# ════════════════════════════════════════════════════════════════════════
# L3 cache with env_digest (extended to also cache env_digest for NCC post-step)
# ════════════════════════════════════════════════════════════════════════

# Cache for the builtin probe (env_digest) — keyed by toolchain config ONLY.
# The builtin macros don't depend on -D/-U flags, so this cache hits for every
# TU sharing the same (libclang, clang binary, std/target flags, target).
_L3_BUILTIN_CACHE: dict[tuple, str] = {}  # (libclang_id, cli_id, std_target_flags, target) → env_digest

# Cache for the full L3 + post-class-C NCC serial — keyed by (toolchain, ncc_serial).
# The NCC post-step is cheap (string hashing), but it needs env_digest from the
# builtin probe. This cache avoids recomputing the post-step for identical TUs.
_L3_FULL_CACHE: dict[tuple, tuple[str, str]] = {}  # (toolchain_key, ncc_serial) → (l3, ncc_post)


def _compute_l3_with_env(expanded: list[str], sanitized_flags: list[str],
                         ncc_serial: str, project_root: Path) -> tuple[str, str] | None:
    """Compute L3 + env_digest, cached. Returns (l3, ncc_serial_post_class_c).

    Two-level cache:
    1. Builtin probe cache: keyed by (libclang_id, cli_id, std_target_flags, target).
       The builtin macros (env_digest) don't depend on -D/-U flags, so this hits
       for every TU sharing the same toolchain config. Avoids the ~3ms
       `clang -dM -E` subprocess on every TU.
    2. Full L3 cache: keyed by (toolchain_key, ncc_serial). The NCC post-step
       (apply_class_c_post_step) is cheap string hashing but needs env_digest.
    """
    from fw_context_mcp.store_v2.certify import (
        _certify_parser_identity, libclang_identity, clang_cli_identity,
        apply_class_c_post_step, certified_roots_from_argv,
        resolve_target_triple,
    )
    from fw_context_mcp.store_v2.keys import h

    libclang_id = libclang_identity()
    cli_id = clang_cli_identity()
    std_target_flags = tuple(sorted(
        f for f in sanitized_flags
        if f.startswith(("-std=", "-m", "-target", "--target=", "--sysroot="))
    ))
    try:
        target = resolve_target_triple(expanded)
    except Exception:
        return None

    toolchain_key = (libclang_id, cli_id, std_target_flags, target)

    # Level 1: get env_digest from builtin probe cache (or run probe once)
    with _L3_CACHE_LOCK:
        env_digest = _L3_BUILTIN_CACHE.get(toolchain_key)

    if env_digest is None:
        identity = _certify_parser_identity(expanded, sanitized_flags)
        if identity.failed:
            return None
        env_digest = identity.environment_digest()
        with _L3_CACHE_LOCK:
            _L3_BUILTIN_CACHE[toolchain_key] = env_digest

    # Level 2: full L3 + post-class-C NCC serial (cached per toolchain + ncc)
    full_cache_key = (toolchain_key, ncc_serial)
    with _L3_CACHE_LOCK:
        cached = _L3_FULL_CACHE.get(full_cache_key)
        if cached is not None:
            return cached

    # Compute L3 from cached env_digest (no subprocess needed)
    toolchain_salt = h("fwctx-tc/v1", cli_id)[:32]
    parser_salt = h("fwctx-ps/v1", libclang_id)[:32]
    l3 = h("L3", toolchain_salt, parser_salt, "1", env_digest)
    ncc_post = apply_class_c_post_step(
        ncc_serial, env_digest, certified_roots_from_argv(expanded))

    with _L3_CACHE_LOCK:
        _L3_FULL_CACHE[full_cache_key] = (l3, ncc_post)
    return l3, ncc_post


# ════════════════════════════════════════════════════════════════════════
# HIT verification: re-read closure files and compare content hash
# ════════════════════════════════════════════════════════════════════════

def verify_closure_content(cached_entry: CachedParse) -> bool:
    """Re-verify the closure content (c4) of a cached entry.

    Called on a bucket_key HIT. Re-reads each file in the cached entry's
    closure_file_list and hashes it. If all match the cached
    closure_content_hash, the closure is unchanged → safe to reuse.

    Returns True if closure content is unchanged (safe HIT).
    Returns False if any file changed or is unreadable (must re-parse).
    """
    hasher = hashlib.sha256()
    for fpath in cached_entry.closure_file_list:
        try:
            hasher.update(Path(fpath).read_bytes())
            hasher.update(b"\x1e")
        except OSError:
            # A closure file became unreadable → content changed → MISS
            return False
    current_hash = hasher.hexdigest()
    return current_hash == cached_entry.closure_content_hash


# ════════════════════════════════════════════════════════════════════════
# Post-parse: extract closure info for cache storage
# ════════════════════════════════════════════════════════════════════════

def extract_closure_from_parsed(parsed) -> tuple[tuple[str, ...], str]:
    """Extract the closure file list + content hash from a parsed TU.

    Uses `parsed.tu.get_includes()` (the same source as `build_certificate`'s
    `_closure_walk`). Returns (file_list, content_hash) for cache storage.

    This is the c4 leg, computed post-parse for storage alongside the cached
    `parsed`. Future lookups re-verify by re-reading these exact files.
    """
    tu = getattr(parsed, "tu", None)
    if tu is None:
        return (), ""
    try:
        includes = tu.get_includes()
    except Exception:
        return (), ""

    file_list: list[str] = []
    hasher = hashlib.sha256()
    seen: set[str] = set()
    for inc in includes:
        # libclang FileInclusion uses .include for the File object
        inc_file = getattr(inc, "include", None)
        if inc_file is None:
            continue
        # File object: try .name first, fall back to str()
        fpath = getattr(inc_file, "name", None) or str(inc_file)
        if not fpath or fpath in seen:
            continue
        seen.add(fpath)
        file_list.append(fpath)
        try:
            hasher.update(Path(fpath).read_bytes())
            hasher.update(b"\x1e")
        except OSError:
            hasher.update(b"<unreadable>\x1e")
    return tuple(file_list), hasher.hexdigest()


def compute_full_kb_c_root(
    unit,
    parsed,
    project_root: Path,
    *,
    nonce: str,
    context_id: str = "",
    config_hash: str = "",
    build_dir_patterns: list[str] | None = None,
) -> str:
    """Compute the full KB-C TUSemanticKey root via the production cert path.

    Used post-parse to store the full key alongside the cached `parsed` for
    debugging/observability. The reuse decision uses the bucket key +
    closure re-verification, NOT this full key.
    """
    from fw_context_mcp.store_v2.certify import build_certificate
    cert = build_certificate(
        unit, parsed, project_root,
        nonce=nonce, context_id=context_id, config_hash=config_hash,
        build_dir_patterns=build_dir_patterns,
    )
    if cert.key_result is not None and cert.key_result.is_reusable:
        return cert.key_result.root
    return ""  # poisoned or missing


# ════════════════════════════════════════════════════════════════════════
# Disabled-flag check (for rollback / negative control)
# ════════════════════════════════════════════════════════════════════════

def m9_reuse_enabled() -> bool:
    """Whether M9 cross-build frontend reuse is enabled.

    Default OFF (status quo). Enabled via `FW_CONTEXT_M9_REUSE=1` env var.
    When disabled, the indexer behaves exactly as before (full parse for
    every TU). This is the rollback path per PRODUCTION_INTEGRATION_RULES.md.
    """
    env = os.environ.get("FW_CONTEXT_M9_REUSE")
    if env is not None:
        return env == "1"
    return False
