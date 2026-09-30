/* poll_cfg.h — PT-G8 stored defaults (conflict by construction with Make.defs).
 * POLL_INTERVAL: stored default 100; the build passes -DPOLL_INTERVAL=250
 * (see Make.defs). The #ifndef guard makes the command-line definition win
 * silently, so the stored text below stays visible while NOT being the
 * effective build value (F-V1-001 class hazard, see PLANT_SPEC.md).
 */
#ifndef PT_G8_POLL_CFG_H
#define PT_G8_POLL_CFG_H

#ifndef POLL_INTERVAL
#define POLL_INTERVAL 100
#endif

#define CTRL_INTERVAL 40

#endif
