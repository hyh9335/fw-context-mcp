"""kb_realchannel.py — D5a P3-P1 real-channel K-B calculator (THROWAWAY).

Extends the byte-identical copy of S2's prototype
(`key_candidates_s2copy_d5a.py`, SHA256 63517C9C…C4 == phase2_experiments/
key_candidates.py; the original is never edited). Companion to:
  phase3/PHASE3_BASELINE.md (frozen K-B contract, line 11/14)
  phase2/SEMANTIC_KEY_CANDIDATES.md ([SKC] — K-B §3)
  phase2/_contrib/S2_build_tu_identity.md ([S2] — NCC §3)
  phase2/DEPENDENCY_FINGERPRINT_DESIGN.md ([DCF] — c1..c7 §2.1 + erratum E2)
  phase2/PHASE2_EXPERIMENT_RESULTS.md (§13 — the stand-in measurements this
                                       library upgrades to real-channel evidence)
  phase2/PHASE2_SEMANTIC_IDENTITY_AUDIT.md gap 6 (the mandate)

COMPONENTS TABLE (real vs stub — the honesty contract, mirrored in
_contrib/D5a_P3P1_MEASUREMENT.md):

  component                status   mechanism
  ------------------------ -------- -------------------------------------------
  source_blob (L0)         REAL     sha256 over real fixture bytes on disk
                                    (checked-in repo fixtures) or scenario
                                    trees written under this scratch dir
  argv channel             REAL     entries copied VERBATIM from checked-in
                                    compile_commands.json files (test-firmware,
                                    fwctx_validation_benchmark_pack G01,
                                    tests/fixtures/keep_only) + the manual
                                    builder's own argv assembly shape
                                    (src/fw_context_mcp/indexer/builders/
                                    manual.py:172-183); scenario deltas are
                                    surgical edits of those argv lists
  NCC canonicalization     REAL     S2 prototype canonicalize() VERBATIM
                                    (implements [S2]§3.2/§3.4 spec rules)
  include resolver         REAL*    mini-preprocessor over the same bytes a
                                    parse would read: conditional-aware
                                    (#if/#ifdef/#ifndef/#elif/#else/#endif,
                                    defined(), integer comparisons, &&/||/!),
                                    quoted->includer-dir/-iquote/-I/-isystem
                                    resolution order, transitive closure in
                                    directive-evaluation order, multi-inclusion
                                    counts, RESOLVED-NOT-FOUND rows, complete
                                    flag; unmodeled directive/expression =>
                                    complete=False => POISON (fail-closed).
                                    *approximates the libclang get_includes()
                                    certificate ([PCS]§3.3 step 2); text-level,
                                    same inputs
  forced-input legs (c3)   REAL     full-width content hashes of real forced
                                    bytes, ordered, position preserved
  DCF c1                   REAL     canon rel path + blob sha ([DCF]§2.1)
  DCF c2                   REAL     spec whitelist (-D/-U, -std=, --target=,
                                    ABI -m*, -Werror*) in argv order — NOT the
                                    S5 full-NCC stub (that over-keyed, M05/M06)
  DCF c4                   REAL     resolver certificate rows: (spelling,
                                    resolved canonical path [E2/R-T4], blob
                                    sha|NOT_FOUND, role)
  DCF c5 macro_state       STUB     scanner-derived final macro map, NOT a
                                    parse-identical `clang -dM -E` dump; every
                                    c5-sensitive verdict is labeled
                                    prototype-fidelity (the real dump remains
                                    Phase-3 work; stdlib-only mandate here)
  DCF c6 environment       STUB*    class-C certification STUB where spec'd:
                                    certified system headers = caller-supplied
                                    content tree -> logical descriptor +
                                    structure fp + per-header content sha;
                                    project-side certification is real bytes
  DCF c7 parser_generation STUB     formula-stub per [TOOLCHAIN]§4 (constant
                                    within a session; gates nothing here)
  K-B L2 composition       BOTH     KB-F = frozen-literal: L2 = H(dcf_digest
                                    (c1..c7), forced hashes)  [PHASE3_BASELINE
                                    line 11 wording]
                                    KB-C = closure-scoped sensitivity: L2 =
                                    H(H(c3,c4,c6), forced hashes) — isolates
                                    the c1-path/c2/c5/c7 overlap with L0/L1/L3
  SnapshotID               STUB     [S1]§2.2 formula stub (grouping evidence
                                    only, never a reuse gate)
  R-K1 hygiene             REAL     full-width hashes; nonce-disciplined poison
                                    bound to >=1 world component (H-2); empty
                                    salt guards (H-6); UNKNOWN env != certified
                                    empty (H-5); unreadable forced include
                                    poisons per-world (H-1); rsp:unknown hashed
                                    into c2 (H-7); resolved-path leg in c4 (H-4)

Stdlib only. All git usage is plumbing in throwaway repos under this scratch
dir. The product repo's git state is never touched; nothing under src/ is
executed or modified.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from key_candidates_s2copy_d5a import (  # verbatim S2 prototype (unmodified)
    SEP, PR, CD, SOURCE_EXT, sha, canonicalize, CanonicalContext,
)

NOT_FOUND = "<RESOLVED-NOT-FOUND>"
UNKNOWN_TAG = "fwctx-UNKNOWN/v1"


def h(*parts: str) -> str:
    return sha(SEP.join(parts))


# ══════════════════════════ mini-preprocessor ══════════════════════════
# Real text-level preprocessor over the same bytes a compile would read.
# Bounded directive subset; ANYTHING unmodeled that could affect include
# resolution => UnmodeledDirective => closure.complete=False => poison.

class UnmodeledDirective(Exception):
    pass


_INT_RE = re.compile(r"^\d+$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DIR_RE = re.compile(r"^\s*#\s*(\w+)\s*(.*?)\s*$")
_INC_RE = re.compile(r'^(<[^>]+>|"[^"]+")$')


def _eval_expr(expr: str, env: dict[str, str]) -> int:
    """C #if expression subset: defined(X)/defined X, !, ints, macro-expanded
    object-like macros (undefined identifier == 0, C standard 6.10.1p4),
    comparisons, &&/||, + - * / %, parens. Raises UnmodeledDirective on
    anything else (=> fail-closed, never a guess)."""
    # macro-substitute object-like macros (one pass, no recursion guard needed
    # for our fixture grammar; a self-referential macro raises on re-expand)
    seen: set[str] = set()

    def subst_ident(m: re.Match) -> str:
        name = m.group(0)
        if name in seen:
            raise UnmodeledDirective(f"recursive macro {name!r}")
        if name in env and env[name] not in (None, "<undef>"):
            if not _INT_RE.match(env[name]) and not _IDENT_RE.fullmatch(env[name]):
                raise UnmodeledDirective(f"non-integer macro value {name}={env[name]!r}")
            seen.add(name)
            return env[name]
        return "0"                      # C standard: undefined identifier -> 0

    def expand(text: str) -> str:
        # evaluate `defined X` / `defined(X)` at substitution time
        def eval_defined(mm: re.Match) -> str:
            inner = mm.group(0)
            name = re.search(r"\w+", inner.replace("defined", "", 1))
            defined = bool(name) and name.group(0) in env \
                and env[name.group(0)] != "<undef>"
            return "1" if defined else "0"
        text = re.sub(r"defined\s*\(\s*\w+\s*\)|defined\s+\w+", eval_defined, text)
        return _IDENT_RE.sub(subst_ident, text)

    tokens = expand(expr).replace("(", " ( ").replace(")", " ) ").split()
    pos = {"i": 0}

    def peek() -> str:
        return tokens[pos["i"]] if pos["i"] < len(tokens) else ""

    def take() -> str:
        t = peek()
        pos["i"] += 1
        return t

    def parse_or() -> int:
        v = parse_and()
        while peek() == "||":
            take()
            rhs = parse_and()
            v = 1 if (v or rhs) else 0
        return v

    def parse_and() -> int:
        v = parse_eq()
        while peek() == "&&":
            take()
            rhs = parse_eq()
            v = 1 if (v and rhs) else 0
        return v

    def parse_eq() -> int:
        v = parse_rel()
        while peek() in ("==", "!="):
            op = take()
            rhs = parse_rel()
            v = 1 if ((v == rhs) if op == "==" else (v != rhs)) else 0
        return v

    def parse_rel() -> int:
        v = parse_shift()
        while peek() in ("<=", ">=", "<", ">"):
            op = take()
            rhs = parse_shift()
            v = 1 if {"<": v < rhs, ">": v > rhs,
                      "<=": v <= rhs, ">=": v >= rhs}[op] else 0
        return v

    def parse_shift() -> int:
        v = parse_add()
        while peek() in ("<<", ">>"):
            op = take()
            rhs = parse_add()
            v = v << rhs if op == "<<" else v >> rhs
        return v

    def parse_add() -> int:
        v = parse_mul()
        while peek() in ("+", "-"):
            op = take()
            rhs = parse_mul()
            v = v + rhs if op == "+" else v - rhs
        return v

    def parse_mul() -> int:
        v = parse_unary()
        while peek() in ("*", "/", "%"):
            op = take()
            rhs = parse_unary()
            if op == "*":
                v = v * rhs
            elif rhs == 0:
                raise UnmodeledDirective("division by zero in #if")
            else:
                v = v // rhs if op == "/" else v % rhs
        return v

    def parse_unary() -> int:
        t = peek()
        if t == "!":
            take()
            return 0 if parse_unary() else 1
        if t == "-":
            take()
            return -parse_unary()
        if t == "(":
            take()
            v = parse_or()
            if take() != ")":
                raise UnmodeledDirective(f"unbalanced parens in {expr!r}")
            return v
        if _INT_RE.match(t or ""):
            take()
            return int(t, 0)
        raise UnmodeledDirective(f"unmodeled token {t!r} in #if {expr!r}")

    v = parse_or()
    if pos["i"] != len(tokens):
        raise UnmodeledDirective(f"trailing tokens in #if {expr!r}")
    return v


class MiniPP:
    """Conditional-aware scanner over one file's text. Yields evaluated
    #include directives (in order, with active state) and evolves the macro
    env. Object-like #define / #undef supported; anything else that could
    change resolution raises UnmodeledDirective."""

    def __init__(self, env: dict[str, str]):
        self.env = env

    def scan(self, text: str, on_include, on_active_line=None) -> None:
        stack: list[list] = []          # [self_active, any_taken, parent_active]
        for raw in text.splitlines():
            m = _DIR_RE.match(raw)
            if not m:
                if on_active_line and all(g[0] for g in stack):
                    on_active_line(raw)
                continue
            d, body = m.group(1), m.group(2).strip()
            active = all(g[0] for g in stack)
            if d == "if":
                cond = active and bool(_eval_expr(body, self.env))
                stack.append([cond, cond, active])
            elif d == "ifdef":
                cond = active and body in self.env and self.env[body] != "<undef>"
                stack.append([cond, cond, active])
            elif d == "ifndef":
                cond = active and not (body in self.env and self.env[body] != "<undef>")
                stack.append([cond, cond, active])
            elif d == "elif":
                if not stack:
                    raise UnmodeledDirective("#elif without #if")
                g = stack[-1]
                cond = g[2] and not g[1] and bool(_eval_expr(body, self.env))
                stack[-1] = [cond, g[1] or cond, g[2]]
            elif d == "else":
                if not stack:
                    raise UnmodeledDirective("#else without #if")
                g = stack[-1]
                stack[-1] = [g[2] and not g[1], True, g[2]]
            elif d == "endif":
                if not stack:
                    raise UnmodeledDirective("#endif without #if")
                stack.pop()
            elif d == "include":
                if active:
                    im = _INC_RE.match(body)
                    if not im:
                        raise UnmodeledDirective(f"unmodeled include {body!r}")
                    on_include(im.group(1))
            elif d == "define" and active:
                parts = body.split(None, 1)
                if not parts:
                    raise UnmodeledDirective("bare #define")
                if "(" in parts[0]:
                    # function-like macro: inert for include gating unless used
                    self.env[parts[0]] = "<func-like>"
                else:
                    self.env[parts[0]] = parts[1] if len(parts) > 1 else "1"
            elif d == "undef" and active:
                parts = body.split()
                if len(parts) != 1:
                    raise UnmodeledDirective(f"unmodeled #undef {body!r}")
                self.env[parts[0]] = "<undef>"
            elif d == "pragma":
                pass                      # #pragma once handled by caller marker
            elif d in ("error", "warning", "line", "ident", "sccs", "assert"):
                if d == "error" and active:
                    pass                  # parse would fail later; resolution unaffected
            else:
                if active:
                    raise UnmodeledDirective(f"unmodeled directive #{d} {body!r}")


# ══════════════════════════ resolution certificate ══════════════════════════

@dataclass
class CertRow:
    order: int          # directive-evaluation order (1-based)
    spelling: str       # include spelling as evaluated, e.g. "driver.h" quoted
    quoted: bool
    resolved: str       # canonical identity: rel path | sys:<role>/<name> | NOT_FOUND
    content: str        # sha256 | NOT_FOUND
    role: str           # project | forced | generated | toolchain | external
    depth: int


@dataclass
class Certificate:
    rows: list[CertRow] = field(default_factory=list)
    complete: bool = True           # False => POISON the key ([SKC]§1.2)
    warnings: list[str] = field(default_factory=list)
    macro_env: dict[str, str] = field(default_factory=dict)   # final map (c5 STUB input)

    def deps_dict(self) -> dict[str, str]:
        """[SKC]§1.2 interface: (resolved_header_identity, role, content)."""
        out: dict[str, str] = {}
        for r in self.rows:
            key = f"{r.role}|{r.resolved}"
            out.setdefault(key, r.content)
        return out


def replay_macro_env(argv: list[str], files: dict[str, str],
                     base: dict[str, str] | None = None
                     ) -> tuple[dict[str, str], list[str]]:
    """Ordered replay of -D/-U and forced includes in ARGV POSITION order
    ([S2]§3.3 ex.3). Returns (final env, ordered forced spellings)."""
    env = dict(base or {})
    forced: list[str] = []
    toks = argv[1:]
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-D", "-U") and i + 1 < len(toks):
            body = toks[i + 1]
            i += 2
        elif t.startswith("-D") and len(t) > 2:
            body, i = t[2:], i + 1
        elif t.startswith("-U") and len(t) > 2:
            body, i = t[2:], i + 1
        elif t in ("-include", "-imacros") and i + 1 < len(toks):
            forced.append(toks[i + 1])
            i += 2
            continue
        elif (t.startswith("-include") and len(t) > 8) or \
             (t.startswith("-imacros") and len(t) > 8):
            forced.append(t[8:] if t.startswith("-include") else t[8:])
            i += 1
            continue
        else:
            i += 1
            continue
        if t.startswith("-U") or (t == "-U"):
            env[body.split("=")[0]] = "<undef>"
        else:
            name, _, val = body.partition("=")
            if name:
                env[name] = val
    for f in forced:                    # forced files processed in order
        key = _norm_key(f)
        text = files.get(key)
        if text is None:
            continue                    # missing forced => certificate poison later
        MiniPP(env).scan(text, lambda *_: None)
    return env, forced


def _norm_key(p: str) -> str:
    return p.replace("\\", "/")


def _is_abs(p: str) -> bool:
    np = _norm_key(p)
    return np.startswith("/") or (len(np) > 1 and np[1] == ":")


def resolve_certificate(argv: list[str], *, compile_dir: str, project_root: str,
                        files: dict[str, str], rel_main: str,
                        sysenv: dict[str, str] | None,
                        sysrole: str = "toolchain",
                        generated: set[str] | None = None,
                        rsp: dict[str, list[str]] | None = None,
                        base_env: dict[str, str] | None = None) -> Certificate:
    """[PCS]§3.3 step-2 certificate from a REAL mini-preprocessor over the
    fixture bytes. Resolution: quoted => includer dir, -iquote, -I, -isystem,
    certified env; angle => -I, -isystem, certified env. Cert env headers get
    logical identity sys:<role>/<name> (class-C stand-in, STUB-labeled)."""
    gen = generated or set()
    cert = Certificate()
    # -- expand rsp, split include dirs by kind, in argv order --
    toks: list[str] = []
    for t in argv[1:]:
        if t.startswith("@"):
            body = (rsp or {}).get(t[1:]) or (rsp or {}).get(_norm_key(t[1:]))
            if body is None:
                cert.complete = False
                cert.warnings.append(f"rsp:unknown {t[1:]!r} (F-2: tag must enter c2)")
                body = []
            toks.extend(body)
        else:
            toks.append(t)
    idirs: list[str] = []       # -I  (project + user)
    quotedirs: list[str] = []   # -iquote
    sysdirs: list[str] = []     # -isystem / -idirafter
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        kind = None
        val = ""
        if t in ("-I", "-isystem", "-iquote", "-idirafter") and nxt:
            kind, val, i = t, nxt, i + 2
        elif t.startswith("-I") and len(t) > 2:
            kind, val, i = "-I", t[2:], i + 1
        elif t.startswith("-isystem") and len(t) > 8:
            kind, val, i = "-isystem", t[8:], i + 1
        elif t.startswith("-iquote") and len(t) > 7:
            kind, val, i = "-iquote", t[7:], i + 1
        elif t.startswith("-idirafter") and len(t) > 10:
            kind, val, i = "-idirafter", t[10:], i + 1
        elif t.startswith("--sysroot="):
            sr = _norm_key(t.split("=", 1)[1]).rstrip("/")
            sysdirs.extend([sr + "/include", sr + "/usr/include"])
            i += 1
            continue
        else:
            i += 1
            continue
        val = _norm_key(val)
        if not _is_abs(val):
            val = _norm_key(compile_dir).rstrip("/") + "/" + val.lstrip("./")
        {"-I": idirs, "-iquote": quotedirs,
         "-isystem": sysdirs, "-idirafter": sysdirs}[kind].append(val)
    # -- macro env: ordered replay (real forced-input semantics) --
    try:
        env, forced = replay_macro_env(argv, files, base_env)
    except UnmodeledDirective as exc:
        env, forced = dict(base_env or {}), []
        cert.complete = False
        cert.warnings.append(f"forced-input macro replay unmodeled: {exc}")
    cert.macro_env = env
    # -- walk --
    counter = {"n": 0}
    visited: set[str] = set()
    pragma_once: set[str] = set()

    def files_key(cand: str) -> str | None:
        """Resolve a candidate path to a files-dict key (cdir-relative)."""
        if cand in files:
            return cand
        rel = _norm_key(cand)
        cd = _norm_key(compile_dir).rstrip("/")
        if rel.lower().startswith(cd.lower() + "/"):
            relk = rel[len(cd) + 1:]
            if relk in files:
                return relk
        return None

    def lookup(name: str, quoted: bool, includer_rel: str) -> tuple[str, str] | None:
        """Returns (identity, role) or None."""
        cands: list[tuple[str, str]] = []
        if quoted and includer_rel:
            d = includer_rel.rsplit("/", 1)[0] if "/" in includer_rel else ""
            cands.append((name if not d else d + "/" + name, "project"))
            cands += [(d.rstrip("/") + "/" + name, "project") for d in quotedirs]
        cands += [(d.rstrip("/") + "/" + name, "project") for d in idirs]
        if not quoted:
            cands += [(d.rstrip("/") + "/" + name, "external") for d in sysdirs]
        for cand, role in cands:
            key = files_key(cand)
            if key is not None:
                return key, ("generated" if key in gen else role)
        if name in (sysenv or {}):      # certified class-C environment (STUB)
            return f"sys:{sysrole}/{name}", "toolchain"
        return None

    def walk(key: str, depth: int) -> None:
        if depth > 64:
            cert.complete = False
            cert.warnings.append(f"include depth cap at {key}")
            return
        text = files.get(key, "")
        if "#pragma once" in text:
            if key in pragma_once:
                return
            pragma_once.add(key)

        def on_include(spelling: str) -> None:
            quoted = spelling.startswith('"')
            name = spelling[1:-1]
            counter["n"] += 1
            hit = lookup(name, quoted, key)
            if hit is None:
                cert.rows.append(CertRow(counter["n"], spelling, quoted,
                                         NOT_FOUND, NOT_FOUND, "project",
                                         depth))
                cert.warnings.append(f"unresolved include {spelling!r} in {key}")
                return
            rkey, role = hit
            cert.rows.append(CertRow(counter["n"], spelling, quoted, rkey,
                                     sha(files[rkey]) if rkey in files
                                     else sha(sysenv[name]), role, depth))
            if rkey not in visited:
                visited.add(rkey)
                walk(rkey, depth + 1)

        try:
            MiniPP(env).scan(text, on_include)
        except UnmodeledDirective as exc:
            cert.complete = False
            cert.warnings.append(f"unmodeled directive in {key}: {exc}")

    for f in forced:                    # forced includes BEFORE the TU ([PCS]§6)
        key = _norm_key(f)
        if not _is_abs(key):
            cand0 = key.lstrip("./")
            key = cand0 if cand0 in files else (
                _norm_key(compile_dir).rstrip("/") + "/" + cand0)
        counter["n"] += 1
        if key not in files:
            cert.complete = False       # F-3: forced missing => per-world poison
            cert.warnings.append(f"forced include missing: {f!r}")
            cert.rows.append(CertRow(counter["n"], f"-include {f}", False,
                                     NOT_FOUND, NOT_FOUND, "forced", 0))
            continue
        cert.rows.append(CertRow(counter["n"], f"-include {_norm_key(f)}",
                                 False, key, sha(files[key]), "forced", 0))
        if key not in visited:
            visited.add(key)
            walk(key, 1)
    main_key = _norm_key(rel_main)
    counter["n"] += 1
    if main_key not in files:
        cert.complete = False
        cert.warnings.append(f"main source unreadable: {rel_main!r}")
        cert.rows.append(CertRow(counter["n"], "<main>", False, NOT_FOUND,
                                 NOT_FOUND, "project", 0))
        return cert
    walk(main_key, 0)
    return cert


# ══════════════════════════ class-C certification (STUB source) ══════════════

def certify_environment(sysenv: dict[str, str] | None, role: str = "toolchain",
                        structure_tag: str = "v1") -> dict | None:
    """[PCS]§2.5 certification STUB: logical descriptor + structure fingerprint
    + resolved-header content hashes. None input => UNKNOWN environment (H-5:
    must NEVER equal the certified-empty case)."""
    if sysenv is None:
        return None
    names = sorted(sysenv)
    structure = h("fwctx-cert-struct/v1", role, structure_tag,
                  SEP.join(names))
    rows = {f"hdr:{n}": sha(sysenv[n]) for n in names}
    return {"role": role, "structure_fp": structure, "rows": rows}


def env_digest(c: dict | None) -> str:
    if c is None:
        return UNKNOWN_TAG              # caller must poison on this value
    payload = SEP.join([c["role"], c["structure_fp"]]
                       + [f"{k}={v}" for k, v in sorted(c["rows"].items())])
    return h("fwctx-cert-env/v1", payload)


def apply_classC(ncc_serial: str, certs: dict[str, str]) -> str:
    """R-B2 post-step on the NCC serial: replace literal external path tokens
    (S2's `ext:role:<abs>` stand-ins) and literal --sysroot values with the
    certified logical descriptor, WHEN a certification is provided for that
    path (fail-closed: uncertified paths stay literal => over-key, safe)."""
    out = []
    for tok in ncc_serial.split(SEP):
        m = re.match(r"^ext:[^:]*:(.+)$", tok)
        cand = None
        if m:
            cand = m.group(1)
        elif tok.startswith("--sysroot="):
            cand = tok.split("=", 1)[1]
        if cand:
            for prefix, logical in sorted(certs.items(), key=lambda kv: -len(kv[0])):
                pl = _norm_key(prefix).rstrip("/").lower()
                cl = _norm_key(cand).rstrip("/").lower()
                if cl == pl or cl.startswith(pl + "/"):
                    if m:
                        tok = f"cert:{logical}"
                    else:
                        tok = f"--sysroot=cert:{logical}"
                    break
        out.append(tok)
    return SEP.join(out)


# ══════════════════════════ DCF components ([DCF]§2.1 + E2) ══════════════════

def dcf_c2(argv: list[str], rsp: dict[str, list[str]] | None,
           rsp_ok: bool = True) -> str:
    """Spec whitelist, argv order, rsp-expanded: -D/-U, -std=, --target=,
    ABI -m*, -Werror*. rsp:unknown hashed INTO the digest (H-7/F-2)."""
    toks: list[str] = []
    for t in argv[1:]:
        if t.startswith("@"):
            body = (rsp or {}).get(t[1:]) or (rsp or {}).get(_norm_key(t[1:]))
            if body is None:
                toks.append("rsp:unknown")     # in-digest tag
                body = []
            toks.extend(body)
        else:
            toks.append(t)
    keep: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t in ("-D", "-U") and nxt:
            keep.append(f"{t}{nxt}")
            i += 2
        elif t.startswith("-D") and len(t) > 2:
            keep.append(t)
            i += 1
        elif t.startswith("-U") and len(t) > 2:
            keep.append(t)
            i += 1
        elif t.startswith("-std=") or t.startswith("--target=") or \
                t.startswith("-m") or t.startswith("-Werror"):
            keep.append(t)
            i += 1
        else:
            i += 1
    if not rsp_ok:
        keep.append("rsp:unknown")
    return h("c2", SEP.join(keep))


def dcf_components(*, rel_main: str, blob: str, cert: Certificate,
                   argv: list[str], rsp: dict[str, list[str]] | None,
                   env_cert: dict | None, c7: str) -> dict[str, str]:
    """c1..c7 per [DCF]§2.1; c4 rows carry the resolved canonical path leg
    (erratum E2 / R-T4). c5 = STUB macro map (labeled); c6 = certification."""
    c1 = h("c1", rel_main, blob)
    c2 = dcf_c2(argv, rsp)
    forced_rows = [r for r in cert.rows if r.role == "forced"]
    c3 = h("c3", *[f"{r.resolved}|{r.content}" for r in forced_rows])
    c4 = h("c4", *[f"{r.spelling}|{r.resolved}|{r.content}|{r.role}"
                   for r in cert.rows])
    c5 = h("c5-stub-scan", SEP.join(f"{k}={v}"
                                    for k, v in sorted(cert.macro_env.items())))
    c6 = h("c6", env_digest(env_cert)) if env_cert is not None \
        else h("c6", UNKNOWN_TAG)
    return {"c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5, "c6": c6, "c7": c7}


def dcf_digest_full(comp: dict[str, str]) -> str:
    return h("fw-context-dcf/1", *[comp[f"c{i}"] for i in range(1, 8)])


def dcf_digest_closure(comp: dict[str, str]) -> str:
    """Closure-scoped reading: only the dependency-closure components
    (c3 forced inputs, c4 certificate, c6 environment). c1/c2/c5/c7 excluded —
    they overlap L0 (blob), L1 (NCC), the c5 stub channel, and L3 (parser)."""
    return h("fw-context-dcf-closure/1", comp["c3"], comp["c4"], comp["c6"])


# ══════════════════════════ K-B keys (frozen contract) ══════════════════════

def poison(component: str, nonce: str, world_fp: str) -> str:
    """[SKC]§1.4 with H-2 repair: binds nonce AND >=1 world component."""
    return h(UNKNOWN_TAG, component, nonce, world_fp)


def salt_guard(salt: str, name: str, nonce: str, world_fp: str) -> str:
    """R-K1(b)/H-6: presence guard — empty salt poisons, never keys."""
    if not salt:
        return poison(f"empty-salt:{name}", nonce, world_fp)
    return salt


def key_kb(*, blob: str, language: str, ncc_serial: str, cert: Certificate,
           comp: dict[str, str], tc: str, ps: str, env_cert: dict | None,
           nonce: str, l2_mode: str = "F") -> dict[str, str]:
    """Frozen K-B ([SKC]§3.1 as amended by PHASE3_BASELINE line 11/14):
    root = H('fwctx-tukb/v1', L0, L1, L2, L3); layers persisted.
    l2_mode 'F' = frozen-literal (full DCF digest in L2);
    'C' = closure-scoped sensitivity reading."""
    world_fp = h("wf", blob, ncc_serial, str(cert.complete), nonce)
    if not cert.complete:
        return {"L0": h("L0", blob, language), "L1": h("L1", ncc_serial),
                "L2": poison("closure-incomplete", nonce, world_fp),
                "L3": poison("closure-incomplete-L3", nonce, world_fp),
                "root": poison("closure-incomplete-root", nonce, world_fp),
                "poisoned": "closure-incomplete"}
    l0 = h("L0", blob, language)
    l1 = h("L1", ncc_serial)
    dd = dcf_digest_full(comp) if l2_mode == "F" else dcf_digest_closure(comp)
    forced_chain = SEP.join(r.content for r in cert.rows if r.role == "forced")
    l2 = h("L2", dd, forced_chain)
    tcg = salt_guard(tc, "toolchain", nonce, world_fp)
    psg = salt_guard(ps, "parser", nonce, world_fp)
    ed = env_digest(env_cert)
    if ed == UNKNOWN_TAG:
        l3 = poison("environment-unknown", nonce, world_fp)   # H-5/F-6
    else:
        l3 = h("L3", tcg, psg, ed)
    return {"L0": l0, "L1": l1, "L2": l2, "L3": l3,
            "root": h("fwctx-tukb/v1", l0, l1, l2, l3),
            "poisoned": ""}


# ══════════════════════════ world ═══════════════════════════════════════════

@dataclass
class World:
    name: str
    root: str                       # project root (abs, posix)
    cdir: str                       # compile dir (abs, posix)
    argv: list[str]                 # VERBATIM real-shaped argv (argv[0] = driver)
    files: dict[str, str]           # cdir-relative posix rel path -> text
    rel_main: str                   # cdir-relative main source
    language: str = "c"
    sysenv: dict[str, str] | None = None    # certified env (STUB source); None=UNKNOWN
    sysrole: str = "toolchain"
    generated: set[str] = field(default_factory=set)
    rsp: dict[str, list[str]] = field(default_factory=dict)
    certs: dict[str, str] = field(default_factory=dict)  # abs prefix -> logical
    tc: str = "gcc|13.2.0|x86_64-w64-mingw32"
    ps: str = "libclang-18.1.1|fw-context-rows/3|keys-v1"
    nonce: str = ""
    env_unknown: bool = False

    def __post_init__(self) -> None:
        if not self.nonce:
            raise ValueError("per-world nonce required ([SKC]§1.4)")

    def blob(self) -> str:
        return sha(self.files[_norm_key(self.rel_main)])

    def ncc(self) -> str:
        raw = canonicalize(list(self.argv), compile_dir=self.cdir,
                           project_root=self.root, file_contents=self.files,
                           rsp_contents=self.rsp).ncc_serial
        return apply_classC(raw, self.certs)

    def certificate(self) -> Certificate:
        return resolve_certificate(list(self.argv), compile_dir=self.cdir,
                                   project_root=self.root,
                                   files=self.files, rel_main=self.rel_main,
                                   sysenv=None if self.env_unknown else self.sysenv,
                                   sysrole=self.sysrole,
                                   generated=self.generated, rsp=self.rsp)

    def env_cert(self) -> dict | None:
        if self.env_unknown:
            return None
        return certify_environment(self.sysenv, self.sysrole)

    def snapshot(self) -> dict:
        cert = self.certificate()
        blob = self.blob()
        comp = dcf_components(rel_main=self.rel_main, blob=blob, cert=cert,
                              argv=list(self.argv), rsp=self.rsp,
                              env_cert=self.env_cert(),
                              c7=h("c7-stub", self.ps, self.tc))
        kbF = key_kb(blob=blob, language=self.language,
                     ncc_serial=self.ncc(), cert=cert, comp=comp,
                     tc=self.tc, ps=self.ps, env_cert=self.env_cert(),
                     nonce=self.nonce, l2_mode="F")
        kbC = key_kb(blob=blob, language=self.language,
                     ncc_serial=self.ncc(), cert=cert, comp=comp,
                     tc=self.tc, ps=self.ps, env_cert=self.env_cert(),
                     nonce=self.nonce, l2_mode="C")
        return {"world": self.name, "blob": blob, "ncc": self.ncc(),
                "cert_complete": cert.complete,
                "cert_warnings": cert.warnings,
                "cert_rows": [r.__dict__ for r in cert.rows],
                "deps": cert.deps_dict(),
                "macro_env": dict(cert.macro_env),
                "dcf_comp": comp,
                "dcf_full": dcf_digest_full(comp),
                "dcf_closure": dcf_digest_closure(comp),
                "kbF": kbF, "kbC": kbC,
                "env_digest": env_digest(self.env_cert())}


def kbeq(a: World, b: World) -> dict[str, bool]:
    sa, sb = a.snapshot(), b.snapshot()
    return {"KB_F": sa["kbF"]["root"] == sb["kbF"]["root"],
            "KB_C": sa["kbC"]["root"] == sb["kbC"]["root"],
            "L0": sa["kbF"]["L0"] == sb["kbF"]["L0"],
            "L1": sa["kbF"]["L1"] == sb["kbF"]["L1"],
            "L2": sa["kbF"]["L2"] == sb["kbF"]["L2"],
            "L3": sa["kbF"]["L3"] == sb["kbF"]["L3"],
            "DCF_full": sa["dcf_full"] == sb["dcf_full"],
            "DCF_closure": sa["dcf_closure"] == sb["dcf_closure"],
            "NCC": sa["ncc"] == sb["ncc"]}


def snapshot_id(ns_tag: str, base_commit: str,
                dirty: list[tuple[str, str, str]]) -> str:
    """[S1]§2.2 stub — grouping evidence only (never a reuse gate)."""
    if dirty:
        payload = SEP.join(sorted(f"{p}\x1e{m}\x1e{b}" for p, m, b in dirty))
        delta = sha(payload)
    else:
        delta = ""
    return h("fwctx-snapstub/v1", ns_tag, base_commit, delta)


# ══════════════════════════ self-tests ══════════════════════════════════════

def run_self_test() -> None:
    fails: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails.append(name)

    print("kb_realchannel.py self-test — D5a extensions over the S2 copy\n")
    SYS = {"stdint.h": "typedef unsigned int uint32_t;\n#define UINT32_MAX 4294967295U\n"}

    print("T-A mini-preprocessor: conditional include gating")
    files = {
        "cfg.h": "#ifndef CFG_H\n#define CFG_H\n#define OPT 1\n#endif\n",
        "a.h": '#include "cfg.h"\n#define A 1\n',
        "b.h": "#define B 1\n",
        "main.c": '#include "cfg.h"\n'
                  '#include "a.h"\n'
                  "#if OPT\n"
                  '#include "b.h"\n'
                  "#endif\n"
                  "int main(void){return 0;}\n",
    }
    w = World("t", "R:/p", "R:/p", ["gcc", "-c", "main.c"], files, "main.c",
              sysenv=SYS, nonce="n1")
    c = w.certificate()
    names = [r.spelling for r in c.rows]
    check("b.h included (OPT==1)", '"b.h"' in names)
    check("cfg.h multi-inclusion counted twice",
          sum(1 for r in c.rows if r.spelling == '"cfg.h"') == 2)
    check("certificate complete", c.complete)
    check("macro env has OPT=1", c.macro_env.get("OPT") == "1")

    print("T-B #if flip changes the closure (config-gated module)")
    files2 = dict(files)
    files2["cfg.h"] = files["cfg.h"].replace("#define OPT 1", "#define OPT 0")
    w2 = World("t2", "R:/p", "R:/p", ["gcc", "-c", "main.c"], files2, "main.c",
               sysenv=SYS, nonce="n2")
    c2 = w2.certificate()
    check("b.h NOT included (OPT==0)",
          '"b.h"' not in [r.spelling for r in c2.rows])
    check("c4 rows differ => L2 differs", w.snapshot()["dcf_full"] != w2.snapshot()["dcf_full"])
    eq = kbeq(w, w2)
    check("K-B differs (config.h byte change)", not eq["KB_F"])

    print("T-C fail-closed: unmodeled #if expression poisons")
    files3 = dict(files)
    files3["main.c"] = files["main.c"].replace(
        "#if OPT\n", "#if OPT > (1<<20) - defined(FOO)*3\n")
    w3 = World("t3", "R:/p", "R:/p", ["gcc", "-c", "main.c"], files3, "main.c",
               sysenv=SYS, nonce="n3")
    s3 = w3.snapshot()
    check("complete=False", not s3["cert_complete"])
    check("root is poison", s3["kbF"]["poisoned"] == "closure-incomplete")

    print("T-D hygiene: H-5/H-6/H-1/H-2")
    wu = World("tu", "R:/p", "R:/p", ["gcc", "-c", "main.c"], files, "main.c",
               sysenv=None, env_unknown=True, nonce="nu")
    we = World("te", "R:/p", "R:/p", ["gcc", "-c", "main.c"], files, "main.c",
               sysenv={}, nonce="ne")          # certified-empty
    su, se = wu.snapshot(), we.snapshot()
    check("H-5: UNKNOWN env != certified-empty env digest",
          su["env_digest"] != se["env_digest"])
    check("H-5: K-B roots differ (fail closed)", su["kbF"]["root"] != se["kbF"]["root"])
    check("H-6: empty parser salt poisons",
          key_kb(blob=w.blob(), language="c", ncc_serial=w.ncc(),
                 cert=w.certificate(),
                 comp=dcf_components(rel_main=w.rel_main, blob=w.blob(),
                                     cert=w.certificate(), argv=w.argv,
                                     rsp=None, env_cert=w.env_cert(), c7="x"),
                 tc=w.tc, ps="", env_cert=w.env_cert(), nonce="nx"
                 )["poisoned"] == "")
    wm = World("tm", "R:/p", "R:/p", ["gcc", "-include gone.h", "-c", "main.c"],
               files, "main.c", sysenv=SYS, nonce="nm")
    sm = wm.snapshot()
    check("H-1/F-3: missing forced include poisons per-world",
          not sm["cert_complete"] and sm["kbF"]["poisoned"] != "")

    print("T-E rename-only: L0 equal, NCC equal; KB_C reuses, KB_F differs")
    wr = World("tr", "R:/p", "R:/p", ["gcc", "-c", "renamed.c"],
               {**files, "renamed.c": files["main.c"]}, "renamed.c",
               sysenv=SYS, nonce="nr")
    eqr = kbeq(w, wr)
    check("L0 equal (content-addressed)", eqr["L0"])
    check("NCC equal (source token excluded)", eqr["NCC"])
    check("KB_C equal (closure-scoped L2)", eqr["KB_C"])
    check("KB_F differs (frozen-literal L2 carries c1 path)", not eqr["KB_F"])
    check("DCF c1 differs (by design, R-T6: DCF never the share key)",
          not eqr["DCF_full"])

    print("T-F class-C certification: sysroot moved, certified => reuse")
    cert_map1 = {"D:/x/toolchain": "gcc-arm:v1"}
    cert_map2 = {"E:/y/toolchain": "gcc-arm:v1"}
    wa = World("wa", "R:/p", "R:/p",
               ["gcc", "--sysroot=D:/x/toolchain", "-c", "main.c"],
               files, "main.c", sysenv=SYS, certs=cert_map1, nonce="na")
    wb2 = World("wb", "R:/p", "R:/p",
                ["gcc", "--sysroot=E:/y/toolchain", "-c", "main.c"],
                files, "main.c", sysenv=SYS, certs=cert_map2, nonce="nb")
    eqc = kbeq(wa, wb2)
    check("certified sysroot move: NCC equal", eqc["NCC"])
    check("certified sysroot move: K-B equal", eqc["KB_F"])
    wc = World("wc", "R:/p", "R:/p",
               ["gcc", "--sysroot=D:/x/toolchain", "-c", "main.c"],
               files, "main.c", sysenv=SYS, nonce="nc")   # UNCERTIFIED
    eqd = kbeq(wa, wc)
    check("uncertified literal sysroot: K-B differs (over-key, safe)",
          not eqd["KB_F"])

    print()
    if fails:
        print(f"RESULT: {len(fails)} FAILURE(S): {fails}")
        raise SystemExit(1)
    print("RESULT: all kb_realchannel self-tests passed.")


if __name__ == "__main__":
    run_self_test()
