# M9 COMPLETION FORMULA

Correctness: `FRONTEND_FALSE_REUSE=0`, `CERTIFICATE_FALSE_ACCEPT=0`, `GT_PARITY_FAILURES=0`, all retained M6/M6.5/M7 regressions pass.

Production: eligible normal indexing can use certificate-gated frontend reuse; ineligible normal indexing safely full-parses; rollback/disable is clean; no M6/M7 authority regression.

Positive-control workload: frontend parse count/time and total wall time materially improve after certificate overhead, with exact semantic/query parity. Thresholds must be frozen before final measurement.

Negative-control FreeRTOS/global-config: false reuse zero, safe misses, overhead below frozen threshold; near-zero hit rate allowed.

Economic rule: `certificate_cost < avoided_frontend_cost` on enabled workloads.

Final review: Fresh Safety Verifier PASS, Fresh Economics Verifier PASS, Fresh Guardian ACCEPT, exact HEAD binding, final hard gate exit 0.
