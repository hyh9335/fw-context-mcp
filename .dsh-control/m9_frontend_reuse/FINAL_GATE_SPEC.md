# FINAL HARD GATE SPEC

Create `gates/run_m9_final_gate.py`.

Require: compact M6/M6.5/M7 regressions PASS; certificate dependency coverage complete; mutation suite PASS; frontend false reuse 0; certificate false accept 0; GT parity failures 0; positive-control correctness PASS; FreeRTOS negative-control correctness PASS; workload probe evaluated; real production indexing path used; no M6/M7 authority regression; certificate overhead measured; positive-control frontend parse reduction PASS; positive-control total wall-time benefit PASS or LIMITED policy explicitly satisfied; negative-control overhead PASS; fresh Safety Verifier PASS; fresh Economics Verifier PASS; fresh Guardian ACCEPT; reviewer HEAD == final HEAD; unresolved blocking defects 0.

Allowed verdicts:
- `M9_CERTIFICATE_GATED_FRONTEND_REUSE_COMPLETE`
- `M9_LIMITED_BUT_PRODUCTION_WORTHY`
- `M9_REJECTED_NO_NET_VALUE`
- `M9_BLOCKED_HUMAN_DECISION`
