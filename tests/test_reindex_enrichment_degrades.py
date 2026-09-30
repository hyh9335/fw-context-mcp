"""LLM-enrichment phases degrade on transport errors — NEW-W1-2.

Diagnosis of the 6 pre-existing WSL ``test_index_integrity`` failures
(TestReindexIntegrity, httpx.ReadTimeout at 7bf462f AND 6417d99, identical
mode):

- ``reindex_file_impl`` runs the post-write enrichment phases
  (``_reindex_post_write_phases``).  With the DEFAULT config
  (``llm.enabled=True``, ``llm.analyze_symbols=True`` — config/settings.py),
  ``_reindex_llm_analysis`` calls ``_build_llm_analysis`` →
  ``_resolve_model_context_size`` → ``http_get(ollama_url/api/tags)``.
- On the WSL host the ambient corporate proxy env hijacks the loopback
  request (NO_PROXY without localhost) → ``httpx.ReadTimeout`` after 5 s.
- ``httpx`` errors are NOT in ``SAFE_EXCEPT`` (ValueError/TypeError/
  RuntimeError/AttributeError/sqlite3.Error/OSError) and NOT in the two
  enrichment guards' ``except (sqlite3.Error, RuntimeError, OSError)`` —
  so the exception escapes a phase whose DOCSTRING promises "Failures are
  non-fatal — the result dict gets an analysis_warning key instead of a
  hard error" (maintenance.py::_reindex_llm_analysis).  On Windows the
  same tests pass only because Ollama answers locally / NO_PROXY covers
  localhost.

Disposition: env-triggered, exposing a real contract bug — the fix widens
the two enrichment guards to degrade httpx transport failures to the
documented warnings.  The tests' assertions about symbol integrity were
never weakened: they now exercise the DESIGNED degradation path.  The
reindex enrichment still runs and still stores warnings when the backend
is unreachable.
"""

from __future__ import annotations

from pathlib import Path

from fw_context_mcp.indexer.db import (
    CURRENT_ROW_FORMAT,
    open_db,
    transaction,
    upsert_build_config,
    upsert_project,
)


def _seed(tmp_path: Path):
    """Temp project + index DB with one active build config; returns (db, cfg)."""
    pid, ch = "enrich-degrade-proj", "cfgenrich0001"
    root = tmp_path / "proj"
    (root / ".fw-context").mkdir(parents=True)
    index_dir = tmp_path / "index"
    (root / ".fw-context" / "config.toml").write_text(
        "[project]\n"
        f'id = "{pid}"\n'
        "\n"
        "[index]\n"
        f'db_dir = "{str(index_dir).replace(chr(92), "/")}"\n',
        encoding="utf-8",
    )
    (root / "compile_commands.json").write_text("[]", encoding="utf-8")

    db_path = index_dir / pid / "index.db"
    db_path.parent.mkdir(parents=True)
    conn = open_db(db_path)
    try:
        with transaction(conn):
            upsert_project(conn, pid, "enrich-degrade-proj", str(root))
            upsert_build_config(
                conn, ch, pid, str(root / "compile_commands.json"),
                row_format=CURRENT_ROW_FORMAT,
            )
    finally:
        conn.close()

    from fw_context_mcp.config import load as load_config

    cfg = load_config(project_root=root)
    assert cfg.llm.enabled and cfg.llm.analyze_symbols, (
        "this oracle pins the DEFAULT config's enrichment behavior; if the "
        "defaults changed, update the diagnosis in the module docstring"
    )
    return db_path, cfg, root, ch


def _httpx_timeout() -> Exception:
    import httpx

    return httpx.ReadTimeout("timed out")


class TestLlmAnalysisPhaseDegrades:
    def test_transport_error_becomes_analysis_warning(self, tmp_path: Path,
                                                      monkeypatch):
        db_path, cfg, root, ch = _seed(tmp_path)
        import fw_context_mcp.indexer._llm_analysis as analysis_mod

        def _explode(*args, **kwargs):
            raise _httpx_timeout()

        monkeypatch.setattr(analysis_mod, "_build_llm_analysis", _explode)

        from fw_context_mcp.mcp.handlers.maintenance import _reindex_llm_analysis

        conn = open_db(db_path)
        result: dict = {}
        try:
            # RED pre-fix: httpx.ReadTimeout propagated out of the guard,
            # contradicting the documented non-fatal contract.
            _reindex_llm_analysis(
                conn, ch, cfg, db_path.parent, result, project_root=root,
            )
        finally:
            conn.close()
        assert "analysis_warning" in result, (
            "an unreachable LLM backend must degrade the enrichment phase to "
            f"analysis_warning, got {result}"
        )
        assert "LLM analysis skipped" in result["analysis_warning"]

    def test_non_fatal_errors_still_degrade(self, tmp_path: Path, monkeypatch):
        """The pre-existing (sqlite3.Error/RuntimeError/OSError) path is intact."""
        db_path, cfg, root, ch = _seed(tmp_path)
        import fw_context_mcp.indexer._llm_analysis as analysis_mod

        monkeypatch.setattr(
            analysis_mod, "_build_llm_analysis",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        from fw_context_mcp.mcp.handlers.maintenance import _reindex_llm_analysis

        conn = open_db(db_path)
        result: dict = {}
        try:
            _reindex_llm_analysis(conn, ch, cfg, db_path.parent, result,
                                  project_root=root)
        finally:
            conn.close()
        assert result.get("analysis_warning", "").startswith("LLM analysis skipped")


class TestEmbeddingPhaseDegrades:
    def test_transport_error_becomes_embedding_warning(self, tmp_path: Path,
                                                       monkeypatch):
        db_path, cfg, root, ch = _seed(tmp_path)
        import fw_context_mcp.indexer.runner as runner_mod

        # The phase only reaches _build_embeddings when the target file has
        # symbols — seed one row for it (normalized project-relative path).
        from fw_context_mcp.indexer.ops import _normalize_file_path

        target = root / "c.c"
        target.write_text("int c_fn(void) { return 0; }\n", encoding="utf-8")
        norm = _normalize_file_path(str(target), root)

        from fw_context_mcp.indexer.db import (
            open_db,
            transaction,
            upsert_file,
        )

        conn = open_db(db_path)
        try:
            with transaction(conn):
                file_id = upsert_file(conn, ch, str(target), "c")
                conn.execute(
                    "INSERT INTO symbols (config_hash, file_id, file_path, usr,"
                    " name, qualified_name, kind, line, col)"
                    " VALUES (?, ?, ?, 'c:usr@fn', 'c_fn', 'c_fn', 'function', 1, 1)",
                    (ch, file_id, norm),
                )
        finally:
            conn.close()

        def _explode(*args, **kwargs):
            raise _httpx_timeout()

        monkeypatch.setattr(runner_mod, "_build_embeddings", _explode)

        from fw_context_mcp.mcp.handlers.maintenance import _reindex_embeddings

        conn = open_db(db_path)
        result: dict = {}
        try:
            # RED pre-fix: the same escape as the analysis phase.
            _reindex_embeddings(conn, ch, cfg, db_path.parent, target, root, result)
        finally:
            conn.close()
        assert "embedding_warning" in result, (
            "an unreachable embedding backend must degrade the phase to "
            f"embedding_warning, got {result}"
        )
        assert "Embedding regeneration skipped" in result["embedding_warning"]


class TestDisabledLlmShortCircuits:
    def test_disabled_llm_makes_no_transport_calls(self, tmp_path: Path,
                                                   monkeypatch):
        """With llm.enabled=False neither phase may attempt a transport call."""
        db_path, cfg, root, ch = _seed(tmp_path)
        cfg.llm.enabled = False

        import fw_context_mcp.indexer._llm_analysis as analysis_mod
        import fw_context_mcp.indexer.runner as runner_mod

        def _explode(*args, **kwargs):  # pragma: no cover — must not run
            raise AssertionError("enrichment phase attempted a call while disabled")

        monkeypatch.setattr(analysis_mod, "_build_llm_analysis", _explode)
        monkeypatch.setattr(runner_mod, "_build_embeddings", _explode)

        from fw_context_mcp.mcp.handlers.maintenance import (
            _reindex_embeddings,
            _reindex_llm_analysis,
        )

        conn = open_db(db_path)
        try:
            r1: dict = {}
            _reindex_llm_analysis(conn, ch, cfg, db_path.parent, r1,
                                  project_root=root)
            r2: dict = {}
            _reindex_embeddings(conn, ch, cfg, db_path.parent,
                                root / "c.c", root, r2)
        finally:
            conn.close()
        assert "analysis_warning" not in r1 and "analysis_updated" not in r1
        assert "embedding_warning" not in r2 and "embeddings_updated" not in r2
