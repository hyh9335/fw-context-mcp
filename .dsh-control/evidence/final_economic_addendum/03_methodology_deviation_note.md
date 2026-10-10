# BENCHMARK METHODOLOGY DEVIATION NOTE (§12)

## Deviation

The frozen methodology (§12) specified 3 repeats per scale run. The actual
benchmark used 1 repeat due to the extreme frontend parse cost (~90s per TU,
~180s per build, ~36 min per scale × 4 scales = ~2.4 hours with 1 repeat;
3 repeats would have taken ~7+ hours).

## Impact Assessment (by fresh economics reviewer)

The deviation does NOT change the binary verdict:

- The claimed 3.35% cumulative improvement at scale 10 is **below the
  measured noise floor of ~11%** (run-to-run variance on independent cold
  BASE builds: REF span=21.4s on mean 188.7s = 11.4%; M6 span=21.1s on
  mean 201.3s = 10.5%).

- The acceptance threshold is 10% cumulative reduction. Even with the
  most favorable noise swing (+1σ toward M6), the measured 3.35% would
  reach at most ~7-8% — still below the 10% threshold.

- The binary verdict ("NOT CLOSED") is robust: the M6 cumulative time
  (2025.0s) is **statistically indistinguishable** from the REF cumulative
  time (2095.2s) given the ~11% noise floor.

## Conclusion

The REPEATS=1 deviation is a methodology imperfection, not a verdict-
invalidating artifact. A re-run with REPEATS=3 would tighten the confidence
interval but would NOT change the "below threshold" conclusion. The
economic Goal remains NOT CLOSED due to the frontend bottleneck (98.2% of
M6 time is libclang parse, not reused across builds).

## Reviewer assessment

Fresh economics reviewer (§24): **PASS** — the evidence is honest and the
conclusion follows from the data despite the repeats deviation.
