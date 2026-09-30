/* sensor_impl.c — PT-G6: exact-lookup fixture definitions.
 * Three real near-miss symbols exist; the absent probe target of this
 * package (named only in PLANT_SPEC.md) is NOT defined in this fixture.
 */
#include "sensor_api.h"

int sensor_read(int ch)
{
	return ch + 1;
}

int sensor_read_scaled(int ch)
{
	return (ch + 1) * SCALE;
}

int sensor_read_scaled_v2(int ch)
{
	return (ch + 1) * SCALE + CAL_V2;
}
