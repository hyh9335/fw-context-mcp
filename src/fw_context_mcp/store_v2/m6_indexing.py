"""M6 indexing adapter — Phase I I8.

Bridges the Clang fact extractor (full_build_gt.BuildFacts) to the M6
shared-fact store (m6_store.BuildSetStore). This is the heart of Phase I:
it materializes semantic facts from a parsed TU and writes them as shared,
canonical, build-conditioned facts.

Architecture (per M6_ARCHITECTURE_CONTRACT.md):
  Layer 1 (Clang oracle) → full_build_gt.parse_tu + extract_facts_from_tu
  Layer 2 (canonical facts) → canon_* functions from prototype_a
  Layer 3 (BuildSets) → BuildSetStore.intern_buildset / add_fact
  Layer 4 (physical bundles) → handled inside BuildSetStore

This module is SHADOW-ONLY during Phase I. Production query paths still
hit the v2 store until shadow differential testing (§34) proves
QUERY_PARITY_FAILURES == 0.
"""
from __future__ import annotations
import json, hashlib, time
from pathlib import Path
from typing import Iterable

from fw_context_mcp.store_v2.m6_store import BuildSetStore
from fw_context_mcp.store_v2.m6_schema import init_m6_tables

# Re-use the canon functions from the prototype (single source of truth).
# These are the canonical identity functions defined in §1 of the contract.
import sys
_M6_RESEARCH = Path(__file__).resolve().parents[2] / ".dsh-control" / "m6_research"
if str(_M6_RESEARCH) not in sys.path:
    sys.path.insert(0, str(_M6_RESEARCH))

from prototype_a import (
    canon_symbol, canon_function, canon_call, canon_variable,
    canon_type, canon_field, canon_enum_const, canon_inheritance,
)


# ── Canonical fact construction ─────────────────────────────────────────
# These mirror build_canonical_fact_list in prototype_c.py — single source
# of truth for how facts are canonicalized. Every fact has a (canon, kind,
# payload) triple.

def _build_name_to_usr_map(facts) -> dict[str, str]:
    return {fn["name"]: fn["usr"] for fn in facts.functions}


def _build_type_name_to_usr_map(facts) -> dict[str, str]:
    return {tp["name"]: tp["usr"] for tp in facts.types}


def canonicalize_symbols(facts) -> list[tuple[str, str, dict]]:
    """Return [(canon, kind, payload), ...] for every symbol fact."""
    out = []
    for sym in facts.symbols:
        canon = canon_symbol(sym["name"], sym["kind"], sym["usr"])
        out.append((canon, "Symbol", {"name": sym["name"], "kind": sym["kind"],
                                       "usr": sym["usr"],
                                       "is_definition": sym.get("is_definition", False)}))
    return out


def canonicalize_functions(facts) -> list[tuple[str, str, dict]]:
    out = []
    for fn in facts.functions:
        canon = canon_function(fn["name"], fn["usr"], fn.get("signature", ""),
                               fn.get("attributes"), fn.get("storage_class", "none"))
        out.append((canon, "Function", {"name": fn["name"], "usr": fn["usr"],
                                          "signature": fn.get("signature", ""),
                                          "attributes": fn.get("attributes"),
                                          "storage_class": fn.get("storage_class", "none")}))
    return out


def canonicalize_calls(facts, name_to_usr: dict[str, str]) -> list[tuple[str, str, dict]]:
    out = []
    for call in facts.calls:
        caller_usr = call.get("caller_usr") or name_to_usr.get(call["caller"], call["caller"])
        canon = canon_call(caller_usr, call["callee"], call["line"],
                           call.get("callee_usr", ""),
                           call.get("callee_signature", ""),
                           call.get("is_indirect", False))
        out.append((canon, "Call", {"caller_usr": caller_usr,
                                     "callee_name": call["callee"],
                                     "callee_usr": call.get("callee_usr", ""),
                                     "line": call["line"],
                                     "is_indirect": call.get("is_indirect", False)}))
    return out


def canonicalize_variables(facts) -> list[tuple[str, str, dict]]:
    out = []
    for var in facts.variables:
        canon = canon_variable(var["name"], var["usr"], var["type"],
                               var["is_global"], var.get("init_value", ""),
                               var.get("storage_class", "none"),
                               var.get("bitfield_width"))
        out.append((canon, "Variable", {"name": var["name"], "usr": var["usr"],
                                         "type": var["type"],
                                         "is_global": var["is_global"],
                                         "init_value": var.get("init_value", ""),
                                         "storage_class": var.get("storage_class", "none"),
                                         "bitfield_width": var.get("bitfield_width")}))
    return out


def canonicalize_types(facts) -> list[tuple[str, str, dict]]:
    out = []
    for tp in facts.types:
        canon = canon_type(tp["name"], tp["kind"], tp["usr"],
                           tp.get("size", 0), tp.get("field_sig", ""),
                           tp.get("pack_state", "default"))
        out.append((canon, "Type", {"name": tp["name"], "kind": tp["kind"],
                                     "usr": tp["usr"],
                                     "size": tp.get("size", 0),
                                     "field_sig": tp.get("field_sig", ""),
                                     "pack_state": tp.get("pack_state", "default")}))
    return out


def canonicalize_fields(facts, type_name_to_usr: dict[str, str]) -> list[tuple[str, str, dict]]:
    out = []
    for fld in facts.fields:
        type_usr = type_name_to_usr.get(fld["type_name"], "")
        canon = canon_field(type_usr, fld["field_name"], fld["field_type"],
                            fld.get("bitfield_width"))
        out.append((canon, "Field", {"type_usr": type_usr,
                                      "type_name": fld["type_name"],
                                      "field_name": fld["field_name"],
                                      "field_type": fld["field_type"],
                                      "bitfield_width": fld.get("bitfield_width")}))
    return out


def canonicalize_enum_constants(facts, type_name_to_usr: dict[str, str]) -> list[tuple[str, str, dict]]:
    out = []
    for ec in facts.enum_constants:
        enum_usr = type_name_to_usr.get(ec["enum_name"], "")
        canon = canon_enum_const(enum_usr, ec["name"], ec["value"])
        out.append((canon, "EnumConstant", {"enum_usr": enum_usr,
                                             "enum_name": ec["enum_name"],
                                             "name": ec["name"],
                                             "value": ec["value"]}))
    return out


def canonicalize_inheritance(facts) -> list[tuple[str, str, dict]]:
    out = []
    for inh in facts.inheritance:
        canon = canon_inheritance(inh["derived_usr"], inh["base_usr"],
                                   inh.get("access", "public"),
                                   inh.get("is_virtual", False))
        out.append((canon, "Inherits", {"derived_usr": inh["derived_usr"],
                                         "base_usr": inh["base_usr"],
                                         "access": inh.get("access", "public"),
                                         "is_virtual": inh.get("is_virtual", False)}))
    return out


# ── Phase I I4/I5/I6: new fact kinds ──────────────────────────────────

def _hash_str(s: str) -> str:
    """Stable SHA-256 digest for body/file content canonicalization."""
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonicalize_references(facts) -> list[tuple[str, str, dict]]:
    """I5: Reference facts — 6 ref_kinds.

    ref_kind ∈ {call, ref, member, indirect, implicit_construct, macro_use}
    """
    out = []
    for ref in facts.references:
        # The canon key includes src+target+kind+line so that two references
        # at different lines are distinct facts (matches production semantics).
        canon = (f"Reference({ref.get('src_usr','')},"
                 f"{ref.get('ref_kind','ref')},"
                 f"{ref.get('target_usr','')},"
                 f"{ref.get('line',0)})")
        out.append((canon, "Reference", {
            "src_usr": ref.get("src_usr", ""),
            "src_name": ref.get("src_name", ""),
            "ref_kind": ref.get("ref_kind", "ref"),
            "target_usr": ref.get("target_usr", ""),
            "target_name": ref.get("target_name", ""),
            "target_kind": ref.get("target_kind", ""),
            "line": ref.get("line", 0),
            "file": ref.get("file", ""),
        }))
    return out


def canonicalize_conditional_states(facts) -> list[tuple[str, str, dict]]:
    """I6: ConditionalState facts — preprocessor directive + taken state."""
    out = []
    for cs in facts.conditional_states:
        canon = (f"ConditionalState({cs.get('file','')},"
                 f"{cs.get('line',0)},"
                 f"{cs.get('directive','')},"
                 f"{cs.get('taken',-1)})")
        out.append((canon, "ConditionalState", {
            "file": cs.get("file", ""),
            "line": cs.get("line", 0),
            "directive": cs.get("directive", ""),
            "condition": cs.get("condition", ""),
            "taken": cs.get("taken", -1),
        }))
    return out


def canonicalize_bodies(facts) -> list[tuple[str, str, dict]]:
    """I4: Body facts — function body text (canonicalized by content digest).

    Two bodies are the SAME fact iff their text is byte-identical AND they
    belong to the same function (by USR). This enables cross-build sharing
    of unchanged function bodies.
    """
    out = []
    for body in facts.bodies:
        body_text = body.get("body_text", "")
        content_digest = _hash_str(body_text)
        canon = (f"Body({body.get('func_usr','')},"
                 f"{content_digest})")
        out.append((canon, "Body", {
            "func_usr": body.get("func_usr", ""),
            "func_name": body.get("func_name", ""),
            "content_digest": content_digest,
            "start_line": body.get("start_line", 0),
            "end_line": body.get("end_line", 0),
            "file": body.get("file", ""),
            # NOTE: body_text is NOT stored in the canon key — it's stored
            # in the payload and can be large. The content_digest is the
            # canonical identity.
            "body_text": body_text,
        }))
    return out


def canonicalize_file_contents(facts) -> list[tuple[str, str, dict]]:
    """I4: FileContent facts — file content digest (canonical by digest).

    Two FileContent facts are the SAME iff their content digests match.
    This enables cross-build sharing of unchanged files.
    """
    out = []
    for fc in facts.file_contents:
        canon = f"FileContent({fc.get('content_digest','')})"
        out.append((canon, "FileContent", {
            "file_path": fc.get("file_path", ""),
            "content_digest": fc.get("content_digest", ""),
            "line_count": fc.get("line_count", 0),
        }))
    return out


def canonicalize_all_facts(facts) -> list[tuple[str, str, dict]]:
    """Canonicalize every fact kind that Phase R + Phase I support.

    Returns a list of (canon_str, fact_kind, payload_dict).
    """
    name_to_usr = _build_name_to_usr_map(facts)
    type_name_to_usr = _build_type_name_to_usr_map(facts)

    out: list[tuple[str, str, dict]] = []
    out.extend(canonicalize_symbols(facts))
    out.extend(canonicalize_functions(facts))
    out.extend(canonicalize_calls(facts, name_to_usr))
    out.extend(canonicalize_variables(facts))
    out.extend(canonicalize_types(facts))
    out.extend(canonicalize_fields(facts, type_name_to_usr))
    out.extend(canonicalize_enum_constants(facts, type_name_to_usr))
    out.extend(canonicalize_inheritance(facts))
    # Phase I I4/I5/I6
    out.extend(canonicalize_references(facts))
    out.extend(canonicalize_conditional_states(facts))
    out.extend(canonicalize_bodies(facts))
    out.extend(canonicalize_file_contents(facts))
    return out


# ── M6 indexing adapter ────────────────────────────────────────────────

class M6IndexingAdapter:
    """Adapts the Clang fact extractor to the M6 shared-fact store.

    Usage (shadow mode):
        adapter = M6IndexingAdapter(store)
        adapter.index_build_facts(build_name, facts)
        # ... facts are now in the M6 store with BuildSet={build_name}

    The adapter handles:
    - Canonicalization (via canon_* functions)
    - BuildSet interning (BuildSet={build_name} for single-build indexing;
      merge for cross-build sharing)
    - Fact storage (add_fact with merge semantics)
    """

    def __init__(self, store: BuildSetStore):
        self._store = store
        # Per-build fact count cache (for stats)
        self._build_fact_counts: dict[str, int] = {}

    def index_build_facts(self, build_name: str, facts) -> dict:
        """Index all facts from a single Build into the M6 store.

        facts: a full_build_gt.BuildFacts object.

        Returns a stats dict: {facts_indexed, facts_reused, kinds}.
        """
        builds = frozenset([build_name])
        canon_list = canonicalize_all_facts(facts)

        facts_indexed = 0
        facts_reused = 0
        kind_counts: dict[str, int] = {}

        for canon, kind, payload in canon_list:
            # add_fact merges the BuildSet if the fact already exists.
            # If it returns the same fact_id, the fact was reused (already known).
            # If it returns a new fact_id, the fact is new.
            existing_before = self._store._fact_cache.get(canon)
            self._store.add_fact(canon, kind, builds, payload)
            existing_after = self._store._fact_cache.get(canon)

            if existing_before is not None:
                facts_reused += 1
            else:
                facts_indexed += 1
            kind_counts[kind] = kind_counts.get(kind, 0) + 1

        self._build_fact_counts[build_name] = facts_indexed + facts_reused
        return {
            "build_name": build_name,
            "facts_total": facts_indexed + facts_reused,
            "facts_new": facts_indexed,
            "facts_reused": facts_reused,
            "kinds": kind_counts,
        }

    def index_build_facts_batch(self, build_name: str, facts_list: list) -> dict:
        """Index facts from multiple TUs of a single Build.

        facts_list: list of BuildFacts objects, one per TU.
        """
        all_canon: list[tuple[str, str, dict]] = []
        for facts in facts_list:
            all_canon.extend(canonicalize_all_facts(facts))

        # Deduplicate within the same build (same canon → same fact)
        seen = {}
        for canon, kind, payload in all_canon:
            if canon not in seen:
                seen[canon] = (kind, payload)

        builds = frozenset([build_name])
        facts_indexed = 0
        facts_reused = 0
        for canon, (kind, payload) in seen.items():
            existing_before = self._store._fact_cache.get(canon)
            self._store.add_fact(canon, kind, builds, payload)
            if existing_before is not None:
                facts_reused += 1
            else:
                facts_indexed += 1

        return {
            "build_name": build_name,
            "tu_count": len(facts_list),
            "facts_total": facts_indexed + facts_reused,
            "facts_new": facts_indexed,
            "facts_reused": facts_reused,
        }

    def projection_parity_check(self, build_name: str, gt_facts) -> dict:
        """Compare M6 store projection against ground-truth BuildFacts.

        This is the §34 shadow differential test for a single build.
        Returns {match: bool, mismatches: [...]}.

        gt_facts: the BuildFacts object extracted directly from Clang
                  (independent of the M6 store).
        """
        # Build the expected canon set from GT
        expected = canonicalize_all_facts(gt_facts)
        expected_canons = {c for c, _, _ in expected}
        expected_by_kind: dict[str, set[str]] = {}
        for c, k, _ in expected:
            expected_by_kind.setdefault(k, set()).add(c)

        # Get the M6 store projection for this build
        shadow_proj = self._store.project_build(build_name)
        shadow_canons = set()
        shadow_by_kind: dict[str, set[str]] = {}
        for kind, facts in shadow_proj.items():
            for f in facts:
                shadow_canons.add(f["fact_canon"])
                shadow_by_kind.setdefault(kind, set()).add(f["fact_canon"])

        # Compare
        missing = expected_canons - shadow_canons
        extra = shadow_canons - expected_canons

        per_kind_mismatches = []
        all_kinds = set(list(expected_by_kind.keys()) + list(shadow_by_kind.keys()))
        for kind in all_kinds:
            exp = expected_by_kind.get(kind, set())
            shd = shadow_by_kind.get(kind, set())
            if exp != shd:
                per_kind_mismatches.append({
                    "kind": kind,
                    "expected_count": len(exp),
                    "shadow_count": len(shd),
                    "missing": sorted(exp - shd)[:5],  # first 5 for brevity
                    "extra": sorted(shd - exp)[:5],
                })

        return {
            "build_name": build_name,
            "match": len(missing) == 0 and len(extra) == 0,
            "expected_count": len(expected_canons),
            "shadow_count": len(shadow_canons),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "per_kind_mismatches": per_kind_mismatches,
        }


def index_corpus_into_m6_store(corpus_dir: Path, store: BuildSetStore,
                                extract_fn=None) -> dict:
    """Index an entire M6 torture corpus into the M6 store.

    corpus_dir: directory containing the torture corpus cases.
    store: an open BuildSetStore.
    extract_fn: function (case_name, build_name) -> BuildFacts. Defaults to
                using full_build_gt.extract_build_facts with the corpus's
                ground-truth extractor.

    Returns {case_name: {build_name: parity_result}}.
    """
    if extract_fn is None:
        # Default: use the corpus's GT extractor
        from full_build_gt import extract_build_facts
        from corpus_gen import generate_corpus
        # Regenerate corpus if missing
        if not (corpus_dir / "CALL_GRAPH_DISPATCH").exists():
            generate_corpus(corpus_dir)

        def extract_fn(case_name, build_name):
            # Load the GT JSON (already extracted)
            gt_path = corpus_dir / f"{case_name}_gt.json"
            if gt_path.exists():
                import json
                gt_data = json.loads(gt_path.read_text())
                if build_name in gt_data and "error" not in gt_data[build_name]:
                    from full_build_gt import BuildFacts
                    bf = BuildFacts(build_name=build_name, defines=gt_data[build_name].get("defines", {}))
                    for k in ["symbols", "calls", "functions", "variables", "types",
                              "fields", "enum_constants", "inheritance"]:
                        setattr(bf, k, gt_data[build_name].get(k, []))
                    return bf
            return None

    adapter = M6IndexingAdapter(store)
    results = {}

    # Discover cases from the corpus directory
    case_dirs = []
    for p in corpus_dir.iterdir():
        if p.is_dir() and p.name != "__pycache__":
            case_dirs.append(p)

    for case_dir in sorted(case_dirs):
        case_name = case_dir.name
        results[case_name] = {}

        # Find the GT file for this case
        gt_path = corpus_dir / f"{case_name}_gt.json"
        if not gt_path.exists():
            continue
        import json
        gt_data = json.loads(gt_path.read_text())

        for build_name in gt_data:
            if "error" in gt_data[build_name]:
                continue
            facts = extract_fn(case_name, build_name)
            if facts is None:
                continue
            # Index into M6 store
            adapter.index_build_facts(build_name, facts)
            # Parity check
            parity = adapter.projection_parity_check(build_name, facts)
            results[case_name][build_name] = parity

    return results
