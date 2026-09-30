# Known Wave-1 Limitations

Recorded per the frozen design gate: limitations are declared BEFORE acceptance
measurement, with their mitigation and their blast radius. None of these is a
post-hoc "expected limitation" excuse; each one is a measured property of this
wave-1 build, and the acceptance verdict
(`WAVE1_MVP_ACCEPTED_WITH_NONBLOCKING_LIMITATIONS`) already includes them.

## L1 — C4/C6 fixture is structure-faithful, not byte-exact

The upstream NuttX material (pre-fix `526ba1ab…`, fix `67ed0362…`, PR #14365)
could not be staged byte-exactly: every byte-exact egress path was blocked
(git, curl, pip, WSL; `web_fetch` middle-prunes long bodies, ~>2.5 KB), so the
fixture was reconstructed from PR/commit `.diff` hunks (commit-diff chain
`96abb51 → 6cac6c5 → 5c853cd` + PR #14365 anchors) with unobservable prose
replaced by elision markers (`[I7-ELIDED …]`, no comment delimiters).
Upstream blob-hash verification: **ATTEMPTED-BLOCKED** (receipt:
`rounds/I7_C4C6_acquisition/ACQUISITION_RECEIPT.json`, fidelity
`STRUCTURE-FAITHFUL DERIVED FIXTURE`). Guard anchors, signature/body, and the
build-listing conditionals are verified; byte-identity is not claimed anywhere.
*Mitigation*: elision receipts + guard-anchor cross-checks; the A/B adjudication
depends on structure, not bytes.

## L2 — K1 transport is in-process, not over the MCP wire

The Agent's K1 gateway invokes `fw_context_mcp` handlers **in-process**
(`call_substrate`), because the environment blocks long-lived server spawning
in the sandbox path. The MCP-wire path (stdio/HTTP) was validated earlier
(W0 baselines) but is not the wave-1 transport. *Blast radius*: none for
discipline (identical handlers), but wave-1 did not re-verify the wire path.

## L3 — clang channel uses the native Windows clang (WSL blocked)

FR-8's independent preprocessor channel planned a POSIX clang under WSL; WSL is
blocked here, so the channel runs native clang with `-I`/`-D` equivalently.
Recorded as a channel substitution in every clang artifact
(`wsl_unavailable_note`).

## L4 — Stale-index-lock fail-closed incident (measured, fixed)

Dead-PID `index.lock`/`write.lock` files under the index home made on-demand
handler indexing **fail closed** (handlers returned empty views), which an
unguarded empty-lookup path could have minted as ABSENCE evidence. Measured:
264 stale locks removed. Fixes shipped in wave-1: (a) `clear_stale_index_locks()`
(dead-PID-only cleanup, fail-safe) run before every round; (b) the
**absence-soundness gate** — an absence claim now requires a file-coverage
proof (`read_file` on the target file); a coverage failure is a first-class
`E-COVERAGE` refusal and the loop abstains instead of claiming absence
(error taxonomy extended). This incident is also the I7 lesson recorded in
`rounds/I7_C4C6_RECEIPT.json`.

## L5 — Reduced KC-2A pool (prereg deviation)

KC-2A ran bounded (N=12, pool = wave-1 corpus) instead of the preregistered
independent held-out corpus with N ≥ 20. Recorded as UNDERPOWERED with a
deviation receipt; KC-2A never gates acceptance. See `KC2A_RESULT.md`.

## L6 — Track-B substrate findings are handed off, not repaired

Per the wave-1 isolation constraint, no substrate repair shipped: 8 findings
(F-V1-001/003/004/005/007/008, F-V2-001/002) are pre-drafted as PRs in
`TRACK_B_PULL_REQUESTS.json` (status NOT_ACTIONED_IN_WAVE1). Where a finding
touches a wave-1 load-bearing path, the agent compensates inside its own layer
(build-snapshot pinning for F-V1-003, G6 fallback rejection for F-V2-002, T2
discipline for F-V1-007, truncation marking for F-V1-008).

## L7 — Comment-delimiter elision hazard (fixture-staging lesson)

The first I7 staging embedded `/* … */`-delimited elision markers inside an
open block comment; the stray `*/` closed the comment early and turned ~32
prose lines into code garbage, silently emptying the index's symbol table while
conditionals stayed recorded — caught only by the A/B asymmetry check. Fix:
delimiters are banned inside staged markers. Recorded as a staging-rule lesson
in `rounds/I7_C4C6_RECEIPT.json`.

## L8 — No LLM in the solving loop

K2 is deterministic by design (no LLM anywhere in solving, K1, or refutation).
This is a design boundary, not an accident, but it bounds what wave-1 can
discover: mechanism hypotheses come from the frozen family library only.
