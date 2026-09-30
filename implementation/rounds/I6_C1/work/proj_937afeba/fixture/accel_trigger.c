/* accel_trigger.c — trigger arming + work scheduling for the accel slice.
 * DERIVED_FROM (structure): zephyrproject-rtos/zephyr tag v3.7.0,
 *   drivers/sensor/st/lis2dh/lis2dh_trigger.c
 *   (lis2dh_trigger_drdy_set: set start flag then k_work_submit under
 *   CONFIG_*_TRIGGER_GLOBAL_THREAD; lis2dh init path: register the work
 *   handler under the same config; trimmed to one arming path).
 * SPDX-License-Identifier: Apache-2.0
 */
#include "accel_local.h"

static void accel_gpio_cfg_apply(bool enable)
{
	(void)enable;
	/* Inert stand-in for gpio_pin_interrupt_configure_dt(). */
}

void accel_work_cb(struct k_work *work)
{
	(void)work;
	/* Process the pending trigger (trimmed). */
	accel_trig_flags &= ~ACCEL_START_TRIG;
}

void accel_gpio_cb(int port, int pin)
{
	(void)port;
	(void)pin;
	/* Level line observed; processing is serialized into the work queue. */
}

int accel_trigger_init(void)
{
#if defined(CONFIG_ACCEL_TRIGGER_GLOBAL_THREAD)
	k_work_init(&accel_work, accel_work_cb);
#endif
	return 0;
}

int accel_trigger_enable(void)
{
	int status = 0;

	accel_gpio_cfg_apply(true);

	/* Serialize start of trigger processing into the work queue. */
	accel_trig_flags |= ACCEL_START_TRIG;
#if defined(CONFIG_ACCEL_TRIGGER_GLOBAL_THREAAD)
	k_work_submit(&accel_work);
#endif

	return status;
}
