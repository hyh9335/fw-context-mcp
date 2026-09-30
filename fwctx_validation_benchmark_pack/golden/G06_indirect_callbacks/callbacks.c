typedef int (*cb_t)(int);
static int plus1(int x) { return x + 1; }
static int plus2(int x) { return x + 2; }

struct ops { cb_t fn; };
static struct ops table = { .fn = plus1 };
static cb_t global_cb = plus2;

int call_global(int x) { return global_cb(x); }
int call_ops(int x) { return table.fn(x); }

static cb_t registered;
void register_cb(cb_t cb) { registered = cb; }
int invoke_registered(int x) { return registered ? registered(x) : -1; }

void setup(void) { register_cb(plus1); }
int opaque(cb_t cb, int x) { return cb(x); }
