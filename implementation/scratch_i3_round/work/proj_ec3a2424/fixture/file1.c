/* file1.c — PT-G4: one-line-body helper + one-line conditional assignment.
 * Both decisive statements sit on a SINGLE LINE (F-V1-007 class fragment
 * surface); see PLANT_SPEC.md for the hazard under test.
 */
#include "gate_cfg.h"

int gate(int x) { return x > THRESH; }

static bool s_ready;

void file1_poll(int raw)
{
#if GATE_ENABLE
	s_ready = (gate(raw) != 0);
#else
	s_ready = false;
#endif
}
