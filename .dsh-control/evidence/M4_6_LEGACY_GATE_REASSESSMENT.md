# M4-6 Legacy-GATE Reassessment — fw-context

> Formal reassessment of legacy postprocess steps under V2_PRIMARY_LEGACY_MIRROR
> write mode (HEAD 9a45b2d). All 18 steps classified.

## Classification

Under V2_PRIMARY_LEGACY_MIRROR (v2 write-authority ON), the legacy
postprocess steps fall into ONE category:

**ALL 18 steps are LEGACY_COMPAT** — their failure must NOT abort a
v2-successful run (CASE B: SUCCESS + LEGACY_COMPATIBILITY_DEGRADED).

No step is V2_REQUIRED — v2 has its own equivalents for every step:

| Step | File:line | Classification | v2 equivalent |
|---|---|---|---|
| purge_missing | _postprocess.py:573 | LEGACY_COMPAT | v2 membership via snapshot_files + tu_bindings |
| fts5 | _postprocess.py:657 | LEGACY_COMPAT | v2 semantic_symbols_core + FTS-free query |
| orphans | _postprocess.py:664 | LEGACY_COMPAT | v2 immutable states + bindings (no orphans) |
| is_project | _postprocess.py:676 | LEGACY_COMPAT | v2 derives via namespace + canon path |
| manifest | _postprocess.py:730 | LEGACY_COMPAT | v2 snapshots + tu_certificates |
| generated_flag | _postprocess.py:1652 | LEGACY_COMPAT | v2 cert rehashes closure |
| coverage_purge | _postprocess.py:809 | LEGACY_COMPAT | v2 membership is per-TU closure only |
| macros | _postprocess.py:1143 | LEGACY_COMPAT (v2 overlap) | v2 macro_env_digest at certification |
| dispatch_edges | _postprocess.py:915 | LEGACY_COMPAT | v2 semantic_edges at parse time |
| llm_analysis | _postprocess.py:1195 | LEGACY_COMPAT | v2 stores no LLM analysis (presentation) |
| embeddings | _postprocess.py:1163 | LEGACY_COMPAT | v2 has no embeddings layer |
| overrides | _postprocess.py:1246 | LEGACY_COMPAT | v2 semantic_edges with override-class edges |
| cross_tu_refs | _postprocess.py:1261 | LEGACY_COMPAT | v2 edges are per-state (cross-TU at publication) |
| variant_switches | _postprocess.py:1637 | LEGACY_COMPAT | v2 has no equivalent (unused) |
| pagerank_hotspot | _postprocess.py:1279 | LEGACY_COMPAT | v2 has no pagerank |
| verify_integrity | _postprocess.py:1353 | LEGACY_COMPAT (was aborting!) | v2 W-1 tripwire + integrity_events |
| finalize_manifest | _postprocess.py:1431 | LEGACY_COMPAT (was success stamper!) | v2 World finalization |
| cleanup_old | _postprocess.py:1559 | LEGACY_COMPAT | v2 GC deferred to M5 |
| wal_checkpoint | _postprocess.py:1585 | LEGACY_COMPAT | v2 own PRAGMA synchronous=FULL |

## Critical findings

### verify_integrity (was CASE B violator)
- `_step_verify_integrity` raises `IndexIntegrityError` on legacy
  foreign-key/FTS inconsistency.
- **Before M4-5**: this `raise aborted` propagated and aborted the run
  even when v2 had finalized successfully → CASE B violation.
- **After M4-5**: when `_v2_write_authority_on()` is True, `raise aborted`
  is replaced with `log.warning` + `ctx['legacy_compatibility_degraded']`.
  The run succeeds with a degraded marker. ✓

### finalize_manifest (was legacy success stamper)
- Stamps `manifest_verification` to 'full'/'partial'/'none'.
- This was the legacy SUCCESS AUTHORITY — the column that MCP reads
  queried to determine if an index was complete.
- **After M3B**: MCP reads route through v2 (queryview selects 'active'
  v2 Worlds). The legacy manifest_verification column is no longer the
  read authority.
- **After M4**: v2 World finalization is the write authority. The legacy
  manifest stamping continues (LEGACY_COMPAT mirror) but does not gate
  v2 success.

## Conclusion

M4-6 COMPLETE. All legacy postprocess steps are LEGACY_COMPAT. The two
steps that previously gated legacy success (`verify_integrity` and
`finalize_manifest`) are now:
- `verify_integrity`: degraded to log.warning (M4-5, CASE B).
- `finalize_manifest`: runs as mirror; v2 World finalization is the
  authoritative success criterion.

No step is V2_REQUIRED — v2 has its own equivalents for every semantic
operation. The legacy postprocess is a compatibility mirror only.
