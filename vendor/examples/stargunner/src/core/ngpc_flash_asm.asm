$MAXIMUM

;
; ngpc_flash_asm.asm - Flash save subroutine (pure TLCS-900/H assembly)
;
; Part of NGPC Template 2026 (MIT License)
;
; Flash target: block 33 (F16_B33), 8KB, used as append-only slot array.
;
; ERASE STRATEGY (why CLR_FLASH_RAM and not manual Sharp commands):
;   - Direct ldb (xde),imm8 writes to 0x3FA000 (cart ROM area) are silently ignored
;     by the hardware — user code cannot generate write cycles on the cart bus.
;     Only BIOS/system.lib can assert /WE on the cartridge slot.
;   - CLR_FLASH_RAM (system.lib) is the only confirmed-working erase path for block 33.
;   - CLR_FLASH_RAM silently fails on the 2nd call within the same session (unknown bug,
;     no source available). Fix: append-only writes avoid mid-session erase entirely.
;     Erase is called at most once per 16 saves (block full), which cannot happen twice
;     in a single session for a high-score tracker.
;
; WRITE STRATEGY:
;   - WRITE_FLASH_RAM (system.lib) writes RBC3*256 bytes from XHL3 to XDE3 (offset).
;   - The destination offset is passed as the second C parameter so the caller can
;     address any slot within the block without hardcoding 0x1FA000.
;
; cc900 ABI (CALL pushes 4-byte XPC as return address):
;   (xsp+0)..(xsp+3) = return address
;   (xsp+4)..(xsp+7) = 1st parameter  (pushed as 4-byte XWA via lda/push)
;   (xsp+8)..(xsp+11)= 2nd parameter  (same)
;

        module  ngpc_flash_asm

        public  _ngpc_flash_erase_asm
        public  _ngpc_flash_write_asm

        extern  large CLR_FLASH_RAM     ; system.lib: erase blocks 32/33/34 (VECT_FLASHERS workaround)
        extern  large WRITE_FLASH_RAM   ; system.lib: write (same params as VECT_FLASHWRITE)

FLASH   section code large

; --- _ngpc_flash_erase_asm ---
; Erases block 33 (F16_B33, 8KB) via CLR_FLASH_RAM (system.lib).
; Called only when all append-only slots are full (~once per 16 saves).
; Works reliably on the first call after power-on; never called twice in
; normal gameplay (16+ unique high scores in one session is impossible).
; No parameters. C prototype: void ngpc_flash_erase_asm(void);
_ngpc_flash_erase_asm:

        ld      ra3,0           ; cart 0 = CS0 (0x200000)
        ld      rb3,0x21        ; block 33 = F16_B33
        ld      (0x6f),0x4e     ; watchdog clear before long BIOS call
        calr    CLR_FLASH_RAM   ; erase block 33 (works on first call per session)
        ld      (0x6f),0x4e     ; watchdog clear after
        ret

; --- _ngpc_flash_write_asm ---
; Writes SAVE_SIZE bytes from data to the flash offset given as second parameter.
; The offset is computed by the C caller: SAVE_OFFSET + slot * SLOT_SIZE.
; Parameters:
;   (xsp+4)  = data: source address (const void *)
;   (xsp+8)  = offset: flash destination offset (u32, e.g. 0x1FA000 + slot*512)
; C prototype: void ngpc_flash_write_asm(const void *data, u32 offset);
_ngpc_flash_write_asm:

        ld      ra3,0           ; cart 0
        ld      rbc3,2          ; 2 x 256 = 512 bytes (= SAVE_SIZE)
        ld      xhl,(xsp+4)     ; 1st param: source pointer
        ld      xhl3,xhl        ; promote to bank-3 for BIOS
        ld      xde,(xsp+8)     ; 2nd param: flash offset into primary XDE
        ld      xde3,xde        ; promote to bank-3 (mirrors ld xhl3,xhl pattern)
        ld      (0x6f),0x4e     ; watchdog clear before write
        calr    WRITE_FLASH_RAM
        ld      (0x6f),0x4e     ; watchdog clear after
        ret

        end
