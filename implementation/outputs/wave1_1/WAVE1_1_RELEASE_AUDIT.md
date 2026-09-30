# WAVE1_1_RELEASE_AUDIT.md — Fresh Independent Verification (Attempted Falsification)

- **Auditor identity:** fresh independent verifier (not an author of any wave-1.1 artifact; adversarial audit, falsification-first)
- **Date:** 2026-09-21 (14:40 local / 06:40 UTC)
- **Mandate:** attempt falsification of the wave-1.1 semantic-correctness revalidation across 8 vectors (V1..V8) + overall coherence. Read-only on all existing artifacts (this file is the only file created). No acceptance-runner execution (all on-disk numbers were read and independently re-derived before/without any runner side effects). C/C++ corpus fixtures accessed exclusively via fw-context-produced artifacts (see Overall Coherence §C7 for the live-MCP limitation and the compliant path used).
- **Binding rules honored:** no artifact modified/deleted/rewritten; no new development; no generalization; substrate read via local git only.

**RESULT AT A GLANCE:** V1 PASS · V2 PASS · V3 PASS · V4 PASS · **V5 FAIL** · V6 PASS · V7 PASS · V8 PASS

**FINAL VERDICT: `WAVE1_1_MVP_NOT_READY`** — one vector fails (V5, a narrow and precisely repairable acceptance-runner gap). The corpus evidence itself is sound; the acceptance runner's card-level gates are not yet fail-closed against missing/corrupt rulecard inputs.

---

## V1 — C5 / E2E counting integrity

**Checked.**
1. Whether the E2E count derives ONLY from terminal execution receipts (`ROUND_RECEIPT.json` + `classify_round_receipt`), with no inflation path via freeze manifests, registry labels, or class-name strings.
2. Whether the classifier is fail-closed (missing / under-traversal / non-terminal states never count).
3. Whether C5 is excluded from E2E counting while still being exercised (T2 tripwire).

**Evidence.**
- `implementation/fault_discovery_agent/k3/terminal_receipt.py` `classify_round_receipt` (lines 76–133): positive requires `source_staged` AND `fact_records>0` AND `mechanism_hypotheses>0` AND non-empty accepted cards, each with `faultness_status=="PASS"`, `evidence_integrity_status=="PASS"`, `refutation_state in ("NOT_REFUTED_WITHIN_SCOPE","REFUTED")` (line 102–113), and zero coherence violations; negative requires an explicit gate-rejection or REFUTED terminal (lines 114–129) — an abstention without a `no_fault_terminal` marker is `not_e2e` (lines 126–133); the final fallthrough is `return "not_e2e"` (line 133). Non-dict input → `not_e2e` (line 80–82).
- **Class-name inflation path does not exist:** the classifier recomputes the class from receipt *content* and never reads `receipt["terminal_class"]`. The runner (`implementation/run_wave1_1_acceptance.py`) counts via `classify_round_receipt` on the receipt file only (G1, lines ~91–141); `FREEZE_*.json` manifests are never read for counting anywhere in the runner; `CORPUS_RECEIPTS_INDEX.json` is used only as a round-discovery list plus an explicit existence check (`if not idx: G1=False`, lines ~91–92).
- Receipts on disk: C1 `e2e_positive` (2 accepted), C4C6_A `e2e_positive` (2), C4C6_B `e2e_negative` (0 accepted, 1 gate rejection), C5_safety `safety_only` (0 accepted, E3 tripwire recorded, `quarantine_proof` all-true), C7_planted `e2e_positive` (3). G1 `count=4` = {C1, C4C6_A, C4C6_B, C7}; C5 contributes `safety_only` and is **excluded** from the E2E count while still exercised by gate G5 (`t2_scenario_exercised`, `pass=true`, from the C5 receipt's `t2_scenarios` + `quarantine_proof`).
- Fail-closed under-traversal check: C5's receipt is exactly the shape T4 guards (`abstentions` + `t2_scenarios` + no accepted/rejected → `safety_only`; without `t2_scenarios` → `not_e2e`, tests `test_t4_t2_refusal_is_safety_only_and_hypothesis_abstention_is_not`, `test_t4_hypothesis_abstention_alone_is_not_negative`).
- Cross-consistency of receipts with the evidence chain (anti-fabrication spot-check): C7 receipt `traversal.fact_records=16` = 20 rows in `fact_log.jsonl` − 2 `kind=refusal` rows (rows 7, 11) − 2 post-receipt RS2 rows (`F-RS2-0004/0005`); accepted ids match `rulecards/` dir contents and `REFUTATIONS.json` rows for all five rounds; demotions match `FAULTNESS_RESULTS.json`.
- Residual trust boundary (non-blocking): the runner trusts the receipt's `traversal` counters as produced by `build_round_receipt` and does not re-derive them from `fact_log.jsonl`; fabricating a receipt would require fabricating the whole per-round artifact chain, which I cross-checked and found consistent. No observed inflation.

**Verdict: PASS.**

## V2 — C6 negative correctness

**Checked.** Zero accepted fault cards for C4C6_B; first-class rejection of the benign conditional with named failed roles; no card files for it; gate is family-agnostic with no case-specific branching that would hard-code the C6 outcome.

**Evidence.**
- `implementation/rounds/wave1_1/C4C6_B/ROUND_RECEIPT.json`: `"accepted_fault_cards": []` (line 8), `terminal_class: "e2e_negative"` (line 28), `rejected_candidates` contains `RC-C4C6-B-LIB-GETCWD-107` with `failed_roles: ["R3_harmful_consequence"]`, `reason: "faultness_gate_failed"` (lines 15–24); `traversal.rulecards_accepted: 0`.
- `implementation/rounds/wave1_1/C4C6_B/FAULTNESS_RESULTS.json`: `evaluated: 1, passed: 0, demoted: 1`; role detail `R1_pattern_compiled_out=OK; R2_config_symbol_guard=OK; R3_harmful_consequence=FAIL` with empty R3 evidence (lines 17–39).
- **No card files:** `glob implementation/rounds/wave1_1/C4C6_B/rulecards/*` → no files (no `rulecards/` directory at all); same for `C5_safety/rulecards/*` → no files.
- **Family-agnostic gate:** `implementation/fault_discovery_agent/k2/faultness_gate.py` dispatches purely on the `family` string (`existence_absence` / `condition_config_gated` / `init_submit_pairing`; unknown family fails closed, G0). Token scan of the whole module (comments included): **zero** case tokens. The A/B divergence is evidence-driven, from the same code path: in C4C6_A the region [38,138] encloses the independently-proved-absent `getcwd` definition → `definition_cutoff` satisfies R3 (`RC-C4C6-A-LIB-GETCWD-38` roles, lines 131–134); in C4C6_B the target map (`C4C6_B/TARGETS.json`) holds only the region [107,110] and the guard symbol `CONFIG_DISABLE_ENVIRON` itself (skipped as "cause, not consequence") → R3 unsatisfiable → rejection. Regression test `test_t2_c6_like_region_candidate_fails_without_consequence` reproduces both behaviors synthetically with generic tokens (`CONFIG_X_GUARD`, `needed_api`).
- Acceptance G4 records the same: `accepted: []`, `rejected_candidates: [{rulecard_id: RC-C4C6-B-LIB-GETCWD-107, failed_roles: [R3_harmful_consequence], family: condition_config_gated, reason: faultness_gate_failed}]`, `pass: true` (`WAVE1_1_ACCEPTANCE_RESULTS.json` lines 48–67).

**Verdict: PASS.**

## V3 — C4 semantic coherence

**Checked.** Statement ↔ structured predicate match on all C4C6_A rulecards; independent reconstruction of the historical C4 defect and confirmation that `validate_semantic_coherence` rejects it.

**Evidence.**
- `implementation/rounds/wave1_1/C4C6_A/rulecards/RC-C4C6-A-API-GETCWD.json`: `kind: "definition_absent"`, `polarity: "negative"`, statement: *"the symbol getcwd has no compiled definition under the pinned build (BUILD-C4C6-DISABLE-ENVIRON)"* — absence card says absence. ✓
- `.../rulecards/RC-C4C6-A-LIB-GETCWD-38.json`: `kind: "condition_text"`, `polarity: "positive"`, statement asserts the region guarded by `'CONFIG_DISABLE_ENVIRON'` *"is compiled OUT"* — region card says compiled OUT. ✓
- Historical defect reconstruction (independent Python run, importing `fault_discovery_agent.k2.statement_render.validate_semantic_coherence` with `sys.path` incl. `implementation/`):
  - kind=`definition_absent` + negative card + statement asserting "has a compiled definition" → **rejected**: `"statement rendering contradicts structured predicate: kind=definition_absent/polarity=negative requires the absence cue ('has no compiled definition'); found presence-cue=True, absence-cue=False"`.
  - kind=`definition_absent` + positive polarity → **rejected**: `"incoherent predicate kind='definition_absent' with card polarity='positive'"`.
  - `render_statement("definition_absent","positive",...)` **raises** ValueError — the renderer cannot produce the defect.
  - All 7 currently-accepted cards re-validated: 7/7 coherent (C1×2, C4C6_A×2, C7×3).
- Root-cause path closed: `k2/loop.py` `_generate_rulecard` (lines 266–289) renders the statement from the structured fields via `render_statement`; driver prose (`target["predicate_statement"]`) is never copied (explicit comment citing the C4 defect).

**Verdict: PASS.**

## V4 — Top-level refutation completeness

**Checked.** `implementation/outputs/wave1_1/REFUTATION_RESULTS_V2.json` — every currently-promoted card exactly once; historical 7 records present with `historical=true` and unchanged final states; no duplicate `(source_artifact_path, rulecard_id)` keys; demoted candidates recorded with `final_state: REJECTED_FAULTNESS_GATE`.

**Evidence.**
- `coverage`: `{duplicates: 0, exactly_once: true, promoted_cards_current: 7, records: 18}` (lines 2–7).
- Record inventory verified record-by-record against sources: current 11 = C1 2 promoted (`...wave1_1/C1/REFUTATIONS.json`) + 2 demoted (`.../C1/FAULTNESS_RESULTS.json`, `REJECTED_FAULTNESS_GATE`) + C4C6_A 2 promoted + C4C6_B 1 demoted (`REJECTED_FAULTNESS_GATE`) + C7 3 promoted + 1 demoted (`REJECTED_FAULTNESS_GATE`). Historical 7 = 4×C1 (`rounds/I6_C1/E2E_RECEIPT.json` — source file has exactly 4 rows, all `NOT_REFUTED_WITHIN_SCOPE`), 2×C4-twin (`rounds/I7_C4C6_A/REFUTATIONS.json` — 2 rows, unchanged), 1×C6-twin (`rounds/I7_C4C6_B/REFUTATIONS.json` — 1 row, unchanged `NOT_REFUTED_WITHIN_SCOPE`), all with `historical: true`, `PASS_HISTORICAL` / `NOT_APPLICABLE_HISTORICAL`, and the note "retained as motivating evidence, NOT as current validation" (lines 120–196).
- No `(source_artifact_path, rulecard_id)` duplicates: the C1 ids appear under BOTH the current path and the historical path, which are distinct keys by design; all other ids unique. The current C6-twin id (`RC-C4C6-B-LIB-GETCWD-107`, now `REJECTED_FAULTNESS_GATE`) vs. the historical `RC-I7-B-LIB-GETCWD-107` (`NOT_REFUTED_WITHIN_SCOPE`, unchanged) demonstrates the semantic repair without rewriting history.
- G14 cross-check (`WAVE1_1_ACCEPTANCE_RESULTS.json` lines 167–177): `duplicates: []`, `promoted_cards_missing_from_aggregate: []`, `historical_records: 7`, `records: 18`, `pass: true`.
- Minor non-blocking note: demotion records receive a hardcoded `evidence_integrity_status: "PASS"` in the runner (the demoted candidates' evidence status is not carried in `FAULTNESS_RESULTS.json` demotion rows); `faultness_status: FAIL` is source-derived. Cosmetic labeling, no count impact.

**Verdict: PASS.**

## V5 — Missing-file vacuous passes — **FAIL (falsified)**

**Checked.** By code inspection of `implementation/run_wave1_1_acceptance.py` (`_load`, `_accepted_cards`, gate assembly) + the T4 family in `implementation/tests/test_wave1_1_repair.py`; then empirically confirmed with an in-process probe using the runner's own functions (bytecode writes disabled; no repo file touched; an earlier full-runner TEMP-copy probe was abandoned when the copied environment broke 30 tests on repo-local index paths — the probe copy was deleted).

**What IS fail-closed (verified):**
- `_load` returns `None` on missing/unparseable JSON (lines 47–51); a missing receipt yields a synthetic `{"terminal_class": "not_e2e", "_missing": True}` receipt (lines 97–98) which `classify_round_receipt` maps to `not_e2e` → G1 count drops; missing `CORPUS_RECEIPTS_INDEX.json` → G1 explicitly False; missing `FAULTNESS_RESULTS.json` → `passed_ids` empty → G8 fails for every accepted card; missing `custody_log.jsonl` → G12 leak failure; missing `REFUTATIONS.json` while cards exist on disk → G14 subset check fails. Receipt-side fail-closedness is covered by the T4 family (`test_t4_freeze_only_or_empty_is_not_e2e`, `test_t4_unterminal_refutation_state_is_not_e2e`, etc. — all passing in my independent 75-test run).

**Counterexample (concrete, code-inspection + in-process proven):**
- `_accepted_cards` (lines 70–77) returns `[]` when a round's `rulecards/` directory is missing (`if not rd.is_dir(): return []`) and silently skips any unparseable card JSON (`card = _load(f); if card`). G7/G9/G11 iterate over that discovered set — with the set empty for a round, the loops append nothing and the gates pass vacuously.
- In-process probe with the runner's own code: `_accepted_cards(<missing dir>)` → `[]`; G7's pass expression `not coherence_failures` over that input → `True`.
- Full-gate consequence (by straight-line inspection of `main()`): with `C7_planted/rulecards/` missing, G1 still counts C7 `e2e_positive` (receipt intact), G2/G3/G4/G5/G6/G8(current cards)/G10/G12/G13/G14 remain green, and **no gate anywhere compares `receipt["accepted_fault_cards"]` against the disk card set** (probe confirmed both lists are currently equal — 3 ids — but the equality is unenforced). The runner can therefore emit 14/14 PASS / `WAVE1_1_MVP_ACCEPTED` while C7's cards were never coherence/faultness/schema/score-checked. The same applies at single-card granularity (one corrupt card JSON silently vanishes from G7/G9/G11/G10).
- This contradicts the runner's own docstring claim ("fail-closed: a missing artifact is a FAIL, never a vacuous pass", lines 3–4) and `POST_MVP_FINDINGS.md` §"Secondary defects" which lists "vacuous-pass paths (missing-file `continue`)" as hardened in Wave-1.1. No regression test covers the runner's card-discovery path (T4 covers only `classify_round_receipt`).

**Impact scoping (for proportionality):** the hole cannot inflate the E2E count, cannot promote any card, and cannot fabricate a fault — it can only let the acceptance verdict be issued while a round's card-level gates were silently skipped. The as-run measurement is NOT vacuous (7/7 cards checked, all consistent). It is a repairable completeness gap in the measurement apparatus, not an unsound measurement.

**Required repair (prescription, not implemented per mandate):** (1) add a reconciliation gate — for every round in the index, the set of `receipt.accepted_fault_cards[].rulecard_id` must equal the set of ids of parseable card files in `rulecards/`, failing in both directions; (2) make G7/G9/G11 fail if any indexed round's receipt claims >0 accepted cards but its card set is empty/unparseable; (3) add a T4-family regression test covering `_accepted_cards` missing-dir / corrupt-card behavior and the reconciliation gate. Re-run acceptance afterwards.

**Verdict: FAIL.**

## V6 — Lineage hardcoding

**Checked.** Lineage originates from driver-supplied parameters, is recorded as metadata, and acceptance/audit derives nothing from a hardcoded lineage constant.

**Evidence.**
- Drivers: `run_c7.py` lines 20+27 (`LINEAGE = "D (planted-source-deterministic)"` passed to `solve_case(..., lineage=LINEAGE)`); `run_wave1_1_corpus.py` lines 47, 85, 156, 204, 239 pass lineage per case.
- `stage_case_round(..., lineage: str = "")` (`k3/e2e_runner.py` lines 45–54) records it into `build_config/build_inputs.json`; `build_round_receipt` reads `staged["build_inputs"].get("lineage", "")` (`k3/terminal_receipt.py` lines 52–53). All five `build_config/build_inputs.json` files carry the correct lineage (`C1`→"A (Zephyr-derived)", `C4C6_A`/`C4C6_B`→"B (public NuttX snapshot pair)", `C5_safety`→"C (planted tripwire, single-line surface)", `C7_planted`→"D (planted-source-deterministic)"), matching receipts and `CORPUS_RECEIPTS_INDEX.json`.
- The acceptance runner contains **no** lineage reference at all; grep across `implementation/` for the four lineage strings shows hits only in drivers, round artifacts (build_inputs/receipts/index), the custodian oracle, and the wave-1 report — never in mechanism or audit code as a source of truth.
- Regression test T5 (`test_t5_lineage_recorded_from_driver_metadata`) asserts the metadata path end-to-end with a synthetic lineage string.

**Verdict: PASS.**

## V7 — Case-specific hardcoding in K2/K3 mechanism code

**Checked.** Independent token scan (getcwd, accel, sensor_submit, sensor_module, link_ready, k_work_submit, work_submit, CONFIG_DISABLE_ENVIRON, CONFIG_ACCEL_*, CONFIG_SENSOR_*, C1..C7, I6..I9) over `k2/faultness_gate.py`, `k2/statement_render.py`, `k3/terminal_receipt.py`, distinguishing executable code from comments/docstrings.

**Evidence.**
- `faultness_gate.py`: **0 hits** anywhere in the file.
- `statement_render.py`: **1 hit**, line 10 — inside the module docstring ("This module exists because Wave-1 accepted RC-I7-A-API-GETCWD…") — documentation, permitted.
- `terminal_receipt.py`: **0 hits**.
- Independent confirmation: runner's own stripped-source scan G10 reports `case_specific_tokens_in_mechanism_modules: []`; regression test T6 re-runs the same scan and asserts empty; `CONFIG_SYMBOL_RE` in `statement_render.py` is generic (`\bCONFIG_[A-Z0-9_]{2,}\b`), and the gate's role keys (`R1_/R2_/R3_/A1_/A2_/A3_/P1_/P2_/P3_`) are role names per the audit instruction.

**Verdict: PASS.**

## V8 — Substrate-modification prohibition

**Checked.** Substrate pin HEAD + clean `src/`; repair confined to `implementation/`.

**Evidence.**
- `git rev-parse HEAD` (run from `D:\github\fw-context-mcp`) → `d8f08a41d3fb9df6de255a187720f6332ed0f1c9` — exact match with `SUBSTRATE_PIN` in `run_wave1_1_acceptance.py` (line 31) and G13's recorded `head` value.
- `git status --porcelain -- src` → empty (no output).
- Full `git status --porcelain` shows only untracked paths, all **outside** `src/` (`.fw-closeout/`, `implementation/`, `fault_discovery_goal_architecture_agent_team/`, `post_mvp_extract/`, etc.) — no tracked file modified anywhere; the entire repair lives under `implementation/` (+ the agent-team documentation tree). The fact log provenance also pins the instrument: `"fw-context-mcp 0.31.0 @ d8f08a41d3fb9df6de255a187720f6332ed0f1c9"` (C7 `fact_log.jsonl` row 1).

**Verdict: PASS.**

---

## Overall coherence

**Test suite (independent run).** `cd implementation; python -m pytest tests -q` → **75 passed, 2 warnings in 12.66s** (my run; runner G6 recorded "75 passed, 2 warnings in 13.14s" — consistent). T1–T7 families inspected line-by-line and confirmed to assert the repair semantics (exact historical C4 defect rejected; benign conditional fails R3 while the C4-like region passes via `definition_cutoff`; guard-macro EA card fails A3; pairing requires negative call-flow and in-region call sites; unresolvable target fails closed; classifier fail-closed matrix; lineage metadata; token scan; schema v1.1 discipline with frozen schema still rejecting the `validation` object).

**Runner verdict.** `implementation/outputs/wave1_1/WAVE1_1_ACCEPTANCE_RESULTS.json`: verdict `"WAVE1_1_MVP_ACCEPTED"` (line 217); **14/14 gates recorded PASS** (G1 count=4 receipt-based with C5=`safety_only`; G2/G3/G4/G5 per-case; G6 suite; G7/G8/G9/G11 `cards_checked=7`, zero failures; G10 `cards_controlled=7`, zero tokens; G12 no oracle leak; G13 substrate; G14 aggregate 18 records / 7 historical / no dupes). All gate semantics re-derived by code inspection; the runner itself was NOT executed by this audit (its side effects would rewrite `outputs/wave1_1/*.json`). Note: per V5, the 14/14 verdict is **not yet trustworthy unconditionally** — see the FAIL and its prescribed repair.

**C7 planted mechanism — source truth.** The round's fw-context index lives in the non-default index home `implementation/fw_index_home/` (164 MB), which this session's MCP server does not expose, and building a new index would have violated the audit's one-file rule — so a live MCP re-query was not possible. The compliant path used: the round's **K1-frozen raw fw-context artifacts** (`implementation/rounds/wave1_1/C7_planted/raw/*.json` — fw-context outputs, hash-pinned in the fact log) plus the fact log and the custodian oracle:
- `list_conditional_regions_5d45fc2ce4177e4c.json`: exactly one conditional region in `fixture/sensor_module.c` — root line 19, end line 26, directive `if`, condition `defined(CONFIG_SENSOR_IRQ_PATH)`, branch `taken: 0` (compiled OUT under the pinned build).
- `explain_condition_1e7a33e9a38dfb0b.json`: `macro_inputs` shows `CONFIG_SENSOR_IRQ_PATH` with `def_source: ""` — **defined nowhere** (custodian `FAULT_ORACLE.json` line 13: plant token; the real Kconfig symbol is `CONFIG_SENSOR_IRQ_MODE`).
- `lookup_symbol_5c8c1ef772cdd646.json` / `lookup_symbol_a241b3723d3217c1.json`: `sensor_submit` has **no** compiled definition (only relaxed-fallback `sensor_read`, line 28).
- `find_callers_59c8a1ac90fe1906.json`: "No callers found for 'work_submit'." — the only submit call is not reachable under the pinned build.
- `read_file_*` windows: file is 34 lines; lines 19–26 are inactive (blank) with the guard skeleton at 19/26 — consistent with the guard enclosing `sensor_submit`'s definition and the only `work_submit` call, while `sensor_read` (line 28) stays live.
- Fact chain: `F-0005` (negative, `fw_context_lookup_symbol`), `F-0008` (negative, `fw_context_lookup_symbol`), `F-0013` (negative, `fw_context_find_callers`, T0) — exactly the evidence the Faultness Gate consumed for the three accepted C7 cards (`FAULTNESS_RESULTS.json` roles: R1/R2/R3 via region [19,26] + `definition_cutoff`; A1/A2/A3 absence with coverage; P1/P2/P3 pairing with `F-0003/F-0012/F-COV-0006/F-COV-0009` definitions and `F-0013` broken call flow). The gate's acceptance of the three C7 cards is **consistent with the source truth**; the A3 demotion of `RC-C7-SYM-CONFIG-SENSOR-IRQ-PATH` (guard macro absent = cause, not consequence) is likewise consistent.

---

## FINAL VERDICT

**`WAVE1_1_MVP_NOT_READY`**

One-line justification: every falsification attempt against the corpus, the mechanisms, the negative control, the semantic-coherence layer, the refutation aggregate, lineage provenance, and the substrate pin failed (V1–V4, V6–V8 PASS), but the acceptance runner's card-level gates (G7/G9/G11) pass vacuously when a round's `rulecards/` directory or any card file is missing/corrupt — with no receipt-vs-disk card reconciliation anywhere (V5 FAIL, empirically demonstrated with the runner's own code) — which violates the runner's own fail-closed contract and is repairable before the `WAVE1_1_MVP_ACCEPTED` verdict can be trusted.

**Work stops here: no generalization, no Wave 2.** The only permitted next step is the V5 repair (reconciliation gate + regression test + acceptance re-run), which is wave-1.1 scope, not new development.

## Non-blocking observations (documented, do not block the verdict mechanics)

1. **V5 residual (the FAIL itself)** — scoped as above; repair prescription given.
2. **Receipt traversal counters are trusted, not re-derived** — the runner does not recompute `traversal.fact_records` etc. from `fact_log.jsonl`. Cross-checked consistent for C7 (16 = 20 rows − 2 refusal rows − 2 RS2 rows); other rounds' receipts consistent with their rulecards/refutations. Fabrication would require forging the full per-round chain.
3. **Demotion records carry a hardcoded `evidence_integrity_status: "PASS"`** in `REFUTATION_RESULTS_V2.json` (runner-side label; `faultness_status: FAIL` is source-derived). Cosmetic; no count impact.
4. **Persisted rulecard JSONs hold pre-refutation state** (`refutation_state: "UNEXECUTED"`, `validation_status: "unvalidated"`) while receipts/REFUTATIONS.json carry the terminal states (`NOT_REFUTED_WITHIN_SCOPE` / `refutation_executed`) — cards are written at gate time and `execute_refutation` updates only the in-memory card + `REFUTATIONS.json` + receipt. The on-disk card state is the *weaker* claim (fail-safe direction); the aggregate correctly reads states from `REFUTATIONS.json`. A card-file rewrite at finalize time would remove the apparent inconsistency.
5. **C7 live fw-context re-query unavailable in this session** (non-default `FW_INDEX_HOME` not exposed to the MCP server; no new index built to honor the one-file rule) — mitigated by verifying via the round's hash-pinned fw-context artifacts, as documented above.

---

# ADDENDUM — 2026-09-21 (post-repair re-verification of V5; supersedes the FINAL VERDICT above)

Auditor: same fresh independent verifier. Scope: re-attempt V5 falsification against the hardened acceptance runner; spot-check that nothing else previously verified changed. The initial verdict block above is retained as the historical record; this addendum is the binding verdict.

## A1. V5 re-attempted falsification against the NEW runner code — all attempts fail

**New code inspected** (`implementation/run_wave1_1_acceptance.py`, mtime 2026-09-21 06:41:59 UTC):
- `_load_round_cards(round_dir)` (lines 74–93) replaces `_accepted_cards`: a missing `rulecards/` directory and any unparseable card file surface as **issues**, never a silent empty set.
- `reconcile_cards(receipt_accepted, disk_cards, issues)` (lines 96–114): enforces `claimed == disk` **in both directions** (`claimed_missing_on_disk` / `on_disk_unclaimed`); unparseable card files **always fatal**; missing dir **fatal whenever the receipt claims accepted cards**; missing dir with zero claims is consistent by design (a negative-control round legitimately has no card files).
- New explicit gate **G15** `receipt_vs_disk_card_reconciliation` over all 5 rounds; the same reconciliation check is **folded into G2/G3/G7/G9/G11** (each appends a failure on reconciliation failure), killing the vacuous-pass path in every card-consuming gate.

**In-process probe with the runner's own functions** (bytecode writes disabled; zero repo writes):
- `_load_round_cards(missing dir)` → `([], [{error: "rulecards directory missing"}])` — no silent pass.
- **The original counterexample (C7 receipt claims 3, disk cards missing) → `reconcile_cards` ok=False** with all 3 ids in `claimed_missing_on_disk`; G7's pass expression over that input → `False` (1 failure). The vacuous pass is dead.
- Both directions: claims=2/disk=3 → False (`on_disk_unclaimed`); claims=3/disk=2 → False (`claimed_missing_on_disk`).
- Unparseable card file with ZERO claims → ok=False (always fatal).
- Missing dir + zero claims → ok=True **by design**, and still guarded: a receipt claiming zero accepted cards for C7/C1/C4C6_A makes `classify_round_receipt` return `not_e2e` (no accepted, no rejected terminal) → G1 count drops to 3 → G1 fails. No path to a false `WAVE1_1_MVP_ACCEPTED` remains without forging a fully coherent receipt+cards+FAULTNESS+REFUTATIONS chain.

**T8 regression tests** (`tests/test_wave1_1_repair.py` lines 349–389, read in full): `test_t8_missing_rulecards_dir_is_an_issue_not_a_vacuous_pass`, `test_t8_unparseable_card_file_is_fatal`, `test_t8_reconciliation_both_directions`, `test_t8_missing_dir_with_zero_claims_is_not_fatal` — all four assert exactly the repair semantics and pass.

**Independent suite run:** `python -m pytest tests -q` → **79 passed, 2 warnings in 12.78s** (runner G6 records "79 passed, 2 warnings in 12.75s" — consistent; was 75 before the repair).

**On-disk acceptance re-run results** (`WAVE1_1_ACCEPTANCE_RESULTS.json`, regenerated): **15/15 gates PASS** (G1–G15); G15 `rounds_checked: 5, failures: [], pass: true`; reconciliation details embedded in G2/G3/G7/G9/G11 details; verdict string on disk: **`WAVE1_1_MVP_ACCEPTED`** (line 253; the transition report's "WAVE1_1_ACCEPTED" wording was a typo — the on-disk string matches the enum).

## A2. Spot-check that nothing else changed

- **V8:** `git rev-parse HEAD` → `d8f08a41d3fb9df6de255a187720f6332ed0f1c9` (pin intact); `git status --porcelain -- src` → empty.
- **Containment:** mtimes show only `run_wave1_1_acceptance.py` (06:41:59 UTC) and `tests/test_wave1_1_repair.py` (06:42:04 UTC) changed since the initial audit; all mechanism modules (`faultness_gate.py` 06:28:17, `statement_render.py` 06:19:06, `terminal_receipt.py` 06:31:03, `loop.py` 06:17:36, `e2e_runner.py` 06:26:16) and ALL round artifacts (receipts, FAULTNESS/REFUTATIONS/fact logs, index — 06:28:3x) predate the repair → **no corpus, mechanism-module, receipt, or historical artifact touched**.
- **V1/V2/V3/V4/V6:** receipts unchanged (C4C6_B still zero accepted + GETCWD-107 rejection; C5 still safety_only; G1 count=4 receipt-based); regenerated `REFUTATION_RESULTS_V2.json` identical coverage (18 records / 7 promoted exactly-once / 0 duplicates / 7 historical); regenerated `NEGATIVE_CONTROL_REPORT.json` identical (7 cards, all checks true, mechanism scan hits=[]); lineage artifacts untouched.
- **V7:** token rescan over `fault_discovery_agent/` finds only documentation hits — the pre-existing `statement_render.py` docstring (line 10) and a comment in `e2e_runner.py` line 539 (`CONFIG_DISABLE_ENVIRON=y` example; e2e_runner is a K3 runner outside the three-module mechanism scan scope, and it is a comment). Executable mechanism code remains token-free.

## A3. Updated per-vector verdicts

V1 PASS · V2 PASS · V3 PASS · V4 PASS · **V5 PASS (repair verified, falsification re-attempts exhausted)** · V6 PASS · V7 PASS · V8 PASS

## FINAL VERDICT (BINDING)

**`WAVE1_1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS`**

All 8 vectors PASS. The V5 repair is verified effective by code inspection, the T8 tests, an independent 79-pass suite run, and re-executed falsification probes using the runner's own functions. Documented non-blocking limitations (named per the verdict contract):

1. **Receipt traversal counters are trusted, not re-derived** from `fact_log.jsonl` by the runner — mitigated by this audit's cross-artifact consistency checks (all five rounds), the fail-closed classifier, and the new reconciliation gate; a fabricated receipt would require forging the full per-round chain.
2. **Demotion records** in `REFUTATION_RESULTS_V2.json` carry a runner-hardcoded `evidence_integrity_status: "PASS"` label (the `faultness_status: FAIL` is source-derived) — cosmetic labeling on already-rejected candidates.
3. **Persisted rulecard JSONs hold pre-refutation state** (`refutation_state: "UNEXECUTED"`, `validation_status: "unvalidated"`); terminal states live in `REFUTATIONS.json` + receipts — fail-safe direction (weaker claim on disk).
4. **Runner module docstring still says "Gates G1..G14"** while 15 gates exist, and the informational `rounds[].accepted_fault_cards` output block still uses the unreconciled `_accepted_cards` discovery (issues discarded there; all gates use the reconciled sets) — cosmetic.
5. (Audit-environment, not a project defect) C7 live fw-context MCP re-query unavailable in this session; verified via the round's K1-frozen fw-context artifacts as documented in the Overall Coherence section.

WAVE1_MVP_SEMANTICALLY_VALIDATED → READY_FOR_DISCOVERY_GENERALIZATION_REVIEW

Work STOPS here: no generalization, no Wave 2.
