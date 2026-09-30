/* accel_local.h — shared declarations of the standalone accel slice.
 * DERIVED_FROM (structure): zephyrproject-rtos/zephyr tag v3.7.0,
 *   drivers/sensor/st/lis2dh/lis2dh_trigger.c (work-item init/submit idiom under
 *   CONFIG_*_TRIGGER_GLOBAL_THREAD; trimmed standalone slice for corpus case C1).
 * The kernel-API stand-ins below are inert plumbing: the slice is not built
 * against the Zephyr tree and models no runtime kernel semantics.
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef ACCEL_LOCAL_H_
#define ACCEL_LOCAL_H_

#include <stdbool.h>
#include <stdint.h>

/* Minimal inert stand-ins for kernel work APIs (no runtime semantics). */
struct k_work {
	void (*handler)(struct k_work *work);
};

static inline void k_work_init(struct k_work *work,
			       void (*handler)(struct k_work *))
{
	work->handler = handler;
}

static inline int k_work_submit(struct k_work *work)
{
	(void)work;
	return 0;
}

/* Driver state shared across the slice. */
extern struct k_work accel_work;
extern volatile uint32_t accel_trig_flags;
extern volatile bool accel_ready;

#define ACCEL_START_TRIG 0x00000001u

int accel_trigger_init(void);
int accel_trigger_enable(void);
void accel_work_cb(struct k_work *work);
void accel_gpio_cb(int port, int pin);
int accel_init(void);
bool accel_is_ready(void);

#endif /* ACCEL_LOCAL_H_ */
