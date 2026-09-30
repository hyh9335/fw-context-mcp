"""Unit tests for DeepSeek Harness (dsh) MCP self-registration.

These verify the installer's dsh channel: the Cordis patch block it writes,
idempotent upsert (add / update / leave-alone), and the profile-aware
``_register_dsh_mcp`` guard.  Everything runs against ``tmp_path`` — nothing
touches the real ``~/.dsh`` or ``$DSH_HOME``.
"""

from __future__ import annotations

from fw_context_mcp.cli._mcp import (
    DSH_ENTRY_ID,
    _dsh_insert_block,
    _register_dsh_mcp,
    _upsert_dsh_patch,
)
from fw_context_mcp.config.tools import TOOLS


def _probe_capsys(capsys):
    return capsys.readouterr()


def test_dsh_insert_block_shape():
    block = _dsh_insert_block("fw-context-mcp", None)
    lines = block.splitlines()
    assert lines[0] == "- insert:"
    assert any(line.strip() == f"- id: {DSH_ENTRY_ID}" for line in lines)
    assert "name: '@deepseek-ai/dsh-mcp-client'" in block
    assert "serverName: fw_context" in block
    assert "transport: stdio" in block
    assert "command: 'fw-context-mcp'" in block
    assert "cwd:" not in block  # project_root None -> no cwd


def test_dsh_insert_block_with_cwd():
    # Windows-style backslash paths are preserved as single-quoted YAML.
    quoted = _dsh_insert_block(r"C:\firmware\fw-context-mcp.exe", __import__("pathlib").Path(r"D:\proj"))
    assert "command: 'C:\\firmware\\fw-context-mcp.exe'" in quoted
    assert "cwd: 'D:\\proj'" in quoted


def test_upsert_add_update_idempotent(tmp_path):
    patch = tmp_path / "cordis.patch.yml"
    bin_a = "fw-context-mcp"
    bin_b = r"C:\dev\scripts\fw-context-mcp.exe"
    root = tmp_path / "proj"

    action, changed = _upsert_dsh_patch(patch, bin_a, root)
    assert (action, changed) == ("added", True)
    assert patch.exists()
    text = patch.read_text(encoding="utf-8")
    assert f"- id: {DSH_ENTRY_ID}" in text
    assert f"cwd: '{root}'" in text

    # Same input again -> leave alone.
    action, changed = _upsert_dsh_patch(patch, bin_a, root)
    assert (action, changed) == ("present", False)
    assert patch.read_text(encoding="utf-8") == text

    # Different command -> in-place update, rest of the block intact.
    action, changed = _upsert_dsh_patch(patch, bin_b, root)
    assert (action, changed) == ("updated", True)
    text2 = patch.read_text(encoding="utf-8")
    assert "command: 'C:\\dev\\scripts\\fw-context-mcp.exe'" in text2
    assert f"cwd: '{root}'" in text2
    assert "reconnect:" in text2  # untouched rows survive


def test_upsert_preserves_other_patch_ops(tmp_path):
    patch = tmp_path / "cordis.patch.yml"
    patch.write_text(
        "- id: tool-pwsh\n  name: '@deepseek-ai/dsh-tool-pwsh'\n\n",
        encoding="utf-8",
    )
    _upsert_dsh_patch(patch, "fw-context-mcp", None)
    text = patch.read_text(encoding="utf-8")
    assert "id: tool-pwsh" in text
    assert text.count(f"- id: {DSH_ENTRY_ID}") == 1
    assert text.count("- insert:") == 1


def test_register_dsh_mcp_dry_run_missing_profile(capsys, tmp_path):
    tool = TOOLS["dsh"]
    assert tool.mcp_dsh is True
    # dry-run on a fresh home -> "would ADD"
    ok = _register_dsh_mcp(tool, "fw-context-mcp", dry_run=True, dsh_home=str(tmp_path))
    assert ok is True
    assert "would ADD" in _probe_capsys(capsys).out
    # a profile that does not exist -> soft skip, no write
    ok = _register_dsh_mcp(tool, "fw-context-mcp", dsh_home=str(tmp_path), dsh_profile="missing")
    assert ok is False
    assert not (tmp_path / "profiles").exists()
