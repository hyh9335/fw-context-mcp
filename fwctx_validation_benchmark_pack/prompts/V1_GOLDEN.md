# V1 — exact golden corpus

Run all REQUIRED G01–G13 cases.

For every case:
1. materialize compile DB/build input;
2. index with fw-context through real supported entrypoint;
3. call the relevant MCP tools through real stdio where practical;
4. save raw outputs;
5. run the independent oracle;
6. normalize only volatile path/ID/timing fields;
7. compare to expected;
8. record positive and negative assertions.

Required adversarial negatives include:
- unrelated same-name method is NOT an override;
- async dispatch is NOT direct CALL;
- KEEP function is NOT definite dead;
- RISC-V vectors are NOT decoded as a Cortex-M pointer table;
- two same-source compile commands are NOT collapsed.

A case failure does not stop unrelated cases.
Do not patch product source.

Output one result JSON per fixture + V1_SUMMARY.md.
