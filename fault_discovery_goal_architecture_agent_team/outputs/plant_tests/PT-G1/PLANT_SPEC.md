# PT-G1 PLANT SPEC — single-variant fence + build-manifest provenance (G1)

Detector: G1 (DETECTOR_ADMISSION_MATRIX.md row G1) — per-variant provenance of
build-context evidence; single-variant fence. Finding class: F-V1-003 (T2:
bare-generator multi-variant truth / cross-variant body claims).
Status: fixture materialized pre-freeze (S0_GOAL_ADR.md §1 item 2); execution
pending K1 implementation. Both the positive hazard case and the control case
live in this fixture set.

## Fixture layout

- `fixture/shared/mode_select.c` — ONE source file compiled into BOTH variants;
  `#if FEATURE` region (lines 10-14) carries the divergent bodies.
- `fixture/variant_a/feat_cfg.h` — variant A context: `#define FEATURE 1`.
- `fixture/variant_b/feat_cfg.h` — variant B context: `#define FEATURE 0`.
- `fixture/variant_a/Make.defs` — variant A build fragment; -D set {VARIANT_TAG=2}.
- `fixture/variant_b/Make.defs` — variant B build fragment; -D set {VARIANT_TAG=3}.

Each variant is indexed SEPARATELY (single variant per index, GA-4 fence):
variant A include path points at `variant_a/`, variant B at `variant_b/`.

## Positive hazard case (what is planted and why)

The plant is the CONSTRUCTION itself: two variants share one source file whose
`#if FEATURE` bodies diverge, while each variant carries its own config header
and -D set. This exercises G1 because the substrate has no in-band per-variant
provenance signal (CURRENT_CAPABILITY_MAP.md bounding facts), so a bare
generator can silently pair variant A's config identity with variant B's body.

Hazard claim under test: **"FEATURE is 1 and `mode_select_path` returns
"mode_b_path""** — variant A's config identity (FEATURE=1,
`variant_a/feat_cfg.h` line 3) paired with variant B's body truth
(`return "mode_b_path";`, active only in variant B). Any cross-variant claim of
this shape must be flagged by the build-manifest diff and blocked; single-
variant claims ("variant A: FEATURE=1, body returns mode_a_path at line 11";
"variant B: FEATURE=0, body returns mode_b_path at line 13") must pass.

## Control case (inside the same fixture set)

Single-variant claims sourced wholly from one variant's manifest + index.
PASS = served normally with variant attribution, no flag, no block.

## PT-G1b planted manifest change (applied at freeze by custodian)

1. Snapshot the declared manifest: per-variant -D sets {VARIANT_TAG=2} (A),
   {VARIANT_TAG=3} (B) as recorded from the Make.defs files.
2. Edit `fixture/variant_b/Make.defs` line 5: `VARIANT_TAG=3` → `VARIANT_TAG=2`;
   re-index variant B.
3. Present the STALE manifest snapshot (VARIANT_TAG=3) as the declared build
   context alongside the re-indexed substrate (indexed defines VARIANT_TAG=2).
Expected: build-manifest diff flags declared {VARIANT_TAG=3} vs indexed
{VARIANT_TAG=2}; build-context claims sourced from the stale manifest are
blocked until the manifest is refreshed.

## Fixture hash recipe (exact; frozen)

1. Enumerate every file under `fixture/` recursively; relative paths use `/`.
2. Per file: `line = "<rel-path>  " + lowercase_hex(SHA-256(raw file bytes)) + "\n"`
   (two-space separator, LF terminator, UTF-8).
3. Sort the lines lexicographically by rel-path (byte order).
4. `fixture_hash` = lowercase hex SHA-256 over the concatenation of sorted
   lines (no extra terminator). Computed by the custodian at freeze; recipe is
   frozen — any change forces re-freeze (S0_GOAL_ADR.md §1 item 4).

## Derivation note

Every expected result in EXPECTED.json was derived BY HAND: the fixture files
were read with plain file reads/greps and standard C preprocessor/build rules
were applied mentally (variant include paths select feat_cfg.h; command-line
-D sets come from the Make.defs). No fw-context tool was run to produce any
expected answer.
