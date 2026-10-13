"""fw-context MCP server — build-aware code intelligence for embedded C/C++ projects.

Serves 42 MCP tools and 4 MCP resources via FastMCP (stdio transport).

**Error contract (ADR-006 c):** a handler that reports a business error
(a top-level ``"error"`` dict/list record) or RAISES an exception leaves the
wire as ``isError=true``.  ``{"info": ...}`` and ``{"warning": ...}`` records
stay ordinary (``isError=false``) results — they are degraded answers, not
failures.  The JSON body of an error result is preserved in the content text.

**Concurrency model — single-tool execution with busy rejection:**
The server processes at most ONE tool request at a time.  If a request
arrives while another is already running, the server waits up to 5 s for
the running request to finish.  If it does not finish within that window,
the new request is rejected with a ``"server busy"`` error that tells the
client which tool is currently executing.

**Why single-tool?**  The underlying SQLite database is WAL-mode but not
designed for concurrent writers.  Parallel requests serialize on the
SQLite executor anyway: 40 concurrent requests × 3 s each = 120 s for
request #40 → client timeout → "Connection closed".  Explicit busy
rejection lets the client retry immediately instead of waiting for a
timeout.

**Tool registration pattern:**  Each handler module (search.py,
callgraph.py, etc.) exports plain functions with type-annotated
signatures.  ``server.py`` wraps them with ``_wrap_tool()`` for error
boundaries, async dispatch, and MCP progress notifications, then
registers with ``mcp.tool()``.  This separation keeps handlers testable
as plain functions without MCP dependencies.

**Handler wrap modes:**
- Sync handlers (BFS graph traversals, SQLite queries): dispatched to
  ``asyncio.to_thread()`` to avoid blocking the event loop.
- Async handlers (Ollama HTTP calls, concurrent I/O): run directly
  on the event loop.
Both modes send MCP progress notifications every 5 s to prevent
client-side connection timeouts.

**Lifecycle:**
1. ``main()`` resolves the project root → validates initialization
   state → sets appropriate sentinels for tools to report status.
2. Pre-marks the database as integrity-checked (avoids startup timeout
   on large DBs — integrity was verified during indexing).
3. Spawns a file-watcher daemon for auto-reindex (once per project).
4. Enters the FastMCP stdio event loop and processes tool requests.

**Search & lookup tools** (delegate to ``fw_context_mcp.search`` pipeline):
``search_code`` (FTS5), ``lookup_symbol`` (exact/prefix), ``smart_search``
(Ollama-driven), ``semantic_search`` (embeddings + cosine similarity),
``search_bodies`` (function body text), ``search_content`` (full file content).

**Symbol reading tools:** ``get_source``, ``get_file_map``, ``get_symbol_context``,
``explain_symbol``, ``read_file``.

**Call graph tools** (require ``--refs`` index): ``find_callers``,
``find_references``, ``find_call_path``, ``find_all_callers_recursive``,
``find_callees_recursive``, ``find_hotspots``, ``find_dead_code``,
``get_vector_table``,
``find_wrapper_callers``, ``find_indirect_call_sites``,
``find_indirect_targets``, ``trace_data_flow``.

**Conditional compilation tools:** ``explain_condition``,
``list_conditional_regions``.

**Variant analysis tools:** ``compare_variants`` (structural comparison of
two indexed builds for a symbol/file/build facts), ``group_equivalent_variants``
(behavior grouping across builds).  Both compare stored index records
textually — results are labeled ``method: "text"``, and only the body
fingerprint dimension is fingerprint-based (W6 / AUD-R5).

**Inheritance tools:** ``get_inheritance_chain``, ``get_class_members``,
``get_template_instances``, ``get_method_overrides``.

**Variables:** ``find_variables``.

**Maintenance tools:** ``get_active_build``, ``get_environment_status``,
``check_dependencies``, ``list_projects``, ``list_variants``,
``get_project_info``, ``check_ollama``, ``configure_llm``,
``reindex_file``, ``reset_index``.  All of them run under the maintenance
lock (ADR-006 b) and serialize against in-flight queries.

**MCP Resources:** ``fw-context://stats``, ``fw-context://projects``,
``fw-context://symbols/{name}``, ``fw-context://skills/fw-review``.
"""

import asyncio
import functools
import inspect
import json
import logging
import os
import re
import sqlite3
import sys
import textwrap
import time
from pathlib import Path
from typing import Annotated

from mcp import types as mcp_types
from mcp.server.fastmcp import Context as MCPContext
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..utils import resolve_project_root
from .background import _ensure_daemon_running
from .handlers import callgraph, conditionals, inheritance, maintenance, search, source, variables, variants
from .shared.context import _check_server_ready, _integrity_checked

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

#: Ensures at most one tool handler runs at a time.  Without this, parallel
#: requests pile up on the serialized SQLite executor: 40 concurrent requests
#: × 3 s each = 120 s for request #40 → client timeout → "Connection closed".
#: With the lock, excess requests receive an immediate "server busy" error and
#: the client can retry.  Timeout 5 s — long enough for a just-finished
#: handler to release the lock, short enough to avoid client-side timeouts.
#:
#: **Why asyncio.Lock instead of threading.Lock?**  Both async and sync
#: paths are wrapped in ``async def`` wrappers that run on the event loop
#: (sync handlers use ``asyncio.to_thread`` but the wrapper itself is
#: async).  ``threading.Lock`` would block the event loop thread — an
#: ``asyncio.Lock`` integrates with the cooperative scheduler.
_SERVER_LOCK = asyncio.Lock()
_SERVER_LOCK_TIMEOUT = 5.0  # seconds

#: Serializes maintenance tools against each other (ADR-006 b).  Maintenance
#: calls may MUTATE the index database (``reindex_file``, ``reset_index``), so
#: two of them must never interleave, and none may run while a query holds
#: ``_SERVER_LOCK`` (T11 C7: a maintenance call racing a live query used to
#: read a half-written index).  Unlike ``_SERVER_LOCK`` there is no busy
#: rejection here: maintenance WAITS for the running query, it does not
#: bounce the client.
_MAINT_LOCK = asyncio.Lock()



def _wrap_debug(msg: str) -> None:
    """Write to /tmp/fw-context-debug.log when FW_CONTEXT_DEBUG_WRAP=1."""
    if os.environ.get("FW_CONTEXT_DEBUG_WRAP") != "1":
        return
    try:
        with open("/tmp/fw-context-debug.log", "a") as f:
            f.write(f"{msg}\n")
    except OSError:
        pass


def _is_error_payload(result: object) -> bool:
    """ADR-006 (c): does this handler payload report a business error?

    Only a TOP-LEVEL ``"error"`` key counts.  ``{"info": ...}`` and
    ``{"warning": ...}`` records are degraded answers, not failures — they
    stay ordinary (``isError=false``) results.  A data record that merely
    nests an error-shaped dict deeper inside is untouched.
    """
    if isinstance(result, dict):
        return "error" in result
    if isinstance(result, list):
        return any(isinstance(r, dict) and "error" in r for r in result)
    return False


def _error_envelope(result):
    """Re-emit a handler business error as an ``isError=true`` result
    (ADR-006 c).

    The MCP SDK only converts RAISED exceptions; a handler that CATCHES a
    failure and returns ``{"error": ...}`` used to reach the client as a
    success result (the C3 drift: three encodings for one concept).  This
    envelope makes the two encodings agree on the wire while keeping the
    JSON body in the text content, so clients that parsed the old
    success-shaped payload keep working.

    ``structuredContent`` mirrors what the success path produces for the
    same return shape — FastMCP wraps list-returning tools under a
    ``"result"`` key — because the lowlevel server validates a passthrough
    ``CallToolResult`` against the tool's output schema.
    """
    body = json.dumps(result, indent=2, default=str)
    if isinstance(result, list):
        structured: object = {"result": json.loads(body)}
    else:
        structured = json.loads(body)
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=body)],
        structuredContent=structured,
        isError=True,
    )


def _wrap_tool(fn, maintenance: bool = False):
    """Wrap a tool handler: error boundary + async dispatch + MCP progress.

    Sync handlers are dispatched to a background thread via
    ``asyncio.to_thread``.  The event loop sends MCP progress
    notifications every 5 s to keep the client alive during long-running
    queries (BFS call-graph traversal, etc.).  The 5 s interval is
    deliberate: some clients (e.g. OpenCode) kill the connection after
    ~15 s without progress, so notifications at t=0/5/10 s provide
    coverage — do NOT lengthen the interval.

    All handlers get an error boundary — no unhandled exception can crash
    the server process.  ``BrokenPipeError`` on the async path is treated
    as a clean exit (client disconnected mid-query) — the server exits 0.

    **Error contract (ADR-006 c):** a raised exception re-raises after
    logging (the MCP layer converts it to ``isError=true``); a handler
    business error dict is re-emitted through ``_error_envelope`` with the
    same ``isError=true`` outcome and the same JSON body.

    **Maintenance mode (``maintenance=True``, ADR-006 b):** the tool waits
    for any in-flight query instead of busy-rejecting (T11 C7 — a call that
    can mutate the index must never race a live query), then takes
    ``_MAINT_LOCK`` so two maintenance calls never interleave.  Lock order
    is always ``_SERVER_LOCK`` then ``_MAINT_LOCK``.

    **Why two code paths (sync vs async)?**  Sync handlers execute
    blocking SQLite queries and libclang traversal.  Running them on
    the event loop would freeze all other coroutines (ping thread,
    daemon health checks, progress notifications).  ``asyncio.to_thread``
    moves them to the default thread pool executor, keeping the event
    loop responsive.
    """

    async def _acquire_slot(tool_name: str, ctx) -> None:
        """Take the execution slot (ADR-006 b).

        Queries busy-reject after 5 s.  Maintenance tools WAIT for the
        running query instead — bouncing a maintenance call would leave the
        caller unsure whether the mutation happened — and then serialize
        against each other via ``_MAINT_LOCK``.
        """
        if maintenance:
            if ctx is not None:
                try:
                    await ctx.info(
                        f"{tool_name}: waiting for the active query to finish")
                except Exception:
                    pass
            await _SERVER_LOCK.acquire()
            try:
                await _MAINT_LOCK.acquire()
            except BaseException:
                _SERVER_LOCK.release()
                raise
            return
        try:
            await asyncio.wait_for(
                _SERVER_LOCK.acquire(), timeout=_SERVER_LOCK_TIMEOUT)
        except TimeoutError:
            _wrap_debug(f"[lock] {tool_name} BUSY — lock timeout")
            raise

    def _release_slot() -> None:
        if maintenance:
            _MAINT_LOCK.release()
        _SERVER_LOCK.release()

    if inspect.iscoroutinefunction(fn):
        # Async path — handler yields to event loop (e.g., Ollama HTTP calls).
        # Manual functools.wraps: copy __name__/__doc__/__module__/__qualname__
        # but NOT __annotations__ — functools.wraps would overwrite the
        # wrapper's annotations (which include ctx) with fn's annotations
        # (which don't), causing find_context_parameter to miss ctx.
        @functools.wraps(fn, assigned=("__module__", "__name__", "__qualname__", "__doc__"))
        async def _wrapper(*a, ctx: MCPContext | None = None, **kw):
            start = time.monotonic()
            _wrap_debug(f"[async] {fn.__name__} ctx={'P' if ctx is not None else 'N'} ann={list(_wrapper.__annotations__.keys())}")
            try:
                await _acquire_slot(fn.__name__, ctx)
            except TimeoutError:
                _wrap_debug(f"[async] {fn.__name__} BUSY — lock timeout")
                return _error_envelope([{"error": f"The MCP server is currently processing another query ({fn.__name__} cannot run right now because a different tool is already executing). Wait for the active query to complete, then retry. If this error persists, the active query may be stuck — restart the server with `fw-context watch`."}])
            try:
                task = asyncio.ensure_future(fn(*a, **kw))

                # Progress loop: poll every 5 s until the handler completes.
                # Each iteration sends an MCP progress notification to the
                # client via ctx.info() — this keeps the connection alive
                # through client-side idle-timeout gates.  If the handler
                # takes >300 s, it is cancelled and all active SQLite
                # connections are interrupted.
                while not task.done():
                    done, pending = await asyncio.wait([task], timeout=5.0)
                    if task in done:
                        break
                    elapsed = int(time.monotonic() - start)
                    _wrap_debug(f"[async-loop] {fn.__name__} t={elapsed}s task_done={task.done()}")
                    if ctx is not None:
                        try:
                            await ctx.info(f"{fn.__name__}: {elapsed}s")
                            _wrap_debug(f"[async-loop] {fn.__name__} ctx.info() OK at {elapsed}s")
                        except Exception as e:
                            _wrap_debug(f"[async-loop] {fn.__name__} ctx.info() FAIL: {e}")
                    if elapsed > 300:
                        task.cancel()
                        try:
                            from .shared.context import interrupt_all

                            interrupt_all()
                        except Exception:  # nosec B110 — best-effort interrupt on timeout
                            pass
                        return _error_envelope(
                            [{"error": f"Tool {fn.__name__} timed out after {elapsed}s"}])

                exception = task.exception()
                if exception is not None:
                    if isinstance(exception, BrokenPipeError):
                        sys.exit(0)
                    if isinstance(exception, (KeyboardInterrupt, SystemExit)):
                        # Never mask control-flow exceptions — let them
                        # propagate exactly as a bare handler would (the
                        # finally below releases the slot first).
                        raise exception
                    # ADR-006 (c): raised exceptions leave the wire as
                    # isError=true.  Re-raise and let the MCP layer convert;
                    # the boundary only guarantees the server survives it.
                    log.error("Tool %s raised", fn.__name__, exc_info=exception)
                    raise exception
                result = task.result()
                if _is_error_payload(result):
                    return _error_envelope(result)
                return result
            finally:
                _release_slot()
        return _wrapper

    # Sync path — handler blocks (SQLite queries, libclang traversal).
    # Dispatched to ``asyncio.to_thread`` so the event loop stays free
    # for progress notifications and the ping thread.
    @functools.wraps(fn, assigned=("__module__", "__name__", "__qualname__", "__doc__"))
    async def _wrapper(*a, ctx: MCPContext | None = None, **kw):
        start = time.monotonic()
        _wrap_debug(f"[sync] {fn.__name__} ctx={'P' if ctx is not None else 'N'} ann={list(_wrapper.__annotations__.keys())}")
        try:
            await _acquire_slot(fn.__name__, ctx)
        except TimeoutError:
            _wrap_debug(f"[sync] {fn.__name__} BUSY — lock timeout")
            return _error_envelope([{"error": f"Server busy — {fn.__name__} cannot run, another query is in progress. Retry in a few seconds."}])
        try:
            # functools.partial captures *a and **kw into a zero-argument
            # callable that asyncio.to_thread can execute on the thread pool.
            task = asyncio.ensure_future(
                asyncio.to_thread(functools.partial(fn, *a, **kw))
            )

            # Same progress loop as async path — the thread pool executor
            # runs the handler while the event loop polls and sends progress
            # notifications every 5 s.
            while not task.done():
                done, pending = await asyncio.wait([task], timeout=5.0)
                if task in done:
                    break
                elapsed = int(time.monotonic() - start)
                _wrap_debug(f"[sync-loop] {fn.__name__} t={elapsed}s task_done={task.done()}")
                if ctx is not None:
                    try:
                        await ctx.info(f"{fn.__name__}: {elapsed}s")
                        _wrap_debug(f"[sync-loop] {fn.__name__} ctx.info() OK at {elapsed}s")
                    except Exception as e:
                        _wrap_debug(f"[sync-loop] {fn.__name__} ctx.info() FAIL: {e}")
                if elapsed > 300:
                    task.cancel()
                    try:
                        from .shared.context import interrupt_all

                        interrupt_all()
                    except Exception:  # nosec B110 — best-effort interrupt on timeout
                        pass
                    return _error_envelope(
                        [{"error": f"Tool {fn.__name__} timed out after {elapsed}s"}])

            exception = task.exception()
            if exception is not None:
                if isinstance(exception, BrokenPipeError):
                    sys.exit(0)
                if isinstance(exception, (KeyboardInterrupt, SystemExit)):
                    # Never mask control-flow exceptions — let them
                    # propagate exactly as a bare handler would (the
                    # finally below releases the slot first).
                    raise exception
                # ADR-006 (c): raised exceptions leave the wire as
                # isError=true.  Re-raise and let the MCP layer convert;
                # the boundary only guarantees the server survives it.
                log.error("Tool %s raised", fn.__name__, exc_info=exception)
                raise exception
            result = task.result()
            if _is_error_payload(result):
                return _error_envelope(result)
            return result
        finally:
            _release_slot()
    return _wrapper


# ── Project selector ──────────────────────────────────────────────────

#: Text of the ``project`` parameter.  One constant feeds both the JSON
#: schema and the docstring, because a tool that describes the parameter
#: differently from its neighbour teaches the model a different rule for
#: that tool.
_PROJECT_PARAM_DESCRIPTION = (
    "Project name or project_id — call list_projects to get them. Use it to "
    "ask about a project that is not the project of the current directory. It "
    "is an alternative to project_root, which takes a root path. Give one of "
    "the two, not both."
)

#: Added to the description of ``project_root``, which now also resolves a
#: name and a project_id.  The sentence must stay short and must point at
#: ``project``: two fields that accept the same three things teach the
#: model nothing about which one to write.
_PROJECT_ROOT_DESCRIPTION_SUFFIX = (
    " This field also accepts a project name or a project_id, but project is "
    "the clear field for those."
)

#: Used when a docstring has no ``project_root:`` entry to insert after.
#: Every handler has one today, thus this is the guard for a handler that
#: someone adds later without an Args: section.
_PROJECT_DOC_FALLBACK = (
    "\nProject selection:\n"
    + textwrap.fill(
        f"project: {_PROJECT_PARAM_DESCRIPTION}",
        width=76,
        initial_indent="    ",
        subsequent_indent="        ",
    )
    + "\n"
)


def _document_project_parameter(doc: str) -> str:
    """Write the ``project`` entry into the ``Args:`` section of *doc*.

    FastMCP sends the docstring as the description of the tool.  A
    parameter that the ``Args:`` section does not name reaches the model
    as a bare word in the JSON schema, and the model reads the list of
    parameters in ``Args:``.  The entry therefore goes next to
    ``project_root``, and not at the end of the docstring.

    The indentation comes from the ``project_root:`` line that the entry
    follows.  It is not a constant: some handlers hold a docstring that
    starts at column 0, and others hold one that keeps the indentation of
    the source.

    Args:
        doc: Docstring of the handler.

    Returns:
        The docstring with one more entry in its ``Args:`` section.  When
        the docstring has no ``project_root:`` line, the result holds a
        separate ``Project selection:`` block at the end instead.
    """
    lines = doc.splitlines()
    start = None
    indent = ""
    for index, line in enumerate(lines):
        found = re.match(r"([ \t]*)project_root:", line)
        if found:
            start, indent = index, found.group(1)
            break
    if start is None:
        return doc.rstrip() + "\n" + _PROJECT_DOC_FALLBACK

    # Step over the continuation lines of the project_root entry.  A line
    # that is empty, or that is not deeper than the entry, starts
    # something else.
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip() or len(line) - len(line.lstrip()) <= len(indent):
            break
        end += 1

    entry = textwrap.fill(
        f"project: {_PROJECT_PARAM_DESCRIPTION}",
        width=79,
        initial_indent=indent,
        subsequent_indent=indent + "    ",
    ).splitlines()
    # splitlines() drops a final newline; put it back so the docstring
    # keeps the shape that it had.
    tail = "\n" if doc.endswith("\n") else ""
    return "\n".join([*lines[:end], *entry, *lines[end:]]) + tail


def _merge_project_selector(tool_name: str, kwargs: dict) -> dict:
    """Move a ``project`` value into ``project_root``.

    ``resolve_project_root`` accepts a project name, a ``project_id``, or
    a path, thus one handler parameter carries all three and no handler
    signature changes.

    Args:
        tool_name: Name of the tool, for the error message.
        kwargs: Keyword arguments of the call.  Changed in place.

    Returns:
        The same dict, with ``project`` removed and its value written to
        ``project_root``.

    Raises:
        ValueError: The call gives ``project`` and ``project_root``, and
            the two values are different.  fw-context does not choose one
            of them, because the answer of the losing project looks
            correct but comes from other source code.
    """
    project = kwargs.pop("project", None)
    if isinstance(project, str):
        project = project.strip()
    if not project:
        return kwargs
    project_root = kwargs.get("project_root")
    if project_root and str(project_root).strip() != project:
        raise ValueError(
            f"{tool_name}: project={project!r} and project_root={project_root!r} "
            "select different projects. Give one of the two, not both."
        )
    kwargs["project_root"] = project
    return kwargs


def _describe_project_root(param: inspect.Parameter) -> inspect.Parameter:
    """Say in the schema that ``project_root`` also takes a name or an id.

    ``resolve_project_root`` resolves a project name and a project_id as
    well as a path.  A description that names only the path is not true
    any more, and a model cannot read behaviour that no text states.

    The sentence is appended to the description that the handler wrote,
    and does not replace it: the handlers say different useful things
    there — ``list_projects`` for example explains that the parameter
    picks one of several indexed projects.

    Args:
        param: One parameter of the signature of a handler.

    Returns:
        The parameter with a longer description, or *param* unchanged
        when it is not ``project_root`` or carries no description.
    """
    if param.name != "project_root":
        return param
    metadata = getattr(param.annotation, "__metadata__", ())
    description = next(
        (text for text in (getattr(m, "description", None) for m in metadata) if text),
        None,
    )
    if description is None:
        return param
    base = getattr(param.annotation, "__origin__", param.annotation)
    # A second Field goes at the END of the Annotated metadata, and it
    # holds the description only.  Pydantic merges the Field objects of
    # an Annotated in order and the last value wins, thus every other
    # setting of the original Field stays.  A change in place would not
    # do: pydantic reads FieldInfo._attributes_set, and not the live
    # attribute, thus a written description would never reach the schema.
    return param.replace(
        annotation=Annotated[
            (
                base,
                *metadata,
                Field(description=description + _PROJECT_ROOT_DESCRIPTION_SUFFIX),
            )
        ]
    )


def _with_project_selector(fn):
    """Add a ``project`` parameter that selects a project by name or id.

    Each tool answers about ONE project.  Before this parameter existed,
    the only selector was ``project_root`` — an absolute path.  A model
    that wrote ``project="<name>"`` got no error, because pydantic
    ignores an unknown argument: the tool then answered about the project
    of the current directory, and that answer looked correct but came
    from other source code.

    The parameter is added here, and not in each of the ~30 handler
    signatures, for two reasons.  One handler that keeps the old
    signature causes exactly the failure above again, and the description
    of the parameter must read the same in every tool.

    HOW the parameter reaches the JSON schema: FastMCP builds the schema
    from ``inspect.signature(fn, eval_str=True)``, and ``inspect`` stops
    its walk along ``__wrapped__`` at the first object that has a
    ``__signature__`` attribute.  This wrapper sets ``__signature__``,
    thus the schema comes from here.  Context injection still works,
    because FastMCP finds the ``ctx`` parameter with
    ``typing.get_type_hints()``, which reads ``__annotations__``, and
    ``functools.wraps`` copies those from *fn* unchanged.

    Args:
        fn: A tool handler, or the ``_wrap_tool`` wrapper around one.

    Returns:
        A wrapper that accepts ``project``, or *fn* unchanged when it has
        no ``project_root`` parameter — ``get_project_info`` and
        ``check_ollama`` take no project.
    """
    # eval_str=True: the handler modules use PEP 563 string annotations,
    # and a stored __signature__ is given to pydantic as it is — inspect
    # does not evaluate it a second time.
    base_sig = inspect.signature(fn, eval_str=True)
    if "project_root" not in base_sig.parameters:
        return fn

    project_param = inspect.Parameter(
        "project",
        inspect.Parameter.KEYWORD_ONLY,
        default=None,
        annotation=Annotated[str | None, Field(description=_PROJECT_PARAM_DESCRIPTION)],
    )
    # A KEYWORD_ONLY parameter must come before **kwargs and after every
    # other parameter kind.
    head = [p for p in base_sig.parameters.values() if p.kind is not inspect.Parameter.VAR_KEYWORD]
    tail = [p for p in base_sig.parameters.values() if p.kind is inspect.Parameter.VAR_KEYWORD]
    head = [_describe_project_root(p) for p in head]
    new_sig = base_sig.replace(parameters=[*head, project_param, *tail])

    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def _selector(*a, **kw):
            return await fn(*a, **_merge_project_selector(fn.__name__, kw))
    else:
        @functools.wraps(fn)
        def _selector(*a, **kw):
            return fn(*a, **_merge_project_selector(fn.__name__, kw))

    # inspect.signature() reads __signature__ and stops there, thus this
    # assignment is what puts `project` into the JSON schema of the tool.
    _selector.__signature__ = new_sig
    _selector.__doc__ = _document_project_parameter(fn.__doc__ or "")
    return _selector


def _forbid_unknown_tool_arguments() -> None:
    """Make FastMCP reject an argument that the tool does not declare.

    Pydantic ignores an unknown field by default.  A call that wrote
    ``project="<name>"`` before that parameter existed thus lost the
    argument without a message, and the tool answered about the project
    of the current directory — a wrong answer that looks correct.  A
    misspelled parameter must fail, and the failure must name it.

    Every argument model that FastMCP builds is a subclass of
    ``ArgModelBase``, and pydantic merges the ``model_config`` of the
    parent when it creates the subclass.  This function therefore must
    run BEFORE the first ``mcp.tool()`` registration.

    A future release of ``mcp`` can change the name or the shape of that
    class.  The server must still start in that case, thus the failure is
    a warning in the log and not an exception.
    """
    try:
        from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase

        ArgModelBase.model_config["extra"] = "forbid"
    except (ImportError, AttributeError, TypeError) as e:
        log.warning(
            "Unknown tool arguments stay silent: cannot set extra=forbid on "
            "the FastMCP argument model (%s)", e
        )

# ── stdio-safe logging (AUD-R2 fix) ─────────────────────────────────────────
def _install_stdio_safe_logging() -> None:
    """Keep server logs OFF the Windows console and ON the stderr pipe.

    FastMCP's ``configure_logging`` (FastMCP.__init__) installs
    ``RichHandler(Console(stderr=True))`` on the ROOT logger via
    ``logging.basicConfig``.  On Windows, rich's legacy-console detection keys
    off the process's inherited console — NOT off the bound stream — so even
    with stdout/stderr piped by the MCP client, rich routes every log record
    through ``WriteConsoleW`` on the inherited console when that console
    reports no VT support.  ``WriteConsoleW`` is a blocking synchronous call
    executed on the asyncio event loop thread while it processes MCP frames.

    Observed consequence (AUD-R2; py-spy evidence
    .fw-closeout/artifacts/W2/phase_b_pyspy_dump.txt): the event loop froze
    inside ``rich._win32_console.write_text`` while emitting
    ``logging.warning("Failed to validate request: ...")`` from
    ``mcp.shared.session._receive_loop`` and NEVER dispatched the response —
    the client saw ``initialize`` / ``tools/call`` hang indefinitely (90 s and
    300 s deadlines, cold start AND restart).  The stderr pipe stayed empty
    because the log bytes went to the console, not to stderr.

    Fix: pre-install a plain ``StreamHandler(sys.stderr)`` on the root logger
    BEFORE ``FastMCP(...)`` is constructed below.  ``logging.basicConfig`` is a
    no-op once the root logger has handlers, so the console-bound RichHandler
    is never created and log records flow to the stderr pipe — the MCP
    diagnostics channel a stdio client already owns and drains.  Pipe writes
    cannot corrupt the JSON-RPC framing on stdout, and unlike the inherited
    console, stderr has a defined reader.
    """
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            return  # already stdio-safe (or explicitly configured) — keep it
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_install_stdio_safe_logging()


# ── native prewarm (AUD-R2 fix, second half) ────────────────────────────────
def _prewarm_sqlite_vec() -> None:
    """Import sqlite-vec (and transitively numpy) BEFORE the server runs.

    Why: ``SyncQueryExecutor._open_connection`` imports ``sqlite_vec`` lazily
    on the first build-aware tool call.  That first call executes on a worker
    thread while the asyncio event loop, the ping thread and AnyIO workers
    are already live.  On Windows, numpy's import chain loads several native
    DLLs (``numpy._core._multiarray_umath.pyd``, ``libscipy_openblas64_*.dll``)
    via ``LoadLibraryExW``; concurrent DLL loads inside one process contend
    on the ntdll loader lock, and when another thread holds it the import
    blocks INDEFINITELY.  Observed (AUD-R2): over real stdio,
    ``get_active_build`` NEVER answered — 90 s and 300 s deadlines, cold start
    AND restart — while the identical import from a fresh interpreter takes
    ~0.1 s, and WSL answers the whole flow in ~0.5 s (ELF loaders have no
    equivalent lock structure).  py-spy native evidence:
    .fw-closeout/artifacts/W2/phase_b_pyspy_native_blocking.txt — executor
    thread parked at ``LoadLibraryExW → LdrLoadDll → RtlEnterCriticalSection
    → RtlSleepConditionVariableCS`` inside the numpy import reached from
    ``executor.py:_open_connection``.

    Importing once HERE — on the main thread, before the event loop and the
    ping thread exist — pays the DLL-load cost in a single-threaded context
    where no other thread can hold the loader lock.  The later lazy import
    inside ``_open_connection`` then resolves against ``sys.modules`` and
    cannot block.  Best effort: any failure keeps the server startable (the
    executor already falls back to the BLOB path when sqlite-vec is absent).
    """
    try:
        import sqlite_vec  # noqa: F401  (also warms numpy + OpenBLAS DLLs)
    except ImportError:
        log.debug("sqlite-vec not installed — semantic search will use legacy BLOB fallback")
    except Exception as e:  # noqa: BLE001 — prewarm must never block startup
        log.debug("sqlite-vec prewarm skipped (%s)", e)


# The FastMCP instructions string is embedded here (not loaded from a file)
# because it must be available at import time — FastMCP reads it during
# construction.  Loading from a data file would add an I/O dependency to
# the import path and complicate packaging (MANIFEST.in, wheel data_files).
# The instructions are the "user manual" for LLM agents using fw-context
# tools — they teach tool selection, FTS5 query rules, the agent loop,
# and the LLM setup workflow.
mcp = FastMCP(
    "fw-context",
    instructions=(
        "CRITICAL: C/C++ questions → fw-context ONLY. This is NOT optional.\n\n"
        "For C/C++ code, use fw-context instead of ANY external search or\n"
        "file-reading tool.\n\n"
        "SELF-CORRECT: the moment you reach for a tool that is NOT fw-context\n"
        "for C/C++ code, stop and use the fw-context equivalent instead.\n\n"
        "EVERY ANSWER IS IFDEF-FILTERED.  fw-context gives the code that\n"
        "COMPILES for the active build.  A line of an inactive #if branch is\n"
        "blank in the content of read_file, in the text that search_content\n"
        "and search_bodies search, and in the body that get_source and\n"
        "get_symbol_context give.  Line numbers never move.  Thus a dead\n"
        "#ifdef block cannot reach you as live code.\n"
        "A comment and a preprocessor directive are part of the answer: a\n"
        "block comment keeps its closing marker, and an include guard shows.\n"
        "A blank line is thus an inactive line, or a line that is blank on\n"
        "disk, and nothing else.  Three limits:\n"
        "• The filter needs an index.  When a file changed after the last\n"
        "  index run, get_source gives the current text from the DISK, which\n"
        "  holds every branch.  It sets source_origin: \"disk\" and a\n"
        "  stale_warning — read both before you cite such a body.\n"
        "• An empty result can mean the pattern is only in a dead branch.\n"
        "  That is an answer, not a failure: the code does not compile.\n"
        "• A file can come back with every line blank.  read_file marks it\n"
        "  with all_lines_inactive and a warning.  Such a file holds code,\n"
        "  and the active build compiles none of it — NOT an empty file.\n\n"
        "TOOL SELECTION (pick the right one):\n"
        '• Symbol by exact/prefix name _____ → lookup_symbol (e.g. "uart_", "main")\n'
        '• Symbols by concept/topic _________ → search_code (e.g. "interrupt handler")\n'
        '• WHICH DEFINITION holds the code ___ → search_bodies (e.g. "attach", "SELF_TEST")\n'
        '• WHICH FILES a topic touches ______ → search_content (e.g. "extern C", "SELF_TEST")\n'
        "• Read a file, or a window of it ____ → read_file (line_numbers, start_line, end_line)\n"
        "• Read function body + callers/callees → get_symbol_context (preferred) / get_source\n"
        "• Function pointer assignments/calls _ → find_indirect_call_sites / find_indirect_targets\n"
        "• Natural-language question ________ → smart_search (slow, thorough)\n\n"
        "IMPORTANT: search_code searches symbol NAMES (what the code IS).\n"
        "search_bodies searches the TEXT OF EVERY DEFINITION — not only\n"
        "functions.  It reaches function and method bodies, AND the body of a\n"
        "class, struct, union, enum or namespace, AND a global with a multi-line\n"
        "initializer.  An enum constant, a bit field, a member declaration such\n"
        "as `InterruptIn _pin;` are all inside search_bodies.\n"
        "A match on a type answers with the type: a query for one enum constant\n"
        "returns the enum, and `match_lines` gives the line of the constant.\n"
        "Only the TEXT matches: a hit in the name, the signature or the\n"
        "`llm_analysis` of a symbol is not a hit here — that is search_code.\n"
        "Text that belongs to NO definition is out of reach — #define,\n"
        '#include, #ifdef, extern "C", and a file-scope comment.  Use\n'
        "search_content for those.\n\n"
        "search_content is the COMPLEMENT of search_bodies, not its fallback.\n"
        "search_bodies answers which definition and takes the query literally;\n"
        "search_content answers which files and widens the query.  Measured:\n"
        "`SELF_TEST` gave 6 files there and 5 definitions here, the extra file\n"
        "holding the comment `Self tester`.  For a feature footprint, run both.\n\n"
        "CITE FROM THE RIGHT FIELD:\n"
        "• search_bodies / search_content → `match_lines` holds the lines of the\n"
        "  matches.  In search_bodies, `line` is where the DEFINITION starts,\n"
        "  which in a long function is hundreds of lines away from the match.\n"
        "  Never cite `line` for a statement.\n"
        "• get_source / get_file_map → `line` and `end_line` are the extent of\n"
        "  the symbol.  Cite `file:line-end_line`.  Do not count lines yourself.\n"
        "• read_file → pass line_numbers=True, or read a window with start_line\n"
        "  and end_line.  Its bare `content` must never be counted.\n"
        "• A field with NO leading underscore is an answer to cite.  An\n"
        "  `_`-prefixed one (`_match_snippet`, `_fallback`) says where it\n"
        "  came from.\n"
        "Do not leave fw-context for a text search to find a line number.\n\n"
        "READ THE RESULT YOU ALREADY HAVE — measured on one session, 4 of 12\n"
        "calls asked again for what the payload already carried.\n"
        "1. Before calling anything for a `path:line`, re-read the last result:\n"
        "   `match_lines` and `file` are usually already there.\n"
        "2. Never repeat one query with a filter added.  Write `kind` in the\n"
        "   first call, or read `kind` on each result.\n"
        '3. Never invent a symbol name because "it should be called that".\n'
        "   Take the name from a result.  A guess costs a whole call.\n"
        "4. A caller is find_callers, never a comment that reads like one.\n\n"
        "UNTRUSTED FIELD — `llm_analysis` ({summary, inputs, outputs}) is written\n"
        "by a model, not by the code.  It is a hint that points you at a symbol.\n"
        "NEVER quote it as fact: one measured summary called an identifier\n"
        '"possibly related to sensor status", which was a guess from its name.\n'
        "Quote `source`, `signature`, or `docstring` instead.\n\n"
        "HOW EACH TOOL READS YOUR QUERY:\n"
        "• search_code, search_content — every bare term gets a trailing `*` and\n"
        '  the terms are OR-joined: "modem init" → modem* OR init*, thus a symbol\n'
        "  with EITHER word matches.  Prefer single words.\n"
        "• search_bodies — the query goes to FTS5 as you wrote it.  A space is an\n"
        "  AND of two exact tokens and NO wildcard is added: `SELF_TEST` misses\n"
        "  `Self tester`, `SELF_TEST*` finds it.  Add the `*` yourself.\n"
        "• Punctuation is not searchable anywhere — FTS5 cannot parse `.attach(`,\n"
        "  thus it is repaired into a phrase and what runs is the word `attach`.\n"
        '  search_bodies marks such an answer (`_fallback: "sanitized"` +\n'
        "  `_query_used`); the hits whose body really holds the pattern are the\n"
        "  ones with `match_lines`.\n"
        '• Underscores are word separators: "modem_init" asks for the two tokens\n'
        '  next to each other and misses modem_parser_oob_init.  Write "modem init".\n'
        "• For exact phrases use double quotes: '\"interrupt handler\"'.\n\n"
        "EMPTY RESULT STRATEGY — an empty list means NO SUCH CODE.  A query FTS5\n"
        "cannot parse comes back as a `warning` + `hint`, never as `[]`.\n"
        "1. Try a simpler/single-word query in the SAME tool first.\n"
        "2. In search_bodies, add the `*` — that tool adds none.\n"
        "3. Use lookup_symbol for known symbol names.\n"
        "4. If search_bodies returns empty, switch to search_content — it widens\n"
        '   the query and covers the preprocessor (#define, #ifdef) and extern "C",\n'
        "   which belong to no definition and thus reach no symbol body.\n"
        "5. find_callers empty → callers exist through member-field accesses\n"
        "   (obj.method()) or base-class pointers. Fall back to\n"
        '   search_bodies("function_name") which text-searches function bodies\n'
        "   independently of the call graph. If still empty, try\n"
        '   search_content("function_name").\n'
        "6. If all fw-context tools return empty, simplify query or use different tool.\n"
        "7. Only AFTER exhausting fw-context — use other available tools.\n\n"
        "project_only=True (on search_code, search_bodies, search_content, and\n"
        "callgraph tools) excludes vendor SDK code — use when asking about YOUR code.\n\n"
        "MULTI-PROJECT — a question about a DIFFERENT project:\n"
        "Each tool answers about ONE project. Without project or project_root\n"
        "this is the project of the current directory — NOT the project that\n"
        "the operator asked about.\n"
        "1. Call list_projects. It gives the name, the project_id, and the\n"
        "   root_path of each indexed project.\n"
        '2. Give project="<name>" (or project_root="<root_path>") to EVERY\n'
        "   call that follows, get_active_build included.\n"
        "Do not invent other parameter names. An unknown argument causes an\n"
        "error that names it.\n\n"
        "PARAMETER NAMES — every tool rejects an unknown argument, thus a guess\n"
        "costs a whole call.  The full schema of each tool is in the tool list.\n"
        "Read it there.  Three names cover almost every tool:\n"
        "• `name` — the symbol.  NOT `symbol`, NOT `symbol_name`.\n"
        "  (lookup_symbol, get_source, get_symbol_context, find_callers,\n"
        "  find_references, explain_symbol, and the call-graph tools)\n"
        "• `file_path` — the file.  (read_file, get_file_map, reindex_file)\n"
        "• `query` — the search terms.  (search_code, search_bodies,\n"
        "  search_content, smart_search, semantic_search)\n"
        "No tool takes a filler argument.  Send no argument you did not read in\n"
        "the schema — `get_active_build` accepts only `project_root` and `fast`.\n"
        "When a call fails on an argument, DROP the bad argument and REPEAT the\n"
        "call.  Do not continue without the answer, and above all do not skip\n"
        "`get_active_build`: it says whether the index can be trusted at all.\n\n"
        "ANTI-PATTERNS — do NOT:\n"
        "• Use external search tools for C/C++ symbols → use lookup_symbol or search_code\n"
        "• Use external search tools for code patterns → use search_bodies (function\n"
        "  bodies) or search_content (full file content)\n"
        "• Use external tools for callbacks, ISRs → use find_references or\n"
        "  search_bodies(project_only=True)\n"
        "• Use file readers for function bodies → use get_source (libclang exact extents)\n"
        "• Use external file readers for C/C++ files → use read_file (returns\n"
        "  ifdef-filtered content)\n"
        "• Call get_source + find_callers separately → use get_symbol_context for body,\n"
        "  callers, and callees in one call (fewer round-trips, richer data)\n"
        "• Run external search tools in parallel with fw-context\n"
        "• Give up on fw-context after one empty result → try simpler query or\n"
        "  different fw-context tool first\n"
        "• search_code for a SINGLE KNOWN symbol → use lookup_symbol(exact=true).\n"
        '  search_code FTS5-tokenizes names: "kb_open_disp" → "kb"+"open"+"disp",\n'
        "  causing false matches on unrelated symbols containing those tokens.\n"
        "  search_code is for concept/keyword DISCOVERY only.\n"
        "• Use generic review agents/skills for C/C++ code → use\n"
        "  fw-review skill (see REVIEW SKILL section below).\n\n"
        "AGENT LOOP: Check(get_active_build) → Find(search_code/lookup_symbol)\n"
        "→ Read(get_symbol_context) ← preferred (body+callers+callees in one call).\n"
        "  Fallback: get_source (body only). For whole-file reads: read_file.\n"
        "→ Trace(find_references/find_callers) — skip if context already from get_symbol_context.\n"
        "→ For body patterns use search_bodies.\n"
        "→ DECISION after get_active_build():\n"
        '  • status="ready" or "reindexing" — fw-context is fully operational.\n'
        "    bg_reindex_running does NOT mean the index is unavailable. Continue.\n"
        '  • status="reindex_needed" — queries still work, but schedule fw-context index.\n'
        "  • client_restart_required=True — NOT a status, and NO command repairs\n"
        "    it. The index holds a newer row format than this session reads, thus\n"
        "    the index is correct and this session is the old reader. Queries keep\n"
        "    working. Do NOT reindex: the indexer writes the same new format again\n"
        "    and the field comes back. Tell the operator to restart the LLM client\n"
        "    (Claude Code, opencode, or the client in use). The MCP server is a\n"
        "    child process of that client, thus nobody can restart it alone.\n"
        '  • status="not_initialized" — project not set up. ASK the operator:\n'
        '    "Initialize fw-context? Runs `fw-context init` — creates project ID,\n'
        "    config files (.fw-context/config.toml), and registers with AI tools.\"\n"
        "    Do NOT run without operator confirmation. On approval → run\n"
        "    `fw-context init` via bash, then call get_active_build() again\n"
        "    (returns no_index). init and index are SEPARATE steps — after init,\n"
        "    ask AGAIN before indexing (see no_index below).\n"
        '  • status="no_index" — project initialized but no symbol index. ASK the\n'
        '    operator: "Build the symbol index? Runs `fw-context index --build` which\n'
        "    compiles and parses ALL C/C++ source with libclang — takes several minutes\n"
        "    and uses gigabytes of disk.\" Do NOT run without operator confirmation.\n"
        "    On approval → run `fw-context index --build` via bash (or\n"
        "    `fw-context index <compile_commands.json>` if one exists), then call\n"
        '    get_active_build() again to confirm status="ready".\n'
        "    NOTE: init and index are NEVER combined — each requires its own\n"
        "    operator confirmation before execution.\n"
        '  • status="error" — DB corruption or access error. Use other tools.\n\n'
        "REVIEW WORKFLOW — when reviewing C/C++ code changes (per changed symbol):\n"
        "0. find_hotspots(project_only=True) — identify highest-impact functions FIRST.\n"
        "   Prioritize review of hotspots (20+ callers) over leaf functions.\n"
        "1. get_symbol_context(name) — body + direct callers + callees + LLM analysis\n"
        "   in one call. Replaces lookup_symbol + find_callers + find_callees_recursive.\n"
        '2. If get_symbol_context callers are empty → search_bodies("name") and\n'
        "   find_indirect_call_sites / find_indirect_targets (function pointers,\n"
        "   callbacks, ISRs invisible to the call graph).\n"
        "3. find_all_callers_recursive() → transitive upstream impact (full call tree).\n"
        "4. find_callees_recursive() → transitive downstream check.\n"
        "   After a logic change, verify compatibility with everything this function\n"
        "   calls — arguments, init order, error paths may have changed.\n"
        '6. find_references("SymbolName") → all reads/writes/calls of changed types.\n'
        '7. search_content("PATTERN") → confirm removal of #define/#ifdef/board names.\n'
        "8. find_dead_code() → detect newly dead functions after removal.\n"
        "9. trace_data_flow(type_name, to_symbol) → cross-module data dependencies.\n"
        "   Use when a changed function produces or consumes typed data that flows\n"
        '   through other modules. E.g. trace_data_flow("SlotPin", "InventoryWriter").\n'
        "10. For each search_bodies result set: scan ALL results — the 3rd match\n"
        "   may reveal an implementation the diff didn't touch (e.g. duplicate CRC\n"
        "   in a private method).\n"
        "11. When analyzing BOTH a diff AND fw-context results: diff shows SCOPE\n"
        "   (what changed), fw-context verifies CORRECTNESS in full project context.\n"
        "   ALWAYS verify diff discoveries recursively with fw-context.\n\n"
        "DIFF → FW-CONTEXT VERIFICATION RULE:\n"
        "→ When you analyze code via diff (git diff, file diff, patch review),\n"
        "  diff shows ONLY what changed — it cannot reveal the impact across\n"
        "  the full codebase.\n"
        "→ After inspecting a diff: verify your findings with fw-context:\n"
        '  • find_references("<symbol>") — all callers/readers, not just diff context\n'
        '  • search_bodies("<pattern>") — pattern consistency across entire codebase\n'
        "  • find_call_path / find_all_callers_recursive — cross-module impact\n"
        '  • trace_data_flow("<type>", "<target>") — cross-module data dependencies\n'
        "  • find_dead_code / find_hotspots — structural effects of changes\n"
        "→ Do NOT draw conclusions from diff results alone — diff is for SCOPE\n"
        "  discovery, fw-context is for IMPACT verification. They complement each\n"
        "  other; neither replaces the other.\n\n"
        "Search: lookup_symbol, search_code, search_bodies (body text),\n"
        "search_content (full file content), smart_search, semantic_search.\n"
        "Call graph: find_callers, find_references, find_call_path,\n"
        "find_all_callers_recursive, find_callees_recursive, find_hotspots,\n"
        "find_dead_code, find_wrapper_callers, trace_data_flow,\n"
        "get_vector_table,\n"
        "find_indirect_call_sites, find_indirect_targets.\n"
        "Inheritance: get_inheritance_chain, get_class_members,\n"
        "get_template_instances, get_method_overrides.\n"
        "Source: get_source, get_symbol_context, get_file_map, explain_symbol, read_file.\n"
         "Maintenance: get_active_build, get_environment_status,\n"
         "check_dependencies, list_projects, list_variants, get_project_info,\n"
         "check_ollama, configure_llm, reindex_file, reset_index.\n\n"
         "MULTIPROJECT / MULTIDEVICE — when get_active_build() returns multi=true:\n"
         "  • The project indexes N variants × M images (boards/cores).\n"
         "  • get_active_build() lists variants/images/variant_images/active_variant.\n"
         "  • Every query tool takes variant=<name> and (sysbuild) image=<name>.\n"
         "  • FAIL-CLOSED: a query without variant errors (listing variants) unless\n"
         "    [build] default_variant is set — never guess a variant silently.\n"
         "  • variant=\"*\" = all variants (explicit only); image omitted = all images\n"
         "    of the variant (results annotated with variant/image).\n"
         "  • list_variants() shows the authoritative per-build config_hash + counts.\n"
         "  • \"hledej v nRF52840\" → variant=\"nrf52840\"; \"ukaž stage0\" → image=\"stage0\".\n\n"
        "REVIEW SKILL — MANDATORY (not optional): When reviewing C/C++ firmware\n"
        "code (diffs, commits, PRs, changed files), your FIRST action MUST be:\n"
        "  skill(name=\"fw-review\")\n"
        "Do NOT use generic review agents (code-explorer, general, etc.) for\n"
        "C/C++ firmware reviews — they do not know fw-context tool selection rules.\n"
        "Do NOT start inline review without the skill. The skill provides:\n"
        "  • Phase 0: mandatory review plan creation from diff stat\n"
        "  • Phase 1: structural verification (callers, callees, types, inheritance)\n"
        "  • Phase 2: logic & memory review (deep code reading — ODR, null deref,\n"
        "    call ordering, truncation, state reset, watchdog, edge cases)\n"
        "  • Anti-pattern checklist (7 common mistakes with fw-context tools)\n"
        "  • Tool selection decision tree (search_code vs lookup_symbol, etc.)\n"
        "Trigger phrases — this skill applies to ALL review types regardless\n"
        "of what the user calls it: review, code review, PR review, diff\n"
        "review, deep review, recursive review, exhaustive review,\n"
        "comprehensive review, safety review, audit, change analysis, commit\n"
        "analysis, impact analysis, examine changes, inspect this code, look\n"
        "at this diff, check this PR, check these changes, analyze this\n"
        "commit, verify this change.\n"
        "If the project has a LOCAL fw-review skill, that overrides\n"
        "the global default — the user has intentionally customized it.\n\n"
        "Start every session with get_active_build(), and repeat it when the\n"
        "first attempt fails on an argument.  An answer built on an index of\n"
        "unknown health is worth nothing.\n"
        "For non-C/C++ files, general-purpose tools are preferred.\n\n"
        "LLM SETUP — after get_active_build returns status='ready', call\n"
        "check_ollama to verify the LLM backend:\n"
        "  • status='ok' — LLM ready. Continue.\n"
        "  • status='not_configured' — Ollama not running, no chat API. ASK:\n"
        "    'Use local Ollama or a cloud API?'\n"
        "    - Local: guide operator to install from https://ollama.com.\n"
        "      For intranet: download installer on an internet machine,\n"
        "      copy to local. Do NOT install Ollama yourself.\n"
        "      After operator confirms Ollama is running, call check_ollama.\n"
        "    - Cloud: ask for URL, key, model. Call configure_llm.\n"
        "      If URL is external, ALWAYS warn: source code will be sent\n"
        "      to that endpoint — ensure compliance with data security.\n"
        "  • status='model_missing' — Chat model not installed.\n"
        "    YOU estimate download size from model name (your knowledge).\n"
        "    Report to operator with caveat: size may be inaccurate.\n"
        "    ALWAYS ASK: 'Download this model, or configure a separate\n"
        "    cloud chat API (no download needed)?'\n"
        "    - Download by me: run `ollama pull {model}` via bash.\n"
        "      On failure, check stderr for network errors (timeout,\n"
        "      unreachable, no such host). If network issue: suggest\n"
        "      offline install — pull on internet machine, copy\n"
        "      ~/.ollama/models/, or download GGUF + ollama create.\n"
        "    - Download manually: tell operator to run `ollama pull\n"
        "      {model}` (NOTE: outside normal flow). Include offline\n"
        "      instructions for intranet.\n"
        "    - Cloud API: ask URL/key/model, call configure_llm.\n"
        "  • status='model_missing' + suggest_cloud=true — No model\n"
        "    ≥7B parameters installed (too small for code tasks).\n"
        "    Check model_details for installed models. Emphasize cloud\n"
        "    API option.\n"
        "  • status='embedding_unavailable' — Chat works (cloud) but\n"
        "    embedding needs Ollama. Warn: semantic search disabled.\n"
        "    Offer to guide Ollama setup.\n"
        "  • chat_api.compliance_warning present — ALWAYS show to operator.\n"
        "    Recommend local or internal API first.\n"
        "Model downloads are LARGE (multi-GB). ALWAYS ask before pulling.\n"
        "auto_pull defaults to false — no automatic downloads on 404.\n"
        "Model size estimates are YOUR judgment — always caveat to operator.\n"
        "configure_llm writes ONLY to <project>/.fw-context/local.toml\n"
        "(gitignored). It does NOT modify global or shared config files.\n"
        "configure_llm returns status='error' — report the error message\n"
        "to the operator. If the test call failed, check API URL/key/model.\n"
        "The config was still written to local.toml — fix the issue and\n"
        "call configure_llm again, or call check_ollama to verify.\n"
        "configure_llm returns status='error' with 'not initialized' —\n"
        "tell operator to run 'fw-context init' in the project root first.\n"
        "Stream option: when the chat API is behind a reverse proxy (nginx,\n"
        "Cloudflare, corporate proxy) that kills idle connections, set\n"
        "stream=true via configure_llm. This sends stream:true and consumes\n"
        "SSE chunks, keeping the connection alive. Default false — non-streaming\n"
        "is sufficient for local Ollama. When false, SSE responses are still\n"
        "auto-detected and parsed as a fallback."
    ),
)

# ── Shared helpers ──────────────────────────────────────────────────────────


# ── Re-exported from .shared.context ──────────────────────────────────────────
# _db_path, _resolve_context, _quick_open_readonly, _is_stale, get_executor,
# invalidate_executor, interrupt_all — see mcp/shared/context.py


# _stale_files, _count_modified_files — re-exported from .shared.stale

# ── Background reindex ──────────────────────────────────────────────────────

# ── File-watch daemon ───────────────────────────────────────────────────────



# ── Tools (non-search) ──────────────────────────────────────────────────────


# ── Graph analytics tools ─────────────────────────────────────────────────────


# ── Shared helpers for SDK path filtering ──────────────────────────────────


# _path_matches, _build_sdk_excludes, _normalize_path_pattern — re-exported from .shared.filtering


# ── Inheritance tool ──────────────────────────────────────────────────────


# ── Pipeline-based search tools ─────────────────────────────────────────────


# _fallback_to_search_code, _fallback_to_search_code_inner — re-exported from .shared.fallback


# ── MCP tool registration (implementations in handlers/) ──────────────

# Maintenance tools ARE wrapped since ADR-006 (b) — the old "too lightweight
# to wrap" argument missed the point: reset_index and reindex_file MUTATE the
# index database, so they must serialize against in-flight queries (T11 C7:
# an unwrapped reset_index used to race a live query) and against each other.
# _wrap_tool(fn, maintenance=True) gives them the same error contract as
# every other tool; the lock wait is announced once via ctx.info and bounded
# by the 300 s cancel that the wrapper already applies.
#
# ``reindex_file_impl`` stays an in-process alias (see its docstring) and is
# deliberately NOT registered — ``_*``/``*_impl`` names never go on the wire
# (ADR-006 a).
#
# All search, call graph, symbol source, inheritance, and variable tools
# ARE wrapped — they may run for seconds to minutes (BFS traversal,
# Ollama HTTP calls, libclang re-parsing) and need progress notifications
# and the 5 s busy-rejection guard.
#
# _with_project_selector adds the `project` parameter to every tool that
# takes a project_root.  It must stay the OUTERMOST wrapper: it is the one
# that FastMCP inspects, and it reads the signature of what it wraps.
#
# _forbid_unknown_tool_arguments must run BEFORE the first registration —
# it changes the base model from which FastMCP builds each argument model.
_forbid_unknown_tool_arguments()

# maintenance.py
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.check_dependencies, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.check_ollama, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.configure_llm, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.get_active_build, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.get_environment_status, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.get_project_info, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.list_projects, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.list_variants, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.reindex_file, maintenance=True)))
mcp.tool()(_with_project_selector(_wrap_tool(maintenance.reset_index, maintenance=True)))

# search.py
# M3: participating tools routed through v2 authority (V2_DEFAULT_READ_AUTHORITY).
# v2_authority_route wraps the legacy handler: when v2 authority is enabled
# (default since M3), the request goes through the REAL v2 request path
# (section 23): resolve QueryView → v2 query → format → response.
# When v2 authority is disabled (=0), legacy is called directly (unchanged).
from .shared.authority import v2_authority_route
from .shared.v2_shadow_adapters import (
    # Batch B (accepted)
    v2_shadow_lookup_symbol,
    v2_shadow_find_callers,
    v2_shadow_find_references,
    # Batch C — source/search
    v2_shadow_search_code,
    v2_shadow_search_bodies,
    v2_shadow_search_content,
    v2_shadow_semantic_search,
    v2_shadow_smart_search,
    v2_shadow_explain_symbol,
    v2_shadow_get_source,
    v2_shadow_get_symbol_context,
    v2_shadow_get_file_map,
    v2_shadow_read_file,
    # Batch D — graph/traversal
    v2_shadow_find_all_callers_recursive,
    v2_shadow_find_call_path,
    v2_shadow_find_callees_recursive,
    v2_shadow_find_dead_code,
    v2_shadow_find_hotspots,
    v2_shadow_find_indirect_call_sites,
    v2_shadow_find_indirect_targets,
    v2_shadow_find_wrapper_callers,
    v2_shadow_trace_data_flow,
    # Batch E — inheritance
    v2_shadow_get_class_members,
    v2_shadow_get_inheritance_chain,
    v2_shadow_get_method_overrides,
    v2_shadow_get_template_instances,
    # Batch F — variables/conditionals
    v2_shadow_find_variables,
    v2_shadow_explain_condition,
    v2_shadow_list_conditional_regions,
)
from .shared.authority import format_v2_find_callers
from .shared.authority import m6_authority_route
from .shared.m6_query_adapters import (
    m6_lookup_symbol, m6_search_code, m6_search_bodies, m6_search_content,
    m6_semantic_search, m6_smart_search,
    m6_find_all_callers_recursive, m6_find_call_path, m6_find_callees_recursive,
    m6_find_callers, m6_find_dead_code, m6_find_hotspots,
    m6_find_indirect_call_sites, m6_find_indirect_targets,
    m6_find_references, m6_find_wrapper_callers, m6_trace_data_flow,
    m6_explain_symbol, m6_get_file_map, m6_get_source, m6_get_symbol_context,
    m6_read_file,
    m6_get_class_members, m6_get_inheritance_chain, m6_get_method_overrides,
    m6_get_template_instances,
    m6_find_variables, m6_explain_condition, m6_list_conditional_regions,
    m6_get_vector_table, m6_list_build_health,
)

# ── M7.2: M6 READ AUTHORITY ────────────────────────────────────────────────
# All 32 core semantic tools are now routed through m6_authority_route.
# When M6 read authority is enabled (default since M7.2), production reads
# route through M6 query implementations (§8: production request → M6
# query implementation → M6 result).
# When M6 read authority is disabled, the v2-routed handler is called
# (preserving the M3 production behavior as fallback).
# No silent fallback (§9): if the M6 store is not populated, the handler
# raises a visible error rather than silently falling back to v2.
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("lookup_symbol",
        v2_authority_route("lookup_symbol", search.lookup_symbol, v2_shadow_lookup_symbol),
        m6_lookup_symbol)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("search_code",
        v2_authority_route("search_code", search.search_code, v2_shadow_search_code),
        m6_search_code)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("search_bodies",
        v2_authority_route("search_bodies", search.search_bodies, v2_shadow_search_bodies),
        m6_search_bodies)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("search_content",
        v2_authority_route("search_content", search.search_content, v2_shadow_search_content),
        m6_search_content)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("semantic_search",
        v2_authority_route("semantic_search", search.semantic_search, v2_shadow_semantic_search),
        m6_semantic_search)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("smart_search",
        v2_authority_route("smart_search", search.smart_search, v2_shadow_smart_search),
        m6_smart_search)
)))

# callgraph.py
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_all_callers_recursive",
        v2_authority_route("find_all_callers_recursive", callgraph.find_all_callers_recursive,
                           v2_shadow_find_all_callers_recursive, format_v2=format_v2_find_callers),
        m6_find_all_callers_recursive)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_call_path",
        v2_authority_route("find_call_path", callgraph.find_call_path, v2_shadow_find_call_path),
        m6_find_call_path)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_vector_table", callgraph.get_vector_table, m6_get_vector_table)
)))


def get_build_health(
    project_root: str | None = None, fast: bool = True,
) -> dict:
    """Legacy fallback for get_build_health when M6 authority is off.

    The per-build indexed-state report is an M6-native read: it needs the
    M6 store (index-m6.db), which the legacy path does not populate.  When
    M6 authority is disabled the answer is an empty report with a note,
    never a fabricated one.
    """
    del project_root, fast
    return {"builds": [], "note": "M6-only report (M6 read authority is off)"}


mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_build_health", get_build_health, m6_list_build_health)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_callees_recursive",
        v2_authority_route("find_callees_recursive", callgraph.find_callees_recursive,
                           v2_shadow_find_callees_recursive, format_v2=format_v2_find_callers),
        m6_find_callees_recursive)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_callers",
        v2_authority_route("find_callers", callgraph.find_callers,
                           v2_shadow_find_callers, format_v2=format_v2_find_callers),
        m6_find_callers)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_dead_code",
        v2_authority_route("find_dead_code", callgraph.find_dead_code, v2_shadow_find_dead_code),
        m6_find_dead_code)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_hotspots",
        v2_authority_route("find_hotspots", callgraph.find_hotspots, v2_shadow_find_hotspots),
        m6_find_hotspots)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_indirect_call_sites",
        v2_authority_route("find_indirect_call_sites", callgraph.find_indirect_call_sites,
                           v2_shadow_find_indirect_call_sites),
        m6_find_indirect_call_sites)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_indirect_targets",
        v2_authority_route("find_indirect_targets", callgraph.find_indirect_targets,
                           v2_shadow_find_indirect_targets),
        m6_find_indirect_targets)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_references",
        v2_authority_route("find_references", callgraph.find_references,
                           v2_shadow_find_references, format_v2=format_v2_find_callers),
        m6_find_references)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_wrapper_callers",
        v2_authority_route("find_wrapper_callers", callgraph.find_wrapper_callers,
                           v2_shadow_find_wrapper_callers),
        m6_find_wrapper_callers)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("trace_data_flow",
        v2_authority_route("trace_data_flow", callgraph.trace_data_flow,
                           v2_shadow_trace_data_flow),
        m6_trace_data_flow)
)))

# source.py
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("explain_symbol",
        v2_authority_route("explain_symbol", source.explain_symbol, v2_shadow_explain_symbol),
        m6_explain_symbol)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_file_map",
        v2_authority_route("get_file_map", source.get_file_map, v2_shadow_get_file_map),
        m6_get_file_map)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_source",
        v2_authority_route("get_source", source.get_source, v2_shadow_get_source),
        m6_get_source)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_symbol_context",
        v2_authority_route("get_symbol_context", source.get_symbol_context,
                           v2_shadow_get_symbol_context),
        m6_get_symbol_context)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("read_file",
        v2_authority_route("read_file", source.read_file, v2_shadow_read_file),
        m6_read_file)
)))

# inheritance.py
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_class_members",
        v2_authority_route("get_class_members", inheritance.get_class_members,
                           v2_shadow_get_class_members),
        m6_get_class_members)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_inheritance_chain",
        v2_authority_route("get_inheritance_chain", inheritance.get_inheritance_chain,
                           v2_shadow_get_inheritance_chain),
        m6_get_inheritance_chain)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_method_overrides",
        v2_authority_route("get_method_overrides", inheritance.get_method_overrides,
                           v2_shadow_get_method_overrides),
        m6_get_method_overrides)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("get_template_instances",
        v2_authority_route("get_template_instances", inheritance.get_template_instances,
                           v2_shadow_get_template_instances),
        m6_get_template_instances)
)))

# variables.py
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("find_variables",
        v2_authority_route("find_variables", variables.find_variables, v2_shadow_find_variables),
        m6_find_variables)
)))

# conditionals.py — conditional compilation provenance (T03 / ADR-011)
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("explain_condition",
        v2_authority_route("explain_condition", conditionals.explain_condition,
                           v2_shadow_explain_condition),
        m6_explain_condition)
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    m6_authority_route("list_conditional_regions",
        v2_authority_route("list_conditional_regions", conditionals.list_conditional_regions,
                           v2_shadow_list_conditional_regions),
        m6_list_conditional_regions)
)))

# variants.py — variant behavior grouping + structural build comparison
# (W6 / AUD-R5): textual/structural comparison of stored index records;
# only the body-fingerprint dimension is fingerprint-based.
# These two tools read the DB-layer index (sqlite index.db) by
# variant/config_hash — their dimensions (body_fingerprint, conditions,
# defines) require stored body/source/condition facts that the M6 store
# does not carry.  They stay OUT of m6_authority_route on purpose: the
# DB-layer handlers ARE the full accepted-design contract (W6), and the
# M6 adapters were a reduced kind-count approximation that broke them.
mcp.tool()(_with_project_selector(_wrap_tool(
    variants.compare_variants
)))
mcp.tool()(_with_project_selector(_wrap_tool(
    variants.group_equivalent_variants
)))

# ── MCP Resources ──────────────────────────────────────────────────────────


@mcp.resource("fw-context://stats")
def resource_stats() -> str:
    """Return a human-readable markdown summary of all indexed projects.

    Read-only. Aggregates stats from every project database found under the
    configured index directory.
    """

    projects = maintenance.list_projects()
    if not projects:
        return "No indexed projects found."
    lines = [f"# fw-context — {len(projects)} project(s)", ""]
    for p in projects:
        if "info" in p:
            lines.append(p["info"])
            continue
        if "error" in p:
            lines.append(f"- **{p.get('db', '?')}**: ERROR — {p['error']}")
            continue
        stale = "⚠ reindex_needed" if p.get("reindex_needed") else "✓ ready"
        lines.append(
            f"- **{p['name']}** ({p['project_id']}) — "
            f"{p['symbol_count']} symbols, {p['file_count']} files, "
            f"indexed {p['indexed_at']}, {stale}"
        )
    return "\n".join(lines)


@mcp.resource("fw-context://projects")
def resource_projects() -> str:
    """Return project list as a JSON string.

    Read-only. Uses the same data as ``list_projects``, serialized as
    indented JSON.
    """
    import json

    return json.dumps(maintenance.list_projects(), indent=2, ensure_ascii=False, default=str)


@mcp.resource("fw-context://symbols/{name}")
def resource_symbol(name: str) -> str:
    """Return the definition source of a symbol as a markdown document.

    Read-only. Renders symbol metadata (name, kind, file, signature) and
    source code as a formatted markdown resource.
    """
    import json

    try:
        result = source.get_source(name)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return json.dumps({"error": f"Failed to read symbol '{name}': {exc}"})
    if "error" in result:
        return json.dumps(result)
    source_code = result.pop("source", "")
    # Render as a small markdown document
    lines = [
        f"# {result['name']}",
        "",
        f"- **qualified:** `{result['qualified_name']}`",
        f"- **kind:** {result['kind']}",
        f"- **file:** `{result['file']}:{result['line']}`",
        f"- **signature:** `{result.get('signature', '')}`",
        "",
        "```cpp",
        source_code,
        "```",
    ]
    return "\n".join(lines)


# ── Embedded review skill ───────────────────────────────────────────────────


def _load_skill_md() -> str | None:
    """Return the fw-review SKILL.md content, or None."""
    skill_path = _DATA_DIR / "skills" / "fw-review" / "SKILL.md"
    try:
        return skill_path.read_text(encoding="utf-8")
    except OSError:
        return None


@mcp.resource("fw-context://skills/fw-review")
def resource_embedded_review_skill() -> str:
    """Return the fw-review SKILL.md as an MCP resource.

    Read-only. Makes the embedded firmware review methodology available
    to any MCP client via a well-known resource URI."""
    content = _load_skill_md()
    if content is None:
        return "Skill not found — reinstall with 'fw-context init --force'."
    return content


# @mcp.tool()
# def fw_embedded_review() -> str:
#     """Embedded C/C++ firmware review methodology.
#
#     Read-only. Three-phase review workflow: plan → structural verification
#     (callers/callees/types) → logic review across 9 embedded-specific
#     domains (memory, concurrency, RTOS, peripherals, etc.). Call when the
#     user asks to review embedded firmware changes."""
#     return resource_embedded_review_skill()


# ── main ────────────────────────────────────────────────────────────────────


def main() -> None:
    """Start the FastMCP stdio server — entry point for the ``fw-context-mcp`` command.

    On startup:
    1. Resolves the project root and validates initialization state.
       - If project root cannot be resolved: sets a sentinel so all tools
         return a clear error (fatal — server cannot operate).
       - If project is not initialized (``fw-context init`` not run): server
         starts normally — ``get_active_build()`` returns ``not_initialized``
         so the agent can ask the operator and run init via bash.  Other
         tools raise ``ProjectNotInitializedError`` (fail-fast).
       - If project is initialized but no index exists: server starts
         normally — ``get_active_build()`` returns ``no_index`` so the
         agent can ask the operator and run index via bash.  The ping
         thread starts, thus the watcher daemon starts as soon as the
         operator creates the index.
    2. When ready, pre-marks the database as integrity-checked.
    3. Ensures the persistent watcher daemon is running for the project
       (spawns it if this is the first MCP server).
    4. Starts a ping thread that keeps the daemon alive, and that
       spawns the daemon again when a ping gets no answer.

    **Why progressive startup?**  The server must NOT exit when the
    project is not initialized or has no index.  The whole point of
    ``get_active_build()`` returning ``not_initialized`` / ``no_index``
    is so the agent can ask the operator and run the corresponding
    command via bash.  An early exit would make the tools disappear from
    the agent's context, breaking the guided setup flow.

    D6.1: stdio streams are reconfigured to UTF-8 with replacement — the
    MCP protocol frames on stdio must never depend on the console's
    legacy codepage.
    """
    from ..platform import configure_stdio

    configure_stdio()
    _prewarm_sqlite_vec()
    log.info("fw-context MCP server starting")

    # Phase 1: pre-populate the project-ready cache with a one-shot check.
    # _check_server_ready() raises RuntimeError when the project isn't
    # ready — we catch it here so the server can still start (tools will
    try:
        _check_server_ready()
    except RuntimeError as exc:
        log.info("Project not ready at startup: %s", exc)

    # Phase 2: fast dependency pre-flight — warn on degraded deps, never exit.
    # Missing optional dependencies (e.g., nvidia-smi for GPU detection) are
    # non-fatal.  The server starts with reduced functionality.
    try:
        from ..deps import run_preflight
        for r in run_preflight():
            if r.status != "ok":
                log.warning("[%s] %s: %s", r.status.upper(), r.name, r.message)
                if r.fix_cmd:
                    log.warning("  fix: %s", r.fix_cmd)
    except Exception:
        log.debug("Dependency pre-flight skipped", exc_info=True)

    # Phase 3: if the project IS ready, pre-mark integrity check and ensure
    # daemon.  If not ready, skip to mcp.run() — tools will report status
    # via get_active_build().
    try:
        root = resolve_project_root(None)
    except OSError:
        log.exception("Failed to resolve project root for daemon setup")
        mcp.run()
        return

    try:
        from ..config import derive_project_id
        from ..config import load as load_config
        from ..config.settings import ProjectNotInitializedError
        project_id = derive_project_id(root)
    except ProjectNotInitializedError:
        log.info("Project not initialized at %s — get_active_build() will report not_initialized", root)
        mcp.run()
        return

    # Check 2: does the index database exist?
    # If not, we DON'T set the sentinel — the server starts normally so
    # that get_active_build() can return status="no_index", allowing the
    # agent to ask the operator and run `fw-context index` via bash.
    cfg = load_config(project_root=root)
    db_path = cfg.index.db_dir / project_id / "index.db"
    if not db_path.exists():
        log.info("No index found at %s — get_active_build() will report no_index", db_path)
        # The ping thread starts even when no index exists.  The thread
        # calls _ensure_daemon_running every PING_INTERVAL seconds, thus
        # the watcher starts when the operator runs `fw-context index`.
        # WHY this is necessary: this function is the only place that
        # spawns the daemon at server startup.  A server that starts
        # BEFORE the first index gets no watcher, and no background
        # reindex runs for the full life of that server.
        try:
            _start_ping_thread(root)
        except (RuntimeError, OSError):
            log.exception("Ping thread startup failed — no automatic reindex until the server restarts")
        mcp.run()
        return

    # Project is ready — pre-mark integrity check and start normally.
    # GOTCHA — integrity_check on large DBs is I/O-bound (10-30 s for
    # 3+ GB).  If it runs here via _open_db_safe during MCP server startup,
    # the MCP client times out during tool discovery and fw-context tools
    # never appear.  The check already ran during ``fw-context index``, so
    # we skip it by pre-marking the DB — a corrupt DB will be caught by
    # individual queries via _open_db_safe.
    _integrity_checked.add(str(db_path.resolve()))

    try:
        _ensure_daemon_running(root)
        _start_ping_thread(root)
    except (RuntimeError, OSError):
        log.exception("Background service startup failed — auto-reindex and file watching unavailable")

    mcp.run()


def _start_ping_thread(root: Path) -> None:
    """Start a daemon thread that pings the watcher daemon every 15 s.

    **Why a separate thread?**  The ping loop is a blocking infinite loop
    with ``time.sleep``.  Running it on the event loop (``asyncio.sleep``)
    would add one more coroutine competing for the same executor.  A
    daemon thread is simpler — it runs independently and dies with the
    process.

    **Why ping?**  The watcher daemon is a separate process that the
    first MCP server spawns.  If the daemon stops (OOM, segfault,
    manual kill), file watching stops without a message.  A failed ping
    is the only signal that an MCP server gets, thus the loop calls
    ``_ensure_daemon_running`` to start the daemon again.

    **Two failure modes, one recovery.**  A ping fails when the daemon
    stopped, and also when no daemon was ever spawned.  The second case
    occurs when the server starts before the project has an index.
    ``_ensure_daemon_running`` corrects the two cases.  Its only
    precondition is an ``index.db`` file that exists.  It spawns a
    daemon only when no daemon holds ``watcher.lock``.  Concurrent MCP
    servers are thus safe — the flock serialises them, and exactly one
    daemon starts.
    """
    import threading
    import time

    from .daemon import PING_INTERVAL, ping_daemon

    def _ping_loop() -> None:
        while True:
            time.sleep(PING_INTERVAL)
            try:
                if ping_daemon(root):
                    continue
                log.debug("Daemon ping failed — the ping thread spawns a new watcher daemon")
                _ensure_daemon_running(root)
            except (RuntimeError, OSError):
                # RuntimeError — the project is not initialized, or it
                # has no index yet.  _db_path() raises it through
                # _check_server_ready().  The two states are temporary,
                # because the operator can still run init or index.  The
                # loop must stay alive and try again on the next tick.
                log.debug("Could not start the watcher daemon", exc_info=True)

    # daemon=True: killed on process exit — no explicit stop mechanism needed
    t = threading.Thread(target=_ping_loop, daemon=True, name="fw-context-ping")
    t.start()
    log.debug("Daemon ping thread started for %s", root)


if __name__ == "__main__":
    # ADR-006 (g): ``python -m fw_context_mcp.mcp.server`` serves stdio
    # exactly like the ``fw-context-mcp`` console script — the module was a
    # silent no-op without this guard, which broke every launch recipe that
    # names the module form (README-MCP.md "Starting the server").
    main()

