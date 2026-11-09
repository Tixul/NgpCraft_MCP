/*
 * ngpc_rtc.c - Real-Time Clock access
 *
 * Part of NgpCraft_base_template (MIT License)
 * Written from hardware specification (ngpcspec.txt).
 *
 * BIOS call convention for TLCS-900/H:
 *   1. Load vector number into RW3 (register W, bank 3)
 *   2. Load parameters into bank 3 registers
 *   3. Switch to bank 3 (ldf 3)
 *   4. W = W * 4 (index into vector table)
 *   5. Load function address from table at 0xFFFE00
 *   6. Call the function
 *
 * All date/time values are BCD-encoded by the BIOS.
 */

#include "ngpc_hw.h"
#include "ngpc_rtc.h"

/* ---- Public API ---- */

void ngpc_rtc_get(NgpcTime *t)
{
    /*
     * BIOS vector 2 (RTCGET): read date/time.
     * Input:  XHL3 = pointer to TIME struct (7 bytes)
     * Output: struct filled with BCD values.
     *
     * The pointer is read from the stack straight into XHL3 once bank 3
     * is selected.
     */
    /* corrigé campagne 2026-11-08 : XIX sauvé (cc900 le croit conservé par
     * l'appelé) ; arguments lus apres `ldf 3` (la pile ne depend pas de la banque). */
    __asm(" push xix");
    __asm(" ldf 3");                /* switch to register bank 3 */
    __asm(" ld w, " NGPC_STR(BIOS_RTCGET));
    __asm(" ld xhl, (xsp+8)");     /* NgpcTime* -> XHL3 (+4 : push xix) */
    __asm(" add w, w");             /* w *= 2 */
    __asm(" add w, w");             /* w *= 2 (total: w *= 4) */
    __asm(" ld xix, 0xfffe00");    /* BIOS vector table base */
    __asm(" ld xix, (xix+w)");     /* load function address */
    __asm(" call xix");             /* call BIOS */
    __asm(" pop xix");
}

void ngpc_rtc_set_alarm(NgpcAlarm *a)
{
    /*
     * BIOS vector 9 (ALARMSET): set alarm during operation.
     * Input: QC3 = day, B3 = hour, C3 = minute
     * Triggers RTC alarm interrupt at 0x6FC8.
     */
    /* corrigé campagne 2026-11-08 : XIX/XIY sauvés (voir ngpc_rtc_get). */
    __asm(" push xix");
    __asm(" push xiy");
    __asm(" ldf 3");                /* switch to register bank 3 */
    __asm(" ld w, " NGPC_STR(BIOS_ALARMSET));
    __asm(" ld xiy, (xsp+12)");    /* NgpcAlarm* (+8 : 2 push) */
    __asm(" ld h, (xiy +)");       /* day */
    __asm(" ld qc, h");            /* day -> QC register */
    __asm(" ld b, (xiy +)");       /* hour */
    __asm(" ld c, (xiy +)");       /* minute */

    __asm(" add w, w");
    __asm(" add w, w");
    __asm(" ld xix, 0xfffe00");
    __asm(" ld xix, (xix+w)");
    __asm(" call xix");
    __asm(" pop xiy");
    __asm(" pop xix");
}

void ngpc_rtc_set_wake(NgpcAlarm *a)
{
    /*
     * BIOS vector 11 (ALARMDOWNSET): set power-on alarm.
     * Same register convention as ALARMSET.
     * This will power on the console at the given date/time,
     * even if it's currently off.
     */
    /* corrigé campagne 2026-11-08 : XIX/XIY sauvés (voir ngpc_rtc_get). */
    __asm(" push xix");
    __asm(" push xiy");
    __asm(" ldf 3");
    __asm(" ld w, " NGPC_STR(BIOS_ALARMDOWNSET));
    __asm(" ld xiy, (xsp+12)");    /* NgpcAlarm* (+8 : 2 push) */
    __asm(" ld h, (xiy +)");       /* day */
    __asm(" ld qc, h");
    __asm(" ld b, (xiy +)");       /* hour */
    __asm(" ld c, (xiy +)");       /* minute */

    __asm(" add w, w");
    __asm(" add w, w");
    __asm(" ld xix, 0xfffe00");
    __asm(" ld xix, (xix+w)");
    __asm(" call xix");
    __asm(" pop xiy");
    __asm(" pop xix");
}

void ngpc_rtc_set_alarm_handler(void (*handler)(void))
{
    /* Install at RTC alarm interrupt vector (0x6FC8). */
    HW_INT_RTC = (IntHandler *)handler;
}
