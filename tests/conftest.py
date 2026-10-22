"""Shared fixtures for fw-context-mcp tests.

Provides:
- Standard fixtures: tmpdir, temp_db, populated_db, isolation, etc.
- Auto-skip for missing dependencies (Ollama, gcc/libclang)
- Test result logging to tests/results/
- M4: v2 write-authority defaults OFF for tests (existing fixtures were
  written for legacy-primary behavior; set FW_CONTEXT_V2_WRITE_AUTHORITY=1
  in a specific test to exercise the v2-authority path).
"""

from __future__ import annotations

import gc
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

# M4: v2 write-authority defaults OFF for the test suite.  Existing test
# fixtures were written for legacy-primary behavior; running with authority
# ON would correctly trigger CASE D (IndexIntegrityError) on synthetic
# incomplete-World fixtures.  Tests that specifically exercise the v2-
# authority path should set FW_CONTEXT_V2_WRITE_AUTHORITY=1 explicitly.
# The M4 gate's C24 regression also runs with this set to "0".
#
# M5: legacy compatibility mode defaults ON for the test suite.  Existing
# fixtures index into the legacy DB and expect legacy postprocess to run.
# Production defaults to v2-only (compat=OFF); the dedicated M5 production-
# default test lane (run_m5_runtime_tests.py) exercises v2-only behavior.
if os.environ.get("FW_CONTEXT_V2_WRITE_AUTHORITY") is None:
    os.environ["FW_CONTEXT_V2_WRITE_AUTHORITY"] = "0"
if os.environ.get("FW_CONTEXT_SHADOW_V2") is None:
    os.environ["FW_CONTEXT_SHADOW_V2"] = "0"
# M5: existing fixtures need the legacy mirror — opt into compat mode.
if os.environ.get("FW_CONTEXT_LEGACY_COMPATIBILITY_MODE") is None:
    os.environ["FW_CONTEXT_LEGACY_COMPATIBILITY_MODE"] = "1"
# M6/mcp-surface GREEN block: the legacy fixtures (no M6 store, no v2
# store) are exercised through the legacy path.  v2 read authority is
# turned OFF so a fixture without a v2 store reaches legacy_fn instead of
# failing closed (M3B design).  Symmetric with V2_WRITE_AUTHORITY=0 above.
if os.environ.get("FW_CONTEXT_V2_READ_AUTHORITY") is None:
    os.environ["FW_CONTEXT_V2_READ_AUTHORITY"] = "0"
# Wave fusion: the M6 read gate defaults ON in production, but the
# legacy fixtures here have no M6 store — a gate that fails closed
# would answer RuntimeError where the test expects the legacy
# structured error.  Symmetric with V2_READ_AUTHORITY above; the tests
# that exercise the gate set it to 1 themselves.
if os.environ.get("FW_CONTEXT_M6_READ_AUTHORITY") is None:
    os.environ["FW_CONTEXT_M6_READ_AUTHORITY"] = "0"

# ── Result logging ─────────────────────────────────────────────────────────

_RESULTS_DIR = Path(__file__).resolve().parent / "results"

log = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _reset_v2_flag_caches():
    """Reset v2 flag caches before each test (M4 + M5).

    The `_v2_write_authority_on()`, `_v2_shadow_flag_on()`, and
    `_legacy_compat_on()` functions cache their result in module globals.
    Without this fixture, a test that sets the env var would pollute the
    cache for subsequent tests.
    """
    try:
        from fw_context_mcp.indexer._unit_processor import (
            _reset_v2_write_authority_cache, _reset_v2_flag_cache,
            _reset_legacy_compat_cache)
        _reset_v2_write_authority_cache()
        _reset_v2_flag_cache()
        _reset_legacy_compat_cache()
    except Exception:
        pass  # module not importable in some test contexts
    yield
    try:
        from fw_context_mcp.indexer._unit_processor import (
            _reset_v2_write_authority_cache, _reset_v2_flag_cache,
            _reset_legacy_compat_cache)
        _reset_v2_write_authority_cache()
        _reset_v2_flag_cache()
        _reset_legacy_compat_cache()
    except Exception:
        pass


def _ensure_results_dir() -> Path:
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return _RESULTS_DIR


def _check_ollama() -> bool:
    """Check if Ollama is running and reachable.

    ``trust_env=False``: the probe targets 127.0.0.1 and must never traverse
    a proxy.  Under a corporate proxy env (``https_proxy`` set), a raw
    ``httpx.get`` routes the loopback probe through the proxy — the answer
    then describes the proxy, not Ollama (R2: made the ollama-marker skip
    condition honest; without it the live LLM-analysis tests could attempt
    a real run behind a hijacked probe and fail instead of skip).
    """
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2.0, trust_env=False)
        return resp.status_code == 200
    except Exception:
        return False


def _check_gcc() -> bool:
    """Check if gcc is available on PATH."""
    return shutil.which("gcc") is not None


# Cache checks so we don't spawn subprocesses / HTTP calls for every test
_OLLAMA_AVAILABLE: bool | None = None
_GCC_AVAILABLE: bool | None = None


def ollama_available() -> bool:
    global _OLLAMA_AVAILABLE
    if _OLLAMA_AVAILABLE is None:
        _OLLAMA_AVAILABLE = _check_ollama()
    return _OLLAMA_AVAILABLE


def gcc_available() -> bool:
    global _GCC_AVAILABLE
    if _GCC_AVAILABLE is None:
        _GCC_AVAILABLE = _check_gcc()
    return _GCC_AVAILABLE


# ── Auto-skip hooks ────────────────────────────────────────────────────────

_SESSION_START: float | None = None


def pytest_configure(config):
    """Register custom markers and ensure results directory exists."""
    _ensure_results_dir()


def pytest_sessionstart(session):
    """Record session start time for duration tracking."""
    global _SESSION_START
    _SESSION_START = time.monotonic()


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests that require unavailable dependencies.

    - Tests marked ``ollama`` are skipped when Ollama is not running.
    - Tests marked ``libclang`` are skipped when gcc is not on PATH.
    """
    skip_ollama = pytest.mark.skip(reason="Ollama not available — install and start it for these tests")
    skip_libclang = pytest.mark.skip(reason="gcc not available — install gcc for indexing tests")

    for item in items:
        if item.get_closest_marker("ollama"):
            if not ollama_available():
                item.add_marker(skip_ollama)

        if item.get_closest_marker("libclang"):
            if not gcc_available():
                item.add_marker(skip_libclang)


def pytest_sessionfinish(session, exitstatus):
    """Write test run summary to tests/results/<timestamp>.json."""
    global _SESSION_START

    results_dir = _ensure_results_dir()
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
    result_file = results_dir / f"test_run_{timestamp}.json"

    failed = getattr(session, "testsfailed", 0)
    collected = session.testscollected
    duration = round(time.monotonic() - _SESSION_START, 3) if _SESSION_START else None

    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "exit_code": exitstatus,
        "tests_passed": collected - failed,
        "tests_failed": failed,
        "tests_collected": collected,
        "duration_s": duration,
        "ollama_available": ollama_available(),
        "gcc_available": gcc_available(),
    }

    result_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Also write latest.json for convenience
    latest = results_dir / "latest.json"
    latest.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── Standard fixtures ──────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _isolate_index_dir():
    """Redirect fw-context index writes to a session temp dir.

    Sets ``FW_CONTEXT_INDEX_DIR`` for the whole test run, so every test
    (including new tests) writes index databases to a temp dir instead of
    the real ``~/.fw-context/index``.  The env var is inherited by any
    subprocess a test spawns, so CLI invocations stay isolated too.  The
    temp dir is removed at session end.

    W1: ``fw-context index`` now REALLY spawns a watcher daemon on win32
    (pre-W1 the spawn died on ``os.O_NOFOLLOW``), and the daemon holds
    ``daemon.log`` open — a Windows rmtree of a directory containing an
    open file fails.  Session teardown therefore terminates every daemon
    the session spawned (identity-checked via its own marker) before the
    directory goes away.
    """
    import shutil

    prev = os.environ.get("FW_CONTEXT_INDEX_DIR")
    d = tempfile.mkdtemp(prefix="fw-context-index-")
    os.environ["FW_CONTEXT_INDEX_DIR"] = d
    try:
        yield d
    finally:
        _terminate_session_daemons(Path(d))
        if prev is None:
            os.environ.pop("FW_CONTEXT_INDEX_DIR", None)
        else:
            os.environ["FW_CONTEXT_INDEX_DIR"] = prev
        shutil.rmtree(d, ignore_errors=True)


def _terminate_session_daemons(index_dir: Path) -> None:
    """Terminate watcher daemons spawned under *index_dir* this session."""
    from fw_context_mcp.platform import process as _process
    from fw_context_mcp.platform import read_pid_marker

    for pid_file in index_dir.rglob("watcher.pid"):
        try:
            marker = read_pid_marker(pid_file)
        except (OSError, ValueError):
            continue
        if marker is None or marker.pid == os.getpid():
            continue
        try:
            _process.terminate(marker.pid, marker, grace_s=2.0)
        except Exception:
            pass


@pytest.fixture
def tmpdir():
    # Windows adjudication: an sqlite3 connection that a test's executor kept
    # in a reference cycle (exception tracebacks hold frames) can still be
    # alive at cleanup, and unlinking an open test.db dies with WinError 32.
    # One gc.collect() breaks the cycle and closes the connection; the retry
    # then succeeds.  On POSIX an open file unlinks fine, so no dance there.
    d = tempfile.mkdtemp()
    try:
        yield Path(d)
    finally:
        if sys.platform == "win32":
            # An executor that the handler registered for a db inside *d*
            # keeps its connection open past the test — close it, or the
            # rmtree below dies on WinError 32.
            try:
                from fw_context_mcp.mcp.shared.executor import invalidate_executor

                for p in Path(d).rglob("*.db"):
                    invalidate_executor(str(p.resolve()))
            except Exception:
                pass
            gc.collect()
            for attempt in range(5):
                try:
                    shutil.rmtree(d)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.2 * (attempt + 1))
                    # A buffered LogRecord that carries exc_info holds the
                    # frames of the failure — and through them the executor
                    # and its connection.  Cutting the traceback lets the
                    # cycle collector free the connection for the next try.
                    gc.collect()
                    for o in gc.get_objects():
                        if isinstance(o, logging.LogRecord) and o.exc_info:
                            o.exc_info = None
                    gc.collect()
        else:
            shutil.rmtree(d)


@pytest.fixture
def fake_arm_gcc_toolchain(tmpdir):
    """Create a minimal fake ARM GCC toolchain directory structure."""
    toolchain = tmpdir / "gcc-arm-none-eabi-9-2020-q2-update"
    bin_dir = toolchain / "bin"
    bin_dir.mkdir(parents=True)

    compiler = bin_dir / "arm-none-eabi-g++"
    compiler.touch()

    gcc_include = toolchain / "lib" / "gcc" / "arm-none-eabi" / "12.3.1" / "include"
    gcc_include.mkdir(parents=True)
    gcc_include_fixed = toolchain / "lib" / "gcc" / "arm-none-eabi" / "12.3.1" / "include-fixed"
    gcc_include_fixed.mkdir(parents=True)

    libc_include = toolchain / "arm-none-eabi" / "include"
    libc_include.mkdir(parents=True)

    cxx_include = toolchain / "arm-none-eabi" / "include" / "c++" / "12.3.1"
    cxx_include.mkdir(parents=True)

    return toolchain, compiler


@pytest.fixture
def compile_commands_json(tmpdir):
    """Create a minimal compile_commands.json for testing."""
    data = [
        {
            "directory": str(tmpdir),
            "file": "src/main.cpp",
            "arguments": [
                "arm-none-eabi-g++",
                "-std=c++14",
                "-mcpu=cortex-m4",
                "-Os",
                "-DFOO=1",
                "-Isrc",
                "-o", "build/main.o",
                "-MD",
                "-MF", "build/main.d",
                "src/main.cpp",
            ],
        },
        {
            "directory": str(tmpdir),
            "file": "lib/helper.c",
            "arguments": [
                "arm-none-eabi-gcc",
                "-std=c11",
                "-O2",
                "-Ilib",
                "-o", "build/helper.o",
                "-MP",
                "lib/helper.c",
            ],
        },
    ]
    path = tmpdir / "compile_commands.json"
    path.write_text(__import__("json").dumps(data))
    return path


@pytest.fixture
def temp_db(tmpdir):
    """Create an empty SQLite database for testing."""
    from fw_context_mcp.indexer.db import open_db

    db_path = tmpdir / "test.db"
    conn = open_db(db_path)
    yield conn
    conn.close()


@pytest.fixture
def populated_db(temp_db):
    """Database with a project and build config pre-inserted."""
    from fw_context_mcp.indexer.db import transaction, upsert_build_config, upsert_project

    with transaction(temp_db):
        upsert_project(temp_db, "proj-001", "test-project", "/tmp/test-project")
        upsert_build_config(temp_db, "hash-deadbeef", "proj-001", "/tmp/compile_commands.json")

    return temp_db


@pytest.fixture
def isolation():
    """Isolate config file writes by redirecting to a temp directory."""
    import fw_context_mcp.config.settings as settings

    old_global = settings._GLOBAL_CONFIG_PATH
    old_project_dir = settings._PROJECT_CONFIG_DIR

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        settings._GLOBAL_CONFIG_PATH = tmp / "global.toml"
        settings._PROJECT_CONFIG_DIR = ".fw-context-test"
        yield tmp
        settings._GLOBAL_CONFIG_PATH = old_global
        settings._PROJECT_CONFIG_DIR = old_project_dir
