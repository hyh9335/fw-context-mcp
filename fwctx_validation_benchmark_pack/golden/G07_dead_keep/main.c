__attribute__((section(".keepme"), used))
void kept_by_linker(void) {}

static void live_leaf(void) {}
void live_root(void) { live_leaf(); }

static void dead_a(void);
static void dead_b(void) { dead_a(); }
static void dead_a(void) { dead_b(); }

int main(void) { live_root(); return 0; }
