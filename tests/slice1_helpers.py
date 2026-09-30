"""slice1_helpers — fixture builders for the Slice-1 certification/publication
tests. Every fixture goes through the REAL production capture path:
compile_commands.json -> compile_commands.parse -> extract_all -> certify."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from fw_context_mcp.indexer.compile_commands import parse as parse_cc  # noqa: E402
from fw_context_mcp.indexer.symbols import extract_all  # noqa: E402
from fw_context_mcp.store_v2.certify import build_certificate  # noqa: E402
from fw_context_mcp.store_v2.publish import (  # noqa: E402
    open_v2_store_for_write,
    store_nonce,
)

CLANG_TARGET = "--target=x86_64-pc-windows-msvc"


def make_project(tmp_path: Path, *, feature: bool = True,
                 util_version: int = 2, transitive_extra: str = "",
                 sysroot: Path | None = None, rsp: dict | None = None) -> dict:
    """A small real C project: main.c + inc/{util,cfg}.h (+ transitive)."""
    proj = tmp_path / "proj"
    (proj / "inc").mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / "inc" / "util.h").write_text(
        f"#define UTIL_VERSION {util_version}\nint util(void);\n")
    (proj / "inc" / "cfg.h").write_text(
        "#ifndef CFG_H\n#define CFG_H\n"
        + ("#define CFG_FEATURE 1\n" if feature else "")
        + transitive_extra + "#endif\n")
    (proj / "inc" / "trans.h").write_text("#define TRANSITIVE_MARK 1\n")
    (proj / "forced.h").write_text("#define FORCED_MARK 7\n")
    (proj / "macros.h").write_text("#define IMACROS_MARK 9\n")
    main_src = ('#include "util.h"\n#include "cfg.h"\n#include "trans.h"\n'
                "int util(void) { return UTIL_VERSION; }\n"
                "int main(void) { return util(); }\n")
    if sysroot is not None:
        sysroot.mkdir(parents=True, exist_ok=True)
        (sysroot / "extdecl.h").write_text("int ext_fn(void);\n")
        main_src = '#include <extdecl.h>\n' + main_src
    (proj / "src" / "main.c").write_text(main_src)
    argv = ["clang", "-c", "src/main.c", "-Iinc", CLANG_TARGET,
            "-DFEATURE=1", "-include", "forced.h"]
    if sysroot is not None:
        argv += ["-isystem", str(sysroot)]
    if rsp is not None:
        argv += ["@flags.rsp"]
    entry = {"directory": str(proj), "arguments": argv,
             "file": str(proj / "src" / "main.c")}
    (proj / "compile_commands.json").write_text(json.dumps([entry]))
    if rsp is not None:
        for name, tokens in rsp.items():
            (proj / name).write_text(" ".join(tokens))
    return {"proj": proj, "argv": argv}


def build_units(proj: Path):
    units = list(parse_cc(proj / "compile_commands.json"))
    assert units, "compile_commands.json yielded no units"
    return units


def parse_unit(unit):
    return extract_all(unit, with_refs=True, return_tu=True)


def certify_project(tmp_path: Path, proj: Path, *, nonce: str = "n1",
                    context_id: str = "cx") -> tuple:
    """Production path: cc-parse -> extract_all -> build_certificate."""
    unit = build_units(proj)[0]
    parsed = parse_unit(unit)
    cert = build_certificate(unit, parsed, proj, nonce=nonce,
                             context_id=context_id)
    return unit, parsed, cert


def fresh_store(tmp_path: Path, name: str = "v2"):
    store = open_v2_store_for_write(tmp_path / name / "pid" / "index-v2.db")
    return store


@pytest.fixture()
def store(tmp_path):
    s = fresh_store(tmp_path)
    yield s
    s.close()


@pytest.fixture()
def cert_store(tmp_path):
    """Store + certified fixture certificate + its parse outputs."""
    data = make_project(tmp_path)
    store = fresh_store(tmp_path)
    nonce = store_nonce(store)
    unit, parsed, cert = certify_project(tmp_path, data["proj"], nonce=nonce)
    yield {"store": store, "cert": cert, "parsed": parsed, "unit": unit,
           "proj": data["proj"], "nonce": nonce, "data": data}
    store.close()
