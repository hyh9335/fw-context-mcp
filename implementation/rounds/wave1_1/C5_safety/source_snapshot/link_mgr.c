#include "link_mgr.h"

/* link_ready() returns 0 when the link is UP (0 == success convention). */
int link_ready(void);

int link_is_down(int state)
{
    /* FAULT (frozen case fact C5): polarity inverted on a single line —
     * the guard treats 0 (UP) as DOWN. */
    if (link_ready() != 0)
    {
        return 1; /* treated as down */
    }
    return 0;
}
