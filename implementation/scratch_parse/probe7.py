import os
from clang import cindex
WS = r"D:\github\fw-context-mcp\implementation\scratch_parse"
os.chdir(WS)
for label, dll in [("native-wheel", r"C:\Users\h00969597\AppData\Local\Programs\Python\Python311\Lib\site-packages\clang\native\libclang.dll"), ("llvm-pf", r"C:\Program Files\LLVM\bin\libclang.dll")]:
    cindex.Config.set_library_file(dll)
    idx = cindex.Index.create()
    for name, f in [("abs", WS + r"\poller.c"), ("rel", "fixture/poller.c")]:
        try:
            idx.parse(f, args=["-Ifixture", "-DPOLL_INTERVAL=250"], options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
            print(label, name, "-> OK")
        except Exception as e:
            print(label, name, "-> FAIL", type(e).__name__)