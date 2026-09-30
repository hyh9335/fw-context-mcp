"""``platform.http_stream`` — the stream-capable factory variant (NEW-W1-1).

Six raw ``httpx.stream(...)`` call sites survived W1's factory migration
(auto_model.py:157, chat_router.py:229, deps/_fixes.py:214,
ollama.py:173/280/286): they construct an httpx.Client through the module
helper with the ambient proxy environment — the exact hostile-NO_PROXY
construction path the D6 factory sanitizes for every other client.  The
fix adds ``http_stream(method, url, **kwargs)`` to the platform factory
(same sanitize → construct → on-parse-failure retry-with-trust_env=False
chain) and routes the six sites through it.

Wire semantics are preserved by construction: ``httpx.stream(method, url,
**kwargs)`` IS ``httpx.Client().stream(method, url, **kwargs)`` — the
module helper builds a default client and delegates; the factory does the
same with a sanitized environment.  Timeouts, headers and the request body
pass through untouched as keyword arguments.  The retry fires ONLY on
client construction (before any request is built), so a streamed POST can
never be replayed by the fallback.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
SRC = WORKTREE / "src"

_HOSTILE = "127.0.0.1,localhost,::1,huawei.com,.huawei.com,[::1]"


class _EchoHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        payload = json.dumps({
            "echo": body.decode("utf-8", errors="replace"),
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture()
def echo_server():
    srv = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _stream_timeout():
    import httpx

    return httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)


def platform_base_http_stream(method: str, url: str, **kwargs):
    """The factory context manager straight from platform.base (not the facade)."""
    from fw_context_mcp.platform import base as platform_base

    return platform_base.http_stream(method, url, **kwargs)


class TestNoRawHttpxStreamSites:
    def test_no_raw_httpx_stream_calls_in_src(self):
        """Every httpx.stream call must go through the platform factory.

        Docstring mentions live inside RST double backticks (``httpx.stream()``);
        real call sites never carry a backtick before the name, so a backtick
        anywhere on the line marks prose, not code.
        """
        offenders: list[str] = []
        for py in SRC.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if (
                    re.search(r"httpx\.stream\(", stripped)
                    and "`" not in stripped
                    and not stripped.startswith("#")
                ):
                    offenders.append(f"{py.relative_to(SRC)}:{lineno}: {stripped}")
        assert not offenders, (
            "raw httpx.stream() call sites bypass the platform factory's "
            f"hostile-NO_PROXY sanitization (NEW-W1-1): {offenders}"
        )


class TestHttpStreamFactory:
    def test_stream_round_trip_preserves_wire_semantics(self, echo_server, monkeypatch):
        """Body, path, headers and timeout kwargs reach the wire untouched."""
        monkeypatch.setenv("NO_PROXY", _HOSTILE)
        monkeypatch.setenv("no_proxy", _HOSTILE)

        from fw_context_mcp.platform import http_stream

        with http_stream(
            "POST", f"{echo_server}/api/chat",
            json={"model": "m", "prompt": "hi"},
            headers={"Authorization": "Bearer t"},
            timeout=_stream_timeout(),
        ) as resp:
            assert resp.status_code == 200
            resp.read()  # streaming response: content available only after read
            body = resp.json()
        assert body["path"] == "/api/chat"
        assert json.loads(body["echo"]) == {"model": "m", "prompt": "hi"}
        assert body["auth"] == "Bearer t"

    def test_stream_survives_hostile_no_proxy(self, echo_server, monkeypatch):
        """The D6 hostile-NO_PROXY case: construction must not raise InvalidURL."""
        monkeypatch.setenv("NO_PROXY", _HOSTILE)
        monkeypatch.setenv("no_proxy", _HOSTILE)

        from fw_context_mcp.platform import http_stream

        with http_stream("POST", f"{echo_server}/api/tags", json={},
                         timeout=5.0) as resp:
            assert resp.status_code == 200

    def test_stream_construction_failure_retries_with_trust_env_false(
        self, monkeypatch,
    ):
        """Parse failures that survive sanitization retry once, trust_env=False."""
        import httpx

        calls: list[dict] = []

        class ExplodingClient:
            def __init__(self, **kwargs):
                calls.append(dict(kwargs))
                raise httpx.InvalidURL("Invalid port ':1]'")

        # httpx is imported lazily inside the factory, so the module-level
        # attribute of the real httpx module IS the constructor it resolves.
        monkeypatch.setattr(httpx, "Client", ExplodingClient)

        gen = platform_base_http_stream("POST", "http://127.0.0.1:1/api", json={})
        with pytest.raises(httpx.InvalidURL):
            # Client construction fails twice (default + trust_env=False) — the
            # second failure propagates, mirroring http_client()'s contract.
            gen.__enter__()
        assert len(calls) == 2, f"expected two construction attempts, got {calls}"
        assert calls[0].get("trust_env") is None
        assert calls[1].get("trust_env") is False

    def test_stream_failure_is_not_swallowed_as_network_error(self, monkeypatch):
        """A construction failure must propagate, never yield a fake response."""
        import httpx

        def explode(*args, **kwargs):
            raise httpx.InvalidURL("Invalid port ':1]'")

        monkeypatch.setattr(httpx, "Client", explode)

        with pytest.raises(httpx.InvalidURL):
            with platform_base_http_stream("POST", "http://x/api", json={}):
                pass  # pragma: no cover — never entered
