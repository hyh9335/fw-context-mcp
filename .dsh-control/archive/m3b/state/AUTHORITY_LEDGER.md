# AUTHORITY_LEDGER — fw-context M3B (cache, NOT truth)

> Cache of the scanner's per-tool classification. Regenerated from
> `.dsh-control/evidence/authority_state.json` each round. NOT a source of
> counters — the scanner is.

## Classification legend

- `V2_NATIVE` — has a v2 read path, no `V2UnsupportedOperation` deferral.
- `V2_ROUTED_LEGACY_EXEC` — raises `V2UnsupportedOperation`, no v2 read (pure legacy exec).
- `PARTIAL_NATIVE_LEGACY_DEFER` — has BOTH a v2 read path AND a deferral branch.
- `UNKNOWN` — unclassified; treated as failing.

## Current classification (HEAD ac76feb)

### V2_NATIVE (3) — already M3B-complete
- find_callers
- find_references
- find_callees_recursive

### PARTIAL_NATIVE_LEGACY_DEFER (2) — has v2 read, still defers on a branch
- lookup_symbol (prefix search, `exact=False`)
- find_all_callers_recursive (recursive case)

### V2_ROUTED_LEGACY_EXEC (24) — pure legacy semantic execution
- search_code, search_bodies, search_content, semantic_search, smart_search
- find_call_path, find_dead_code, find_hotspots, find_indirect_call_sites,
  find_indirect_targets, find_wrapper_callers, trace_data_flow
- explain_symbol, get_file_map, get_source, get_symbol_context, read_file
- get_class_members, get_inheritance_chain, get_method_overrides, get_template_instances
- find_variables, explain_condition, list_conditional_regions

### SPECIALIZED_OPTIONAL (non-core, evidence-backed)
- get_vector_table, compare_variants, group_equivalent_variants

### OPERATIONAL_NON_SEMANTIC (non-core, by design)
- check_dependencies, check_ollama, configure_llm, get_active_build,
  get_environment_status, get_project_info, list_projects, list_variants,
  reindex_file, reset_index
