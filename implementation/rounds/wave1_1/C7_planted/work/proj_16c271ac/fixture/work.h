/* C7 planted fixture — work.h
 * Minimal submit-API stub. work_submit is DECLARED here but has no
 * definition inside the slice (mirrors the C1 shape: the submit API lives
 * outside the indexed slice, so find_callers can observe whether any live
 * caller reaches it).
 */
#ifndef WORK_H
#define WORK_H

struct sensor_work {
    int raw;
};

void work_submit(struct sensor_work *w);

#endif /* WORK_H */
