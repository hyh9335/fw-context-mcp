typedef void (*pFunc)(void);
void Reset_Handler(void) {}
void NMI_Handler(void) {}
void HardFault_Handler(void) {}
void SVC_Handler(void) {}
void PendSV_Handler(void) {}
void SysTick_Handler(void) {}
void IRQ0_Handler(void) {}
void IRQ1_Handler(void) {}

void *__initial_sp;
const pFunc vectors[] __attribute__((section(".vectors"))) = {
    (pFunc)&__initial_sp,
    Reset_Handler,
    NMI_Handler,
    HardFault_Handler,
    0, 0, 0, 0, 0, 0, 0,
    SVC_Handler,
    0,
    0,
    PendSV_Handler,
    SysTick_Handler,
    IRQ0_Handler,
    IRQ1_Handler
};
