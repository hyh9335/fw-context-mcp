/* C7 planted fixture — sensor_module.c
 *
 * Planted mechanism (family: init_submit_pairing under a dead config guard):
 * the ONLY work_submit call for the module's work item lives inside the
 * guarded branch below; the guard token is never defined anywhere in this
 * slice, so sensor_submit AND its work_submit call are compiled out under
 * the pinned build: the module's submit path is unreachable at source level
 * while sensor_read stays live.
 */
#include "work.h"

struct sensor_sample {
    int raw;
    int scaled;
};

static struct sensor_work sensor_work;

#if defined(CONFIG_SENSOR_IRQ_PATH)
int sensor_submit(int raw)
{
    sensor_work.raw = raw;
    work_submit(&sensor_work);
    return 0;
}
#endif

int sensor_read(int raw)
{
    struct sensor_sample s;
    s.raw = raw;
    s.scaled = raw * 2;
    return s.scaled;
}
