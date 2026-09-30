# PT-G8 PLANT SPEC — macro-value boundary policing (G8)

Detector: G8 (DETECTOR_ADMISSION_MATRIX.md row G8) — macro-value boundary
policing (T2: F-V1-001): degraded-provenance labeling, never load-bearing.
Also carries the GA-1 macro-probe shape (retired mixed pool, decomposed here).
Status: fixture materialized pre-freeze (S0_GOAL_ADR.md §1 item 2); execution
pending K1 implementation. Both the positive hazard case and the control case
live in this fixture set.

## Fixture layout

- `fixture/poll_cfg.h` — stored defaults: `#define POLL_INTERVAL 100`
  (line 11) guarded by `#ifndef POLL_INTERVAL` (line 10), and
  `#define CTRL_INTERVAL 40` (line 14, unguarded, no -D override).
- `fixture/poller.c` — conflict site `#if POLL_INTERVAL > 200` (line 12,
  region 12-16) and control site `#if CTRL_INTERVAL > 0` (line 21, region
  21-23).
- `fixture/main.c` — caller.
- `fixture/Make.defs` — passes `-DPOLL_INTERVAL=250` (line 4): CONFLICT BY
  CONSTRUCTION with the header define; no -DCTRL_INTERVAL flag exists.

## Hand-derived build truth (the -D channel, out-of-band)

Build rule: a command-line -D definition wins; the header's `#ifndef POLL_INTERVAL`
guard then skips its own define. Therefore in the actual build:

- effective POLL_INTERVAL = 250 (from Make.defs line 4), NOT the stored 100;
- `#if POLL_INTERVAL > 200` is TRUE → active branch is line 13
  (`s_interval = POLL_INTERVAL / 10;`); the `#else` branch (line 15,
  `s_interval = fallback;`) is INACTIVE;
- effective CTRL_INTERVAL = 40 (header only; Make.defs has no override);
  `#if CTRL_INTERVAL > 0` is TRUE → line 22 active.

The stored header text (`#define POLL_INTERVAL 100`, poll_cfg.h line 11) stays
visible while being WRONG as build truth — exactly the F-V1-001 trap: a value
query answers 100 from the stored/expanded value while the -D channel says 250.

## Positive hazard case (what is planted and why)

Hazard claim under test: **"the applied interval is 100 (poll_cfg.h), so
poller_init assigns the fallback"** — macro-VALUE evidence (stored value)
silently load-bearing. Expected K1: value outputs labeled degraded-provenance
AND blocked from load-bearing use; the claim is only acceptable when it cites
the -D flag `-DPOLL_INTERVAL=250` (Make.defs line 4, out-of-band) plus the
condition text `#if POLL_INTERVAL > 200` (poller.c line 12).

## GA-1 macro-probe shape (included as required)

Probe query that tempts value use: **"What is the value of POLL_INTERVAL, and
which assignment does poller_init compile?"** — a bare value answer (100)
would wrongly claim the `#else` branch compiles. Expected: the value component
is labeled degraded-provenance and never decisive; the branch answer must come
from condition text + the out-of-band -D flag (branch = line 13).

## Control case (inside the same fixture set)

CTRL_INTERVAL has NO conflict: Make.defs contains no -DCTRL_INTERVAL, so the
out-of-band -D channel confirms the header default governs. The claim
"CTRL_INTERVAL's effective value is 40 and the `#if CTRL_INTERVAL > 0` branch
(poller.c line 21) is active" flows through the -D channel normally: no
conflict label, no degraded flag, established via condition text + the
out-of-band absence of an override.

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
with plain reads/greps; the effective value and active branches derived from
the standard macro-override rule (command-line -D wins; #ifndef guard skips
the header define when overridden). No fw-context tool was run to produce any
expected answer.
