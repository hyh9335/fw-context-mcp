# Refutation aggregation explanation (extraction only; no aggregate modified)

## The three facts to reconcile

1. `implementation/outputs/wave1/WAVE1_ACCEPTANCE_RESULTS.json` records **7 promoted RuleCards** — `case_rounds`: C1 → 4 (`RC-C1-ACCEL-TRIGGER-33`, `RC-C1-ACCEL-TRIGGER-47`, `RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD`, `RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR`), C4-twin → 2 (`RC-I7-A-LIB-GETCWD-38`, `RC-I7-A-API-GETCWD`), C6-twin → 1 (`RC-I7-B-LIB-GETCWD-107`), C5-tripwire → 0.
2. The top-level `implementation/outputs/wave1/REFUTATION_RESULTS.json` contains **3 entries** (`refutations[]`): C4-twin × 2 and C6-twin × 1.
3. The release audit (`implementation/outputs/wave1/WAVE1_RELEASE_AUDIT.md`, check C07/C16) reports every promoted RuleCard has refutation coverage — **AUDIT_PASS**.

## Determined explanation (supported by artifacts)

**Primary category: refutations are stored per-round and the top-level file is only a partial aggregate.** Additionally, **the audit check uses a different source of truth.** The top-level file is not stale in the sense of lost data — every promoted card's refutation exists in a per-round artifact; the aggregate simply does not include C1, and C5 needs no entry.

### Exact mechanism, with paths and counts

The aggregate is assembled by `implementation/run_i9_acceptance.py` (lines 98–100):

```python
refs = rd / "REFUTATIONS.json"
if refs.is_file():
    all_refutations[name] = json.loads(refs.read_text(encoding="utf-8"))
```

It only picks up per-round files named exactly `REFUTATIONS.json`. Those exist for exactly two rounds:

| Per-round artifact | Entries |
|---|---|
| `implementation/rounds/I7_C4C6_A/REFUTATIONS.json` | 2 (`RC-I7-A-LIB-GETCWD-38`, `RC-I7-A-API-GETCWD`) |
| `implementation/rounds/I7_C4C6_B/REFUTATIONS.json` | 1 (`RC-I7-B-LIB-GETCWD-107`) |
| `implementation/rounds/I6_C1/REFUTATIONS.json` | **does not exist** |
| `implementation/rounds/I8_C5_T2_TRIPWIRE/REFUTATIONS.json` | **does not exist** |

Hence the top-level aggregate = 2 + 1 = **3 entries**.

Why C1 has no `REFUTATIONS.json`: round `I6_C1` was executed before the per-round `REFUTATIONS.json` persistence was added to `solve_case`/`execute_refutation` (added during the I7 re-run). C1's refutation records — all 4, each with RS1/RS2/RS3 steps and final state `NOT_REFUTED_WITHIN_SCOPE` — are persisted in `implementation/rounds/I6_C1/E2E_RECEIPT.json` under the key `refutations` (extracted copy: `E_REFUTATIONS/C1/E2E_RECEIPT.json`).

Why the audit still passes: audit check C07/C16 in `implementation/verify_i11_23checks.py` (lines 139–145) uses a different source of truth with an explicit C1 fallback:

```python
rp = ROUNDS[n][0] / "REFUTATIONS.json"
if rp.is_file():
    refs = {r["rulecard_id"]: r for r in json.loads(rp.read_text(encoding="utf-8"))}
elif n == "C1":
    # C1 predates persistence: refutations live in E2E_RECEIPT.json
    refs = {r["rulecard_id"]: r for r in json.loads(.../E2E_RECEIPT.json).get("refutations", [])}
```

and it only requires coverage for **promoted** cards — C5 promoted zero cards (terminal outcome `insufficient_evidence` before card creation, proven by `implementation/rounds/I8_C5_T2_TRIPWIRE/I8_RECEIPT.json`), so C5 correctly requires no refutation entry anywhere.

### Count reconciliation

| Source | Count | Coverage |
|---|---|---|
| Promoted cards (acceptance) | 7 | C1:4, C4-twin:2, C6-twin:1, C5:0 |
| Top-level `REFUTATION_RESULTS.json` | 3 | C4-twin:2, C6-twin:1 — **C1 absent (filename-based aggregate only)** |
| Per-round refutation records (all locations) | 7 | C1:4 (in `E2E_RECEIPT.json`), C4-twin:2, C6-twin:1 |
| Audit check C07/C16 scope | 7 | reads both locations (explicit C1 fallback); promoted-only |

**Conclusion of the check:** fact 3 holds — all 7 promoted cards have executable refutation records RS1–RS3 with frozen-state results, spread across two per-round artifact forms. Facts 1 and 2 differ because the top-level file is a partial, filename-based aggregate (primary category), while the audit reads the per-round sources of truth directly (secondary category). No aggregate file was modified.
