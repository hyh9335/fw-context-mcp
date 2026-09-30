import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/C1/responses_c1b.json", encoding="utf-8"))
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    if k.startswith("lookup_symbol") and "VECTOR_TABLE" in k:
        print("== lookup VECTOR_TABLE:", " ".join(t.split())[:400])
    elif k.startswith("search_content") and "VECTOR_TABLE" in k:
        print("== search_content VECTOR_TABLE:")
        print(" ".join(t.split())[:600])
    elif k.startswith("list_conditional"):
        # summarize taken states
        rows = []
        dec = json.JSONDecoder()
        s = t.strip()
        i = 0
        while i < len(s):
            s2 = s[i:].lstrip()
            if not s2:
                break
            off = len(s[i:]) - len(s2)
            try:
                obj, end = dec.raw_decode(s2)
                for r in obj.get("regions", []):
                    states = ["%d:%s=%d" % (b["line"], b["kind"], b["taken"])
                              for b in r.get("branches", [])]
                    rows.append(f"{r['file'].split('/')[-1]}@{r['root_line']} "
                                f"{r['condition'][:40]} [{';'.join(states)}]")
                i = i + off + end
            except ValueError:
                break
        print("== conditional regions:")
        for r in rows:
            print("  ", r)
