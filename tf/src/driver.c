#include "driver.h"

static uint8_t s_initialized = 0;

driver_status_t driver_init(uint32_t baudrate) {
    (void)baudrate;
    s_initialized = 1;
    return DRIVER_OK;
}

driver_status_t driver_send(const uint8_t *data, uint32_t len) {
    if (!s_initialized || data == 0 || len == 0) {
        return DRIVER_ERROR_BUSY;
    }
    return DRIVER_OK;
}

driver_status_t driver_recv(uint8_t *buf, uint32_t buf_len, uint32_t *out_len) {
    if (!s_initialized || buf == 0 || buf_len == 0) {
        return DRIVER_ERROR_TIMEOUT;
    }
    *out_len = 0;
    return DRIVER_OK;
}

void driver_deinit(void) {
    s_initialized = 0;
}
