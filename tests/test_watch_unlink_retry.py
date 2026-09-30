"""``_unlink_when_released`` — win32 teardown retry unit tests (NEW-W5-1).

W5 measured (dedb533): after a verified daemon stop on win32,
``TerminateProcess`` signals before the handle-table teardown releases
``watcher.lock``, so the single-attempt unlink of the follow-up restart
surfaced WinError 32 (sharing violation → PermissionError).  The helper
retries within a bounded window and keeps single-attempt semantics when
``wait_s=0``.  These unit tests pin all four paths; the e2e restart oracle
from W5 keeps covering the real daemon path.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from fw_context_mcp.cli._watch import _unlink_when_released


class _FlakyUnlink:
    """Replace Path.unlink with a scriptable fake (no real locking involved).

    Assigned as a plain class attribute, the fake is NOT a descriptor:
    ``path.unlink(missing_ok=True)`` invokes ``__call__(missing_ok=True)``
    on the instance — the receiver is the fake itself, never the path.
    """

    def __init__(self, failures: int = 0, permanent: bool = False):
        self.failures = failures
        self.permanent = permanent
        self.attempts = 0

    def __call__(self, *args, **kwargs):
        self.attempts += 1
        if self.permanent or self.attempts <= self.failures:
            raise PermissionError(32, "The process cannot access the file "
                                      "because it is being used by another process")
        return None


@pytest.fixture()
def flaky_unlink(monkeypatch):
    holder: dict[str, _FlakyUnlink] = {}

    def _install(fake: _FlakyUnlink) -> _FlakyUnlink:
        monkeypatch.setattr(Path, "unlink", fake)
        holder["fake"] = fake
        return fake

    yield _install


class TestUnlinkWhenReleased:
    def test_unlinks_immediately_when_free(self, tmp_path: Path, flaky_unlink):
        fake = flaky_unlink(_FlakyUnlink())
        target = tmp_path / "watcher.lock"
        _unlink_when_released(target, wait_s=0.0)
        assert fake.attempts == 1

    def test_missing_file_is_not_an_error(self, tmp_path: Path, flaky_unlink):
        fake = flaky_unlink(_FlakyUnlink())
        target = tmp_path / "never_existed.lock"
        _unlink_when_released(target, wait_s=0.0)
        assert fake.attempts == 1

    def test_retries_while_dying_process_still_holds(self, tmp_path: Path,
                                                     flaky_unlink, monkeypatch):
        """PermissionError twice → third attempt succeeds (bounded window)."""
        # No real sleeping: the retry loop only needs monotonic to advance,
        # which it does between attempts even with a no-op sleep.
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        fake = flaky_unlink(_FlakyUnlink(failures=2))
        target = tmp_path / "watcher.lock"
        _unlink_when_released(target, wait_s=5.0)
        assert fake.attempts == 3

    def test_wait_s_zero_keeps_single_attempt(self, tmp_path: Path, flaky_unlink):
        """wait_s=0: one attempt, PermissionError re-raised (refusal paths)."""
        fake = flaky_unlink(_FlakyUnlink(permanent=True))
        target = tmp_path / "watcher.lock"
        with pytest.raises(PermissionError):
            _unlink_when_released(target, wait_s=0.0)
        assert fake.attempts == 1

    def test_re_raises_after_deadline(self, tmp_path: Path, flaky_unlink):
        """Permanent holder: bounded window then the error propagates."""
        fake = flaky_unlink(_FlakyUnlink(permanent=True))
        target = tmp_path / "watcher.lock"
        with pytest.raises(PermissionError):
            _unlink_when_released(target, wait_s=0.25)
        assert fake.attempts >= 2, (
            "the bounded window must actually retry (>=2 attempts in 0.25 s)"
        )
