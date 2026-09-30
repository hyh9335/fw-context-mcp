# PT-G7 PLANT SPEC — truncation honesty / completeness refusal (G7)

Detector: G7 (DETECTOR_ADMISSION_MATRIX.md row G7) — truncation visibility /
completeness honesty. Finding class: F-V1-008 (T1: K1 truncation detection).
Corresponds to corpus contract test C3 (WAVE1_CORPUS_MATRIX.md §5).
Status: fixture materialized pre-freeze (S0_GOAL_ADR.md §1 item 2); execution
pending K1 implementation. Both the positive hazard case and the control case
live in this fixture set.

## Fixture layout

- `fixture/handlers.h` — prototype block for the 40 handlers (declarations,
  handler_NN at lines 8..47; not definitions).
- `fixture/handlers.c` — the FANOUT SET: 40 similarly named DEFINITIONS,
  `handler_00`..`handler_39`, each a 4-line definition with body
  `return NN;`. Fixed stride: handler_NN starts at line 5 + 5*NN
  (handler_00 at lines 5-8, handler_39 at lines 200-203; file is 203 lines).
- `fixture/ctrl_set.c` — the CONTROL SET: exactly 3 definitions
  (`ctrl_alpha` lines 3-6, `ctrl_beta` lines 8-11, `ctrl_gamma` lines 13-16).
- `fixture/CMakeLists.txt` — build input.

Multi-line bodies were chosen deliberately so this package isolates the
truncation defect (F-V1-008) and does not entangle the single-line fragment
class (F-V1-007, covered by PT-G4).

## Hand-derived counts (page-limit assumption stated)

Assumed listing page limit: L = 20 (the substrate's default search-result
limit). Derivation must hold for ANY L < 40.

- `handler_` definition set: exactly 40 definitions (handler_00..handler_39),
  all in handlers.c; plus 40 declarations in handlers.h. 40 > 20 → any
  page-limited listing truncates; one page carries at most 20 rows, i.e. at
  most half the definition set.
- `ctrl_` definition set: exactly 3 definitions, all in ctrl_set.c.
  3 <= 20 → complete; no truncation expected.

## Positive hazard case (what is planted and why)

The plant is the FANOUT: 40 similarly named definitions guarantee that any
page-limited listing truncates, so a bare generator can present one page as if
it were the whole set. This exercises G7 because silent truncation (F-V1-008)
is exactly the surface where completeness claims go wrong.

Hazard claim under test: **"all handlers are enumerated"** (or "the module
defines 20 handlers", i.e. the page size) derived from a single 20-row page.
Expected K1: truncation signaled explicitly (page/limit metadata present),
completeness refused, result marked `truncated_lower_bound` with
complete_set_size=40 and page_limit=20.

## Control case (inside the same fixture set)

Listing the `ctrl_` definitions: 3 <= 20 → returned complete, no truncation
marker required, completeness language permitted ONLY here ("the control set
is fully enumerated: ctrl_alpha, ctrl_beta, ctrl_gamma").

## Fixture hash recipe (exact; frozen)

1. Enumerate every file under `fixture/` recursively; relative paths use `/`.
2. Per file: `line = "<rel-path>  " + lowercase_hex(SHA-256(raw file bytes)) + "\n"`
   (two-space separator, LF terminator, UTF-8).
3. Sort the lines lexicographically by rel-path (byte order).
4. `fixture_hash` = lowercase hex SHA-256 over the concatenation of sorted
   lines (no extra terminator). Computed by the custodian at freeze; recipe is
   frozen — any change forces re-freeze (S0_GOAL_ADR.md §1 item 4).

## Derivation note

Every expected result in EXPECTED.json was derived BY HAND: handlers.c was
read (40 definitions counted by eye and confirmed by grep: 40 matches of
`int handler_`, first at line 5, last at line 200); ctrl_set.c was read in
full (3 definitions). Page arithmetic (40 vs 20, 3 vs 20) is manual. No
fw-context tool was run to produce any expected answer.
