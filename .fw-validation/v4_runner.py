"""V4_ADVERSARIAL runner — 8 mutation classes from workflow/STATE_MACHINE.md:47-57.

Small golden fixtures (G01/G03) + real stdio MCP + direct DB oracle checks.
Every case: real entrypoints only (init -> index -> stdio queries), daemon killed before
any tree/CC mutation, artifacts under .fw-validation/artifacts/V4/<case>/.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mcp_client import McpStdioClient  # noqa: E402
from v1_runner import VENV_PY, PACK, kill_daemon  # noqa: E402

REPO = Path(r"D:\github\fw-context-mcp")
OUT = REPO / ".fw-validation" / "artifacts" / "V4"
WORK_ROOT = Path.home() / "fwctx-validation-work" / "V4"
G01 = PACK / "G01_multi_tu_header"
G03 = PACK / "G03_conditional_matrix"
CASE_NAMES = {"a1": "A1_shared_header_change", "a2": "A2_macro_config_change",
              "a3": "A3_build_variant_switch", "a4": "A4_source_delete_add",
              "a5": "A5_stale_noop_reindex", "a6": "A6_reindex_x3_idempotence",
              "a7": "A7_unsupported_arch_query", "a8": "A8_malformed_env"}


def sh(cmd, cwd, timeout=300, env=None):
    e = {**__import__("os").environ, **(env or {})}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd), env=e,
                       encoding="utf-8", errors="replace")
    return r


def fresh(case, fixture):
    out = OUT / case
    work = WORK_ROOT / case / "work"
    idx = WORK_ROOT / case / "index"
    raw = out / "raw"
    for d in (work, idx):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    for d in (work, idx, raw):
        d.mkdir(parents=True, exist_ok=True)
    for f in fixture.iterdir():
        if f.is_file():
            shutil.copyfile(f, work / f.name)
        elif f.is_dir() and f.name != ".fw-context":
            shutil.copytree(f, work / f.name)
    return out, work, idx, raw


def init_index(work, idx, raw, project_root=None):
    env = {"FW_CONTEXT_INDEX_DIR": str(idx)}
    root = str(project_root or work)
    # materialize CC from spec (fixture ships pack-absolute CC; regen binds paths to the work copy)
    spec = work / "compile_commands.spec.json"
    if spec.exists():
        r = sh([VENV_PY, str(PACK / "materialize_compile_commands.py"), str(work)], work, env=env)
        (raw / "materialize.log").write_text(f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
    r0 = sh([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", root, "--quick"], work, env=env)
    (raw / "init.log").write_text(f"exit: {r0.returncode}\n{r0.stdout}\n{r0.stderr}", encoding="utf-8")
    t0 = time.time()
    r = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
            "--project", root, "--no-analyze", "--no-embeddings"], work, env=env)
    (raw / "index.log").write_text(f"exit: {r.returncode}\n{r.stdout}\n{r.stderr}", encoding="utf-8")
    return r0, r, round(time.time() - t0, 2)


def db_facts(idx):
    dbs = sorted(Path(idx).glob("*/index.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not dbs:
        return {"db": None}
    con = sqlite3.connect(str(dbs[0]))
    n = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    ic = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    return {"db": str(dbs[0]), "symbols": n, "integrity": ic}


def q(client, calls):
    resp = {}
    for name, tool, args in calls:
        try:
            resp[f"{name}::{json.dumps(args, sort_keys=True)}"] = client.call_tool(tool, args, timeout=90)
        except Exception as e:
            resp[f"{name}::{json.dumps(args, sort_keys=True)}"] = {"isError": True, "exception": repr(e)}
    return resp


def record(case, status, expected, observed, notes, extra=None):
    out = OUT / case
    out.mkdir(parents=True, exist_ok=True)
    doc = {"case_id": case, "phase": "V4_ADVERSARIAL", "status": status,
           "expected": expected, "observed": observed, "notes": notes, **(extra or {})}
    (out / f"{case}.result.json").write_text(json.dumps(doc, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"{case} -> {status}", flush=True)
    return doc


# ---------- A1: shared header change ----------
def a1():
    case = CASE_NAMES["a1"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    pre = db_facts(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=case)
    client.start()
    pre_resp = q(client, [("pre_ctx", "get_symbol_context", {"name": "role_value"})])
    h = work / "common.h"
    h.write_text(h.read_text(encoding="utf-8-sig").replace("return 11;", "return 77;")
                 .replace("return 22;", "return 88;"), encoding="utf-8")
    kill_daemon(idx)
    r1i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
              "--project", str(work), "--no-analyze", "--no-embeddings"],
             work, env={"FW_CONTEXT_INDEX_DIR": str(idx)})
    (raw / "reindex.log").write_text(f"exit: {r1i.returncode}\n{r1i.stdout}\n{r1i.stderr}", encoding="utf-8")
    post = db_facts(idx)
    post_resp = q(client, [("post_ctx", "get_symbol_context", {"name": "role_value"}),
                            ("post_feat", "get_symbol_context", {"name": "feature_value"})])
    client.stop()
    txt = json.dumps(post_resp)
    # mutation only touched role_value branches (11->77 master, 22->88 slave)
    updated = "return 77;" in txt and "return 11;" not in txt
    ok = pre.get("integrity") == "ok" and post.get("integrity") == "ok" and updated \
        and post.get("symbols") == pre.get("symbols")
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="role_value context re-derives to return 77 after shared-header edit (mutation touched only role_value); counts stable; DB intact",
                  observed={"pre_symbols": pre.get("symbols"), "post_symbols": post.get("symbols"),
                             "values_updated": updated, "integrity": post.get("integrity")},
                  notes=["expected.json untouched; mutation only in disposable work copy"],
                  extra={"responses": {**pre_resp, **post_resp}})


# ---------- A2: macro/config change ----------
def a2():
    case = CASE_NAMES["a2"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    pre = db_facts(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=case)
    client.start()
    pre_resp = q(client, [("pre", "search_bodies", {"query": "role_value"})])
    cc = work / "compile_commands.json"
    doc = json.loads(cc.read_text(encoding="utf-8-sig"))
    flipped = 0
    for e in doc:
        args = e.get("arguments") or (e.get("command") or "").split()
        for i, a in enumerate(args):
            if "ROLE_MASTER" in str(a):
                args[i] = str(a).replace("ROLE_MASTER", "ROLE_SLAVE")
                flipped += 1
        e.pop("command", None)
        e["arguments"] = [str(a) for a in args]
    cc.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    kill_daemon(idx)
    r1i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(cc),
              "--project", str(work), "--no-analyze", "--no-embeddings"],
             work, env={"FW_CONTEXT_INDEX_DIR": str(idx)})
    (raw / "reindex.log").write_text(f"exit: {r1i.returncode}\n{r1i.stdout}\n{r1i.stderr}", encoding="utf-8")
    post = db_facts(idx)
    post_resp = q(client, [("post", "search_bodies", {"query": "role_value"})])
    client.stop()
    txt = json.dumps(post_resp)
    ok = ("return 22;" in txt) and ("return 11;" not in txt) and post.get("integrity") == "ok"
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="after ROLE_MASTER->ROLE_SLAVE flag flip, active bodies re-derive to the slave branch (22), master branch (11) no longer active",
                  observed={"flags_flipped": flipped, "pre_symbols": pre.get("symbols"),
                             "post_symbols": post.get("symbols"), "slave_branch_active": "return 22;" in txt,
                             "master_branch_gone": "return 11;" not in txt, "integrity": post.get("integrity")},
                  notes=["CC edited in disposable copy only; expected.json untouched"],
                  extra={"responses": {**pre_resp, **post_resp}})


# ---------- A3: build variant switch ----------
def a3():
    case = CASE_NAMES["a3"]
    out, work, idx, raw = fresh(case, G03)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    pre = db_facts(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=case)
    client.start()
    pre_resp = q(client, [("pre", "search_bodies", {"query": "selected"}),
                           ("pre_helper", "search_bodies", {"query": "helper"})])
    cc = work / "compile_commands.json"
    doc = json.loads(cc.read_text(encoding="utf-8-sig"))
    # variant switch: TU a loses -DFEATURE_A=1 -> its selected() falls to the #else branch (30)
    switched = 0
    for e in doc:
        args = e.get("arguments") or []
        if "-DFEATURE_A=1" in args:
            e["arguments"] = [a for a in args if a != "-DFEATURE_A=1"]
            switched += 1
    cc.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    idx2 = WORK_ROOT / case / "index_v2"
    idx2.mkdir(exist_ok=True)
    kill_daemon(idx)
    kill_daemon(idx2)
    r1i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(cc),
              "--project", str(work), "--no-analyze", "--no-embeddings"],
             work, env={"FW_CONTEXT_INDEX_DIR": str(idx2)})
    (raw / "reindex.log").write_text(f"exit: {r1i.returncode}\n{r1i.stdout}\n{r1i.stderr}", encoding="utf-8")
    post = db_facts(idx2)
    client2 = McpStdioClient(index_dir=idx2, cwd=str(work), log_prefix=case + "-v2")
    client2.start()
    post_resp = q(client2, [("post", "search_bodies", {"query": "selected"}),
                             ("post_helper", "search_bodies", {"query": "helper"})])
    client2.stop()
    txt = json.dumps(post_resp)
    ok = ("return 30;" in txt) and ("return 10;" not in txt) and ("return 20;" in txt) \
        and post.get("integrity") == "ok" and post.get("symbols") == pre.get("symbols")
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="variant switch (TU a loses FEATURE_A): its selected() re-derives 10->30; b stays 20; helper untouched; separate index dir",
                  observed={"tus_switched": switched, "pre_symbols": pre.get("symbols"),
                             "post_symbols": post.get("symbols"), "branch30_present": "return 30;" in txt,
                             "branch10_gone": "return 10;" not in txt, "branch20_kept": "return 20;" in txt,
                             "integrity": post.get("integrity")},
                  notes=["variant separation via separate FW_CONTEXT_INDEX_DIR (same registration root)"],
                  extra={"responses": {**pre_resp, **post_resp}})


# ---------- A4: source deletion/addition ----------
def a4():
    case = CASE_NAMES["a4"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    pre = db_facts(idx)
    cc = work / "compile_commands.json"
    doc = json.loads(cc.read_text(encoding="utf-8-sig"))
    doc = [e for e in doc if "slave" not in json.dumps(e)]
    cc.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    (work / "slave.c").unlink()
    kill_daemon(idx)
    rd = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(cc),
             "--project", str(work), "--no-analyze", "--no-embeddings"],
            work, env={"FW_CONTEXT_INDEX_DIR": str(idx)})
    (raw / "reindex_delete.log").write_text(f"exit: {rd.returncode}\n{rd.stdout}\n{rd.stderr}", encoding="utf-8")
    mid = db_facts(idx)
    (work / "master2.c").write_text('#include "common.h"\nint master2_entry(void){ return role_value() + 1; }\n',
                                    encoding="utf-8")
    # mirror the materialized entry shape exactly (clang + same include flags)
    tmpl = json.loads(json.dumps(doc[0]))
    tmpl["file"] = str(work / "master2.c")
    a = [str(work / "master2.c") if str(x).replace("\\", "/").endswith("master.c") else x
         for x in tmpl["arguments"]]
    if "-o" in a:
        a[a.index("-o") + 1] = "master2.o"
    else:
        a += ["-o", "master2.o"]
    tmpl["arguments"] = a
    if "output" in tmpl:
        tmpl["output"] = "master2.o"
    doc.append(tmpl)
    cc.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    kill_daemon(idx)
    r2i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(cc),
              "--project", str(work), "--no-analyze", "--no-embeddings"],
             work, env={"FW_CONTEXT_INDEX_DIR": str(idx)})
    (raw / "reindex_add.log").write_text(f"exit: {r2i.returncode}\n{r2i.stdout}\n{r2i.stderr}", encoding="utf-8")
    post = db_facts(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=case)
    client.start()
    resp = q(client, [("final", "lookup_symbol", {"name": "master2_entry", "exact": True}),
                       ("final2", "lookup_symbol", {"name": "slave_entry", "exact": True})])
    client.stop()
    # parse the RESULT ROWS (ignore the arguments echo): exact=True must return master2_entry itself
    def row_names(v):
        out = []
        try:
            for obj in json.JSONDecoder().raw_decode(v.get("text") or "")[0:1] or []:
                pass
        except Exception:
            pass
        dec = json.JSONDecoder()
        s = v.get("text") or ""
        i = 0
        while i < len(s):
            try:
                o, j = dec.raw_decode(s, i)
                if isinstance(o, dict) and o.get("name"):
                    out.append(o["name"])
                i = j if j > i else i + 1
            except Exception:
                i += 1
        return out
    added_rows = row_names(resp.get(list(resp)[0], {}))
    added_present = "master2_entry" in added_rows
    fallback_row = [n for n in added_rows if n != "master2_entry"]
    txt = json.dumps(resp)
    ok = post.get("integrity") == "ok" and added_present and not fallback_row \
        and mid.get("symbols", 0) < pre.get("symbols", 1) and post.get("symbols", 0) > mid.get("symbols", -1)
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="deleting slave.c removes its TU contribution (count drops); adding master2.c re-derives its symbol (exact rows, no fallback); DB consistent",
                  observed={"pre_symbols": pre.get("symbols"), "mid_symbols": mid.get("symbols"),
                             "post_symbols": post.get("symbols"), "added_present": added_present,
                             "added_rows": added_rows, "fallback_rows": fallback_row,
                             "integrity": post.get("integrity")},
                  notes=["exact=True lookup of deleted slave_entry: recorded verbatim (F-V2-002 known fallback issue may show here)"],
                  extra={"responses": resp})


# ---------- A5: stale DB / no-op reindex ----------
def a5():
    case = CASE_NAMES["a5"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    pre = db_facts(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=case)
    client.start()
    kill_daemon(idx)
    t0 = time.time()
    r1i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
              "--project", str(work), "--no-analyze", "--no-embeddings"],
             work, env={"FW_CONTEXT_INDEX_DIR": str(idx)})
    dt = round(time.time() - t0, 2)
    (raw / "reindex.log").write_text(f"exit: {r1i.returncode}\n{r1i.stdout}\n{r1i.stderr}", encoding="utf-8")
    post = db_facts(idx)
    resp = q(client, [("post", "get_active_build", {})])
    client.stop()
    stable = post.get("symbols") == pre.get("symbols") and post.get("integrity") == "ok"
    return record(case, "PASS" if stable else "FAIL_PRODUCT",
                  expected="no-op reindex on unchanged tree: symbol count identical, DB intact, build status sane",
                  observed={"pre_symbols": pre.get("symbols"), "post_symbols": post.get("symbols"),
                             "noop_reindex_s": dt, "integrity": post.get("integrity")},
                  notes=["no-op reindex idempotence probe"],
                  extra={"responses": resp})


# ---------- A6: repeated reindex x3 ----------
def a6():
    case = CASE_NAMES["a6"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    counts = [db_facts(idx)]
    for i in range(3):
        kill_daemon(idx)
        ri = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(work / "compile_commands.json"),
                 "--project", str(work), "--no-analyze", "--no-embeddings"],
                work, env={"FW_CONTEXT_INDEX_DIR": str(idx)})
        (raw / f"reindex{i+1}.log").write_text(f"exit: {ri.returncode}\n{ri.stdout}\n{ri.stderr}", encoding="utf-8")
        counts.append(db_facts(idx))
    sym = [c.get("symbols") for c in counts]
    integ = all(c.get("integrity") == "ok" for c in counts)
    ok = len(set(sym)) == 1 and integ
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="3 consecutive reindexes: symbol count constant, DB integrity ok after each",
                  observed={"symbol_counts": sym, "all_integrity_ok": integ},
                  notes=["idempotence under repeated identical reindex"])


# ---------- A7: partial/unsupported architecture query ----------
def a7():
    case = CASE_NAMES["a7"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    r0, r, t = init_index(work, idx, raw)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=case)
    client.start()
    resp = q(client, [
        ("vector", "get_vector_table", {}),
        ("cond_none", "explain_condition", {"file_path": "common.h", "line": 1}),
        ("absent", "lookup_symbol", {"name": "HardFault_Handler", "exact": True}),
        ("dead", "find_dead_code", {}),
    ])
    client.stop()
    errs = [k for k, v in resp.items() if isinstance(v, dict) and v.get("isError")]
    txt = json.dumps(resp)
    vector_honest = ("no vector table" in txt.lower()) or ("no_interrupts" in txt) or ("'[]'" in txt)
    ok = not errs and vector_honest
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="arch-specific queries on a non-MCU fixture return honest empties (no fabricated tables, no crashes)",
                  observed={"isError_calls": errs, "vector_honest": vector_honest},
                  notes=["unsupported-arch honesty probe"],
                  extra={"responses": resp})


# ---------- A8: malformed / missing optional env components ----------
def a8():
    case = CASE_NAMES["a8"]
    out, work, idx, raw = fresh(case, G01)
    kill_daemon(idx)
    cc = work / "compile_commands.json"
    cc.write_text("{ this is not json", encoding="utf-8")
    env = {"FW_CONTEXT_INDEX_DIR": str(idx)}
    r0 = sh([VENV_PY, "-m", "fw_context_mcp.cli", "init", "--project", str(work), "--quick"], work, env=env)
    r1i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(cc),
              "--project", str(work), "--no-analyze", "--no-embeddings"], work, env=env)
    (raw / "malformed_index.log").write_text(f"init_exit: {r0.returncode}\nindex_exit: {r1i.returncode}\n"
                                              f"{r1i.stdout}\n{r1i.stderr}", encoding="utf-8")
    malformed_honest = r1i.returncode != 0 and ("error" in (r1i.stderr + r1i.stdout).lower())
    cc.unlink()
    r2i = sh([VENV_PY, "-m", "fw_context_mcp.cli", "index", str(cc),
              "--project", str(work), "--no-analyze", "--no-embeddings"], work, env=env)
    (raw / "missing_index.log").write_text(f"exit: {r2i.returncode}\n{r2i.stdout}\n{r2i.stderr}", encoding="utf-8")
    missing_honest = r2i.returncode != 0
    facts = db_facts(idx)
    ok = malformed_honest and missing_honest
    return record(case, "PASS" if ok else "FAIL_PRODUCT",
                  expected="malformed CC and missing CC both fail with clear non-zero errors; no fabricated success",
                  observed={"malformed_honest": malformed_honest, "missing_honest": missing_honest,
                             "db_after": facts.get("integrity", "no-db")},
                  notes=["malformed/missing env honesty probe"],
                  extra={"db_facts": facts})


CASES = [a1, a2, a3, a4, a5, a6, a7, a8]


def main():
    only = [o.upper() for o in sys.argv[1:]]
    for fn in CASES:
        key = fn.__name__[:2].upper()
        if only and key not in only:
            continue
        try:
            fn()
        except Exception as e:
            record(CASE_NAMES[fn.__name__], "FAIL_PRODUCT",
                   expected=None, observed={"runner_exception": repr(e)},
                   notes=["runner exception — rerun before classification"])


if __name__ == "__main__":
    main()
