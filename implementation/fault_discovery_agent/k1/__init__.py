"""k1 subpackage — the Evidence Gateway (sole substrate access path)."""
from .channels import ChannelError, build_manifest_diff, clang_e, grep, pp_active_branches
from .detectors import (
    detect_g1_substrate_pin,
    detect_g4_fragment,
    detect_g6_fallback,
    detect_g7_truncation,
    detect_g8_macro_value,
    run_all_detectors,
)
from .factlog import ERROR_TAXONOMY, FactLog
from .gateway import GatewayResult, K1Gateway, RoundPin
from .substrate import (
    SUBSTRATE_COMMIT,
    RawResponse,
    SubstrateError,
    call_substrate,
    substrate_identity,
)

__all__ = [
    "SUBSTRATE_COMMIT",
    "ChannelError",
    "RawResponse",
    "SubstrateError",
    "GatewayResult",
    "K1Gateway",
    "RoundPin",
    "FactLog",
    "ERROR_TAXONOMY",
    "build_manifest_diff",
    "clang_e",
    "grep",
    "pp_active_branches",
    "detect_g1_substrate_pin",
    "detect_g4_fragment",
    "detect_g6_fallback",
    "detect_g7_truncation",
    "detect_g8_macro_value",
    "run_all_detectors",
    "call_substrate",
    "substrate_identity",
]
