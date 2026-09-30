# Validation Findings Seed

Re-open `.fw-validation/FINDINGS.json` and source before relying on this summary.

Context/build truth:
- F-V1-001 multi-define macro observability/consistency issue.
- F-V1-003 variants share mutable compile_commands artifact; last variant can overwrite build truth.
- G03 condition context selection varied across runs.

Source completeness:
- F-V1-007 single-line definitions store empty source; F-V1-002 appears to be an instance.

Callbacks:
- F-V1-004 direct pointers resolve, transitive registration flow does not.

Reachability:
- F-V1-005 compile_commands-only projects can lack roots/linker evidence and mislabel live entry chain graph_unreachable.

Vectors:
- F-V1-006 slot0 data symbol omitted.
- F-V2-001 standard CMSIS6 C-form vector table falsely reported absent.

Query contract:
- F-V2-002 exact=True may return relaxed fallback.
- F-V1-008 result cap <=100 with no pagination.

Robustness:
- F-V4-001 watcher daemon assertion during mutation.

Coverage gaps:
- FreeRTOS QEMU/linker/map optional case not run;
- current campaign did not rerun Windows→WSL public launch-path;
- some Windows golden cells blocked by missing toolchain;
- large-project mutations less complete than golden mutations.
