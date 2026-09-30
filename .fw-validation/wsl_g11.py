"""G11 runner for WSL (needs clang for .S preprocessing):
  ~/fwctx-venv311/bin/python /mnt/d/github/fw-context-mcp/.fw-validation/wsl_g11.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

WSL_ROOT = Path("/mnt/d/github/fw-context-mcp")
sys.path.insert(0, str(WSL_ROOT / ".fw-validation"))
from mcp_client import McpStdioClient  # noqa: E402

WORK = Path.home() / "fwctx-v1-work" / "G11" / "work"
INDEX = Path.home() / "fwctx-v1-work" / "G11" / "index"
OUT = WSL_ROOT / ".fw-validation" / "artifacts" / "V1" / "G11" / "wsl"
PY = str(Path.home() / "fwctx-venv311" / "bin" / "python")


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for d in (WORK, INDEX):
        if d.exists():
            shutil.rmtree(d)
    WORK.mkdir(parents=True)
    shutil.copyfile(WSL_ROOT / "fwctx_validation_benchmark_pack/golden/G11_riscv_mtvec/vector.S",
                    WORK / "vector.S")
    cc = [{"directory": str(WORK), "arguments": ["clang", "-c", str(WORK / "vector.S"),
                                                 "-o", "vector.o"], "file": str(WORK / "vector.S")}]
    (WORK / "compile_commands.json").write_text(json.dumps(cc, indent=1), encoding="utf-8")
    import os
    env = {**dict(os.environ), "FW_CONTEXT_INDEX_DIR": str(INDEX)}

    def run(cmd, log):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(WORK),
                           env=env, encoding="utf-8", errors="replace")
        (OUT / log).write_text(f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
        print(log, "exit", r.returncode)
        return r

    r0 = run([PY, "-m", "fw_context_mcp.cli", "init", "--project", str(WORK), "--quick"], "init.log")
    idx = [PY, "-m", "fw_context_mcp.cli", "index", str(WORK / "compile_commands.json"),
           "--project", str(WORK), "--no-analyze", "--no-embeddings"]
    r = run(idx, "index.log")
    if r0.returncode != 0 or r.returncode != 0:
        (OUT / "FAILED_INDEX.txt").write_text(r.stderr[-3000:], encoding="utf-8")
        sys.exit(2)

    queries = [
        ("get_vector_table", {}),
        ("lookup_symbol", {"name": "irq", "exact": False}),
        ("lookup_symbol", {"name": "trap_base", "exact": True}),
        ("lookup_symbol", {"name": "exception_handler", "exact": True}),
        ("find_references", {"name": "exception_handler"}),
        ("find_references", {"name": "irq1_handler"}),
    ]
    client = McpStdioClient(index_dir=INDEX, cwd=str(WORK), platform="wsl")
    responses = {}
    try:
        client.start()
        for tool, args in queries:
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

    def objs(prefix):
        t = None
        for k, v in responses.items():
            if k.startswith(prefix) and not k.endswith("::ERROR"):
                t = v.get("text") or ""
                break
        out = []
        dec = json.JSONDecoder()
        s = (t or "").strip()
        i = 0
        while i < len(s):
            s2 = s[i:].lstrip()
            if not s2:
                break
            off = len(s[i:]) - len(s2)
            try:
                val, end = dec.raw_decode(s2)
                out.append(val)
                i = i + off + end
            except ValueError:
                break
        return out

    vt_rows = objs("get_vector_table::")
    vt_slots = [x for x in vt_rows if isinstance(x, dict) and "slot" in x and "name" in x]
    irq_rows = objs('lookup_symbol::{"exact": false, "name": "irq"')
    irq_names = sorted({x.get("name") for x in irq_rows if isinstance(x, dict) and x.get("name")})
    tb_rows = objs('lookup_symbol::{"exact": true, "name": "trap_base"')
    has_trap_base = any(isinstance(x, dict) and x.get("name") == "trap_base" for x in tb_rows)
    exc_rows = objs('lookup_symbol::{"exact": true, "name": "exception_handler"')
    has_exc = any(isinstance(x, dict) and x.get("name") == "exception_handler" for x in exc_rows)
    refs_exc = [x for x in objs('find_references::{"name": "exception_handler"')
                if isinstance(x, dict) and x.get("caller")]
    expected = json.loads((WSL_ROOT / "fwctx_validation_benchmark_pack/golden/"
                           "G11_riscv_mtvec/expected.json").read_text(encoding="utf-8-sig"))
    checks = {
        "no_pointer_table_interpretation": len(vt_slots) == 0,
        "branch_handlers_indexed": {"irq1_handler", "irq2_handler", "irq3_handler"} <= set(irq_names)
        and has_trap_base and has_exc,
    }
    # observation (not gated): the asm indexer stores no refs for branch
    # instructions (j targets) — find_references returns 'No references
    # indexed' for this unit. expected.json asserts no branch-ref surface.
    checks["all_match"] = all(checks.values())
    status = "PASS" if checks["all_match"] else "FAIL_PRODUCT"
    result = {"case_id": "G11", "platform": "wsl", "command": {"index": " ".join(idx)},
              "exit_code": 0, "expected": expected,
              "observed": {"vector_table_slots": vt_slots, "vector_table_info": vt_rows,
                           "irq_symbols": irq_names, "exception_handler_refs": refs_exc},
              "comparison": checks, "status": status,
              "notes": ["oracle knowledge cells (cause offsets 4/8/12, BASE+4*cause) are RISC-V "
                        "spec truth; the product surface test is the negative (no pointer-sized "
                        "function-pointer table interpretation)"]}
    (OUT / "G11.result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(checks, indent=1))
    print("STATUS:", status)


if __name__ == "__main__":
    main()
