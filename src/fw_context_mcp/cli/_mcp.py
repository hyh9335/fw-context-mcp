"""MCP server registration helpers for ``fw-context init``.

Registers fw-context-mcp as an MCP server in AI coding tools (Claude Code,
OpenCode, Codex, Cursor, etc.) during ``fw-context init``.  Each tool
stores MCP configuration differently — CLI commands, JSON files, TOML files,
or markdown with YAML frontmatter.  This module provides a unified interface
that dispatches to the right registration method per tool.

WHY registration during init: users should not need to manually edit MCP
config files — the tool should integrate itself into the user's existing
AI coding setup automatically.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_mcp_bin() -> str | None:
    """Find the fw-context-mcp binary, preferring canonical install over dev venv.

    Search order (priority):
    1. ``~/.local/bin/fw-context-mcp`` — canonical user install (pipx / pip --user)
    2. ``~/.fw-context/.venv/bin/fw-context-mcp`` — managed venv
    3. ``shutil.which("fw-context-mcp")`` — system PATH
    4. ``sys.executable / fw-context-mcp`` — dev venv (editable install)

    WHY canonical install first: the managed venv and system PATH may point
    to different versions or stale installations.  The canonical path is
    the most likely to match the currently installed package.
    """
    for candidate in [
        Path.home() / ".local" / "bin" / "fw-context-mcp",
        Path.home() / ".fw-context" / ".venv" / "bin" / "fw-context-mcp",
    ]:
        if candidate.exists():
            return str(candidate)
    mcp_bin = shutil.which("fw-context-mcp")
    if mcp_bin:
        return mcp_bin
    dev_candidate = Path(sys.executable).parent / "fw-context-mcp"
    if dev_candidate.exists():
        return str(dev_candidate)
    return None


DSH_ENTRY_ID = "mcp-fw-context"
DSH_SERVER_NAME = "fw_context"
DSH_DEFAULT_TOOL_TIMEOUT_MS = 180000


def _yaml_single_quote(value: str) -> str:
    """Return *value* as a safe single-quoted YAML scalar."""
    return "'" + str(value).replace("'", "''") + "'"


def _dsh_home_dir(dsh_home: str | None) -> Path:
    """Resolve the dsh home directory: explicit arg, $DSH_HOME, or ~/.dsh."""
    if dsh_home:
        return Path(os.path.expanduser(dsh_home)).resolve()
    env_home = os.environ.get("DSH_HOME")
    if env_home:
        return Path(env_home).resolve()
    return (Path.home() / ".dsh").resolve()


def _dsh_patch_path(dsh_home_dir: Path, profile: str | None) -> Path:
    """Target cordis patch: a profile's patch, or the home-level patch."""
    if profile:
        return dsh_home_dir / "profiles" / profile / "cordis.patch.yml"
    return dsh_home_dir / "cordis.patch.yml"


def _dsh_insert_block(mcp_bin: str, project_root: Path | None) -> str:
    """Build the ``- insert:`` block that mounts fw-context-mcp via dsh-mcp-client.

    Mirrors dsh-adaptation/cordis.fw-context.patch.yml. The raw mcp name that
    dsh-mcp-client sees on the wire stays ``search_code`` etc.; the public
    name dsh shows the model is ``mcp__fw_context__<tool>``.
    """
    lines = [
        "- insert:",
        "    - id: " + DSH_ENTRY_ID,
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        "        serverName: " + DSH_SERVER_NAME,
        "        transport: stdio",
        "        command: " + _yaml_single_quote(mcp_bin),
        "        args: []",
    ]
    if project_root is not None:
        lines.append("        cwd: " + _yaml_single_quote(str(project_root)))
    lines.extend([
        "        toolCallTimeoutMs: " + str(DSH_DEFAULT_TOOL_TIMEOUT_MS),
        "        failOnStartupError: false",
        "        reconnect:",
        "          enabled: true",
        "          initialDelayMs: 1000",
        "          maxDelayMs: 15000",
        "          maxAttempts: 5",
    ])
    return "\n".join(lines)


def _upsert_dsh_patch(patch_path: Path, mcp_bin: str, project_root: Path | None) -> tuple[str, bool]:
    """Idempotently insert/refresh the fw-context row in a dsh cordis patch.

    Returns ``(action, changed)`` where *action* is ``"added"``, ``"updated"``
    or ``"present"``.  Only the ``mcp-fw-context`` block is touched; every
    other row and any ``!!js`` expressions are left byte-for-byte intact.
    """
    new_block = _dsh_insert_block(mcp_bin, project_root)
    if not patch_path.exists():
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(new_block + "\n", encoding="utf-8")
        return "added", True

    text = patch_path.read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    new_block = new_block.replace("\n", nl)

    match = re.search(
        r"(?m)^([ \t]*- id:[ \t]*" + re.escape(DSH_ENTRY_ID) + r"[ \t]*\n)"
        r"(?P<body>(?s:(?:(?![ \t]*- id:).)*))",
        text,
    )
    if not match:
        body = text.rstrip()
        if body:
            body += nl + nl
        patch_path.write_text(body + new_block + nl, encoding="utf-8")
        return "added", True

    header = match.group(1)
    body = match.group("body")
    changed = False
    new_command = _yaml_single_quote(mcp_bin)

    cmd_new, n = re.compile(r"(?m)^([ \t]*command:[ \t]*)[^\n]*$").subn(
        lambda m: m.group(1) + new_command, body, count=1)
    if n and cmd_new != body:
        changed = True
        body = cmd_new

    if project_root is not None:
        new_cwd = _yaml_single_quote(str(project_root))
        cwd_new, n = re.compile(r"(?m)^([ \t]*cwd:[ \t]*)[^\n]*$").subn(
            lambda m: m.group(1) + new_cwd, body, count=1)
        if n:
            if cwd_new != body:
                changed = True
                body = cwd_new
        else:
            args_line = re.search(r"(?m)^([ \t]*)args: \[\]\r?\n", body)
            if args_line:
                indent = args_line.group(1)
                insertion = indent + "cwd: " + new_cwd + nl
                body = body[: args_line.end()] + insertion + body[args_line.end():]
                changed = True

    if changed:
        patch_path.write_text(text[: match.start()] + header + body + text[match.end():], encoding="utf-8")
        return "updated", True
    return "present", False


def _register_dsh_mcp(
    tool,
    mcp_bin: str,
    *,
    dry_run: bool = False,
    project_root: Path | None = None,
    dsh_home: str | None = None,
    dsh_profile: str | None = None,
) -> bool:
    """Register fw-context-mcp with DeepSeek Harness via its Cordis MCP bridge.

    Writes the official ``@deepseek-ai/dsh-mcp-client`` insert into the
    home-level ``$DSH_HOME/cordis.patch.yml`` (applies to every profile) or,
    when *dsh_profile* is given, into ``profiles/<profile>/cordis.patch.yml``.
    Returns True when the registration is present (or would be, in dry-run);
    False only when the requested profile does not exist.
    """
    dsh_home_dir = _dsh_home_dir(dsh_home)
    patch_path = _dsh_patch_path(dsh_home_dir, dsh_profile)
    if dsh_profile:
        if not (dsh_home_dir / "profiles" / dsh_profile).is_dir():
            print(
                f"  [skip] dsh: profile '{dsh_profile}' not found under {dsh_home_dir} - "
                "omit --dsh-profile to register for all profiles",
                file=sys.stderr,
            )
            return False
    if dry_run:
        existing = patch_path.read_text(encoding="utf-8") if patch_path.exists() else ""
        if re.search(r"(?m)^[ \t]*- id:[ \t]*" + re.escape(DSH_ENTRY_ID) + r"[ \t]*$", existing):
            print(f"  [dry-run] dsh: would UPDATE fw-context in {patch_path}")
        else:
            print(f"  [dry-run] dsh: would ADD fw-context to {patch_path}")
        return True
    action, _changed = _upsert_dsh_patch(patch_path, mcp_bin, project_root)
    if action == "added":
        print(f"  [ok] dsh: fw-context registered ({patch_path})")
    elif action == "updated":
        print(f"  [ok] dsh: updated fw-context registration ({patch_path})")
    else:
        print(f"  [ok] dsh: fw-context already registered ({patch_path})")
    return True


def _register_mcp(
    tool,
    mcp_bin: str,
    dry_run: bool = False,
    *,
    project_root: Path | None = None,
    dsh_home: str | None = None,
    dsh_profile: str | None = None,
) -> None:
    """Register fw-context as an MCP server with *tool*'s configuration.

    *tool* is an ``AiTool`` instance; *mcp_bin* is the path or name of the
    ``fw-context-mcp`` executable. Dispatches to dsh's Cordis patch, a
    file-based client, or a CLI command depending on which fields are set
    on *tool*.
    """
    if getattr(tool, "mcp_dsh", False):
        _register_dsh_mcp(
            tool, mcp_bin, dry_run=dry_run,
            project_root=project_root, dsh_home=dsh_home, dsh_profile=dsh_profile,
        )
    elif tool.mcp_config_file:
        _register_mcp_file(tool, mcp_bin, dry_run=dry_run)
    elif tool.mcp_registration:
        if dry_run:
            print(f"  [dry-run] {tool.name}: would register {mcp_bin}")
        else:
            _register_mcp_cli(tool, mcp_bin)


def _register_mcp_cli(tool, mcp_bin: str) -> None:
    """Register fw-context as an MCP server via a CLI command."""
    if not tool.mcp_registration:
        return

    cmd_str = tool.mcp_registration.replace("{bin}", mcp_bin)
    parts = cmd_str.split()
    binary = parts[0]

    if not shutil.which(binary):
        print(f"  [skip] '{binary}' not found in PATH — register manually:")
        print(f"         {cmd_str}")
        return

    result = subprocess.run(parts, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"  [ok] {tool.name}: fw-context registered ({mcp_bin})")
    else:
        msg = (result.stderr or result.stdout).strip()
        msg_lower = msg.lower()
        if "already registered" in msg_lower or "already exists" in msg_lower:
            print(f"  [ok] {tool.name}: fw-context already registered")
        else:
            print(f"  [warn] {tool.name}: {msg}", file=sys.stderr)


def _ensure_subagent_mcp_permission(data: dict, tool_id: str) -> bool:
    """Ensure general subagent has fw-context MCP tool permissions (OpenCode)."""
    if "agent" not in data:
        data["agent"] = {}
    if "general" not in data["agent"]:
        data["agent"]["general"] = {}
    if "permission" not in data["agent"]["general"]:
        data["agent"]["general"]["permission"] = {}
    general_perm = data["agent"]["general"]["permission"]
    if "mcp__fw-context__*" not in general_perm:
        general_perm["mcp__fw-context__*"] = "allow"
        return True
    return False


def _register_mcp_file(tool, mcp_bin: str, dry_run: bool = False) -> None:
    """Register fw-context as an MCP server by editing a JSON config file.

    Used for tools that store MCP server configuration in a JSON file
    rather than exposing a CLI command (e.g. OpenCode's ``opencode.json``).
    Preserves existing file structure (schema, other MCP servers, etc.)
    and marks fw-context as ``enabled: true`` with ``type: local``.
    Also ensures the general subagent has fw-context MCP tool permissions.
    """
    if not tool.mcp_config_file:
        return

    config_path = Path(os.path.expanduser(tool.mcp_config_file))

    try:
        if config_path.exists():
            raw = config_path.read_text(encoding="utf-8")
            # Strip JSONC comments (// and /* */) before parsing.
            # OpenCode config files support comments for human readability
            # but they are not valid JSON.  We strip them before json.loads()
            # to avoid parse errors, then write back as plain JSON.
            # Regex patterns:
            #   /\*.*?\*/ — block comments (non-greedy, dotall for multiline)
            #   (?<!:)//.*$ — line comments (// not preceded by colon, to
            #     preserve URLs like "http://")
            raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
            raw = re.sub(r'(?<!:)//.*$', '', raw, flags=re.MULTILINE)
            data = json.loads(raw)
        else:
            data = {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [warn] {tool.name}: could not read {config_path}: {e}", file=sys.stderr)
        return

    mcp_servers = data.setdefault("mcp", {})
    key = tool.mcp_config_key or "fw-context"

    existing = mcp_servers.get(key)
    mcp_already_registered = isinstance(existing, dict) and existing.get("command") == [mcp_bin]

    # Always ensure subagent permissions, even if MCP is already registered
    perm_added = _ensure_subagent_mcp_permission(data, tool.id)

    if mcp_already_registered:
        if perm_added:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"  [ok] {tool.name}: fw-context already registered, added general subagent permission")
        else:
            print(f"  [ok] {tool.name}: fw-context already registered")
        return

    if dry_run:
        if isinstance(existing, dict):
            print(f"  [dry-run] {tool.name}: {config_path}: would UPDATE fw-context → {mcp_bin}")
        else:
            print(f"  [dry-run] {tool.name}: {config_path}: would ADD fw-context → {mcp_bin}")
        if not mcp_already_registered or perm_added:
            print(f"  [dry-run] {tool.name}: would ensure general subagent fw-context permission")
        return

    mcp_servers[key] = {
        "command": [mcp_bin],
        "enabled": True,
        "type": "local",
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  [ok] {tool.name}: fw-context registered ({mcp_bin})")


def _update_marked_section(path: Path, content: str, marker: str) -> None:
    """Insert or replace a <!-- marker --> ... <!-- /marker --> block in a markdown file."""
    start_tag = f"<!-- {marker} -->"
    end_tag = f"<!-- /{marker} -->"

    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    # Always create a backup before modifying, so users can recover
    # their custom content if the section detection is wrong.
    if path.exists() and existing.strip():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(existing, encoding="utf-8")

    if start_tag in existing and end_tag in existing:
        # Replace the existing marked block (keep markers for idempotency)
        before = existing[: existing.index(start_tag)]
        after = existing[existing.index(end_tag) + len(end_tag) :]
        updated = before.rstrip("\n") + "\n\n" + start_tag + "\n" + content + "\n" + end_tag + "\n" + after.lstrip("\n")
    else:
        # Remove any unmarked section with the same heading (idempotency for manual installs)
        heading_match = re.search(r"^## .+", content, re.MULTILINE)
        if heading_match:
            heading = heading_match.group()
            lines = existing.splitlines()
            result_lines: list[str] = []
            skip_until_next_h2 = False
            for line in lines:
                if not skip_until_next_h2:
                    if line.strip() == heading:
                        skip_until_next_h2 = True
                        continue
                    result_lines.append(line)
                else:
                    if line.startswith("## "):
                        skip_until_next_h2 = False
                        result_lines.append(line)
            existing = "\n".join(result_lines)
        updated = (
            existing.rstrip("\n")
            + ("\n\n" if existing.strip() else "")
            + start_tag
            + "\n"
            + content
            + "\n"
            + end_tag
            + "\n"
        )

    path.write_text(updated, encoding="utf-8")


def _inject_agent_section(path: Path, content: str, marker: str) -> None:
    """Insert or replace a ``<!-- marker --> ... <!-- /marker -->`` block in an agent markdown file.

    Unlike ``_update_marked_section``, when no existing marker is found this
    inserts the block right after the YAML frontmatter (``---`` delimiters)
    so the CRITICAL instructions are the first thing the agent sees.
    """
    start_tag = f"<!-- {marker} -->"
    end_tag = f"<!-- /{marker} -->"

    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    if start_tag in existing and end_tag in existing:
        # Replace the existing marked block (keep markers for idempotency)
        before = existing[: existing.index(start_tag)]
        after = existing[existing.index(end_tag) + len(end_tag) :]
        updated = before.rstrip("\n") + "\n\n" + start_tag + "\n" + content + "\n" + end_tag + "\n" + after.lstrip("\n")
    else:
        # Insert right after YAML frontmatter (after second "---").
        # Only count --- delimiters that appear in the document preamble —
        # once we pass the closing --- (or see non-YAML content before any
        # opening ---), we stop looking.  This avoids false matches on ---
        # inside fenced code blocks later in the file.
        lines = existing.splitlines()
        result_lines: list[str] = []
        frontmatter_dashes = 0
        in_preamble = True
        inserted = False

        for line in lines:
            result_lines.append(line)
            stripped = line.strip()
            if not in_preamble:
                continue
            if stripped == "---":
                frontmatter_dashes += 1
                if frontmatter_dashes == 2 and not inserted:
                    result_lines.append("")
                    result_lines.append(start_tag)
                    result_lines.append(content)
                    result_lines.append(end_tag)
                    inserted = True
                    in_preamble = False
            elif frontmatter_dashes == 0 and stripped:
                # Non-blank, non---- line before opening --- → no frontmatter
                in_preamble = False
            # If frontmatter_dashes == 1 and stripped is non-blank, it's
            # YAML keys inside the frontmatter — stay in_preamble.

        if inserted:
            updated = "\n".join(result_lines) + "\n"
        else:
            # No frontmatter found — prepend to file
            updated = start_tag + "\n" + content + "\n" + end_tag + "\n\n" + existing

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


def _inject_agent_toml_section(path: Path, content: str, marker: str) -> None:
    """Inject a ``# marker ... # /marker`` block into a Codex TOML agent file.

    Codex agent files use TOML format with an ``[instructions]`` key that
    holds freeform text.  The fw-context block is inserted into the
    ``[instructions]`` section using ``# fw-context`` comment markers.
    When no ``[instructions]`` section exists, one is created at the end
    of the file.
    """
    start_comment = f"# {marker}"
    end_comment = f"# /{marker}"

    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    # If markers already exist, replace between them
    if start_comment in existing and end_comment in existing:
        before = existing[: existing.index(start_comment)]
        after = existing[existing.index(end_comment) + len(end_comment) :]
        updated = (
            before.rstrip("\n") + "\n" + start_comment + "\n" + content + "\n" + end_comment + "\n" + after.lstrip("\n")
        )
    else:
        # Find [instructions] section and insert the block there
        instructions_match = re.search(r"^\[instructions\]\s*$", existing, re.MULTILINE)
        if instructions_match:
            # Find end of instructions section (next TOML section or EOF)
            section_end = len(existing)
            next_section = re.search(r"^\[", existing[instructions_match.end() :], re.MULTILINE)
            if next_section:
                section_end = instructions_match.end() + next_section.start()
            before = existing[:section_end]
            after = existing[section_end:]
            updated = before.rstrip("\n") + "\n" + start_comment + "\n" + content + "\n" + end_comment + "\n" + after
        else:
            # No [instructions] section — create one at end of file
            updated = (
                existing.rstrip("\n")
                + "\n\n[instructions]\n"
                + start_comment
                + "\n"
                + content
                + "\n"
                + end_comment
                + "\n"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")


def _convert_agent_md_to_toml(md_content: str) -> str:
    """Convert agent ``.md`` template to Codex ``.toml`` format.

    YAML frontmatter → TOML comments, ``<!-- fw-context -->`` markers
    → ``# fw-context``, body wrapped in ``[instructions]`` section.
    """
    parts = md_content.split("---", 2)
    if len(parts) >= 3:
        frontmatter_text = parts[1]
        body = parts[2]
    else:
        frontmatter_text = ""
        body = md_content

    lines: list[str] = []
    for line in frontmatter_text.strip().splitlines():
        m = re.match(r"^(name|description):\s*(.*)", line)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            lines.append(f"# {key}: {value}")
    if lines:
        lines.append("")

    lines.append("[instructions]")
    # Convert HTML comment markers to TOML hash-comment markers
    body = body.replace("<!-- fw-context -->", "# fw-context")
    body = body.replace("<!-- /fw-context -->", "# /fw-context")
    lines.append(body.strip())
    return "\n".join(lines) + "\n"
