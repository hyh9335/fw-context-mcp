"""K2 hypothesis-and-rule loop (K2_INTERFACE_SPEC.md).

Deterministic, NO LLM in the solving loop (oracle isolation is mechanical).
Consumes ONLY K1-minted fact_ids; never touches the substrate itself and
never sees oracles. Family library = versioned JSON data (hashed into the
freeze). Output is EXACTLY ONE of: candidate RuleCard / rejected hypothesis /
insufficient-evidence abstention (first-class).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..k1 import GatewayResult, K1Gateway
from ..schemas.models import (
    new_mechanism_hypothesis,
    new_protocol_step,
    new_refutation_protocol,
    new_rulecard,
)
from .evidence_gate import run_evidence_gate
from .statement_render import render_statement

FAMILY_LIB_PATH = Path(__file__).parent / "families.json"
MAX_ITERATIONS = 3  # fixed constant (§5)


def load_family_library() -> dict:
    return json.loads(FAMILY_LIB_PATH.read_text(encoding="utf-8"))


@dataclass
class K2Outcome:
    kind: str  # rulecard | rejected | insufficient_evidence
    hypothesis: dict
    rulecard: dict | None = None
    missing_evidence: list = field(default_factory=list)
    gate_decisions: list = field(default_factory=list)


class K2Loop:
    def __init__(self, gateway: K1Gateway, family_lib: dict | None = None):
        self.gw = gateway
        self.lib = family_lib or load_family_library()
        self._fact_rows: list[dict] = []
        self._counter = 0

    # ── typed request execution (1:1 onto K1 surface calls) ────────────────
    def _next_fact_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}"

    def _execute_call(self, call: dict, params: dict) -> Any:
        name = call["call"]
        p = {k: self._subst(v, params) for k, v in call.get("params", {}).items()}
        fid = self._next_fact_id("F")
        if name == "query_conditional_skeleton":
            res = self.gw.query_conditional_skeleton(p["file_path"], fid)
        elif name == "query_condition_context":
            res = self.gw.query_condition_context(p["file_path"], int(p["line"]), fid, intent=p.get("intent", "condition_text"))
        elif name == "query_definition_window":
            res = self.gw.query_definition_window(p["file_path"], int(p["start_line"]), int(p["end_line"]), fid)
        elif name == "query_exact_symbol":
            res = self.gw.query_exact_symbol(p["name"], fid)
            # K1 soundness precondition for ABSENCE: an empty exact lookup is
            # only absence evidence if the index covers the queried file at
            # all. If the target names a file, prove coverage with a read of
            # that file; a broken/unavailable index view must NEVER mint an
            # absence fact (fail closed -> template refused).
            if (
                res.ok
                and res.fact is not None
                and res.fact.get("polarity") == "negative"
                and params.get("file")
            ):
                cov = self.gw.query_definition_window(params["file"], 0, 2, self._next_fact_id("F-COV"))
                payload_err = None
                if isinstance(cov.raw.payload, dict):
                    payload_err = cov.raw.payload.get("error")
                if not cov.ok or payload_err:
                    refusal = self.gw.log.refuse(
                        tool="read_file",
                        args={"file_path": params["file"], "start_line": 0, "end_line": 2},
                        code="E-COVERAGE",
                        detail=(
                            f"index coverage unproven for {params['file']!r} "
                            f"(payload error: {payload_err or cov.raw.error}); "
                            "empty exact-lookup is NOT absence evidence — fail closed"
                        ),
                        freeze_id=self.gw.pin.freeze_id,
                    )
                    res = GatewayResult(ok=False, fact=None, raw=cov.raw, refusals=[refusal])
        elif name == "query_call_flow_direct":
            res = self.gw.query_call_flow_direct(p["name"], fid, direction=p.get("direction", "callers"))
        elif name == "submit_independent_grep":
            res = self.gw.submit_independent_grep(p["pattern"], Path(p["root"]), fid)
        else:
            raise ValueError(f"unknown K1 request {name!r}")
        if res.fact:
            self._fact_rows.append(res.fact)
        return res

    @staticmethod
    def _subst(value: Any, params: dict) -> Any:
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            return params.get(value[1:-1], value)
        return value

    # ── the loop ────────────────────────────────────────────────────────────
    def run(self, query_bundle: dict, target: dict) -> K2Outcome:
        """One hypothesis attempt for one (family, target). target keys:
        file, line, start_line, end_line, symbol, condition_text_head,
        symbol_definition_pattern, root, single_line, value_decisive."""
        family = self.lib["families"][query_bundle["family"]]
        # The frozen MECHANISM_HYPOTHESIS_SCHEMA demands non-empty
        # mechanism_steps/required_facts, so the loop gathers facts FIRST and
        # constructs the schema object only once at least one step exists.
        missing_evidence: list[dict] = []
        tried_templates: set[str] = set()
        refused_templates: set[str] = set()
        steps: list[dict] = []
        required_facts: list[dict] = []

        for _iteration in range(MAX_ITERATIONS):
            for tmpl in family["query_templates"]:
                if tmpl["template_id"] in tried_templates and tmpl["template_id"] not in refused_templates:
                    continue
                tried_templates.add(tmpl["template_id"])
                refused = False
                for call in tmpl["k1_calls"]:
                    res = self._execute_call(call, target)
                    if not res.ok:
                        refused = True
                        refused_templates.add(tmpl["template_id"])
                        missing_evidence.append({
                            "what": f"{call['call']} on {json.dumps(call.get('params', {}))[:80]}",
                            "why_needed": f"required by template {tmpl['template_id']} for family {family['family_id']}",
                            "channel": (family["missing_evidence_prompts"].get(self._kind_of(call)) or "runtime_unavailable"),
                        })
                        break  # one refusal stops this template this round
                if not refused:
                    self._attach_steps(steps, required_facts, tmpl, target)
            kinds_present = {s["step_kind"] for s in steps}
            gaps = [k for k in family["required_fact_kinds"] if k not in kinds_present]
            if not gaps:
                break

        if not steps:
            # Abstention with NO evidence at all: the frozen schema cannot hold
            # an empty hypothesis, so the outcome carries missing_evidence only.
            return K2Outcome(
                kind="insufficient_evidence",
                hypothesis=None,
                missing_evidence=missing_evidence or [{"what": "no evidence gathered", "why_needed": "family required kinds unsatisfied", "channel": "runtime_unavailable"}],
            )

        hypothesis = new_mechanism_hypothesis(
            # ids are restricted to ^H-[A-Z0-9-]+$ — fold anything else out
            hypothesis_id="H-" + re.sub(r"[^A-Z0-9-]", "-", f"{query_bundle['corpus_case']}-{str(target.get('tag', 'T')).upper()}"),
            fault_class=query_bundle["family"],
            # frozen step shape is CLOSED: {step_id, description,
            # required_fact_ids, step_kind} — discipline fields stay on the
            # rich in-memory form, gate-side only.
            mechanism_steps=[
                {k: s[k] for k in ("step_id", "description", "required_fact_ids", "step_kind")}
                for s in steps
            ],
            required_facts=required_facts,
            assumptions=target.get("assumptions", []),
            missing_evidence=missing_evidence,
            forbidden_overclaims=["no complete-enumeration claims", "T2 rows never load-bearing"],
            scope={"goal_item_ref": query_bundle.get("goal_item_ref", ""), "corpus_ref": query_bundle["corpus_case"], "lineage": query_bundle.get("lineage", "")},
            status="candidate",
        )
        # evidence gate runs on the RICH steps (discipline fields present)
        gate = run_evidence_gate(
            steps,
            self._fact_rows,
            pinned_build_snapshot_id=self.gw.pin.build_snapshot_id,
            hypothesis_text=json.dumps({"target": target, "family": family["family_id"]}, ensure_ascii=False),
        )
        if gate.refused:
            hypothesis["status"] = "rejected" if any(
                d.rule in ("E2", "E6") for d in gate.refusals()
            ) else hypothesis["status"]
            return K2Outcome(
                kind="rejected" if hypothesis["status"] == "rejected" else "insufficient_evidence",
                hypothesis=hypothesis,
                missing_evidence=missing_evidence or [{"what": "gate refusal", "why_needed": "; ".join(f"{d.rule}: {d.detail}" for d in gate.refusals()), "channel": "none"}],
                gate_decisions=[d.__dict__ for d in gate.decisions],
            )
        kinds_present = {s["step_kind"] for s in steps}
        if not all(k in kinds_present for k in family["required_fact_kinds"]):
            hypothesis["status"] = "insufficient_evidence"
            return K2Outcome(kind="insufficient_evidence", hypothesis=hypothesis, missing_evidence=missing_evidence, gate_decisions=[d.__dict__ for d in gate.decisions])
        # promote
        hypothesis["status"] = "promoted_to_rule"
        rulecard = self._generate_rulecard(hypothesis, query_bundle, target, rich_steps=steps)
        return K2Outcome(kind="rulecard", hypothesis=hypothesis, rulecard=rulecard, gate_decisions=[d.__dict__ for d in gate.decisions])

    @staticmethod
    def _kind_of(call: dict) -> str:
        m = {"query_condition_context": "condition", "query_conditional_skeleton": "condition",
             "query_definition_window": "definition", "query_exact_symbol": "definition",
             "query_call_flow_direct": "call_flow", "submit_independent_grep": "absence"}
        return m.get(call["call"], "definition")

    def _attach_steps(self, steps: list, required_facts: list, tmpl: dict, target: dict) -> None:
        """Attach mechanism steps backed by the facts this template minted."""
        existing_kinds = {s["step_kind"] for s in steps}
        for kind in tmpl["expected_fact_kinds"]:
            if kind in existing_kinds:
                continue
            rows = self._rows_for_kind(kind)
            if not rows:
                continue
            step_id = f"S{len(steps) + 1}"
            step = {
                "step_id": step_id,
                "description": target.get(f"description_{kind}") or f"{kind} established via {tmpl['template_id']}",
                "required_fact_ids": [r["fact_id"] for r in rows],
                "step_kind": kind,
                "polarity": rows[0].get("polarity", "positive"),
                "single_line": bool(target.get("single_line")) and kind == "definition",
                "load_bearing": True,
                "enumerated_attempts": [r["exact_query"] for r in rows] if kind == "absence" or rows[0].get("polarity") == "negative" else [],
            }
            steps.append(step)
            required_facts.extend({"fact_id": r["fact_id"], "trust_class": r["trust_class"]} for r in rows)

    def _rows_for_kind(self, kind: str) -> list[dict]:
        m = {
            "condition": ((), ()),
            "definition": ((), ("positive",)),
            "call_flow": ((), ()),
            "absence": ((), ("negative",)),
            "config": ((), ()),
        }
        surfaces = {
            "condition": ("fw_context_explain_condition", "fw_context_list_conditional_regions"),
            "definition": ("fw_context_read_file", "fw_context_lookup_symbol"),
            "call_flow": ("fw_context_find_callers",),
            "absence": ("fw_context_lookup_symbol", "independent_grep"),
            "config": ("build_manifest_diff", "independent_grep"),
        }.get(kind, ())
        need_polarity = m[kind][1]
        rows = [r for r in self._fact_rows if r.get("evidence_surface") in surfaces]
        if need_polarity:
            rows = [r for r in rows if r.get("polarity") in need_polarity]
        return rows

    # ── RuleCard generation (§6) ───────────────────────────────────────────
    def _generate_rulecard(self, hypothesis: dict, query_bundle: dict, target: dict, *, rich_steps: list | None = None) -> dict:
        family = self.lib["families"][query_bundle["family"]]
        rich_steps = rich_steps if rich_steps is not None else hypothesis["mechanism_steps"]
        predicate_kind = {
            "condition_config_gated": "condition_text",
            "init_submit_pairing": "call_reachable",
            "existence_absence": "definition_absent",
            "direct_flow_presence": "call_reachable",
        }[query_bundle["family"]]
        polarity = self._card_polarity(query_bundle["family"], rich_steps)
        # PMR-3 repair: the STRUCTURED predicate fields are authoritative and
        # the statement is RENDERED from them (statement_render). Driver prose
        # (target["predicate_statement"]) is NEVER copied into the card — that
        # path produced the C4 incoherence (definition_absent/negative card
        # whose statement asserted the definition exists).
        statement = render_statement(
            predicate_kind,
            polarity,
            subject=str(target.get("symbol") or target.get("file") or ""),
            build_id=self.gw.pin.build_snapshot_id,
            condition_text=str(target.get("condition_text") or target.get("condition_text_head") or ""),
            start_line=target.get("start_line"),
            end_line=target.get("end_line"),
            call_symbol=str(target.get("call_symbol") or target.get("symbol") or ""),
            call_line=target.get("call_line"),
            guard_symbol=str(target.get("guard_symbol") or ""),
        )
        trigger = {
            "predicate_id": f"P-{hypothesis['hypothesis_id'][2:]}-1",
            "kind": predicate_kind,
            "fact_refs": [{"fact_id": fr["fact_id"], "trust_class": fr["trust_class"]} for fr in hypothesis["required_facts"]],
            "statement": statement,
            "negatable": True,
        }
        evidence = [
            {"fact_id": fr["fact_id"], "trust_class": fr["trust_class"]}
            for fr in hypothesis["required_facts"]
        ]
        surfaces = sorted({self._row_surface(fr["fact_id"]) for fr in hypothesis["required_facts"]})
        protocol = new_refutation_protocol(
            steps=[
                new_protocol_step(
                    step_id="RS1",
                    action="query_substrate_via_k1",
                    inputs={"fact_refs": [fr["fact_id"] for fr in hypothesis["required_facts"]], "freeze_id": self.gw.pin.freeze_id},
                    rule_holds_when="every trigger fact re-derives identically under the same freeze",
                    rule_falsified_when="any trigger fact fails to re-derive or changes polarity",
                ),
                new_protocol_step(
                    step_id="RS2",
                    action="independent_crosscheck",
                    inputs={"surfaces": surfaces},
                    rule_holds_when="independent grep / clang -E region-survival agrees with the trigger",
                    rule_falsified_when="the independent surface contradicts the trigger",
                ),
                new_protocol_step(
                    step_id="RS3",
                    action="construct_counterexample_search",
                    inputs={"flip": "pinned config value; re-run region check"},
                    rule_holds_when="no counterexample configuration survives",
                    rule_falsified_when="a counterexample configuration survives and satisfies the trigger",
                ),
            ],
            oracle_requirement=target.get("oracle_requirement", "none"),
        )
        rc = new_rulecard(
            rulecard_id=f"RC-{hypothesis['hypothesis_id'][2:]}",
            mechanism_id=f"M-{hypothesis['hypothesis_id'][2:]}",
            hypothesis_ref=hypothesis["hypothesis_id"],
            fault_class=query_bundle["family"],
            # frozen scope_stamp is CLOSED: {goal_item, corpus_case, lineage,
            # schema_version}. Freeze/build pinning lives on the fact rows
            # (build_snapshot_id) and the loop's audit trail, never here.
            scope_stamp={
                "goal_item": query_bundle.get("goal_item_ref", ""),
                "corpus_case": query_bundle["corpus_case"],
                "lineage": query_bundle.get("lineage", ""),
                "schema_version": "wave1-design-gate-1",
            },
            trigger_conditions=[trigger],
            evidence=evidence,
            provenance={"derived_from": surfaces, "certainty": "measured", "k1_mediated": True},
            # polarity reflects the mechanism: absence/pairing-broken cards
            # assert a negative (dead guard / no live caller)
            polarity=polarity,
            forbidden_overclaims=[*hypothesis["forbidden_overclaims"], *family.get("runtime_overclaim_bars", [])],
            refutation_protocol=protocol,
            refutation_result={
                "state": "UNEXECUTED",
                "protocol_completed": False,
                "counterexample_ref": None,
                "missing_evidence_refs": [],
                "adjudicated_at": None,
            },
            validation_status="unvalidated",
            oracle_provenance="none",
            # wave1-1-semantic-gate-1: the three validation axes are reported
            # SEPARATELY (PMR-4). faultness starts PENDING — the round-level
            # Faultness Gate adjudicates it before promotion is final.
            validation={
                "evidence_integrity_status": "PASS",
                "faultness_status": "PENDING",
                "refutation_state": "UNEXECUTED",
            },
        )
        return rc

    @staticmethod
    def _card_polarity(family: str, steps: list) -> str:
        if family in ("existence_absence", "init_submit_pairing") and any(
            s.get("polarity") == "negative" for s in steps
        ):
            return "negative"
        return "positive"

    def _row_surface(self, fact_id: str) -> str:
        for r in self._fact_rows:
            if r["fact_id"] == fact_id:
                return r.get("evidence_surface", "")
        return ""
