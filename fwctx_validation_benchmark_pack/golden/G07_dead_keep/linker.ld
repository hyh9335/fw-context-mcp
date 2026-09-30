ENTRY(main)
SECTIONS
{
  . = 0x10000;
  .text : {
    *(.text.main)
    *(.text.live_root)
    *(.text.live_leaf)
    KEEP(*(.keepme))
    *(.text*)
  }
}
