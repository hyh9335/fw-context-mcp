"""I5_DETECTOR_ADMISSION formal run — all five plant packages, must-pass gate."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.k3 import run_plant_tests, must_pass_gate  # noqa: E402

RD = Path(__file__).resolve().parent / "scratch_i5_admission"
RD.mkdir(exist_ok=True)
PACKAGES = ["PT-G1", "PT-G4", "PT-G6", "PT-G7", "PT-G8"]

receipts = run_plant_tests(PACKAGES, freeze_id="FRZ-I5-ADMISSION", round_dir=RD)
must_pass_gate(receipts, PACKAGES)
print("ADMISSION GATE: PASS")
for r in receipts:
    print(f"  {r.package}: {r.verdict} checks={len(r.checks_evaluated)} fixture_hash={r.fixture_hash[:12]}...")
print("receipts file:", (RD / "plant_receipts.jsonl").exists())
