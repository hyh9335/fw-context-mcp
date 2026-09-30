# Wave-1 Implementation Report — Fault Discovery Agent

**Package**: `implementation/fault_discovery_agent/` (`__version__ = 0.1.0`,
`WAVE1_SCHEMA_VERSION = "wave1-design-gate-1"`)
**State machine**: I0_PRECHECK → … → I11_RELEASE_AUDIT (all states executed in
order; every state has a machine-readable receipt under `implementation/rounds/`
and `implementation/outputs/wave1/`).

---

## 1. What was built

A deterministic (no-LLM) agent that discovers firmware fault mechanisms from
source and emits evidence-backed fault-detection RuleCards with executable
refutation:

```
source → evidence (K1) → mechanism hypothesis (K2) → RuleCard → refutation (RS1-RS3)
```

| Module | Role | Discipline |
|---|---|---|
| `schemas/` | frozen JSON schemas + canonical JSON + refutation state machine | draft-07 validation; 5 refutation states; `counterexample_ref=None` required for NOT_REFUTED_WITHIN_SCOPE |
| `freeze/` | FREEZE_MANIFEST_SPEC implementation | canonical JSON, SHA-256, combined hash over sorted `<sha256>  <relpath>` lines, `FRZ-<CASE>-<ts>` |
| `k1/` | sole substrate access (T0 in-process handler calls), detectors G1/G4/G6/G7/G8, fact log + error taxonomy, independent channels (raw-disk grep, clang -E, manifest diff), gateway | T0 named scopes; T1 fail-closed (rejects `_fallback`/`_did_you_mean`; truncation ⇒ lower bound); T2 never load-bearing |
| `k2/` | evidence gate (E1-E4) + deterministic loop over family `wave1-families-1` | gate PASS 100% required per outcome; exactly one terminal: card / rejected / abstention |
| `k3/` | plant runner (provisioning, stale-lock hygiene), custody log, scorer (blind S3 sampling, overclaim hard-zero), e2e runner (staging → solving → refutation) | custody events per round; oracle-blind solving path |

**Case-rounds executed** (all frozen + custody-logged):
- **C1** (Zephyr-derived accel-trigger, dead-config guard): 4 cards, all PASS, refuted-in-scope (I6).
- **C4-twin / C6-twin** (blind A/B NuttX getcwd pair): A — whole-file guard compiled OUT + `getcwd` definition ABSENT (the C4 mechanism); B — narrowed local guard only, **zero absence cards** (C6 negative control clean) (I7).
- **C5-tripwire** (single-line `link_ready() != 0` inversion): T2-only promotion refused first-class (E3); same mechanism passes once an independent second surface is attached (I8).

**Acceptance** (I9): 10/10 gates PASS →
`WAVE1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS`. **KC-2A** (I10): 12/12 units
agree (bounded, underpowered, non-gating). **Track-B**: 8 substrate findings
handed off as pre-drafted PRs, none actioned (`TRACK_B_PULL_REQUESTS.json`).

## 2. Evidence-soundness hardening (the I7 lesson, generalized)

The decisive engineering event of wave-1: the C6 twin initially produced a
**false absence card** for a symbol that was present. Root-caused to a chain of
three independent defects, all fixed inside the agent layer:

1. **Stale index locks** (dead-PID holders) made handler indexing fail CLOSED,
   surfacing as empty views. Fix: `clear_stale_index_locks()` (PID-liveness
   checked, fail-safe) before every round; 264 stale locks removed in the
   incident run.
2. **Unguarded empty-lookup→absence minting**: an empty exact lookup was read
   as absence evidence. Fix: the **absence-soundness gate** — absence claims
   require index-coverage proof (`read_file` on the target file); coverage
   failure ⇒ `E-COVERAGE` refusal (taxonomy extended) ⇒ loop abstains.
3. **Comment-delimiter elision markers** in the staged fixture closed the
   enclosing block comment early, turning prose into code garbage and silently
   emptying the symbol table (conditionals stayed recorded). Fix: staging rule
   bans comment delimiters inside markers.

The pair now adjudicates cleanly: A absence (true), B no absence (true) —
symmetric, deterministic, oracle-blind.

## 3. Environment substitutions (recorded, not hidden)

- **K1 transport**: in-process handler invocation (MCP wire validated in W0, not re-run here) — L2.
- **clang channel**: native Windows clang (WSL blocked) — L3.
- **Egress**: git/curl/pip/WSL blocked; `web_fetch` only, middle-prunes long bodies ⇒ structure-faithful C4/C6 fixture with acquisition receipt; upstream blob-hash verification ATTEMPTED-BLOCKED — L1.
- **Subprocess hygiene**: every subprocess uses `encoding="utf-8", errors="replace"` (Windows GBK); multiline `python -c` replaced by script files.

## 4. Test & measurement summary

- pytest: **52/52 passed** at I11 (suite covers schemas, K1 detectors, K2 gate/loop, K3 runner/scorer/refutation).
- Detector admission (I5): PT-G1/G4/G6/G7/G8 all **PASS 100%**.
- Acceptance (I9): 10/10 gates; blind-sampling ratio 0.75 (≥0.30 floor); 7/7 cards schema-valid with executed RS1-RS3; zero silent unsupported claims; zero custody breaches; oracle isolation verified against 6 oracle files (byte-hash level).
- KC-2A (I10): 12/12 agreement, deviation receipt recorded.

## 5. Wave-2 handoff (out of scope here)

Deferred with receipts: indirect callback-flow families (blocked on F-V1-004),
dead-code families (F-V1-005), boot/vector families (F-V1-006/F-V2-001),
independent held-out corpus + powered KC-2A, and the substrate PRs in
`TRACK_B_PULL_REQUESTS.json`.
