"""Platform lock adapter — boolean try_lock contract (W1, D3).

RED proofs live here while ``fw_context_mcp.platform`` does not exist:
the module import at the top fails, which IS the first failure.  After the
migration these tests hold the accepted contract in place:

- ``LockHandle.try_lock()`` returns a BOOLEAN — it never raises for
  "lock held".  msvcrt.locking signals contention with plain ``OSError``
  (EACCES/EDEADLK), fcntl.flock with ``BlockingIOError``; call sites that
  branch on the exception type break on one platform or the other.  The
  adapter normalizes every "locked" indication to ``False``.
- Exclusion holds across processes (O3) and after the holder is killed
  without unlocking (O4 — the kernel releases on process death).
- ``write_lock`` timeout is deterministic.
- The lock file records the holder pid (message-only convention).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from fw_context_mcp.platform import lock
from fw_context_mcp.indexer.db._locking import WriteLockTimeout, write_lock

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _child_script(lock_file: Path, *, hold_s: float = 0.0) -> str:
    return textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        sys.path.insert(0, {_SRC!r})
        from fw_context_mcp.platform import lock

        handle = lock.open(Path({str(lock_file)!r}))
        ok = handle.try_lock()
        print("ACQUIRED" if ok else "REFUSED", flush=True)
        if ok and {hold_s!r}:
            time.sleep({hold_s!r})
        handle.release()
        """
    )


class TestBooleanTryLockContract:
    """try_lock() must return False for "held" — never raise."""

    def test_second_handle_in_same_process_gets_boolean_false(self, tmp_path: Path):
        """Two handles on one lock file: the second must get False.

        msvcrt raises OSError(EACCES) here, fcntl raises BlockingIOError —
        a call site that branches on the exception type works on exactly
        one platform.  The adapter contract is a boolean.
        """
        h1 = lock.open(tmp_path / "probe.lock")
        assert h1.try_lock() is True
        h2 = lock.open(tmp_path / "probe.lock")
        try:
            assert h2.try_lock() is False, (
                "try_lock must report contention as False, not raise "
                "(msvcrt raises OSError, fcntl raises BlockingIOError)"
            )
        finally:
            h1.release()
            h2.release()

    def test_release_allows_a_new_holder(self, tmp_path: Path):
        lock_file = tmp_path / "probe.lock"
        h1 = lock.open(lock_file)
        assert h1.try_lock() is True
        h1.release()
        h2 = lock.open(lock_file)
        try:
            assert h2.try_lock() is True
        finally:
            h2.release()

    def test_release_is_idempotent(self, tmp_path: Path):
        h = lock.open(tmp_path / "probe.lock")
        assert h.try_lock() is True
        h.release()
        h.release()  # must not raise


class TestO3CrossProcessExclusion:
    def test_a_second_process_is_refused(self, tmp_path: Path):
        """O3 — two independent processes, one lock file, one winner."""
        lock_file = tmp_path / "cross.lock"
        holder = lock.open(lock_file)
        assert holder.try_lock() is True
        try:
            result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [sys.executable, "-c", _child_script(lock_file)],
                capture_output=True, text=True, timeout=60,
            )
            assert result.stdout.strip() == "REFUSED", result.stdout + result.stderr
        finally:
            holder.release()

    def test_the_child_acquires_after_release(self, tmp_path: Path):
        lock_file = tmp_path / "cross.lock"
        holder = lock.open(lock_file)
        assert holder.try_lock() is True
        holder.release()
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-c", _child_script(lock_file)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.stdout.strip() == "ACQUIRED", result.stdout + result.stderr


class TestO4HolderDeath:
    def test_lock_is_acquirable_after_holder_is_killed(self, tmp_path: Path):
        """O4 — the holder is killed hard, never releasing; the lock must free.

        The kernel drops the lock on process exit on every platform:
        fcntl.flock on POSIX, byte-range locks on Windows.
        """
        lock_file = tmp_path / "orphan.lock"
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-c", _child_script(lock_file, hold_s=60.0)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        try:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == "ACQUIRED"
        except BaseException:
            proc.kill()
            proc.wait(timeout=10)
            raise
        # Kill hard.  On win32 Popen.kill() IS TerminateProcess; on POSIX
        # it is SIGKILL.  Either way the holder never releases.
        proc.kill()
        proc.wait(timeout=10)

        deadline = time.monotonic() + 10.0
        handle = lock.open(lock_file)
        try:
            while time.monotonic() < deadline:
                if handle.try_lock():
                    break
                time.sleep(0.1)
            assert handle.try_lock() or True  # last attempt already looped
        finally:
            handle.release()
        # The loop above must have acquired within the deadline; assert the
        # fast path explicitly to keep the failure readable.
        handle2 = lock.open(lock_file)
        try:
            assert handle2.try_lock() is True, (
                "lock stayed held after the holder process was killed"
            )
        finally:
            handle2.release()


class TestHolderPidConvention:
    def test_holder_pid_records_the_lock_owner(self, tmp_path: Path):
        """The lock file names the holder, for the refusal message only."""
        lock_file = tmp_path / "named.lock"
        h = lock.open(lock_file)
        try:
            assert h.try_lock() is True
            assert h.holder_pid() == os.getpid(), (
                f"holder_pid should read back the recorded pid, got {h.holder_pid()!r}"
            )
        finally:
            h.release()

    def test_holder_pid_is_none_for_an_anonymous_lock_file(self, tmp_path: Path):
        h = lock.open(tmp_path / "blank.lock")
        try:
            assert h.holder_pid() is None
        finally:
            h.release()


class TestWriteLockTimeoutDeterminism:
    def test_write_lock_raises_within_the_timeout(self, tmp_path: Path):
        """A held write.lock must produce WriteLockTimeout promptly."""
        holder = lock.open(tmp_path / "write.lock")
        assert holder.try_lock() is True
        try:
            t0 = time.monotonic()
            with pytest.raises(WriteLockTimeout):
                with write_lock(tmp_path, timeout=1.0):
                    pass  # pragma: no cover — never acquired
            elapsed = time.monotonic() - t0
            assert elapsed < 10.0, f"timeout took {elapsed:.1f}s — not deterministic"
        finally:
            holder.release()
