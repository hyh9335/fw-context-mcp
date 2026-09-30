# C6 PAIRING NOTE — PRESENTATION PROTOCOL (custodian-held; NOT solver-visible)

Scope stamp: corpus cases C4/C6; goal items 1–5; lineage B; schema freeze `wave1-design-gate-1`.

## 1. What C6 is
C6 is the POST-FIX snapshot (apache/nuttx @ `67ed036227b14bbab01b63801bfd7e218dfd6107`) of the exact
code that C4 presents PRE-FIX (`526ba1ab343a00b2e1801e089392eed64758b820`). Same freeze recipe, same
lineage B, same key files, same pinned build context (CONFIG_DISABLE_ENVIRON=y).

## 2. Presentation protocol
1. The C4 and C6 source snapshots are handed to the solver as TWO UNLABELED snapshots, "A" and "B".
2. A/B order is randomized by the custodian at freeze time; the mapping (A→SHA, B→SHA) is recorded
   ONLY in the custodian-held freeze manifest, never in solver-visible material.
3. The solver must adjudicate each snapshot INDEPENDENTLY: no verdict may depend on the other
   snapshot's verdict; no shared conclusions across snapshots.
4. The solver is NOT told that the two snapshots are related, which one is pre-fix, or which is
   post-fix. CASE_DESIGN-style briefs given to the solver describe only the task, not the pairing.

## 3. Custody separation statement
- Oracle holder (custodian) ≠ solving agent. FAULT_ORACLE.json (C4 and C6), SOURCE_LOCK.json (both),
  this note, and every PLANT_DIFF.md are custodian-only; K3 MUST exclude them from solver context.
- The scorer is a fresh verifier context that receives the frozen outputs plus the oracles only
  AFTER solving completes; the solver never sees oracle content at any point in the round.
- The freeze manifest records SHA-256 hashes of every oracle file — oracle hash visible, oracle
  content hidden — per CORPUS_LOCK_PLAN.md §2/§4.
- Any custody breach voids the round (S0_GOAL_ADR.md §1/§4; WAVE1_CORPUS_MATRIX.md §2).
