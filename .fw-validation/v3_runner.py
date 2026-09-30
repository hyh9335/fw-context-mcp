"""V3 cross-platform phase — WINDOWS reproducibility leg: golden cases re-run on Windows,
compared against the V1 Windows baselines (same expected.json oracles). Reuses v1_runner
recipes verbatim; redirects outputs to artifacts/V3 and work dirs to .../V3 so V1 evidence
stays pristine. The WSL parity leg is v3_wsl_runner.py (artifacts/V3/wsl/); PARITY_DIFF.json
compares the two platforms.

Windows scope per pack plan: G01, G02, G03, G05, G06, G07, G10, G12.
G04/G11: Windows side BLOCKED (toolchain facts recorded in V0-WIN).
NOTE: G08/G09 ran on WINDOWS in V1 (per V1 ledger); G11 ran on WSL; G13 scope per V1_SUMMARY.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import v1_runner as R  # noqa: E402

R.V1 = R.REPO / ".fw-validation" / "artifacts" / "V3"
R.WORK_ROOT = Path.home() / "fwctx-validation-work" / "V3"

CASES = ["G01", "G02", "G03", "G05", "G06", "G07", "G10", "G12"]


def main():
    only = sys.argv[1:] or CASES
    for case_id in only:
        print(f"===== V3 {case_id} =====", flush=True)
        try:
            res = R.run_case(case_id)
            print(f"  -> {res.get('status') if isinstance(res, dict) else res}", flush=True)
        except Exception as e:  # keep going; classification happens per-case
            print(f"  -> EXCEPTION {e!r}", flush=True)


if __name__ == "__main__":
    main()
