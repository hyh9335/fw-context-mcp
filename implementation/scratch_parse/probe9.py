import os
from clang import cindex
WS = r"D:\github\fw-context-mcp\implementation\scratch_parse"
os.chdir(WS)
idx = cindex.Index.create()
for name, opts in [("no options", 0), ("detailed", cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD), ("none-flag", cindex.TranslationUnit.PARSE_NONE)]:
    try:
        idx.parse(WS + r"\poller.c", args=["-Ifixture", "-DPOLL_INTERVAL=250"], options=opts)
        print(name, "-> OK")
    except Exception as e:
        print(name, "-> FAIL", type(e).__name__)