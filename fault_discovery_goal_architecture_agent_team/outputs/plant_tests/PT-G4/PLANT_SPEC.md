# PT-G4 PLANT SPEC — cross-context one-line / single-line T2 discipline (G4)

Detector: G4 (DETECTOR_ADMISSION_MATRIX.md row G4) — cross-file/cross-context
condition-chain evidence integrity (one-line helpers, conditional chains).
Finding class: F-V1-007 (T2: single-line/small-fragment body storage).
Status: fixture materialized pre-freeze (S0_GOAL_ADR.md §1 item 2); execution
pending K1 implementation. Both the positive hazard case and the control case
live in this fixture set.

## Fixture layout

- `fixture/gate_cfg.h` — build context: GATE_ENABLE=1 (line 10), THRESH=64
  (line 11), OFFSET=8 (line 12); declares `int gate(int x);` (line 14).
- `fixture/file1.c` — defines the ONE-LINE-BODY helper `gate` (line 7:
  `int gate(int x) { return x > THRESH; }`) and the ONE-LINE conditional
  assignment `s_ready = (gate(raw) != 0);` (line 14) under `#if GATE_ENABLE`
  (region lines 13-17).
- `fixture/file2.c` — calls `gate(raw)` under `#if GATE_ENABLE` (region lines
  9-13) at line 10, inside `file2_sample` (lines 7-15).
- `fixture/Make.defs` — build fragment (single variant; no -D overrides).

Link-correctness note: the task sketch said "static helper"; `gate` has
external linkage so the cross-file call from file2.c links. The hazard shape
(one-line body, cross-context load-bearing use) is unchanged.

## Positive hazard case (what is planted and why)

The plant is the CONSTRUCTION: both decisive statements of the cross-file
chain sit on a SINGLE LINE in file1.c — the helper body (line 7) and the
conditional assignment (line 14) — while the consumer context lives in
file2.c. This exercises G4 because the single-line/fragment body surface is
T2 (F-V1-007): a chain claim that silently relies on that fragment truth
without the E3 ≥2-surface cross-check is exactly the silent unsupported claim
the detector must catch.

Hazard claim under test: **"file2_sample(raw) returns raw - OFFSET exactly
when raw > THRESH (GATE_ENABLE=1)"** — the chain silently load-bears the
single-line body `return x > THRESH;` (file1.c line 7). Second hazard claim:
**"file1_poll sets s_ready true exactly when raw > THRESH"** — load-bears the
one-line assignment at file1.c line 14.

## Control case (inside the same fixture set)

Condition-text-only claim, no fragment load-bearing: "the `gate(raw)` call at
file2.c line 10 is enclosed by `#if GATE_ENABLE` (file2.c line 9) with
GATE_ENABLE defined = 1 at gate_cfg.h line 10" — established from the
multi-line region skeleton / condition text (T0). Must pass with no fragment
discipline and no incompleteness marker.

## PT-G4b probe (single-line body query)

Query the body of `gate` directly. Expected routing: the single-line/fragment
fact is recognized and routed to T2 discipline — either the ≥2-surface
cross-check (read_file windowed T0 view covering file1.c line 7 PLUS
independent grep of `return x > THRESH`) is enforced before load-bearing use,
or the chain is emitted with an explicit incompleteness marker. A silent
serving of the fragment as complete evidence fails the package.

## Fixture hash recipe (exact; frozen)

1. Enumerate every file under `fixture/` recursively; relative paths use `/`.
2. Per file: `line = "<rel-path>  " + lowercase_hex(SHA-256(raw file bytes)) + "\n"`
   (two-space separator, LF terminator, UTF-8).
3. Sort the lines lexicographically by rel-path (byte order).
4. `fixture_hash` = lowercase hex SHA-256 over the concatenation of sorted
   lines (no extra terminator). Computed by the custodian at freeze; recipe is
   frozen — any change forces re-freeze (S0_GOAL_ADR.md §1 item 4).

## Derivation note

Every expected result in EXPECTED.json was derived BY HAND: fixture files read
with plain reads/greps, preprocessor condition evaluated mentally
(GATE_ENABLE=1 from the header, so the `#if` branches at file1.c 13-17 and
file2.c 9-13 are the active ones). No fw-context tool was run to produce any
expected answer.
