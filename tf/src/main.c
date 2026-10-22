#include "driver.h"

static int process_command(driver_status_t status) {
    if (status == DRIVER_OK) {
        return 1;
    }
    return 0;
}

int main(void) {
    driver_status_t status = driver_init(115200);
    if (status != DRIVER_OK) {
        return 1;
    }

    uint8_t buf[64];
    uint32_t out_len = 0;
    status = driver_recv(buf, sizeof(buf), &out_len);

    if (process_command(status)) {
        driver_send(buf, out_len);
    }

    driver_deinit();
    return 0;
}
