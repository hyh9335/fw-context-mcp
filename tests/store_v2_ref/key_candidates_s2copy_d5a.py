"""key_candidates.py — THROWAWAY prototype for Phase-2 semantic-key candidates (S2).

NOT product code. No product imports; stdlib only. Evidence for S5's adversarial
measurements, companion to:
  multi_version_index_review/phase2/SEMANTIC_KEY_CANDIDATES.md        ([SKC])
  multi_version_index_review/phase2/_contrib/S2_build_tu_identity.md  ([S2])
  multi_version_index_review/phase2/PATH_CANONICALIZATION_SPEC.md     ([PCS])

Implements, in miniature and honestly approximated:
  * NormalizedCompileContext (NCC) canonicalization — [S2]§3.2/§3.4: ordered
    semantic flags, rsp expansion, output-only/diagnostic drops, `-Werror`
    exception, placeholder path substitution ([PCS]§2.4), class-C stand-in.
  * K-A monolithic key — [SKC]§2; K-B layered Merkle — [SKC]§3 (L0..L3 persisted);
    K-C preprocessor-state key — [SKC]§4 (approximated, see CAVEATS).

CAVEATS (prototype fidelity, stated per [SKC] honesty rules):
  * "Filesystem" is caller-supplied dicts (file_contents / rsp_contents) — no I/O.
  * Forced-include macro contribution is simulated by a mini conditional-aware
    scanner over the forced file's text (#ifdef/#ifndef/#if defined/#else/#elif/
    #endif/#define/#undef); the real design uses a parse-identical preprocessing
    dump ([SKC]§4.3). Unknown #if expressions evaluate conservatively to False.
  * Optimization predefines are simulated ([S2]§2.5): any -O>0 ⇒ __OPTIMIZE__=1;
    -Os/-Oz additionally __OPTIMIZE_SIZE__=1; -O0/-O alone ⇒ neither.
  * K-C keeps a conservative include-search-list gate (no real resolution
    certificate here; a real one ([PCS]§3.3) could relax it).
  * deps=None means the dependency closure is UNKNOWN → poisoned key ([SKC]§1.4);
    deps={} is a complete, empty closure (a digestable, normal state).
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
    """The NCC ([S2]§3) plus the state metadata the candidates consume."""
    ncc_serial: str = ""
    include_list: list[str] = field(default_factory=list)     # ordered class-E entries
    forced_includes: list[str] = field(default_factory=list)  # ordered content blobs
    defines_final: dict[str, str] = field(default_factory=dict)  # last-wins map (meta)
    macro_env: dict[str, str] = field(default_factory=dict)   # simulated env (K-C)
    target_std: str = ""                                      # -std/--target (K-C)
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

def _digest_map(m: dict[str, str]) -> str:
    return sha(SEP.join(f"{k}={v}" for k, v in sorted(m.items())))

def _deps_digest(deps: dict[str, str] | None, nonce: str) -> str:
    """[S3-interface] digest; deps=None (UNKNOWN) → poison ([SKC]§1.4)."""
    if deps is None:
        return sha(f"fwctx-UNKNOWN/v1{SEP}deps{SEP}{nonce}")
    return _digest_map(deps)

def key_ka(ctx, source_blob, deps, toolchain_salt, parser_salt,
           env: dict[str, str] | None = None, nonce: str = "n") -> str:
    """[SKC]§2 — monolithic: one hash over all normalized semantic inputs."""
    return sha(SEP.join(["fwctx-tuka/v1", source_blob, ctx.ncc_serial,
                         _deps_digest(deps, nonce), _digest_map(env or {}),
                         toolchain_salt, parser_salt]))

def key_kb(ctx, source_blob, deps, toolchain_salt, parser_salt,
           env: dict[str, str] | None = None, nonce: str = "n") -> dict[str, str]:
    """[SKC]§3 — layered Merkle: root + persisted L0..L3."""
    l0 = sha(SEP.join(["L0", source_blob]))
    l1 = sha(SEP.join(["L1", ctx.ncc_serial]))
    l2 = sha(SEP.join(["L2", _deps_digest(deps, nonce), SEP.join(ctx.forced_includes)]))
    l3 = sha(SEP.join(["L3", toolchain_salt, parser_salt, _digest_map(env or {})]))
    return {"L0": l0, "L1": l1, "L2": l2, "L3": l3,
            "root": sha(SEP.join(["fwctx-tukb/v1", l0, l1, l2, l3]))}

def key_kc(ctx, source_blob, deps, toolchain_salt, parser_salt,
           env: dict[str, str] | None = None, nonce: str = "n") -> str:
    """[SKC]§4 — preprocessor-state key (prototype: simulated PSC + conservative
    include-search-list gate; a real resolution certificate could relax the gate)."""
    if "<UNKNOWN>" in ctx.ncc_serial:
        return sha(f"fwctx-UNKNOWN/v1{SEP}psc{SEP}{nonce}")
    seq = _deps_digest(deps, nonce)
    return sha(SEP.join(["fwctx-tukc/v1", source_blob, _digest_map(ctx.macro_env),
                         seq, sha(SEP.join(ctx.include_list)), ctx.target_std,
                         toolchain_salt, parser_salt, _digest_map(env or {})]))

# ─────────────────────────── self-test ([SKC] evidence for S5) ───────────────────────────

FAILURES: list[str] = []

FILES = {
    "util.h": "#define UTIL 1\n",
    "inc/util.h": "#define UTIL 2\n",
    "cfg.h": "#ifdef FEATURE\n#define CFG_F 1\n#else\n#define CFG_F 0\n#endif\n",
}
MAIN = "int main(void){return 0;}\n"
TC, PS = "gcc|13.2.0|arm-none-eabi", "libclang-18.1.1|fw-context-rows/3|keys-v1"
R1, C1 = "D:/userA/repo", "D:/userA/repo/build"          # canonical test roots

def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)

def world(root: str, cdir: str, argv: str, rsp: dict[str, list[str]] | None = None):
    """argv as a space-free string (test convenience); real API takes a list."""
    ctx = canonicalize(argv.split(), compile_dir=cdir, project_root=root,
                       file_contents=FILES, rsp_contents=rsp or {},
                       path_roles={"/opt/toolchain": "toolchain"})
    return ctx, sha(MAIN)

def ka(ctx, deps: dict[str, str] | None = None, tc: str = TC, ps: str = PS,
       nonce: str = "n", bl: str | None = None) -> str:
    return key_ka(ctx, bl or sha(MAIN), deps, tc, ps, nonce=nonce)

def kb(ctx, deps: dict[str, str] | None = None, tc: str = TC, ps: str = PS) -> dict:
    return key_kb(ctx, sha(MAIN), deps, tc, ps)

def kc(ctx, deps: dict[str, str] | None = None, tc: str = TC, ps: str = PS) -> str:
    return key_kc(ctx, sha(MAIN), deps, tc, ps)

def run_self_test() -> None:
    print("key_candidates.py self-test — [SKC]/[S2] behaviors (evidence for S5)\n")

    print("T1 — two clone paths produce equal keys ([PCS]§2.6 goal)")
    a, ba = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 -o m.o")
    b, bb = world("E:/ci/repo", "E:/ci/repo/build", "gcc -c src/main.c -Iinc -DFOO=1 -o m.o")
    check("NCC serial equal (de-tainted)", a.ncc_serial == b.ncc_serial,
          f"{a.ncc_serial!r} vs {b.ncc_serial!r}")
    check("K-A equal", ka(a) == ka(b))
    check("K-B equal", kb(a) == kb(b))
    check("K-C equal", kc(a) == kc(b))

    print("T2 — -D change → different keys (CE-2a)")
    c, bc = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=2 -o m.o")
    check("K-A differs", ka(a) != ka(c))
    check("K-B root differs", kb(a)["root"] != kb(c)["root"])
    check("K-C differs (macro env)", kc(a) != kc(c))

    print("T3 — -I order swap with shadowing risk → different keys (conservative)")
    d, bd = world(R1, C1, "gcc -c src/main.c -Iinc -I. -o m.o")
    e, be = world(R1, C1, "gcc -c src/main.c -I. -Iinc -o m.o")
    dep2 = {"inc/util.h": "h2"}
    check("include_list order kept", d.include_list != e.include_list)
    check("K-A differs", ka(d, dep2) != ka(e, dep2))
    check("K-B differs", kb(d, dep2)["root"] != kb(e, dep2)["root"])
    check("K-C differs (search-list gate)", kc(d, dep2) != kc(e, dep2))

    print("T4 — -o change → equal keys (output-only, [S2]§3.2 class D)")
    f, bf = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 -o other.o")
    check("NCC equal", a.ncc_serial == f.ncc_serial)
    check("K-A equal", ka(a) == ka(f))
    check("K-B equal", kb(a) == kb(f))
    check("K-C equal", kc(a) == kc(f))

    print("T5 — naive-normalization traps ([S2]§3.3): sort/dedup/position")
    g, _ = world(R1, C1, "gcc -c s.c -DFOO=1 -UFOO -DBAR")
    h, _ = world(R1, C1, "gcc -c s.c -UFOO -DFOO=1 -DBAR")
    check("T5a distinct NCC (order kept)", g.ncc_serial != h.ncc_serial)
    check("T5a NAIVE sort would collide (trap demonstrated)",
          SEP.join(sorted(g.ncc_serial.split(SEP))) == SEP.join(sorted(h.ncc_serial.split(SEP))))
    check("T5a keys differ", ka(g, bl="x") != ka(h, bl="x"))
    i2, _ = world(R1, C1, "gcc -c s.c -DFOO=1 -DFOO=2")
    check("T5b duplicate -D last-wins → FOO=2", i2.defines_final.get("FOO") == "2")
    check("T5b duplicates NOT deduped in NCC", i2.ncc_serial.count("-DFOO") == 2)
    j, _ = world(R1, C1, "gcc -c s.c -include cfg.h -DFEATURE=1")
    k, _ = world(R1, C1, "gcc -c s.c -DFEATURE=1 -include cfg.h")
    check("T5c -include vs -D position → different NCC", j.ncc_serial != k.ncc_serial)
    check("T5c forced-include state differs: CFG_F",
          j.macro_env.get("CFG_F") != k.macro_env.get("CFG_F"),
          f"{j.macro_env.get('CFG_F')!r} vs {k.macro_env.get('CFG_F')!r}")
    check("T5c keys differ", ka(j, bl="x") != ka(k, bl="x"))

    print("T6 — -O adjudication ([S2]§2.5): -O is semantic; -O2 vs -O3 state-equal (K-C reuse)")
    l, _ = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 -O2")
    m, _ = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 -Os")
    m3, _ = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 -O3")
    kb_a, kb_l = kb(a), kb(l)
    check("L0 equal (source unchanged)", kb_a["L0"] == kb_l["L0"])
    check("L1 differs (-O2 vs none)", kb_a["L1"] != kb_l["L1"])
    check("K-B roots differ", kb_a["root"] != kb_l["root"])
    check("K-C: -O2 vs -Os differ (__OPTIMIZE_SIZE__ sim)", kc(l) != kc(m))
    check("K-C: -O2 vs -O3 EQUAL (state reuse beyond K-A/K-B)", kc(l) == kc(m3))
    check("K-A: -O2 vs -O3 still differ (input-keyed)", ka(l) != ka(m3))
    check("-O0 defines no predefine (sim)",
          "__OPTIMIZE__" not in world(R1, C1, "gcc -c s.c -O0")[0].macro_env)

    print("T7 — rsp expansion + missing rsp deterministic-empty ([PCS]§6)")
    n2, _ = world(R1, C1, "gcc -c src/main.c -DFOO=1 @flags.rsp",
                  rsp={"flags.rsp": ["-Iinc", "-O2"]})
    o2, _ = world(R1, C1, "gcc -c src/main.c -DFOO=1 -Iinc -O2")
    check("rsp-expanded NCC == inline NCC (expansion at @ position)",
          n2.ncc_serial == o2.ncc_serial, f"{n2.ncc_serial!r} vs {o2.ncc_serial!r}")
    n3, _ = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 -O2 @flags.rsp",
                  rsp={"flags.rsp": ["-O2"]})
    check("duplicate token via rsp is KEPT (no dedup, [S2]§3.3 ex.4)",
          n3.ncc_serial.count("-O2") == 2)
    p1, _ = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 @gone.rsp")
    p2, _ = world(R1, C1, "gcc -c src/main.c -Iinc -DFOO=1 @gone.rsp")
    check("missing rsp warns (recorded, not silent)", len(p1.warnings) == 1)
    check("missing rsp is deterministic (equal keys)", ka(p1) == ka(p2))

    print("T8 — salts + UNKNOWN poison (CE-4a / CE-10 / [SKC]§1.4)")
    check("compiler version bump re-keys", ka(a, tc="gcc|12.3.0|x") != ka(a))
    check("libclang bump re-keys", ka(a, ps="libclang-16|rows/3") != ka(a))
    check("deps=None poisons (≠ any well-formed key)", ka(a, deps=None) != ka(a, deps={}))
    check("poison deterministic per world (storeable, never equal across worlds)",
          ka(a, deps=None, nonce="w1") != key_ka(b, bb, None, TC, PS, nonce="w2"))

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
        raise SystemExit(1)
    print("RESULT: all self-tests passed.")

if __name__ == "__main__":
    run_self_test()
