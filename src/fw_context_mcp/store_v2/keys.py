"""store_v2.keys — KB-C TUSemanticKey mechanics over ALREADY-CERTIFIED inputs.

Slice 0 (PHASE4 task §4/§5; SLICE0_G7_SCOPE_ADR): pure, deterministic key
mechanics ONLY. The input object represents semantic inputs that have ALREADY
been certified. How production indexing obtains certified fields is G7
(`BLOCKS_KB_IMPLEMENTATION`, Phase-4 Slice 1) and is deliberately absent here.

Normative sources (implemented EXACTLY, not re-designed):

* Frozen K-B root — `phase3/PHASE3_BASELINE.md` L11 / `phase2/SEMANTIC_KEY_CANDIDATES.md`
  §3.1 as amended: ``root = H('fwctx-tukb/v1', L0, L1, L2, L3)``.
* Canonical KB-C L2 composition — `phase3/PHASE3_STORAGE_DECISION.md` L52
  (resolves D1's flagged discrepancy with D5a; the DECISION doc is the canonical
  normative source per its G2 disposition row):

    ``L2_key_digest = H("fwctx-l2c/v1",
                        forced-input content digests,              (c3 legs)
                        resolved-include CONTENT-sequence digest,  (c4 content legs)
                        macro-state digest (c5),
                        key_format)``

  with **parser_generation, environment, toolchain salts riding L3**, and
  path/provenance legs excluded from every key digest (they live in the stored
  per-binding certificate only — Amendment A-1).
* R-K1 key-input hygiene — `phase2/SAFE_REUSE_CONTRACT.md` L174-181:
  (a) full-width hashes; (b) presence guards (FAILED capture poisons or fails
  closed, never a shared constant); (c) nonce discipline (poison binds the
  per-world nonce AND ≥1 world component); (d) UNKNOWN tags are hashed INTO the
  digest they qualify.
* UNKNOWN poison — [SKC]§1.4: an uncertifiable input yields a per-world poison
  digest that (i) never equals any well-formed key, (ii) never equals another
  world's poison, (iii) is storeable as diagnostic but must never enter the
  reusable ``semantic_states`` population path.

Slice-0 interpretations (recorded, minimal-deviation readings of the DECISION
sentence — see SLICE0_SCHEMA_MAPPING.md §KB-C):
* "forced-input content digests" = the ordered full-width content hashes of the
  forced inputs, collapsed under tag ``c3`` (content legs only; the forced
  row's resolved-path leg is provenance per A-1 and is NOT digested).
* "resolved-include CONTENT-sequence digest" = digest over the ordered content
  legs of the resolution certificate rows (directive order), with the
  ``<RESOLVED-NOT-FOUND>`` marker preserved in-sequence where unresolved
  (fail-closed marker; paths/spellings/roles excluded), under tag
  ``c4-content-seq``.
* L3 order: the D5a salt order ``(toolchain, parser)`` is kept and the
  anchored ``parser_generation`` is inserted before the environment digest:
  ``L3 = H('L3', tcg, psg, str(parser_generation), environment_digest)``.

What is deliberately NOT here: commit SHAs (never a semantic reuse input —
SnapshotID is a grouping-only primitive in :mod:`store_v2.world_id` and is not
an input to any key), production certification (G7), publication (Slice 1),
reuse decisions (Slice 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ncc import SEP, sha

#: Canonical KB-C ``key_format`` value (D1 §3 example; DECISION L52: key_format
#: domain-separates frozen-KB-F keys from KB-C keys).
KEY_FORMAT = "fwctx-tukb/v1.l2closure"

#: K-B root domain tag (frozen contract, [SKC]§3.1 / PHASE3_BASELINE L11).
ROOT_DOMAIN = "fwctx-tukb/v1"

#: Canonical KB-C L2 composition domain tag (DECISION L52).
L2_DOMAIN = "fwctx-l2c/v1"

#: UNKNOWN sentinel ([SKC]§1.4 / D5a ``UNKNOWN_TAG``). Hashed INTO the digest it
#: qualifies (R-K1(d)); NEVER equal to any certified value, including the
#: certified-empty environment (H-5).
UNKNOWN_TAG = "fwctx-UNKNOWN/v1"

#: RESOLVED-NOT-FOUND marker preserved in the c4 content sequence (R-T4).
NOT_FOUND = "<RESOLVED-NOT-FOUND>"

_HEX64 = set("0123456789abcdef")


def _is_digest(s: str) -> bool:
    return len(s) == 64 and all(c in _HEX64 for c in s)


def h(*parts: str) -> str:
    """D5a hash convention: full-width SHA-256 over ``SEP``-joined parts."""
    return sha(SEP.join(parts))


def poison(component: str, nonce: str, world_fp: str) -> str:
    """[SKC]§1.4 with H-2 repair: binds the per-world nonce AND >=1 world
    component. Result is deterministic per world and never shared across
    worlds, and never equals any well-formed key."""
    return h(UNKNOWN_TAG, component, nonce, world_fp)


def salt_guard(salt: str, name: str, nonce: str, world_fp: str) -> str:
    """R-K1(b)/H-6 presence guard: empty salt poisons, never keys.

    Returns the poison digest for an empty salt (a value that is itself a
    poison preimage, never a certified value); the salt unchanged otherwise.
    Use :func:`guarded_salt` when the caller must distinguish the two.
    """
    if not salt:
        return poison(f"empty-salt:{name}", nonce, world_fp)
    return salt


def guarded_salt(salt: str, name: str, nonce: str, world_fp: str) -> tuple[str, str]:
    """R-K1(b)/H-6 presence guard with an explicit poison signal.

    Returns ``(value, component)`` where ``component`` is ``""`` for a present
    salt and ``"empty-salt:<name>"`` for a FAILED (absent) one.
    """
    if not salt:
        component = f"empty-salt:{name}"
        return poison(component, nonce, world_fp), component
    return salt, ""


@dataclass(frozen=True)
class CertifiedSemanticInputs:
    """Semantic inputs that have ALREADY been certified (PHASE4 task §4).

    Every field is an already-certified component of the KB-C identity. There
    is deliberately NO field for a commit SHA, a clone root, an absolute path,
    a snapshot id, or a world id — none of them is a semantic reuse input
    (frozen Phase-2 contract; [S1]§4 negative filter).

    Attributes:
        source_blob_sha: full-width SHA-256 of the main source bytes (L0 leg).
        language: language tag of the TU (L0 leg, e.g. ``"c"``).
        ncc_serial: canonical NCC serialization (L1 leg) — class-C post-step
            already applied (certified logical descriptors replace certified
            external path tokens; uncertified paths stay literal = over-key,
            fail-safe direction).
        forced_content_legs: ordered full-width content hashes of the forced
            inputs, position preserved (c3 legs).
        cert_content_sequence: ordered content legs (full-width hash or
            :data:`NOT_FOUND`) of the resolution certificate rows in
            directive-evaluation order (c4 content legs).
        macro_state_digest: already-certified macro-state digest (c5). The
            certification SOURCE for this digest is G7 territory; here it is an
            opaque certified input (Slice-0 vectors use the D5a scanner stub,
            labeled prototype-fidelity).
        environment_digest: certified environment digest, or :data:`UNKNOWN_TAG`
            when the environment could not be certified (H-5: UNKNOWN never
            equals the certified-empty digest).
        parser_generation: anchored parser/indexer generation (bind-time gate
            value; rides L3 per DECISION L52 and is the uniqueness co-column).
        toolchain_salt: non-empty toolchain identity salt (L3).
        parser_salt: non-empty parser identity salt (L3).
        key_format: digest-domain tag (default :data:`KEY_FORMAT`).
        closure_complete: False ⇒ the dependency closure is incomplete ⇒ the
            whole L2/L3/root is poisoned ([SKC]§1.2/§1.4).
        nonce: per-world random nonce (required; poison discipline [SKC]§1.4).

    Raises:
        ValueError: fail-closed, on any malformed component (R-K1(b): a FAILED
            capture must never serialize as a shared constant).
    """

    source_blob_sha: str
    language: str
    ncc_serial: str
    forced_content_legs: tuple[str, ...]
    cert_content_sequence: tuple[str, ...]
    macro_state_digest: str
    environment_digest: str
    parser_generation: int
    toolchain_salt: str
    parser_salt: str
    key_format: str = KEY_FORMAT
    closure_complete: bool = True
    nonce: str = ""

    def __post_init__(self) -> None:
        if not _is_digest(self.source_blob_sha):
            raise ValueError(f"source_blob_sha must be a full-width sha256 hex: "
                             f"{self.source_blob_sha!r}")
        if not self.language:
            raise ValueError("language must be non-empty")
        for i, leg in enumerate(self.forced_content_legs):
            if leg != NOT_FOUND and not _is_digest(leg):
                raise ValueError(f"forced_content_legs[{i}] is neither a "
                                 f"full-width sha256 nor the NOT_FOUND marker: {leg!r}")
        for i, leg in enumerate(self.cert_content_sequence):
            if leg != NOT_FOUND and not _is_digest(leg):
                raise ValueError(f"cert_content_sequence[{i}] is neither a "
                                 f"full-width sha256 nor the NOT_FOUND marker: {leg!r}")
        if self.macro_state_digest != UNKNOWN_TAG and not _is_digest(self.macro_state_digest):
            raise ValueError("macro_state_digest must be a full-width sha256 hex "
                             f"or {UNKNOWN_TAG!r}")
        if self.environment_digest != UNKNOWN_TAG and not _is_digest(self.environment_digest):
            raise ValueError("environment_digest must be a full-width sha256 hex "
                             f"or {UNKNOWN_TAG!r} (UNKNOWN is representable, "
                             "malformed data is not)")
        if not isinstance(self.parser_generation, int) or self.parser_generation < 0:
            raise ValueError("parser_generation must be a non-negative int "
                             "(anchored value; UNKNOWN refuses)")
        if not self.nonce:
            raise ValueError("per-world nonce required ([SKC]§1.4)")
        if not self.key_format:
            raise ValueError("key_format must be non-empty (domain separation)")


@dataclass(frozen=True)
class TUKeyResult:
    """The computed TUSemanticKey layers plus poison status.

    Attributes:
        L0: source-blob layer digest.
        L1: NCC layer digest.
        L2: KB-C closure-scoped content digest (DECISION L52 composition).
        L3: toolchain/parser/parser_generation/environment layer digest.
        root: the TUSemanticKey (full-width; the shared identity value).
        key_format: domain tag the key was computed under.
        parser_generation: anchored generation the key was computed under.
        poisoned: ``""`` when the key is well-formed, else the poison
            component name. A poisoned result is a storeable per-world
            diagnostic, NEVER a reusable identity: it must not enter
            ``semantic_states`` (INV-6).
        world_fp: the world fingerprint bound into every poison digest (H-2).
    """

    L0: str
    L1: str
    L2: str
    L3: str
    root: str
    key_format: str
    parser_generation: int
    poisoned: str
    world_fp: str
    layer_digests: tuple[tuple[str, str], ...] = field(default=())

    @property
    def is_reusable(self) -> bool:
        """False for any poisoned result — poisoned keys never enter
        ``semantic_states`` (INV-6; T03)."""
        return self.poisoned == ""


def compute_tu_semantic_key(inputs: CertifiedSemanticInputs) -> TUKeyResult:
    """KB-C key mechanics over already-certified inputs (pure, deterministic).

    Same certified inputs (including the same per-world nonce) ⇒ byte-identical
    result. The nonce participates ONLY in poison digests, so well-formed keys
    are world-independent while poisons are per-world ([SKC]§1.4).
    """
    world_fp = h("wf", inputs.source_blob_sha, inputs.ncc_serial,
                 str(inputs.closure_complete), inputs.nonce)

    if not inputs.closure_complete:
        p2 = poison("closure-incomplete", inputs.nonce, world_fp)
        p3 = poison("closure-incomplete-L3", inputs.nonce, world_fp)
        proot = poison("closure-incomplete-root", inputs.nonce, world_fp)
        return TUKeyResult(
            L0=h("L0", inputs.source_blob_sha, inputs.language),
            L1=h("L1", inputs.ncc_serial),
            L2=p2, L3=p3, root=proot,
            key_format=inputs.key_format,
            parser_generation=inputs.parser_generation,
            poisoned="closure-incomplete",
            world_fp=world_fp,
            layer_digests=(("L2", p2), ("L3", p3), ("root", proot)),
        )

    l0 = h("L0", inputs.source_blob_sha, inputs.language)
    l1 = h("L1", inputs.ncc_serial)

    # ── canonical KB-C L2 (PHASE3_STORAGE_DECISION.md L52) ──
    # c3: forced-input content digests (content legs only, ordered).
    c3_digest = h("c3", *inputs.forced_content_legs)
    # c4: resolved-include CONTENT-sequence digest — ordered content legs with
    # NOT_FOUND markers preserved; paths/spellings/roles excluded (A-1).
    c4_seq_digest = h("c4-content-seq", *inputs.cert_content_sequence)
    # L2 = H("fwctx-l2c/v1", c3, c4-content-seq, c5, key_format)
    l2 = h(L2_DOMAIN, c3_digest, c4_seq_digest,
           inputs.macro_state_digest, inputs.key_format)

    # ── L3: toolchain/parser salts + parser_generation + environment (DECISION L52) ──
    tcg, tcomp = guarded_salt(inputs.toolchain_salt, "toolchain", inputs.nonce, world_fp)
    psg, pcomp = guarded_salt(inputs.parser_salt, "parser", inputs.nonce, world_fp)
    component = tcomp or pcomp
    if component:
        # empty salt ⇒ presence-guard poison (H-6): poisoned L3 + root
        p3 = poison(component + "-L3", inputs.nonce, world_fp)
        proot = poison(component + "-root", inputs.nonce, world_fp)
        return TUKeyResult(
            L0=l0, L1=l1, L2=l2, L3=p3, root=proot,
            key_format=inputs.key_format,
            parser_generation=inputs.parser_generation,
            poisoned=component,
            world_fp=world_fp,
            layer_digests=(("L2", l2), ("L3", p3), ("root", proot)),
        )
    if inputs.environment_digest == UNKNOWN_TAG:
        l3 = poison("environment-unknown-L3", inputs.nonce, world_fp)   # H-5/F-6
        proot = poison("environment-unknown-root", inputs.nonce, world_fp)
        return TUKeyResult(
            L0=l0, L1=l1, L2=l2, L3=l3, root=proot,
            key_format=inputs.key_format,
            parser_generation=inputs.parser_generation,
            poisoned="environment-unknown",
            world_fp=world_fp,
            layer_digests=(("L2", l2), ("L3", l3), ("root", proot)),
        )
    l3 = h("L3", tcg, psg, str(inputs.parser_generation), inputs.environment_digest)

    root = h(ROOT_DOMAIN, l0, l1, l2, l3)
    return TUKeyResult(
        L0=l0, L1=l1, L2=l2, L3=l3, root=root,
        key_format=inputs.key_format,
        parser_generation=inputs.parser_generation,
        poisoned="",
        world_fp=world_fp,
        layer_digests=(("L0", l0), ("L1", l1), ("L2", l2), ("L3", l3),
                       ("root", root)),
    )
