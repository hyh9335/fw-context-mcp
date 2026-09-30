/* mode_select.c — PT-G1 shared source: compiled into BOTH variants.
 * The #if FEATURE body below diverges by variant; a single-variant index
 * must never serve one variant's body under the other variant's config
 * identity (F-V1-003 class hazard, see PLANT_SPEC.md).
 */
#include "feat_cfg.h"

const char *mode_select_path(void)
{
#if FEATURE
	return "mode_a_path";
#else
	return "mode_b_path";
#endif
}
