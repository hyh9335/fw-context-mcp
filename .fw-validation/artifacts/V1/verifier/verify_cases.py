"""Independent V1 verifier: rerun selected golden cases end-to-end.

Usage: venv-win python verify_cases.py G01 G03 G06 G10 G12
Writes raw responses + extracted facts under .fw-validation/artifacts/V1/verifier/raw/<case>/.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402

REPO = Path(r"D:\github\fw-context-mcp")
PACK = REPO / "fwctx_validation_benchmark_pack" / "golden"
VER = REPO / ".fw-validation" / "artifacts" / "V1" / "verifier"
WORK_ROOT = Path.home() / "fwctx-validation-work" / "V1-VERIFY"
VENV_PY = str(REPO / ".fw-validation" / "venv-win" / "Scripts" / "python.exe")

CASES = {
    "G01": "G01_multi_tu_header",
    "G03": "G03_conditional_matrix",
    "G06": "G06_indirect_callbacks",
    "G10": "G10_cmsis_vector",
    "G12": "G12_raw_crlf",
}

QUERIES = {
    "G01": [
        ("search_bodies", {"query": "role_value"}),
        ("search_bodies", {"query": "feature_value"}),
        ("lookup_symbol", {"name": "role_value"}),
        ("lookup_symbol", {"name": "feature_value"}),
        ("get_active_build", {}),
    ],
    "G03": [
        ("search_bodies", {"query": "selected"}),
        ("search_bodies", {"query": "helper"}),
        ("lookup_symbol", {"name": "helper"}),
        ("explain_condition", {"file_path": "config.c", "line": 5}),
        ("get_active_build", {}),
    ],
    "G06": [
        ("find_indirect_targets", {"name": "global_cb"}),
        ("find_indirect_targets", {"name": "fn"}),
        ("find_indirect_targets", {"name": "registered"}),
        ("find_indirect_targets", {"name": "cb"}),
        ("find_indirect_call_sites", {"name": "global_cb"}),
        ("find_indirect_call_sites", {"name": "fn"}),
        ("find_indirect_call_sites", {"name": "registered"}),
        ("find_callers", {"name": "plus1"}),
        ("find_callers", {"name": "plus2"}),
    ],
    "G10": [
        ("get_vector_table", {}),
    ],
    "G12": [
        ("read_file", {"file_path": "source.c", "mode": "raw"}),
        ("read_file", {"file_path": "source.c", "mode": "active"}),
        ("read_file", {"file_path": "source.c", "mode": "directives"}),
    ],
}


def kill_daemon(index_dir):
    pid_file = Path(index_dir) / "watcher.pid"
    if not pid_file.exists():
        cands = list(Path(index_dir).glob("*/watcher.pid"))
        if not cands:
            return
        pid_file = cands[0]
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
    except (ValueError, OSError, subprocess.SubprocessError):
        pass


def jobjs(text):
    dec = json.JSONDecoder()
    out, i = [], 0
    while i < len(text or ""):
        s = (text or "")[i:].lstrip()
        if not s:
            break
        off = len(text) - i - len(s)
        try:
            o, end = dec.raw_decode(s)
        except json.JSONDecodeError:
            break
        out.append(o)
        i += off + end
    return out


def run(cmd, cwd=None, env=None, timeout=150):
    e = {**os.environ, **(env or {}), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=e, timeout=timeout)


def prep(case):
    fixture = PACK / CASES[case]
    work = WORK_ROOT / case / "work"
    index_dir = WORK_ROOT / case / "index"
    raw = VER / "raw" / case
    kill_daemon(index_dir)
    for d in (work, index_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    for d in (work, index_dir, raw):
        d.mkdir(parents=True, exist_ok=True)
    for f in fixture.iterdir():
        if f.is_file():
            shutil.copyfile(f, work / f.name)
    log = {}
    r = run([VENV_PY, str(PACK / "materialize_compile_commands.py"), str(work)])
    log["materialize"] = f"exit {r.returncode}\n{r.stdout}\n{r.stderr}"
    if r.returncode != 0:
        raise RuntimeError("materialize failed:\n" + log["materialize"][-800:])
    env = {"FW_CONTEXT_INDEX_DIR": str(index_dir)}
    r0 = run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"],
             cwd=str(work), env=env)
    log["init"] = f"exit {r0.returncode}\n{r0.stdout}\n{r0.stderr}"
    if r0.returncode != 0:
        raise RuntimeError("init failed:\n" + log["init"][-800:])
    t0 = time.time()
    r1 = run([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
              "--project", str(work), "--no-analyze", "--no-embeddings"],
             cwd=str(work), env=env, timeout=170)
    log["index"] = f"exit {r1.returncode}\n{r1.stdout}\n{r1.stderr}\n(elapsed {time.time()-t0:.2f}s)"
    if r1.returncode != 0:
        raise RuntimeError("index failed:\n" + log["index"][-800:])
    (raw / "setup.log").write_text(
        "\n\n".join(f"== {k} ==\n{v}" for k, v in log.items()), encoding="utf-8")
    return work, index_dir, raw


def run_queries(case, work, index_dir, raw):
    client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix="VER-" + case)
    responses = {}
    try:
        info = client.start()
        responses["serverInfo"] = info
        for tool, args in QUERIES[case]:
            key = f"{tool}::{json.dumps(args, sort_keys=True)[:100]}"
            try:
                responses[key] = client.call_tool(tool, args, timeout=90)
            except Exception as e:  # per-call failure recorded, not fatal
                responses[key + "::ERROR"] = {"tool": tool, "arguments": args,
                                              "exception": repr(e), "isError": True}
    finally:
        responses["server_exit_code"] = client.stop()
        client.dump_transcript(raw / "mcp_transcript.txt")
        time.sleep(0.5)
        kill_daemon(index_dir)
    (raw / "responses.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
    return responses


def _first(responses, prefix):
    for k, v in responses.items():
        if k.startswith(prefix) and not k.endswith("::ERROR"):
            return v
    return None


def extract_and_compare(case, responses):
    def objs(prefix):
        return jobjs((_first(responses, prefix) or {}).get("text", ""))

    facts, checks = {}, {}
    if case == "G01":
        for sym, (va, vb) in {"role_value": ("return 11;", "return 22;"),
                              "feature_value": ("return 100;", "return 200;")}.items():
            bodies = [o.get("source", "") for o in objs(f'search_bodies::{{"query": "{sym}"')]
            sq = [b.replace(" ", "") for b in bodies]
            rows = objs(f'lookup_symbol::{{"name": "{sym}"')
            facts[f"{sym}_body_count"] = len(bodies)
            facts[f"{sym}_has_{va.split()[1].rstrip(';')}"] = any(va.replace(" ", "") in b for b in sq)
            facts[f"{sym}_has_{vb.split()[1].rstrip(';')}"] = any(vb.replace(" ", "") in b for b in sq)
            facts[f"{sym}_mixed_bodies"] = sum(
                1 for b in sq if va.replace(" ", "") in b and vb.replace(" ", "") in b)
            facts[f"{sym}_lookup_rows"] = len(rows)
            checks[f"{sym}_two_bodies"] = len(bodies) == 2
            checks[f"{sym}_has_{va.split()[1].rstrip(';')}"] = facts[f"{sym}_has_{va.split()[1].rstrip(';')}"]
            checks[f"{sym}_has_{vb.split()[1].rstrip(';')}"] = facts[f"{sym}_has_{vb.split()[1].rstrip(';')}"]
            checks[f"{sym}_no_collapse"] = facts[f"{sym}_mixed_bodies"] == 0
            checks[f"{sym}_lookup_rows_2"] = len(rows) == 2
        ab = (objs("get_active_build::{}") or [{}])[0]
        facts["build_status"] = ab.get("status")
        facts["defines_varying"] = ab.get("defines_varying")
        checks["build_ready"] = ab.get("status") == "ready"
        checks["defines_varying_2"] = ab.get("defines_varying") == 2
    elif case == "G03":
        sel = [o.get("source", "") for o in objs('search_bodies::{"query": "selected"')]
        sq = [b.replace(" ", "") for b in sel]
        facts["selected_body_count"] = len(sel)
        for v in (10, 20, 30):
            needle = f"return {v};".replace(" ", "")
            facts[f"selected_has_return_{v}"] = any(needle in b for b in sq)
        helper_bodies = [o.get("source", "") for o in objs('search_bodies::{"query": "helper"')]
        helper_rows = objs('lookup_symbol::{"name": "helper"')
        facts["helper_bodies"] = helper_bodies
        facts["helper_body_count"] = len(helper_bodies)
        facts["helper_lookup_rows"] = len(helper_rows)
        facts["helper_row_names"] = sorted({r.get("name") for r in helper_rows if r.get("name")})
        ex = objs('explain_condition::{"file_path": "config.c", "line": 5')
        chain = (ex[0].get("chain") or [{}])[0] if ex else {}
        bs = {(b.get("kind"), b.get("condition")): b.get("taken")
              for b in (chain.get("branch_states") or [])}
        facts["explain_conditions"] = sorted({c for (_, c) in bs if c})
        facts["explain_branch_states"] = {f"{k}:{v}": t for (k, v), t in bs.items()}
        facts["defines_varying"] = ((objs("get_active_build::{}") or [{}])[0]).get("defines_varying")
        checks["selected_3_bodies"] = len(sel) == 3
        checks["selected_values_10_20_30"] = all(
            any(f"return {v};".replace(" ", "") in b for b in sq) for v in (10, 20, 30))
        checks["helper_search_bodies_empty"] = len(helper_bodies) == 0
        checks["helper_lookup_rows_2"] = len(helper_rows) == 2
        checks["cond_A_text_present"] = any("FEATURE_A" in c for c in facts["explain_conditions"])
        checks["cond_B_text_present"] = any("FEATURE_B" in c for c in facts["explain_conditions"])
        checks["if_false_elif_taken"] = bs.get(("if", "defined(FEATURE_A) && LEVEL >= 2")) == 0 and \
            bs.get(("elif", "defined(FEATURE_B) || LEVEL == 1")) == 1
    elif case == "G06":
        def targets(name):
            rows = objs(f'find_indirect_targets::{{"name": "{name}"')
            return sorted({r.get("rhs_name") for r in rows if r.get("rhs_name")})
        def sites(name):
            rows = objs(f'find_indirect_call_sites::{{"name": "{name}"')
            return sorted({r.get("caller") for r in rows if r.get("caller")})
        def callers(fn):
            rows = objs(f'find_callers::{{"name": "{fn}"')
            return sorted({(r.get("caller"), r.get("ref_kind")) for r in rows
                           if r.get("caller") and r.get("ref_kind") in ("call", "indirect")})
        for nm in ("global_cb", "fn", "registered", "cb"):
            facts[f"targets_{nm}"] = targets(nm)
        facts["registered_rows"] = objs('find_indirect_targets::{"name": "registered"')
        for nm in ("global_cb", "fn", "registered"):
            facts[f"sites_{nm}"] = sites(nm)
        facts["callers_plus1"] = [list(t) for t in callers("plus1")]
        facts["callers_plus2"] = [list(t) for t in callers("plus2")]
        checks["global_cb_targets_plus2"] = facts["targets_global_cb"] == ["plus2"]
        checks["fn_targets_plus1"] = facts["targets_fn"] == ["plus1"]
        checks["registered_never_plus1"] = "plus1" not in facts["targets_registered"]
        checks["registered_param_cb_visible"] = "cb" in facts["targets_registered"]
        checks["site_call_global"] = facts["sites_global_cb"] == ["call_global"]
        checks["site_call_ops"] = facts["sites_fn"] == ["call_ops"]
        checks["site_invoke_registered"] = facts["sites_registered"] == ["invoke_registered"]
        checks["plus1_indirect_reachable"] = any(k == "indirect" for _, k in facts["callers_plus1"])
        checks["plus2_indirect_reachable"] = any(k == "indirect" for _, k in facts["callers_plus2"])
    elif case == "G10":
        rows = objs("get_vector_table::{}")
        slots, tables, coverage = {}, set(), None
        for r in rows:
            if not isinstance(r, dict):
                continue
            if "coverage" in r:
                coverage = r["coverage"]
            if "slot" in r and "name" in r:
                slots[str(r["slot"])] = r["name"]
                tables.add(r.get("table_name") or "?")
        facts["slots"] = slots
        facts["tables"] = sorted(tables)
        facts["coverage"] = coverage
        exp = {"1": "Reset_Handler", "2": "NMI_Handler", "3": "HardFault_Handler",
               "11": "SVC_Handler", "14": "PendSV_Handler", "15": "SysTick_Handler",
               "16": "IRQ0_Handler", "17": "IRQ1_Handler"}
        facts["handler_slots_exact"] = {k: v for k, v in slots.items() if k in exp} == exp
        facts["slot0_named"] = "0" in slots
        facts["reserved_named"] = sorted([s for s in ("4", "5", "6", "7", "8", "9", "10", "12", "13") if s in slots])
        facts["slot0_in_coverage"] = bool(coverage) and "0" in (coverage or "")
        checks["handler_slots_exact"] = facts["handler_slots_exact"]
        checks["single_table"] = len(tables) == 1
        checks["reserved_slots_unnamed"] = facts["reserved_named"] == []
        checks["slot0_missing"] = not facts["slot0_named"]
    elif case == "G12":
        raw = (objs('read_file::{"file_path": "source.c", "mode": "raw"') or [{}])[0]
        act = (objs('read_file::{"file_path": "source.c", "mode": "active"') or [{}])[0]
        drs = (objs('read_file::{"file_path": "source.c", "mode": "directives"') or [{}])[0]
        rb = (raw.get("content") or "").encode("utf-8")
        facts["raw_sha256"] = hashlib.sha256(rb).hexdigest()
        facts["raw_crlf_count"] = rb.count(b"\r\n")
        facts["raw_source_origin"] = raw.get("source_origin")
        facts["raw_content"] = raw.get("content")
        facts["active_content"] = act.get("content")
        facts["directives"] = [d.get("text") for d in (drs.get("directives") or [])]
        checks["raw_sha256_exact"] = facts["raw_sha256"] == \
            "c7c3fa2af6ede9e08ba27f377e874b133ab7951a953dd0c0be6bef277087b8cc"
        checks["raw_crlf_6"] = facts["raw_crlf_count"] == 6
        checks["active_has_active_line"] = "active_line" in (facts["active_content"] or "")
        checks["active_excludes_inactive_line"] = "inactive_line" not in (facts["active_content"] or "")
        checks["directives_exact"] = facts["directives"] == \
            ["#define FLAG 1", "#if FLAG", "#else", "#endif"]
    checks["ALL"] = all(bool(v) for v in checks.values())
    return facts, checks


def main():
    cases = [a for a in sys.argv[1:] if a in CASES]
    summary = {}
    for case in cases:
        t0 = time.time()
        try:
            work, index_dir, raw = prep(case)
            responses = run_queries(case, work, index_dir, raw)
            facts, checks = extract_and_compare(case, responses)
            (raw / "facts.json").write_text(
                json.dumps({"facts": facts, "checks": checks, "work": str(work),
                            "index_dir": str(index_dir), "elapsed_s": round(time.time() - t0, 2)},
                           indent=2, ensure_ascii=False), encoding="utf-8")
            summary[case] = {"ok": True, "checks": checks, "work": str(work),
                             "elapsed_s": round(time.time() - t0, 2)}
        except Exception as e:
            summary[case] = {"ok": False, "error": repr(e)}
        print(json.dumps({case: summary[case]}, ensure_ascii=False))
    (VER / f"summary_{'_'.join(cases) or 'none'}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
