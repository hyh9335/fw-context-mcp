#ifdef USE_USB
int usb_send(int x) { return x + 2; }
int transport_send(int x) { return usb_send(x); }
#else
int uart_send(int x) { return x + 1; }
int transport_send(int x) { return uart_send(x); }
#endif
int app_entry(void) { return transport_send(7); }
