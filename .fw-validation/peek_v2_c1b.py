import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/C1/responses_c1b.json", encoding="utf-8"))
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    if k.startswith("list_conditional_regions"):
        print("== conditional regions")
        print(" ".join(t.split())[:500])
    elif k.startswith("lookup_symbol"):
        print(f"== {k[:60]}")
        print(" ".join(t.split())[:300])
    elif k.startswith("search_bodies") or k.startswith("search_content"):
        print(f"== {k[:60]}")
        print(" ".join(t.split())[:300])
    elif k.startswith("read_file"):
        lines = t.splitlines()
        hits = [(i, ln) for i, ln in enumerate(lines)
                if "VECTOR_TABLE" in ln or "Reset_Handler," in ln
                or "NMI_Handler" in ln or "INITIAL_SP" in ln]
        print("== read_file vector-relevant lines:")
        for i, ln in hits[:15]:
            print("  ", ln.strip()[:110])
