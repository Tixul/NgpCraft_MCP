# TLCS-900/H Reference

High-density reference for the TLCS-900/H CPU as used in the Neo Geo Pocket Color: registers, ABI, data types, memory model, opcode encodings, addressing modes, and the full instruction set. Facts are drawn from the official Toshiba documentation and verified against open-source disassembler/assembler sources.

---

## 1. CPU — Registers

### 32-bit (extended)
```
XWA  XBC  XDE  XHL  XIX  XIY  XIZ  XSP
```
### 16-bit (low word of the above)
```
WA   BC   DE   HL   IX   IY   IZ   SP
```
### 8-bit
```
W A  B C  D E  H L
```
### Flags (register F)
```
SF  ZF  VF  HF  CF   (Sign, Zero, oVerflow, Half-carry, Carry)
```
### Reserved registers
```
XIX   -> PIC (position-independent code)
XIY   -> PID (position-independent data)
XSP   -> stack pointer, MUST be initialized before any CALL/PUSH/POP
```

---

## 2. ABI / Calling Convention

### `__cdecl`

```
Args      : stack, right to left (last arg pushed first)
1-byte arg: extended to 2 bytes on the stack
Return    : XHL (<= 4 bytes)
            if > 4 bytes -> hidden pointer in XWA (caller allocates)
Ret instr : ret
```

### `__adecl`
```
Arg 1  : WA / XWA (<= 2 bytes -> WA, <= 4 bytes -> XWA)
Arg 2  : BC / XBC
Arg 3  : DE / XDE
Arg 4+ : stack left to right
Return : XHL (max 4 bytes, > 4 bytes = compile error)
Ret    : ret (no stack args) | retd <size> (with stack args)
```

### Caller-saved (reload after CALL)
```
XWA  XBC  XDE  XIX  XIY  XIZ
```
### Callee-saved (preserved by the callee)
```
XHL
```

### `__interrupt`
```
Ret : reti
Args/return : void mandatory
Save : all used registers saved automatically
```

### `__regbank(N)` — N in {-1, 0, 1, 2, 3}
```
N = -1  -> no bank change, no save
N = 0-3 -> change bank, save XIY/XIX/XIZ
Ret : reti
```

---

## 3. Data Types

```
char            1 byte  signed by default (-128..127)
unsigned char   1 byte  (0..255)
short           2 bytes signed
unsigned short  2 bytes
int             2 bytes  === short  (IMPORTANT: not 4!)
unsigned int    2 bytes  === unsigned short
long            4 bytes signed
unsigned long   4 bytes
float           4 bytes  IEEE 754
double          8 bytes  IEEE 754
long double    10 bytes

near pointer    2 bytes
far  pointer    4 bytes
void*           4 bytes  far
```

### Stack alignment
```
1-byte arg -> extended to 2 bytes on the stack
Stack alignment : 2 bytes
Data sections  : align 2,2 by default
Code sections  : align 1
```

---

## 4. Memory Model

### Addressing zones
```
tiny  : 0x000000 - 0x0000FF  (8-bit  displacement)
near  : 0x000000 - 0x00FFFF  (16-bit displacement)
far   : 0x000000 - 0xFFFFFF  (32-bit displacement)
```

### C qualifiers
```c
int __tiny  *p;   // 8-bit  pointer
int __near  *p;   // 16-bit pointer
int __far   *p;   // 32-bit pointer
```

### extern asm qualifiers
```asm
extern tiny   sym   ; 1 byte
extern small  sym   ; 2 bytes (near)
extern medium sym   ; 3 bytes
extern large  sym   ; 4 bytes (far)
```

---

## 5. NGPC Memory Layout

```
0x000000 - 0x0000FF   Internal I/O (SFR)
0x004000 - 0x005FFF   Main RAM 8 KB (battery-backed)
0x006000 - 0x006BFF   System RAM (reserved)
0x006C00 - 0x006FFF   Reserved for internal program
0x007000 - 0x007FFF   Z80 RAM 4 KB (PSG driver)
0x008000 - 0x0087FF   Video registers
0x008800 - 0x0088FF   Sprite VRAM (256/288 bytes)
0x009000 - 0x0097FF   Scroll plane 1 VRAM 2 KB
0x009800 - 0x009FFF   Scroll plane 2 VRAM 2 KB
0x00A000 - 0x00BFFF   Tile RAM 8 KB
0x200000 - 0x3EFFFF   Flash cartridge ROM 2 MB max
0x3F0000 - 0x3FFFFF   Monitor ROM 64 KB (BIOS)
0xFF0000 - 0xFFFFFF   Internal ROM 64 KB
```

### Linker sections -> memory
```
f_code    -> ROM  0x200000+    align 1
const     -> ROM  0x200000+    align 2,2
f_data    -> ROM  (source) / RAM 0x004000+ (dest)  align 2,2
f_area    -> RAM  0x004000+    align 2,2  (BSS, never in ROM)
```

---

## 6. Cartridge Header (0x200000)

```
0x200000  28 bytes  Copyright string
          "COPYRIGHT BY SNK CORPORATION"  (official)
          " LICENSED BY SNK CORPORATION"  (licensed)

0x20001C  4 bytes   Start address (little-endian)
                    -> points to __startup in ROM

0x200020  2 bytes   Software ID, BCD (0x0000 = dev)
0x200022  1 byte    Sub-code / version
0x200023  1 byte    Compatible system
                    0x00 = monochrome only
                    0x10 = color compatible

0x200024  12 bytes  Software title, ASCII
0x200030  16 bytes  Reserved (set to 0)
```

---

## 7. User Interrupt Vectors (0x6FB8-0x6FFF, 4 bytes each)

```
0x6FB8   SWI 3
0x6FBC   SWI 4
0x6FC0   SWI 5
0x6FC4   SWI 6
0x6FC8   RTC Alarm
0x6FCC   VBlank  <- MANDATORY, cannot be disabled
0x6FD0   Z80 IRQ
0x6FD4   Timer 0
0x6FD8   Timer 1
0x6FDC   Timer 2
0x6FE0   Timer 3
0x6FE4   Serial TX  (not usable in practice)
0x6FE8   Serial RX  (not usable in practice)
0x6FF0   End MicroDMA 0
0x6FF4   End MicroDMA 1
0x6FF8   End MicroDMA 2
0x6FFC   End MicroDMA 3
```

### Reset vectors (internal ROM)
```
0xFFFF00   Reset -> __startup
0xFFFF04   SWI 0
0xFFFF08   INTUNDEF / SWI 2
```

---

## 8. Watchdog

```
Address  : 0x6F  (SYSCR1)
Value    : write 0x4E to clear
Timeout  : ~100 ms
Asm      : ldb (0x6f), 0x4e
C        : *(volatile unsigned char*)0x6F = 0x4E;
```

---

## 9. Linker Symbols (generated automatically)

```
__FAreaOrg    large  -> start address of f_area in RAM
__FAreaSize   medium -> size of f_area in bytes
__FDataAddr   large  -> destination address of f_data in RAM
__FDataOrg    large  -> source address of f_data in ROM
__FDataSize   medium -> size of f_data in bytes
__BaseXSP     medium -> top of stack (end of RAM)
```

---

## 10. crt0 Bootstrap — Exact Sequence

```asm
$MAXIMUM
module crt0

extern medium _WDMOD          ; 0x5c
extern medium _WDCR           ; 0x5d
extern large  __FAreaOrg
extern medium __FAreaSize
extern large  __FDataAddr
extern large  __FDataOrg
extern medium __FDataSize
extern medium __BaseXSP
extern large  _main

f_area  section data  large align=2,2
f_data  section data  large align=2,2
f_code  section code  large

    public __startup
__startup:
    di                          ; disable interrupts
    ldb (_WDMOD), 0x00          ; disable watchdog
    ldb (_WDCR),  0xb1          ; clear watchdog
    ld  XSP, __BaseXSP          ; init stack  <- FIRST USE OF XSP

    ; --- clear BSS (f_area) ---
    ld  XDE, __FAreaOrg
    ld  BC,  __FAreaSize
    or  BC, BC
    j   z, .Larea_done
.Larea_loop:
    ldb (XDE+), 0
    sub BC, 1
    j   nz, .Larea_loop
.Larea_done:

    ; --- copy f_data ROM -> RAM ---
    ld  XDE, __FDataAddr
    ld  XHL, __FDataOrg
    ld  BC,  __FDataSize
    or  BC, BC
    j   z, .Ldata_done
.Ldata_loop:
    ldb A, (XHL+)
    ldb (XDE+), A
    sub BC, 1
    j   nz, .Ldata_loop
.Ldata_done:

    ei                          ; enable interrupts
    j   _main                   ; jump to main()

    end
```

### Instructions FORBIDDEN before XSP init
```
CALL  CALR  LINK  POP  PUSH  RET  RETD  RETI  SWI  UNLK
```

---

## 11. Assembler Syntax

### Source format
```
[Label:]
Mnemonic [operands]  ; comment
;;comment invisible in the listing
```

### Numbers
```
0b1010 | 0y1010   binary
0377              octal
255               decimal
0xff              hexadecimal
3.14f             float 32-bit
3.14              double 64-bit
```

### Strings
```asm
db "Hello", 0      ; max 511 bytes, no automatic null
db 'AB'            ; 32-bit little-endian character
```

### Escapes
```
\0 \n \t \r \b \f \v \a \" \' \\ \? \x## \OOO
```

### Expressions — precedence (high -> low)
```
1. unary : - ~    | functional : sizeof() startof() fims() fimdh/l() fimxh/m/l()
2. * / %
3. + -
4. >> <<
5. &
6. ^
7. |
```
All values are signed 32-bit integers.

---

## 12. Assembler Directives — Complete Reference

### Module
```asm
module <name>          ; module declaration (once per file)
end                    ; end of module (mandatory)
```

### Sections
```asm
<name> section code    large [align=<s>,<d>]   ; executable code
<name> section data    medium [align=<s>,<d>]  ; RAM data
<name> section romdata large [align=<s>,<d>]   ; ROM data
<name> section data    abs=0x1000              ; absolute address
```
Alignment: powers of 2, default `align=1,1`

### Inter-module symbols
```asm
public sym1, sym2           ; export
extern large sym1, sym2     ; import (large = 4 bytes)
extern medium sym3          ; 3 bytes
```

### Initialized data
```asm
db  val[, val...]       ; 8-bit
dw  val[, val...]       ; 16-bit
dp  val[, val...]       ; 24-bit
dd  val[, val...]       ; 32-bit
dl  val[, val...]       ; 32-bit (alias dd)
```

### Repeated data
```asm
dfb cnt, val            ; cnt x 8-bit = val
dfw cnt, val            ; cnt x 16-bit = val
dfp cnt, val            ; cnt x 24-bit = val
dfd cnt, val            ; cnt x 32-bit = val
```

### Reservation without init (BSS)
```asm
dsb cnt                 ; cnt x 8-bit
dsw cnt                 ; cnt x 16-bit
dsp cnt                 ; cnt x 24-bit
dsd cnt                 ; cnt x 32-bit
dsl cnt                 ; cnt x 32-bit (alias dsd)
```

### Constant
```asm
NAME  equ  expression    ; absolute constant, not redefinable
```

### Alignment / origin
```asm
align  4                ; align LC to 4 bytes
org    0x1000           ; force LC (must be increasing)
```

### Control
```asm
$MAXIMUM                ; MANDATORY at top of file
$include "file.inc"     ; include (max 99 levels)
```

---

## 13. Standard Sections — Official Names

| Section  | Type     | Displacement | Location | Contents                    |
|----------|----------|--------------|----------|-----------------------------|
| f_code   | code     | large        | ROM      | Executable code             |
| n_code   | code     | medium       | ROM      | Near code                   |
| f_data   | data     | large        | RAM      | Initialized data            |
| n_data   | data     | medium       | RAM      | Near initialized data       |
| f_area   | data     | large        | RAM      | BSS (zeroed at init)        |
| n_area   | data     | medium       | RAM      | Near BSS                    |
| const    | romdata  | large        | ROM      | Constants                   |
| pic_base | code     | large        | ROM      | PIC base                    |
| pid_base | data     | large        | RAM      | PID base                    |

---

## 14. Toshiba Object Format: IEEE695

- Extensions: `.rel` (relocatable), `.abs` (absolute), `.lib` (library)
- Binary IEEE695 format
- Contains: sections, symbol table, relocation records
- Alternative output: Intel HEX (`.h*`) or Motorola S-Record (`.s*`)

A simpler textual object format may also be used by open-source tools, with a minimal structure:
```
sections[]      -> name, data, address = 0 (to be relocated)
symbols[]       -> name, section, offset, public|extern
relocations[]   -> offset_in_section, type, sym_ref
```

### Relocation types
```
REL_ABS32    32-bit absolute address (far)
REL_ABS16    16-bit absolute address (near, vectors only)
REL_REL8     8-bit relative jump
REL_REL16    16-bit relative jump
```

---

## 15. Linker — Script Format (.lcf)

### General structure
```
MEMORY {
    RAM : org=0x004000, len=0x2000, attr=I
    ROM : org=0x200000, len=0x1E0000, attr=R
}

SECTIONS {
    f_code   { ... } > ROM
    const    { ... } > ROM
    f_data   { ... load_addr(ROM) } > RAM
    f_area   { ... } > RAM
}

SYMBOL {
    __FAreaOrg  = start(f_area);
    __FAreaSize = size(f_area);
    __FDataAddr = start(f_data);
    __FDataOrg  = loadaddr(f_data);
    __FDataSize = size(f_data);
    __BaseXSP   = end(RAM);
}
```

### Memory attributes
```
I = Initializable (RAM)
R = Read-only (ROM)
W = Write-only
L = Link-only (debug, not downloaded)
```

### Script operators
```
sizeof(<section>)          section size
startof(<section>)         start address
startof_memory(<mem>)      start address of memory region
sizeof_memory(<mem>)       size of memory region
```

---

## 16. Symbol Naming

```
C function foo()     -> _foo
Global variable bar  -> _bar
Linker symbol        -> __xxx  (two underscores)
Local asm labels     -> .Lname  (dot-L prefix)
__adecl func baz()   -> .baz   (single dot)
```

---

## 17. C Pragmas — Reference

```c
#pragma section data   f_data far     // variables -> f_data
#pragma section const  f_const far    // constants -> f_const
#pragma section code   f_code far     // code -> f_code
#pragma pack(1)                       // struct 1-byte align
#pragma pack()                        // back to default (2)
#pragma extern far                    // externs = far by default
#pragma io VARNAME 0xADDR             // SFR mapping
#pragma disinterrupt(level) funcname  // mask IRQ in func
#pragma warningoff 42                 // suppress warning 42
#pragma endwarningoff 42
#pragma pid_on / pid_off              // PID
#pragma cdecl                         // cdecl for following functions
#pragma adecl                         // adecl
```

---

## 18. C Register Pseudo-Variables

```c
// 32-bit
unsigned long __XHL, __XDE, __XBC, __XWA, __XIX, __XIY, __XIZ, __XSP;
// 16-bit
unsigned int __HL, __DE, __BC, __WA, __IX, __IY, __IZ, __SP, __AF;
// 8-bit
unsigned char __H, __L, __D, __E, __B, __C, __W, __A;
// Flags
unsigned int __SF, __ZF, __VF, __CF;
// DMA
unsigned int __MDMA0, __MDMA1, __MDMA2, __MDMA3;
```

Restrictions:
- No `&` operator on these variables
- No global assignment
- Simple expressions only

---

## 19. Assembler Optimization (`-O1`)

```
j  [cc,] addr   ->  jr  (rel 8-bit)  if in range
                ->  jrl (rel 16-bit) otherwise
                ->  jp  (absolute)   fallback
cal [cc,] addr  ->  calr (rel)       if in range
                ->  call (absolute)  fallback
```

---

## 20. NGPC I/O Registers — Key (IO900L.H)

```
0x5c  WDMOD    watchdog mode
0x5d  WDCR     watchdog control
0x6E  SYSCR0   system clock control 0
0x6F  SYSCR1   system clock control 1  <- clear watchdog = write 0x4E
0x70  INTE0AD  interrupt enable 0 + A/D
0x7b  IIMC     INT input mode control
0x7c-0x7f  DMA0V..DMA3V  DMA vectors
```

### Timers
```
0x20  TRUN     timer run register
0x22-0x27  TREG0-3  8-bit timers
0x30/0x32  TREG4/5  16-bit timers
0x40/0x42  TREG6/7  16-bit timers
```

### Serial
```
0x50/0x54  SC0BUF/SC1BUF   serial buffers
0x51/0x55  SC0CR/SC1CR     serial control
0x52/0x56  SC0MOD/SC1MOD   serial mode
0x53/0x57  BR0CR/BR1CR     baud rate
```

---

## 21. Confirmed Opcode Table

Verified against open-source TLCS-900/H disassembler sources.

### Fixed instructions
```
00              NOP
06 07           DI               (CRITICAL: NOT 06 00; DI = EI mask=7)
06 n            EI n             (n != 7)
07              RETI
08 n8 i8        LDB (n8), #8     (3 bytes, I/O byte write 0x00-0xFF)
0A n8 lo hi     LDW (n8), #16    (4 bytes, I/O word write 0x00-0xFF)
0E              RET
1B lo mi hi     JP #24           (4 bytes, confirmed)
1C lo hi        CALL #16         (3 bytes, abs16)
1D lo mi hi     CALL #24         (4 bytes, abs24)
1E lo hi        CALR d16         (3 bytes)
F8+n            SWI n            (1 byte, n=0..7, F9=SWI 1)
(0x60+cc) d8    JR  [cc] d8      (2 bytes)
(0x70+cc) lo hi JRL [cc] d16     (3 bytes)
(0xC8+r) 1C d8  DJNZ r, d8       (3 bytes, decrement r then jump if != 0)
(0x98+r) 0C lo hi  LINK r, d16   (4 bytes, create frame)
(0x98+r) 0D     UNLK r           (2 bytes, destroy frame)
```

### Variable instructions (first byte encodes register/size)
```
R8:{W=0 A=1 B=2 C=3 D=4 E=5 H=6 L=7}
R16:{WA=0 BC=1 DE=2 HL=3 IX=4 IY=5 IZ=6 SP=7}
R32:{XWA=0 XBC=1 XDE=2 XHL=3 XIX=4 XIY=5 XIZ=6 XSP=7}

(0x20+R) i8          LD R8, #8    (2 bytes)
(0x30+R) lo hi       LD R16, #16  (3 bytes, LD HL = 33 lo hi)
(0x40+R) b0..b3      LD R32, #32  (5 bytes, LD XSP = 47 b0..b3)
0x28+R               PUSH R16     (1 byte)
0x38+R               PUSH R32     (1 byte)
0x48+R               POP  R16     (1 byte)
0x58+R               POP  R32     (1 byte)
(0x60+cc) d8         JR  [cc,] d8  (2 bytes)
(0x70+cc) lo hi      JRL [cc,] d16 (3 bytes)
```

### Memory writes (B0_mem)
```
F1 lo hi (0x50+R16)         LDW (abs16), R16   ex: LDW (0x8282),HL = F1 82 82 53
F1 lo hi 02 lo2 hi2         LDW (abs16), #16   ex: LDW (0x8282),#0FFF = F1 82 82 02 FF 0F
F1 lo hi (0x40+R8)          LD  (abs16), R8
```

### Extended-register prefixes (0xC7 byte / 0xE7 long)

`0xC7` is the **BYTE** extended-register prefix: `C7 <reg_code> <sub-op> [imm]`. The
reg_code can select a register in any bank (not just the current-bank r8), which is how
the bank-3 banked registers below are reached:
```
C7 30 (0xA8+n)   LD RA3, n    (C7 30 AB = LD RA3,3)
C7 31 (0xA8+n)   LD RW3, n    (C7 31 B2 = LD RW3,10)
```

`0xE7` is the **LONG** extended-register prefix — the 32-bit sibling of `0xC7` (byte):
`E7 <reg_code> <sub-op> [imm32]`. HW/ngdis-confirmed 2026-07-03. The reg_code indexes the
r32 table: `0x38` -> `XDE3` (bank-3 banked long); `0xE0..0xFF` -> the current-bank longs
`XWA..XSP`. It routes through the same extended-register decoder as `0xC7`
(getmem(0xE7)=0x17), with getzz(0xE7)=2 = long. Seen on the real SNK BIOS boot path.

The TLCS-900/L1 CPU core uses the same instruction set as the TLCS-900/H apart from SFRs, so the /L1 core datasheet opcode table is a valid instruction reference.

---

## 22. Complete Example — Minimal ASM Program

```asm
$MAXIMUM
module hello

extern large  _main
extern medium __BaseXSP
; (linker symbols defined in the lcf script)

f_area  section data  large align=2,2
f_data  section data  large align=2,2

f_code  section code  large
    public __startup
__startup:
    di
    ldb (0x6f), 0x4e         ; clear watchdog
    ld  XSP, __BaseXSP

    ; simplified BSS clear (if no BSS, trivial loop)
    ei
    j   _main

CODE_SEC section code large
    public _main
_main:
    ; clear watchdog in the main loop
.Lloop:
    ldb (0x6f), 0x4e
    j   .Lloop

    end
```

---

## 23. Section Naming Conventions

For the open-source assembler/linker:

| Internal tool name | asm section     | Final segment |
|--------------------|-----------------|---------------|
| SEC_CODE           | f_code          | ROM           |
| SEC_RODATA         | const           | ROM           |
| SEC_DATA_LOAD      | f_data (ROM side) | ROM         |
| SEC_DATA           | f_data (RAM side) | RAM         |
| SEC_BSS            | f_area          | RAM           |

---

## 24. Complete Instruction Set — Mnemonics

### Load / Transfer
```
LD     r,src           load (8/16/32-bit per zz)
LDW    (mem),#16       load 16-bit to memory
LDA    R,mem           load effective address into R
LDAR   R,$+4+d16       load relative address (PIC)
PUSH   r / R / #imm    push (8/16/32)
PUSHW  (mem) / #16     push 16-bit
POP    r / R           pop
POPW   (mem)           pop 16-bit to memory
LDC    cr,r / r,cr     load/store control register
LDX    (a8),#8         extended load (5-byte format, a8 = IO)
LDF    #3              load register bank (0-7)
```

### Exchange / Mirror
```
EX     R,r / F,F'      exchange registers
MIRR   r               mirror bits of the 16-bit register
```

### Block / String instructions
```
LDI    [(XDE+),(XHL+)]   copy byte XHL->XDE, increment
LDIW   [(XDE+),(XHL+)]   copy word
LDIR   [(XDE+),(XHL+)]   repeat LDI (BC times)
LDIRW  [(XDE+),(XHL+)]   repeat LDIW
LDD    [(XDE-),(XHL-)]   copy byte, decrement
LDDW   [(XDE-),(XHL-)]   copy word, decrement
LDDR   [(XDE-),(XHL-)]   repeat LDD
LDDRW  [(XDE-),(XHL-)]   repeat LDDW
CPI    A/WA,(Xrr+)       compare and increment
CPIR   A/WA,(Xrr+)       repeat CPI
CPD    A/WA,(Xrr-)       compare and decrement
CPDR   A/WA,(Xrr-)       repeat CPD
```

### Arithmetic
```
ADD  / ADDW   dst,src    addition (8 or 16-bit)
ADC  / ADCW   dst,src    addition with carry
SUB  / SUBW   dst,src    subtraction
SBC  / SBCW   dst,src    subtraction with carry
CP   / CPW    dst,src    compare (subtraction without result)
INC  / INCW   #3,dst     increment by 1..8 (0->8)
DEC  / DECW   #3,dst     decrement by 1..8
NEG            r          negate
EXTZ           r          zero-extend (byte->word or word->long)
EXTS           r          sign-extend
DAA            r          decimal adjust after BCD ADD/SUB
PAA            r          pointer adjust (align to even)
MUL  / MULS   RR,src     multiply 8x8->16 (S: signed)
DIV  / DIVS   RR,src     divide 16/8->quot+rem (S: signed)
                         (16-bit only: e.g. DIV WA,#8 -> quotient in W, remainder in A;
                          there is NO 32-bit hardware divide)
MULA           rr         multiply-accumulate (rr x rr -> XHL)
MINC1  #,r               modular increment +/-1 (mod #)
MINC2  #,r               modular increment +/-2
MINC4  #,r               modular increment +/-4
MDEC1  #,r               modular decrement +/-1
MDEC2  #,r               modular decrement +/-2
MDEC4  #,r               modular decrement +/-4
```

### Logic
```
AND  / ANDW   dst,src    logical AND
OR   / ORW    dst,src    logical OR
XOR  / XORW   dst,src    exclusive OR
CPL            r          one's complement (NOT)
```

### Carry operations
```
RCF            clear CF <- 0
SCF            set CF <- 1
CCF            invert CF
ZCF            CF <- NOT ZF
```

### Bit operations
```
BIT    #3/A,(mem)    test bit (sets ZF)
RES    #3/A,r/(mem)  reset bit to 0
SET    #3/A,r/(mem)  set bit to 1
CHG    #3/A,r/(mem)  invert bit
TSET   #3/A,r/(mem)  test and set to 1 (ZF <- NOT old bit)
LDCF   #3/A,r/(mem)  load bit into CF
STCF   #3/A,r/(mem)  store CF into bit
ANDCF  #3/A,r/(mem)  CF <- CF AND bit
ORCF   #3/A,r/(mem)  CF <- CF OR bit
XORCF  #3/A,r/(mem)  CF <- CF XOR bit
BS1F   A,r           find first bit=1 (forward, LSB->MSB)
BS1B   A,r           find first bit=1 (backward, MSB->LSB)
```

### Rotates / Shifts
```
RLC  #4/A, r/(mem)   rotate left with carry
RRC  #4/A, r/(mem)   rotate right with carry
RL   #4/A, r/(mem)   rotate left through CF
RR   #4/A, r/(mem)   rotate right through CF
SLA  #4/A, r/(mem)   arithmetic shift left
SRA  #4/A, r/(mem)   arithmetic shift right
SLL  #4/A, r/(mem)   logical shift left
SRL  #4/A, r/(mem)   logical shift right
RLD  [A],(mem)       BCD 4-bit rotate left  (A<-hi nib, hi nib<-lo nib, lo nib<-A)
RRD  [A],(mem)       BCD 4-bit rotate right
```

### CPU control
```
NOP              nothing
EI   [#3]        enable interrupts (level 0-7, default=7)
DI               disable interrupts (EI 07)
HALT             halt (wait for interrupt)
SWI  #3          software call (SWI 0..7, vectors in internal ROM)
INCF             increment register bank number
DECF             decrement register bank number
SCC  cc,r        r <- 1 if cc true, else 0
LINK r,d16       create stack frame (push r, sp<-sp+d16)
UNLK r           destroy stack frame (sp<-r, pop r)
```

### Jumps / Calls
```
JP   [cc,]mem    absolute jump (conditional)
JR   [cc,]$+2+d8    8-bit relative jump
JRL  [cc,]$+3+d16   16-bit relative jump
CALL [cc,]mem    absolute call
CALR $+3+d16     16-bit relative call
DJNZ [r,]$+d8   decrement r, jump if != 0 (default r=B)
RET  [cc]        return (conditional)
RETD d16         return + pop d16 bytes
RETI             return from interrupt
```

---

## 25. Conditions (cc) — Complete Table

```
Code  Mnemonic     Description
 0    F            never (false)
 1    LT           signed <     (SF!=VF)
 2    LE           signed <=    (ZF=1 or SF!=VF)
 3    ULE          unsigned <=  (CF=1 or ZF=1)
 4    PE / OV      parity even / overflow (VF=1)
 5    MI / N       negative (SF=1)
 6    EQ / Z       equal / zero (ZF=1)
 7    C / ULT      carry / unsigned < (CF=1)
 8    (none)       always (true) — omitted in mnemonics
 9    GE           signed >=    (SF=VF)
10    GT           signed >     (ZF=0 and SF=VF)
11    UGT          unsigned >   (CF=0 and ZF=0)
12    PO / NOV     parity odd / no overflow (VF=0)
13    PL / P       positive (SF=0)
14    NE / NZ      not equal / non-zero (ZF=0)
15    NC / UGE     no carry / unsigned >= (CF=0)
```

Encoded in 4 bits within the opcode byte (`cccc` field).

---

## 26. Banked Registers — r vs R Notation

### Distinction R (current) vs r (any)

```
R  = register in the CURRENT bank, encoded in 3 bits
     W A B C D E H L   (8-bit)
     WA BC DE HL IX IY IZ SP  (16-bit)
     XWA XBC XDE XHL XIX XIY XIZ XSP  (32-bit)

r  = any register, may be from any bank 0-3
     encoded in a full byte (extracted from the physical register)
```

### Full banked naming (4 banks: 0, 1, 2, 3)

8-bit (r8), bank N:
```
RA0 RW0  RC0 RB0  RE0 RD0  RL0 RH0   (bank 0)
RA1 RW1  RC1 RB1  RE1 RD1  RL1 RH1   (bank 1)
RA2 RW2  RC2 RB2  RE2 RD2  RL2 RH2   (bank 2)
RA3 RW3  RC3 RB3  RE3 RD3  RL3 RH3   (bank 3)
```
Current-bank aliases:  `A  W  B  C  D  E  H  L`
Alternate-bank aliases: `A' W' B' C' D' E' H' L'`

16-bit (r16), bank N:
```
RWA0 RBC0 RDE0 RHL0   RWA1 RBC1 RDE1 RHL1
RWA2 RBC2 RDE2 RHL2   RWA3 RBC3 RDE3 RHL3
```
Aliases: `WA BC DE HL IX IY IZ SP`

32-bit (r32), bank N:
```
XWA0 XBC0 XDE0 XHL0   XWA1 XBC1 XDE1 XHL1
XWA2 XBC2 XDE2 XHL2   XWA3 XBC3 XDE3 XHL3
```
Aliases: `XWA XBC XDE XHL XIX XIY XIZ XSP`

### In assembler usage
The "banked" names are used essentially for DMA/LDC accesses. Application code uses the
current-bank aliases (A, BC, XDE, etc.).

---

## 27. Addressing Modes — Complete Table

Encoded in the `mem` byte (prefix B0 or 80 + mem).

### Register indirect (1 prefix byte)
```
(XWA) (XBC) (XDE) (XHL) (XIX) (XIY) (XIZ) (XSP)   -> code 0x00-0x07
```

### Register indirect + signed 8-bit displacement (2 bytes)
```
(XWA+d8) (XBC+d8) ... (XSP+d8)   -> code 0x08-0x0F, then d8
```

### Absolute address
```
(#8)    -> code 0x10, then 1 byte  (zone $00-$FF, tiny IO)
(#16)   -> code 0x11, then 2 bytes (near zone)
(#24)   -> code 0x12, then 3 bytes (far zone)
```

### Arbitrary register indirect — ARI mode (2-4 bytes)
```
code 0x13, then qualifier byte:
  qualif & 0x03 == 0x00 -> (r32)             2 bytes total
  qualif & 0x03 == 0x01 -> (r32+d16)         4 bytes total
  qualif & 0x03 == 0x03 :
    qualif & 0x04 == 0    -> (r32+r8)         4 bytes total
    qualif & 0x04 != 0    -> (r32+r16)        4 bytes total
```

### Pre-decrement / Post-increment
```
(-r32)  -> code 0x14, then r32 index (pre-decrement before access)
(r32+)  -> code 0x15, then r32 index (post-increment after access)
```

---

## 28. Opcode Encoding — Fixed Sequences

### 1-byte opcodes (opcode only)
```
00        NOP
02        PUSH SR
03        POP SR
05        HALT
07        RETI
0C        INCF
0D        DECF
0E        RET
10        RCF
11        SCF
12        CCF
13        ZCF
14        PUSH A
15        POP A
16        EX F,F'
18        PUSH F
19        POP F
28+R      PUSH R16    (R=0..7 -> WA BC DE HL IX IY IZ SP)
38+R      PUSH R32    (R=0..7 -> XWA XBC XDE XHL XIX XIY XIZ XSP)
48+R      POP R16
58+R      POP R32
F8-FF     SWI 0-7
```

### 2-byte opcodes
```
06 07     DI
06 0N     EI N           (N = interrupt level 0-6)
09 n8     PUSH #8
17 0N     LDF N          (N = bank 0-7)
20+R n8   LD R8,#8       (R=0..7 -> W A B C D E H L)
30+R n16  LD R16,#16
40+R n32  LD R32,#32
60+cc d8  JR [cc,]$+2+d8
```

### 3-byte opcodes
```
0B n16    PUSHW #16
0F d16    RETD d16
1A a16    JP #16
1C a16    CALL #16
1E d16    CALR $+3+d16   (d16 signed, relative to end of instruction)
70+cc d16 JRL [cc,]$+3+d16
```

### 4-byte opcodes
```
08 a8 n8     LD (a8),#8   (tiny zone: a8 = 8-bit address)
1B a24       JP #24
1D a24       CALL #24
```

### 6-byte opcode
```
0A a8 n16    LDW (a8),#16
```

### 5-byte opcode
```
F7 00 a8 00 n8   LDX (a8),#8   (extended format: byte, addr, byte, imm)
```

### Main prefixes (followed by a sub-opcode)
```
B0+mem  ...   Memory instructions (CF, BIT, LD, LDA, JP, CALL, RET cc)
80+zz+mem ... ALU memory+register instructions (ADD, SUB, AND, OR, XOR, CP...)
C8+zz+r  ...  ALU register instructions (LD, PUSH, POP, ADD, SUB, INC, DEC...)
83/85+zz ...  Block instructions (LDI, LDD, LDIR, LDDR, CPI, CPD...)
```

---

## 29. Control Registers (CR)

Used with `LDC cr,r` / `LDC r,cr`.

```
Register   Role
DMAS0-3    DMA source, channel 0-3 (32-bit address)
DMAD0-3    DMA destination, channel 0-3 (32-bit address)
DMAC0-3    DMA control, channel 0-3 (mode flags)
DMAM0-3    DMA mode, channel 0-3 (transfer size, direction)
INTNEST    Current interrupt nesting level
```

---

## 30. Status Register (SR) — Detailed Bits

```
SR (16 bits):

Bit   Name    Description
  0   C       Carry flag
  1   N       Add/Subtract (BCD)
  2   V       Parity / Overflow
  4   H       Half Carry
  6   Z       Zero flag
  7   S       Sign flag (= bit 7 of result)
 8-10 RFP2-0  Register File Pointer (current bank 0-3)
 11   MAX     Maximum mode (always 1 on NGPC / 900/L1)
12-14 IFF2-0  Interrupt Mask Flip-Flop (mask level 0-7)
 15   SYSM    System Mode (always 1 in practice on NGPC)
```

C access via pseudo-variables:
```c
unsigned int __SF, __ZF, __VF, __CF;   // individual flags
```

ASM access: read/write via `PUSH SR` / `POP SR` or `EX F,F'`.

---

## 31. TLCS-900 CPU Variant Differences

| Feature                      | 900      | 900/L    | 900/H · 900/L1  | 900/H2   |
|------------------------------|----------|----------|-----------------|----------|
| Max address bus              | 24 bits  | <-       | <-              | <-       |
| Max data bus                 | 16 bits  | <-       | <-              | 32 bits  |
| Instruction queue            | 4 bytes  | <-       | <-              | 12 bytes |
| LDX instruction              | yes      | **no**   | yes             | **no**   |
| Micro DMA                    | 4 chan   | <-       | <-              | 8 chan   |
| CPU mode                     | MIN+MAX  | <-(MAX)  | MAX only        | <-       |
| Mode after reset             | MIN      | MAX      | MAX             | MAX      |
| Interrupt method             | Restart  | Vector   | Vector          | Vector   |
| Normal stack (XNSP)          | yes      | **no**   | yes             | <-       |
| INTNEST                      | **no**   | yes      | yes             | <-       |
| System privilege             | Sys+User | Sys only | Sys only        | <-       |

**NGPC uses TLCS-900/H (TMP95C061)** -> column "900/H · 900/L1".

---

## 32. Stack Behavior — Critical Difference TLCS-900 vs TLCS-90

### TLCS-900/L1 (and NGPC) — correct order
```
PUSH HL :  XSP <- XSP - 2      (decrement FIRST)
           (XSP)   <- L
           (XSP+1) <- H
```

### TLCS-90 — INVERSE order (compatibility broken)
```
PUSH HL :  (XSP - 1) <- H      (save FIRST)
           (XSP - 2) <- L
           XSP <- XSP - 2
```

**crt0 impact:** when porting TLCS-90 code, the direction of PUSH/POP on the stack is
inverted. NGPC uses the TLCS-900/L1 model -> decrement BEFORE save.

---

## 33. C8+ZZ+R Encoding — Complete Sub-Opcode Table

### First byte: C8+zz+r (register selector + size)

```
Formula : first_byte = 0xC8 + zz + r          (register index = first_byte & 7)
  zz = 0x00  byte size   -> range 0xC8..0xCF  (r=0..7)
  zz = 0x10  word size   -> range 0xD8..0xDF  (r=0..7)   ; WORD (16-bit) register-direct
  zz = 0x20  long size   -> range 0xE8..0xEF  (r=0..7)   ; genuine 32-bit long register

  NOTE (HW-confirmed on real NGPC 2026-07-03; ngdis): 0xD0..0xD7 is NOT part of this
  register-direct family. It is the WORD MEMORY-addressing family — the word-size sibling
  of the 0xC0..0xC7 byte memory family (see §35). It routes through the memory decoder,
  not the register decoder. Example: `D0 B6 3F 50 00` = `cpw (0xB6), 0x0050` (word compare
  of a MEMORY operand), NOT the 2-byte `sbc IZ, WA` and not any register op. The earlier
  reading of D0..D7 as a "word register prefix" — and the resulting "D0..D7 word r+r
  silicon-broken" claim — was a MIS-DECODE of memory-addressing bytes. The practical rule
  is unchanged but for a new reason: the toolchain must never emit 0xD0..0xD7 for
  word-register-immediate ops — not because those register ops are broken, but because
  0xD0..0xD7 does not encode them at all (it would decode as an unintended word-memory
  operation). Emit the word register-direct prefix 0xD8..0xDF instead. ngdis masker.h
  getzz() is authoritative: getzz(0xD8)=1 (word register), getzz(0xE8)=2 (long register);
  0xD0..0xD7 has getmem set and is handled by the memory path.

Registers r (index = first_byte & 7):
  byte  : W=0  A=1  B=2  C=3  D=4  E=5  H=6  L=7            (0xC8..0xCF)
  word  : WA=0 BC=1 DE=2 HL=3 IX=4 IY=5 IZ=6 SP=7           (0xD8..0xDF)
  long  : XWA=0 XBC=1 XDE=2 XHL=3 XIX=4 XIY=5 XIZ=6 XSP=7   (0xE8..0xEF)

Example : HL (word, index=3) -> zz=0x10 -> 0xC8+0x10+3 = 0xDB
Example : A  (byte, index=1) -> zz=0x00 -> 0xC8+0x00+1 = 0xC9
Example : D8 89 = `ld BC, WA`   (WORD 16-bit: D8=WA word src, 88+1=LD dest BC; dest
          HIGH16 preserved -> XWA=0x11223344, XBC=0xAAAAAAAA => XBC=0xAAAA3344)
Example : D9 1C = `djnz BC`     (WORD: D9=BC word, 1C=DJNZ -> XBC=0x00020000 => 0x0002FFFF)
Example : E8 89 = `ld XBC, XWA` (LONG 32-bit: E8=XWA long src, genuine 32-bit prefix)
```

### Second byte: sub-opcode (after C8+zz+r)

```
Sub-op   Instruction              Notes
00       LD   r, #              (followed by # bytes = size of r; r=dest)
03       LD   r, # (banked)     (C8+zz+r : 03 : #, followed by imm)
04       PUSH r
05       POP  r
06       CPL  r                 (r = NOT r)
07       NEG  r                 (r = 0 - r)
08       MUL  rr, #             (rr = r x #, r and # same size)
09       MULS rr, #             (signed MUL)
0A       DIV  rr, #
0B       DIVS rr, #
0C       LINK r, d16            (long only)
0E       BS1F A, r             (find bit=1, LSB->MSB)
0F       BS1B A, r             (find bit=1, MSB->LSB)
12       EXTZ r                 (zero-extend: byte->word, word->long)
13       EXTS r                 (sign-extend)
14       PAA  r                 (align r to even if r<0>=1)
1C       DJNZ r, $+3+d8         (r--, jump if r!=0; followed by signed d8)
20+#4    ANDCF #4, r            (CY <- CY AND r<#4>)
21+#4    ORCF  #4, r
22+#4    XORCF #4, r
23+#4    LDCF  #4, r            (CY <- r<#4>)
24+#4    STCF  #4, r            (r<#4> <- CY)
28       ANDCF A, r
29       ORCF  A, r
2A       XORCF A, r
2B       LDCF  A, r
2C       STCF  A, r
2E+cr    LDC  cr, r             (control register cr <- r)
2F+cr    LDC  r, cr             (r <- control register cr)
30+#4    RES  #4, r             (r<#4> <- 0; bit #4 = 0..15)
31+#4    SET  #4, r             (r<#4> <- 1)
32+#4    CHG  #4, r             (r<#4> <- NOT r<#4>)
33+#4    BIT  #4, r             (ZF <- NOT r<#4>)
34+#4    TSET #4, r             (ZF <- NOT r<#4>, then r<#4> <- 1)
38       MINC1 #-1, r           (modular increment +1)
39       MINC2 #-2, r
3A       MINC4 #-4, r
3C       MDEC1 #-1, r
3D       MDEC2 #-2, r
3E       MDEC4 #-4, r
40+R     MUL  R, r              (R = r src, R = dest x 2; ex: MUL WA,HL = DB:43)
48+R     MULS R, r              (signed)
50+R     DIV  R, r
58+R     DIVS R, r
60+#3    INC  #3, r             (r <- r + #3; #3=0 -> +8, else +1..+7)
68+#3    DEC  #3, r             (r <- r - #3; #3=0 -> -8, else -1..-7)
70+cc    SCC  cc, r             (r <- 1 if cc, else 0)
80+R     ADD  R, r              (R <- R + r; R=dest, r=src from first byte)
88+R     LD   R, r              (R <- r)
90+R     ADC  R, r
98+R     LD   r, R              (r <- R)
A0+R     SUB  R, r
A8+#3    LD   r, #3             (r <- 3-bit immediate 0..7)
B0+R     SBC  R, r
C0+R     AND  R, r
C8       ADD  r, #              (r <- r + imm; r=dest from first byte)
C9       ADC  r, #
CA       SUB  r, #
CB       SBC  r, #
CC       AND  r, #
CD       XOR  r, #
CE       OR   r, #
CF       CP   r, #              (flags only, r unchanged)
D0+R     XOR  R, r
D8+#3    CP   r, #3             (compare with 3-bit immediate)
E0+R     OR   R, r
E8+#4    RLC  #4, r
E9+#4    RRC  #4, r
EA+#4    RL   #4, r
EB+#4    RR   #4, r
EC+#4    SLA  #4, r
ED+#4    SRA  #4, r
EE+#4    SLL  #4, r
EF+#4    SRL  #4, r
F0+R     CP   R, r
F8       RLC  A, r              (rotate by A register)
F9       RRC  A, r
FA       RL   A, r
FB       RR   A, r
FC       SLA  A, r
FD       SRA  A, r
FE       SLL  A, r
FF       SRL  A, r
```

### LD (memory) encodings — B0+mem family

```
B0+mem : 00+z : #  ->  LD (mem), #    (z=0:byte, z=1:word)
B0+mem : 40+zz+R   ->  LD (mem), R    (store register to memory)
   zz=0x00 : byte store -> 40+R
   zz=0x10 : word store -> 50+R  (ex: LDW (0x8282),HL = F1 82 82 53 : F1=B0+abs16, 53=50+3)
   zz=0x20 : long store -> 60+R

80+zz+mem : 20+R   ->  LD R, (mem)   (load register from memory)
   zz=0x00, R  :  byte load
   zz=0x08, R  :  word load
   zz=0x10, R  :  long load

B0+mem : D0+cc     ->  JP  [cc,] (mem)   (conditional indirect jump)
B0+mem : E0+cc     ->  CALL [cc,] (mem)  (conditional indirect call)
B0+mem : F0+cc     ->  RET cc            (conditional return, B0=F0 -> mem=0 = no addr)

mem addresses (in B0+mem = first byte):
  B0..B7  : (XWA)..(XSP) indirect register
  B8..BF  : (XWA+d8)..(XSP+d8) register + 8-bit displacement
  F1      : abs16 (16-bit absolute) = B0 + 0x41   <- CONFIRMED
```

### zz formula — summary by family

```
Family                          byte    word    long
C8+zz+r (register prefix)       +0x00   +0x10   +0x20   [byte 0xC8..0xCF, word 0xD8..0xDF, long 0xE8..0xEF. 0xD0..0xD7 is NOT this family — it is word MEMORY (see §35).]
20+zz+R (LD R, #)                +0x00   +0x10   +0x20
18+zz+R (PUSH/POP R)             n/a     +0x10   +0x20
B0+mem:40+zz+R (LD mem,R)        +0x00   +0x10   +0x20
80+zz+mem (ALU mem)              +0x00   +0x08   +0x10
```

---

## 34. Arithmetic Encoding Examples

```asm
; LD HL, 0x0FFF  ->  33 FF 0F
;   0x30+3=33 (LD R16,#16 for HL), little-endian 0x0FFF

; INC 1, HL  ->  DB 61
;   C8+0x10+3=DB (HL word), 0x60+1=61 (INC #3=1)

; DEC 1, HL  ->  DB 69
;   C8+0x10+3=DB (HL word), 0x68+1=69 (DEC #3=1)

; ADD HL, 4   ->  DB C8 04 00
;   DB (HL word), C8 (ADD r,#16), 04 00 (imm=4 LE)

; SUB A, 3    ->  C9 CA 03
;   C9 (A byte), CA (SUB r,#8), 03

; AND HL, DE  ->  DA C3
;   DA (C8+0x10+2 = DE word src), C3 (C0+3 = AND dest=HL)

; OR  HL, BC  ->  D9 E3
;   D9 (BC word src), E3 (E0+3 = OR dest=HL)

; CP HL, 0    ->  DB CF 00 00
;   DB (HL word), CF (CP r,#16), 00 00

; NEG A       ->  C9 07
;   C9 (A byte), 07 (NEG)

; EXTZ HL     ->  DB 12
;   DB (HL word), 12 (EXTZ, zero-extend word->long)

; DJNZ B, label  ->  CA 1C disp
;   CA (C8+0+2 = B byte), 1C (DJNZ), disp = label-(PC+3)
```

---

## 35. 80+ZZ+MEM Encoding — ALU Memory-Form (Complete Table)

A family of ALU instructions that operate **directly on memory** (rather than through a
load/compute/store byte-split). Useful for ROM size: a compound-assign on a local becomes
3 bytes instead of 12-13.

### 35.1 First byte: `0x80 | (zz << 4) | mem`

```
zz : 0 = byte (B)   1 = word (W)   2 = long (L)
mem : addressing-mode selector
  mem 0..7   = ARI  (R32)        -> low4 = mem,       bit6 = 0
  mem 8..15  = ARID (R32+d8)     -> low4 = mem & 0xF, bit6 = 0
  mem 16..18 = ABS  (abs8/16/24) -> low4 = mem - 16,  bit6 = 1
  mem 19     = secondary indexed ARI  (R32+d16)/(R32+R8)/(R32+R16)
  mem 20     = ARI_PD (-R32)  pre-decrement
  mem 21     = ARI_PI (R32+)  post-increment
```

Examples (zz = word): XWA -> `0x90`, XBC -> `0x91`, XIY -> `0x95`,
XWA+d8 -> `0x98`, **XIY+d8 -> `0x9D`** (most relevant for XIY-relative locals),
XSP+d8 -> `0x9F`, abs16 -> `0xD1`.

> **Clarification (HW-confirmed 2026-07-03, ngdis):** the whole `0xD0..0xD7` row is the
> WORD MEMORY-addressing family — the word-size sibling of the `0xC0..0xC7` byte memory
> family. `0xD0`/`0xD1`/`0xD2` = word abs8/abs16/abs24, the remaining bytes = word
> register-indirect / indexed forms. There is **no** separate `0xD0..0xD7`
> word-register-direct family: the earlier "silicon-broken D0..D7 R-direct" reading was a
> mis-decode of these memory-addressing bytes. Word register-direct is `0xD8..0xDF` only
> (see §33). Example: `D0 B6 3F 50 00` = `cpw (0xB6), 0x0050`.

### 35.2 Sub-opcode (2nd byte) — `R` = low 3 bits = register index

`R` : 0 = WA/A/XWA ; 1 = BC/C/XBC ; ... ; 7 = SP/r7/XSP.

```
Read from memory (R = R OP mem)              Write to memory (mem = mem OP R)
  0x20+R  LD   R, (mem)                         0x30+R  EX  (mem), R
  0x80+R  ADD  R, (mem)                         0x88+R  ADD (mem), R     -7 B/site
  0x90+R  ADC  R, (mem)                         0x98+R  ADC (mem), R
  0xA0+R  SUB  R, (mem)                         0xA8+R  SUB (mem), R     -7 B/site
  0xB0+R  SBC  R, (mem)                         0xB8+R  SBC (mem), R
  0xC0+R  AND  R, (mem)                         0xC8+R  AND (mem), R     -7 B/site
  0xD0+R  XOR  R, (mem)                         0xD8+R  XOR (mem), R     -7 B/site
  0xE0+R  OR   R, (mem)                         0xE8+R  OR  (mem), R     -7 B/site
  0xF0+R  CP   R, (mem)  (flags only)           0xF8+R  CP  (mem), R     (flags only)
  0x40+R  MUL  RR, (mem)    0x48+R  MULS RR,(mem)
  0x50+R  DIV  RR, (mem)    0x58+R  DIVS RR,(mem)

ALU memory with immediate (no R)             INC/DEC memory by 1..8
  0x38:#  ADD (mem), #   (imm8 if byte,          0x60+n  INC #n, (mem)   n = sub-op & 7
  0x39:#  ADC             imm16 if word,                          (n=0 => 8)
  0x3A:#  SUB             imm32 if long)         0x68+n  DEC #n, (mem)   same
  0x3B:#  SBC
  0x3C:#  AND                                  Other fixed sub-ops
  0x3D:#  XOR                                    0x04 PUSH (mem)   0x06/07 RLD/RRD
  0x3E:#  OR                                     0x78..0x7F RLC/RRC/RL/RR/SLA/SRA/SLL/SRL (mem)
  0x3F:#  CP  (flags only)
```

The same sub-ops apply to the abs8 (`0xC0..0xC5`), abs16 (`0xD0..0xD5`) and abs24
(`0xE0..0xE5`) first-bytes = same operations on global variables.

### 35.3 Size-gain patterns (compound-assign on XIY-relative local)

`local += other` (u16, two XIY-relative locals) — **13 B -> 6 B**:

```asm
; legacy (13 B)                        ; memory-form (6 B)
db 0x9D, d_other, 0x23   ; LDW HL,(XIY+d_other)   db 0x9D, d_other, 0x20  ; LDW WA,(XIY+d_other)
db 0x9D, d_local, 0x20   ; LDW WA,(XIY+d_local)   db 0x9D, d_local, 0x88  ; ADDW (XIY+d_local),WA
add A, L  /  adc W, H                              ; (= -7 B)
db 0x9D, d_local, 0x50   ; LDW (XIY+d_local),WA
```

`local += imm16` — **12 B -> 5 B**: `db 0x9D, d_local, 0x38, imm_lo, imm_hi`
(ADDW (XIY+d_local), #imm16) -> -7 to -9 B/site.

`local++` / `local += 1..8` — **12-13 B -> 3 B** (best ratio):
`db 0x9D, d_local, 0x61` (INCW 1, (XIY+d_local)) -> -9 to -10 B/site.

> **Silicon safety:** the ALU memory-form family (`80..AF` + ALU sub-ops) is safe. Note
> that `0xD0..0xD7` belongs to this same word-memory family (§33 / §35.1), so it is NOT a
> broken register prefix — the old "D0..D7 R-direct broken" label was a mis-decode.
> Flags S/Z/V/H/C/N are updated
> the same as the equivalent byte-split sequence. The memory-form ALU first-bytes
> (0x99/0x91/0x88/0x89) appear frequently in disassembled commercial ROMs.

---

## 36. Branch-Free and Code-Size Idioms

A few patterns that exploit the instruction set to drop branches or bytes.

### 36.1 Conditional RET as a function-entry precondition guard

`RET cc` is a 2-byte instruction (B0+mem family, F0+cc). Used at the top of a function it
guards the body without a branch over it — cheaper than a 3-6 byte conditional jump:

```asm
        cp      E, 0x40
        ret     GE              ; 2 bytes: return early if E >= 0x40, else fall through
        ; ... body runs only when E < 0x40 ...
```

### 36.2 Sign-flip + sign-extend a signed delta (no branch, 4 bytes)

Negate a signed i8 and widen it to i16 with sign extension:

```asm
        neg     C               ; C <- -C  (signed)
        exts    BC              ; sign-extend i8 (C) -> i16 (BC)
```

### 36.3 LINK/UNLK vs PUSH/POP for locals

LINK r,d16 (4 B) + UNLK r (2 B) = 6 bytes of frame management. For <= 1 register of
locals, PUSH r / POP r (2 B total) is smaller, so LINK/UNLK are rare in optimized output.
Any r32 may serve as the frame pointer — there is no requirement to pin XIY.

### 36.4 8.8 fixed-point sub-pixel accumulate via the carry chain

For an 8.8 fixed-point value (low byte = fraction, next byte = integer), accumulate the
sub-pixel velocity and let the unsigned fraction overflow propagate into the integer part
through the carry, with no explicit carry handling:

```asm
        add     frac, velocity  ; unsigned overflow out of the fraction sets CF
        adc     integer, ...    ; carry rolls into the integer part automatically
```

---

## See Also

- [Assembly](Assembly.md) — writing TLCS-900/H assembly, assembler gotchas, ABI in practice
- [Build Toolchain](Build-Toolchain.md) — compiler rules, ABI, codegen, toolchain pipeline
- [Hardware Registers](../01_Hardware/Hardware-Registers.md) — full memory map and register addresses
- [BIOS](../01_Hardware/BIOS.md) — BIOS SWI calling convention
