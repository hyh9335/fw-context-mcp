# store_v2_ref — verbatim D5a experiment oracle (TEST TREE ONLY)

**Provenance (byte-fidelity):**

| file | copied from | SHA-256 |
|---|---|---|
| `key_candidates.py` | `multi_version_index_review/phase2_experiments/key_candidates.py` | `63517C9C7956937BBB140A82080E6197A24F4EAB80BD95D57278E8859CF612C4` |
| `key_candidates_s2copy_d5a.py` | `multi_version_index_review/phase3_experiments/p3p1/key_candidates_s2copy_d5a.py` (README-recorded `63517C9C…C4`, verified byte-identical to the S2 original at Slice-0 copy time) | `63517C9C7956937BBB140A82080E6197A24F4EAB80BD95D57278E8859CF612C4` |
| `kb_realchannel.py` | `multi_version_index_review/phase3_experiments/p3p1/kb_realchannel.py` | (see git history of the copy commit) |

**Status:** THROWAWAY experiment code, ported verbatim (unmodified) so the
Slice-0 tests can treat the accepted Phase-2/3 experiment outputs as external
truth (PHASE4 task §23 golden-vector rule). The files are self-contained
(stdlib only) and carry their own honesty contract:

* REAL components: source blobs, argv channel, NCC canonicalization (S2 spec
  rules verbatim), include resolver (text-level mini-preprocessor; fail-closed
  on anything unmodeled), forced-input content legs, DCF c1/c2/c4.
* STUB components (labeled inside, preserved exactly): DCF c5 macro_state
  (scanner map, not `-dM -E`), DCF c6 certification source (caller-supplied
  cert tree), c7 parser_generation formula, SnapshotID formula.

**Rules:**

1. NEVER imported by production code (`src/fw_context_mcp/**`). This is the
   fixture-certification oracle for tests only; the production certification
   source is G7 (`BLOCKS_KB_IMPLEMENTATION`, Phase-4 Slice 1) — see
   `multi_version_index_review/phase4/slice0/SLICE0_G7_SCOPE_ADR.md`.
2. NEVER edited. If the experiments ever produce new accepted vectors, re-copy
   from the experiment directory (fresh provenance note), never patch in place.
3. The product KB-C composition (DECISION L52, `fwctx-l2c/v1`) deliberately
   DIFFERS from this reference's frozen D5a composition
   (`fw-context-dcf-closure/1` over c3+c4+c6): the D5a outputs are the
   mechanics oracle (equality verdicts + byte-level layer digests under the
   frozen composition), while the product ships the DECISION-canonical
   composition. Both facts are recorded per vector in
   `SLICE0_KEY_VECTOR_REPORT.json`.

Self-test (unchanged, from the source experiments):

```
python tests/store_v2_ref/key_candidates.py     # S2 self-tests
python tests/store_v2_ref/kb_realchannel.py     # 24 D5a self-checks
```
