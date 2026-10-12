# WORKLOAD PROBE SPEC

Do not blindly enable certificate work for every project.

Build a cheap probe using safe signals such as number of Builds, per-TU flag/config variation, candidate TU overlap, estimated certificate-equivalent TU ratio, certificate cost, and historical hit rate.

Allowed outcomes: ENABLE, DISABLE, SAMPLE/LEARN.

Evaluate on FreeRTOS/global-config (expected low benefit) and per-file-config workload (expected meaningful benefit).

Wrong ENABLE is tolerable only if safety remains intact and overhead remains under threshold. Wrong HIT is never acceptable.
