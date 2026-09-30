import json
import io

d = json.load(io.open(r".fw-validation/artifacts/V2/N2/responses_n2.json", encoding="utf-8"))
facts = {}
for k, v in d.items():
    if not isinstance(v, dict):
        continue
    t = v.get("text") or ""
    if k.endswith("get_active_build"):
        o, _ = json.JSONDecoder().raw_decode(t.strip())
        facts[k] = {"config_hash": o.get("config_hash", "")[:16], "symbols": o.get("symbol_count"),
                    "files": o.get("file_count"), "indexed_at": o.get("indexed_at")}
    elif k.endswith("find_hotspots"):
        i = 0; names = []
        while i < len(t):
            s2 = t[i:].lstrip()
            if not s2: break
            off = len(t[i:]) - len(s2)
            try:
                o, end = json.JSONDecoder().raw_decode(s2)
                if isinstance(o, dict) and "name" in o:
                    names.append(f"{o['name']}({o.get('caller_count')})")
                i = i + off + end
            except ValueError:
                break
        facts[k] = names[:4]
    elif "ERROR" in k:
        facts[k] = v
print(json.dumps(facts, indent=1))
# config diff from snapshots
import subprocess
n1cfg = io.open(r".fw-validation/artifacts/V2/N2/n1_nsh_dotconfig.txt", encoding="utf-8", errors="replace").read().splitlines()
n2cfg = io.open(r".fw-validation/artifacts/V2/N2/n2_smp_dotconfig.txt", encoding="utf-8", errors="replace").read().splitlines()
c1 = dict(l.split("=", 1) for l in n1cfg if l.startswith("CONFIG_") and "=" in l)
c2 = dict(l.split("=", 1) for l in n2cfg if l.startswith("CONFIG_") and "=" in l)
diff = {k: (c1.get(k), c2.get(k)) for k in set(c1) | set(c2) if c1.get(k) != c2.get(k)}
print("config keys diff count:", len(diff))
for k in sorted(diff)[:12]:
    print("  ", k, diff[k])
json.dump({"facts": facts, "config_diff_count": len(diff),
           "config_diff_sample": dict(list(sorted(diff.items()))[:40])},
          open(r".fw-validation/artifacts/V2/N2/n2_variant_facts.json", "w"), indent=1)
