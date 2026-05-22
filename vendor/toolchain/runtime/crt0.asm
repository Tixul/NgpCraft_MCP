; crt0.asm — NGPCraft Toolchain — Runtime C startup (Jalon 3)
;
; Sequence : di + watchdog + init XSP + zero BSS + copy DATA + call _main + halt
;
; Symboles linker fournis par t900ld.py v2 :
;   _Bss_START      adresse RAM debut BSS       (32-bit)
;   _Bss_SIZE       taille BSS en bytes         (16-bit)
;   _DataROM_START  adresse ROM source f_data   (32-bit)
;   _DataRAM_START  adresse RAM dest f_data     (32-bit)
;   _DataROM_SIZE   taille bloc data            (16-bit)
;   _StackTop       sommet pile (= 0x6000)      (32-bit)

    module  crt0

    f_header section code large

    public  __startup

    extern  _Bss_START
    extern  _Bss_SIZE
    extern  _DataROM_START
    extern  _DataRAM_START
    extern  _DataROM_SIZE
    extern  _StackTop
    extern  _main

; ============================================================
; __startup -- point d'entree cartouche NGPC
; ============================================================
__startup:

    ; 1. Clear watchdog (ne pas appeler DI : le BIOS NGPC utilise les interruptions
    ;    pour SYSFONTSET et d'autres SWI — DI bloquerait les appels BIOS)
    ldb (0x6F), 0x4E

    ; 2. Initialiser le pointeur de pile
    ld XSP, _StackTop           ; LD_R32_SYM patch -> 47 xx xx xx xx

    ; 3. Clear BSS (f_area) : ecrire 0 sur _Bss_SIZE bytes depuis _Bss_START
    ;
    ; DANGER : ld (XDE+), A utilise le prefixe D2 (famille D0..D7 = CASSE hardware).
    ;          ld (XHL+), A = D3, meme probleme.
    ;          Fix : utiliser XIY comme pointeur destination (prefixe ED = safe).
    ;          Store byte : db 0xBD, 0x00, 0x41 = LDB (XIY+0), A (confirme hardware J7).
    ;          Inc XIY    : db 0xED, 0x61        = inc XIY      (confirme Ganbare).
    ld  XIY, _Bss_START         ; LD_R32_SYM patch — XIY = pointeur destination BSS
    ld  WA,  _Bss_SIZE          ; LD_R16_SYM patch — WA = nombre de bytes a zeriser
    or  A,   W                  ; Z=1 ssi WA=0
    jr  Z,   .Larea_done
.Larea_loop:
    push WA                     ; sauvegarder compteur
    ld   A,  0
    db 0xBD, 0x00, 0x41         ; LDB (XIY+0), A — ecrire 0 (XIY-based, safe hardware)
    db 0xED, 0x61               ; inc XIY         — avancer pointeur (safe hardware)
    pop  WA                     ; restaurer compteur
    ld   HL, 65535              ; HL = 0xFFFF = -1 en u16 (BC/CB opcode cassé hw!)
    add  A,  L                  ; CF 81 — safe (add A,C = CB 81 = BROKEN family)
    adc  W,  H                  ; CE B0 — safe (adc W,B = CA 90 = BROKEN)
    ; FIX Bug#3 : or A,W corrompt A (A = A|W). Si W impair et A impair, point fixe
    ; → boucle infinie. Fix : push/pop WA autour du test (pop ne modifie pas les flags).
    push WA                     ; sauvegarder WA decremente avant que or A,W corrompe A
    or   A,  W                  ; Z=1 ssi WA=0 (modifie A — corrige par pop ci-dessous)
    pop  WA                     ; restaurer WA sans toucher les flags (Z reste valide)
    jr   NZ, .Larea_loop
.Larea_done:

    ; 4. Copier f_data ROM -> RAM
    ; DANGER : ld A, (XHL+) = prefixe D3 (CASSE). ld (XDE+), A = D2 (CASSE).
    ;          Fix : XIZ = source ROM (EE prefix), XIY = dest RAM (ED prefix).
    ;          Load byte  : db 0x8E, 0x00, 0x21 = LDB A, (XIZ+0)
    ;          Inc XIZ    : db 0xEE, 0x61        = inc XIZ
    ;          Store byte : db 0xBD, 0x00, 0x41  = LDB (XIY+0), A
    ;          Inc XIY    : db 0xED, 0x61         = inc XIY
    ld  XIZ, _DataROM_START     ; LD_R32_SYM patch — XIZ = source ROM
    ld  XIY, _DataRAM_START     ; LD_R32_SYM patch — XIY = destination RAM
    ld  WA,  _DataROM_SIZE      ; LD_R16_SYM patch
    or  A,   W                  ; Z=1 ssi WA=0
    jr  Z,   .Ldata_done
.Ldata_loop:
    push WA                     ; sauvegarder compteur
    db 0x8E, 0x00, 0x21         ; LDB A, (XIZ+0) — lire octet ROM (XIZ-based, safe)
    db 0xEE, 0x61               ; inc XIZ         — avancer source
    db 0xBD, 0x00, 0x41         ; LDB (XIY+0), A  — ecrire octet RAM (XIY-based, safe)
    db 0xED, 0x61               ; inc XIY          — avancer destination
    pop  WA                     ; restaurer compteur
    ld   HL, 65535              ; HL = 0xFFFF (BC/CB opcode cassé hw!)
    add  A,  L                  ; CF 81 — safe
    adc  W,  H                  ; CE B0 — safe
    ; FIX Bug#3 : or A,W corrompt A (A = A|W). Si W impair et A impair, point fixe
    ; → boucle infinie (ex: DataROM_SIZE=0x010D : W=1,A=0x0D → 0x0C|0x01=0x0D forever).
    ; Fix : push/pop WA autour du test (pop ne modifie pas les flags en TLCS-900H).
    push WA                     ; sauvegarder WA decremente avant que or A,W corrompe A
    or   A,  W                  ; Z=1 ssi WA=0 (modifie A — corrige par pop ci-dessous)
    pop  WA                     ; restaurer WA sans toucher les flags (Z reste valide)
    jr   NZ, .Ldata_loop
.Ldata_done:

    ; 5. Appeler main() — EI laisse a la charge du code C si besoin
    ;    (ne pas activer EI ici : vecteurs non configures -> crash VBlank)
    call _main                  ; CALL_ABS24 patch

    ; 6. Boucle halt (main() ne devrait pas retourner)
.Lhalt:
    ldb (0x6F), 0x4E
    jp  .Lhalt

    end
