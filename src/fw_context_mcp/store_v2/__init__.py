"""fw_context_mcp.store_v2 — Phase-4 SLICE-0 shadow v2 store (inert by default).

Scope (multi_version_index_review/phase4/slice0/SLICE0_CODE_BOUNDARY.md,
SLICE0_G7_SCOPE_ADR.md): identity primitives (KB-C key mechanics over
ALREADY-CERTIFIED inputs), the physically separate shadow store skeleton
(``index-v2.db``, M-C two-store decision), and the schema-level
SemanticState/TUBinding ownership boundaries — NOTHING else.

Inertness contract (T19): this package is imported by NO legacy code path and
reads NO configuration at import time. The v2 store exists only after an
explicit, flag-gated Slice-0 shadow/test creation
(``cfg.index.shadow_v2_enabled``). With the flag off:

* no ``index-v2.db`` (and no -wal/-shm) is ever created;
* no v2 lock or metadata write occurs;
* legacy ``index.db`` behavior is byte-identical to baseline.

Deliberately NOT here (later slices): publication/concurrency (Slice 1), world
writes (Slice 2), strict QueryView read path (Slice 3), reuse enablement
(Slice 4), retention/GC (Slice 5), promotion/cutover (Slice 7). G7 (production
certification source) gates Slice 1 and is not implemented here — Slice 0
consumes certified fixtures/test structures only.
"""

from __future__ import annotations

__all__ = [
    "CertifiedSemanticInputs",
    "TUKeyResult",
    "compute_tu_semantic_key",
    "KEY_FORMAT",
    "UNKNOWN_TAG",
    "NOT_FOUND",
    "poison",
    "salt_guard",
    "guarded_salt",
    "ShadowStore",
    "v2_db_path",
    "StoreFormatError",
    "StoreVersionError",
    "StoreGenerationError",
    "SCHEMA_GENERATION",
    "STORE_FORMAT",
    "STORE_VERSION",
    "V2_DB_FILENAME",
]

# Lazy re-exports (module attributes, no import-time side effects beyond
# loading the pure-Python modules themselves).
from .keys import (  # noqa: E402
    KEY_FORMAT,
    NOT_FOUND,
    UNKNOWN_TAG,
    CertifiedSemanticInputs,
    TUKeyResult,
    compute_tu_semantic_key,
    guarded_salt,
    poison,
    salt_guard,
)
from .schema import (  # noqa: E402
    SCHEMA_GENERATION,
    STORE_FORMAT,
    STORE_VERSION,
    V2_DB_FILENAME,
)
from .store import (  # noqa: E402
    ShadowStore,
    StoreFormatError,
    StoreGenerationError,
    StoreVersionError,
    v2_db_path,
)
