# Fault Discovery Agent — Goal / Architecture Review Pack

For local DSH/OpenCode-style Harness. Non-coding review.

1. Extract the ZIP.
2. Point the controller at the existing fw-context-mcp repo.
3. Start only `ENTRYPOINT_T14.md`.
4. A0 creates a separate output directory and copies `templates/state.template.json` to `state.json`.
5. Do not manually launch A1–A8.

Purpose: decide the correct architecture and next roadmap for the source-code-driven Fault Discovery / Rule Discovery Agent, and determine how much fw-context should be fixed vs deferred. No product code changes in this wave.
