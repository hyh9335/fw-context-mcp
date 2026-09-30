"""Slice-0 inertness tests (T19) — flag off ⇒ v2 completely inert.

T19 (PHASE4 task §9): with the flag off, running the legacy indexer/query
behaves byte-identically: no v2 DB/WAL/shm, no v2 lock, no v2 metadata write,
no legacy behavior change.
"""

from __future__ import annotations

import re
from pathlib import Path

from fw_context_mcp.config.settings import (
    IndexConfig,
    _INDEX_FIELDS,
    _PROJECT_DEFAULTS_TEMPLATE,
)
from fw_context_mcp.store_v2 import v2_db_path

REPO = Path(__file__).resolve().parents[1]

# Frozen baseline: every pre-Slice-0 IndexConfig default (byte-comparable).
LEGACY_DEFAULTS: dict[str, object] = {
    "db_dir": IndexConfig.__dataclass_fields__["db_dir"].default_factory(),
    "compile_commands": IndexConfig.__dataclass_fields__["compile_commands"]
    .default_factory(),
    "config_header": "",
    "vendor_paths": [],
    "project_paths": [],
    "index_refs": True,
    "index_embeddings": True,
    "rerank_top_k": 50,
    "min_dense_count": 3,
    "max_symbol_body_lines": 1000,
    "purge_max_missing_percent": 20,
}


def test_flag_defaults_off_and_inert():
    cfg = IndexConfig()
    assert cfg.shadow_v2_enabled is False
    assert cfg.shadow_v2_db is None
    # every legacy field keeps its exact baseline default
    for name, value in LEGACY_DEFAULTS.items():
        assert getattr(cfg, name) == value, name


def test_flag_registered_in_config_mapping():
    names = {f[0] for f in _INDEX_FIELDS}
    assert {"shadow_v2_enabled", "shadow_v2_db"} <= names


def test_template_documents_the_flag():
    assert "shadow_v2_enabled" in _PROJECT_DEFAULTS_TEMPLATE
    assert "shadow_v2_enabled = false" in _PROJECT_DEFAULTS_TEMPLATE


def test_no_implicit_v2_creation(tmp_path, monkeypatch):
    """Nothing in the config path creates index-v2.db: constructing configs
    (even pointing db_dir at a temp dir) must leave the filesystem untouched."""
    cfg = IndexConfig(db_dir=tmp_path / "idx")
    assert not v2_db_path(cfg.db_dir, "someproject").exists()
    assert not (tmp_path / "idx").exists()      # not even the directory


def test_no_legacy_module_imports_store_v2():
    """Structural inertness gate: `store_v2` is imported by NOTHING outside
    the package itself (legacy indexing/query/CLI/MCP paths stay untouched)."""
    pkg_root = REPO / "src" / "fw_context_mcp"
    pattern = re.compile(r"(from\s+fw_context_mcp\.store_v2|from\s+\.\.?store_v2"
                         r"|import\s+fw_context_mcp\.store_v2)")
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        rel = py.relative_to(pkg_root).as_posix()
        if rel.startswith("store_v2/"):
            continue
        text = py.read_text(encoding="utf-8")
        if pattern.search(text):
            offenders.append(rel)
    assert offenders == []


def test_flag_on_explicit_path_is_functional(tmp_path):
    """The explicit shadow path (flag on, test harness) can create the v2 store
    and compute keys — the complete Slice-0 surface, nothing more."""
    from fw_context_mcp.store_v2 import CertifiedSemanticInputs, ShadowStore, \
        compute_tu_semantic_key
    from fw_context_mcp.store_v2.keys import h

    cfg = IndexConfig(db_dir=tmp_path / "idx", shadow_v2_enabled=True)
    assert cfg.shadow_v2_enabled is True
    store = ShadowStore.create(v2_db_path(cfg.db_dir, "someproject"))
    try:
        assert store.metadata()["store_format"] == "fw-context-store/2"
        key = compute_tu_semantic_key(CertifiedSemanticInputs(
            source_blob_sha=h("b", "x"), language="c", ncc_serial="-std=c11",
            forced_content_legs=(h("f", "1"),),
            cert_content_sequence=(h("f", "1"),),
            macro_state_digest=h("c5", "m"),
            environment_digest=h("e", "cert"), parser_generation=1,
            toolchain_salt="tc", parser_salt="ps", nonce="w"))
        assert key.is_reusable and len(key.root) == 64
    finally:
        store.close()


def test_product_kbc_domain_constants():
    """The product KB-C family domain is the adopted one (D1 §3 example)."""
    import fw_context_mcp.store_v2.keys as keys
    assert keys.KEY_FORMAT == "fwctx-tukb/v1.l2closure"
    assert keys.L2_DOMAIN == "fwctx-l2c/v1"
    assert keys.ROOT_DOMAIN == "fwctx-tukb/v1"
    assert keys.UNKNOWN_TAG == "fwctx-UNKNOWN/v1"
