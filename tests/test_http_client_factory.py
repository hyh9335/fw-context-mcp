"""platform.http_client() factory — NEW-W0-1 (httpx 0.28.1 vs hostile NO_PROXY).

httpx 0.28.1 raises ``httpx.InvalidURL: Invalid port: ':1]'`` at Client
construction when NO_PROXY carries a bracketed IPv6 entry (``[::1]``) —
observed as ``fw-context init`` exit 1 on the W0 machine.  The accepted
fix (D6) is ONE factory: sanitize the environment-derived proxy config,
construct, and on parse failure retry once with ``trust_env=False``.

Measured on httpx 0.28.1 (W0 machine): bracketed IPv6 in NO_PROXY breaks
construction; the same entry in HTTP_PROXY/HTTPS_PROXY does not; an
unbracketed ``::1`` NO_PROXY entry constructs fine.  The sanitizer
therefore drops exactly the bracket-carrying NO_PROXY entries.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fw_context_mcp.platform import http_client, http_get

_HOSTILE = "127.0.0.1,localhost,::1,huawei.com,.huawei.com,[::1]"

_SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture
def hostile_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", _HOSTILE)
    monkeypatch.setenv("no_proxy", _HOSTILE)


class TestFactoryConstruction:
    def test_client_constructs_under_hostile_no_proxy(self, hostile_proxy_env):
        client = http_client()
        client.close()

    def test_client_constructs_with_base_url(self, hostile_proxy_env):
        client = http_client(base_url="http://127.0.0.1:1", timeout=1.0)
        client.close()

    def test_bracketed_entries_are_dropped_saner_entries_kept(
        self, hostile_proxy_env, caplog
    ):
        http_client().close()
        current = os.environ.get("NO_PROXY", "")
        assert "[::1]" not in current, f"bracketed entry must be dropped, got {current!r}"
        assert "127.0.0.1" in current, "safe entries must survive the sanitizer"
        assert any("NO_PROXY" in rec.getMessage() for rec in caplog.records), (
            "dropped entries must be logged with the variable name"
        )

    def test_safe_environment_is_not_mutated(self, monkeypatch):
        monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
        http_client().close()
        assert os.environ.get("NO_PROXY") == "127.0.0.1,localhost"

    def test_http_get_routes_through_the_factory(self, hostile_proxy_env):
        """A one-shot GET against a closed port: construction must survive."""
        import httpx

        with pytest.raises(httpx.HTTPError) as excinfo:
            http_get("http://127.0.0.1:1/api/tags", timeout=2.0)
        assert not isinstance(excinfo.value, httpx.InvalidURL), (
            "InvalidURL is a construction failure, not a network failure"
        )


class TestProductSiteE2E:
    def test_llm_diag_check_setup_reports_error_not_invalidurl(
        self, hostile_proxy_env
    ):
        """A representative product site (llm/_diag.py) under the hostile env."""
        from fw_context_mcp.config.settings import LLMConfig
        from fw_context_mcp.llm._diag import check_setup

        cfg = LLMConfig(ollama_url="http://127.0.0.1:1", model="m", embed_model="e")
        result = check_setup(cfg)
        assert result.get("status") == "error"
        assert "Invalid port" not in str(result.get("message", ""))

    def test_subprocess_product_import_construction(self, tmp_path: Path):
        """End to end: a fresh interpreter constructs the product's HTTP path."""
        script = "\n".join(
            [
                "import os",
                f"os.environ['NO_PROXY'] = {_HOSTILE!r}",
                "os.environ['no_proxy'] = os.environ['NO_PROXY']",
                "import sys",
                f"sys.path.insert(0, {_SRC!r})",
                "from fw_context_mcp.platform import http_client",
                "c = http_client(base_url='http://127.0.0.1:1')",
                "c.close()",
                "print('OK')",
            ]
        )
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "OK"
