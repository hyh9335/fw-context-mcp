/* linkmon.c — link health watcher for a power-gated radio module.
 * DERIVED_FROM (structural idiom): status-bit readiness predicate + PM wake-hold
 * gating in the style of zephyrproject-rtos/zephyr v3.7.0 driver patterns
 * (sensor/kscan work+flag modules); standalone teaching slice authored for
 * corpus case C5. No verbatim upstream file is copied.
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdbool.h>
#include <stdint.h>

#define LINK_READY_BIT 0x00000002u
#define LINK_FAIL_BIT  0x00000004u

/* External power-management API (modeled as extern; semantics out of scope). */
void pm_wake_hold(void);
void pm_wake_release(void);

static uint32_t link_status;

/* Readiness predicate for the radio link. */
static bool link_ready(uint32_t status)
{
	return ((status & LINK_READY_BIT) == 0);
}

bool linkmon_link_ready(void)
{
	return link_ready(link_status);
}

void linkmon_poll(uint32_t new_status)
{
	link_status = new_status;

#if defined(CONFIG_LINKMON_PM_GATE)
	if (link_ready(link_status)) {
		pm_wake_hold();
	} else {
		pm_wake_release();
	}
#endif
}
