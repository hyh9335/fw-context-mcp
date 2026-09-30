#define FLAG 1
#if FLAG
int active_line(void) { return 1; }
#else
int inactive_line(void) { return 0; }
#endif
