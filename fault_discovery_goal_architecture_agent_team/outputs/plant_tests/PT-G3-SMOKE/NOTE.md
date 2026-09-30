# PT-G3-SMOKE — intended smoke shape (design stub)

Intended smoke shape: a paired reachability-negative fixture — (a) a
compile_commands-only mini project (no linker script, no ENTRY()) and (b) the
same mini project plus a minimal linker script naming ENTRY(); the probe runs
a dead-code/unreachability query against a function reachable only from a
never-called init path. Expected behavior under test (G3, F-V1-005): the
compile_commands-only run must carry the root-set attestation caveat
("unreachable from ATTESTED known roots" only; no "dead in final binary"
claim), while the linker-script run demonstrates the stronger attestation
pattern. Pass = every negative output carries the caveat appropriate to its
root-set evidence; fail = any unattested negative or final-binary truth
emitted. Measured and reported, non-blocking for Wave 1 (first dead-code case
admission governs; see DETECTOR_ADMISSION_MATRIX.md row G3).

Status: design_only_not_wave1_load_bearing
