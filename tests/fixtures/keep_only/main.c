/* W4 keep_only fixture (AUD-R4): KEEP-protection vs dead-code detection.
 *
 * Shape per the accepted design (.fw-closeout/artifacts/arch/
 * accepted_design_W4.md) and the W0 sharpened root cause
 * (.fw-closeout/artifacts/W0/R4_dead_code.log):
 *
 *   linker.ld is ENTRY + KEEP only — no MEMORY block, no symbol
 *   assignment — so the stored script registers neither a
 *   memory_regions row nor an `ld:` symbol.  keep_fn (zero references,
 *   section(".keep_sec"), deliberately NO `used` attribute) is protected
 *   solely by the script's `KEEP(*(.keep_sec))` line; dead_fn is the
 *   unprotected negative control.
 *
 * keep_fn and dead_fn have EXTERNAL linkage on purpose (A07 rule, same
 * choice as the T07 fixture): the compiler may elide an unreferenced
 * static definition, and the binary oracle needs the LINKER — not the
 * compiler — to perform the removal.  External linkage alone is
 * deliberately NOT a reachability root (ADR-009).
 *
 * Expected verdicts (find_dead_code):
 *   keep_fn : never reported dead — the KEEP() line is a reachability
 *             root (the linker keeps the section through --gc-sections).
 *   dead_fn : still reported dead (no over-retention).
 *   ctor_fn / entry_live_fn / main : live (constructor, call, ENTRY).
 */
__attribute__((constructor))
static void ctor_fn(void) { }          /* GNU constructor root */

static void entry_live_fn(void);       /* called from main (ENTRY-reachable) */

__attribute__((section(".keep_sec")))
void keep_fn(void) { }                 /* zero refs; KEEP()ed in linker.ld */

void dead_fn(void) { }                 /* zero refs; no protection */

static void entry_live_fn(void) { }

int main(void) {
    entry_live_fn();
    return 0;
}
