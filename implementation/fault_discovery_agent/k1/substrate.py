"""K1 substrate adapter — the ONLY path from the Agent to fw-context.

TRANSPORT NOTE (recorded honestly, K1_INTERFACE_SPEC.md contract-faithful):
the execution sandbox of this Wave-1 environment forbids named-pipe stdio
subprocesses, so the MCP stdio server cannot be spawned here. The adapter
therefore invokes the SAME handler functions the stdio server dispatches to
(``fw_context_mcp.mcp.handlers.*``, editable install of the pinned substrate
fw-context-mcp v0.31.0 @ d8f08a41d3fb9df6de255a187720f6332ed0f1c9) with
``project_root`` pinned per case and ``FW_CONTEXT_INDEX_DIR`` pinned per
round. Every raw response is captured byte-for-byte to the round artifact
store before any processing (provenance, never ground truth — E1).

No handler that mutates substrate state (reindex/reset/configure) is ever
exposed. K1 is read-only over the substrate.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SUBSTRATE_COMMIT = "d8f08a41d3fb9df6de255a187720f6332ed0f1c9"
SUBSTRATE_PACKAGE = "fw_context_mcp"


class SubstrateError(RuntimeError):
    """Substrate invocation failed structurally (import, mapping, exit)."""


@dataclass
class RawResponse:
    """Unmodified raw substrate output + captured query (E3 replayability)."""

    tool: str
    args: dict
    payload: Any
    raw_text: str
    artifact_path: str | None = None
    sha256: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "args": self.args,
            "artifact_path": self.artifact_path,
            "sha256": self.sha256,
            "error": self.error,
        }


_HANDLERS: dict[str, tuple[str, str]] = {
    # K1-exposed surfaces -> (module, function) in fw_context_mcp.mcp.handlers
    "read_file": ("source", "read_file"),
    "lookup_symbol": ("_lookup", "lookup_symbol"),
    "list_conditional_regions": ("conditionals", "list_conditional_regions"),
    "explain_condition": ("conditionals", "explain_condition"),
    "find_callers": ("callgraph", "find_callers"),
    "get_active_build": ("maintenance", "get_active_build"),
    "find_references": ("callgraph", "find_references"),
    "get_file_map": ("source", "get_file_map"),
    "search_bodies": ("search", "search_bodies"),
    "search_content": ("search", "search_content"),
    "search_code": ("search", "search_code"),
}

_FORBIDDEN_TOOLS = (
    # mutating / configuration surfaces never exposed through K1
    "reindex_file",
    "reset_index",
    "configure_llm",
    "check_dependencies",
)


def _load_handler(tool: str) -> Callable:
    if tool in _FORBIDDEN_TOOLS:
        raise SubstrateError(f"tool {tool!r} is mutating and never exposed through K1")
    if tool not in _HANDLERS:
        raise SubstrateError(f"tool {tool!r} is not a mapped K1 substrate surface")
    mod_name, fn_name = _HANDLERS[tool]
    module = importlib.import_module(f"fw_context_mcp.mcp.handlers.{mod_name}")
    return getattr(module, fn_name)


def substrate_identity() -> dict:
    """Version pin of the substrate actually loaded (G1 detector input)."""
    import importlib.metadata as md

    dist_version = None
    try:
        dist_version = md.version(SUBSTRATE_PACKAGE)
    except md.PackageNotFoundError:  # pragma: no cover
        pass
    mod = importlib.import_module(SUBSTRATE_PACKAGE)
    file_version = getattr(mod, "__version__", None)
    return {
        "package": SUBSTRATE_PACKAGE,
        "distribution_version": dist_version,
        "module_version": file_version,
        "pinned_commit": SUBSTRATE_COMMIT,
    }


def call_substrate(
    tool: str,
    args: dict,
    *,
    project_root: str,
    index_home: str | None = None,
    artifact_store: Path | None = None,
) -> RawResponse:
    """Invoke one substrate surface with pinned context; capture raw output.

    Environment discipline: FW_CONTEXT_INDEX_DIR is set for THIS process (the
    adapter refuses cross-round reuse by pinning per call).
    """
    if index_home:
        os.environ["FW_CONTEXT_INDEX_DIR"] = index_home
    handler = _load_handler(tool)
    full_args = dict(args)
    full_args.setdefault("project_root", project_root)
    raw_text = ""
    payload: Any = None
    error: str | None = None
    try:
        payload = handler(**full_args)
        raw_text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception as exc:  # substrate-side failure is DATA, not a crash
        error = f"{type(exc).__name__}: {exc}"
        raw_text = json.dumps({"error": error}, ensure_ascii=False)
    resp = RawResponse(tool=tool, args=full_args, payload=payload, raw_text=raw_text, error=error)
    if artifact_store is not None:
        artifact_store.mkdir(parents=True, exist_ok=True)
        from ..schemas import file_sha256, sha256_hex

        # stable name: tool + args hash
        from ..schemas import canonical_json_bytes

        stem = sha256_hex(canonical_json_bytes({"tool": tool, "args": full_args}))[:16]
        out = artifact_store / f"{tool}_{stem}.json"
        out.write_text(raw_text, encoding="utf-8")
        resp.artifact_path = str(out)
        resp.sha256 = file_sha256(out)
    return resp


def run_git_status(project_root: str) -> str | None:
    """Out-of-band substrate-pin check (G1): `git status --porcelain src` is
    expected to be empty for the pinned tree; None when git is unavailable."""
    return _git(["status", "--porcelain", "src"], project_root)


def run_git_head(project_root: str) -> str | None:
    """`git rev-parse HEAD` of the substrate checkout; None when unavailable."""
    out = _git(["rev-parse", "HEAD"], project_root)
    return out.strip() if out is not None else None


def _git(args: list[str], project_root: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout
