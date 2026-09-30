# CORPUS LOCK PLAN — Wave-1 E2E corpus (C1, C4, C5, C6)

Governs: S0_GOAL_ADR.md §1 (freeze discipline) + §2 (rubric); WAVE1_CORPUS_MATRIX.md §1–5.
State: D1 materialization COMPLETE (2026-09-20T08:02:03Z); freeze execution is a CUSTODIAN act per
this plan. The §5 hashes are the authoring-time pre-freeze baseline; re-hash at freeze.

## 1. Freeze order and conditions per case
| Order | Case | Freeze condition | Result of freezing |
|---|---|---|---|
| 1 | C4 | nuttx cloned; checkout 526ba1ab343a00b2e1801e089392eed64758b820 (recipe in C4 SOURCE_LOCK.json) | pre-fix solver snapshot + manifest |
| 2 | C6 | same clone; checkout 67ed036227b14bbab01b63801bfd7e218dfd6107 | post-fix solver snapshot + A/B mapping (custodian-only) |
| 3 | C1 | fixture/ present in planted state; build context pinned | fixture manifest |
| 4 | C5 | fixture/ present in planted state; build context pinned | fixture manifest |
| 5 | all | plants verified vs PLANT_DIFF; oracles hashed; solver bundle assembled; A/B randomized+recorded | freeze_id issued; round may open |

Order rationale: C4→C6 share one clone; A/B randomization happens once both snapshots exist.
C4/C6 pin the build context CONFIG_DISABLE_ENVIRON=y (C4 SOURCE_LOCK.json build_context).

## 2. Fixture/hash recipe (exact)
1. Per file: SHA-256 over raw file bytes, lowercase hex (`Get-FileHash -Algorithm SHA256` / `sha256sum`).
2. File set per case: that case's frozen files, relative paths sorted byte-wise ascending.
3. Combined manifest hash: SHA-256 over UTF-8 text (no BOM, LF endings) of one
   `<sha256>  <relpath>\n` line per file, concatenated in sorted order (two spaces between fields).
4. freeze_id: `FRZ-<CASE>-<YYYYMMDDTHHMMSSZ>`; every FactRecord row of the round carries it.
5. Oracle files use the same recipe; their HASHES go in the manifest, their CONTENT never leaves
   custody (oracle hash visible, oracle content hidden).

"frozen" means: C4 = pre-fix checkout + pinned config + manifest; C6 = post-fix checkout + same
config + custodian-held A/B mapping; C1/C5 = planted fixture tree + pinned build context + manifest.
After freeze, S0-Q1 item 1 applies verbatim (no modification of case source, oracle, frozen Agent,
or frozen fw-context within a round; changes between rounds require a new freeze/hash and re-run).

## 3. Pre-freeze plant rule (S0-Q1)
Plants were introduced BEFORE freeze: C1 = one-token guard misspelling (C1 PLANT_DIFF.md); C5 =
one-line comparison inversion (C5 PLANT_DIFF.md). C4/C6 carry NO plant (real upstream states). No
measurement round has consumed any case; §5 is the untouched baseline. Any post-freeze edit to a
fixture or oracle requires a new freeze + new round (S0-Q1 item 4).

## 4. Custody
- Custodian-only (K3 MUST exclude from solver context): every FAULT_ORACLE.json; C4 EVIDENCE_MAP.md;
  C4+C6 SOURCE_LOCK.json; C6 PAIRING_NOTE.md; C1+C5 PLANT_DIFF.md.
- Solver-visible: the snapshot/fixture trees, C1+C5 CASE_DESIGN.md, and a redacted task brief
  (no fix SHA, no pairing statement, no plant location).
- Freeze manifest records oracle hashes (visible) while oracle content stays hidden (§2.5).
- Scorer = fresh verifier context, receives oracles only AFTER solving; any custody breach voids
  the round (S0_GOAL_ADR.md §1/§4).

## 5. Authoring-time baseline manifest (SHA-256, 2026-09-20)
```
fd30f258c9bc864da99df2a134ba4f85a3e4350513995572211cbc9fd70f9120  corpus/C1_PFC1_SOURCE_DETERMINISTIC/CASE_DESIGN.md
d1f90980e31305748351cfd560d476d1da25dfbe1639219a5b81cc88c5e67451  corpus/C1_PFC1_SOURCE_DETERMINISTIC/FAULT_ORACLE.json
12a947b297e1b4abfa611832421d9a0eb790c70a4a6fc50eadab0b0f1cfe26b5  corpus/C1_PFC1_SOURCE_DETERMINISTIC/fixture/accel_core.c
3442629b70341936b4193323897f2b946b298de16bba8acf34199e3db442d4d5  corpus/C1_PFC1_SOURCE_DETERMINISTIC/fixture/accel_local.h
a86e9cebda4b4c49c03056e16db6ab9ee4e8470f5cc3b8aee4b076e27cc4c5ee  corpus/C1_PFC1_SOURCE_DETERMINISTIC/fixture/accel_trigger.c
990bc522ded9a6d198f906d50dbbf2687554ff00e02ffa41fc2d2084b1b6bfbc  corpus/C1_PFC1_SOURCE_DETERMINISTIC/fixture/CMakeLists.txt
25b36959cc6f31f2ff68c735ffe08a0bee77be778357b916c4ab52c9d0d2b410  corpus/C1_PFC1_SOURCE_DETERMINISTIC/fixture/Kconfig
db6d64ac14cf0ae1d78d720517844efd207caf3c50897bd703117844b5d66ce0  corpus/C1_PFC1_SOURCE_DETERMINISTIC/PLANT_DIFF.md
d082f3e739639586b01855330834a6bf79b38d27b60bd719e82f1c140b15a0f4  corpus/C4_REAL_PUBLIC_FAULT/EVIDENCE_MAP.md
0968115a575f14e4743f4b0c95e23cae0bd270002b8b9fc4bf0a6ab2a76942b3  corpus/C4_REAL_PUBLIC_FAULT/FAULT_ORACLE.json
421c26ceb4108ce1fe73aa6fc0721b33e5b5aac94a0fe086a8788f844e1c7685  corpus/C4_REAL_PUBLIC_FAULT/SOURCE_LOCK.json
146ad25225f9c8decc92b34cd8c28eb9777961713fb22e7752bc9bdf798bed99  corpus/C5_T2_TOUCHING/CASE_DESIGN.md
eadf51ed1d4efb5f0e79c2815ff18f26e4c59c05d73e099f07ff110e2dfc434f  corpus/C5_T2_TOUCHING/FAULT_ORACLE.json
3198e3a612a0d6cae46239bd71287e045df167d43b402fec62172ae72a4c1081  corpus/C5_T2_TOUCHING/fixture/CMakeLists.txt
8e9ad7f8b580308b37db3649c204081296e4bdd9e6e98a4eb3d51d7c7b0a4cd9  corpus/C5_T2_TOUCHING/fixture/linkmon.c
c2f65e948dd559e13110cecf43a2d075bddd4779f8e5b242f59b8289ac05256e  corpus/C5_T2_TOUCHING/PLANT_DIFF.md
5fa1c7241c27c2f9f4774ee68a10522b5d6cb33c95e6a5b9bedbad6a4e8d416c  corpus/C6_NEGATIVE_CONTROL/FAULT_ORACLE.json
66a90b0105bef39f7b778421d5c0e82cf901cc340d6d5656a7e4a654467c377c  corpus/C6_NEGATIVE_CONTROL/PAIRING_NOTE.md
cd6725071582732b3ab0743dd911ae697131450cbda7a1f2a04a9a04b1817cb4  corpus/C6_NEGATIVE_CONTROL/SOURCE_LOCK.json
```
Combined corpus manifest SHA-256: `3b2ad0619de9d4017d7a110041ee373331f681dfb43bfc5d6797a96869ad1d1d`
(C4/C6 snapshot trees are hashed at freeze time from the checkout, not here.)

## 6. C2r/C3r disposition
C2r (exact-lookup/`_fallback`-rejection reshape) and C3r (truncation-honesty reshape) are recorded
as OPTIONAL_STRETCH_CASE. Admission requires an independent mechanism-value justification recorded
HERE before any build: which mechanism does the reshape exercise that C1/C4/C5/C6 do not. Corpus-
count pressure is explicitly REJECTED as justification (WAVE1_CORPUS_MATRIX.md §5): C1+C4+C5+C6
already satisfy the ≥4 E2E minimum; neither C2r nor C3r is required for it.

## 7. Lineage table (rubric item 6: ≥2 independent lineages)
| Lineage | Source | Cases |
|---|---|---|
| A | Zephyr — real structure: zephyr v3.7.0 drivers/sensor/st/lis2dh (lis2dh_trigger.c, Kconfig, CMakeLists.txt), reworked into a standalone slice | C1 |
| B | apache/nuttx public history (pre-fix 526ba1ab… / post-fix 67ed0362…), era nuttx-apps 15b081f0… | C4, C6 |
| C | authored standalone fixtures, no verbatim upstream file | C2/C3 contract fixtures; C5 fixture (structural idiom modeled on Zephyr patterns; DERIVED_FROM note only) |

## 8. Open items → next phase
- Single open item (NON-blocking): nuttx-apps getcwd consumer pinning. Fallback is active and
  recorded — consumer predicate = "any caller of getcwd() at link time" (C4 EVIDENCE_MAP §4; RC4
  existential, negatable=false). Next-phase option: where network egress allows a real clone of
  nuttx-apps @ 15b081f0…, grep for getcwd callers and upgrade RC4 to a named consumer. This upgrades
  evidence quality only; the case is fully specified without it.
- No other blocking items; the freeze execution checklist is §9.

## 9. Freeze checklist (per case, custodian executes)
1. Verify plant presence against PLANT_DIFF (C1/C5) or the upstream SHA (C4/C6).
2. Compute per-file + combined hashes per §2; record the freeze_id.
3. Hash every oracle file (§2.5); confirm K3 excludes all custodian-only files from solver context.
4. Assemble the solver bundle (snapshot/fixture + CASE_DESIGN + redacted brief); randomize A/B
   (C4/C6) and record the mapping custodian-side only.
5. Open the round only after 1–4 are done; S0-Q1 discipline applies from that moment.
