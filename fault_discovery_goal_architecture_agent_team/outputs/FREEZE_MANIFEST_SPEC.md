# FREEZE MANIFEST SPEC — reproducible freeze recipe (Wave-1)

Governing: S0_GOAL_ADR.md §1 (S0-Q1) and §4 (custody); CORPUS_LOCK_PLAN.md §2 (fixture/hash recipe — this spec MUST agree with it so corpus, plant fixtures, and manifest hash identically); FAULT_DISCOVERY_REQUIREMENTS.md FR-6. Producer: custodian via K3 (K3_INTERFACE_SPEC.md §1). Consumers: K1 (freeze_id stamping), K2 (bundle binding), scorer.

## 1. Canonical serialization + hashing recipe
1. JSON artifacts: canonical JSON = UTF-8 (no BOM), LF line endings, no trailing whitespace, object keys sorted byte-wise lexicographically, no insignificant whitespace, arrays keep given order, numbers in shortest round-trip form.
2. Non-JSON artifacts (source trees, prompts, logs, EXPECTED.json as stored): raw bytes as-is.
3. Per-artifact hash: lowercase hex SHA-256 over the canonical bytes.
4. Combined manifest hash: SHA-256 over UTF-8 text (no BOM, LF) of one `<sha256>  <relpath>\n` line per artifact — TWO spaces between fields — paths relative to the round root, sorted byte-wise ascending, no extra terminator. This is EXACTLY the CORPUS_LOCK_PLAN.md §2.3 recipe (and the PT fixture_hash recipe inside each PLANT_SPEC.md): corpus and manifest agree by construction.
5. freeze_id = `FRZ-<CASE>-<YYYYMMDDTHHMMSSZ>` (CORPUS_LOCK_PLAN.md §2.4); one manifest per case; artifacts shared across cases of one round (e.g. C4/C6) repeat their per-artifact hash in each manifest.

## 2. Hashed artifact classes (ALL required; an omission refuses the freeze)
| # | class | contents |
|---|---|---|
| 1 | target_source_snapshot | solver-visible case tree: C4/C6 checkout (resolved HEAD SHA recorded), C1/C5 fixture/ |
| 2 | build_config_snapshot | compile_commands.json + pinned build context (e.g. CONFIG_DISABLE_ENVIRON=y) + fw-context index state id |
| 3 | agent_implementation_version | solving Agent implementation + prompt-bundle version hash |
| 4 | prompts_config | K2 family library, QueryBundle templates, redacted task briefs |
| 5 | fw_context_version | fw-context-mcp version + HEAD |
| 6 | k123_implementation_version | K1/K2/K3 implementation version (field present once implementations exist) |
| 7 | rulecard_schema_version | RULECARD_SCHEMA.json $id + freeze revision (wave1-design-gate-1); likewise FACT_RECORD_SCHEMA.json, MECHANISM_HYPOTHESIS_SCHEMA.json, REFUTATION_STATE_MODEL.md#5 |
| 8 | detector_versions | detector implementations G1..G9 (load-bearing set G1/G4/G6/G7/G8 first) |
| 9 | corpus_fixture | per CORPUS_LOCK_PLAN.md §5 baseline, RE-hashed at freeze (§5 is the pre-freeze baseline, not the freeze itself) |
| 10 | oracle_version | oracle FILE HASHES only (FAULT_ORACLE.json etc.); content NEVER enters the manifest — custodian-only (CORPUS_LOCK_PLAN.md §2.5/§4) |

## 3. Manifest schema (example)

```json
{
  "manifest_version": "1",
  "freeze_id": "FRZ-C1-20260921T000000Z",
  "case": "C1",
  "lineage": "A",
  "artifacts": [
    {"class": "target_source_snapshot", "relpath": "corpus/C1_PFC1_SOURCE_DETERMINISTIC/fixture/accel_core.c", "sha256": "<64-hex>"},
    {"class": "build_config_snapshot", "relpath": "build/compile_commands.json", "sha256": "<64-hex>"},
    {"class": "oracle_version", "relpath": "corpus/C1_PFC1_SOURCE_DETERMINISTIC/FAULT_ORACLE.json", "sha256": "<64-hex>", "custody": "custodian_only_content_hidden"}
  ],
  "combined_manifest_sha256": "<64-hex>",
  "created_at": "2026-09-21T00:00:00Z",
  "created_by": "custodian"
}
```

## 4. Re-freeze rule (S0-Q1)
- Any change to any hashed artifact AFTER measurement begins ⇒ measurement-time adaptation ⇒ round VOIDED (S0_GOAL_ADR.md §1).
- Changes BETWEEN rounds are legal but require a NEW freeze_id + new round; the changed artifact re-runs the round under its new hash.
- Detector expiry triggers (DETECTOR_ADMISSION_MATRIX.md expiry/revalidation column) force re-freeze + re-run of the affected MUST-PASS plant tests.

## 5. Worked example (placeholder hashes)
Freezing C1: the custodian runs the CORPUS_LOCK_PLAN.md §9 checklist — verify the plant against PLANT_DIFF.md (custodian-side), hash fixture/ per §1.4, hash FAULT_ORACLE.json (hash visible, content hidden), assemble the solver bundle, then emit manifest FRZ-C1-20260921T000000Z whose combined_manifest_sha256 covers the sorted `<sha256>  <relpath>` lines (placeholder `<64-hex>` values above stand for real digests). K1 stamps every FactRecord of the round with this freeze_id; the scorer refuses any round whose combined_manifest_sha256 does not recompute (K3_INTERFACE_SPEC.md §1).
