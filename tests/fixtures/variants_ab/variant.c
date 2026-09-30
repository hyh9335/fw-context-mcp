void send_uart(void);
void send_usb(void);

int stable_value(void) { return 7; }

void transport_send(void) {
#ifdef IMPL_B
    send_usb();
#else
    send_uart();
#endif
}
