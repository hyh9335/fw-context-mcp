# PT-G2-SMOKE — intended smoke shape (design stub)

Intended smoke shape: a registration-chain fixture in which a driver struct's
callback field is assigned different worker functions under two `#if` branches
plus one generic registration call site, indexed once as a single variant; the
probe asks for the transitive may-target set of the registration site.
Expected behavior under test (G2, lower_bound polarity, F-V1-004): every
may-target is emitted ONLY as an uncertainty-labeled lower bound — the known
may-target set appears with uncertainty labels, the registration chain is
shown as proven edges plus labeled may-targets, and any "complete transitive
callback truth" framing is refused. Pass = no may-target presented as
proven/complete call truth; fail = any proven/complete framing. Measured and
reported, non-blocking for Wave 1 (family-#2 admission review governs; see
DETECTOR_ADMISSION_MATRIX.md row G2).

Status: design_only_not_wave1_load_bearing
