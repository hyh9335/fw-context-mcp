# Context protocol

Controller sends only:
- phase/task ID
- exact prompt path
- relevant fixture/public scenario paths
- current fw-context HEAD
- output artifact directory
- prior verifier finding only on retry

Children may inspect fw-context source and the assigned corpus themselves.

Do not send:
- full conversation
- every fixture
- all prior logs
- unrelated public repositories

No child may spawn another child.
Long output goes to artifacts.
