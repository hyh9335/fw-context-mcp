"""G04 runner for WSL (has gcc). Run inside WSL:
  ~/fwctx-venv311/bin/python /mnt/d/github/fw-context-mcp/.fw-validation/wsl_g04.py
Artifacts land on /mnt/d/.../artifacts/V1/G04/wsl/.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

WSL_ROOT = Path("/mnt/d/github/fw-context-mcp")
sys.path.insert(0, str(WSL_ROOT / ".fw-validation"))
from mcp_client import McpStdioClient  # noqa: E402
from v1_runner import RECIPES  # noqa: E402  (recipes are pure data/functions)

WORK = Path.home() / "fwctx-v1-work" / "G04" / "work"
INDEX = Path.home() / "fwctx-v1-work" / "G04" / "index"
OUT = WSL_ROOT / ".fw-validation" / "artifacts" / "V1" / "G04" / "wsl"
PY = str(Path.home() / "fwctx-venv311" / "bin" / "python")

def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for d in (WORK, INDEX):
        if d.exists():
            shutil.rmtree(d)
    WORK.mkdir(parents=True)
    shutil.copyfile(WSL_ROOT / "fwctx_validation_benchmark_pack/golden/G04_variant_semantic/app.c",
                    WORK / "app.c")
    env = {**dict(__import__("os").environ), "FW_CONTEXT_INDEX_DIR": str(INDEX)}

    def run(cmd, log):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(WORK),
                           env=env, encoding="utf-8", errors="replace")
        (OUT / log).write_text(f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
        print(log, "exit", r.returncode)
        return r

    r0 = run([PY, "-m", "fw_context_mcp.cli", "init", "--project", str(WORK), "--quick"], "init.log")
    if r0.returncode != 0:
        sys.exit(1)
    for name in ("A", "B", "C"):
        run([PY, "-m", "fw_context_mcp.cli", "init-variants", "add", "--name", name,
             "--project", str(WORK)], f"init_variants_{name}.log")
    cfg = WORK / ".fw-context" / "config.toml"
    lines = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, skipping = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and not s.startswith("[["):
            skipping = (s == "[build]")
        elif s.startswith("[["):
            skipping = s.startswith("[[build")
        if not skipping:
            kept.append(ln)
    cfg_text = "".join(kept).rstrip() + (
        '\n\n[build]\nsystem = "bare"\ncompiler = "gcc"\nsource_dirs = ["."]\n'
        'default_variant = "A"\n\n'
        '[[build.variants]]\nname = "A"\ndefines = []\n\n'
        '[[build.variants]]\nname = "B"\ndefines = ["USE_USB=1"]\n\n'
        '[[build.variants]]\nname = "C"\ndefines = ["UNRELATED=1"]\n')
    cfg.write_text(cfg_text, encoding="utf-8")
    (OUT / "config.toml").write_text(cfg_text, encoding="utf-8")

    idx = [PY, "-m", "fw_context_mcp.cli", "index", "--build", "--variants", "A,B,C",
           "--project", str(WORK), "--no-analyze", "--no-embeddings"]
    r = run(idx, "index.log")
    if r.returncode != 0:
        (OUT / "FAILED_INDEX.txt").write_text(r.stderr[-3000:], encoding="utf-8")
        sys.exit(2)

    recipe = RECIPES["G04"]
    client = McpStdioClient(index_dir=INDEX, cwd=str(WORK), platform="wsl")
    responses = {}
    try:
        client.start()
        for tool, args in recipe["queries"]:
            try:
                responses[f"{tool}::{json.dumps(args, sort_keys=True)[:80]}"] = \
                    client.call_tool(tool, args, timeout=90)
            except Exception as e:
                responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                               "exception": repr(e), "isError": True}
    finally:
        responses["server_exit_code"] = client.stop()
        client.dump_transcript(OUT / "mcp_transcript.txt")
    (OUT / "responses.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")

    def _call(prefix):
        for k, v in responses.items():
            if k.startswith(prefix) and not k.endswith("::ERROR"):
                return v
        return None

    extracted = recipe["extract"](responses, [])
    expected = json.loads((WSL_ROOT / "fwctx_validation_benchmark_pack/golden/"
                           "G04_variant_semantic/expected.json").read_text(encoding="utf-8-sig"))
    comparison = recipe["compare"](expected, extracted, responses)
    status = "PASS" if comparison.get("all_match", False) else "FAIL_PRODUCT"
    result = {"case_id": "G04", "platform": "wsl",
              "command": {"index": " ".join(idx)}, "exit_code": 0,
              "expected": expected, "observed": extracted,
              "comparison": comparison, "status": status}
    (OUT / "G04.result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(comparison, indent=1))
    print("STATUS:", status)


if __name__ == "__main__":
    main()
