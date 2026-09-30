# V3 — Windows/WSL parity

Run the same golden subset at minimum:
G01, G02, G03, G04, G05, G06, G07, G10, G12.

Windows and WSL:
- fresh DBs
- same logical source bytes
- real stdio
- canonical outputs

Canonicalization may remove:
- absolute root prefix
- timing
- volatile database integer IDs

It must NOT remove:
- context/build identity
- symbol identity
- relative location
- edge kind/certainty
- condition evaluation
- variant/config provenance
- semantic fingerprint

Also run:
- Windows -> WSL MCP launch path
- one RIOT or NuttX public smoke on WSL
- one public source query on Windows if compile DB/path portability permits

Output PARITY_DIFF.json and V3_SUMMARY.md.
