ENTRY(rt_init)
SECTIONS {
  . = 0x4ab00;
  .text : { *(.text*) *(.rodata*) }
  . = 0x8017cf08;
  .bss (NOLOAD) : { *(.bss*) *(COMMON) }
  /DISCARD/ : { *(.comment*) *(.ARM.attributes*) *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.bss) <= 0x1200, "Runtime state exceeds carved GUI heap window")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x7f000, "Patch overlaps config page")
