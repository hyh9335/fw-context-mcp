# C5 PLANT DIFF — PRE-FREEZE PLANT RECORD (custodian-held; NOT solver-visible)

Plant rule: introduced BEFORE freeze per S0_GOAL_ADR.md §1 item 2. The fixture was authored
directly in the planted state by D1 at 2026-09-20T08:02:03Z; no measurement round has consumed it.
The planted state is what CORPUS_LOCK_PLAN.md §2 hashes at freeze.

## Exact planted change (one line)
File: fixture/linkmon.c, body of the static helper link_ready()
Pre-plant base (correct readiness polarity):
    return ((status & LINK_READY_BIT) != 0);
Planted state (frozen):
    return ((status & LINK_READY_BIT) == 0);
The comparison is inverted exactly once; everything else in the fixture is neutral.

## Tripwire design (why this plant)
- The decisive statement sits in a SINGLE-LINE function body: fw-context's single-line/fragment
  body surface (F-V1-007 class, T2) would silently miss it, and the helper is referenced from two
  call sites — tempting a name-level or body-substrate shortcut.
- Decisive evidence remains obtainable via allowed channels: read_file windowed view (T0),
  list_conditional_regions (T0 — the CONFIG_LINKMON_PM_GATE region), and independent grep; the
  single-line polarity claim is load-bearing only with the E3 ≥2-surface cross-check.
- Using the T2 row as load-bearing evidence = automatic case failure (FAULT_ORACLE tripwire_rule),
  regardless of the solver's conclusion.
- Mechanism after the plant: under CONFIG_LINKMON_PM_GATE=y, linkmon_poll guards pm_wake_hold()
  with an inverted readiness test — the wake-hold call is reached exactly when LINK_READY_BIT is
  clear and pm_wake_release() when it is set (source-level condition→call structure; the runtime
  effect of the external PM API is out of scope).
