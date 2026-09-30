/* main.c — PT-G8 caller. */
#include "poll_cfg.h"

void poller_init(unsigned fallback);
void poller_ctrl_init(unsigned base);

int main(void)
{
	poller_init(50);
	poller_ctrl_init(7);
	return 0;
}
