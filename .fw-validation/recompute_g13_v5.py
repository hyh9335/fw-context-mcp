"""G13 definitive comparison v5 — uses v4 phrase probes for values."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\github\fw-context-mcp\.fw-validation")

BASE = Path.home() / "fwctx-validation-work" / "V1" / "G13"
OUT = Path(r"D:\github\fw-context-mcp\.fw-validation\artifacts\V1\G13\raw")
SIZES = [1, 10, 50, 100, 250]


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


def count_name(d, tool, needle, pred):
    return len([o for o in objs(find_text(d, tool, needle))
                if isinstance(o, dict) and pred(o)])


def ab(d):
    o = objs(find_text(d, "get_active_build", ""))
    return o[0] if o else {}


def phrase_files(d, phrase):
    rows = [o for o in objs(find_text(d, "search_content", phrase))
            if isinstance(o, dict) and o.get("file")]
    names = {Path(o["file"]).name for o in rows}
    return {"present": "shared.h" in names, "rows": len(rows), "names": sorted(names)[:5]}


def main():
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
        p7 = phrase_files(vp, "return 7")
        p8 = phrase_files(vp, "return 8")
        inc8 = phrase_files(vinc, "return 8")
        inc7 = phrase_files(vinc, "return 7")
        mut8 = phrase_files(vmut, "return 8")
        mut7 = phrase_files(vmut, "return 7")
        sv = lambda d: count_name(d, "lookup_symbol", '"shared_value"',
                                   lambda o: o.get("name") == "shared_value")
        ff = lambda d: count_name(d, "lookup_symbol", '"f_"',
                                  lambda o: str(o.get("name", "")).startswith("f_"))
        checks = {
            "fanout_rows_capped": sv(p) == cap,
            "file_count_2n_uncapped": ab_p.get("file_count") == 2 * n,
            "pristine_value_7": p7["present"] and not p8["present"],
            "tu_symbols_capped": ff(p) == cap,
            "incremental_rows": sv(inc) == cap,
            "incremental_index_converged": ab_inc.get("modified_files_count") == 0,
            "incremental_value_8": inc8["present"],
            "incremental_old_value_gone": not inc7["present"],
            "incremental_equals_clean_full":
                sv(inc) == sv(mut) and ff(inc) == ff(mut)
                and ab_inc.get("file_count") == ab_mut.get("file_count")
                and ab_inc.get("symbol_count") == ab_mut.get("symbol_count")
                and inc8["present"] == mut8["present"] and not inc7["present"]
                and not mut7["present"]
                and ab_inc.get("modified_files_count") == 0
                and ab_mut.get("modified_files_count") == 0,
        }
        checks["all_match"] = all(checks.values())
        per_size[n] = {"rows_p": sv(p), "rows_inc": sv(inc), "rows_mut": sv(mut),
                       "f_p": ff(p), "f_inc": ff(inc), "f_mut": ff(mut),
                       "file_count_p": ab_p.get("file_count"),
                       "file_count_inc": ab_inc.get("file_count"),
                       "file_count_mut": ab_mut.get("file_count"),
                       "symbol_count_inc": ab_inc.get("symbol_count"),
                       "symbol_count_mut": ab_mut.get("symbol_count"),
                       "modified_files_inc": ab_inc.get("modified_files_count"),
                       "modified_files_mut": ab_mut.get("modified_files_count"),
                       "p7": p7["present"], "p8": p8["present"],
                       "inc8": inc8["present"], "inc7": inc7["present"],
                       "mut8": mut8["present"], "mut7": mut7["present"],
                       "checks": checks}
        all_ok = all_ok and checks["all_match"]
    status = "PASS" if all_ok else "FAIL_PRODUCT"
    result = {"case_id": "G13", "platform": "windows",
              "command": {"generate": "generate.py <work> <n>",
                          "index": "index <work>/compile_commands.json --project <work>",
                          "sizes": SIZES},
              "exit_code": 0,
              "expected": {"fanout": "n", "shared_header_contexts": "n",
                           "mutation_affected_contexts": "n",
                           "invariant": "incremental_after_shared_header_mutation == clean_full"},
              "observed": per_size,
              "comparison": {"all_sizes_match": all_ok},
              "status": status,
              "notes": [
                  "definitive comparison v5: daemon-disciplined requeries; value "
                  "evidence via exact-phrase search_content ('return 7'/'return 8')",
                  "row counts compared against min(n,100): lookup_symbol caps at "
                  "limit=100 with no pagination (F-V1-008); file_count==2n is the "
                  "uncapped per-context evidence (n TU files + n shared.h contexts)",
                  "function-body value checks via search_bodies are impossible on "
                  "this fixture: every function is a single-line definition and "
                  "single-line definitions store empty symbols.source (F-V1-007)",
                  "incremental convergence cross-checked via modified_files_count==0 "
                  "on both index_inc (incremental) and index_mut (clean full)"
              ]}
    (OUT.parent / "G13.result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({str(n): per_size[n] for n in SIZES}, indent=1))
    print("STATUS:", status)


if __name__ == "__main__":
    main()
