from pathlib import Path
import json, shlex, sys

root = Path(sys.argv[1]).resolve()
spec = json.loads((root / "compile_commands.spec.json").read_text(encoding="utf-8"))
out = []
for e in spec:
    file = (root / e["file"]).resolve()
    directory = (root / e.get("directory", ".")).resolve()
    args = [x.replace("${ROOT}", str(root)) for x in e["arguments"]]
    out.append({
        "directory": str(directory),
        "file": str(file),
        "arguments": args,
        **({"output": e["output"]} if e.get("output") else {})
    })
(root / "compile_commands.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
print(root / "compile_commands.json")
