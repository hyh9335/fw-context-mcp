"""PMR-3 repair: deterministic statement rendering + semantic-coherence
validation for RuleCards.

The STRUCTURED predicate fields (kind, polarity, subject, build pin) are
authoritative. The human-readable ``statement`` is RENDERED from them by
``render_statement``; ``validate_semantic_coherence`` re-checks the rendered
card machine-enforcedly (no LLM, no human interpretation). A contradiction
between structured fields and the rendered statement blocks promotion.

This module exists because Wave-1 accepted RC-I7-A-API-GETCWD with
``kind="definition_absent"`` / ``polarity="negative"`` while its statement
asserted the definition EXISTS (post_mvp_extract/D_C4_RULECARD.json).
"""
from __future__ import annotations

import re

CONFIG_SYMBOL_RE = re.compile(r"\bCONFIG_[A-Z0-9_]{2,}\b")

# (predicate_kind, card_polarity) combinations that are coherent at all.
COHERENT_KIND_POLARITY = {
    ("definition_absent", "negative"),
    ("definition_present", "positive"),
    ("condition_text", "positive"),   # asserts the dead-guard pattern exists
    ("call_reachable", "negative"),   # pairing broken: no live caller
    ("call_reachable", "positive"),   # flow present
}

# which fault_class may carry which predicate kind
FAMILY_KIND_MAP = {
    "condition_config_gated": {"condition_text"},
    "init_submit_pairing": {"call_reachable"},
    "existence_absence": {"definition_absent", "definition_present"},
    "direct_flow_presence": {"call_reachable"},
}

_CUE_POS = "has a compiled definition"
_CUE_NEG = "has no compiled definition"


def render_statement(
    predicate_kind: str,
    polarity: str,
    subject: str,
    *,
    build_id: str = "",
    condition_text: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
    call_symbol: str = "",
    call_line: int | None = None,
    guard_symbol: str = "",
) -> str:
    """Deterministically render a trigger statement from STRUCTURED fields.

    Raises ValueError on a (kind, polarity) pair that cannot be rendered
    coherently — the caller must not promote such a predicate.
    """
    if (predicate_kind, polarity) not in COHERENT_KIND_POLARITY:
        raise ValueError(
            f"incoherent predicate: kind={predicate_kind!r} with polarity={polarity!r}"
        )
    pin = f" under the pinned build ({build_id})" if build_id else " under the pinned build"
    if predicate_kind == "definition_absent":
        return f"the symbol {subject} {_CUE_NEG}{pin}"
    if predicate_kind == "definition_present":
        return f"the symbol {subject} {_CUE_POS}{pin}"
    if predicate_kind == "condition_text":
        return (
            f"region {subject}:{start_line}-{end_line} guarded by '{condition_text}' is compiled "
            f"OUT{pin}"
        )
    # call_reachable
    if polarity == "negative":
        return (
            f"the {call_symbol} call at {subject}:{call_line} is guarded by dead config symbol "
            f"{guard_symbol}; under the pinned build it has no live caller "
            "(init/submit pairing broken at source level)"
        )
    return f"the {call_symbol} call at {subject}:{call_line} is reachable from a live caller{pin}"


def _statement_cue_errors(predicate_kind: str, polarity: str, statement: str) -> list[str]:
    s = str(statement)
    cue_pos = _CUE_POS in s
    cue_neg = _CUE_NEG in s
    errs: list[str] = []
    if predicate_kind in ("definition_absent", "definition_present"):
        if polarity == "negative":
            if cue_pos or not cue_neg:
                errs.append(
                    "statement rendering contradicts structured predicate: kind=definition_absent/"
                    "polarity=negative requires the absence cue "
                    f"({_CUE_NEG!r}); found presence-cue={cue_pos}, absence-cue={cue_neg}"
                )
        else:
            if cue_neg or not cue_pos:
                errs.append(
                    "statement rendering contradicts structured predicate: definition_present/"
                    f"positive requires the presence cue ({_CUE_POS!r})"
                )
    if predicate_kind == "condition_text" and polarity == "positive":
        if "compiled OUT" not in s:
            errs.append(
                "statement rendering contradicts structured predicate: condition_text/positive "
                "dead-guard assertion must state the guarded region is 'compiled OUT'"
            )
    if predicate_kind == "call_reachable" and polarity == "negative":
        if "no live caller" not in s:
            errs.append(
                "statement rendering contradicts structured predicate: call_reachable/negative "
                "must state 'no live caller'"
            )
        if cue_pos:
            errs.append(
                "statement rendering contradicts structured predicate: negative pairing claim "
                f"must not contain the presence cue ({_CUE_POS!r})"
            )
    return errs


def validate_semantic_coherence(
    card: dict,
    *,
    fact_rows: list[dict] | None = None,
) -> list[str]:
    """Machine-enforced semantic-coherence check. Returns a list of violation
    strings; an EMPTY list means coherent. Structured fields are authoritative.

    Checks:
      1. (predicate kind, card polarity) coherence;
      2. fault_class <-> predicate-kind compatibility;
      3. statement rendering consistency with the structured predicate;
      4. evidence-polarity consistency (a negative card must cite at least one
         negative fact and vice versa) — only when fact_rows are supplied.
    """
    errs: list[str] = []
    rid = card.get("rulecard_id", "<card>")
    family = card.get("fault_class", "")
    polarity = card.get("polarity", "")
    for trig in card.get("trigger_conditions", []) or []:
        kind = trig.get("kind", "")
        stmt = trig.get("statement", "")
        pid = trig.get("predicate_id", "<predicate>")
        if (kind, polarity) not in COHERENT_KIND_POLARITY:
            errs.append(
                f"{rid}/{pid}: incoherent predicate kind={kind!r} with card polarity={polarity!r}"
            )
        allowed = FAMILY_KIND_MAP.get(family)
        if allowed is not None and kind not in allowed:
            errs.append(
                f"{rid}/{pid}: predicate kind={kind!r} is not expressible by fault_class={family!r}"
            )
        errs.extend(f"{rid}/{pid}: {e}" for e in _statement_cue_errors(kind, polarity, stmt))
    if fact_rows is not None:
        by_id = {r.get("fact_id"): r for r in fact_rows}
        cited_polarities = set()
        for ev in card.get("evidence", []) or []:
            fid = ev.get("fact_id") if isinstance(ev, dict) else ev
            row = by_id.get(fid)
            if row is not None:
                cited_polarities.add(row.get("polarity"))
        if polarity == "negative" and "negative" not in cited_polarities:
            errs.append(
                f"{rid}: card polarity=negative but no cited fact has negative polarity "
                f"(cited: {sorted(p for p in cited_polarities if p)})"
            )
        if polarity == "positive" and "positive" not in cited_polarities:
            errs.append(
                f"{rid}: card polarity=positive but no cited fact has positive polarity "
                f"(cited: {sorted(p for p in cited_polarities if p)})"
            )
    return errs


def is_semantically_coherent(card: dict, *, fact_rows: list[dict] | None = None) -> bool:
    return not validate_semantic_coherence(card, fact_rows=fact_rows)
