"""schemas subpackage: frozen-schema loading, models, refutation state machine."""
from .canonical_json import (
    canonical_json_bytes,
    combined_manifest_hash,
    file_sha256,
    fixture_tree_hash,
    git_blob_sha1,
    manifest_line,
    sha256_hex,
    tree_manifest_lines,
)
from .loader import SchemaValidationError, load_schema, validate
from .refutation import (
    ACCEPTANCE_ELIGIBLE,
    STATES,
    VALIDATION_STATUS_ALLOWED,
    RefutationTransitionError,
    acceptance_eligible,
    check_status_mapping,
    check_transition,
    validate_refutation_result,
)

__all__ = [
    "canonical_json_bytes",
    "combined_manifest_hash",
    "file_sha256",
    "fixture_tree_hash",
    "git_blob_sha1",
    "manifest_line",
    "sha256_hex",
    "tree_manifest_lines",
    "SchemaValidationError",
    "load_schema",
    "validate",
    "ACCEPTANCE_ELIGIBLE",
    "STATES",
    "VALIDATION_STATUS_ALLOWED",
    "RefutationTransitionError",
    "acceptance_eligible",
    "check_status_mapping",
    "check_transition",
    "validate_refutation_result",
]
