; TLCS-900 C Compiler - Code Generator (32) Version 1.8n.06
; (C)Copyright TOSHIBA CORPORATION 1989-2009  All rights reserved
	$MAXIMUM
	module src_game_shmup_profile_c
f_data section data large align=2,2
_s_save_dirty:
	db 0
f_code section code large align=1,1
_save_entry_score_get:
	ld	XWA,(XSP+0x4)
	ld	A,(XWA+0x5)
	extz	WA
	ld	BC,WA
	sll	0x8,BC
	ld	XWA,(XSP+0x4)
	ld	A,(XWA+0x4)
	extz	WA
	ld	HL,WA
	or	HL,BC
	ret
_save_entry_score_set:
	ld	XWA,(XSP+0x4)
	ld	BC,(XSP+0x8)
	ld	B,0x0
	ld	(XWA+0x4),C
	ld	XWA,(XSP+0x4)
	ld	BC,(XSP+0x8)
	srl	0x8,BC
	ld	(XWA+0x5),C
	ret
_save_checksum_compute:
	ld	XDE,(XSP+0x4)
	ld	L,0x0
	ld	BC,0x0
	cp	BC,0x1ff                  	;	511
	j	uge,L1
L2:  ;2 
	ld	WA,BC
	extz	XWA
	add	XWA,XDE
	add	L,(XWA)
	inc	0x1,BC
	cp	BC,0x1ff                  	;	511
	j	ult,L2
L1:  ;2 
	ld	XWA,(XSP+0x4)
	sub	L,(XWA+0x58)
	xor	L,0x5a                   	;	01011010b
	ret
_save_defaults_set:
	push	QIZ
	ldb	(_s_save),0xca           	;	202
	ldb	(_s_save + 0x1),0xfe     	;	254
	ldb	(_s_save + 0x2),0x20     	;	32
	ldb	(_s_save + 0x3),0x26     	;	38
	ldb	(_s_save + 0x4),0x1
	ldb	(_s_save + 0x5),0x3
	ldb	(_s_save + 0x6),0x0
	ldb	(_s_save + 0x7),0x0
	ld	QIZH,0x0
	cp	QIZH,0xa                  	;	10
	j	uge,L3
L4:  ;2 
	ld	A,QIZH
	extz	WA
	sla	0x3,WA
	lda	XBC,_s_save + 0x8
	ldb	(XBC+WA),0x2d            	;	45
	ld	A,QIZH
	extz	WA
	sla	0x3,WA
	inc	0x8,WA
	lda	XBC,_s_save + 0x1
	ldb	(XBC+WA),0x2d            	;	45
	ld	A,QIZH
	extz	WA
	sla	0x3,WA
	inc	0x8,WA
	lda	XBC,_s_save + 0x2
	ldb	(XBC+WA),0x2d            	;	45
	pushw	0x0
	ld	A,QIZH
	extz	WA
	sla	0x3,WA
	lda	XBC,_s_save + 0x8
	exts	XWA
	add	XWA,XBC
	push	XWA
	cal	_save_entry_score_set
	inc	0x6,XSP
	ld	A,QIZH
	extz	WA
	sla	0x3,WA
	inc	0x8,WA
	lda	XBC,_s_save + 0x6
	ldb	(XBC+WA),0x0
	inc	0x1,QIZH
	cp	QIZH,0xa                  	;	10
	j	ult,L4
L3:  ;2 
	ld	QIZH,0x0
	cp	QIZH,0xbb                 	;	187
	j	uge,L5
L6:  ;2 
	ld	A,QIZH
	extz	WA
	lda	XBC,_s_save + 0x5a
	ldb	(XBC+WA),0x0
	inc	0x1,QIZH
	cp	QIZH,0xbb                 	;	187
	j	ult,L6
L5:  ;2 
	lda	XWA,_s_save
	push	XWA
	cal	_save_checksum_compute
	inc	0x4,XSP
	ld	(_s_save + 0x58),L
	pop	QIZ
	ret
_save_is_valid:
	push	XIZ
	ld	XIZ,(XSP+0x8)
	cpb	(XIZ),0xca               	;	202
	j	ne,L7
	cpb	(XIZ+0x1),0xfe           	;	254
	j	ne,L7
	cpb	(XIZ+0x2),0x20           	;	' ' 32
	j	ne,L7
	cpb	(XIZ+0x3),0x26           	;	'&' 38
	j	eq,L8
L7:  ;4 
	ld	L,0x0
	j	L50000
L8:  ;1 
	cpb	(XIZ+0x4),0x1
	j	eq,L11
	ld	L,0x0
	j	L50000
L11:  ;1 
	cpb	(XIZ+0x5),0xa            	;	10
	j	ule,L12
	ld	L,0x0
	j	L50000
L12:  ;1 
	push	XIZ
	cal	_save_checksum_compute
	inc	0x4,XSP
	cp	(XIZ+0x58),L
	j	eq,L13
	ld	L,0x0
	j	L50000
L13:  ;1 
	ld	L,0x1
L50000:  ;5 
	pop	XIZ
	ret
_save_commit:
	lda	XWA,_s_save
	push	XWA
	cal	_save_checksum_compute
	ld	(_s_save + 0x58),L
	lda	XWA,_s_save
	push	XWA
	cal	_ngpc_flash_save
	inc	0x8,XSP
	ldb	(_s_save_dirty),0x0
	ret
	public _shmup_profile_init
_shmup_profile_init:
	cal	_ngpc_flash_init
	cal	_ngpc_flash_exists
	cp	L,0x0
	j	eq,L14
	lda	XWA,_s_save
	push	XWA
	cal	_ngpc_flash_load
	lda	XWA,_s_save
	push	XWA
	cal	_save_is_valid
	inc	0x8,XSP
	cp	L,0x0
	j	eq,L14
	ldb	(_s_save_dirty),0x0
	ret
L14:  ;2 
	cal	_save_defaults_set
	ldb	(_s_save_dirty),0x0
	ret
	public _shmup_profile_continue_setting_get
_shmup_profile_continue_setting_get:
	ld	L,(_s_save + 0x5)
	ret
	public _shmup_profile_continue_setting_set
_shmup_profile_continue_setting_set:
	ld	A,(XSP+0x4)
	cp	A,0xa                     	;	10
	j	ule,L16
	ld	A,0xa                     	;	10
L16:  ;2 
	cp	(_s_save + 0x5),A
	ret	eq
L17:  ;1 
	ld	(_s_save + 0x5),A
	ldb	(_s_save_dirty),0x1
	ret
	public _shmup_profile_flush
_shmup_profile_flush:
	cpb	(_s_save_dirty),0x0
	ret	eq
L18:  ;1 
	j	_save_commit
	public _shmup_profile_highscore_get
_shmup_profile_highscore_get:
	push	XIZ
	ld	XIZ,(XSP+0xa)
	ld	C,(XSP+0x8)
	or	XIZ,XIZ
	j	eq,L50001
L19:  ;1 
	cp	C,0xa                     	;	10
	j	ult,L20
	ldb	(XIZ),0x2d               	;	45
	ldb	(XIZ+0x1),0x2d           	;	45
	ldb	(XIZ+0x2),0x2d           	;	45
	ldb	(XIZ+0x3),0x0
	ldw	(XIZ+0x4),0x0
	j	L50001
L20:  ;1 
	ld	A,C
	extz	WA
	sla	0x3,WA
	lda	XDE,_s_save + 0x8
	ld	A,(XDE+WA)
	ld	(XIZ),A
	ld	A,C
	extz	WA
	sla	0x3,WA
	inc	0x8,WA
	lda	XDE,_s_save + 0x1
	ld	A,(XDE+WA)
	ld	(XIZ+0x1),A
	ld	A,C
	extz	WA
	sla	0x3,WA
	inc	0x8,WA
	lda	XDE,_s_save + 0x2
	ld	A,(XDE+WA)
	ld	(XIZ+0x2),A
	ldb	(XIZ+0x3),0x0
	ld	A,C
	extz	WA
	sla	0x3,WA
	lda	XBC,_s_save + 0x8
	exts	XWA
	add	XWA,XBC
	push	XWA
	cal	_save_entry_score_get
	inc	0x4,XSP
	ld	(XIZ+0x4),HL
L50001:  ;3 
	pop	XIZ
	ret
	public _shmup_profile_highscore_qualifies
_shmup_profile_highscore_qualifies:
	lda	XWA,_s_save + 0x50
	push	XWA
	cal	_save_entry_score_get
	inc	0x4,XSP
	cp	(XSP+0x4),HL
	scc	uge,HL
	ret
	public _shmup_profile_highscore_submit
_shmup_profile_highscore_submit:
	lda	XSP,XSP-0xa
	push	QIZ
	ldb	(XSP+0x2),0xa            	;	10
	ld	QIZH,0x0
	cp	QIZH,0xa                  	;	10
	j	uge,L21
L22:  ;2 
	ld	A,QIZH
	extz	WA
	sla	0x3,WA
	lda	XBC,_s_save + 0x8
	exts	XWA
	add	XWA,XBC
	push	XWA
	cal	_save_entry_score_get
	inc	0x4,XSP
	cp	(XSP+0x14),HL
	j	ult,L23
	ld	A,QIZH
	ld	(XSP+0x2),A
	j	L21
L23:  ;1 
	inc	0x1,QIZH
	cp	QIZH,0xa                  	;	10
	j	ult,L22
L21:  ;3 
	cpb	(XSP+0x2),0xa            	;	10
	j	ult,L24
	ld	L,0x0
	j	L50002
L24:  ;1 
	ld	XWA,(XSP+0x10)
	ld	A,(XWA)
	ld	(XSP+0x4),A
	ld	XWA,(XSP+0x10)
	ld	A,(XWA+0x1)
	ld	(XSP+0x5),A
	ld	XWA,(XSP+0x10)
	ld	A,(XWA+0x2)
	ld	(XSP+0x6),A
	pushw	(XSP+0x14)
	lda	XWA,XSP+0x6
	push	XWA
	cal	_save_entry_score_set
	inc	0x6,XSP
	ldb	(XSP+0xa),0x0
	ld	QIZH,0x9
	ld	A,QIZH
	cp	A,(XSP+0x2)
	j	ule,L25
L26:  ;2 
	ld	A,QIZH
	extz	WA
	ld	DE,WA
	sla	0x3,DE
	lda	XHL,_s_save + 0x8
	ld	A,QIZH
	dec	0x1,A
	extz	WA
	sla	0x3,WA
	lda	XBC,_s_save + 0x8
	lda	XIY,XBC+WA
	lda	XIX,XHL+DE
	ld	BC,0x4
	ldirw	(XIX+),(XIY+)
	dec	0x1,QIZH
	ld	A,QIZH
	cp	A,(XSP+0x2)
	j	ugt,L26
L25:  ;2 
	ld	A,(XSP+0x2)
	extz	WA
	sla	0x3,WA
	lda	XDE,_s_save + 0x8
	lda	XIY,XSP+0x4
	lda	XIX,XDE+WA
	ld	BC,0x4
	ldirw	(XIX+),(XIY+)
	ldb	(_s_save_dirty),0x1
	cal	_save_commit
	ld	L,0x1
L50002:  ;2 
	pop	QIZ
	lda	XSP,XSP+0xa
	ret
	extern large _ngpc_flash_init,_ngpc_flash_save,_ngpc_flash_load,_ngpc_flash_exists
f_area section data large align=2,2
_s_save:
	dsb 534
	end
