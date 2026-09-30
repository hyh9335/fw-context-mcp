/* file2.c — PT-G4: cross-file use of the one-line helper under #if.
 * The call to gate() below crosses the translation-unit boundary into the
 * single-line body in file1.c (see PLANT_SPEC.md).
 */
#include "gate_cfg.h"

int file2_sample(int raw)
{
#if GATE_ENABLE
	if (gate(raw)) {
		return raw - OFFSET;
	}
#endif
	return raw;
}
