import os
from clang import cindex
cx = cindex
idx = cindex.Index.create()
WS = r"D:\github\fw-context-mcp\implementation\scratch_parse"
os.chdir(WS)
tests = {
  "rel file": ("fixture/poller.c", ["-Ifixture", "-DPOLL_INTERVAL=250"]),
  "abs file D:": (WS + r"\poller.c", ["-Ifixture", "-DPOLL_INTERVAL=250"]),
  "abs file D: fwd": (WS.replace("\\","/") + "/fixture/../poller.c", ["-Ifixture", "-DPOLL_INTERVAL=250"]),
}
for name, (f, args) in tests.items():
    try:
        idx.parse(f, args=args, options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
        print(name, "-> OK")
    except Exception as e:
        print(name, "-> FAIL:", type(e).__name__)