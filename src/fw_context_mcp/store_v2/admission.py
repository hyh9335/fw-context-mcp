"""store_v2.admission — Slice-4A fast-reuse admission protocol (P→A8).

Implements the accepted protocol order exactly (task §2, contract §9):

    P → A1 → A2 → A3 → A4 → A7-5 → A5′ → A6 → A7 → A-c5 → GATE-LEG → A8

with the accepted GATE-LEG short-circuit: BL-1 evaluates the gate pre-parse
as a cheap dict lookup; it is evaluated FIRST (before the candidate scan, so
rename/first-occurrence misses are cheap) and RE-AFFIRMED immediately before
A8 (the §13 "no admission-side transaction before the gate" rule).

Hard rules honored here (each cites its task §):

* WorkKey is candidate SELECTION only — never reuse authority (§31): A3
  merely orders candidates (``cert_id DESC``); EVERY candidate must pass
  A4+A7-5+A5′+A6+A7+A-c5 independently; a newer candidate failing any gate
  falls through to an older one.
* UNKNOWN/unreadable/probe failure NEVER becomes a hit (§12/§17): every
  miss falls through to the unchanged full path (§24); the ONLY
  admission-specific write on a miss is the diagnostic ``reuse_admissions``
  row (§23) — never a partial fast-path semantic mutation before the
  commit point (§21/§48).
* The parse-skip decision becomes FINAL only after ``bind_tu`` returns
  ``bound_new``/``bound_existing`` (§21).
* LEGACY-GATE (BL-1): a fast hit is admissible ONLY if the CURRENT legacy
  config already holds a files row for (path, context_id) with non-empty
  ``content_hash`` — evaluated from the caller-loaded per-context dict
  (runner ``existing_by_context``, keyed for the current config_hash +
  context_id), one dict lookup, before any transaction (§13).
  Rename / cross-config / first occurrence ⇒ ``legacy_gate:*`` miss.
* No Slice-4B work: no ``builtin_predefine_cache`` authority (§10), no
  C1/C2/C5/C6 (§27); A-c5 is mandatory on EVERY hit (§12).

This module NEVER raises into the caller: any internal failure is a miss
with provenance (or a silent ``None`` when not even the store is reachable).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

log = logging.getLogger(__name__)

# ── accepted constants (frozen sources, imported not redefined) ─────────────

#: The authoritative env-var list for the C7 gate (task §5 + R3 §3.4):
#: env-derived include paths are read by the COMPILER, recorded nowhere in
#: the v2 store (R3 inventory I11) ⇒ any set-but-unrecorded var ⇒ miss.
C7_ENV_VARS = (
    "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH",
    "OBJC_INCLUDE_PATH", "INCLUDE", "C_STANDARD_INCLUDE",
)


@dataclass
class FastHit:
    """A COMMITTED fast hit (A8 bind succeeded — parse-skip authorized)."""

    hashes: tuple[str, str, str]          # (source_hash, flags_hash, manifest_entry_hash)
    cert_id: int
    state_key: str
    state_generation: int
    binding_status: str                   # bound_new | bound_existing
    checks: dict = field(default_factory=dict)
    timings: dict = field(default_factory=dict)


# ── provenance (§23: diagnostic/audit; NEVER semantic truth) ────────────────

def record_admission(store, *, admitted: bool, reason: str = "",
                     run_id: str = "", tu_context_id: str = "",
                     main_file: str = "", config_hash: str = "",
                     cert_id: int | None = None, state_key: str = "",
                     state_generation: int | None = None,
                     binding_status: str = "", checks: dict | None = None,
                     evidence: dict | None = None,
                     timings: dict | None = None,
                     legacy_gate: str = "") -> None:
    """One ``reuse_admissions`` row.  Exception-isolated; best effort."""
    try:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        cur = store._conn.execute(
            "INSERT INTO reuse_admissions(ts, run_id, tu_context_id, "
            "main_file, config_hash, admitted, fallback_reason, cert_id, "
            "state_key, state_generation, binding_status, checks_json, "
            "evidence_json, timing_json, legacy_gate) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, run_id, tu_context_id, main_file, config_hash,
             1 if admitted else 0, reason[:400], cert_id, state_key,
             state_generation, binding_status,
             json.dumps(checks or {}, sort_keys=True),
             json.dumps(evidence or {}, sort_keys=True),
             json.dumps(timings or {}, sort_keys=True),
             legacy_gate[:200]))
        store._conn.commit()
        return int(cur.lastrowid)
    except Exception as exc:                              # pragma: no cover
        log.debug("reuse_admissions write failed (diagnostic only): %s", exc)
        return None


def _finalize_admission_row(store, admission_id: int | None,
                            binding_status: str,
                            state_generation: int) -> None:
    """Record the A8 outcome on the TXN-1 hit row (same admission, final
    result).  The TXN-1 row is written BEFORE the bind transaction (§22) so
    a crash mid-bind still leaves the admission evidence; the bind outcome
    is patched onto it after the commit point."""
    if admission_id is None:
        return
    try:
        row = store._conn.execute(
            "SELECT checks_json FROM reuse_admissions WHERE admission_id=?",
            (int(admission_id),)).fetchone()
        checks = {}
        if row is not None and row[0]:
            try:
                checks = json.loads(row[0])
            except Exception:                             # pragma: no cover
                checks = {}
        checks["A8"] = binding_status[:40]
        store._conn.execute(
            "UPDATE reuse_admissions SET binding_status=?, "
            "state_generation=?, checks_json=? WHERE admission_id=?",
            (binding_status[:40], int(state_generation),
             json.dumps(checks, sort_keys=True), int(admission_id)))
        store._conn.commit()
    except Exception as exc:                              # pragma: no cover
        log.debug("reuse_admissions finalize failed (diagnostic only): %s",
                  exc)


# ── the recorded-cert adapter (A8, contract C9/C10) ─────────────────────────

def _build_recorded_cert(row, *, closure_members, current_warnings,
                         current_rsp_warnings, current_display):
    """Reconstruct the bind-surface certificate from the RECORDED row.

    ``worlds.bind_tu`` + ``_ensure_world_rows`` + ``_insert_cert_rows`` +
    ``_insert_membership`` read: cert_version, status, tusemantic_key,
    parser_generation, main_file_display, language, source_blob_sha,
    key_result.L0..L3, ncc_serial, ncc_warnings, rsp_warnings, closure
    members, forced_legs, macro_env_digest/count, environment_digest,
    parser_identity, inputs.environment_digest.  Every semantic value is
    the RECORDED one (A4 verified current bytes == recorded digests);
    presentation fields of the NEW binding carry the current display and
    the A2 re-run's warnings (the honest values for this binding).
    """
    from .certify import ClosureMember, ParserIdentity
    from .keys import KEY_FORMAT, TUKeyResult

    ident_json = json.loads(row["parser_identity"] or "{}")
    ident = ParserIdentity(
        indexer_generation=int(ident_json.get("indexer_generation", 0)),
        libclang_id=str(ident_json.get("libclang_id", "")),
        capture_binary_id=str(ident_json.get("capture_binary_id", "")),
        builtin_digest=str(ident_json.get("builtin_digest", "")),
        builtin_count=int(ident_json.get("builtin_count", 0)),
        target_triple=str(ident_json.get("target_triple", "")))
    members = [ClosureMember(abs_path=m["abs_path"],
                             display_path=m["display_path"],
                             blob_sha=m["blob_sha"], generated=m["generated"],
                             role=m["role"], text=m["text"])
               for m in closure_members]
    kr = TUKeyResult(
        L0=row["l0_sha"], L1=row["l1_sha"], L2=row["l2_sha"],
        L3=row["l3_sha"], root=row["tusemantic_key"],
        key_format=KEY_FORMAT, parser_generation=int(row["parser_generation"]),
        poisoned="", world_fp="")
    env_digest = row["environment_digest"] or ""
    # Salts recomputed from the RECORDED identity components (A6 verified
    # them equal to the current ones) — exactly certify.py:682-684, so the
    # world's build identity candidate matches the run's anchored one.
    from .certify import CLASS_C_DOMAIN  # noqa: F401  (domain provenance)
    from .keys import h as _h
    toolchain_salt = _h("fwctx-tc/v1", ident.capture_binary_id)[:32]
    parser_salt = _h("fwctx-ps/v1", ident.libclang_id,
                     str(ident.indexer_generation))[:32]
    return SimpleNamespace(
        cert_version=row["cert_version"], status="certified",
        poison_reason="", tusemantic_key=row["tusemantic_key"],
        parser_generation=int(row["parser_generation"]),
        context_id=row["context_id"], config_hash=row["config_hash"],
        main_file_display=current_display, language=row["language"],
        source_blob_sha=row["source_blob_sha"], ncc_serial=row["ncc_serial"],
        ncc_warnings=list(current_warnings),
        rsp_warnings=list(current_rsp_warnings),
        forced_legs=tuple(json.loads(row["forced_legs"] or "[]")),
        closure=members, macro_env_digest=row["macro_env_digest"],
        macro_env_count=int(row["macro_env_count"] or 0),
        environment_digest=env_digest, parser_identity=ident,
        key_result=kr, path_spelling_sensitive=False,
        inputs=SimpleNamespace(environment_digest=env_digest, nonce="",
                               toolchain_salt=toolchain_salt,
                               parser_salt=parser_salt,
                               parser_generation=int(row["parser_generation"])))


# ── gate helpers ────────────────────────────────────────────────────────────

def _resolve_display(abs_path: Path, project_root: Path) -> str:
    from .certify import _project_rel
    return _project_rel(abs_path, project_root)


def _current_compile_argv(unit, compile_dir: Path):
    """Replicates certify.py:563-583 (raw argv → rsp expansion → expanded)."""
    from .certify import _expand_rsp, _raw_argv
    raw_argv = _raw_argv(unit)
    rsp_contents, rsp_unreadable = _expand_rsp(raw_argv, compile_dir)
    if rsp_unreadable:
        return None, None, None, f"rsp-unreadable:{','.join(sorted(rsp_unreadable))}"
    expanded: list[str] = []
    for tok in raw_argv:
        if tok.startswith("@") and rsp_contents.get(tok[1:]) is not None:
            expanded.extend(rsp_contents[tok[1:]])
        elif tok.startswith("@"):
            expanded.extend(rsp_contents.get(tok[1:].split("/")[-1], []))
        else:
            expanded.append(tok)
    if expanded and not expanded[0].startswith("-"):
        expanded = expanded[1:]
    return raw_argv, rsp_contents, expanded, ""


def _read_forced_inputs(expanded, compile_dir: Path):
    """Replicates certify.py:585-604 (forced-input content, cert grade)."""
    from .certify import _collect_forced_inputs
    from .ncc import _posix
    file_contents: dict[str, str] = {}
    forced_digests: list[str] = []
    for name in _collect_forced_inputs(expanded):
        fpath = Path(name)
        if not fpath.is_absolute():
            fpath = compile_dir / name
        try:
            # EXACT certify.py:604 semantics: read_text (universal newlines)
            # for the NCC content leg, raw bytes for the c3 forced digest.
            text = fpath.read_text(encoding="utf-8", errors="replace")
            raw = fpath.read_bytes()
        except OSError:
            return None, None, f"forced-input-unreadable:{name}"
        file_contents[_posix(name)] = text
        file_contents[fpath.name] = text
        forced_digests.append(hashlib.sha256(raw).hexdigest())
    return file_contents, forced_digests, ""


def _legacy_gate(existing_ctx, norm_path: str) -> str:
    """BL-1 LEGACY-GATE (decision §10): the CURRENT legacy config must
    already hold a files row for (path, context_id) with non-empty
    content_hash.  ``existing_ctx`` is the caller-loaded per-context dict
    for (current config_hash, current context_id).  Returns "" on pass,
    else the ``legacy_gate:*`` reason."""
    rec = existing_ctx.get(norm_path) if existing_ctx else None
    if rec is None:
        return "legacy_gate:no-current-config-row"
    content_hash = getattr(rec, "content_hash", None)
    if content_hash is None and isinstance(rec, dict):
        content_hash = rec.get("content_hash")
    if not content_hash:
        return "legacy_gate:empty-content-hash"
    return ""


def _driver_name(unit) -> str:
    entry = getattr(unit, "raw_entry", None) or {}
    args = entry.get("arguments")
    if args:
        return os.path.basename(str(args[0])).lower()
    command = entry.get("command")
    if command:
        return os.path.basename(str(command).split(" ", 1)[0]).lower()
    return ""


# ── the protocol ────────────────────────────────────────────────────────────

def attempt_fast_reuse(*, unit, config_hash: str, context_id: str,
                       project_root, db_path, world,
                       existing_ctx, manifest=None, hash_cache=None,
                       header_table=None) -> FastHit | None:
    """Run the accepted admission protocol for one TU, pre-parse.

    Returns a :class:`FastHit` when the parse-skip is COMMITTED (A8 bind
    succeeded); ``None`` otherwise (caller runs the unchanged full path).
    Never raises.
    """
    if world is None:                       # P: v2 flag off / world unavailable
        return None
    try:
        return _attempt(unit=unit, config_hash=config_hash,
                        context_id=context_id, project_root=project_root,
                        db_path=db_path, world=world,
                        existing_ctx=existing_ctx, manifest=manifest,
                        hash_cache=hash_cache, header_table=header_table)
    except Exception as exc:                # pragma: no cover — fail closed
        log.debug("Slice-4A admission failed (full path unaffected): %s",
                  exc, exc_info=True)
        return None


def _attempt(*, unit, config_hash, context_id, project_root, db_path, world,
             existing_ctx, manifest, hash_cache, header_table) -> FastHit | None:
    from .publish import open_v2_store_for_write

    t = {"total": time.monotonic()}
    project_root = Path(project_root).resolve()
    compile_dir = Path(str(unit.directory))
    main_path = unit.file.resolve()
    display = _resolve_display(main_path, project_root)
    checks: dict = {}
    evidence: dict = {"source_display": display, "context_id": context_id}

    # ── P — preconditions (§3) ─────────────────────────────────────────────
    if getattr(unit, "raw_entry", None) is None:
        return None                          # no raw compile command: silent
    if "clang-cl" in _driver_name(unit):     # C8 decline guard (§3/§27)
        return None                          # declined family: silent, not a miss event
    checks["P"] = "pass"

    db_path = Path(db_path)
    from .certify import v2_store_path
    v2_path = v2_store_path(db_path)
    if not v2_path.exists():
        return None                          # no v2 store yet ⇒ nothing to reuse
    try:
        store = open_v2_store_for_write(v2_path)
    except Exception:
        return None
    try:
        t["p"] = time.monotonic()

        def miss_now(reason: str, *, cert_id: int | None = None,
                     state_key: str = "", state_generation: int | None = None,
                     binding_status: str = "", legacy_gate: str = ""):
            record_admission(store, admitted=False, reason=reason,
                             run_id=world.run_id, tu_context_id=context_id,
                             main_file=display, config_hash=config_hash,
                             cert_id=cert_id, state_key=state_key,
                             state_generation=state_generation,
                             binding_status=binding_status, checks=checks,
                             evidence=evidence, timings=_finish(t),
                             legacy_gate=legacy_gate)
            return None

        # ── A1 — source L0 rehash (§4; mtime is NOT authority) ─────────────
        try:
            source_bytes = main_path.read_bytes()
        except OSError as exc:
            return miss_now(f"A1:source-unreadable:{exc}")
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        checks["A1"] = "pass"
        t["a1"] = time.monotonic()

        # ── A2 part 1 — C7 environment gate (task §5) ──────────────────────
        set_env = [v for v in C7_ENV_VARS if os.environ.get(v)]
        if set_env:
            return miss_now("A2:env-include-vars-set:" +
                            ",".join(sorted(set_env)))

        # ── A2 part 2 — argv/rsp reconstruction + forced inputs (§5) ───────
        raw_argv, rsp_contents, expanded, err = _current_compile_argv(
            unit, compile_dir)
        if err:
            return miss_now(f"A2:{err}")
        file_contents, current_forced, err = _read_forced_inputs(
            expanded, compile_dir)
        if err:
            return miss_now(f"A2:{err}")

        # ── A2 part 3 — NCC reproduction (§5) ──────────────────────────────
        from .ncc import canonicalize
        try:
            cc = canonicalize(raw_argv, compile_dir=str(compile_dir),
                              project_root=str(project_root),
                              file_contents=file_contents,
                              rsp_contents=rsp_contents)
        except Exception as exc:
            return miss_now(f"A2:ncc-canonicalization-failed:{exc}")
        checks["A2"] = "pass"
        t["a2"] = time.monotonic()

        # ── GATE-LEG short-circuit (§13; cheap, before any scan) ───────────
        from fw_context_mcp.indexer.ops import _normalize_file_path
        norm_path = _normalize_file_path(str(main_path), project_root)
        gate = _legacy_gate(existing_ctx, norm_path)
        if gate:
            return miss_now(gate, legacy_gate=gate)
        checks["GATE-LEG"] = "pass"
        t["gate_leg"] = time.monotonic()

        # ── A3 — candidate lookup via idx_tu_cert_workkey (§6) ─────────────
        # The WorkKey pair (source_blob_sha, l1_sha) drives selection.  The
        # l1_sha column stores the class-C post-step L1, whose reproduction
        # needs each candidate's recorded environment_digest — so the SQL
        # narrows on the index prefix (source_blob_sha) + structural
        # filters, and the exact l1 reproduction+comparison runs per
        # candidate below (first loop check, labelled A2:l1-mismatch).
        from .certify import CERT_VERSION, clang_cli_identity, \
            libclang_identity
        try:
            cur_libclang_id = libclang_identity()
            cur_capture_binary_id = clang_cli_identity()
        except Exception as exc:
            return miss_now(f"A6:identity-capture-failed:{exc}")
        conn = store._conn
        candidates = conn.execute(
            """
            SELECT c.*, s.publication, s.completeness_sha
            FROM tu_certificates c
            JOIN semantic_states s
              ON s.tusemantic_key = c.tusemantic_key
             AND s.parser_generation = c.parser_generation
            WHERE c.source_blob_sha = ?
              AND c.status = 'certified'
              AND c.outcome IN ('published_new','converged')
              AND c.tusemantic_key <> ''
              AND c.cert_version = ?
              AND s.publication = 'published'
            ORDER BY c.cert_id DESC
            """,
            (source_hash, CERT_VERSION)).fetchall()
        t["a3"] = time.monotonic()

        tried: dict[int, str] = {}
        chosen = None

        for row in candidates:
            cid = int(row["cert_id"])

            # ── A2 exact — recomputed L1 vs recorded (§5) ──────────────────
            from .certify import apply_class_c_post_step, \
                certified_roots_from_argv
            from .keys import h
            recorded_serial = apply_class_c_post_step(
                cc.ncc_serial, row["environment_digest"] or "",
                certified_roots_from_argv(expanded))
            fresh_l1 = h("L1", recorded_serial)
            if fresh_l1 != row["l1_sha"]:
                tried[cid] = "A2:l1-mismatch"
                evidence.setdefault("l1_debug", []).append({
                    "cert_id": cid,
                    "stored_l1": row["l1_sha"], "fresh_l1": fresh_l1,
                    "stored_serial": row["ncc_serial"],
                    "fresh_serial": cc.ncc_serial,
                    "post_serial": recorded_serial,
                    "stored_env": (row["environment_digest"] or "")[:24],
                    "raw_argv": raw_argv,
                    "expanded": expanded,
                    "fc_keys": sorted(file_contents.keys()) if file_contents else [],
                    "fc_shas": {k: hashlib.sha256(v.encode("utf-8")).hexdigest()[:16]
                                for k, v in (file_contents or {}).items()},
                })
                continue

            # ── A4 — recorded closure rehash at recorded resolution (§7) ───
            members, a4_reason, verified_seq = _a4_rehash(row, project_root)
            if a4_reason:
                tried[cid] = a4_reason
                continue
            if tuple(current_forced) != tuple(
                    json.loads(row["forced_legs"] or "[]")):
                tried[cid] = "A4:forced-legs-mismatch"
                continue
            checks["A4"] = "pass"
            t.setdefault("a4", time.monotonic())

            # ── A7-5 — same-world pin (§8) ─────────────────────────────────
            a75 = _a7_5_pin(conn, cid, str(main_path.parent),
                            str(compile_dir))
            if a75:
                tried[cid] = a75
                continue
            checks["A7-5"] = "pass"

            # ── A5′ — fresh directory attestation (§9) ─────────────────────
            att_rows = conn.execute(
                "SELECT * FROM cert_dir_attestations WHERE cert_id=? "
                "ORDER BY dir_ordinal", (cid,)).fetchall()
            if not att_rows:
                tried[cid] = "A5':no-attestations"       # accepted ramp (§46)
                continue
            memo: dict = {}
            a5_misses = observe_attested_dirs(att_rows, memo)
            if a5_misses:
                tried[cid] = a5_misses[0]
                continue
            checks["A5'"] = "pass"
            t.setdefault("a5", time.monotonic())

            # ── A6 — parser identity, Slice-4A form (§10) ──────────────────
            a6 = _a6_identity(row, cur_libclang_id, cur_capture_binary_id,
                              expanded)
            if a6:
                tried[cid] = a6
                continue
            checks["A6"] = "pass"
            t.setdefault("a6", time.monotonic())

            # ── A7 — state health (§11) ────────────────────────────────────
            a7 = _a7_state_health(conn, row)
            if a7:
                tried[cid] = a7
                continue
            checks["A7"] = "pass"
            t.setdefault("a7", time.monotonic())

            # ── A-c5 — macro re-derivation + L2 recompose (§12) ─────────────
            # Slice-4B: A-c5 is NO LONGER mandatory on the normal HIT path.
            # The structural replacements C1 (implicit-chain attestation),
            # C2 (joined include forms), C5 (content-derived builtin proof),
            # and C6 (NCC -Wp, correctness) now independently guard the
            # three classes A-c5 previously caught:
            #   J1(b) implicit include-chain mutation → C1 + C2 (A5′ rows)
            #   J2 in-place toolchain replacement    → C5 (A6 builtin_digest)
            #   N2 -Wp, semantic collision           → C6 (NCC serial differs)
            # A-c5 runs ONLY when FW_CONTEXT_4B_DUAL_PROOF is set (shadow
            # validation mode, task §20).  This keeps the old proof available
            # for divergence analysis without making it permanent production
            # cost (task §20 last paragraph).
            if os.environ.get("FW_CONTEXT_4B_DUAL_PROOF"):
                ac5 = _a_c5(row, verified_seq, expanded, main_path,
                            compile_dir)
                if ac5:
                    tried[cid] = ac5
                    continue
                checks["A-c5"] = "pass(dual-proof)"
                t["a_c5"] = time.monotonic()
            else:
                # Normal production 4B HIT path: A-c5 SKIPPED.
                checks["A-c5"] = "skipped(4B)"
                t["a_c5"] = time.monotonic()

            chosen = (row, members)
            checks["A3"] = f"pass(cert={cid})"
            break

        if chosen is None:
            reason = tried[next(iter(tried))] if tried else "A3:no-candidate"
            evidence["candidates_tried"] = {str(k): v
                                            for k, v in tried.items()}
            checks["candidate_count"] = len(candidates)
            return miss_now(reason, cert_id=(max(tried) if tried else None))
        row, members = chosen

        # ── GATE-LEG re-affirmed (§13: pre-transaction, pre-A8) ────────────
        gate = _legacy_gate(existing_ctx, norm_path)
        if gate:
            return miss_now(gate, cert_id=int(row["cert_id"]),
                            state_key=row["tusemantic_key"],
                            state_generation=int(row["parser_generation"]),
                            legacy_gate=gate)
        checks["GATE-LEG-2"] = "pass"
        t["gate_leg2"] = time.monotonic()

        # ── hashes for the BL-1 minimum bookkeeping (fresh, current world) ─
        from fw_context_mcp.indexer.config_hash import compute_flags_hash
        from fw_context_mcp.utils import compute_source_hash
        from fw_context_mcp.indexer._unit_processor import (
            _get_manifest_entry_hash_for_unit)
        flags_hash = compute_flags_hash(unit.raw_entry)
        manifest_entry_hash = _get_manifest_entry_hash_for_unit(
            unit, project_root, manifest, hash_cache=hash_cache,
            header_table=header_table, context_id=context_id)
        hashes = (compute_source_hash(main_path), flags_hash,
                  manifest_entry_hash)

        # ── TXN-1 — provenance (§22/§23), separate txn from bind ──────────
        cid = int(row["cert_id"])
        adm_id = record_admission(
            store, admitted=True, reason="",
            run_id=world.run_id, tu_context_id=context_id,
            main_file=display, config_hash=config_hash,
            cert_id=cid, state_key=row["tusemantic_key"],
            state_generation=int(row["parser_generation"]),
            checks=checks, evidence=evidence,
            timings=_finish(t, stop=False),
            legacy_gate="pass")
        t["txn1"] = time.monotonic()

        # ── A8 — bind via the recorded-cert adapter (§20) ──────────────────
        adapter = _build_recorded_cert(
            row, closure_members=members,
            current_warnings=list(cc.warnings), current_rsp_warnings=[],
            current_display=display)
        from .worlds import bind_tu
        result = bind_tu(store, world, adapter, tu_context_id=context_id,
                         canon_source_path=display,
                         state_key=row["tusemantic_key"],
                         state_generation=int(row["parser_generation"]),
                         is_new_state=False)
        t["a8"] = time.monotonic()
        if result.status not in ("bound_new", "bound_existing"):
            # Commit point NOT reached (§21): honest miss, full path runs.
            world.inc("fast_bind_refused")
            return miss_now(f"bind_refused:{result.status}:{result.detail}",
                            cert_id=cid, state_key=row["tusemantic_key"],
                            state_generation=int(row["parser_generation"]),
                            binding_status=result.status)
        # ── COMMIT POINT (§21): bind success ⇒ skip decision FINAL ────────
        checks["A8"] = result.status
        _finalize_admission_row(store, adm_id, result.status,
                                int(row["parser_generation"]))
        world.inc("fast_hits")
        world.inc("parse_skipped")
        return FastHit(hashes=hashes, cert_id=cid,
                       state_key=row["tusemantic_key"],
                       state_generation=int(row["parser_generation"]),
                       binding_status=result.status, checks=checks,
                       timings=_finish(t, stop=False))
    finally:
        try:
            store.close()
        except Exception:                                 # pragma: no cover
            pass


# ── per-candidate gate implementations ──────────────────────────────────────

def _a4_rehash(row, project_root: Path):
    """Re-read every recorded closure member at its recorded resolution;
    verify content digest, count, order; no null/unresolved member (§7).
    Returns (member_rows, "", verified_c4_seq) or (None, reason, None)."""
    rows = _json_list(row["closure"])
    if not rows:
        return None, "A4:closure-empty", None
    out: list[dict] = []
    seq: list[str] = []
    for m in rows:
        disp = str(m.get("path", ""))
        sha = m.get("blob_sha")
        if not disp or not sha:
            return None, "A4:null-or-unresolved-member", None
        p = Path(disp)
        if not p.is_absolute():
            p = project_root / disp
        try:
            raw = p.read_bytes()
        except OSError:
            return None, f"A4:member-unreadable:{disp}", None
        digest = hashlib.sha256(raw).hexdigest()
        if digest != sha:
            return None, f"A4:content-mismatch:{disp}", None
        out.append({"abs_path": str(p), "display_path": disp,
                    "blob_sha": sha,
                    "generated": bool(m.get("generated", False)),
                    "role": str(m.get("role", "header")),
                    "text": raw.decode("utf-8", errors="replace")})
        seq.append(sha)
    return out, "", seq


def _a7_5_pin(conn, cert_id: int, source_dir: str, compile_dir: str) -> str:
    """A7-5 (§8): source dir + compile dir must BE the attested ones
    (normalized source/compile directory equality — cross-directory move
    ⇒ miss).  The pin/listing freshness is A5′; this is the world pin."""
    from .attest import _normcase_posix
    for kind, cur in (("source-dir", source_dir), ("compile-dir", compile_dir)):
        rec = conn.execute(
            "SELECT dir_normcase FROM cert_dir_attestations "
            "WHERE cert_id=? AND kind=?", (cert_id, kind)).fetchone()
        if rec is None:
            return f"A7-5:{kind}-not-attested"
        if _normcase_posix(cur) != rec["dir_normcase"]:
            return f"A7-5:{kind}-moved"
    return ""


def _a6_identity(row, libclang_id: str, capture_binary_id: str,
                 expanded) -> str:
    """A6 Slice-4A form (§10): cheap identity components + indexer
    generation.

    Slice-4B C5: now ALSO re-derives the builtin predefine digest from the
    CURRENT compiler and compares it to the certified ``builtin_digest``.
    This closes the J2 hole (in-place toolchain replacement with stable
    version strings) WITHOUT per-TU A-c5, because the builtin set is
    TU-independent (depends only on compiler/target/sysroot, not on TU
    source or closure).  The re-derivation is amortized per
    (binary, target_flags) within the process via
    :func:`certify.discover_builtin_digest` (the C5 cache, content-derived).

    The OLD string-only identity (libclang_id, capture_binary_id,
    target_triple) remains as a fast pre-filter — a string mismatch is a
    cheap MISS before the more expensive builtin probe runs.
    """
    from .certify import INDEXER_GENERATION, resolve_target_triple
    try:
        ident = json.loads(row["parser_identity"] or "{}")
    except Exception:
        return "A6:parser-identity-unreadable"
    if not ident or ident.get("failed"):
        return "A6:candidate-identity-failed"
    if int(ident.get("indexer_generation", -1)) != INDEXER_GENERATION:
        return "A6:indexer-generation-changed"
    if ident.get("libclang_id") != libclang_id:
        return "A6:libclang-changed"
    if ident.get("capture_binary_id") != capture_binary_id:
        return "A6:capture-binary-changed"
    try:
        if ident.get("target_triple") != resolve_target_triple(expanded):
            return "A6:target-changed"
    except Exception as exc:
        return f"A6:target-resolution-failed:{exc}"
    # ── Slice-4B C5: content-derived builtin/toolchain proof ──────────
    # Re-derive the builtin predefine digest from the CURRENT compiler
    # using the SAME (binary, target_flags) key the certifier used.  The
    # probe is amortized per-process (content-derived cache); the FIRST
    # call in a process pays the clang subprocess, later calls reuse.
    # A mismatch ⇒ the toolchain was semantically mutated in-place (J2).
    cert_builtin_digest = ident.get("builtin_digest", "")
    if cert_builtin_digest:
        try:
            from .certify import rederive_builtin_digest
            cur_digest = rederive_builtin_digest(expanded)
        except Exception as exc:
            # C5 probe failure ⇒ fail closed (FAST_MISS).  Never accept
            # through unknown toolchain state (task §21).
            return f"A6:c5-probe-failed:{exc}"
        if cur_digest is None:
            # Probe could not establish evidence ⇒ UNKNOWN ⇒ full path.
            return "A6:c5-evidence-unavailable"
        if cur_digest != cert_builtin_digest:
            return "A6:c5-builtin-digest-mismatch"
    else:
        # Old certificate without builtin_digest (pre-C5 cert_version).
        # cert_version=v4+ always has it; a v4 cert missing it is a
        # malformed cert ⇒ honest MISS.
        return "A6:c5-no-certified-builtin-digest"
    return ""


def _a7_state_health(conn, row) -> str:
    """A7 (§11): publication == published, fact_set_digest ==
    completeness_sha, no W-1/refusal integrity events (A-2 spelling mirror
    is re-checked inside bind_tu — gate 2)."""
    key = row["tusemantic_key"]
    generation = int(row["parser_generation"])
    if row["publication"] != "published":
        return "A7:state-not-published"
    fact_digest = row["fact_set_digest"] or ""
    completeness = row["completeness_sha"] or ""
    if not fact_digest or fact_digest != completeness:
        return "A7:fact-set-digest-mismatch"
    n = conn.execute(
        "SELECT count(*) FROM integrity_events WHERE tusemantic_key=? AND "
        "parser_generation=?", (key, generation)).fetchone()[0]
    if int(n) > 0:
        return "A7:integrity-events-present"
    return ""


def _a_c5(row, verified_c4_seq, expanded, main_path: Path,
          compile_dir: Path) -> str:
    """A-c5 (§12): mandatory fresh ``clang -dM -E`` over the CURRENT
    post-rsp argv + current TU cwd; canonicalize exactly as Slice-1
    certification (sorted #define lines, SHA-256, count verified);
    recompose required L2 from recorded c3 + A4-verified c4 + fresh c5 and
    compare with the candidate's recorded l2_sha and macro count."""
    from .certify import _run_dm_e, macro_environment_digest
    from .keys import KEY_FORMAT, L2_DOMAIN, h

    from fw_context_mcp.indexer.macros import _find_clang_binary, \
        _sanitize_flags
    sanitized = _sanitize_flags([_find_clang_binary(), *expanded])
    flags = sanitized[1:]
    try:
        lines = _run_dm_e(flags, main_path, cwd=str(compile_dir))
    except RuntimeError as exc:
        return f"A-c5:probe-failed:{exc}"
    digest, count = macro_environment_digest(lines)
    if digest != row["macro_env_digest"] or \
            count != int(row["macro_env_count"] or 0):
        return "A-c5:macro-env-mismatch"
    # Required-L2 recomposition (contract §12): recorded c3 legs (A4
    # verified equal to current), A4-verified c4 content sequence, fresh c5.
    forced = json.loads(row["forced_legs"] or "[]")
    l2_fresh = h(L2_DOMAIN, h("c3", *forced),
                 h("c4-content-seq", *verified_c4_seq), digest, KEY_FORMAT)
    if l2_fresh != row["l2_sha"]:
        return "A-c5:l2-recomposition-mismatch"
    return ""


def _json_list(text) -> list:
    try:
        value = json.loads(text or "[]")
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _finish(t: dict, stop: bool = True) -> dict:
    """Turn the monotonic checkpoint dict into a step-duration dict (ms).

    ``ms_total`` is the elapsed wall time AT RECORDING TIME — on the TXN-1
    hit row that is the total up to the provenance write (the bind cost is
    not yet known, §22 ordering); on miss rows it is the full attempt.
    """
    order = ["p", "a1", "a2", "gate_leg", "a3", "a4", "a5", "a6", "a7",
             "a_c5", "gate_leg2", "txn1", "a8"]
    keys = [k for k in order if k in t]
    out = {f"ms_{b}": round((t[b] - t[a]) * 1000.0, 3)
           for a, b in zip(keys, keys[1:])}
    out["ms_total"] = round((time.monotonic() - t["total"]) * 1000.0, 3)
    return out


# admission.observe_attested_dirs is re-exported for callers that want the
# A5′ primitive directly (tests):
from .attest import observe_attested_dirs  # noqa: E402,F401
