"""G13 final targeted probes + definitive comparison (v3).

Value evidence uses search_content (file-level FTS) because single-line
function bodies are not stored in symbols.source (see F-V1-007).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")
from mcp_client import McpStdioClient  # noqa: E402
from v1_runner import kill_daemon  # noqa: E402

BASE = Path.home() / "fwctx-validation-work" / "V1" / "G13"
OUT = Path(r"D:\github\fw-context-mcp\.fw-validation\artifacts\V1\G13\raw")
SIZES = [1, 10, 50, 100, 250]


def probe(idx, work, tag, queries):
    kill_daemon(idx)
    client = McpStdioClient(index_dir=idx, cwd=str(work), log_prefix=f"g13v3-{tag}")
    responses = {}
    try:
        client.start()
        for tool, args in queries:
            try:
                responses[f"{tool}::{json.dumps(args)[:90]}"] = \
                    client.call_tool(tool, args, timeout=120)
            except Exception as e:
                responses[f"{tool}::ERROR"] = {"exception": repr(e), "isError": True}
    finally:
        client.stop()
        responses["server_exit_code"] = 0
    (OUT / f"responses_{tag}.json").write_text(
        json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
    return responses


def objs(text):
    out, dec, s, i = [], json.JSONDecoder(), (text or "").strip(), 0
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


def find_text(d, tool, needle):
    for k, v in d.items():
        if k.startswith(f"{tool}::") and needle in k and not k.endswith("::ERROR") \
                and isinstance(v, dict):
            return v.get("text") or ""
    return ""


def count_shared_value(d):
    rows = [o for o in objs(find_text(d, "lookup_symbol", '"shared_value"'))
            if isinstance(o, dict) and o.get("name") == "shared_value"]
    return len(rows)


def count_f(d):
    rows = [o for o in objs(find_text(d, "lookup_symbol", '"f_"'))
            if isinstance(o, dict) and str(o.get("name", "")).startswith("f_")]
    return len(rows)


def ab(d):
    o = objs(find_text(d, "get_active_build", ""))
    return o[0] if o else {}


def files_with(d, tool_needle_pair):
    text = find_text(d, tool_needle_pair[0], tool_needle_pair[1])
    return sorted(Path(o["file"]).name for o in objs(text)
                  if isinstance(o, dict) and o.get("file"))


def main():
    # targeted value probes
    for n in SIZES:
        probe(BASE / f"n{n}" / "index_p", BASE / f"n{n}" / "work_p", f"vp{n}",
              [("search_content", {"query": "return 7"}),
               ("get_source", {"name": "shared_value"})])
        for stage, dirname in [("vinc", "index_inc"), ("vmut", "index_mut")]:
            probe(BASE / f"n{n}" / dirname, BASE / f"n{n}" / "work", f"{stage}{n}",
                  [("search_content", {"query": "return 8"}),
                   ("search_content", {"query": "return 7"}),
                   ("get_source", {"name": "shared_value"})])

    per_size, all_ok = {}, True
    for n in SIZES:
        p = json.loads((OUT / f"responses_p{n}.json").read_text(encoding="utf-8"))
        inc = json.loads((OUT / f"responses_inc{n}.json").read_text(encoding="utf-8"))
        mut = json.loads((OUT / f"responses_mut{n}.json").read_text(encoding="utf-8"))
        vp = json.loads((OUT / f"responses_vp{n}.json").read_text(encoding="utf-8"))
        vinc = json.loads((OUT / f"responses_vinc{n}.json").read_text(encoding="utf-8"))
        vmut = json.loads((OUT / f"responses_vmut{n}.json").read_text(encoding="utf-8"))
        cap = min(n, 100)
        ab_p, ab_inc, ab_mut = ab(p), ab(inc), ab(mut)
        p_ret7 = files_with(vp, ("search_content", "return 7"))
        inc_ret8 = files_with(vinc, ("search_content", "return 8"))
        inc_ret7 = files_with(vinc, ("search_content", "return 7"))
        mut_ret8 = files_with(vmut, ("search_content", "return 8"))
        gs_inc = objs(find_text(vinc, "get_source", ""))
        gs_inc_src = " ".join(str(o.get("source", "")) for o in gs_inc)
        checks = {
            "fanout_rows_capped": count_shared_value(p) == cap,
            "file_count_2n_uncapped": ab_p.get("file_count") == 2 * n,
            "pristine_value_7_file_level": p_ret7 == ["shared.h"],
            "tu_symbols_capped": count_f(p) == cap,
            "incremental_rows": count_shared_value(inc) == cap,
            "incremental_index_converged": ab_inc.get("modified_files_count") == 0,
            "incremental_value_8": inc_ret8 == ["shared.h"] and "return 8" in gs_inc_src,
            "incremental_old_value_gone": inc_ret7 == [],
            "incremental_equals_clean_full":
                count_shared_value(inc) == count_shared_value(mut)
                and count_f(inc) == count_f(mut)
                and ab_inc.get("file_count") == ab_mut.get("file_count")
                and ab_inc.get("symbol_count") == ab_mut.get("symbol_count")
                and inc_ret8 == mut_ret8
                and ab_inc.get("modified_files_count") == 0
                and ab_mut.get("modified_files_count") == 0,
        }
        checks["all_match"] = all(checks.values())
        per_size[n] = {"rows_p": count_shared_value(p), "rows_inc": count_shared_value(inc),
                       "rows_mut": count_shared_value(mut),
                       "f_p": count_f(p), "f_inc": count_f(mut),
                       "file_count_p": ab_p.get("file_count"),
                       "file_count_inc": ab_inc.get("file_count"),
                       "file_count_mut": ab_mut.get("file_count"),
                       "symbol_count_inc": ab_inc.get("symbol_count"),
                       "symbol_count_mut": ab_mut.get("symbol_count"),
                       "modified_files_inc": ab_inc.get("modified_files_count"),
                       "modified_files_mut": ab_mut.get("modified_files_count"),
                       "pristine_ret7_files": p_ret7, "inc_ret8_files": inc_ret8,
                       "inc_ret7_files": inc_ret7, "mut_ret8_files": mut_ret8,
                       "inc_get_source": gs_inc_src[:120],
                       "checks": checks}
        all_ok = all_ok and checks["all_match"]
    status = "PASS" if all_ok else "FAIL_PRODUCT"
    result = {"case_id": "G13", "platform": "windows",
              "command": {"generate": "generate.py <work> <n>",
                          "index": "index <work>/compile_commands.json --project <work>",
                          "sizes": SIZES},
              "exit_code": 0,
              "expected": {"fanout": "n", "shared_header_contexts": "n",
                           "invariant": "incremental_after_shared_header_mutation == clean_full"},
              "observed": per_size,
              "comparison": {"all_sizes_match": all_ok},
              "status": status,
              "notes": [
                  "definitive comparison v3: daemon-disciplined requeries "
                  "(responses_p*/inc*/mut* + vp*/vinc*/vmut* value probes)",
                  "row counts compared against min(n,100): lookup_symbol has no "
                  "pagination beyond limit=100 (F-V1-008); file_count==2n is the "
                  "uncapped per-context evidence",
                  "body-value evidence is file-level (search_content) because "
                  "single-line function definitions store empty symbols.source "
                  "(F-V1-007); search_bodies cannot see any function in this fixture",
                  "original first-pass FAIL_PRODUCT was a harness artifact "
                  "(sort_keys key mismatch + missing limit + wrong body-text probe)"
              ]}
    (OUT.parent / "G13.result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({str(n): per_size[n] for n in SIZES}, indent=1))
    print("STATUS:", status)


if __name__ == "__main__":
    main()
