"""Recompute G13 checks offline from saved responses_*.json.

The runner stored MCP keys with sort_keys=True while _rows_for expects
{"name": ...} first; this script matches keys by (tool + substring) so the
order does not matter. Overwrites G13.result.json with corrected checks.
"""
import json
import io
import re
from pathlib import Path

RAW = Path(r".fw-validation/artifacts/V1/G13/raw")
OUT = Path(r".fw-validation/artifacts/V1/G13/G13.result.json")
SIZES = [1, 10, 50, 100, 250]


def find_text(d, tool, needle):
    for k, v in d.items():
        if k.startswith(f"{tool}::") and needle in k and not k.endswith("::ERROR"):
            return v.get("text") or ""
    return ""


def json_objects(text):
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


def stage(tag):
    p = RAW / f"responses_{tag}.json"
    if not p.exists():
        return None
    d = json.load(io.open(p, encoding="utf-8"))
    sv = [o for o in json_objects(find_text(d, "lookup_symbol", '"shared_value"'))
          if isinstance(o, dict) and o.get("name") == "shared_value"]
    bodies = [o.get("source", "") for o in json_objects(find_text(d, "search_bodies", "shared_value"))
              if isinstance(o, dict) and "shared_value" in (o.get("source") or "")]
    f_rows = [o for o in json_objects(find_text(d, "lookup_symbol", '"f_"'))
              if isinstance(o, dict) and str(o.get("name", "")).startswith("f_")]
    ab = (json_objects(find_text(d, "get_active_build", "")) or [{}])[0]
    values = sorted({("return 8" if "return 8" in b else
                      ("return 7" if "return 7" in b else "?")) for b in bodies})
    return {"shared_value_rows": len(sv), "shared_value_bodies": len(bodies),
            "shared_value_body_values": values, "f_rows": len(f_rows),
            "file_count": ab.get("file_count"),
            "index_elapsed_s": None}


def main():
    per_size, all_ok = {}, True
    for n in SIZES:
        gen_p = RAW / f"generate_{n}.log"
        if not gen_p.exists():
            per_size[n] = {"error": "missing artifacts"}
            all_ok = False
            continue
        gen_txt = gen_p.read_text(encoding="utf-8", errors="replace")
        if not gen_txt.startswith("exit: 0"):
            per_size[n] = {"error": "generate failed"}
            all_ok = False
            continue
        # generated expected.json lives in the (possibly mutated) work tree;
        # regenerate its content deterministically instead of reading work dir
        expected = {"fanout": n, "translation_units": n, "shared_header_contexts": n,
                    "mutation_affected_contexts": n,
                    "invariant": "incremental_after_shared_header_mutation == clean_full"}
        clean = stage(f"clean{n}")
        pre = stage(f"pre{n}")
        post = stage(f"post{n}")
        mutclean = stage(f"mutclean{n}")
        if not all([clean, pre, post, mutclean]):
            per_size[n] = {"error": "incomplete stages", "have": {"clean": bool(clean),
                            "pre": bool(pre), "post": bool(post), "mutclean": bool(mutclean)}}
            all_ok = False
            continue
        checks = {
            "fanout_contexts": clean["shared_value_rows"] == expected["shared_header_contexts"] == n,
            "bodies_per_context": clean["shared_value_bodies"] == n,
            "pristine_value_7": clean["shared_value_body_values"] == ["return 7"],
            "tu_symbols": clean["f_rows"] == n,
            "incremental_all_updated": post["shared_value_body_values"] == ["return 8"]
            and post["shared_value_rows"] == n,
            "incremental_equals_clean_full":
                post["shared_value_body_values"] == mutclean["shared_value_body_values"]
                and post["shared_value_rows"] == mutclean["shared_value_rows"]
                and post["f_rows"] == mutclean["f_rows"],
        }
        checks["all_match"] = all(checks.values())
        per_size[n] = {"expected": expected, "clean": clean, "pre": pre,
                       "post_incremental": post, "mutated_clean": mutclean,
                       "checks": checks}
        all_ok = all_ok and checks["all_match"]
    status = "PASS" if all_ok else "FAIL_PRODUCT"
    result = {"case_id": "G13", "platform": "windows",
              "command": {"generate": "generate.py <work> <n>",
                          "index": "index <work>/compile_commands.json --project <work>",
                          "sizes": SIZES},
              "exit_code": 0,
              "expected": {"invariant": "incremental == clean_full"},
              "observed": per_size,
              "comparison": {"all_sizes_match": all_ok},
              "status": status,
              "notes": ["shared.h mutation 7->8 between pre and post",
                        "comparison recomputed offline from saved responses_*.json: the runner "
                        "stored MCP keys with sort_keys=True while _rows_for expects "
                        '{"name": ...} first; key matching here is order-insensitive',
                        "per-stage index logs: raw/index_<stage><n>.log"]}
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({str(n): per_size[n].get("checks", per_size[n]) for n in SIZES}, indent=1))
    print("STATUS:", status)


if __name__ == "__main__":
    main()
