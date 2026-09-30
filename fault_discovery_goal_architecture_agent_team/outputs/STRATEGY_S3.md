# STRATEGY S3 — Dual-Track, Use-Case-Driven Hardening ("build the Agent and fix the kernel, both gated by measurement")

**Thesis.** Split the effort: Track A builds the Agent end-to-end on proven surfaces (as S2 but starting from the narrowest objective case set); Track B hardens fw-context in strict S-metric priority order, where every fix must carry a fault/rule-discovery impact statement (EVIDENCE_RULES) and a Level-2 miss attribution to be admitted. Neither track is trusted to define success for itself.

## Architecture
- Track A (Agent): as S2's C0–C8 decomposition and G1–G7 guards, but Track A's first corpus is the two scenarios usable-as-is (A6: 07 exact-lookup-ambiguity, 08 large-fanout-rule) plus PFC-1 — the minimum set that exercises the pipeline without touching a defective surface.
- Track B (substrate): two-tier trust model. Tier-1 proven surfaces: trusted-with-guards. Tier-2 quarantined surfaces: bare-generator variants (F-V1-003), C-form vectors (F-V2-001), macro-value queries (F-V1-001), single-line body search (F-V1-007), transitive callback targets (F-V1-004), compile_commands-only dead-code roots (F-V1-005). Quarantine ends only when a fix lands AND a Level-2 miss attribution or corpus need justifies the unquarantine.
- Independent verification channel (A7) shared by both tracks as the arbiter of truth.

## Work sequence
1. Week-0 parallel start: Track A skeleton + harness + minimal corpus; Track B diagnosis spike on F-V1-003 (the only BLOCKING-class finding) and the raw-evidence export (A7-Q3 interface-decay quick win: macros + single-line bodies are already stored correctly).
2. Track A: first cases → S measurement → corpus expansion in rule-type order (A3 MVP verdict: R1 + R4 + R2-lower-bound + R3-with-provenance + R6-decomposed + R7-scoped + R8-sync).
3. Track B: fixes admitted in S-attribution order only (expected: F-V1-003, F-V2-001, export; expected-deferred: F-V1-002/004/005/006/008, F-V2-002, F-V4-001 unless S says otherwise).
4. Monthly gate: S-metric re-measurement reallocates effort; any Track B item without an S-attributed miss is dropped.

## fw-context role
Two-tier trust (proven vs quarantined); Track B owns product repairs; Track A owns the wrapper.

## Mandatory fixes / deferred
Mandatory (Track B): F-V1-003 or permanent single-context rule; raw-evidence export as first candidate. Gated: F-V2-001 before vector-class corpus. Deferred: the rest pending S attribution.

## First corpus
Scenarios 07 + 08 + PFC-1, then A6's full planted-fixture corpus as Track A matures.

## Success / kill criteria
Success: A1-P1 rubric met AND S ≥ 80% AND every Track B fix traces to a measured miss. Kill: track-split coordination failure (measured: either track idles >1 cycle waiting on the other); Track B items failing the impact test are cut (anti-completion-bias lever); A7 KC-1..KC-4 apply to both tracks.

## Local-optimum risk
Moderate-low on bias (the S-gate is the structural anti-sunk-cost mechanism), moderate on execution (dual-track coordination cost; risk that Track B regresses into polishing under schedule pressure — mitigated by the drop rule); risk that the narrow first corpus under-exercises the pipeline (mitigated by A6's planned expansion).

## Evidence base
F-R0-02..05, F-R0-13, C-04; R1: A1-F3/P2, A3 (rule-type MVP verdicts), A4 (BLOCKING classification), A5 (guards, capability tiers), A6 (S metric, scenario verdicts — 07/08 usable-as-is), A7 (interface-decay class, export proposal), A8 (primitives spec, defer variant/compare).
