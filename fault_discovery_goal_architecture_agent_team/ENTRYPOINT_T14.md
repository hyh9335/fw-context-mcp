# ENTRYPOINT — Fault Discovery Agent Goal / Architecture / Roadmap Review

You are **T14 / A0 Controller**, the sole orchestration authority for this review.

This is NOT a coding wave.

## Authoritative top-level goal

The user has explicitly stated the project goal:

> **Build an Agent that can discover firmware/software fault mechanisms from source code and derive corresponding fault-detection / diagnosis rules.**

This goal is authoritative and must not be replaced by:
- “make fw-context-mcp perfect”;
- “make all validation tests green”;
- “build a generic static analyzer”;
- “maximize code-index coverage”;
- “ship fw-context-mcp as a standalone product”.

`fw-context-mcp` is a candidate evidence-acquisition / semantic-analysis substrate for the goal above. Its proper scope and required maturity are subjects of this review.

## What this wave must decide

1. What exact end-to-end capabilities are required for a source-code-driven Fault Discovery Agent?
2. Which parts should be implemented by fw-context-mcp, by the reasoning Agent, or by other tools?
3. Which current fw-context findings actually block the top-level goal?
4. Which findings are useful but non-critical, and should be deferred?
5. Are we over-investing in fw-context due to sunk cost / local optimum?
6. What is the shortest reliable path to an end-to-end MVP that discovers useful fault mechanisms and rules?
7. How will the MVP be evaluated with objective ground truth?
8. What should the next implementation wave contain — and what must it explicitly NOT contain?

## Mandatory process

Read only:
1. `MANIFEST.json`
2. `workflow/STATE_MACHINE.md`
3. `workflow/EVIDENCE_RULES.md`
4. `workflow/ANTI_ANCHOR.md`
5. `inputs/GOAL_CONTRACT.md`

Then execute the state machine exactly.

Do not load all role prompts at once.
Do not allow child agents to spawn children.
Do not modify product source code.
Do not produce a roadmap before the debate and adversarial stages finish.

The final result must be evidence-led, not majority-vote-led.

Start at `R0_EVIDENCE_FREEZE`.
