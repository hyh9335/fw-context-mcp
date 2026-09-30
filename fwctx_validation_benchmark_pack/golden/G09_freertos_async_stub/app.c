typedef void (*TaskFunction_t)(void *);
typedef void (*TimerCallbackFunction_t)(void *);
typedef void * QueueHandle_t;
typedef void * TimerHandle_t;

int xTaskCreate(TaskFunction_t f, const char *n, int s, void *a, int p, void *h) { return f != 0; }
TimerHandle_t xTimerCreate(const char *n, int p, int reload, void *id, TimerCallbackFunction_t cb) { return (TimerHandle_t)cb; }
int xTimerStart(TimerHandle_t t, int ticks) { return t != 0; }
int xQueueSend(QueueHandle_t q, const void *x, int ticks) { return q != 0; }
int xQueueReceive(QueueHandle_t q, void *x, int ticks) { return q != 0; }

static void task(void *x) { (void)x; }
static void timer_cb(void *x) { (void)x; }
static QueueHandle_t q;
static TimerHandle_t t;

void setup(void) {
    xTaskCreate(task, "t", 128, 0, 1, 0);
    t = xTimerCreate("tm", 10, 1, 0, timer_cb);
    xTimerStart(t, 0);
}
void producer(int x) { xQueueSend(q, &x, 0); }
void consumer(void) { int x; xQueueReceive(q, &x, 0); }
