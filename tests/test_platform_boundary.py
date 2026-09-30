"""Platform-boundary grep gates (D1).

- ``import fcntl`` may appear ONLY inside ``src/fw_context_mcp/platform/``.
- ``os.name`` branching may appear ONLY inside ``src/fw_context_mcp/platform/``.
  Business modules must ask the facade, not the operating system.
- Text-mode ``open()``/``os.fdopen()`` in the W1-touched modules must pass
  an explicit ``encoding=`` (D6 encoding policy).

The checks walk the AST so string content and comments do not trip them.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "fw_context_mcp"
_PLATFORM_DIR = _SRC / "platform"

_TOUCHED_TEXT_MODULES = [
    _SRC / "mcp" / "background.py",
    _SRC / "mcp" / "daemon.py",
    _SRC / "mcp" / "shared" / "pid_file.py",
    _SRC / "indexer" / "db" / "_locking.py",
]


def _python_files() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestNoFcntlOutsidePlatform:
    def test_no_import_fcntl_outside_platform(self):
        offenders: list[str] = []
        for path in _python_files():
            if _PLATFORM_DIR in path.parents:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name.split(".")[0] == "fcntl" for alias in node.names):
                        offenders.append(f"{path}:{node.lineno} import fcntl")
                elif isinstance(node, ast.ImportFrom) and node.module == "fcntl":
                    offenders.append(f"{path}:{node.lineno} from fcntl import ...")
        assert offenders == [], (
            "import fcntl escaped the platform boundary:\n" + "\n".join(offenders)
        )


class TestNoOsNameBranchingOutsidePlatform:
    def test_no_os_name_reference_outside_platform(self):
        offenders: list[str] = []
        for path in _python_files():
            if _PLATFORM_DIR in path.parents:
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "name"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                ):
                    offenders.append(f"{path}:{node.lineno} os.name")
        assert offenders == [], (
            "os.name branching must live in fw_context_mcp/platform/:\n"
            + "\n".join(offenders)
        )


class TestEncodingPolicyOnTouchedModules:
    def test_text_mode_opens_carry_explicit_encoding(self):
        offenders: list[str] = []

        def _check_call(node: ast.Call, path: Path, what: str) -> None:
            func = node.func
            if what == "open":
                # Plain open() builtin only — .open() file-handle methods
                # (Path.open, lock adapters) are not text-mode opens.
                if not isinstance(func, ast.Name) or func.id != "open":
                    return
            else:  # fdopen — builtin form and os.fdopen attribute form
                is_name = isinstance(func, ast.Name) and func.id == "fdopen"
                is_os_fdopen = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "fdopen"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                )
                if not (is_name or is_os_fdopen):
                    return
            mode = ""
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            if "b" in mode:
                return  # binary mode needs no encoding
            has_encoding = any(kw.arg == "encoding" for kw in node.keywords)
            if not has_encoding:
                offenders.append(f"{path}:{node.lineno} {what}(...) without encoding=")

        for path in _TOUCHED_TEXT_MODULES:
            if not path.exists():
                offenders.append(f"{path}: missing (expected present)")
                continue
            tree = _parse(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    _check_call(node, path, "open")
                    _check_call(node, path, "fdopen")
        assert offenders == [], (
            "D6: text-mode handles need encoding='utf-8', errors='replace':\n"
            + "\n".join(offenders)
        )
