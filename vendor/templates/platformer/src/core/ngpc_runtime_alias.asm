$MAXIMUM

; cc900 appelle ses aides 32 bits avec l'operande gauche dans XWA, la droite
; dans XBC, et attend le resultat dans XHL ; les aides Toshiba ne modifient que
; XWA/XBC/XDE/XHL (XIX/XIY/XIZ intacts, les appelants comptent dessus).
; Celles de ngpc_runtime.c sont des fonctions C : elles lisent leurs arguments
; sur la PILE et peuvent utiliser XIX/XIY. L'ancien `jp _C9H_xxx` leur faisait
; donc lire l'adresse de retour : tout `*`, `/`, `%` 32 bits non reduit etait
; faux (0x12345678 * 155 donnait E1056A00 ; ngpc_random hors bornes).
; Chaque pont sauve XIX/XIY, pousse les operandes dans l'ordre C (XWA = 1er
; parametre), appelle la fonction C, depile et restaure. (2026-11-03)

        module  ngpc_runtime_alias

        public  C9H_mullu, C9H_divlu, C9H_remlu
        extern  large _C9H_mullu, _C9H_divlu, _C9H_remlu

ALIAS   section code large

C9H_mullu:
        push    XIX
        push    XIY
        push    XBC
        push    XWA
        cal     _C9H_mullu
        lda     XSP,XSP+8
        pop     XIY
        pop     XIX
        ret

C9H_divlu:
        push    XIX
        push    XIY
        push    XBC
        push    XWA
        cal     _C9H_divlu
        lda     XSP,XSP+8
        pop     XIY
        pop     XIX
        ret

C9H_remlu:
        push    XIX
        push    XIY
        push    XBC
        push    XWA
        cal     _C9H_remlu
        lda     XSP,XSP+8
        pop     XIY
        pop     XIX
        ret

        end
