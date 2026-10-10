# M7.0A Toolchain Audit

**Generated:** 2026-01-20
**Method:** Direct `--version` invocation on each tool

---

## Available Tools

### Python
- **Version:** Python 3.11.9
- **Path:** `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\python.exe`
- **Status:** Available (primary scripting language)

### Git
- **Version:** git version 2.50.1.windows.1
- **Path:** on PATH
- **Status:** Available (corpus checkout + version control)

### Clang (system LLVM install)
- **Version:** clang version 22.1.8 (https://github.com/llvm/llvm-project ca7933e47d3a3451d81e72ac174dcb5aa28b59d1)
- **Target:** x86_64-pc-windows-msvc
- **Path:** `C:\Program Files\LLVM\bin\clang.exe`
- **Status:** Available but NOT on PATH — must use full path

### Clang++
- **Version:** clang version 22.1.8 (same build as clang)
- **Path:** `C:\Program Files\LLVM\bin\clang++.exe`
- **Status:** Available but NOT on PATH

### libclang (Python binding)
- **Package:** `clang` (pip package, bundles libclang.dll)
- **DLL location 1:** `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\Lib\site-packages\clang\native\libclang.dll`
- **DLL location 2:** `C:\Program Files\LLVM\bin\libclang.dll` (system LLVM)
- **Status:** Available — this is what M6.5 used for semantic extraction

### fw-context CLI
- **Version:** 0.31.0
- **Path:** `C:\Users\h00969597\AppData\Local\Programs\Python\Python311\Scripts\fw-context.exe`
- **Status:** Available (editable install from `D:\github\fw-context-mcp`)

---

## NOT Available

### CMake
- **Status:** NOT installed
- **Impact:** Cannot use `CMAKE_EXPORT_COMPILE_COMMANDS=ON` as §9 suggests
- **Mitigation:** Generate compile_commands.json manually (like M6.5 synthetic corpora do) — this is a valid JSON file format that fw-context consumes

### Ninja
- **Status:** NOT installed
- **Impact:** Cannot use Ninja generator
- **Mitigation:** Not needed — we generate compile_commands.json directly

### GCC
- **Status:** NOT installed
- **Impact:** None — clang is the preferred compiler for fw-context semantic extraction

### Make
- **Status:** NOT installed
- **Impact:** None — not needed for compile_commands.json generation

---

## Toolchain Decision

The M7.0A baseline will use:
1. **clang 22.1.8** at `C:\Program Files\LLVM\bin\clang.exe` as the compiler
2. **Python `clang.cindex`** binding for semantic extraction (same as M6.5)
3. **Manual compile_commands.json generation** (no CMake needed)
4. **fw-context CLI** for v2 baseline indexing

This matches the M6.5 toolchain assumptions (target `x86_64-pc-windows-msvc`) and adds the compile_commands.json layer that M7.0A §10 requires.

---

## libclang Version Compatibility Note

M6.5 used the pip `clang` package's bundled libclang.dll. The system LLVM 22.1.8 is a newer version. The `full_build_gt.py` GT extraction uses the Python binding, which will use whichever libclang.dll it finds first. For reproducibility, the M7.0A baseline should use the same libclang that M6.5 used (the pip package's bundled version) for GT generation, and the system clang for compile_commands.json compilation verification.
