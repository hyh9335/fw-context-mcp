#ifndef DRIVER_H
#define DRIVER_H

#include <stdint.h>

typedef enum {
    DRIVER_OK = 0,
    DRIVER_ERROR_TIMEOUT = 1,
    DRIVER_ERROR_BUSY = 2,
} driver_status_t;

driver_status_t driver_init(uint32_t baudrate);
driver_status_t driver_send(const uint8_t *data, uint32_t len);
driver_status_t driver_recv(uint8_t *buf, uint32_t buf_len, uint32_t *out_len);
void driver_deinit(void);

#endif
