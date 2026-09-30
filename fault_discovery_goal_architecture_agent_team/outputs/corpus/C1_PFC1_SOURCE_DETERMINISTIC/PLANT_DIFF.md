# C1 PLANT DIFF — PRE-FREEZE PLANT RECORD (custodian-held; NOT solver-visible)

Plant rule: introduced BEFORE freeze per S0_GOAL_ADR.md §1 item 2. The fixture was authored
directly in the planted state by D1 at 2026-09-20T08:02:03Z; no measurement round has consumed it.
The planted state is what CORPUS_LOCK_PLAN.md §2 hashes at freeze.

## Exact planted change (one line, one token)
File: fixture/accel_trigger.c, inside accel_trigger_enable()
Pre-plant base (faithful to the upstream-derived structure):
    #if defined(CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD)
        k_work_submit(&accel_work);
    #endif
Planted state (frozen):
    #if defined(CONFIG_ACCEL_TRIGGER_GLOBAL_THREAAD)
        k_work_submit(&accel_work);
    #endif
i.e. the guard token is misspelled (GLOBAL_THREAAD) so the branch is dead under EVERY configuration
(the Kconfig defines CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD only). Every other guard in the slice is
correct, including the handler registration in accel_trigger_init().

## Why this plant satisfies the matrix shape (§2)
- Init/submit pairing becomes inconsistent under a deterministic source condition: arming completes,
  accel_init reports ready, and the handler registration stays live — while the ONLY
  k_work_submit site in the slice is compiled out.
- Discovery requires the cross-file condition chain: Kconfig default (CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD,
  selects ACCEL_TRIGGER) + conditional guard text + reachability of the submit site — not a trivial grep.
- Two-part separation: the source mechanism is provable (T0/T1); runtime occurrence is
  required-but-unavailable and must never be asserted (CASE_DESIGN §2.2).

## Non-goals
No runtime kernel semantics are modeled; the k_* stand-ins in accel_local.h are inert plumbing.
