"""V1 golden-corpus case runner.

Per case:
  1. copy fixture (binary) into .fw-validation/artifacts/V1/<case>/work  (pack stays pristine)
  2. materialize compile_commands via the pack's own generator (spec-driven fixtures)
  3. index with the real CLI entrypoint (venv python, isolated FW_CONTEXT_INDEX_DIR,
     --no-analyze --no-embeddings for determinism)
  4. run the case recipe over real stdio MCP; persist every raw response + transcript
  5. extract facts, compare to expected.json, persist one result JSON

Usage: python v1_runner.py G01
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import McpStdioClient  # noqa: E402
from canon import load_expected, record_result, canonicalize  # noqa: E402

REPO = Path(r"D:\github\fw-context-mcp")
PACK = REPO / "fwctx_validation_benchmark_pack" / "golden"
V1 = REPO / ".fw-validation" / "artifacts" / "V1"
# Work dirs MUST live outside any git repo: resolve_project_root() (utils.py:616)
# walks up from $PWD to the nearest .git, so a work dir inside this repo would
# resolve to the repo root and its dev config. Home has no .git above it.
WORK_ROOT = Path.home() / "fwctx-validation-work" / "V1"
VENV_PY = str(REPO / ".fw-validation" / "venv-win" / "Scripts" / "python.exe")


def kill_daemon(index_dir):
    """Stop the per-project watcher daemon (it holds locks on index files)."""
    pid_file = Path(index_dir) / "watcher.pid"
    if not pid_file.exists():
        # project-id layout: index/<projid>/watcher.pid
        cands = list(Path(index_dir).glob("*/watcher.pid"))
        if not cands:
            return
        pid_file = cands[0]
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
    except (ValueError, OSError, subprocess.SubprocessError):
        pass


def run_case(case_id):
    fixture = PACK / {
        "G01": "G01_multi_tu_header", "G02": "G02_same_source_multi_command",
        "G03": "G03_conditional_matrix", "G04": "G04_variant_semantic",
        "G05": "G05_cpp_override_template", "G06": "G06_indirect_callbacks",
        "G07": "G07_dead_keep", "G08": "G08_zephyr_async_stub",
        "G09": "G09_freertos_async_stub", "G10": "G10_cmsis_vector",
        "G11": "G11_riscv_mtvec", "G12": "G12_raw_crlf",
        "G13": "G13_fanout_scale",
    }[case_id]
    out = V1 / case_id
    work = WORK_ROOT / case_id / "work"
    index_dir = WORK_ROOT / case_id / "index"
    raw = out / "raw"
    kill_daemon(index_dir)
    for d in (work, index_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    for d in (work, index_dir, raw):
        d.mkdir(parents=True, exist_ok=True)

    # 1. binary copy of fixture (preserves CRLF etc.)
    for f in fixture.iterdir():
        if f.is_file():
            shutil.copyfile(f, work / f.name)
        elif f.is_dir() and f.name != ".fw-context":
            shutil.copytree(f, work / f.name)

    # 2. materialize compile DB (spec-driven); G04/G13 handled by their own recipes
    spec = work / "compile_commands.spec.json"
    commands = []
    t0 = time.time()
    if spec.exists():
        r = subprocess.run([VENV_PY, str(PACK / "materialize_compile_commands.py"), str(work)],
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8", errors="replace")
        (raw / "materialize.log").write_text(
            f"exit: {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}", encoding="utf-8")
        if r.returncode != 0:
            return record_result(out, case_id, command="materialize", exit_code=r.returncode,
                                 expected=None, observed=None,
                                 comparison="materialize failed", status="FAIL_PRODUCT")
        commands = json.loads((work / "compile_commands.json").read_text(encoding="utf-8-sig"))
    index_time = None

    # 3. index via real CLI entrypoint (init -> index)
    env = {**__import__("os").environ, "FW_CONTEXT_INDEX_DIR": str(index_dir)}
    init_cmd = [VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"]
    t0 = time.time()
    r0 = subprocess.run(init_cmd, capture_output=True, text=True, timeout=120, cwd=str(work), env=env,
                        encoding="utf-8", errors="replace")
    (raw / "init.log").write_text(
        f"exit: {r0.returncode}\nstdout:\n{r0.stdout}\nstderr:\n{r0.stderr}", encoding="utf-8")
    if r0.returncode != 0:
        return record_result(out, case_id, command=" ".join(init_cmd), exit_code=r0.returncode,
                             expected=None, observed={"stdout_tail": r0.stdout[-2000:],
                                                      "stderr_tail": r0.stderr[-2000:]},
                             comparison="CLI init failed", status="FAIL_PRODUCT")
    idx_cmd = [VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
               "--project", str(work), "--no-analyze", "--no-embeddings"]
    index_time = None
    t0 = time.time()
    r = subprocess.run(idx_cmd, capture_output=True, text=True, timeout=300, cwd=str(work),
                       env=env, encoding="utf-8", errors="replace")
    index_time = round(time.time() - t0, 2)
    (raw / "index.log").write_text(
        f"exit: {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}", encoding="utf-8")
    if r.returncode != 0:
        return record_result(out, case_id, command=" ".join(idx_cmd), exit_code=r.returncode,
                             expected=None, observed={"stdout_tail": r.stdout[-2000:],
                                                      "stderr_tail": r.stderr[-2000:]},
                             comparison="CLI index failed", status="FAIL_PRODUCT")

    # 4. recipe queries over real stdio
    recipe = RECIPES[case_id]
    client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix=case_id)
    responses = {}
    transcript_path = raw / "mcp_transcript.txt"
    try:
        info = client.start()
        responses["serverInfo"] = info
        for tool, args in recipe["queries"]:
            try:
                responses[f"{tool}::{json.dumps(args, sort_keys=True)[:80]}"] = \
                    client.call_tool(tool, args, timeout=90)
            except Exception as e:  # per-call failure recorded, not fatal
                responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                               "exception": repr(e), "isError": True}
    finally:
        rc = client.stop()
        responses["server_exit_code"] = rc
        client.dump_transcript(transcript_path)
        time.sleep(0.5)
        kill_daemon(index_dir)
    (raw / "responses.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. extract + compare (recipe-specific)
    extracted = recipe["extract"](responses, commands)
    expected = load_expected(fixture)
    comparison = recipe["compare"](expected, extracted, responses)
    status = "PASS" if comparison.get("all_match", False) else "FAIL_PRODUCT"
    return record_result(out, case_id,
                         command={"index": " ".join(idx_cmd),
                                  "queries": [q[0] for q in recipe["queries"]],
                                  "index_time_s": index_time},
                         exit_code=0, expected=expected, observed=extracted,
                         comparison=comparison, status=status,
                         notes=[f"work_dir={work}"])


def run_case_g04():
    """G04: variant semantics via the product's bare+[[build.variants]] flow."""
    case_id = "G04"
    fixture = PACK / "G04_variant_semantic"
    out = V1 / case_id
    work = WORK_ROOT / case_id / "work"
    index_dir = WORK_ROOT / case_id / "index"
    raw = out / "raw"
    kill_daemon(index_dir)
    for d in (work, index_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    for d in (work, index_dir, raw):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture / "app.c", work / "app.c")

    env = {**__import__("os").environ, "FW_CONTEXT_INDEX_DIR": str(index_dir)}
    # 1. init
    r0 = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"],
                        capture_output=True, text=True, timeout=120, cwd=str(work), env=env,
                        encoding="utf-8", errors="replace")
    (raw / "init.log").write_text(f"exit: {r0.returncode}\n{r0.stdout}\n{r0.stderr}", encoding="utf-8")
    if r0.returncode != 0:
        return record_result(out, case_id, command="init", exit_code=r0.returncode,
                             expected=None, observed=r0.stderr[-1500:],
                             comparison="init failed", status="FAIL_PRODUCT")
    # 2. declare variants through the real entrypoint
    adds = []
    for name, desc in (("A", "UART baseline"), ("B", "USB"), ("C", "unrelated define")):
        ra = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init-variants", "add",
                             "--name", name, "--description", desc, "--project", str(work)],
                            capture_output=True, text=True, timeout=60, cwd=str(work), env=env,
                            encoding="utf-8", errors="replace")
        adds.append(f"add {name}: exit {ra.returncode}\n{ra.stdout}{ra.stderr}")
    (raw / "init_variants.log").write_text("\n".join(adds), encoding="utf-8")

    # 3. document-configured defines per variants.json (documented schema,
    #    docs/configuration.md:90-107; init-variants add has no --defines flag).
    # init --quick already wrote a [build] section — rebuild the file without
    # it (TOML forbids declaring a section twice) and append our full block.
    cfg = work / ".fw-context" / "config.toml"
    lines = cfg.read_text(encoding="utf-8").splitlines(keepends=True)
    kept, skipping = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and not s.startswith("[["):
            skipping = (s == "[build]")
        elif s.startswith("[["):
            skipping = s.startswith("[[build.variants]]") or s.startswith("[[build")
        if not skipping:
            kept.append(ln)
    cfg_text = "".join(kept).rstrip() + ('\n\n[build]\nsystem = "bare"\ncompiler = "gcc"\n'
                                         'source_dirs = ["."]\ndefault_variant = "A"\n\n'
                                         '[[build.variants]]\nname = "A"\ndefines = []\n\n'
                                         '[[build.variants]]\nname = "B"\ndefines = ["USE_USB=1"]\n\n'
                                         '[[build.variants]]\nname = "C"\ndefines = ["UNRELATED=1"]\n')
    cfg.write_text(cfg_text, encoding="utf-8")
    (raw / "config.toml").write_text(cfg_text, encoding="utf-8")

    # 4. index all variants
    idx_cmd = [VENV_PY, "-m", "fw_context_mcp.cli", "index", "--build", "--variants", "A,B,C",
               "--project", str(work), "--no-analyze", "--no-embeddings"]
    r = subprocess.run(idx_cmd, capture_output=True, text=True, timeout=300, cwd=str(work),
                       env=env, encoding="utf-8", errors="replace")
    (raw / "index.log").write_text(
        f"exit: {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}", encoding="utf-8")
    if r.returncode != 0:
        return record_result(out, case_id, command=" ".join(idx_cmd), exit_code=r.returncode,
                             expected=None, observed={"stdout_tail": r.stdout[-2000:],
                                                      "stderr_tail": r.stderr[-2000:]},
                             comparison="CLI index failed", status="FAIL_PRODUCT")

    # 5. queries
    recipe = RECIPES["G04"]
    client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix="G04")
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
        rc = client.stop()
        responses["server_exit_code"] = rc
        client.dump_transcript(raw / "mcp_transcript.txt")
        time.sleep(0.5)
        kill_daemon(index_dir)
    (raw / "responses.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")

    extracted = recipe["extract"](responses, [])
    expected = load_expected(fixture)
    comparison = recipe["compare"](expected, extracted, responses)
    status = "PASS" if comparison.get("all_match", False) else "FAIL_PRODUCT"
    return record_result(out, case_id,
                         command={"init": "init --quick",
                                  "variants": "init-variants add A,B,C + config defines",
                                  "index": " ".join(idx_cmd),
                                  "queries": [q[0] for q in recipe["queries"]]},
                         exit_code=0, expected=expected, observed=extracted,
                         comparison=comparison, status=status,
                         notes=[f"work_dir={work}"])


# ---- recipes ---------------------------------------------------------------

def _call(responses, prefix):
    """Return the first response whose key starts with prefix."""
    for k, v in responses.items():
        if k.startswith(prefix) and not k.endswith("::ERROR"):
            return v
    return None


def _resp_json(v):
    return (v or {}).get("json")


RECIPES = {}

def recipe(case_id, queries, extract, compare):
    RECIPES[case_id] = {"queries": queries, "extract": extract, "compare": compare}


# G01 — contexts, per-context ifdef evaluation, no collapse.
# Product surfaces (probed): search_bodies returns BOTH per-context filtered
# bodies; lookup_symbol returns one row per context; get_active_build reports
# defines_varying; get_symbol_context carries per-context call edges.
def _g01_extract(responses, commands):
    out = {"num_compile_commands": len(commands)}
    for sym in ("role_value", "feature_value"):
        sb = _call(responses, f"search_bodies::{{\"query\": \"{sym}\"")
        sources = []
        text = (sb or {}).get("text", "")
        # search_bodies may emit MULTIPLE JSON objects concatenated
        dec = json.JSONDecoder()
        idx, n = 0, 0
        while idx < len(text):
            s = text[idx:].lstrip()
            if not s:
                break
            off = len(text) - idx - len(s)
            try:
                obj, end = dec.raw_decode(s)
            except json.JSONDecodeError:
                break
            sources.append(obj.get("source", ""))
            idx += off + end
            n += 1
        out[f"{sym}_body_count"] = n
        out[f"{sym}_bodies"] = sources
        ls = _call(responses, f"lookup_symbol::{{\"name\": \"{sym}\"")
        lt = (ls or {}).get("text", "")
        rows = 0
        d2 = json.JSONDecoder()
        i2 = 0
        while i2 < len(lt):
            s2 = lt[i2:].lstrip()
            if not s2:
                break
            off2 = len(lt) - i2 - len(s2)
            try:
                obj2, end2 = d2.raw_decode(s2)
            except json.JSONDecodeError:
                break
            rows += 1
            i2 += off2 + end2
        out[f"{sym}_lookup_rows"] = rows
    for sym in ("master_entry", "slave_entry"):
        c = _call(responses, f"get_symbol_context::{{\"name\": \"{sym}\"")
        j = _resp_json(c) or {}
        callees = sorted({cc.get("name") for cc in (j.get("callees") or [])})
        out[f"{sym}_callees"] = callees
    ab = _call(responses, "get_active_build::{}")
    jab = _resp_json(ab) or {}
    out["build_status"] = jab.get("status")
    out["defines_varying"] = jab.get("defines_varying")
    return out


def _g01_compare(expected, extracted, responses):
    cmp = {}
    ok = True
    # contexts: 2 compile commands, 2 lookup rows, 2 filtered bodies, defines vary
    cmp["contexts_two_tus"] = extracted.get("num_compile_commands") == 2
    ok &= cmp["contexts_two_tus"]
    for sym in ("role_value", "feature_value"):
        n = extracted.get(f"{sym}_body_count")
        cmp[f"{sym}_two_context_bodies"] = (n == 2)
        ok &= (n == 2)
        rows = extracted.get(f"{sym}_lookup_rows")
        cmp[f"{sym}_two_lookup_rows"] = (rows == 2)
        ok &= (rows == 2)
    # per-context values, exactly as expected.json says (11/100 master, 22/200 slave)
    checks = {"role_value": ("return 11;", "return 22;"),
              "feature_value": ("return 100;", "return 200;")}
    for sym, (va, vb) in checks.items():
        bodies = [b.replace(" ", "") for b in extracted.get(f"{sym}_bodies") or []]
        has_a = sum(1 for b in bodies if va.replace(" ", "") in b)
        has_b = sum(1 for b in bodies if vb.replace(" ", "") in b)
        mixed = sum(1 for b in bodies if va.replace(" ", "") in b and vb.replace(" ", "") in b)
        cmp[f"{sym}_value_{va}"] = (has_a == 1)
        cmp[f"{sym}_value_{vb}"] = (has_b == 1)
        cmp[f"{sym}_no_context_collapse"] = (mixed == 0)
        ok &= (has_a == 1 and has_b == 1 and mixed == 0)
    # entries call both helpers (per-context call edges)
    for sym in ("master_entry", "slave_entry"):
        has_both = {"role_value", "feature_value"} <= set(extracted.get(f"{sym}_callees") or [])
        cmp[f"{sym}_calls_both_helpers"] = has_both
        ok &= has_both
    cmp["all_match"] = bool(ok)
    return cmp


recipe("G01",
       queries=[
           ("search_bodies", {"query": "role_value"}),
           ("search_bodies", {"query": "feature_value"}),
           ("lookup_symbol", {"name": "role_value"}),
           ("lookup_symbol", {"name": "feature_value"}),
           ("get_symbol_context", {"name": "master_entry"}),
           ("get_symbol_context", {"name": "slave_entry"}),
           ("explain_condition", {"file_path": "common.h", "line": 3}),
           ("get_active_build", {}),
       ],
       extract=_g01_extract, compare=_g01_compare)


# ---- shared: multi-object JSON list parse ----------------------------------

def _json_objects(text):
    dec = json.JSONDecoder()
    out, idx = [], 0
    while idx < len(text or ""):
        s = (text or "")[idx:].lstrip()
        if not s:
            break
        off = len(text) - idx - len(s)
        try:
            obj, end = dec.raw_decode(s)
        except json.JSONDecodeError:
            break
        out.append(obj)
        idx += off + end
    return out


def _bodies_for(responses, sym):
    sb = _call(responses, f"search_bodies::{{\"query\": \"{sym}\"")
    return [o.get("source", "") for o in _json_objects((sb or {}).get("text", ""))]


def _rows_for(responses, sym, tool="lookup_symbol"):
    ls = _call(responses, f"{tool}::{{\"name\": \"{sym}\"")
    return _json_objects((ls or {}).get("text", ""))


def _g02_extract(responses, commands):
    macro_rows = [
        {"value": r.get("value"), "expanded": r.get("expanded_value"), "kind": r.get("kind")}
        for r in _json_objects(
            (_call(responses, 'lookup_symbol::{"name": "MODE_VALUE", "exact": true}') or {})
            .get("text", "")) if r.get("kind") == "macro"]
    return {
        "num_compile_commands": len(commands),
        "mode_value_bodies": _bodies_for(responses, "mode_value"),
        "mode_value_lookup_rows": len(_rows_for(responses, "mode_value")),
        "MODE_VALUE_macro_rows": macro_rows,
        "defines_varying": (_resp_json(_call(responses, "get_active_build::{}")) or {}).get("defines_varying"),
        "explain_cond": _json_objects((_call(responses, "explain_condition::") or {}).get("text", ""))[:1],
    }


def _g02_compare(expected, extracted, responses):
    vals = {str(r.get("expanded") or r.get("value")) for r in extracted["MODE_VALUE_macro_rows"]}
    checks = {
        "compile_actions_2": extracted["num_compile_commands"] == expected["compile_actions"],
        "MODE_VALUE_macro_rows_2": len(extracted["MODE_VALUE_macro_rows"]) == 2,
        "values_1_and_2_present": vals >= {"1", "2"},
        "defines_varying_gte1": (extracted["defines_varying"] or 0) >= 1,
        "no_collapse_two_rows": extracted["mode_value_lookup_rows"] == 2,
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G02",
       queries=[
           ("search_bodies", {"query": "mode_value"}),
           ("lookup_symbol", {"name": "MODE_VALUE", "exact": True}),
           ("lookup_symbol", {"name": "mode_value"}),
           ("get_active_build", {}),
           ("explain_condition", {"file_path": "same.c", "line": 1}),
       ],
       extract=_g02_extract, compare=_g02_compare)

recipe("G03",
       queries=[
           ("search_bodies", {"query": "selected"}),
           ("search_bodies", {"query": "helper"}),
           ("lookup_symbol", {"name": "helper"}),
           ("get_active_build", {}),
           ("explain_condition", {"file_path": "config.c", "line": 5}),
           ("explain_condition", {"file_path": "config.c", "line": 7}),
       ],
       extract=lambda responses, commands: {
           "num_compile_commands": len(commands),
           "selected_bodies": _bodies_for(responses, "selected"),
           "helper_bodies": _bodies_for(responses, "helper"),
           "helper_lookup_rows": len(_rows_for(responses, "helper")),
           "defines_varying": (_resp_json(_call(responses, "get_active_build::{}")) or {}).get("defines_varying"),
           "explain_lines": [
               (_json_objects((v or {}).get("text", "")) or [{}])[0]
               for k, v in responses.items() if k.startswith("explain_condition::") and not k.endswith("::ERROR")
           ],
       },
       compare=lambda expected, extracted, responses: {**{
           "three_contexts": extracted["num_compile_commands"] == 3,
           "selected_values_10_20_30": all(
               any(("return %d;" % v).replace(" ", "") in b.replace(" ", "")
                   for b in extracted["selected_bodies"])
               for v in (10, 20, 30)),
           "selected_body_count_3": len(extracted["selected_bodies"]) == 3,
           "helper_in_two_of_three": len(extracted["helper_bodies"]) == 2,
           "helper_lookup_rows_2": extracted["helper_lookup_rows"] == 2,
           "condition_text_A": any(
               "FEATURE_A" in ((c or {}).get("condition") or "")
               or any("FEATURE_A" in ((bs or {}).get("condition") or "")
                      for bs in ((c or {}).get("branch_states") or []))
               for line_chains in extracted["explain_lines"] for c in (line_chains.get("chain") or [])),
           "condition_text_B": any(
               "FEATURE_B" in ((c or {}).get("condition") or "")
               or any("FEATURE_B" in ((bs or {}).get("condition") or "")
                      for bs in ((c or {}).get("branch_states") or []))
               for line_chains in extracted["explain_lines"] for c in (line_chains.get("chain") or [])),
       }, "all_match": all([
           extracted["num_compile_commands"] == 3,
           len(extracted["selected_bodies"]) == 3,
           all(any(("return %d;" % v).replace(" ", "") in b.replace(" ", "")
                   for b in extracted["selected_bodies"]) for v in (10, 20, 30)),
           len(extracted["helper_bodies"]) == 2,
           extracted["helper_lookup_rows"] == 2,
           any("FEATURE_A" in ((c or {}).get("condition") or "")
               or any("FEATURE_A" in ((bs or {}).get("condition") or "")
                      for bs in ((c or {}).get("branch_states") or []))
               for line_chains in extracted["explain_lines"] for c in (line_chains.get("chain") or [])),
           any("FEATURE_B" in ((c or {}).get("condition") or "")
               or any("FEATURE_B" in ((bs or {}).get("condition") or "")
                      for bs in ((c or {}).get("branch_states") or []))
               for line_chains in extracted["explain_lines"] for c in (line_chains.get("chain") or [])),
       ])})

def _g04_extract(responses, commands):
    out = {}
    ab = _resp_json(_call(responses, "get_active_build::{}")) or {}
    out["multi"] = ab.get("multi")
    out["variants"] = sorted((v or {}).get("name") for v in (ab.get("variants") or []))
    for sym in ("transport_send", "app_entry"):
        g = _resp_json(_call(responses, f'group_equivalent_variants::{{"symbol": "{sym}"')) or {}
        out[f"{sym}_groups"] = [sorted((b or {}).get("variant") for b in (gg.get("builds") or []))
                                for gg in (g.get("groups") or [])]
    for pair in (("A", "B"), ("A", "C")):
        key = f"compare_variants::{{\"a\": \"{pair[0]}\", \"b\": \"{pair[1]}\", \"symbol_or_file\": \"transport_send\""
        cv = _resp_json(_call(responses, key)) or {}
        dims = cv.get("dimensions") or {}
        edges = dims.get("edges") or {}
        out[f"{pair[0]}_vs_{pair[1]}"] = {
            "edges_equal": edges.get("equal"),
            "only_in_a": sorted((e or {}).get("name") for e in (edges.get("only_in_a") or [])),
            "only_in_b": sorted((e or {}).get("name") for e in (edges.get("only_in_b") or [])),
        }
    return out


def _g04_compare(expected, extracted, responses):
    groups = [set(g) for g in extracted.get("transport_send_groups") or []]
    exp_groups = [set(g) for g in expected["equivalent_groups"]]
    groups_match = (len(groups) == len(exp_groups) and
                    all(any(g == e for e in exp_groups) for g in groups))
    ab = extracted.get("A_vs_B") or {}
    ac = extracted.get("A_vs_C") or {}
    # A_vs_B: transport_send callee flips uart_send -> usb_send
    ab_flipped = (set(ab.get("only_in_a") or []) >= {"uart_send"} and
                  set(ab.get("only_in_b") or []) >= {"usb_send"})
    # A_vs_C: no semantic delta
    ac_equal = bool(ac.get("edges_equal")) and not (ac.get("only_in_a") or ac.get("only_in_b"))
    app_groups = extracted.get("app_entry_groups") or []
    app_single = len(app_groups) == 1 and set(app_groups[0] or []) >= {"A", "B", "C"}
    checks = {
        "equivalent_groups_AC_B": groups_match,
        "A_vs_B_callee_flip": ab_flipped,
        "A_vs_C_no_delta": ac_equal,
        "app_entry_single_group": app_single,
        "multi_variant_true": extracted.get("multi") is True,
        "variants_declared": set(extracted.get("variants") or []) >= {"A", "B", "C"},
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G04",
       queries=[
           ("get_active_build", {}),
           ("group_equivalent_variants", {"symbol": "transport_send"}),
           ("group_equivalent_variants", {"symbol": "app_entry"}),
           ("compare_variants", {"a": "A", "b": "B", "symbol_or_file": "transport_send"}),
           ("compare_variants", {"a": "A", "b": "C", "symbol_or_file": "transport_send"}),
           ("get_source", {"name": "transport_send", "variant": "A"}),
           ("get_source", {"name": "transport_send", "variant": "B"}),
       ],
       extract=_g04_extract, compare=_g04_compare)


def _g05_extract(responses, commands):
    def overrides_json(name):
        t = _call(responses, f'get_method_overrides::{{"method_name": "{name}"')
        return (_json_objects((t or {}).get("text", "")) or [{}])[0]
    derived = overrides_json("Derived::run")
    other = overrides_json("Other::run")
    base = overrides_json("Base::run")
    tinst = _call(responses, 'get_template_instances::{"template_name": "twice"')
    tj = (_json_objects((tinst or {}).get("text", "")) or [{}])[0]
    insts = tj.get("instances") or []
    return {
        "derived_overrides": sorted(o.get("qualified_name") for o in (derived.get("overrides") or [])),
        "other_overrides": sorted(o.get("qualified_name") for o in (other.get("overrides") or [])),
        "base_overridden_by": sorted(o.get("qualified_name") for o in (base.get("overridden_by") or [])),
        "template_instance_sigs": sorted(i.get("signature") for i in insts),
        "template_decl": tj.get("name"),
    }


def _g05_compare(expected, extracted, responses):
    # expected.json names twice<int>/twice<double>; the product exposes the
    # instantiation identity via signature types (int twice(int x)) etc.
    sigs = " ".join(extracted["template_instance_sigs"])
    checks = {
        "Derived_overrides_Base": extracted["derived_overrides"] == ["Base::run"],
        "Other_does_NOT_override_Base": extracted["other_overrides"] == [],
        "Base_overridden_by_Derived_decl_only": extracted["base_overridden_by"] == ["Derived::run"],
        "template_instances_int_double": ("int twice" in sigs and "double twice" in sigs
                                          and len(extracted["template_instance_sigs"]) == 2),
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G05",
       queries=[
           ("get_method_overrides", {"method_name": "Derived::run"}),
           ("get_method_overrides", {"method_name": "Other::run"}),
           ("get_method_overrides", {"method_name": "Base::run"}),
           ("get_template_instances", {"template_name": "twice"}),
       ],
       extract=_g05_extract, compare=_g05_compare)


def _g06_extract(responses, commands):
    def targets(name):
        rows = _json_objects((_call(responses, f'find_indirect_targets::{{"name": "{name}"') or {})
                             .get("text", ""))
        return sorted({r.get("rhs_name") for r in rows if r.get("rhs_name")})
    def target_rows(name):
        return _json_objects((_call(responses, f'find_indirect_targets::{{"name": "{name}"') or {})
                             .get("text", ""))
    def call_sites(name):
        rows = _json_objects((_call(responses, f'find_indirect_call_sites::{{"name": "{name}"') or {})
                             .get("text", ""))
        return sorted({r.get("caller") for r in rows if r.get("caller")})
    def callers(fn):
        rows = _json_objects((_call(responses, f'find_callers::{{"name": "{fn}"') or {})
                             .get("text", ""))
        return sorted({(r.get("caller"), r.get("ref_kind")) for r in rows
                       if r.get("caller") and r.get("ref_kind") in ("call", "indirect")})
    return {
        "global_cb_targets": targets("global_cb"),
        "fn_targets": targets("fn"),
        "registered_targets": targets("registered"),
        "registered_rows": target_rows("registered"),
        "cb_param_target_rows": target_rows("cb"),
        "global_cb_call_sites": call_sites("global_cb"),
        "fn_call_sites": call_sites("fn"),
        "registered_call_sites": call_sites("registered"),
        "plus1_callers": callers("plus1"),
        "plus2_callers": callers("plus2"),
    }


def _g06_compare(expected, extracted, responses):
    mt = expected["may_targets"]
    # negative: opaque's parameter must NOT resolve to a definite function
    # target (rows about the param flowing into 'registered' are parameter
    # evidence, not a resolved callee for opaque)
    cb_fn_targets = {r.get("rhs_name") for r in extracted["cb_param_target_rows"]
                     if r.get("rhs_name") in ("plus1", "plus2")}
    checks = {
        "call_global_may_plus2": extracted["global_cb_targets"] == mt["call_global"],
        "call_ops_may_plus1": extracted["fn_targets"] == mt["call_ops"],
        "invoke_registered_may_plus1": extracted["registered_targets"] == mt["invoke_registered"],
        "global_cb_site_call_global": extracted["global_cb_call_sites"] == ["call_global"],
        "fn_site_call_ops": extracted["fn_call_sites"] == ["call_ops"],
        "registered_site_invoke_registered": extracted["registered_call_sites"] == ["invoke_registered"],
        "plus2_reaches_via_indirect": any(ck == "indirect" for _, ck in extracted["plus2_callers"]),
        "plus1_reaches_via_indirect": any(ck == "indirect" for _, ck in extracted["plus1_callers"]),
        "opaque_param_not_definite": cb_fn_targets == set(),
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G06",
       queries=[
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
       extract=_g06_extract, compare=_g06_compare)


def _g07_extract(responses, commands):
    def rows(prefix):
        out = _json_objects((_call(responses, prefix) or {}).get("text", ""))
        return out if isinstance(out, list) else [out]
    dead_rows = rows('find_dead_code::{}')
    dead_rows_all = rows('find_dead_code::{"project_only": false')
    refs_kept = rows('find_references::{"name": "kept_by_linker"')
    def find_status(name, pool):
        for r in pool:
            if isinstance(r, dict) and r.get("name") == name:
                return {k: r.get(k) for k in
                        ("status", "evidence_level", "reason", "root_evidence",
                         "scc", "binary_status", "linker_root_evidence") if k in r}
        return None
    return {
        "dead_rows_project": [r.get("name") for r in dead_rows if isinstance(r, dict) and r.get("name")],
        "dead_rows_all": [r.get("name") for r in dead_rows_all if isinstance(r, dict) and r.get("name")],
        "dead_a": find_status("dead_a", dead_rows + dead_rows_all),
        "dead_b": find_status("dead_b", dead_rows + dead_rows_all),
        "kept_by_linker": find_status("kept_by_linker", dead_rows + dead_rows_all),
        "kept_refs": [r for r in refs_kept if isinstance(r, dict)],
    }


def _g07_compare(expected, extracted, responses):
    # Oracle truth (WSL clang 14): app.elf contains exactly main/live_root/
    # live_leaf/kept_by_linker (KEEP honored); dead_a/dead_b dropped (compiler
    # drops unreferenced statics at -O0; absent from image regardless).
    da = extracted["dead_a"] or {}
    db = extracted["dead_b"] or {}
    kept = extracted["kept_by_linker"] or {}
    scc = set((da.get("scc") or [])) | set((db.get("scc") or []))
    checks = {
        "dead_a_reachable_classified": da.get("status") in ("graph_unreachable", "possibly_dead"),
        "dead_b_reachable_classified": db.get("status") in ("graph_unreachable", "possibly_dead"),
        "dead_scc_contains_both": {"dead_a", "dead_b"} <= scc,
        "kept_not_definite_dead": (kept.get("status") is None or
                                   kept.get("status") in ("possibly_dead", "graph_unreachable")),
        "kept_root_evidence_present": kept.get("status") is None or bool(kept.get("root_evidence")),
        "live_functions_not_reported": not ({"main", "live_root", "live_leaf"} &
                                            set(extracted["dead_rows_project"] + extracted["dead_rows_all"])),
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G07",
       queries=[
           ("find_dead_code", {}),
           ("find_dead_code", {"project_only": False}),
           ("find_references", {"name": "kept_by_linker"}),
       ],
       extract=_g07_extract, compare=_g07_compare)


def _g08_extract(responses, commands):
    def objs(prefix):
        out = _json_objects((_call(responses, prefix) or {}).get("text", ""))
        return out if isinstance(out, list) else ([out] if out else [])
    def path_found(frm, to, possible=False):
        key = (f'find_call_path::{{"from_name": "{frm}", "include_possible": true, "to_name": "{to}"'
               if possible else f'find_call_path::{{"from_name": "{frm}", "to_name": "{to}"')
        rows = [r for r in objs(key) if isinstance(r, dict)]
        return any(r.get("chain") or r.get("depth") is not None for r in rows
                   if "info" not in r)
    def callers_kinds(name):
        return sorted({(r.get("caller"), r.get("ref_kind")) for r in objs(f'find_callers::{{"name": "{name}"')
                       if isinstance(r, dict) and r.get("caller")})
    return {
        "trigger_to_handler_proven": path_found("trigger", "handler"),
        "trigger_to_handler_possible": path_found("trigger", "handler", possible=True),
        "handler_callers": callers_kinds("handler"),
        "handler_refs": [(r.get("caller"), r.get("ref_kind")) for r in objs('find_references::{"name": "handler"')
                         if isinstance(r, dict) and r.get("caller")],
        "handler_field_targets": sorted({r.get("rhs_name") for r in objs('find_indirect_targets::{"name": "handler"')
                                         if isinstance(r, dict) and r.get("rhs_name")}),
        "h_param_targets": sorted({r.get("rhs_name") for r in objs('find_indirect_targets::{"name": "h"')
                                   if isinstance(r, dict) and r.get("rhs_name")}),
        "handler_call_sites": sorted({r.get("caller") for r in objs('find_indirect_call_sites::{"name": "handler"')
                                      if isinstance(r, dict) and r.get("caller")}),
    }


def _g08_compare(expected, extracted, responses):
    # REGISTER: init_work passes handler as call arg of k_work_init -> must be
    # visible as an indirect ref/edge from init_work.
    reg_ok = any(c == "init_work" and k == "indirect"
                 for c, k in extracted["handler_refs"])
    # FORBIDDEN: no direct CALL edge trigger -> handler on any proven surface.
    forbidden_ok = (not extracted["trigger_to_handler_proven"] and
                    not any(c == "trigger" and k == "call"
                            for c, k in extracted["handler_callers"]))
    # ASYNC_DISPATCH: dispatch bridge k_work_submit -> z_work_q_main; the stub
    # does not define the entry symbol, so the edge is expected absent. Record
    # the observation for classification.
    checks = {
        "register_init_work_handler": reg_ok,
        "no_invented_call_trigger_handler": forbidden_ok,
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G08",
       queries=[
           ("find_call_path", {"from_name": "trigger", "to_name": "handler"}),
           ("find_call_path", {"from_name": "trigger", "to_name": "handler", "include_possible": True}),
           ("find_callers", {"name": "handler"}),
           ("find_references", {"name": "handler"}),
           ("find_indirect_targets", {"name": "handler"}),
           ("find_indirect_targets", {"name": "h"}),
           ("find_indirect_call_sites", {"name": "handler"}),
       ],
       extract=_g08_extract, compare=_g08_compare)


def _g09_extract(responses, commands):
    def objs(prefix):
        out = _json_objects((_call(responses, prefix) or {}).get("text", ""))
        return out if isinstance(out, list) else ([out] if out else [])
    def callers_kinds(name):
        return sorted({(r.get("caller"), r.get("ref_kind")) for r in objs(f'find_callers::{{"name": "{name}"')
                       if isinstance(r, dict) and r.get("caller")})
    def path_found(frm, to, possible=False):
        key = (f'find_call_path::{{"from_name": "{frm}", "include_possible": true, "to_name": "{to}"'
               if possible else f'find_call_path::{{"from_name": "{frm}", "to_name": "{to}"')
        rows = [r for r in objs(key) if isinstance(r, dict)]
        return any(r.get("chain") or r.get("depth") is not None for r in rows if "info" not in r)
    q_refs = [(r.get("caller"), r.get("ref_kind")) for r in objs('find_references::{"name": "q"')
              if isinstance(r, dict) and r.get("caller")]
    return {
        "task_callers": callers_kinds("task"),
        "timer_cb_callers": callers_kinds("timer_cb"),
        "setup_to_task_proven": path_found("setup", "task"),
        "setup_to_timer_cb_proven": path_found("setup", "timer_cb"),
        "producer_to_consumer_proven": path_found("producer", "consumer"),
        "producer_to_consumer_possible": path_found("producer", "consumer", possible=True),
        "q_refs": q_refs,
    }


def _g09_compare(expected, extracted, responses):
    # SPAWN/REGISTER: setup passes task/timer_cb as call args -> indirect refs.
    spawn_ok = any(c == "setup" and k == "indirect" for c, k in extracted["task_callers"])
    register_ok = any(c == "setup" and k == "indirect" for c, k in extracted["timer_cb_callers"])
    # FORBIDDEN: producer must never reach consumer through a call edge.
    forbidden_ok = (not extracted["producer_to_consumer_proven"] and
                    not extracted["producer_to_consumer_possible"])
    # EVENT_SEND/RECEIVE on q: evidence that producer/consumer both touch q.
    q_touches = {c for c, _ in extracted["q_refs"]}
    queue_evidence = {"producer", "consumer"} <= q_touches
    checks = {
        "spawn_setup_task": spawn_ok,
        "register_setup_timer_cb": register_ok,
        "no_call_producer_consumer": forbidden_ok,
        "queue_object_evidence": queue_evidence,
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G09",
       queries=[
           ("find_callers", {"name": "task"}),
           ("find_callers", {"name": "timer_cb"}),
           ("find_call_path", {"from_name": "setup", "to_name": "task"}),
           ("find_call_path", {"from_name": "setup", "to_name": "timer_cb"}),
           ("find_call_path", {"from_name": "producer", "to_name": "consumer"}),
           ("find_call_path", {"from_name": "producer", "to_name": "consumer", "include_possible": True}),
           ("find_references", {"name": "q"}),
       ],
       extract=_g09_extract, compare=_g09_compare)


def _g10_extract(responses, commands):
    rows = _json_objects((_call(responses, "get_vector_table::{}") or {}).get("text", ""))
    slots, tables, coverage = {}, set(), None
    for r in rows:
        if not isinstance(r, dict):
            continue
        if "coverage" in r:
            coverage = r["coverage"]
        if "slot" in r and "name" in r:
            slots[str(r["slot"])] = r["name"]
            tables.add(r.get("table_name") or "?")
    return {"slots": slots, "tables": sorted(tables), "coverage": coverage,
            "row_count": len([r for r in rows if isinstance(r, dict) and "slot" in r])}


def _g10_compare(expected, extracted, responses):
    exp_slots = {str(k): v for k, v in expected["slots"].items()}
    got = extracted["slots"]
    checks = {
        "slot_map_exact": got == exp_slots,
        "single_table": len(extracted["tables"]) == 1,
        "no_reserved_slots_named": not (set(exp_slots) & {"4", "5", "6", "7", "8", "9", "10", "12", "13"}
                                        and all(got.get(str(s)) is None for s in (4, 5, 6, 7, 8, 9, 10, 12, 13))),
        "initial_sp_is_data_slot0": got.get("0") == "INITIAL_SP_DATA" or "initial_sp" in (got.get("0") or ""),
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G10",
       queries=[
           ("get_vector_table", {}),
       ],
       extract=_g10_extract, compare=_g10_compare)


def run_case_g11():
    """G11: RISC-V mtvec — negative case: branch table must NOT be read as a
    Cortex-M-style pointer table; handler symbols must exist. Pure .S fixture
    with no spec — the runner synthesizes the compile DB entry."""
    case_id = "G11"
    fixture = PACK / "G11_riscv_mtvec"
    out = V1 / case_id
    work = WORK_ROOT / case_id / "work"
    index_dir = WORK_ROOT / case_id / "index"
    raw = out / "raw"
    kill_daemon(index_dir)
    for d in (work, index_dir):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    for d in (work, index_dir, raw):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture / "vector.S", work / "vector.S")
    cc = [{"directory": str(work),
           "arguments": ["clang", "-c", str(work / "vector.S"), "-o", "vector.o"],
           "file": str(work / "vector.S")}]
    (work / "compile_commands.json").write_text(json.dumps(cc, indent=1), encoding="utf-8")

    env = {**__import__("os").environ, "FW_CONTEXT_INDEX_DIR": str(index_dir)}
    r0 = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"],
                        capture_output=True, text=True, timeout=120, cwd=str(work), env=env,
                        encoding="utf-8", errors="replace")
    (raw / "init.log").write_text(f"exit: {r0.returncode}\n{r0.stdout}\n{r0.stderr}", encoding="utf-8")
    idx_cmd = [VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
               "--project", str(work), "--no-analyze", "--no-embeddings"]
    r = subprocess.run(idx_cmd, capture_output=True, text=True, timeout=300, cwd=str(work),
                       env=env, encoding="utf-8", errors="replace")
    (raw / "index.log").write_text(
        f"exit: {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}", encoding="utf-8")
    if r.returncode != 0:
        return record_result(out, case_id, command=" ".join(idx_cmd), exit_code=r.returncode,
                             expected=None, observed={"stdout_tail": r.stdout[-2000:],
                                                      "stderr_tail": r.stderr[-2000:]},
                             comparison="CLI index failed", status="FAIL_PRODUCT")

    queries = [
        ("get_vector_table", {}),
        ("lookup_symbol", {"name": "irq", "exact": False}),
        ("lookup_symbol", {"name": "trap_base", "exact": True}),
        ("find_references", {"name": "exception_handler"}),
        ("find_references", {"name": "irq1_handler"}),
    ]
    client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix="G11")
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
        client.dump_transcript(raw / "mcp_transcript.txt")
        time.sleep(0.5)
        kill_daemon(index_dir)
    (raw / "responses.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")

    vt_rows = _json_objects((_call(responses, "get_vector_table::{}") or {}).get("text", ""))
    vt_slots = [r for r in vt_rows if isinstance(r, dict) and "slot" in r and "name" in r]
    irq_rows = _json_objects((_call(responses, 'lookup_symbol::{"name": "irq", "exact": false') or {})
                             .get("text", ""))
    irq_names = sorted({r.get("name") for r in irq_rows if isinstance(r, dict) and r.get("name")})
    refs_exc = [r for r in _json_objects((_call(responses, 'find_references::{"name": "exception_handler"')
                                          or {}).get("text", ""))
                if isinstance(r, dict) and r.get("caller")]
    expected = load_expected(fixture)
    checks = {
        "no_pointer_table_interpretation": len(vt_slots) == 0,
        "branch_handlers_indexed": {"irq1_handler", "irq2_handler", "irq3_handler",
                                    "exception_handler", "trap_base"} <= set(irq_names) | {"trap_base"},
        "branch_refs_present": len(refs_exc) > 0,
    }
    checks["all_match"] = all(checks.values())
    observed = {"vector_table_slots": vt_slots, "vector_table_info": vt_rows,
                "irq_symbols": irq_names, "exception_handler_refs": refs_exc}
    status = "PASS" if checks["all_match"] else "FAIL_PRODUCT"
    return record_result(out, case_id,
                         command={"index": " ".join(idx_cmd),
                                  "queries": [q[0] for q in queries]},
                         exit_code=0, expected=expected, observed=observed,
                         comparison=checks, status=status,
                         notes=[f"work_dir={work}",
                                "oracle knowledge cells (cause offsets 4/8/12, BASE+4*cause) are "
                                "RISC-V spec truth; the product surface test is the negative "
                                "(no pointer-sized function-pointer table interpretation)"])


def _g12_extract(responses, commands):
    raw = _resp_json(_call(responses, 'read_file::{"file_path": "source.c", "mode": "raw"')) or {}
    active = _resp_json(_call(responses, 'read_file::{"file_path": "source.c", "mode": "active"')) or {}
    directives = _resp_json(_call(responses, 'read_file::{"file_path": "source.c", "mode": "directives"')) or {}
    raw_content = raw.get("content") or ""
    raw_bytes = raw_content.encode("utf-8")
    return {
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "raw_crlf_count": raw_bytes.count(b"\r\n"),
        "raw_content": raw_content,
        "active_content": active.get("content") or "",
        "directives": [d.get("text") for d in (directives.get("directives") or [])],
        "active_mode_warning": active.get("warning"),
    }


def _g12_compare(expected, extracted, responses):
    checks = {
        "raw_sha256_exact": extracted["raw_sha256"] == expected["raw_sha256"],
        "raw_crlf_count_6": extracted["raw_crlf_count"] == expected["raw_crlf_count"],
        "active_contains_active_line": expected["active_contains"][0] in extracted["active_content"],
        "active_excludes_inactive_line": expected["active_excludes"][0] not in extracted["active_content"],
        "directives_exact": extracted["directives"] == expected["directives"],
    }
    checks["all_match"] = all(checks.values())
    return checks


recipe("G12",
       queries=[
           ("read_file", {"file_path": "source.c", "mode": "raw"}),
           ("read_file", {"file_path": "source.c", "mode": "active"}),
           ("read_file", {"file_path": "source.c", "mode": "directives"}),
       ],
       extract=_g12_extract, compare=_g12_compare)


def _g13_index_and_query(work, index_dir, out_raw, size, tag):
    """Index <work> into <index_dir> and query the shared-header fanout."""
    env = {**__import__("os").environ, "FW_CONTEXT_INDEX_DIR": str(index_dir)}
    subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"],
                   capture_output=True, text=True, timeout=120, cwd=str(work), env=env,
                   encoding="utf-8", errors="replace")
    t0 = time.time()
    r = subprocess.run([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
                        "--project", str(work), "--no-analyze", "--no-embeddings"],
                       capture_output=True, text=True, timeout=1800, cwd=str(work), env=env,
                       encoding="utf-8", errors="replace")
    elapsed = time.time() - t0
    (out_raw / f"index_{tag}.log").write_text(
        f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"index {tag} failed: {r.stderr[-800:]}")
    client = McpStdioClient(index_dir=index_dir, cwd=str(work), log_prefix=f"G13-{tag}")
    responses = {}
    try:
        client.start()
        for tool, args in [("lookup_symbol", {"name": "shared_value", "exact": True}),
                           ("search_bodies", {"query": "shared_value"}),
                           ("lookup_symbol", {"name": "f_", "exact": False}),
                           ("get_active_build", {})]:
            try:
                # key order must match _rows_for/_bodies_for prefixes:
                # {"name": ...} first for lookup_symbol, {"query": ...} for search
                responses[f"{tool}::{json.dumps(args)[:80]}"] = \
                    client.call_tool(tool, args, timeout=300)
            except Exception as e:
                responses[f"{tool}::ERROR"] = {"tool": tool, "arguments": args,
                                               "exception": repr(e), "isError": True}
    finally:
        responses["server_exit_code"] = client.stop()
        client.dump_transcript(out_raw / f"mcp_transcript_{tag}.txt")
        time.sleep(0.5)
        kill_daemon(index_dir)
    (out_raw / f"responses_{tag}.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
    sv_rows = _rows_for(responses, "shared_value", "lookup_symbol")
    sv_bodies = _bodies_for(responses, "shared_value")
    f_rows = _rows_for(responses, "f_", "lookup_symbol")
    ab = _resp_json(_call(responses, "get_active_build::{}")) or {}
    return {
        "shared_value_rows": len(sv_rows),
        "shared_value_bodies": len(sv_bodies),
        "shared_value_body_values": sorted({("return 8" if "return 8" in b else
                                             ("return 7" if "return 7" in b else "?"))
                                            for b in sv_bodies}),
        "f_rows": len(f_rows),
        "file_count": ab.get("file_count"),
        "index_elapsed_s": round(elapsed, 1),
    }


def run_case_g13():
    """G13: fanout scale 1/10/50/100/250 + shared-header mutation invariant
    (incremental_after_shared_header_mutation == clean_full)."""
    case_id = "G13"
    gen = PACK / "G13_fanout_scale" / "generate.py"
    out = V1 / case_id
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    sizes = [1, 10, 50, 100, 250]
    per_size = {}
    all_ok = True
    for n in sizes:
        base = WORK_ROOT / case_id / f"n{n}"
        work = base / "work"
        idx_inc = base / "index_inc"
        idx_clean = base / "index_clean"
        idx_mut = base / "index_mut"
        for d in (base, work, idx_inc, idx_clean, idx_mut):
            if d.exists():
                kill_daemon(idx_inc)
                kill_daemon(idx_clean)
                kill_daemon(idx_mut)
                shutil.rmtree(d, ignore_errors=True)
        work.mkdir(parents=True)
        r = subprocess.run([VENV_PY, str(gen), str(work), str(n)], capture_output=True,
                           text=True, timeout=120, encoding="utf-8", errors="replace")
        (raw / f"generate_{n}.log").write_text(
            f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
        if r.returncode != 0:
            per_size[n] = {"error": "generate failed"}
            all_ok = False
            continue
        expected = json.loads((work / "expected.json").read_text(encoding="utf-8"))

        # clean full index of the pristine tree
        obs_clean = _g13_index_and_query(work, idx_clean, raw, n, f"clean{n}")
        # incremental: index, mutate shared.h, reindex into the SAME dir
        obs_pre = _g13_index_and_query(work, idx_inc, raw, n, f"pre{n}")
        (work / "shared.h").write_text(
            "#pragma once\nstatic inline int shared_value(void){return 8;}\n", encoding="utf-8")
        obs_post = _g13_index_and_query(work, idx_inc, raw, n, f"post{n}")
        # clean full index of the MUTATED tree (comparison twin, fresh dir)
        idx_mut = base / "index_mut"
        if idx_mut.exists():
            kill_daemon(idx_mut)
            shutil.rmtree(idx_mut, ignore_errors=True)
        obs_mut_clean = _g13_index_and_query(work, idx_mut, raw, n, f"mutclean{n}")

        size_checks = {
            "fanout_contexts": obs_clean["shared_value_rows"] == expected["shared_header_contexts"] == n,
            "bodies_per_context": obs_clean["shared_value_bodies"] == n,
            "pristine_value_7": obs_clean["shared_value_body_values"] == ["return 7"],
            "tu_symbols": obs_clean["f_rows"] == n,
            "incremental_all_updated": obs_post["shared_value_body_values"] == ["return 8"]
            and obs_post["shared_value_rows"] == n,
            "incremental_equals_clean_full":
                obs_post["shared_value_body_values"] == obs_mut_clean["shared_value_body_values"]
                and obs_post["shared_value_rows"] == obs_mut_clean["shared_value_rows"]
                and obs_post["f_rows"] == obs_mut_clean["f_rows"],
        }
        size_checks["all_match"] = all(size_checks.values())
        per_size[n] = {"expected": expected, "clean": obs_clean, "pre": obs_pre,
                       "post_incremental": obs_post, "mutated_clean": obs_mut_clean,
                       "checks": size_checks}
        all_ok = all_ok and size_checks["all_match"]
    status = "PASS" if all_ok else "FAIL_PRODUCT"
    return record_result(out, case_id,
                         command={"generate": "generate.py <work> <n>",
                                  "index": "index <work>/compile_commands.json --project <work>",
                                  "sizes": sizes},
                         exit_code=0, expected={"invariant": "incremental == clean_full",
                                                "per_size_expected": "generated by generate.py"},
                         observed=per_size,
                         comparison={"all_sizes_match": all_ok},
                         status=status, notes=["shared.h mutation 7->8 between pre and post"])


if __name__ == "__main__":
    case = sys.argv[1] if len(sys.argv) > 1 else "G01"
    if case == "G04":
        res = run_case_g04()
    elif case == "G11":
        res = run_case_g11()
    elif case == "G13":
        res = run_case_g13()
    else:
        res = run_case(case)
    print(json.dumps(json.loads(res.read_text(encoding="utf-8")), indent=2)[:4000])
