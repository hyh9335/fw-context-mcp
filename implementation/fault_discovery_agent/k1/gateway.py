"""K1Gateway — the Evidence Gateway (K1_INTERFACE_SPEC.md).

Contract clauses implemented:
- C1 snapshot identity: every fact carries the round's pinned
  source/build snapshot ids + freeze_id; queries whose build_snapshot_id
  differs from the round pin are refused (E-BUILD-SNAPSHOT).
- C2 T0 named-scope enforcement: only the four T0 scopes of
  CURRENT_CAPABILITY_MAP §T0. Out-of-scope use AUTO-DOWNGRADES (recorded in
  trust_class + detector_checks) or refuses — never silent.
- C3 T1 fail-closed: fallback/suggestion rows rejected (G6); truncation
  => truncated_lower_bound, completeness claims refused (G7); absence is
  explicit no-match + NON-EVIDENCE near-miss list.
- §4 semantics: macro-value rows degraded (G8); every row freeze_id +
  recorded_at stamped before any measurement consumes it.
- HARD RULE: all substrate access flows through this gateway. Components
  must not call substrate surfaces directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schemas import file_sha256
from . import channels, detectors
from .factlog import ERROR_TAXONOMY, FactLog
from .substrate import RawResponse, call_substrate, run_git_head, run_git_status, substrate_identity

_NEAR_MISS_RE = re.compile(r"\b(\w+)\b")


def _findings_to_strings(findings: list[dict]) -> list[str]:
    """Frozen FACT_RECORD_SCHEMA: detector_checks items match '^G[0-9]+$' —
    the ids of triggered detectors. The rich finding detail lives on the
    GatewayResult and in refusal rows (never inside the FactRecord)."""
    out = []
    for f in findings or []:
        did = str(f.get("detector", "")).strip()
        if did and did not in out:
            out.append(did)
    return sorted(out)


@dataclass
class RoundPin:
    freeze_id: str
    case: str
    project_root: str
    source_snapshot_id: str
    build_snapshot_id: str
    repository: str
    commit: str
    index_home: str
    fact_log_path: Path
    artifact_store: Path


@dataclass
class GatewayResult:
    """Outcome of one K1 surface call."""

    ok: bool
    fact: dict | None
    raw: RawResponse | None
    refusals: list[dict] = field(default_factory=list)
    detector_findings: list[dict] = field(default_factory=list)
    trust_class: str | None = None
    notes: list[str] = field(default_factory=list)


class K1Gateway:
    """Sole access path to fw-context for every Agent component (T0 included)."""

    def __init__(self, pin: RoundPin, fact_log: FactLog | None = None):
        self.pin = pin
        self.log = fact_log or FactLog(pin.fact_log_path)
        self._identity_checked = False
        self._id_counter = 0

    def mint_fact_id(self, prefix: str = "F") -> str:
        """Sequential fact-id helper for drivers composing multi-query sweeps.

        The counter must never collide with ids already in the fact log:
        drivers may call this after loop runs minted their own ids, so seed
        the counter from the log's high-water mark on first use."""
        if not getattr(self, "_id_seeded", False):
            used = [f.get("fact_id", "") for f in self.log.all_facts()]
            n = 0
            for u in used:
                m = re.match(rf"^{re.escape(prefix)}-(\d+)$", str(u))
                if m:
                    n = max(n, int(m.group(1)))
            self._id_counter = max(self._id_counter, n)
            self._id_seeded = True
        self._id_counter += 1
        return f"{prefix}-{self._id_counter:04d}"

    # ── internal plumbing ───────────────────────────────────────────────────
    def _artifact_store(self) -> Path:
        d = Path(self.pin.artifact_store)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _mint_fact(
        self,
        *,
        fact_id: str,
        file: str,
        range_start: int,
        range_end: int,
        evidence_surface: str,
        exact_query: str,
        raw: RawResponse,
        trust_class: str,
        certainty: str = "measured",
        polarity: str = "positive",
        detector_checks: list | None = None,
        completeness_status: str = "complete",
    ) -> dict:
        return self.log.mint(
            fact_id=fact_id,
            source_snapshot_id=self.pin.source_snapshot_id,
            build_snapshot_id=self.pin.build_snapshot_id,
            repository=self.pin.repository,
            commit=self.pin.commit,
            file=file,
            range_start=range_start,
            range_end=range_end,
            evidence_surface=evidence_surface,
            exact_query=exact_query,
            raw_artifact_ref=(f"{raw.artifact_path}#{raw.sha256}" if raw.artifact_path else "inline"),
            trust_class=trust_class,
            provenance={
                "instrument": "fw-context-mcp 0.31.0 @ d8f08a41d3fb9df6de255a187720f6332ed0f1c9",
                "channel": "in-band substrate",
                "k1_mediated": True,
            },
            certainty=certainty,
            polarity=polarity,
            context_identity=f"{self.pin.case}:{self.pin.freeze_id}",
            detector_checks=_findings_to_strings(detector_checks),
            completeness_status=completeness_status,
            freeze_id=self.pin.freeze_id,
            recorded_at=_utcnow(),
        )

    def _mint_independent_fact(
        self,
        *,
        fact_id: str,
        file: str,
        range_start: int,
        range_end: int,
        evidence_surface: str,
        exact_query: str,
        raw_text: str,
        instrument: str,
        trust_class: str = "T1",
        certainty: str = "measured",
        polarity: str = "positive",
        completeness_status: str = "complete",
    ) -> dict:
        # persist raw output
        from ..schemas import canonical_json_bytes, sha256_hex

        store = self._artifact_store()
        stem = sha256_hex(canonical_json_bytes({"q": exact_query}))[:16]
        out = store / f"{fact_id}_{stem}.txt"
        out.write_text(raw_text, encoding="utf-8")
        return self.log.mint(
            fact_id=fact_id,
            source_snapshot_id=self.pin.source_snapshot_id,
            build_snapshot_id=self.pin.build_snapshot_id,
            repository=self.pin.repository,
            commit=self.pin.commit,
            file=file,
            range_start=range_start,
            range_end=range_end,
            evidence_surface=evidence_surface,
            exact_query=exact_query,
            raw_artifact_ref=f"{out}#{file_sha256(out)}",
            trust_class=trust_class,
            provenance={"instrument": instrument, "channel": "independent raw-disk/preprocessor/out-of-band", "k1_mediated": False},
            certainty=certainty,
            polarity=polarity,
            context_identity=f"{self.pin.case}:{self.pin.freeze_id}",
            detector_checks=[],
            completeness_status=completeness_status,
            freeze_id=self.pin.freeze_id,
            recorded_at=_utcnow(),
        )

    # ── G1 substrate pin check (once per gateway, mandatory before use) ────
    def verify_substrate_pin(self) -> list[dict]:
        repo_root = r"D:\github\fw-context-mcp"
        if not Path(repo_root, ".git").is_dir():
            repo_root = self.pin.project_root
        head = run_git_head(repo_root)
        git_status = run_git_status(repo_root)
        findings = detectors.detect_g1_substrate_pin(head, git_status)
        for f in findings:
            if f["verdict"] == "FAIL":
                self.log.refuse(
                    tool="<substrate-pin>",
                    args={"head_commit": head},
                    code="E-SUBSTRATE",
                    detail=f"G1 substrate pin check failed: {f['detail']}",
                    freeze_id=self.pin.freeze_id,
                    detector_findings=findings,
                )
        self._identity_checked = all(f["verdict"] == "PASS" for f in findings)
        return findings

    def _require_pin_checked(self) -> None:
        if not self._identity_checked:
            findings = self.verify_substrate_pin()
            if not self._identity_checked:
                raise RuntimeError(f"substrate pin not verified: {findings}")

    def _call(self, tool: str, args: dict) -> RawResponse:
        self._require_pin_checked()
        raw = call_substrate(
            tool,
            args,
            project_root=self.pin.project_root,
            index_home=self.pin.index_home,
            artifact_store=self._artifact_store(),
        )
        # Handlers may return a payload-level {'error': ...} dict instead of
        # raising — normalize it into the error channel so queries refuse.
        if raw.error is None and isinstance(raw.payload, dict) and "error" in raw.payload:
            raw.error = str(raw.payload["error"])
            raw.payload = None
            raw.raw_text = raw.raw_text  # raw artifact keeps the error payload
        return raw

    # ── T0 scope (a): conditional structure of the active build ────────────
    def query_conditional_skeleton(self, file_path: str, fact_id: str) -> GatewayResult:
        raw = self._call("list_conditional_regions", {"file_path": file_path})
        if raw.error:
            refusal = self.log.refuse(tool="list_conditional_regions", args={"file_path": file_path}, code="E-SUBSTRATE", detail=raw.error, freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal])
        payload = raw.payload or {}
        regions = payload.get("regions", []) if isinstance(payload, dict) else []
        # region rows carry root_line/end_line (branches carry line)
        fact = self._mint_fact(
            fact_id=fact_id,
            file=file_path,
            range_start=regions[0].get("root_line", 0) if regions else 0,
            range_end=regions[-1].get("end_line", 0) if regions else 0,
            evidence_surface="fw_context_list_conditional_regions",
            exact_query=f"list_conditional_regions(file_path={file_path!r})",
            raw=raw,
            trust_class="T0",
            completeness_status="complete",
        )
        return GatewayResult(ok=True, fact=fact, raw=raw, trust_class="T0")

    def query_definition_window(
        self, file_path: str, start_line: int, end_line: int, fact_id: str, *, intent: str = "region_truth"
    ) -> GatewayResult:
        """T0(a) ONLY for multi-line windows. A single-line window is flagged
        by G4 and routed to T2 discipline: the fact is minted T2 with the G4
        detector duty attached — load-bearing use then requires a >=2-surface
        cross-check (E3), enforced by K2's gate, not by a silent pass."""
        single_line = (end_line == start_line)
        raw = self._call(
            "read_file",
            {
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "mode": "active",
            },
        )
        if raw.error:
            refusal = self.log.refuse(tool="read_file", args={"file_path": file_path, "start_line": start_line, "end_line": end_line}, code="E-SUBSTRATE", detail=raw.error, freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal])
        findings = detectors.run_all_detectors("read_file", {"start_line": start_line, "end_line": end_line}, raw.payload, intent=intent)
        if single_line:
            findings.append({
                "detector": "G4",
                "verdict": "DOWNGRADE",
                "detail": {"window": [start_line, end_line], "duty": "single-line window: T2 discipline; load-bearing requires >=2-surface cross-check (E3)"},
            })
        fact = self._mint_fact(
            fact_id=fact_id,
            file=file_path,
            range_start=start_line,
            range_end=end_line,
            evidence_surface="fw_context_read_file",
            exact_query=f"read_file(file_path={file_path!r}, start_line={start_line}, end_line={end_line}, mode='active')",
            raw=raw,
            trust_class="T2" if single_line else "T0",
            certainty="measured",
            detector_checks=findings,
        )
        notes = []
        if single_line:
            notes.append("single-line window: T2; E3 cross-check required before load-bearing use")
        return GatewayResult(ok=True, fact=fact, raw=raw, detector_findings=findings, trust_class=fact["trust_class"], notes=notes)

    # ── T0 scope (b): proven direct call/reference edges ────────────────────
    def query_call_flow_direct(self, name: str, fact_id: str, *, direction: str = "callers", limit: int = 50) -> GatewayResult:
        if direction not in ("callers", "references"):
            raise ValueError("direction must be 'callers' or 'references'")
        tool = "find_callers" if direction == "callers" else "find_references"
        args = {**({"name": name, "limit": limit, "include_possible": False} if direction == "callers" else {"name": name, "limit": limit})}
        raw = self._call(tool, args)
        if raw.error:
            refusal = self.log.refuse(tool=tool, args=args, code="E-SUBSTRATE", detail=raw.error, freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal])
        rows = raw.payload if isinstance(raw.payload, list) else []
        findings = detectors.run_all_detectors(tool, args, raw.payload)
        # C2(b): a may-target row (certainty != exact) exits T0 -> T2 lower_bound
        possible_rows = [r for r in rows if isinstance(r, dict) and r.get("certainty") not in (None, "exact")]
        has_possible = bool(possible_rows)
        # C3: an {"info": ...} row is the substrate's explicit no-result /
        # explanation answer — a VALID ABSENCE, not truncation. G7 flags come
        # only from run_all_detectors (limit reached / _source_truncated).
        info_rows = [r for r in rows if isinstance(r, dict) and ("error" in r or "info" in r)]
        # absence = no substantive rows (empty list OR info/error-only rows)
        explicit_absence = not any(isinstance(r, dict) and "name" in r for r in rows)
        notes: list[str] = []
        if explicit_absence:
            notes.append(f"explicit no-result from substrate: {info_rows} (absence evidence, C3)")
        if has_possible:
            trust = "T2"
            certainty = "inferred"
            completeness = "partial_cross_checked"
            notes.append("may-target rows present: T0(b) scope exited; emitted as T2 lower_bound, never T0")
        else:
            trust = "T0"
            certainty = "measured"
            completeness = "truncated_lower_bound" if any(f["detector"] == "G7" for f in findings) else "complete"
        fact = self._mint_fact(
            fact_id=fact_id,
            file=name,
            range_start=0,
            range_end=0,
            evidence_surface="fw_context_find_callers" if direction == "callers" else "fw_context_find_callers",
            exact_query=f"{tool}(name={name!r}, include_possible=False)" if direction == "callers" else f"{tool}(name={name!r})",
            raw=raw,
            trust_class=trust,
            certainty=certainty,
            polarity="negative" if explicit_absence else "positive",
            detector_checks=findings,
            completeness_status=completeness,
        )
        return GatewayResult(ok=True, fact=fact, raw=raw, detector_findings=findings, trust_class=trust, notes=notes)

    # ── T0 scope (c): exact-name symbol existence + definition rows ─────────
    def query_exact_symbol(self, name: str, fact_id: str) -> GatewayResult:
        raw = self._call("lookup_symbol", {"name": name, "exact": True})
        rows = raw.payload if isinstance(raw.payload, list) else []
        findings = detectors.run_all_detectors("lookup_symbol", {"name": name, "exact": True}, raw.payload)
        if raw.error:
            refusal = self.log.refuse(tool="lookup_symbol", args={"name": name, "exact": True}, code="E-SUBSTRATE", detail=raw.error, freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal])
        # C3: fallback/suggestion provenance -> reject as evidence; explicit
        # absence with NON-EVIDENCE near-miss list instead.
        if any(f["detector"] == "G6" for f in findings):
            refusal = self.log.refuse(
                tool="lookup_symbol", args={"name": name, "exact": True}, code="E-TIER-FALLBACK",
                detail="fallback/suggestion rows present — rejected as evidence (G6/C3)",
                freeze_id=self.pin.freeze_id, detector_findings=findings,
            )
            near_miss = sorted({r.get("name") for r in rows if isinstance(r, dict) and r.get("name")})
            fact = self._mint_fact(
                fact_id=fact_id,
                file=name,
                range_start=0,
                range_end=0,
                evidence_surface="fw_context_lookup_symbol",
                exact_query=f"lookup_symbol(name={name!r}, exact=True)",
                raw=raw,
                trust_class="T1",
                certainty="measured",
                polarity="negative",
                completeness_status="complete",
                detector_checks=findings,
            )
            notes = [f"ABSENCE: no trusted exact match for {name!r}; near-miss list NON-EVIDENCE: {near_miss}"]
            return GatewayResult(ok=True, fact=fact, raw=raw, refusals=[refusal], detector_findings=findings, trust_class="T1", notes=notes)
        # exact rows only; multi-line definition rows are T0(c), single-line T2 (G4)
        exact_defs = [r for r in rows if isinstance(r, dict) and r.get("is_definition") and r.get("name") == name]
        single_line_defs = [r for r in exact_defs if r.get("end_line") == r.get("line")]
        findings.extend(detectors.detect_g4_fragment("lookup_symbol", {}, rows))
        trust = "T2" if single_line_defs and not [r for r in exact_defs if r.get("end_line") != r.get("line")] else "T0"
        fact = self._mint_fact(
            fact_id=fact_id,
            file=exact_defs[0]["file"] if exact_defs else name,
            range_start=exact_defs[0].get("line", 0) if exact_defs else 0,
            range_end=exact_defs[0].get("end_line", 0) if exact_defs else 0,
            evidence_surface="fw_context_lookup_symbol",
            exact_query=f"lookup_symbol(name={name!r}, exact=True)",
            raw=raw,
            trust_class=trust,
            polarity="positive" if exact_defs else "negative",
            detector_checks=findings,
            completeness_status="complete",
        )
        if not exact_defs:
            near_miss = sorted({r.get("name") for r in rows if isinstance(r, dict) and r.get("name")})
            fact = dict(fact)
            return GatewayResult(ok=True, fact=fact, raw=raw, detector_findings=findings, trust_class=trust,
                                 notes=[f"ABSENCE: no exact definition row for {name!r}; near-miss list NON-EVIDENCE: {near_miss}"])
        return GatewayResult(ok=True, fact=fact, raw=raw, detector_findings=findings, trust_class=trust)

    # ── T1 scope: deterministic fail-closed enumeration (prefix search) ────
    def query_search_family(self, prefix: str, fact_id: str, *, limit: int = 20) -> GatewayResult:
        """T1 enumeration of a symbol family by prefix. Truncation discipline
        applies: result count >= limit => G7 => truncated_lower_bound; any
        fallback/suggestion provenance => refusal (G6); NEVER a complete-
        enumeration claim (HARD RULES)."""
        raw = self._call("lookup_symbol", {"name": prefix, "exact": False, "limit": limit})
        if raw.error:
            refusal = self.log.refuse(tool="lookup_symbol", args={"name": prefix, "exact": False, "limit": limit}, code="E-SUBSTRATE", detail=raw.error, freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal])
        rows = raw.payload if isinstance(raw.payload, list) else []
        findings = detectors.run_all_detectors("lookup_symbol", {"name": prefix, "exact": False, "limit": limit}, raw.payload)
        if any(f["detector"] == "G6" for f in findings):
            refusal = self.log.refuse(
                tool="lookup_symbol", args={"name": prefix, "exact": False, "limit": limit}, code="E-TIER-FALLBACK",
                detail="fallback/suggestion rows present — rejected as evidence (G6/C3)",
                freeze_id=self.pin.freeze_id, detector_findings=findings,
            )
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal], detector_findings=findings)
        truncated = any(f["detector"] == "G7" for f in findings)
        completeness = "truncated_lower_bound" if truncated else "complete"
        fact = self._mint_fact(
            fact_id=fact_id,
            file=prefix,
            range_start=0,
            range_end=0,
            evidence_surface="fw_context_lookup_symbol",
            exact_query=f"lookup_symbol(name={prefix!r}, exact=False, limit={limit})",
            raw=raw,
            trust_class="T1",
            polarity="positive" if rows else "negative",
            detector_checks=findings,
            completeness_status=completeness,
        )
        notes = []
        if truncated:
            notes.append(f"G7: {len(rows)} rows at limit={limit} — family enumeration is a LOWER BOUND; no complete-enumeration claim")
        return GatewayResult(ok=True, fact=fact, raw=raw, detector_findings=findings, trust_class="T1", notes=notes)

    # ── T0 scope (d): explain_condition CONDITION TEXT ONLY ────────────────
    def query_condition_context(self, file_path: str, line: int, fact_id: str, *, intent: str = "condition_text") -> GatewayResult:
        raw = self._call("explain_condition", {"file_path": file_path, "line": line})
        if raw.error:
            refusal = self.log.refuse(tool="explain_condition", args={"file_path": file_path, "line": line}, code="E-SUBSTRATE", detail=raw.error, freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=raw, refusals=[refusal])
        findings = detectors.run_all_detectors("explain_condition", {"file_path": file_path, "line": line}, raw.payload, intent=intent)
        chain = (raw.payload or {}).get("chain", []) if isinstance(raw.payload, dict) else []
        macro_inputs = (raw.payload or {}).get("macro_inputs", []) if isinstance(raw.payload, dict) else []
        trust = "T0"
        certainty = "measured"
        if intent.lower() in ("macro_value", "branch_state", "expansion", "value"):
            trust = "T2"
            certainty = "degraded"
        fact = self._mint_fact(
            fact_id=fact_id,
            file=file_path,
            range_start=line,
            range_end=line,
            evidence_surface="fw_context_explain_condition",
            exact_query=f"explain_condition(file_path={file_path!r}, line={line})",
            raw=raw,
            trust_class=trust,
            certainty=certainty,
            detector_checks=findings,
            completeness_status="complete",
        )
        notes = []
        if trust == "T2":
            notes.append("G8: macro-value/branch-state intent — T2 degraded, never load-bearing (E6)")
        notes.append(f"condition text pinned to run context {fact['context_identity']}; macro_inputs={len(macro_inputs)}; chain_depth={len(chain)}")
        return GatewayResult(ok=True, fact=fact, raw=raw, detector_findings=findings, trust_class=trust, notes=notes)

    # ── independent channels ────────────────────────────────────────────────
    def submit_independent_grep(self, pattern: str, root: Path, fact_id: str, *, glob: str | None = None, regex: bool = False) -> GatewayResult:
        result = channels.grep(pattern, root, glob=glob, regex=regex)
        raw_text = f"pattern={pattern!r} glob={glob!r} truncated={result['truncated']}\n" + "\n".join(f"{m['file']}:{m['line']}: {m['text']}" for m in result["matches"])
        completeness = "truncated_lower_bound" if result["truncated"] else "complete"
        fact = self._mint_independent_fact(
            fact_id=fact_id,
            file=str(root),
            range_start=0,
            range_end=0,
            evidence_surface="independent_grep",
            exact_query=f"grep(pattern={pattern!r}, root={Path(root).as_posix()}, glob={glob!r}, regex={regex})",
            raw_text=raw_text,
            instrument="python re raw-disk grep (independent of substrate)",
            polarity="positive" if result["matches"] else "negative",
            completeness_status=completeness,
        )
        return GatewayResult(ok=True, fact=fact, raw=None, trust_class="T1",
                             notes=["independent channel (FR-8): raw disk, substrate-uninvolved"])

    def submit_independent_clang_e(self, source: Path, include_dirs: list[Path], defines: list[str], fact_id: str, *, markers: dict[str, tuple[str, str]] | None = None) -> GatewayResult:
        try:
            result = channels.clang_e(source, include_dirs, defines, workdir=source.parent)
        except channels.ChannelError as exc:
            refusal = self.log.refuse(tool="clang_e", args={"source": str(source)}, code="E-CHANNEL-UNAVAILABLE", detail=str(exc), freeze_id=self.pin.freeze_id)
            return GatewayResult(ok=False, fact=None, raw=None, refusals=[refusal])
        raw_text = f"$ {' '.join(result['cmd'])}\nrc={result['returncode']}\n{result['stdout']}"
        completeness = "complete" if result["returncode"] == 0 else "truncated_lower_bound"
        fact = self._mint_independent_fact(
            fact_id=fact_id,
            file=source.as_posix(),
            range_start=0,
            range_end=0,
            evidence_surface="independent_clang_e",
            exact_query=" ".join(result["cmd"]),
            raw_text=raw_text,
            instrument="native clang -E driver (LLVM; WSL unavailable in this environment — recorded substitution)",
            polarity="positive",
            completeness_status=completeness,
        )
        notes = ["independent channel (FR-8): preprocessor view"]
        if markers:
            regions = channels.pp_active_branches(result, markers)
            notes.append(f"active-region evaluation: {regions['regions']}")
        return GatewayResult(ok=True, fact=fact, raw=None, trust_class="T1", notes=notes)

    def submit_build_manifest_diff(self, cc_a: Path, cc_b: Path, fact_id: str) -> GatewayResult:
        result = channels.build_manifest_diff(cc_a, cc_b)
        import json as _json

        raw_text = _json.dumps(result, indent=2, sort_keys=True)
        fact = self._mint_independent_fact(
            fact_id=fact_id,
            file=f"{Path(cc_a).name}..{Path(cc_b).name}",
            range_start=0,
            range_end=0,
            evidence_surface="build_manifest_diff",
            exact_query=f"build_manifest_diff({Path(cc_a).as_posix()}, {Path(cc_b).as_posix()})",
            raw_text=raw_text,
            instrument="out-of-band compile_commands diff (G1 instrument)",
            polarity="negative" if result["identical"] else "positive",
        )
        return GatewayResult(ok=True, fact=fact, raw=None, trust_class="T1")


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
