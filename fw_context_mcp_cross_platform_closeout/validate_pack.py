from pathlib import Path
import yaml, json, sys
root=Path(__file__).resolve().parent
m=yaml.safe_load((root/"MANIFEST.yaml").read_text(encoding="utf-8"))
req=set(m["initial_load"])
for t in m["tasks"].values():
    req.update([t["prompt"],t["design"],t["test"]])
req.update(m["special_agents"].values())
req.update(m["workflow"].values())
missing=[x for x in sorted(req) if not (root/x).exists()]
if missing:
    print("MISSING", *missing, sep="\n"); sys.exit(1)
if (root/m["entrypoint"]).stat().st_size > 6000:
    print("ENTRYPOINT_TOO_LARGE"); sys.exit(2)
ledger=json.loads((root/"state/issue_ledger.seed.json").read_text(encoding="utf-8"))
ids=[x["id"] for x in ledger]
assert len(ids)==len(set(ids))
print("OK", len(req), "refs;", len(ids), "issues")
