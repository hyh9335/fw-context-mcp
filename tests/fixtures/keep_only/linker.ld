/* W4 keep_only linker script (AUD-R4): ENTRY + KEEP only.
 *
 * O8 oracle lines — the script content IS the oracle:
 *   - `ENTRY(main)`          : main is the entry root.
 *   - `KEEP(*(.keep_sec))`   : the linker's promise that --gc-sections
 *     cannot remove the .keep_sec input section, where keep_fn lives.
 *
 * Deliberately NO MEMORY block and NO symbol assignment: the script
 * registers neither a memory_regions row nor an `ld:` symbol, which is
 * exactly the handler-layer discovery gap the W4 fix closes.  DO NOT add
 * either while this fixture guards the AUD-R4 regression.
 */
ENTRY(main)

SECTIONS
{
  .keep_sec : { KEEP(*(.keep_sec)) }
}
