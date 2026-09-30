# Evidence Rules

Every substantive claim is labeled:
- FACT — directly supported by user goal, code, git, validation artifact, or reproducible experiment.
- INFERENCE — reasoned conclusion from facts.
- ASSUMPTION — necessary but unverified premise.
- PROPOSAL — future design/action.
- UNCERTAINTY — material unknown.

Source priority:
1. user's explicit top-level goal;
2. current code/validation artifacts;
3. project history/decision artifacts;
4. reproducible experiment;
5. external research;
6. agent inference.

Never treat green tests as semantic truth without an independent oracle. Never make a fw-context bug roadmap-critical unless its impact on fault/rule discovery is explained.
