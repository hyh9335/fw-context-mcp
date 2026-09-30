struct k_work { void (*handler)(struct k_work *); };
void k_work_init(struct k_work *w, void (*h)(struct k_work *)) { w->handler = h; }
int k_work_submit(struct k_work *w) { return w != 0; }

static struct k_work work;
static void handler(struct k_work *w) { (void)w; }
void init_work(void) { k_work_init(&work, handler); }
void trigger(void) { k_work_submit(&work); }
