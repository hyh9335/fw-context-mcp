/* gate_cfg.h — PT-G4 build context (config header).
 * Defines the gate constants and declares the one-line helper that
 * file1.c defines and file2.c calls under #if (see PLANT_SPEC.md).
 */
#ifndef PT_G4_GATE_CFG_H
#define PT_G4_GATE_CFG_H

#include <stdbool.h>

#define GATE_ENABLE 1
#define THRESH 64
#define OFFSET 8

int gate(int x);

#endif
