/* accel_core.c — driver init + readiness for the standalone accel slice.
 * DERIVED_FROM (structure): zephyrproject-rtos/zephyr tag v3.7.0,
 *   drivers/sensor/st/lis2dh/lis2dh_trigger.c (trigger init called from the
 *   driver init path; trimmed standalone slice for corpus case C1).
 * SPDX-License-Identifier: Apache-2.0
 */
#include "accel_local.h"

struct k_work accel_work;
volatile uint32_t accel_trig_flags;
volatile bool accel_ready;

int accel_init(void)
{
	int status;

	status = accel_trigger_init();
	if (status == 0) {
		status = accel_trigger_enable();
	}

	/* The driver reports ready once the init sequence completes. */
	accel_ready = (status == 0);

	return status;
}

bool accel_is_ready(void)
{
	return accel_ready;
}
