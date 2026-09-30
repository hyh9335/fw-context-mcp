# PT-G9-SMOKE — intended smoke shape (design stub)

Intended smoke shape: a two-form vector fixture — (a) an assembly-form
startup table (.S file with `.word handler` slots) and (b) a C-form array
fixture (an array-of-function-pointers table, the F-V2-001 boundary shape);
the probe asks which interrupts are serviced. Expected behavior under test
(G9, F-V1-006 / F-V2-001): assembly-form presence claims are emitted only
with form attestation plus >=2-surface E5 corroboration, and C-form absence
claims are refused outright (never load-bearing from the vector surface).
Pass = no uncorroborated assembly presence and no C-form absence emitted;
fail = either. Measured and reported, non-blocking for Wave 1 (first vector
case admission governs; see DETECTOR_ADMISSION_MATRIX.md row G9).

Status: design_only_not_wave1_load_bearing
