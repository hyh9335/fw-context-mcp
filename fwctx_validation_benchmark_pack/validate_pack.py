from pathlib import Path
import json, yaml, hashlib, sys
root = Path(__file__).resolve().parent
m = yaml.safe_load((root/"MANIFEST.yaml").read_text(encoding="utf-8"))
required = set(m["initial_load"])
required.update(m["prompts"].values())
required.update([m["public_corpus"],m["golden_manifest"],m["oracles"],m["coverage"]])
missing = [x for x in sorted(required) if not (root/x).exists()]
if missing:
    print("MISSING", *missing, sep="\n"); sys.exit(1)
g = yaml.safe_load((root/m["golden_manifest"]).read_text(encoding="utf-8"))
for f in g["fixtures"]:
    if not (root/f["path"]).exists():
        print("MISSING_FIXTURE", f["id"]); sys.exit(2)
raw=(root/"golden/G12_raw_crlf/source.c").read_bytes()
exp=json.loads((root/"golden/G12_raw_crlf/expected.json").read_text(encoding="utf-8"))
if hashlib.sha256(raw).hexdigest()!=exp["raw_sha256"]:
    print("CRLF_HASH_MISMATCH"); sys.exit(3)
if b"\r\n" not in raw:
    print("CRLF_LOST"); sys.exit(4)
print("OK", len(required), "referenced files;", len(g["fixtures"]), "golden fixtures")
