# PRODUCTION INTEGRATION RULES

M9 sits upstream of M6.

Flow: Build/TU -> frontend dependency certificate -> equivalent prior TU?

HIT: reuse only frontend work/artifact proven safe by the implemented runtime mechanism, preserve Build/TU binding, continue into existing M6 production flow.

MISS: run existing full frontend parse and continue normally.

M9 must not change M6 fact identity, BuildSet semantics, World identity, query authority, or reintroduce legacy/v2 dependency.

If libclang lifecycle/serialization prevents safe persistent AST reuse, do not invent unsafe serialization. Implement the safest runtime-supported reuse level and measure its real value. If no safe reusable frontend artifact exists, reject M9 rather than broaden scope.
