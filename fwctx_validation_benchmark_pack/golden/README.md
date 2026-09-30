# Golden corpus execution

For G01/G02/G03/G05/G06/G07/G08/G09/G10/G12:
run:

`python ../materialize_compile_commands.py <fixture-dir>`

from any location using the correct script path.

G04 is variant-driven: materialize one compile database per variant with the listed defines.
G11 is assembly/architecture semantics and may be queried from source/ELF depending tool capability.
G13 is generated at fanout sizes 1, 10, 50, 100, 250.

Never modify expected.json to make a product result pass.
If expected data is suspected wrong, classify FAIL_ORACLE and independently prove it before changing corpus.
