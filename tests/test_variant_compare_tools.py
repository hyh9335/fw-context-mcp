"""W6 RED/GREEN oracle — variant compare/group MCP surface (AUD-R5).

Ledger issue AUD-R5 (W0 verdict: "gap confirmed, 0 hits"): the strings
``compare_variants`` / ``semantic_diff`` appear NOWHERE in src/ — the only
variant surface is the DB-layer ``group_equivalent_variants``
(``indexer/db/_variants.py``), which is exported by ``indexer.db`` but
exposed through NO MCP tool.  The accepted design
(``artifacts/arch/accepted_design_W6.md``) chose the BEHAVIOR fix: add the
two tools to the wire — it does NOT flip docs against behavior.

Fixture: ``tests/fixtures/variants_ab/variant.c`` — one TU, three variants
of one project: A=UART (no defines), B=USB (``-DIMPL_B=1``),
C=semantically A plus an unrelated define (``-DUNRELATED=123``) — the
T14 SLICE-E shape, per tests/specs/W6.md.

RED cases (each fails at the integration HEAD bdc282a for one reason):
- W6a_registry — the wire registry holds 40 tools and neither new name
  (the literal AUD-R5 absence).
- W6b_ab_delta / W6c_ac_no_delta / W6d_group / W6e_file_mode /
  W6f_multi_image — every wire call dies as "unknown tool" pre-fix.

HONEST fingerprint semantics (design RED plan (b) branch): the body
fingerprint hashes the stored IFDEF-FILTERED body per ``config_hash``, so
A and B store genuinely different bodies and the fingerprint is NOT equal
for A/B — the test pins that actual semantics instead of fabricating an
equivalence.  A/C (same observed behavior, unrelated define) fingerprint
equal — the equivalence the fingerprint DOES support.

Wire access follows the post-W2 convention: an in-memory
``ClientSession`` against the module-level FastMCP instance (same stack
as stdio — ``test_mcp_surface_slice_n.py``).  Isolation: every DB lives
under ``tmp_path_factory`` dirs; the repo-root ``.fw-context`` is never
touched.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

import fw_context_mcp

# ── Worktree pinning (anti-drift, same as test_mcp_surface_slice_n.py) ───────
_WT_SRC = Path(__file__).resolve().parents[1] / "src"
_PKG_FILE = Path(fw_context_mcp.__file__).resolve()
assert _PKG_FILE.is_relative_to(_WT_SRC), (
    "fw_context_mcp resolved OUTSIDE this worktree's src: "
    f"{_PKG_FILE} (expected under {_WT_SRC}). "
    "Run pytest with PYTHONPATH pointing at the worktree src."
)

from mcp.shared.memory import (  # noqa: E402
    create_connected_server_and_client_session,
)

import fw_context_mcp.mcp.server as srv  # noqa: E402  (import AFTER the pin check)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "variants_ab"
PROJECT_ID = "w6abfixture0000000000000000000000a"
PROJECT_NAME = "w6-variants-ab-fixture"

# A = UART (no defines), B = USB, C = semantically A + unrelated define.
VARIANT_ARGS: tuple[tuple[str, list[str]], ...] = (
    ("A", []),
    ("B", ["-DIMPL_B=1"]),
    ("C", ["-DUNRELATED=123"]),
)

CONFIG_TMPL = """[project]
name = "{pname}"
id = "{pid}"

[build]
[[build.variants]]
name = "A"
[[build.variants]]
name = "B"
[[build.variants]]
name = "C"

[index]
db_dir = "{db_dir}"
index_refs = true
index_embeddings = false

[llm]
enabled = false
"""


# ── Wire helpers (same stack as test_mcp_surface_slice_n.py) ─────────────────
async def _wire(tool: str, args: dict):
    async with create_connected_server_and_client_session(srv.mcp) as session:
        await session.initialize()
        return await session.call_tool(tool, args)


def wire_call(tool: str, args: dict):
    """One full MCP round trip (initialize + call_tool) per invocation."""
    return asyncio.run(_wire(tool, args))


def wire_names() -> list[str]:
    async def _list():
        async with create_connected_server_and_client_session(srv.mcp) as session:
            await session.initialize()
            res = await session.list_tools()
            return [t.name for t in res.tools]

    return asyncio.run(_list())


def wire_body(result) -> object:
    """Parse the text content of a CallToolResult as JSON."""
    return json.loads(result.content[0].text)


@pytest.fixture(autouse=True)
def _fresh_server_locks():
    """Bind fresh asyncio locks per test (each wire_call spins its own loop)."""
    old_server = srv._SERVER_LOCK
    srv._SERVER_LOCK = asyncio.Lock()
    yield
    srv._SERVER_LOCK = old_server


# ── Fixture builders (adapted from test_variant_provenance_slice_e.py) ───────
def _write_fixture(root: Path, index_dir: Path) -> None:
    """variant.c (checked-in fixture) + .fw-context/config.toml."""
    (root / ".fw-context").mkdir(parents=True, exist_ok=True)
    src = (FIXTURE_DIR / "variant.c").read_text(encoding="utf-8")
    (root / "variant.c").write_text(src, encoding="utf-8")
    (root / ".fw-context" / "config.toml").write_text(
        CONFIG_TMPL.format(
            pname=PROJECT_NAME,
            pid=PROJECT_ID,
            db_dir=str(index_dir).replace("\\", "/"),
        ),
        encoding="utf-8",
    )


def _write_cc(root: Path, args: list[str]) -> Path:
    """Per-variant single-command compile_commands.json."""
    fwd = str(root).replace("\\", "/")
    payload = [
        {
            "directory": fwd,
            "arguments": ["clang", "-std=c11", "-I.", *args, "-c", "variant.c", "-o", "variant.o"],
            "file": "variant.c",
        }
    ]
    key = hash(tuple(args)) & 0xFFFF
    cc = root / f"cc_{key}.json"
    cc.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return cc


def _run_build(root: Path, cc: Path, index_dir: Path, variant: str) -> str:
    from fw_context_mcp.indexer.runner import run

    db_path = index_dir / PROJECT_ID / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return run(
        cc,
        db_path,
        project_root=root,
        project_id=PROJECT_ID,
        project_name=PROJECT_NAME,
        index_refs=True,
        index_embeddings=False,
        analyze_symbols=False,
        variant=variant,
    )


@pytest.fixture(scope="module")
def variants_ab(tmp_path_factory) -> dict:
    """The A/B/C fixture indexed as three builds into ONE DB (read-only)."""
    root = tmp_path_factory.mktemp("w6_ab_root")
    index_dir = tmp_path_factory.mktemp("w6_ab_index")
    _write_fixture(root, index_dir)
    hashes: dict[str, str] = {}
    for label, args in VARIANT_ARGS:
        hashes[label] = _run_build(root, _write_cc(root, args), index_dir, label)
    # Sanity: one project_id, three distinct builds (design RED plan).
    assert len(set(hashes.values())) == 3, f"expected 3 distinct config_hash: {hashes}"
    conn = sqlite3.connect(str(index_dir / PROJECT_ID / "index.db"))
    try:
        rows = conn.execute(
            "SELECT DISTINCT variant FROM build_configs WHERE project_id=?", (PROJECT_ID,)
        ).fetchall()
    finally:
        conn.close()
    assert {r[0] for r in rows} == {"A", "B", "C"}, f"fixture variants wrong: {rows}"
    return {
        "root": root,
        "index_dir": index_dir,
        "db_path": index_dir / PROJECT_ID / "index.db",
        "hashes": hashes,
    }


# ══ W6a — registry absence (the literal AUD-R5 gap) ══════════════════════════


def test_W6a_wire_registry_has_compare_and_group_tools() -> None:
    """The public MCP must expose compare_variants AND group_equivalent_variants.

    Pre-fix (bdc282a): 40 tools, neither name registered (AUD-R5: 0 hits of
    ``compare_variants|semantic_diff`` in src/).  Post-fix: 42 — the design
    grows the registry by exactly these two.  Later fusions add more:
    the M6 per-build health report (fusion A3) is the 43rd.
    """
    names = wire_names()
    assert "compare_variants" in names, (
        f"AUD-R5 gap not closed: compare_variants absent from the wire registry. "
        f"Have {sorted(names)}"
    )
    assert "group_equivalent_variants" in names, (
        f"AUD-R5 gap not closed: group_equivalent_variants absent from the wire "
        f"registry. Have {sorted(names)}"
    )
    assert len(names) >= 42, (
        f"registry grew 40 -> 42 by design, then fusions beyond; got {len(names)}: {sorted(names)}"
    )
    assert "get_build_health" in names, (
        f"fusion A3 get_build_health absent from the wire registry. Have {sorted(names)}"
    )


# ══ W6b — compare A/B: call-edge delta + honest fingerprint semantics ════════


def test_W6b_compare_variants_ab_call_edge_delta(variants_ab) -> None:
    """compare_variants(A, B, symbol_or_file='transport_send') — the T05 shape.

    Expected: same existence/signature (same USR), the ``#ifdef IMPL_B``
    branch states differ (A: if-branch 0, B: 1), outgoing call edges differ
    (A calls send_uart, B calls send_usb), and the body fingerprint DIFFERS —
    the stored body is ifdef-filtered per config_hash, so the two builds
    genuinely hash different bodies.  Every fact carries the provenance
    (variant, image, config_hash) of the build it was observed in.
    """
    root = str(variants_ab["root"])
    result = wire_call(
        "compare_variants",
        {"a": "A", "b": "B", "symbol_or_file": "transport_send", "project_root": root},
    )
    body = wire_body(result)
    assert result.isError is not True, f"compare_variants failed: {str(body)[:400]}"
    assert body.get("kind") == "symbol", f"expected symbol mode: {body}"

    # ── provenance envelope on both sides (design rule 3) ──
    for side, label in (("a", "A"), ("b", "B")):
        ident = body[side]
        assert ident["variant"] == label, f"side {side}: {ident}"
        assert ident["image"] == "", f"side {side}: {ident}"
        assert ident["config_hash"] == variants_ab["hashes"][label], f"side {side}: {ident}"

    dims = body["dimensions"]
    assert dims["existence"]["equal"] is True, f"existence: {dims['existence']}"
    assert dims["signature"]["equal"] is True, f"signature: {dims['signature']}"

    # ── fingerprint: branch-filtered semantics, pinned honestly (no
    #    fabricated equivalence) ──
    fp = dims["body_fingerprint"]
    assert fp["equal"] is False, (
        f"branch-filtered bodies A/B must fingerprint differently: {fp}"
    )
    assert fp["a"]["fingerprint"] != fp["b"]["fingerprint"], fp
    assert fp["method"] == "fingerprint", fp

    # ── conditions: the IMPL_B branch evaluates differently ──
    cond = dims["conditions"]
    assert cond["equal"] is False, f"conditions: {cond}"
    implb = [d for d in cond["differing"] if "IMPL_B" in (d.get("condition") or "")]
    assert implb, f"IMPL_B region must differ: {cond['differing']}"
    by_dir = {rec["directive"]: rec for rec in implb}
    rec = by_dir.get("ifdef") or implb[0]
    a_taken = {br["kind"]: br["taken"] for br in rec["branches_a"]}
    b_taken = {br["kind"]: br["taken"] for br in rec["branches_b"]}
    assert (a_taken.get("if"), b_taken.get("if")) == (0, 1), (
        f"A must evaluate IMPL_B false and B true: a={a_taken} b={b_taken}"
    )

    # ── edges: the call-edge delta (test spec) ──
    edges = dims["edges"]
    assert edges["equal"] is False, f"edges: {edges}"
    only_a = {(r["name"], r["ref_kind"]) for r in edges["only_in_a"]}
    only_b = {(r["name"], r["ref_kind"]) for r in edges["only_in_b"]}
    assert ("send_uart", "call") in only_a, f"only_in_a: {edges['only_in_a']}"
    assert ("send_usb", "call") in only_b, f"only_in_b: {edges['only_in_b']}"
    for rec in edges["a"] + edges["b"] + edges["only_in_a"] + edges["only_in_b"]:
        for key in ("variant", "image", "config_hash"):
            assert key in rec, f"edge record lost provenance key {key}: {rec}"

    # naming honesty (design rule 4): textual comparison says so
    assert body.get("method") == "text", f"method label: {body.get('method')}"


# ══ W6c — compare A/C: no semantic delta, define-only difference ═════════════


def test_W6c_compare_variants_ac_no_semantic_delta(variants_ab) -> None:
    """compare_variants(A, C, symbol_or_file='transport_send').

    C differs from A ONLY by -DUNRELATED=123, which no conditional of the
    unit references: same observed behavior, so body_fingerprint EQUAL,
    no edge delta, same branch states — only the config-dependency
    (defines) dimension differs.  Provenance on both sides.
    """
    root = str(variants_ab["root"])
    result = wire_call(
        "compare_variants",
        {"a": "A", "b": "C", "symbol_or_file": "transport_send", "project_root": root},
    )
    body = wire_body(result)
    assert result.isError is not True, f"compare_variants failed: {str(body)[:400]}"
    dims = body["dimensions"]

    fp = dims["body_fingerprint"]
    assert fp["equal"] is True, f"A/C same behavior must fingerprint equal: {fp}"
    assert fp["a"]["fingerprint"] == fp["b"]["fingerprint"], fp

    edges = dims["edges"]
    assert edges["equal"] is True, f"A/C must have no target delta: {edges}"
    assert edges["only_in_a"] == [] and edges["only_in_b"] == [], edges

    assert dims["existence"]["equal"] is True, dims["existence"]
    assert dims["signature"]["equal"] is True, dims["signature"]
    assert dims["conditions"]["equal"] is True, f"IMPL_B false in both: {dims['conditions']}"

    defines = dims["defines"]
    assert defines["equal"] is False, f"C carries -DUNRELATED: {defines}"
    assert defines["only_in_b"] == {"UNRELATED": "123"}, defines
    assert defines["only_in_a"] == {}, defines

    # provenance
    assert body["a"]["variant"] == "A" and body["b"]["variant"] == "C"
    assert body["a"]["config_hash"] != body["b"]["config_hash"]


# ══ W6d — group A/C together, separate B (tests/specs/W6.md) ═════════════════


def test_W6d_group_equivalent_variants_groups_ac_separates_b(variants_ab) -> None:
    """group_equivalent_variants('transport_send') over the public MCP.

    The DB-layer implementation (indexer/db/_variants.py) already splits the
    F04 fixture into {A, C} and {B}; the AUD-R5 gap is that NO MCP tool
    exposes it.  This test drives the wire surface and pins: 3 builds, two
    groups, A/C together, B alone, full provenance per member build.
    """
    root = str(variants_ab["root"])
    result = wire_call(
        "group_equivalent_variants", {"symbol": "transport_send", "project_root": root}
    )
    body = wire_body(result)
    assert result.isError is not True, f"group_equivalent_variants failed: {str(body)[:400]}"
    assert body.get("symbol") == "transport_send", body
    assert body.get("build_count") == 3, body
    assert body.get("unobserved") == [], f"all builds observe it: {body}"

    groups = body["groups"]
    assert len(groups) == 2, f"expected exactly two groups: {groups}"
    member_sets = sorted(tuple(sorted(g["variants"])) for g in groups)
    assert ("A", "C") in member_sets, f"A/C must group together: {member_sets}"
    assert ("B",) in member_sets, f"B must stand alone: {member_sets}"

    for g in groups:
        builds = g["builds"]
        assert len(builds) == len(g["variants"]), f"group provenance: {g}"
        got = {(b["variant"], b["image"], b["config_hash"]) for b in builds}
        want = {
            (v, "", variants_ab["hashes"][v]) for v in g["variants"]
        }
        assert got == want, f"group {g['variants']}: {got} != {want}"


# ══ W6e — file mode: symbol_or_file resolves an indexed file ═════════════════


def test_W6e_compare_variants_file_mode(variants_ab) -> None:
    """compare_variants(A, B, symbol_or_file='variant.c') — per-file compare.

    A file target compares the file's conditional regions and a rollup over
    the symbols defined in it: transport_send fingerprints differ (A vs B),
    the two call-target declarations and stable_value agree.
    """
    root = str(variants_ab["root"])
    result = wire_call(
        "compare_variants",
        {"a": "A", "b": "B", "symbol_or_file": "variant.c", "project_root": root},
    )
    body = wire_body(result)
    assert result.isError is not True, f"compare_variants failed: {str(body)[:400]}"
    assert body.get("kind") == "file", f"expected file mode: {body}"
    assert body.get("method") == "text", body

    cond = body["conditions"]
    assert cond["equal"] is False, f"IMPL_B branch differs: {cond}"
    assert any("IMPL_B" in (d.get("condition") or "") for d in cond["differing"]), cond

    symbols = body["symbols"]
    assert "transport_send" in symbols, sorted(symbols)
    assert "stable_value" in symbols, sorted(symbols)
    assert symbols["transport_send"]["fingerprint_equal"] is False, symbols["transport_send"]
    assert symbols["stable_value"]["fingerprint_equal"] is True, symbols["stable_value"]
    summary = body["summary"]
    assert summary["symbols_only_in_a"] == [] and summary["symbols_only_in_b"] == [], summary
    assert summary["fingerprints_differing"] == ["transport_send"], summary

    for rec in symbols.values():
        for side in ("a", "b"):
            for key in ("variant", "image", "config_hash"):
                assert key in rec[side], f"{rec.get('name')}: {rec[side]}"


# ══ W6f — multi-image configs require image (design rule 5) ══════════════════


def test_W6f_multi_image_requires_image(variants_ab, tmp_path) -> None:
    """A variant with several indexed images fails closed until image is given.

    Simulated by copying the fixture DB and inserting a second build row for
    variant A with image='img2' (same pattern as the slice-E tie probe —
    no real second index needed for a resolution-level error).  Also checks
    the image filter of group_equivalent_variants.
    """
    root2 = tmp_path / "w6_mi_root"
    index2 = tmp_path / "w6_mi_index"
    shutil.copytree(variants_ab["root"], root2)
    index2.mkdir(parents=True)
    (index2 / PROJECT_ID).mkdir()
    # Complete independent DB copy via the sqlite backup API (WAL-safe; the
    # raw copytree of the index dir trips on Windows in the nested dir).
    src = sqlite3.connect(str(variants_ab["db_path"]))
    dst = sqlite3.connect(str(index2 / PROJECT_ID / "index.db"))
    src.backup(dst)
    dst.close()
    src.close()
    (root2 / ".fw-context" / "config.toml").write_text(
        CONFIG_TMPL.format(
            pname=PROJECT_NAME, pid=PROJECT_ID, db_dir=str(index2).replace("\\", "/")
        ),
        encoding="utf-8",
    )
    conn = sqlite3.connect(str(index2 / PROJECT_ID / "index.db"))
    try:
        conn.execute(
            "INSERT INTO build_configs (config_hash, project_id, compile_commands_path, "
            "variant, image) VALUES (?, ?, 'probe', 'A', 'img2')",
            ("f" * 64, PROJECT_ID),
        )
        conn.commit()
    finally:
        conn.close()

    result = wire_call(
        "compare_variants",
        {"a": "A", "b": "B", "symbol_or_file": "transport_send", "project_root": str(root2)},
    )
    body = wire_body(result)
    assert result.isError is True, f"multi-image must fail closed: {body}"
    err = body.get("error", "") if isinstance(body, dict) else str(body)
    assert "image" in err.lower(), f"error must name the image requirement: {err}"

    ok = wire_call(
        "compare_variants",
        {"a": "A", "b": "A", "image": "img2", "project_root": str(root2)},
    )
    ok_body = wire_body(ok)
    assert ok.isError is not True, f"image='img2' must resolve: {ok_body}"
    assert ok_body["a"]["image"] == "img2", ok_body
    assert ok_body["b"]["image"] == "img2", ok_body
    # strict fail-closed: image applies to BOTH sides, so a variant without
    # that image is an error that names it
    strict = wire_call(
        "compare_variants",
        {
            "a": "A",
            "b": "B",
            "symbol_or_file": "transport_send",
            "image": "img2",
            "project_root": str(root2),
        },
    )
    strict_body = wire_body(strict)
    assert strict.isError is True, f"B has no img2 build — must fail: {strict_body}"
    strict_err = strict_body.get("error", "") if isinstance(strict_body, dict) else ""
    assert "img2" in strict_err and "B" in strict_err, strict_err

    grouped = wire_call(
        "group_equivalent_variants",
        {"symbol": "transport_send", "image": "img2", "project_root": str(root2)},
    )
    g_body = wire_body(grouped)
    assert grouped.isError is not True, f"group with image filter failed: {g_body}"
    assert g_body["build_count"] == 1, f"only the img2 build matches: {g_body}"


# ═══ NEW-W6-1 — build-level compare mode (the untested W6 surface) ══════════


def test_W6g_compare_variants_build_level_ab(variants_ab) -> None:
    """compare_variants(A, B) with ``symbol_or_file`` omitted → build-level mode.

    W6 shipped ``_compare_build_level`` (counts of symbols/files/
    conditional-regions per config_hash + the ``-D`` delta read from each
    build's own compile_commands.json) but covered only symbol/file mode;
    NEW-W6-1 closes the coverage gap.  A=UART (no defines) vs B=USB
    (``-DIMPL_B=1``): same TU → same counts; the define delta must name
    IMPL_B as only_in_b.
    """
    root = str(variants_ab["root"])
    result = wire_call(
        "compare_variants",
        {"a": "A", "b": "B", "project_root": root},
    )
    body = wire_body(result)
    assert result.isError is not True, f"build-level compare failed: {str(body)[:400]}"

    assert body["kind"] == "build", f"omitted target must be build mode: {body.get('kind')}"
    assert body.get("target") is None
    assert body.get("method") == "text"

    # Provenance identity on both sides and in every per-side record.
    for side, label in (("a", "A"), ("b", "B")):
        ident = body[side]
        assert ident["variant"] == label, f"side {side}: {ident}"
        assert ident["config_hash"] == variants_ab["hashes"][label], f"side {side}: {ident}"

    # ── counts: same TU indexed twice → identical per-build counts ──
    counts = body["counts"]
    for side in ("a", "b"):
        c = counts[side]
        for key in ("symbols", "files", "conditional_regions"):
            assert isinstance(c[key], int) and c[key] > 0, f"counts.{side}.{key}: {c}"
        assert c["files"] == counts["a"]["files"], c
        assert c["symbols"] == counts["a"]["symbols"], (
            "same TU, both branches define the same symbol set: "
            f"a={counts['a']} b={counts['b']}"
        )
        assert c["conditional_regions"] == counts["a"]["conditional_regions"], c

    # ── defines: read from each build's compile_commands.json ──
    defines = body["defines"]
    assert defines["a"]["defines"] == {}, f"A carries no -D: {defines['a']}"
    assert defines["b"]["defines"].get("IMPL_B") == "1", f"B carries -DIMPL_B=1: {defines['b']}"
    assert defines["equal"] is False
    assert defines["only_in_a"] == {}, f"only_in_a must be empty: {defines}"
    assert defines["only_in_b"] == {"IMPL_B": "1"}, f"only_in_b must name IMPL_B: {defines}"
    assert defines["differing"] == {}, f"no shared define differs: {defines}"


def test_W6g_compare_variants_build_level_ac_defines_equal(variants_ab) -> None:
    """Build-level A vs C: only ``-DUNRELATED`` differs — delta proves it."""
    root = str(variants_ab["root"])
    result = wire_call(
        "compare_variants",
        {"a": "A", "b": "C", "project_root": root},
    )
    body = wire_body(result)
    assert result.isError is not True, f"build-level A/C failed: {str(body)[:400]}"
    assert body["kind"] == "build", body.get("kind")
    defines = body["defines"]
    assert defines["only_in_b"] == {"UNRELATED": "123"}, defines
    assert defines["equal"] is False, defines
    counts = body["counts"]
    assert counts["a"]["symbols"] == counts["b"]["symbols"], counts
    assert counts["a"]["conditional_regions"] == counts["b"]["conditional_regions"], counts
