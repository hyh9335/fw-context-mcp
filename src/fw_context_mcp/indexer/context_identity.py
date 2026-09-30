"""Compile-context identity — the single shared helper (T14 ADR-001).

ADR-001 defines ONE canonical per-compile-action identity:

    context_id = H(build_id, main_file, project-relative, compile_directory,
                   canonical_argv, response_file_content_hashes, output_name)

truncated to 16 hex chars.  One context is one entry of compile_commands.json:
the same source file listed twice with different ``-D`` flags is two contexts
of one build.  ``build_id`` is the build's ``config_hash`` — the set-union
dialect hash computed by :func:`manifest.compute_config_hash`, which after
this module exists is DEMOTED to the grouping component of the context
identity: it may never again be used as a per-context identity on its own.

T02's original formula (defines + dialect flags + output) survives as the
*dialect* hash (:func:`dialect_hash`) — grouping metadata, never an identity:
it drops ``main_file``/``cwd``, so two files compiled with the same flags
would collapse.

Canonical argv normalization lives HERE so manifest (T01) and storage (T02)
cannot drift apart — ADR-001's risk note mandates a single helper module.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path
from typing import Any, Iterable, Sequence

# The context pre-context code writes rows into; every migrated row and every
# write path that has no context yet lives here (ADR-005).
CANONICAL_CONTEXT_ID = ""

# ADR-001: the identity is truncated to 16 hex chars.
_CONTEXT_ID_HEX_CHARS = 16

# Separator for the stable serialization: a control char that cannot appear
# in a path or a compiler flag, so component boundaries are unambiguous.
_SEP = "\x1f"


def _clean(value: Any) -> str:
    """Normalize one component to a string with separators neutralized."""
    if value is None:
        return ""
    text = str(value)
    return text.replace(_SEP, " ").replace("\x00", " ")


def stable_serialization(*components: Any) -> str:
    """Serialize identity components into one deterministic string.

    Order IS meaningful (it is the ADR-001 formula order); inside a
    component, order is the caller's contract.  No JSON: the serialization
    must not depend on dict ordering or on how a component was built.
    """
    return _SEP.join(_clean(c) for c in components)


def canonical_argv(argv: Iterable[str]) -> list[str]:
    """Normalize one compile command's argv for identity purposes.

    The rule is deliberately minimal and shared by every consumer (ADR-001
    risk note): a flag and its separately-spelled value are joined
    (``-D NAME=V`` → ``-DNAME=V``) so the two spellings of one command get
    one identity, empty tokens are dropped, and ORDER is otherwise kept —
    argument order can change what a compiler does, so reordering here would
    erase real contexts.  The compiler binary is not part of the dialect: the
    caller strips it (same rule as ``compile_commands.parse``).
    """
    joined = ("-D", "-U", "-include", "-imacros", "-include-pch", "-isystem",
              "-I", "-iquote", "-L", "-l", "-MF", "-MT", "-MQ", "-o", "-x")
    out: list[str] = []
    tokens = [t for t in argv if t]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in joined and i + 1 < len(tokens):
            out.append(tok + tokens[i + 1])
            i += 2
            continue
        out.append(tok)
        i += 1
    return out


def defines_of_argv(argv: Iterable[str]) -> dict[str, str]:
    """Extract the ``-D`` defines of one compile command as ``{name: value}``.

    Handles both the joined (``-DNAME=V``) and the separated (``-D NAME=V``)
    spelling.  A bare ``-DNAME`` yields an empty-string value.  This is the
    per-context record of what ADR-001 puts into the identity, and what the
    conditionals slice (SLICE-F) reads as command-line provenance.
    """
    defines: dict[str, str] = {}
    tokens = [t for t in argv if t]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-D" and i + 1 < len(tokens):
            body = tokens[i + 1]
            i += 2
        elif tok.startswith("-D"):
            body = tok[2:]
            i += 1
        else:
            i += 1
            continue
        name, _, value = body.partition("=")
        if name:
            defines[name] = value
    return defines


def dialect_token_kind(token: str) -> str | None:
    """Classify one argv token for dialect collection.

    Returns ``"define"`` for ``-D`` macros, ``"dialect"`` for any other flag,
    ``None`` for a non-flag (filename or stray value).  This is the SHARED
    vocabulary of the build-level set-union in ``manifest.compute_config_hash``
    and of the per-context dialect hash below — one classification, two
    scopes, no drift.
    """
    if not token.startswith("-"):
        return None
    if token.startswith("-D"):
        return "define"
    return "dialect"


def dialect_hash(argv: Iterable[str], output_name: str = "") -> str:
    """T02's demoted formula: H(defines + dialect flags + output), 16 hex.

    Grouping metadata only — it drops ``main_file``/``cwd``, so it must never
    be used as a context identity (ADR-001).  Deterministic across argument
    order: the defines and dialect flags are sorted sets.
    """
    defines: list[str] = []
    flags: list[str] = []
    for tok in canonical_argv(argv):
        kind = dialect_token_kind(tok)
        if kind == "define":
            defines.append(tok)
        elif kind == "dialect":
            flags.append(tok)
    serialization = stable_serialization(
        sorted(defines), sorted(flags), _clean(output_name)
    )
    return hashlib.sha256(serialization.encode("utf-8")).hexdigest()[:_CONTEXT_ID_HEX_CHARS]


def response_file_hashes(argv: Iterable[str], cwd: str | Path = "") -> list[str]:
    """SHA-256 content hash of every response file the command expands.

    Positional: one entry per ``@rsp`` token in *argv*, in order — ``""`` when
    the file is missing (the identity still moves, deterministically).  ADR-001
    names response-file content a component precisely because an rsp file can
    change the compile without touching the command line.
    """
    hashes: list[str] = []
    for tok in argv:
        if not tok.startswith("@"):
            continue
        rsp = Path(tok[1:])
        if cwd and not rsp.is_absolute():
            rsp = Path(cwd) / rsp
        try:
            hashes.append(hashlib.sha256(rsp.read_bytes()).hexdigest())
        except OSError:
            hashes.append("")
    return hashes


def raw_argv_of_entry(raw_entry: dict | None) -> list[str]:
    """Extract the raw compiler argv of one compile_commands.json entry.

    Mirrors ``compile_commands.parse``: ``arguments`` list, else ``command``
    string split with shlex; the compiler binary (argv[0] when it is not a
    flag) is dropped — the dialect helper consumes flags only.  Response-file
    tokens stay as ``@...`` tokens; their content hashes are a separate
    identity component.
    """
    if not raw_entry:
        return []
    raw_args = raw_entry.get("arguments") or shlex.split(raw_entry.get("command", ""))
    if raw_args and not raw_args[0].startswith("-"):
        raw_args = raw_args[1:]
    return list(raw_args)


def _project_relative(path: str, project_root: str | Path) -> str:
    """Project-relative form of *path*, falling back to the absolute path."""
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(Path(project_root).resolve()))
    except (ValueError, OSError):
        return _clean(path)


def compute_context_id(
    config_hash: str,
    main_file: str,
    project_root: str | Path = "",
    compile_dir: str | Path = "",
    argv: Sequence[str] = (),
    response_hashes: Sequence[str] = (),
    output_name: str = "",
) -> str:
    """Return the ADR-001 context identity of one compile action, 16 hex.

    ``context_id = H(build_id, main_file, project-relative, compile_directory,
    canonical_argv, response_file_content_hashes, output_name)``.

    * ``build_id`` is the build's ``config_hash`` (the demoted set-union
      dialect hash — grouping only).
    * ``main_file`` is recorded project-relative when it lives under
      *project_root*, absolute otherwise, so the identity survives a checkout
      move but not a rename.
    * ``canonical_argv`` uses :func:`canonical_argv` — the one normalization
      both manifest and storage share.
    """
    serialization = stable_serialization(
        _clean(config_hash),
        _project_relative(main_file, project_root) if project_root else _clean(main_file),
        _clean(str(Path(compile_dir).resolve())) if compile_dir else "",
        _SEP.join(_clean(t) for t in canonical_argv(argv)),
        _SEP.join(_clean(h) for h in response_hashes),
        _clean(output_name),
    )
    return hashlib.sha256(serialization.encode("utf-8")).hexdigest()[:_CONTEXT_ID_HEX_CHARS]


def unit_identity_fields(
    unit: Any,
    config_hash: str,
    project_root: str | Path,
    entry_index: int = 0,
) -> dict[str, Any]:
    """Compute every build_contexts field of one CompilationUnit.

    One call site in the runner keeps the identity derivation single: argv
    from the raw entry, response hashes from the rsp tokens of that argv,
    output from the entry, defines from the argv, flags hash from the entry,
    and the ADR-001 ``context_id`` from all of them together.  Computing the
    identity HERE — not from the returned fields — is what makes the row a
    faithful function of the compile action.

    ``entry_index`` is the unit's position in this run's entry list: where
    the context came from, not part of the identity (ADR-001 — the identity
    has no positional component, so a reordered compile_commands.json keeps
    every context_id stable).
    """
    raw_entry = getattr(unit, "raw_entry", None)
    argv = raw_argv_of_entry(raw_entry)
    cwd = str(getattr(unit, "directory", "") or "")
    output_name = str((raw_entry or {}).get("output") or "")
    main_file = str(Path(unit.file).resolve())
    compile_dir = str(Path(cwd).resolve()) if cwd else ""
    resp_hashes = response_file_hashes(argv, cwd)
    from .config_hash import compute_flags_hash

    # Command-line defines as SPELLED (``-DNAME=V`` tokens of the canonical
    # argv): the faithful provenance record of what this context's command
    # line contributed.  The conditionals slice (SLICE-F) parses these.
    define_tokens = sorted(tok for tok in canonical_argv(argv) if tok.startswith("-D"))

    return {
        "config_hash": config_hash,
        "context_id": compute_context_id(
            config_hash=config_hash,
            main_file=main_file,
            project_root=project_root,
            compile_dir=compile_dir,
            argv=argv,
            response_hashes=resp_hashes,
            output_name=output_name,
        ),
        "entry_index": entry_index,
        "main_file": main_file,
        "compile_dir": compile_dir,
        "canonical_argv": _SEP.join(canonical_argv(argv)),
        "response_hashes": _SEP.join(resp_hashes),
        "output_name": output_name,
        "defines_json": _SEP.join(define_tokens),
        "flags_hash": compute_flags_hash(raw_entry) if raw_entry else "",
    }
