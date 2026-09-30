# PT-G6 PLANT SPEC — exact-lookup no-substitution (G6)

Detector: G6 (DETECTOR_ADMISSION_MATRIX.md row G6) — exact-lookup
no-substitution (`_fallback` rejection). Finding class: F-V2-002.
Corresponds to corpus contract test C2 (WAVE1_CORPUS_MATRIX.md §5).
Status: fixture materialized pre-freeze (S0_GOAL_ADR.md §1 item 2); execution
pending K1 implementation. Both the positive hazard case and the control case
live in this fixture set.

## Fixture layout

- `fixture/sensor_api.h` — config + API header: SCALE=10 (line 8),
  CAL_V2=3 (line 9); prototypes of the three real symbols (lines 11-13).
- `fixture/sensor_impl.c` — defines exactly three real symbols:
  `sensor_read` (lines 7-10), `sensor_read_scaled` (lines 12-15),
  `sensor_read_scaled_v2` (lines 17-20).
- `fixture/main.c` — control caller: calls `sensor_read_scaled(2)` (line 6).
- `fixture/CMakeLists.txt` — build input.

## The absent target (hand-verified)

`sensor_read_scaled_missing` occurs ZERO times anywhere under `fixture/`
(verified by reading every fixture file and by grep after deliberately
removing earlier comment mentions; the name appears only in this PLANT_SPEC
and EXPECTED.json, outside the fixture). The two near-miss shapes are real:
`sensor_read` (shorter prefix) and `sensor_read_scaled_v2` (longer, shares the
prefix `sensor_read_scaled`), plus the exact-prefix sibling
`sensor_read_scaled` itself.

## Positive hazard case (what is planted and why)

The plant is the ABSENCE: a query for `sensor_read_scaled_missing` has no
exact answer, while three near-misses exist. This exercises G6 because the
substrate's fallback/suggestion behavior (F-V2-002) can serve a near-match row
(`_fallback` / `_did_you_mean`) that a bare generator would silently explain
as if it were the requested symbol.

Hazard claim under test: **"sensor_read_scaled_missing is defined in
sensor_impl.c"** (or any explanation of the missing target's behavior built on
a substituted near-match). Expected K1: reject any `_fallback`/
suggestion-served row; the answer is explicit absence plus the near-miss list
{sensor_read, sensor_read_scaled, sensor_read_scaled_v2} labeled NON-EVIDENCE.

## Control case (inside the same fixture set)

Exact query for `sensor_read_scaled`: resolves with exact identity — exact
name, exactly one definition, sensor_impl.c lines 12-15, body
`return (ch + 1) * SCALE;` — verified against the fixture by hand. Must be
served normally (no absence, no fallback rejection) and must NOT return
`sensor_read_scaled_v2` as the definition row under exact-match semantics.

## Fixture hash recipe (exact; frozen)

1. Enumerate every file under `fixture/` recursively; relative paths use `/`.
2. Per file: `line = "<rel-path>  " + lowercase_hex(SHA-256(raw file bytes)) + "\n"`
   (two-space separator, LF terminator, UTF-8).
3. Sort the lines lexicographically by rel-path (byte order).
4. `fixture_hash` = lowercase hex SHA-256 over the concatenation of sorted
   lines (no extra terminator). Computed by the custodian at freeze; recipe is
   frozen — any change forces re-freeze (S0_GOAL_ADR.md §1 item 4).

## Derivation note

Every expected result in EXPECTED.json was derived BY HAND: every fixture file
was read in full; the absence of the target name was established by reading
and grepping the fixture (after removing comment mentions); definition extents
were read off the numbered file contents. No fw-context tool was run to
produce any expected answer.
