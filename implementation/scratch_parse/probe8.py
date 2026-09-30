import os, sys
from clang import cindex
cindex.Config.set_library_file(r"C:\Program Files\LLVM\bin\libclang.dll")
WS = r"D:\github\fw-context-mcp\implementation\scratch_parse"
os.chdir(WS)
idx = cindex.Index.create()
for name, f in [("abs", WS + r"\poller.c"), ("rel", "fixture/poller.c")]:
    try:
        tu = idx.parse(f, args=["-Ifixture", "-DPOLL_INTERVAL=250"], options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
        n = sum(1 for _ in tu.cursor.walk_preorder())
        print("llvm-pf", name, "-> OK", n, "cursors")
    except Exception as e:
        print("llvm-pf", name, "-> FAIL", type(e).__name__)