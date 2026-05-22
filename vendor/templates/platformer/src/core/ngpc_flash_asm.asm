$MAXIMUM

;
; ngpc_flash_asm.asm - Flash erase and write (pure TLCS-900/H assembly)
;
; Part of NgpCraft_base_template (MIT License)
;
; ═══════════════════════════════════════════════════════════════════
; FLASH TARGET
; ═══════════════════════════════════════════════════════════════════
;
;   Block 33 (F16_B33)  offset 0x1FA000  size 8 KB
;   Used as 32 append-only slots x 256 bytes each.
;   SAVE_SIZE = 256 bytes  =>  rbc3 = 1  (BIOS writes in units of 256)
;
;   If you change SAVE_SIZE to 512, set rbc3 = 2 in _ngpc_flash_write_asm.
;   NUM_SLOTS = 8192 / SAVE_SIZE must remain >= 1.
;
; ═══════════════════════════════════════════════════════════════════
; WHY CLR_FLASH_RAM (not VECT_FLASHERS, not manual Sharp commands)
; ═══════════════════════════════════════════════════════════════════
;
;  VECT_FLASHERS:  BIOS bug confirmed on production hardware
;                  (SysCall.txt p.299) — silently fails for blocks
;                  32, 33, 34 on 16Mbit carts.  Must use CLR_FLASH_RAM.
;
;  Manual erase:   Direct writes to cart ROM area are no-ops.
;                  User code cannot assert /WE on the cart bus; only
;                  BIOS/system.lib can.  Evidence: ldb (xde),0x20 with
;                  xde=0x3FA000 is silently ignored.  The subsequent
;                  `bit 7,(xde)` reads 0xCA (real flash data; bit 7=1)
;                  and exits immediately — the chip never entered
;                  erase mode.  Confirmed across 3 separate attempts
;                  (essais 10, 15, 16 in FLASH_SAVE_RESEARCH.md).
;
;  CLR_FLASH_RAM:  The documented workaround (SysLib.txt).
;                  Reliable on the FIRST call per power-on session.
;                  Silently fails on the 2nd call (hardware bug; no
;                  source available for system.lib).
;                  Append-only slots ensure it is NEVER called twice
;                  per session: erase only triggers when all 32 slots
;                  are used, which requires 32 saves without power
;                  cycle — impossible in normal gameplay.
;
; ═══════════════════════════════════════════════════════════════════
; WHY ld xde,(xsp+8) + ld xde3,xde  (NOT ld xde3,(xsp+8))
; ═══════════════════════════════════════════════════════════════════
;
;  asm900 Error-230 "Operand type mismatch": stack-relative addressing
;  (xsp+disp) is NOT encodable with bank-3 extended registers.
;  Workaround: two-step load — identical pattern to the XHL3 case:
;
;      ld  xhl,(xsp+4)    ; stack-rel to primary XHL   (valid)
;      ld  xhl3,xhl       ; primary to bank-3           (valid)
;
;      ld  xde,(xsp+8)    ; stack-rel to primary XDE   (valid)
;      ld  xde3,xde       ; primary to bank-3           (valid)
;
; ═══════════════════════════════════════════════════════════════════
; cc900 ABI (TLCS-900/H CALL pushes 4-byte XPC as return address)
; ═══════════════════════════════════════════════════════════════════
;
;   (xsp+0)..(xsp+3)   = return address       (4 bytes, pushed by CALL)
;   (xsp+4)..(xsp+7)   = 1st parameter        (pushed as 4-byte XWA)
;   (xsp+8)..(xsp+11)  = 2nd parameter        (pushed as 4-byte XWA)
;

        module  ngpc_flash_asm

        public  _ngpc_flash_erase_asm
        public  _ngpc_flash_write_asm

        extern  large CLR_FLASH_RAM     ; system.lib: erase blocks 32/33/34
        extern  large WRITE_FLASH_RAM   ; system.lib: write (= VECT_FLASHWRITE)

FLASH   section code large

; ── _ngpc_flash_erase_asm ───────────────────────────────────────────
;
; Erases block 33 (F16_B33, 8 KB) using CLR_FLASH_RAM (system.lib).
;
; Called ONLY when all 32 append-only slots are exhausted.  In practice
; this requires 32 writes without a power cycle, which does not happen
; under normal gameplay.  Therefore this is ALWAYS the first
; CLR_FLASH_RAM call of the session -> guaranteed to succeed.
;
; CLR_FLASH_RAM parameters (register bank 3):
;   RA3 = cart select (0 = CS0, base address 0x200000)
;   RB3 = block number (0x21 = 33 = F16_B33)
;
; BIOS does DI for the duration of the erase.  The watchdog is cleared
; before and after the call; the erase takes ~5-15 ms (8 KB block),
; well within the ~100 ms watchdog period.
;
; No parameters.
; C prototype: void ngpc_flash_erase_asm(void);
;
_ngpc_flash_erase_asm:
        ld      ra3,0           ; cart 0 = CS0 (base 0x200000)
        ld      rb3,0x21        ; block 33 = F16_B33
        ld      (0x6f),0x4e     ; clear watchdog before BIOS call
        calr    CLR_FLASH_RAM   ; erase block 33
        ld      (0x6f),0x4e     ; clear watchdog after
        ret

; ── _ngpc_flash_write_asm ───────────────────────────────────────────
;
; Writes SAVE_SIZE (256) bytes from 'data' to flash at 'offset'.
; The offset is pre-computed by the C caller:
;     offset = 0x1FA000 + slot_index * SAVE_SIZE
;
; WRITE_FLASH_RAM parameters (register bank 3):
;   RA3  = cart select  (0 = CS0 = 0x200000)
;   RBC3 = transfer count, in units of 256 bytes
;          1 = 256 bytes = SAVE_SIZE (default, for SAVE_SIZE=256)
;          2 = 512 bytes (use if SAVE_SIZE is changed to 512)
;   XHL3 = source address in RAM (pointer to the data buffer)
;   XDE3 = destination offset; WRITE_FLASH_RAM adds 0x200000 internally
;          e.g. 0x1FA000 for slot 0, 0x1FA100 for slot 1, etc.
;
; Parameters:
;   (xsp+4)   = data   : const void *  (source RAM buffer)
;   (xsp+8)   = offset : u32           (flash destination offset)
;
; NOTE: ld xde3,(xsp+8) is NOT valid (asm900 Error-230).
;       Use: ld xde,(xsp+8)  then  ld xde3,xde  (two-step, see header).
;
; C prototype: void ngpc_flash_write_asm(const void *data, u32 offset);
;
_ngpc_flash_write_asm:
        ld      ra3,0           ; cart 0 = CS0
        ld      rbc3,1          ; 1 x 256 = 256 bytes (= SAVE_SIZE)
                                ; NOTE: change to rbc3,2 if SAVE_SIZE=512
        ld      xhl,(xsp+4)     ; 1st param: source pointer -> primary XHL
        ld      xhl3,xhl        ; promote to bank-3 for BIOS
        ld      xde,(xsp+8)     ; 2nd param: flash offset -> primary XDE
        ld      xde3,xde        ; promote to bank-3 for BIOS
        ld      (0x6f),0x4e     ; clear watchdog before write
        calr    WRITE_FLASH_RAM ; write SAVE_SIZE bytes
        ld      (0x6f),0x4e     ; clear watchdog after
        ret

        end
