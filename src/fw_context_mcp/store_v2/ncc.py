"""store_v2.ncc — Normalized Compile Context (NCC) canonicalization, Slice 0.

Verbatim port of the S2-prototype canonicalizer accepted as REAL in the D5a
component table (`phase3_experiments/p3p1/kb_realchannel.py` L30-31:
"NCC canonicalization | REAL | S2 prototype canonicalize() VERBATIM
(implements [S2]§3.2/§3.4 spec rules)"), originating from
`phase2_experiments/key_candidates.py`. Companion specs:

  [S2]  phase2/_contrib/S2_build_tu_identity.md      (NCC §3.2/§3.4)
  [PCS] phase2/PATH_CANONICALIZATION_SPEC.md         (§1/§2.1/§2.4/§2.5/§6)
  [SKC] phase2/SEMANTIC_KEY_CANDIDATES.md            (§3)

Slice-0 scope (PHASE3_IMPLEMENTATION_BOUNDARY.md L9): "NCC canonicalization
module" — a pure deterministic primitive. No I/O, no store access, no flag.

Contract points carried verbatim from the prototype:
  * argv[0] (compiler driver) is stripped — it enters identity via
    ToolchainIdentity (L3), never the NCC.
  * Ordered semantic tokens; NO sorting / dedup of compile semantics
    (duplicate -D and reordered flags are preserved; [S2]§3.3 ex.4).
  * Clone-root de-tainting: project root / compile dir are replaced by the
    <PROJECT_ROOT>/<COMPILE_DIR> placeholders ([PCS]§2.4); external paths keep
    the `ext:role:<path>` stand-in until the class-C certification post-step
    (R-B2) replaces them with the certified logical descriptor.
  * rsp expansion at the `@` position; missing rsp is a deterministic
    deterministic-empty expansion WITH a recorded warning (never silent).
  * `rsp:unknown` uncertainty is hashed INTO the digest that qualifies (H-7).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

SEP = "\x1f"                      # control char, cannot appear in flags/paths ([S2]§3.4)
PR, CD = "<PROJECT_ROOT>", "<COMPILE_DIR>"   # [PCS]§2.4 placeholders
SOURCE_EXT = {"c", "cc", "cpp", "cxx", "c++", "s", "S", "m", "mm", "i", "ii"}

# ── drop tables, mirroring parse-arg reality ([S2]§3.2; cf. compile_commands.py) ──
DROP_WITH_ARG = {"-o", "-MF", "-MT", "-MQ", "-specs"}       # output-only / unsupported
DROP_TOKEN = {"-MD", "-MP", "-MMD", "-MG", "-c", "-g", "-pipe", "-save-temps",
              "-flto", "-fno-common"}                        # output/debug/codegen-only
DROP_GCC_WARN = {"-Wno-stringop-truncation", "-Wstringop-truncation",
                 "-Wstringop-overflow", "-Wno-stringop-overflow", "-Wlogical-op",
                 "-Wno-logical-op", "-Wformat-signedness", "-Wno-format-signedness"}
JOIN = {"-D", "-U", "-include", "-imacros", "-I", "-isystem", "-iquote",
        "-idirafter", "-x", "-MF", "-MT", "-MQ"}             # joined spellings too


def sha(data: str) -> str:
    """Full-width SHA-256 hex digest (R-K1(a): no truncation inside any key)."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _posix(p: str) -> str:
    return p.replace("\\", "/")


def _canon_path(p: str, project_root: str, compile_dir: str,
                path_roles: dict[str, str] | None) -> str:
    """Classify & de-taint one path token ([PCS]§1/§2.4): placeholder or ext:role.
    Relative paths resolve against compile_dir first ([PCS]§2.1)."""
    np = _posix(p)
    if not (np.startswith("/") or (len(np) > 1 and np[1] == ":")):
        np = _posix(compile_dir).rstrip("/") + "/" + np
    pr, cd = _posix(project_root).rstrip("/"), _posix(compile_dir).rstrip("/")
    for root, ph in ((pr, PR), (cd, CD)):
        if np.lower() == root.lower():
            return ph
        if np.lower().startswith(root.lower() + "/"):
            return ph + np[len(root):]          # remainder keeps on-disk case
    role = "external"
    for prefix, r in sorted((path_roles or {}).items(), key=lambda kv: -len(kv[0])):
        if np.lower().startswith(_posix(prefix).rstrip("/").lower() + "/"):
            role = r
            break
    return f"ext:{role}:{np}"                    # class-C logical stand-in ([PCS]§2.5)


@dataclass
class CanonicalContext:
    """The NCC ([S2]§3) plus the state metadata the key calculator consumes."""
    ncc_serial: str = ""
    include_list: list[str] = field(default_factory=list)     # ordered class-E entries
    forced_includes: list[str] = field(default_factory=list)  # ordered content hashes
    defines_final: dict[str, str] = field(default_factory=dict)  # last-wins map (meta)
    macro_env: dict[str, str] = field(default_factory=dict)   # simulated env
    target_std: str = ""                                      # -std/--target
    warnings: list[str] = field(default_factory=list)


def _apply_define(env: dict[str, str], body: str) -> None:
    name, _, val = body.partition("=")
    if name:
        env[name] = val                          # last-wins ([S2]§3.3 ex.4)


def _scan_defines(text: str, env: dict[str, str], warnings: list[str]) -> None:
    """Mini conditional-aware forced-include macro scan (prototype approximation)."""
    stack: list[list] = []                       # [self_active, any_taken, parent_active]

    def cond_of(s: str) -> bool:
        parts = s.split()
        if s.startswith("#ifdef") and len(parts) > 1:
            return parts[1] in env and env[parts[1]] != "<undef>"
        if s.startswith("#ifndef") and len(parts) > 1:
            return not (parts[1] in env and env[parts[1]] != "<undef>")
        body = s[3:].strip()
        if body.startswith("defined"):
            inner = body[len("defined"):].strip().lstrip("(")
            name = inner.split(")")[0].strip()
            return name in env and env[name] != "<undef>"
        if body in ("1", "0"):
            return body == "1"
        warnings.append(f"unmodeled #if expr ({s!r}) → conservative False")
        return False

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("#if") and not s.startswith("#include"):
            parent = all(g[0] for g in stack)
            cond = parent and cond_of(s)
            stack.append([cond, cond, parent])
        elif s.startswith("#elif") and stack:
            g = stack[-1]
            cond = g[2] and not g[1] and cond_of("#if" + s[5:])
            stack[-1] = [cond, g[1] or cond, g[2]]
        elif s.startswith("#else") and stack:
            g = stack[-1]
            stack[-1] = [g[2] and not g[1], True, g[2]]
        elif s.startswith("#endif") and stack:
            stack.pop()
        elif s.startswith("#undef") and (not stack or stack[-1][0]):
            parts = s.split()
            if len(parts) > 1:
                env[parts[1]] = "<undef>"
        elif s.startswith("#define") and (not stack or stack[-1][0]):
            parts = s[len("#define"):].strip().split(None, 1)
            if parts and "(" not in parts[0]:
                env[parts[0]] = parts[1] if len(parts) > 1 else "1"
            elif parts:
                warnings.append(f"function-like macro not modeled: {parts[0]}")


def canonicalize(argv: list[str], *, compile_dir: str, project_root: str,
                 file_contents: dict[str, str] | None = None,
                 rsp_contents: dict[str, list[str]] | None = None,
                 path_roles: dict[str, str] | None = None) -> CanonicalContext:
    """Compile-command argv → NCC ([S2]§3.2 classification table). argv[0] (the
    compiler driver) is stripped; it enters identity via ToolchainIdentity."""
    fc, rc = file_contents or {}, rsp_contents or {}
    cc = CanonicalContext()
    toks: list[str] = []
    for t in argv[1:]:                                   # strip driver ([S2]§3.2)
        if t.startswith("@"):                            # rsp expansion ([PCS]§6)
            body = rc.get(t[1:]) or rc.get(_posix(t[1:]))
            if body is None:
                cc.warnings.append(f"missing rsp {t[1:]!r}: deterministic empty expansion")
                body = []
            toks.extend(body)
        else:
            toks.append(t)
    out: list[str] = []
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < n else ""
        if t in DROP_WITH_ARG:                           # output-only: skip value if any
            i += 2 if nxt and not nxt.startswith("-") else 1
        elif t in DROP_TOKEN or t in DROP_GCC_WARN or t == "-specs":
            i += 1
        elif t.startswith("-Wp,"):
            # C6 (Slice-4B): -Wp,opt passes opt to the preprocessor.
            # Was previously dropped by the -W* handler (N2 collision bug).
            # Split on commas — each piece becomes a separate pp option and
            # is re-injected into the token stream so the existing -D/-U/-I/
            # -isystem/-include/etc. handlers process it naturally.
            sub_opts = t[4:].split(",")
            toks[i:i + 1] = sub_opts
            n = len(toks)
            continue
        elif t.startswith("-W") and not t.startswith("-Werror"):
            i += 1                                       # diagnostics-only; -Werror kept
        elif t in JOIN and nxt and not t.startswith(("-D", "-U")):
            val = nxt
            if t in ("-I", "-isystem", "-iquote", "-idirafter"):
                val = _canon_path(val, project_root, compile_dir, path_roles)
                cc.include_list.append(f"{t}={val}")     # ordered class-E list
                out.append(f"{t}{val}")
            elif t in ("-include", "-imacros"):          # class-B content, order kept
                body = fc.get(_posix(val)) or fc.get(val.split("/")[-1])
                if body is None:
                    cc.warnings.append(f"forced include unreadable: {val!r} → UNKNOWN")
                    out.append(f"{t}=<UNKNOWN>")
                else:
                    h = sha(body)
                    cc.forced_includes.append(h)
                    out.append(f"{t}={h[:16]}")
                    _scan_defines(body, cc.macro_env, cc.warnings)
            elif t == "-x":
                out.append(f"-x{val}")
            i += 2
        elif t.startswith("-I") and len(t) > 2:          # joined spellings
            val = _canon_path(t[2:], project_root, compile_dir, path_roles)
            cc.include_list.append(f"-I={val}")
            out.append(f"-I{val}")
            i += 1
        elif t.startswith("-isystem") and len(t) > 8:
            val = _canon_path(t[8:], project_root, compile_dir, path_roles)
            cc.include_list.append(f"-isystem={val}")
            out.append(f"-isystem{val}")
            i += 1
        elif t.startswith("-iquote") and len(t) > 7:
            # C2 (Slice-4B): joined form -iquote/path was not entering
            # include_list, leaving the A5′ attestation set incomplete.
            val = _canon_path(t[7:], project_root, compile_dir, path_roles)
            cc.include_list.append(f"-iquote={val}")
            out.append(f"-iquote{val}")
            i += 1
        elif t.startswith("-idirafter") and len(t) > 10:
            # C2 (Slice-4B): same coverage hole for joined -idirafter/path.
            val = _canon_path(t[10:], project_root, compile_dir, path_roles)
            cc.include_list.append(f"-idirafter={val}")
            out.append(f"-idirafter{val}")
            i += 1
        elif (t == "-D" and nxt) or (t.startswith("-D") and len(t) > 2):
            body, step = (nxt, 2) if t == "-D" else (t[2:], 1)
            out.append(f"-D{body}")
            _apply_define(cc.defines_final, body)     # last-wins ([S2]§3.3 ex.4)
            _apply_define(cc.macro_env, body)
            i += step
        elif (t == "-U" and nxt and not nxt.startswith("-")) \
                or (t.startswith("-U") and len(t) > 2):
            body, step = (nxt, 2) if t == "-U" else (t[2:], 1)
            out.append(f"-U{body}")
            cc.defines_final[body] = cc.macro_env[body] = "<undef>"
            i += step
        elif "." in t and t.rsplit(".", 1)[-1] in SOURCE_EXT:
            i += 1                                       # source token: membership, not flags
        else:
            out.append(t)                                # semantic tail: -m*/-f*/-std/-O/…
            if t.startswith("-std=") or t.startswith("--target="):
                cc.target_std += t
            if t.startswith("-O"):
                val = t[2:]
                if val not in ("", "0"):                 # -O0/-O alone ⇒ no predefine
                    cc.macro_env["__OPTIMIZE__"] = "1"   # simulated ([S2]§2.5)
                    if val in ("s", "z"):
                        cc.macro_env["__OPTIMIZE_SIZE__"] = "1"
            i += 1
    cc.ncc_serial = SEP.join(out)
    return cc
