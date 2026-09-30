"""I7 acquisition — write fetched C4/C6 snapshots to disk + git blob SHA-1 receipt.

Source: raw.githubusercontent.com over HTTPS at the pinned SHAs (web_fetch;
git/pip egress blocked in this environment — recorded channel substitution).
GitHub API blob verification attempted; api.github.com returns HTTP 422 via
the fetch channel — recorded as ATTEMPTED-BLOCKED. Provenance rests on the
HTTPS raw fetch + cross-file consistency with PR #14365 (pre-fix conditional
listing / post-fix unconditional listing / local guard).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fault_discovery_agent.schemas.canonical_json import git_blob_sha1  # noqa: E402

ACQ = Path(__file__).resolve().parent / "rounds" / "I7_C4C6_acquisition"
PRE = "526ba1ab343a00b2e1801e089392eed64758b820"
POST = "67ed036227b14bbab01b63801bfd7e218dfd6107"

FILES = {
    "libs/libc/unistd/lib_getcwd.c": {"pre": PRE, "post": POST},
    "libs/libc/unistd/Make.defs": {"pre": PRE, "post": POST},
    "libs/libc/unistd/CMakeLists.txt": {"pre": PRE, "post": POST},
    "libs/libc/stdlib/Kconfig": {"pre": PRE, "post": None},
}

# Content fetched via web_fetch (verbatim HTTP 200 bodies). The pre-fix and
# post-fix Make.defs/lib_getcwd.c bodies are stored by the acquisition run
# I7_FETCH (run_i7_fetch.py) — this module hashes whatever is on disk.
receipt = {"channel": "web_fetch raw.githubusercontent.com (HTTPS)",
           "api_blob_verification": "ATTEMPTED-BLOCKED (HTTP 422)",
           "pre_fix_sha": PRE, "post_fix_sha": POST, "files": {}}
for rel, sides in FILES.items():
    for side, sha in sides.items():
        if sha is None:
            continue
        p = ACQ / side / rel
        if not p.is_file():
            receipt["files"][f"{side}:{rel}"] = "MISSING"
            continue
        blob = git_blob_sha1(p.read_bytes())
        receipt["files"][f"{side}:{rel}"] = {"bytes": p.stat().st_size, "git_blob_sha1": blob, "commit": sha}
(ACQ / "ACQUISITION_RECEIPT.json").write_text(
    json.dumps(receipt, indent=1, sort_keys=True), encoding="utf-8"
)
print(json.dumps(receipt, indent=1, sort_keys=True))
