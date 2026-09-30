# V2 — public real firmware corpus

Use PUBLIC_LOCK.json; never float versions during the run.

Required:
- Zephyr
- NuttX
- FreeRTOS-Kernel
- CMSIS 6
- RIOT

For each repository:
1. build only the scenarios in public/SCENARIOS.md;
2. record exact build commands and generated compile DB provenance;
3. index a fresh DB;
4. run feature-specific queries;
5. evaluate with the oracle hierarchy;
6. record scale: files/TUs/contexts/DB size/index time;
7. run one no-op reindex if supported;
8. do not treat "index succeeded" as semantic PASS.

Public repo source is read-only except disposable copied-checkout mutation scenarios.

Optional Juliet/TACLE only after all required corpora complete.

Output per-project report + machine result JSON.
