"""Fault Discovery Agent — Wave-1 implementation package.

ISOLATION BOUNDARY (Wave-1 task §3): this package is the Fault Discovery Agent
implementation. It is deliberately separate from ``src/fw_context_mcp/**``.
Nothing in this package edits, patches, or repairs the substrate product.

The Agent consumes fw-context-mcp v0.31.0 @ HEAD d8f08a4 as an
untrusted-but-contracted EVIDENCE PROVIDER through K1 (``fault_discovery_agent.k1``).
All substrate access flows through K1 (K1_INTERFACE_SPEC.md HARD RULE).
"""

__version__ = "0.1.0"

WAVE1_SCHEMA_VERSION = "wave1-design-gate-1"

# Authoritative goal (verbatim; S0_GOAL_ADR.md line 5 — byte-identical quote).
AUTHORITATIVE_GOAL = (
    "构建一个 Agent，能够从源代码中发现固件/软件故障机制，并推导出相应的故障检测/诊断规则。"
)
