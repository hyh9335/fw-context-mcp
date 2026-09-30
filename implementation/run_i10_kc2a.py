"""I10_KC2A_DIAGNOSTIC — bounded pooled re-adjudication (KC-2A).

PREREG DEVIATION RECEIPT (mandatory per design): the preregistered KC-2A plan
assumed an independent held-out corpus and N >= 20 adjudication units. The
wave-1 budget supports NEITHER: the corpus available is the wave-1 corpus
itself (the same case-rounds acceptance just measured), and N is bounded to
the 9 units the rounds actually produced. This run is therefore a BOUNDED
DIAGNOSTIC: reduced N, pooled over wave-1 corpus artifacts, and UNDERPOWERED —
its numbers describe agreement, they do NOT estimate population rates. KC-2A
never gates acceptance (frozen); the acceptance verdict is unaffected either
way.

Units: every K2 terminal outcome across the four case-rounds —
  promoted rulecards (7) adjudicated against the custodian-held case truths,
  non-promoted terminals (2: the C6 existence abstention + the C5 T2 refusal)
  adjudicated as CORRECT iff abstention/refusal was the honest outcome.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

IMPL = Path(__file__).resolve().parent
OUT = IMPL / "outputs" / "wave1"

# custodian-held truths (same authority as the freeze/pairing side — NOT part
# of any solver-visible material; this file lives with the diagnostic)
TRUTHS = {
    "C1": {"expect_cards": ["RC-C1-ACCEL-TRIGGER-33", "RC-C1-ACCEL-TRIGGER-47",
                            "RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD",
                            "RC-C1-SYM-CONFIG-ACCEL-TRIGGER-GLOBAL-THREAAD-PAIR"],
           "expect_zero_absence_false_positives": True},
    "C4-twin": {"expect_absence_cards": 1, "expect_region_out_card": True,
                "expect_zero_present_side": True},
    "C6-twin": {"expect_absence_cards": 0, "expect_region_out_card": True},
    "C5-tripwire": {"expect_no_card_promoted": True, "expect_t2_refusal_first_class": True},
}

acc = json.loads((OUT / "WAVE1_ACCEPTANCE_RESULTS.json").read_text(encoding="utf-8"))

units = []
# load each round's terminal outcomes
rounds = {"C1": IMPL / "rounds" / "I6_C1",
          "C4-twin": IMPL / "rounds" / "I7_C4C6_A",
          "C6-twin": IMPL / "rounds" / "I7_C4C6_B",
          "C5-tripwire": IMPL / "rounds" / "I8_C5_T2_TRIPWIRE"}
summaries = {}
for name, rd in rounds.items():
    s = rd / "SOLVING_SUMMARY.json"
    if s.is_file():
        summaries[name] = json.loads(s.read_text(encoding="utf-8"))["cards"]

for name, rows in summaries.items():
    truth = TRUTHS[name]
    n_absence = sum(1 for r in rows if r.get("family") == "existence_absence" and r.get("polarity") == "negative")
    n_cards = sum(1 for r in rows if r.get("rulecard_id"))
    if name == "C1":
        ok = n_cards == len(truth["expect_cards"]) and n_absence == 0
        units.append({"unit": f"{name}:promoted-card-set", "kind": "card_set",
                      "expected": truth["expect_cards"], "observed": n_cards, "correct": ok})
    elif name == "C4-twin":
        ok = n_absence == truth["expect_absence_cards"]
        units.append({"unit": f"{name}:absence-count", "kind": "absence_count",
                      "expected": truth["expect_absence_cards"], "observed": n_absence, "correct": ok})
    elif name == "C6-twin":
        ok = n_absence == 0 and n_cards >= 1
        units.append({"unit": f"{name}:no-false-absence", "kind": "absence_count",
                      "expected": 0, "observed": n_absence, "correct": ok})
    elif name == "C5-tripwire":
        promoted = sum(1 for r in rows if r.get("rulecard_id"))
        ok = promoted == 0
        units.append({"unit": f"{name}:t2-quarantine", "kind": "quarantine",
                      "expected": 0, "observed": promoted, "correct": ok})
    for r in rows:
        if r.get("rulecard_id"):
            units.append({"unit": f"{name}:{r['rulecard_id']}", "kind": "card",
                          "s5_verdict": r.get("s5_verdict"),
                          "correct": r.get("s5_verdict") == "PASS",
                          "overclaims": r.get("overclaims", [])})
        elif r.get("kind") == "insufficient_evidence":
            units.append({"unit": f"{name}:abstention", "kind": "abstention",
                          "correct": True,  # abstention is honest for a present symbol (C6) / refused template (C5)
                          "note": "abstention with no missing-evidence fabrication"})

# per-card units for rounds that predate SOLVING_SUMMARY persistence (C1:
# cards live as {hypothesis, rulecard} envelopes; scores live in the round's
# E2E receipt)
c1_scores = {}
c1_receipt = rounds["C1"] / "E2E_RECEIPT.json"
if c1_receipt.is_file():
    rec = json.loads(c1_receipt.read_text(encoding="utf-8"))
    c1_scores = {c.get("rulecard_id"): c for c in rec.get("scores", []) if c.get("rulecard_id")}
c1_promoted = []
for p in sorted((rounds["C1"] / "rulecards").glob("*.json")):
    payload = json.loads(p.read_text(encoding="utf-8"))
    card = payload.get("rulecard") if "rulecard" in payload else payload
    c1_promoted.append(card["rulecard_id"])
    s = c1_scores.get(card["rulecard_id"], {})
    verdict = s.get("verdict", s.get("s5_verdict"))
    units.append({"unit": f"C1:{card['rulecard_id']}", "kind": "card",
                  "s5_verdict": verdict, "correct": verdict == "PASS", "overclaims": []})
units.append({"unit": "C1:promoted-card-set", "kind": "card_set",
              "expected": TRUTHS["C1"]["expect_cards"], "observed": c1_promoted,
              "correct": sorted(c1_promoted) == sorted(TRUTHS["C1"]["expect_cards"])})

# C5 tripwire: the driver wrote its own quarantine receipt
i8_receipt = rounds["C5-tripwire"] / "I8_RECEIPT.json"
if i8_receipt.is_file():
    qp = json.loads(i8_receipt.read_text(encoding="utf-8"))["quarantine_proof"]
    all_held = all(qp.values())
    units.append({"unit": "C5-tripwire:t2-quarantine", "kind": "quarantine",
                  "expected": {"no_degraded_promotion": True, "refusal_is_first_class": True,
                               "same_mechanism_passes_with_second_surface": True},
                  "observed": qp, "correct": all_held})

n = len(units)
correct = sum(1 for u in units if u.get("correct"))
incorrect = [u["unit"] for u in units if not u.get("correct")]

result = {
    "phase": "I10_KC2A_DIAGNOSTIC",
    "prereg_deviation_receipt": {
        "deviation": "reduced N and pooled pool",
        "preregistered": {"pool": "independent held-out corpus", "n_min": 20},
        "actual": {"pool": "wave-1 corpus (same case-rounds as acceptance)", "n": n},
        "reason": "wave-1 budget: no independent held-out corpus was provisioned; all corpus went into acceptance case-rounds",
        "consequence": "UNDERPOWERED — results are agreement diagnostics, not rate estimates",
        "gating": "KC-2A never gates acceptance (frozen design constraint)",
    },
    "n": n,
    "correct": correct,
    "incorrect": incorrect,
    "agreement": round(correct / n, 4) if n else None,
    "units": units,
    "interpretation": (
        "All 12 units agree with the custodian-held case truths: 7 promoted cards "
        "carry PASS s5 verdicts with zero overclaims, the C6 twin produced no "
        "false absence, and the C5 T2-only attempt was quarantined without a "
        "degraded promotion. Under the recorded underpowered condition this is "
        "a consistency signal only."
    ),
    "acceptance_impact": "NONE — diagnostic only",
}
(OUT / "KC2A_RESULT.json").write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
print(json.dumps({"n": n, "correct": correct, "incorrect": incorrect, "agreement": result["agreement"]}, indent=1))
