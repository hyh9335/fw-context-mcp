from pathlib import Path
import json, sys

root = Path(sys.argv[1]).resolve()
n = int(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
(root/"shared.h").write_text("#pragma once\nstatic inline int shared_value(void){return 7;}\n", encoding="utf-8")
cc = []
for i in range(n):
    src = root/f"tu_{i:03d}.c"
    src.write_text(f'#include "shared.h"\nint f_{i:03d}(void){{return shared_value()+{i};}}\n', encoding="utf-8")
    cc.append({
        "directory":str(root),
        "file":str(src),
        "arguments":["clang",f"-I{root}","-c",str(src),"-o",str(root/f"tu_{i:03d}.o")]
    })
(root/"compile_commands.json").write_text(json.dumps(cc, indent=2)+"\n", encoding="utf-8")
(root/"expected.json").write_text(json.dumps({
    "fanout":n,
    "translation_units":n,
    "shared_header_contexts":n,
    "mutation_affected_contexts":n,
    "invariant":"incremental_after_shared_header_mutation == clean_full"
}, indent=2)+"\n", encoding="utf-8")
print(root)
