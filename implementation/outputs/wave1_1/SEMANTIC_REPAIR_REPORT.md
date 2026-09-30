# SEMANTIC_REPAIR_REPORT.md — Wave-1.1 semantic-correctness repair + revalidation

Phase: `WAVE1_MVP_NOT_READY → SEMANTIC_CORRECTNESS_REPAIR_REQUIRED` → repair
executed → revalidation. Scope: PMR-1..PMR-4 + the 13 numbered work items of
the post-MVP findings. No Wave-2 work, no new fault families, no LLM
hypothesis generation, no substrate modification, no Track-B action, no
historical-artifact rewrite.

## 1. Repairs delivered (mapping to findings)

| Finding | Repair | Artifacts |
|---|---|---|
| PMR-1 E2E counting was freeze-manifest-based; C5 mislabeled E2E | Terminal-receipt criterion: every round emits `ROUND_RECEIPT.json`; `classify_round_receipt` (fail-closed) is the ONLY E2E classification basis; C5 is safety-only | `k3/terminal_receipt.py`; corpus index receipt |
| PMR-2 C6 negative control leaked a fault card (benign conditional accepted) | Round-level family-scoped Faultness Gate; benign conditional demoted as a first-class rejection with named roles | `k2/faultness_gate.py`; C4C6_B receipt |
| PMR-3 statements were driver prose (incoherent on C4: kind=definition_absent with a statement asserting presence) | Structured predicate is authoritative; deterministic statement renderer; machine-enforced coherence validator blocks promotion | `k2/statement_render.py`; coherence gate in `solve_case` |
| PMR-4 no consequence semantics ("compiled out" ⇒ fault) | Family-scoped Faultness/Consequence Gate with per-family role predicates (deterministic, no DAG, no LLM) | `FAULTNESS_GATE_SPEC.md` |
| PMR-5 evidence validation conflated with refutation | Separate axes on every card: `evidence_integrity_status` / `faultness_status` / `refutation_state` (schema v1.1, frozen contract untouched) | `schemas/RULECARD_SCHEMA_V1_1.json`; `REFUTATION_VALIDATION_STATE_SPEC.md` |
| PMR-6 aggregation missed C1; coverage not exactly-once | Normalized `REFUTATION_RESULTS_V2.json` keyed by (source_artifact_path, rulecard_id), historical records included unchanged with `historical: true` | acceptance gate G14 |

## 2. Corpus revalidation (all receipts regenerated; new freeze ids)

| Round | Class | Accepted fault cards | Rejections (first-class) |
|---|---|---|---|
| C1 (lineage A) | e2e_positive | RC-C1-ACCEL-TRIGGER-47 (region, dead typo guard), RC-C1-SYM-...-THREAAD-PAIR (pairing) | RC-C1-ACCEL-TRIGGER-33 (region compiles IN — intended tightening), RC-C1-SYM-...-THREAAD (A3: macro-absence is cause, not consequence) |
| C4C6_A (lineage B, pre-fix NuttX) | e2e_positive | RC-C4C6-A-LIB-GETCWD-38 (region, definition_cutoff via getcwd), RC-C4C6-A-API-GETCWD (absence) | — |
| C4C6_B (lineage B, post-fix twin) | e2e_negative | NONE (zero accepted fault cards) | RC-C4C6-B-LIB-GETCWD-107 demoted (R3: no in-region consequence) — benign conditional retained as evidence-grade rejection |
| C5 (lineage C) | safety_only | NONE | T2-tripwire E3 refusal first-class; same mechanism + second surface passes the evidence gate |
| C7 (lineage D, NEW planted) | e2e_positive | RC-C7-SENSOR-MODULE-19 (region), RC-C7-API-SENSOR-SUBMIT (absence), RC-C7-SYM-CONFIG-SENSOR-IRQ-PATH-PAIR (pairing) | RC-C7-SYM-CONFIG-SENSOR-IRQ-PATH (A3) |

E2E count by terminal receipts: **4** (C1, C4C6_A, C4C6_B, C7) — meets the
frozen ≥4 criterion with C1/C4/C6/C7 roles intact; C5 contributes safety
coverage only.

## 3. Acceptance result

`WAVE1_1_ACCEPTANCE_RESULTS.json`: **15/15 executable gates PASS** —
G1 e2e_rounds≥4 (receipt-based), G2 C1 correct, G3 C4 correct, G4 C6 zero
accepted, G5 T2 scenario, G6 detector plants + full suite (79 tests) green,
G7 coherence all accepted cards, G8 faultness all accepted cards, G9
separate validation axes, G10 family-agnostic negative control + zero
case-specific tokens in mechanism modules, G11 no silent unsupported claims
(blind scorer re-run), G12 no oracle leak, G13 substrate untouched (HEAD
`d8f08a41d3fb9df6de255a187720f6332ed0f1c9`, `git status --porcelain -- src`
empty), G14 normalized aggregate exactly-once, **G15 receipt-vs-disk card
reconciliation (added by the audit V5 repair: card discovery fail-closed;
claimed ids == disk ids in both directions; unparseable card files always
fatal)**.

Runner verdict: `WAVE1_1_MVP_ACCEPTED` (subject to the fresh independent
audit, which issues the binding verdict).

### Audit-driven hardening (post first audit)

The fresh independent audit (`WAVE1_1_RELEASE_AUDIT.md`) falsified exactly
one vector — V5: the acceptance runner's `_accepted_cards` returned `[]` for
a missing rulecards directory and silently skipped unparseable card files,
letting G7/G9/G11 pass vacuously, with no receipt-vs-disk reconciliation.
The prescribed repair landed in full: `_load_round_cards` (fail-closed card
discovery), `reconcile_cards` (both-direction id reconciliation), gate G15,
and the same fail-closed check folded into G2/G3/G7/G9/G11; regression tests
T8 (4 tests) pin the behavior. Suite: 79 passed. Acceptance re-run: 15/15
gates PASS. The auditor re-verified V5 and issued the binding final verdict
(see `WAVE1_1_RELEASE_AUDIT.md` addendum).

## 4. Intended semantic tightenings (documented, not regressions)

1. C1 region -33 demoted: the region guarded by the CORRECTLY-SPELLED symbol
   compiles IN under the pinned build; wave-1 had promoted it as a fault.
2. C1/C7 guard-macro EA cards demoted: a missing `CONFIG_*` macro is the
   CAUSE of a dead guard, not a harmful consequence.
3. C6 benign conditional [107,110] demoted: compiled-out region with no
   independently-proved in-round consequence. It remains recorded (rejected
   candidate + aggregate record), never silently dropped.

## 5. Non-goals honored

No generalization work; no new fault families beyond the four frozen ones
(`direct_flow_presence` remains unsatisfiable, fail-closed); no LLM in the
solving loop; historical Wave-1 artifacts byte-unchanged; substrate HEAD
pinned and `src/` clean.
