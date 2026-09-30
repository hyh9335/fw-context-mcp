/* sensor_api.h — PT-G6 build context (config + API header).
 * Declares the three real symbols. The absent probe target of PT-G6
 * (named only in PLANT_SPEC.md) is deliberately NOT declared anywhere.
 */
#ifndef PT_G6_SENSOR_API_H
#define PT_G6_SENSOR_API_H

#define SCALE 10
#define CAL_V2 3

int sensor_read(int ch);
int sensor_read_scaled(int ch);
int sensor_read_scaled_v2(int ch);

#endif
