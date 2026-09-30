.section .text
.global trap_base
.align 2
trap_base:
    j exception_handler
    j irq1_handler
    j irq2_handler
    j irq3_handler

exception_handler:
    ret
irq1_handler:
    ret
irq2_handler:
    ret
irq3_handler:
    ret
