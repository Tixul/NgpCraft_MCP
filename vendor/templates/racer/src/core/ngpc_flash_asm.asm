$MAXIMUM

;
; ngpc_flash_asm.asm - Flash erase/write primitives for ngpc_flash.c
;
; Part of NgpCraft_base_template (MIT License)
;
; Two entry points, both called ONLY by ngpc_flash.c (never from an ISR):
;
;   _ngpc_flash_erase_asm(u32 abs_block)
;       Erases one 8 KB block with the AMD erase stub extracted from StarGunner
;       (hardware-validated), copied to RAM 0x6E00 and run from there.
;       VECT_FLASHERS cannot erase blocks 32/33/34 (SysCall.txt p.299), so the
;       erase keeps the stub.
;
;   _ngpc_flash_write_bios(const void *src, u32 offset, u32 pages)
;       Programs pages x 256 bytes through the BIOS (VECT_FLASHWRITE, swi 1),
;       the path StarGunner uses on hardware. The watchdog is refreshed right
;       before and right after, as in the SDK save pattern.
;
; THE BLOCK IS A PARAMETER, NOT A CONSTANT. The save block sits 0x6000 below
; the top of the chip, so its address depends on the cartridge size:
; 0x27A000 on 4 Mbit, 0x2FA000 on 8 Mbit, 0x3FA000 on 16 Mbit. A hard-coded
; 0x3FA000 erases 8 KB ELSEWHERE on a smaller chip - possibly the game code.
; ngpc_flash.c reads the size the BIOS stores at 0x6C58 and refuses unknown ones.
;
; cc900 ABI (CALL pushes 4-byte XPC as return address):
;   (xsp+0)..(xsp+3)  = return address
;   (xsp+4)..(xsp+7)  = 1st parameter
;   (xsp+8)..(xsp+11) = 2nd parameter
;   (xsp+12)..(xsp+15)= 3rd parameter
;
; ===========================================================================
; INTERRUPTS ARE PROHIBITED FOR THE WHOLE SEQUENCE. On hardware that is not
; optional. A flash chip being programmed or erased answers reads with STATUS,
; not with its contents. The stub runs from RAM for that reason, but the
; interrupt handlers are still in the cartridge: an interrupt taken while the
; chip is busy fetches its handler from status bits. A game taking VBlank at
; 60 Hz cannot get through a 256-byte program or an 8 KB erase without one.
;
; AN EMULATOR WHOSE FLASH KEEPS SERVING ROM DURING THE WRITE NEVER SHOWS THIS.
; Toshiba: "all interrupts are prohibited during system calls related to flash
; memory management" (SysCall.txt). Hence `di` (encoded 06 07 = EI 7) on entry
; and `ei 0` (06 00) on exit: level 0 is what ngpc_init() runs the game at.
; ===========================================================================

        module  ngpc_flash_asm

        public  _ngpc_flash_erase_asm
        public  _ngpc_flash_write_bios

FLASH_RAM       equ     0x6E00          ; RAM address for stub execution
FLASH_BUS_CTRL  equ     0x6E            ; I/O: flash /WE enable (0x14=on, 0xF0=off)
FLASH_WD        equ     0x6F            ; I/O: watchdog  (0xB1=extended, 0x4E=refresh)
CART_BASE       equ     0x200000        ; CS0 base address

FLASH   section code large


; ===========================================================================
; _ngpc_flash_erase_asm
;   param 1 (xsp+4) : u32 abs_block -- ABSOLUTE address of the 8 KB block
; C prototype: void ngpc_flash_erase_asm(u32 abs_block);
; ===========================================================================
_ngpc_flash_erase_asm:

        di                              ; see the note at the top of the file
        ld      (FLASH_BUS_CTRL),0x14   ; enable /WE on cart bus
        ld      (FLASH_WD),0xB1         ; watchdog: extended mode

        ; Copy erase stub (98 bytes) from ROM to RAM at FLASH_RAM
        push    xbc                     ; preserve XBC across copy
        ld      xde,FLASH_RAM           ; destination: RAM
        ld      xhl,_erase_stub         ; source: stub bytes in ROM
        ld      bc,0x62                 ; BC = 98 bytes
        db      0x83,0x11               ; ldir (xde+),(xhl+) -- copy BC bytes
        pop     xbc                     ; restore XBC (stack back to entry)

        ; Set up registers for stub call
        ld      xix,CART_BASE           ; XIX = 0x200000 (AMD unlock base)
        ld      xiy,0x0                 ; XIY = 0 (erase stub uses as delay counter)
        ld      a,0x0                   ; A = 0 (erase stub state init)
        ld      xde,(xsp+4)             ; XDE = block, given by the caller

        call    FLASH_RAM               ; execute stub from RAM

        ld      (FLASH_BUS_CTRL),0xF0   ; restore watchdog mode first
        ld      (FLASH_WD),0x4E         ; then refresh the enabled watchdog
        ei      0                       ; the chip answers as memory again
        ret


; ===========================================================================
; _ngpc_flash_write_bios
;   param 1 (xsp+4)  : const void *src -- source buffer, in RAM
;   param 2 (xsp+8)  : u32 offset      -- offset INSIDE the cartridge
;                                         (e.g. 0x1FA000 + slot*SAVE_SIZE),
;                                         not the absolute address: XDE3 wants
;                                         the offset.
;   param 3 (xsp+12) : u32 pages       -- number of 256-byte units
; C prototype: void ngpc_flash_write_bios(const void *src, u32 offset, u32 pages);
;
; The BIOS holds the bus itself (no manual 0x6E write). The AMD write stub used
; before disabled the watchdog and wrote its refresh code while it was off.
; ===========================================================================
_ngpc_flash_write_bios:

        di                              ; see the note at the top of the file
        ld      xhl,(xsp+4)             ; 1st param: source
        ld      xde,(xsp+8)             ; 2nd param: cartridge offset
        ld      xbc,(xsp+12)            ; 3rd param: 256-byte pages
        ld      xhl3,xhl                ; bank 3: the BIOS reads its arguments there
        ld      xde3,xde
        ld      xbc3,xbc
        ld      ra3,0                   ; CS0
        ld      rw3,0x0006              ; VECT_FLASHWRITE
        ld      (FLASH_WD),0x4E         ; watchdog refreshed RIGHT BEFORE
        swi     1
        ld      (FLASH_WD),0x4E         ; and right after
        ei      0
        ret


; ===========================================================================
; Erase stub, extracted from StarGunner/bin/main.ngp (hardware-validated).
; 98 bytes, file offset 0x13733. Position-independent, uses XIX for AMD cycles.
;   AMD unlock -> reset -> unlock -> erase setup (0x80) -> unlock -> sector
;   erase (0x30) -> poll DQ7 -> final reset/unlock -> ret
;   Register interface: XIX=cart_base XDE=block_addr XIY=0 A=0
; ===========================================================================

_erase_stub:
        db      0xF3,0xF1,0x55,0x55,0x00,0xAA, 0xF3,0xF1,0xAA,0x2A,0x00,0x55
        db      0xF3,0xF1,0x55,0x55,0x00,0xF0, 0x00,0x00,0xF3,0xF1,0x55,0x55
        db      0x00,0xAA,0xF3,0xF1,0xAA,0x2A, 0x00,0x55,0xF3,0xF1,0x55,0x55
        db      0x00,0x80,0xF3,0xF1,0x55,0x55, 0x00,0xAA,0xF3,0xF1,0xAA,0x2A
        db      0x00,0x55,0xB2,0x00,0x30,0x82, 0x3F,0xFF,0x66,0x15,0xED,0x61
        db      0xED,0xCF,0xFF,0xFF,0x1F,0x00, 0x66,0x09,0xB2,0xCD,0x66,0xED
        db      0x82,0x3F,0xFF,0x66,0x02,0x21, 0xFF,0xF3,0xF1,0x55,0x55,0x00
        db      0xAA,0xF3,0xF1,0xAA,0x2A,0x00, 0x55,0xF3,0xF1,0x55,0x55,0x00
        db      0xF0,0x0E

        end
