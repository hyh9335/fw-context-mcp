/* poller.c — PT-G8: macro-value boundary fixture.
 * Two #if sites: the POLL_INTERVAL conflict site and the CTRL_INTERVAL
 * no-conflict control site (see PLANT_SPEC.md).
 */
#include "poll_cfg.h"

static unsigned s_interval;
static unsigned s_ctrl;

void poller_init(unsigned fallback)
{
#if POLL_INTERVAL > 200
	s_interval = POLL_INTERVAL / 10;
#else
	s_interval = fallback;
#endif
}

void poller_ctrl_init(unsigned base)
{
#if CTRL_INTERVAL > 0
	s_ctrl = base + CTRL_INTERVAL;
#endif
}
