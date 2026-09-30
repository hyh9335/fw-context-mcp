"""V3_CROSS_PLATFORM — WSL leg.

Runs the same 8 golden cases as the Windows V3 leg, inside WSL with the product venv
(~/fwctx-venv311 + PYTHONPATH=/mnt/d/github/fw-context-mcp/src), reusing v1_runner recipes
verbatim with WSL-path constants. Outputs to artifacts/V3/wsl/<case>/ so the Windows leg
stays pristine. Run:  wsl bash -c "cd /mnt/d/.../.fw-validation && ~/fwctx-venv311/bin/python v3_wsl_runner.py"
"""
import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v1_runner as R  # noqa: E402
from mcp_client import McpStdioClient  # noqa: E402

REPO_WSL = Path("/mnt/d/github/fw-context-mcp")
R.REPO = REPO_WSL
R.PACK = REPO_WSL / "fwctx_validation_benchmark_pack" / "golden"
R.V1 = REPO_WSL / ".fw-validation" / "artifacts" / "V3" / "wsl"
R.WORK_ROOT = Path.home() / "fwctx-validation-work" / "V3-WSL"
R.VENV_PY = str(Path.home() / "fwctx-venv311" / "bin" / "python")
# the client must spawn the MCP server with the WSL venv python, not the Windows one
R.McpStdioClient = partial(McpStdioClient, platform="wsl")

CASES = ["G01", "G02", "G03", "G05", "G06", "G07", "G10", "G12"]


def main():
    only = sys.argv[1:] or CASES
    for case_id in only:
        print(f"===== V3-WSL {case_id} =====", flush=True)
        try:
            res = R.run_case(case_id)
            status = res.get("status") if isinstance(res, dict) else "?"
            print(f"  -> {status}", flush=True)
        except Exception as e:
            print(f"  -> EXCEPTION {e!r}", flush=True)


if __name__ == "__main__":
    main()
