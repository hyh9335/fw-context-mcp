# Oracles

O1 Cross-platform semantic parity:
same fixture -> canonical semantic outputs match Windows vs WSL, excluding only volatile paths/timings/row IDs.

O2 Incremental/full:
canonical(incremental) == canonical(clean full).

O3 Interprocess lock:
two independent writers cannot enter protected writer critical section simultaneously.

O4 Crash recovery:
lock releases after holder crash.

O5 Process identity:
stale/reused PID marker never authorizes killing unrelated process.

O6 Real stdio:
actual subprocess stdio only; in-memory is insufficient.

O7 Override truth:
Clang relationship/fixture ground truth; declaration-only override must not vanish.

O8 Linker truth:
KEEP/GC linker output is independent oracle.

O9 Old schema:
real historical DB or historical generator.

O10 Docs behavior:
claims must match actual behavior.

O11 Issue ledger:
no discovered issue missing final disposition at release.
