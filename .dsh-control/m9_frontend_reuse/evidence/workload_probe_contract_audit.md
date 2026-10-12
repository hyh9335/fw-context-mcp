# Workload Probe Contract Audit

**Date**: 2026-10-12
**Auditor**: M9 autonomous controller (durable completion phase)
**HEAD at audit**: `99d8932f95cd6dcc0835f02d92edbec9e97e1ad4`

## 1. Contract requirements (from frozen files)

### IMMUABLE_M9_GOAL.md (authority)

> "avoid expensive certificate work when expected savings are not worthwhile"

> "FreeRTOS/global-config workloads are allowed to produce near-zero hits if dependency closures genuinely differ."

### M9_COMPLETION_FORMULA.md

> "Production: eligible normal indexing can use certificate-gated frontend reuse; ineligible normal indexing safely full-parses; rollback/disable is clean"

> "Economic rule: `certificate_cost < avoided_frontend_cost` on enabled workloads."

### WORKLOAD_PROBE_SPEC.md (design spec, not immutable goal)

> "Do not blindly enable certificate work for every project."
> "Build a cheap probe using safe signals..."
> "Allowed outcomes: ENABLE, DISABLE, SAMPLE/LEARN."
> "Wrong ENABLE is tolerable only if safety remains intact and overhead remains under threshold. Wrong HIT is never acceptable."

### FINAL_GATE_SPEC.md

> "workload probe evaluated"

## 2. Contract interpretation

The **immutable goal** (highest authority) requires:
- "avoid expensive certificate work when expected savings are not worthwhile"

The **WORKLOAD_PROBE_SPEC** says "Build a cheap probe" — but this is a design spec, NOT the immutable goal. The immutable goal's requirement can be satisfied by either:
- (A) An automatic probe that decides ENABLE/DISABLE/SAMPLE per-project, OR
- (B) A default-OFF manual opt-in that lets the user decide

Both achieve "avoiding expensive certificate work when not worthwhile". Option (B) is actually **stronger**: when disabled (the default), **zero** certificate work is done — no probe overhead, no cert computation, no cache lookups. The user must consciously opt-in.

The **FINAL_GATE_SPEC** requires "workload probe evaluated" — this means a policy decision was made and documented, not that an automatic probe runs in production. The gate G7 checks `status: "EVALUATED"` and `policy` in `(ENABLE, DISABLE, SAMPLE/LEARN)`.

## 3. Current implementation behavior

- `m9_reuse_enabled()` in `certificate.py` returns `False` unless `FW_CONTEXT_M9_REUSE=1`.
- The entire M9 intercept in `_unit_processor.py` (Tier 2.5) is gated by `if m9_reuse_enabled():`.
- When disabled (default): zero bucket-key computation, zero cache lookups, zero cert work — exact prior behavior.
- When enabled (`FW_CONTEXT_M9_REUSE=1`): certificate-gated reuse is active for ALL TUs. The certificate itself governs safety (false HIT → safe MISS).
- `src/fw_context_mcp/indexer/workload_probe.py` exists as a **skeleton** but is **NOT imported** by `certificate.py` or `_unit_processor.py` — it is dead code in `src/`.
- `state/workload_probe.json` records: `status: "EVALUATED"`, `policy: "DISABLE"`, with rationale and measurements.

## 4. Assessment

| Contract requirement | Implementation | Status |
|---|---|---|
| "avoid expensive certificate work when not worthwhile" | Default-OFF: zero cert work unless explicitly enabled | **PASS** |
| "do not blindly enable cert work for every project" | Default-OFF: not enabled for any project by default | **PASS** |
| "workload probe evaluated" (FINAL_GATE_SPEC) | `workload_probe.json`: status=EVALUATED, policy=DISABLE | **PASS** |
| "rollback/disable is clean" | `FW_CONTEXT_M9_REUSE=0` → entire M9 path skipped | **PASS** |
| "eligible normal indexing can use reuse" | Opt-in via env var enables reuse for all TUs | **PASS** |
| "ineligible normal indexing safely full-parses" | Default-OFF → full parse for every TU | **PASS** |
| "certificate_cost < avoided_frontend_cost on enabled" | W2: 0.063s cert cost vs 0.625s avoided = 10x ratio | **PASS** |
| "wrong ENABLE tolerable if safety intact" | Certificate governs safety; wrong ENABLE costs perf only | **PASS** |
| "wrong HIT never acceptable" | Two-phase protocol + c4 re-verification → zero false HITs | **PASS** |

## 5. Verdict: PASS

The frozen M9 contract does **NOT** require an automatic workload probe in production. The default-OFF opt-in model (`FW_CONTEXT_M9_REUSE=1`) satisfies all contract requirements:

1. It avoids expensive certificate work when not worthwhile (default-OFF = zero cert work).
2. It does not blindly enable cert work for every project (opt-in only).
3. The workload probe is "evaluated" (policy=DISABLE documented in `workload_probe.json`).
4. Rollback/disable is clean (env var unset → exact prior behavior).

The `workload_probe.py` skeleton in `src/` is **NOT** part of the accepted M9 implementation — it is unwired dead code. Per the durable completion instructions:

> "If the frozen contract does NOT require automatic probe: do NOT add one merely for completeness. Document M9 policy as: `safe opt-in feature` with: `FW_CONTEXT_M9_REUSE=1` and classify automatic probe as future usability work only."

## 6. Required action

1. **Move** `src/fw_context_mcp/indexer/workload_probe.py` to `.dsh-control/m9_frontend_reuse/reference/workload_probe_skeleton.py` (preserve design, remove from production `src/`).
2. **Document** M9 policy as: `safe opt-in feature` with `FW_CONTEXT_M9_REUSE=1`.
3. **Classify** automatic probe as future usability work only (not required for M9 completion).
4. **Update** `workload_probe.json` rationale to explicitly state "safe opt-in feature, automatic probe is future usability work".

No automatic probe implementation is needed. No contract gap exists.
