from clang import cindex
import sys
cx = cindex
idx = cindex.Index.create()
tests = {
  "with -o": ["-Ifixture", "-DPOLL_INTERVAL=250", "-o", "poller.o"],
  "without -o": ["-Ifixture", "-DPOLL_INTERVAL=250"],
  "with clang argv0": ["clang", "poller.c", "-Ifixture", "-DPOLL_INTERVAL=250", "-o", "poller.o"],
}
for name, args in tests.items():
    try:
        tu = idx.parse("fixture/poller.c", args=args, options=cx.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
        print(name, "-> OK")
    except Exception as e:
        print(name, "-> FAIL:", type(e).__name__, e)