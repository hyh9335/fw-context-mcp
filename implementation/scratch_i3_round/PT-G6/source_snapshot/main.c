/* main.c — PT-G6 control caller (exact symbol use of the real symbol). */
#include "sensor_api.h"

int main(void)
{
	return sensor_read_scaled(2);
}
