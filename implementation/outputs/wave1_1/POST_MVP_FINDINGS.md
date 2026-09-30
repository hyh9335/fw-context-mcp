# POST-MVP FINDINGS — basis for Wave-1.1 semantic correctness repair

Status transition recorded: `WAVE1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS` → **`SUPERSEDED_PENDING_REVALIDATION`**;
working state **`WAVE1_MVP_NOT_READY → SEMANTIC_CORRECTNESS_REPAIR_REQUIRED`**.

Evidence base: the external review package extracted read-only into `post_mvp_extract/` (byte-identical copies; see
`post_mvp_extract/MANIFEST.json`). Historical Wave-1 artifacts under `implementation/outputs/wave1/` and
`implementation/rounds/` are preserved unchanged as the motivating evidence.

---

## PMR-1 — E2E acceptance counting bug (VERIFIED)

`implementation/run_i9_acceptance.py` lines 56–64 compute `e2e_rounds` from `meta["kind"] == "e2e"` but never use it;
gate `A1_case_rounds` counts every round that has a `FREEZE_*.json`. `C5-tripwire` (registry `kind: "tripwire"`) was
therefore counted toward `>=4 E2E case-rounds` although its recorded terminal state is
`loop_outcome_kind: "insufficient_evidence"`, `promoted: false`, zero RuleCards, zero refutation executions
(`implementation/rounds/I8_C5_T2_TRIPWIRE/I8_RECEIPT.json`; extracted copy `post_mvp_extract/B_C5/`).

This invalidated gate A1: a freeze manifest is not an execution receipt. A detector/EvidenceGate-only tripwire is a
safety integration result, not a full fault-discovery E2E traversal.

## PMR-2 — C6 negative-control false-positive semantics (VERIFIED)

C6 is the clean/post-fix NuttX twin. The post-fix local conditional at
`fixture/libs/libc/unistd/lib_getcwd.c:107–110` (`#ifndef CONFIG_DISABLE_ENVIRON` getenv-default block) is intentional
post-fix code. Wave-1 nevertheless promoted `RC-I7-B-LIB-GETCWD-107` (`fault_class: condition_config_gated`,
`polarity: positive`, statement "region … 107-110 … is compiled OUT") with scorer PASS and
`NOT_REFUTED_WITHIN_SCOPE` (`post_mvp_extract/A_C6_RULECARD.json` + `A_C6_SUPPORTING/REFUTATIONS.json`).

Root cause: the old A10 negative-control gate checked only `absence_cards == 0` (family-locked to
`existence_absence`), so a false-positive fault card in another family passed. A true conditional source fact is not
automatically a fault mechanism. Repair: for a negative control, **ZERO accepted fault RuleCards**; family-agnostic
evaluation over all accepted fault cards; the benign conditional may be retained as evidence only.

## PMR-3 — RuleCard semantic-coherence bug (VERIFIED)

`RC-I7-A-API-GETCWD` (`post_mvp_extract/D_C4_RULECARD.json`) carries a structured predicate
`kind: "definition_absent"`, `polarity: negative`, while its trigger `statement` renders "the public API getcwd has a
compiled definition under the pinned build" — an internal contradiction between the authoritative structured fields and
the human-readable rendering. The Wave-1 validator checked schema shape only, so a schema-valid object was accepted
despite semantic incoherence. Repair: machine-enforced coherence layer (structured predicate authoritative; statement
deterministically rendered from it; contradiction blocks promotion); regression tests reproducing this exact defect.

## PMR-4 — Evidence truth conflated with fault truth (VERIFIED)

RS1/RS2 (fact re-derivation, independent-surface agreement) validate **evidence integrity**, not **faultness**. C6
demonstrates the failure mode: all evidence is true and re-derivable, yet no fault exists. Repair: an explicit,
deterministic, family-scoped **Faultness/Consequence Gate** between hypothesis construction and fault RuleCard
promotion, requiring composed evidence of harmful consequence / violated invariant / broken required relationship —
not merely pattern presence. Refutation semantics are split accordingly: final validation artifacts must report
`evidence_integrity_status`, `faultness_status`, `refutation_state` separately, and `NOT_REFUTED_WITHIN_SCOPE` must
never be read as "fault confirmed".

## Secondary defects found during extraction (recorded, in scope of §7/§8)

- Top-level `REFUTATION_RESULTS.json` aggregated only per-round `REFUTATIONS.json` files (3 entries) while C1's four
  refutation records live in `rounds/I6_C1/E2E_RECEIPT.json` — normalization required
  (`post_mvp_extract/REFUTATION_AGGREGATION_EXPLANATION.md`).
- Verifier weaknesses: vacuous-pass paths (missing-file `continue`), `" passed"` string matching, hardcoded lineage
  constants, negative control scoped to one family — hardened in Wave-1.1 with regression tests.

## Repair obligations (from the review directive)

1. Receipt-based E2E counting; C5 not relabeled, not force-counted.
2. C6 zero accepted fault cards; family-agnostic negative control; no case-specific hardcoding in K2/K3.
3. Semantic-coherence validator + deterministic statement rendering; C4 card coherent.
4. Deterministic Faultness Gate; evidence validation separated from fault-mechanism validation.
5. C5 remains the T2 safety integration scenario; exactly ONE new bounded planted E2E case added if E2E count < 4.
6. Normalized top-level refutation aggregate (every promoted card exactly once, with integrity/faultness state).
7. Hardened K3 acceptance/audit (fail-closed, exact counts, metadata-derived lineage, receipt-based E2E).
8. Substrate untouched; no Track-B PRs; new freeze hashes for all re-evaluated cases; historical artifacts immutable.
