"""ControlTransport — daemon control endpoint per platform (D4).

POSIX keeps AF_UNIX (``daemon.sock``); Windows moves to a TCP loopback
listener on an ephemeral port recorded in ``daemon.port``.  The contract
both backends must satisfy:

- ``listen`` creates the endpoint and returns a listener whose ``ping``
  answers ``True`` while it is up;
- after ``close()``/``wait_closed()`` the ping answers ``False``;
- ``cleanup`` removes the endpoint artifacts (stale socket/port file);
- the ping protocol is identical: connect, send ``b"ping\\n"``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fw_context_mcp.platform import transport


def _run_lifecycle(db_dir: Path, *, ping_inside: bool) -> bool:
    """listen → (ping?) → close → wait_closed, all inside one loop.

    on_ping fires from the server's connection handler — a task racing
    the client's sendall — so the caller polls for it before asserting.
    """
    import time

    async def _go() -> bool:
        pings: list[int] = []
        listener = await transport.listen(db_dir, lambda: pings.append(1))
        answered = transport.ping(db_dir) if ping_inside else False
        deadline = time.monotonic() + 5.0
        while not pings and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        listener.close()
        await listener.wait_closed()
        if ping_inside:
            assert answered, "the ping must answer while the listener is up"
            assert pings == [1], f"on_ping must fire on a ping, got {pings}"
        else:
            assert pings == [], f"no ping was sent, but on_ping fired: {pings}"
        return answered

    return asyncio.run(_go())


class TestListenPingStop:
    def test_ping_answers_while_listening(self, tmp_path: Path):
        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        assert _run_lifecycle(db_dir, ping_inside=True) is True

    def test_ping_fails_after_close(self, tmp_path: Path):
        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        _run_lifecycle(db_dir, ping_inside=False)
        assert transport.ping(db_dir) is False, (
            "no daemon is listening — the ping must fail after close"
        )

    def test_cleanup_removes_endpoint_artifacts(self, tmp_path: Path):
        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        _run_lifecycle(db_dir, ping_inside=False)
        transport.cleanup(db_dir)
        artifact = transport.artifact_path(db_dir)
        assert not artifact.exists(), f"endpoint artifact {artifact} must be removed"

    def test_ping_without_any_daemon_is_false(self, tmp_path: Path):
        db_dir = tmp_path / "proj"
        db_dir.mkdir()
        assert transport.ping(db_dir) is False

    def test_endpoint_artifact_exists_while_listening(self, tmp_path: Path):
        db_dir = tmp_path / "proj"
        db_dir.mkdir()

        async def _go() -> Path:
            listener = await transport.listen(db_dir, lambda: None)
            artifact = transport.artifact_path(db_dir)
            listener.close()
            await listener.wait_closed()
            return artifact

        assert asyncio.run(_go()).exists(), (
            "the endpoint artifact (daemon.sock / daemon.port) must exist while listening"
        )
