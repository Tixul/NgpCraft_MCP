"""The hardware registers, decoded field by field.

The memory viewer can already show you that `0x8118` holds `0x87`. What it cannot
tell you is that this means "backdrop enabled, palette 7" -- and that `0x07` in the
same byte means "backdrop OFF, and the picture goes black" even though only one bit
moved. Every register on this machine packs several unrelated switches into one
byte, so reading them as hex is reading them in a language nobody thinks in.

This module is the dictionary. It is deliberately:

  * **pure** -- no Qt, no numpy, no core. It takes a `read(addr, n) -> bytes`
    callable, so it is testable against a plain dict and reusable from a script.
  * **sourced** -- every register carries `source`, naming the document that
    defines it. Where nothing authoritative exists the source says REVERSE, and
    that is not the same claim (`HARDWARE_COMPAT_POLICY.md`: three of our
    "hardware findings" were wrong until the manufacturer documents were read).
  * **honest about levels** -- an interrupt level of 0 means the source is
    DISABLED, which is the single fact that cost this project a 69->56 corpus
    regression when it was assumed instead of read (`cpp/src/machine.hpp`).

`checks()` is the part that earns its keep: a handful of cross-register conditions
the documents state explicitly as illegal or as "the picture goes black". They are
not opinions -- each one quotes the sentence that makes it a defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Callable

# ---------------------------------------------------------------- sources
# Short tags so a register can say where it comes from without a paragraph.
SPEC = "SDK ngpcspec.txt"
TIMERS_DOC = "SDK 8Bit.txt"
DATASHEET = "TMP95C061 datasheet"
REVERSE = "REVERSE (no manufacturer doc)"


@dataclass(frozen=True)
class Field:
    """One run of bits inside a register.

    `values` maps the raw field value to what it MEANS; `fmt` handles the cases a
    table cannot express (an interrupt level, a DMA vector index). A field with
    neither just shows its number, which is right for a coordinate or a counter.
    """

    name: str
    hi: int
    lo: int
    values: dict[int, str] | None = None
    fmt: Callable[[int], str] | None = None
    note: str = ""

    def extract(self, raw: int) -> int:
        width = self.hi - self.lo + 1
        return (raw >> self.lo) & ((1 << width) - 1)

    def bits_label(self) -> str:
        return f"b{self.hi}" if self.hi == self.lo else f"b{self.hi}-{self.lo}"

    def describe(self, value: int) -> str:
        if self.fmt is not None:
            return self.fmt(value)
        if self.values is not None:
            return self.values.get(value, "?")
        return str(value)


@dataclass(frozen=True)
class Reg:
    addr: int
    name: str
    summary: str
    source: str
    fields: tuple[Field, ...] = ()
    width: int = 1          # bytes; 2 = little-endian word
    reset: int | None = None


@dataclass(frozen=True)
class Group:
    name: str
    note: str
    regs: tuple[Reg, ...] = _dc_field(default_factory=tuple)


# ---------------------------------------------------------------- helpers
def _level(v: int) -> str:
    """An interrupt-enable nibble.

    Bits 2-0 are the priority level and **0 means the source is disabled** --
    Toshiba's levels run 1..7. Bit 3 is the request-clear bit, which reads back as
    part of the same nibble, so it is shown rather than hidden.
    """
    lvl = v & 7
    clear = " +clear" if v & 8 else ""
    return (f"level {lvl}{clear}" if lvl else f"DISABLED (level 0){clear}")


# Vector indices a micro-DMA start register can name. From the TMP95C061 vector
# table (Table 3.3 (1)) as already mapped in cpp/src/machine.hpp.
_DMA_VECTORS = {
    0x00: "off",
    0x08: "INT0 (power button)",
    0x0A: "RTC alarm",
    0x0B: "INT4 / VBlank",
    0x0C: "INT5 (from Z80)",
    0x10: "INTT0 (timer 0)",
    0x11: "INTT1 (timer 1)",
    0x12: "INTT2 (timer 2)",
    0x13: "INTT3 (timer 3)",
    0x18: "serial receive",
    0x19: "serial transmit",
}


def _dma_vector(v: int) -> str:
    return _DMA_VECTORS.get(v, f"vector index 0x{v:02X}")


_T0CLK = {0: "external TI0", 1: "T1", 2: "T4", 3: "T16"}
_T1CLK = {0: "timer-0 comparator / overflow (16-bit cascade)",
          1: "T1", 2: "T16", 3: "T256"}
_T2CLK = {0: "--", 1: "T1", 2: "T4", 3: "T16"}
_TMODE = {0: "two 8-bit timers", 1: "16-bit timer",
          2: "8-bit PPG (square wave)", 3: "8-bit PWM + 8-bit timer"}
_PWMCYC = {0: "--", 1: "2^6-1", 2: "2^7-1", 3: "2^8-1"}
_ON_OFF = {0: "stopped", 1: "running"}

# The joypad byte's layout -- the same one at the hardware port 0x00B0 and in the
# BIOS's copy at 0x6F82. ONE definition, because two would drift: this table was
# first written from memory with A/B/Option a bit too high, which mislabels three
# buttons in a way nothing else in the emulator would ever contradict.
# `ngpc_input.py` builds the byte the console actually receives, and a test holds
# the two together.
JOYPAD_BITS = (("Up", 0), ("Down", 1), ("Left", 2), ("Right", 3),
               ("A", 4), ("B", 5), ("Option", 6))
JOYPAD_POWER_BIT = 7        # not a button: 0x80 is POWER
JOYPAD_BUTTON_MASK = (1 << JOYPAD_POWER_BIT) - 1

# The sound-CPU side of the I/O page, and the RAM the two processors share. These
# live HERE and are imported by `core/z80_debug.py` rather than written down twice:
# the same reasoning as JOYPAD_BITS above, applied before it could go wrong.
T6W28_RESET = 0x0000B8      # 0x55 = sound chip out of reset
Z80_RESET = 0x0000B9        # 0x55 = sound CPU running
Z80_NMI = 0x0000BA          # any write = one NMI at the sound CPU
Z80_COMM = 0x0000BC         # dual-port byte both CPUs can see
LINK_PORT = 0x0000B1
SHARED_RAM = 0x007000       # what the Z80 sees at its own 0x0000
SHARED_RAM_SIZE = 0x1000


# ---------------------------------------------------------------- the map
VIDEO = Group(
    "Video (K2GE)",
    "The 2D controller. Every one of these takes effect on the NEXT LINE drawn, "
    "not the next frame -- which is what makes mid-frame raster tricks possible "
    "and mid-frame writes dangerous.",
    (
        Reg(0x008000, "INT_CTL", "Frame/line interrupt enables", REVERSE, (
            Field("V_INT", 7, 7, {0: "off", 1: "enabled"}),
            Field("H_INT", 6, 6, {0: "off", 1: "enabled"}),
        ), reset=0xC0),
        Reg(0x008002, "WBA.H", "Window origin X", SPEC, reset=0x00),
        Reg(0x008003, "WBA.V", "Window origin Y", SPEC, reset=0x00),
        Reg(0x008004, "WSI.H", "Window width", SPEC, reset=0xFF),
        Reg(0x008005, "WSI.V", "Window height", SPEC, reset=0xFF),
        Reg(0x008006, "REF", "Frame rate / blanking period (locked: do not change)",
            SPEC, reset=0xC6),
        Reg(0x008008, "RAS.H", "Raster X -- counts DOWN through the 515-clock line",
            SPEC),
        Reg(0x008009, "RAS.V", "Raster Y -- current line (0..198)", SPEC),
        Reg(0x008010, "2D status", "What the chip is doing right now", SPEC, (
            Field("C.OVR", 7, 7, {0: "ok", 1: "CHARACTER OVER (sprites dropped)"},
                  note="cleared at the end of V blank"),
            Field("BLNK", 6, 6, {0: "displaying", 1: "V blanking"}),
        )),
        Reg(0x008012, "2D control", "Inversion and the colour outside the window",
            SPEC, (
                Field("NEG", 7, 7, {0: "normal", 1: "INVERTED display"}),
                Field("OOWC", 2, 0, fmt=lambda v: f"backdrop palette entry {v}",
                      note="outside-window colour; reads the WINDOW palette at 0x83F0"),
            )),
        Reg(0x008020, "PO.H", "Sprite global X offset (added to every sprite)", SPEC),
        Reg(0x008021, "PO.V", "Sprite global Y offset (added to every sprite)", SPEC),
        Reg(0x008030, "Plane priority", "Which scroll plane is in front", SPEC, (
            Field("P.F", 7, 7, {0: "plane 1 in front", 1: "plane 2 in front"}),
        )),
        Reg(0x008032, "S1SO.H", "Plane 1 scroll X", SPEC),
        Reg(0x008033, "S1SO.V", "Plane 1 scroll Y", SPEC),
        Reg(0x008034, "S2SO.H", "Plane 2 scroll X", SPEC),
        Reg(0x008035, "S2SO.V", "Plane 2 scroll Y", SPEC),
        Reg(0x008118, "BGC", "Backdrop colour control", SPEC, (
            Field("BGON", 7, 6, {0: "off", 1: "off (invalid)",
                                 2: "ON (b7=1,b6=0)", 3: "off (invalid)"},
                  note="only b7=1,b6=0 is the enabled encoding"),
            Field("BGC", 2, 0, fmt=lambda v: f"backdrop palette entry {v}"),
        ), reset=0x80),
        Reg(0x0087E2, "MODE", "K2GE colour vs K1GE compatibility", SPEC, (
            Field("MODE", 7, 7, {0: "K2GE colour", 1: "K1GE upper-palette compat"}),
        )),
        Reg(0x008400, "LED", "Power LED", REVERSE, reset=0xFF),
    ),
)

TIMERS = Group(
    "8-bit timers",
    "Four 8-bit timers off a 9-bit prescaler (fc/4). Timer 0 is the raster timer "
    "in practice: games clock it off TI0, which the video chip pulses one line "
    "before each drawn line.",
    (
        Reg(0x000020, "TRUN", "Run/stop per timer, plus the prescaler", TIMERS_DOC, (
            Field("PRRUN", 7, 7, _ON_OFF,
                  note="stopping the prescaler is PROHIBITED (breaks serial)"),
            Field("T3RUN", 3, 3, _ON_OFF),
            Field("T2RUN", 2, 2, _ON_OFF),
            Field("T1RUN", 1, 1, _ON_OFF),
            Field("T0RUN", 0, 0, _ON_OFF),
        )),
        Reg(0x000022, "TREG0", "Timer 0 compare value (write-only on hardware)",
            TIMERS_DOC),
        Reg(0x000023, "TREG1", "Timer 1 compare value", TIMERS_DOC),
        Reg(0x000024, "T01MOD", "Timers 0/1 mode and clock sources", TIMERS_DOC, (
            Field("T01M", 7, 6, _TMODE),
            Field("PWM0", 5, 4, _PWMCYC, note="don't-care outside PWM mode"),
            Field("T1CLK", 3, 2, _T1CLK),
            Field("T0CLK", 1, 0, _T0CLK),
        )),
        Reg(0x000026, "TREG2", "Timer 2 compare value", TIMERS_DOC),
        Reg(0x000027, "TREG3", "Timer 3 compare value", TIMERS_DOC),
        Reg(0x000028, "T23MOD", "Timers 2/3 mode and clock sources", TIMERS_DOC, (
            Field("T23M", 7, 6, _TMODE),
            Field("PWM2", 5, 4, _PWMCYC, note="don't-care outside PWM mode"),
            Field("T3CLK", 3, 2, _T1CLK),
            Field("T2CLK", 1, 0, _T2CLK),
        )),
    ),
)

INTERRUPTS = Group(
    "Interrupt levels",
    "Each nibble is one source's priority, and LEVEL 0 MEANS DISABLED. The CPU "
    "accepts an interrupt when its level >= the SR mask. Software rewrites these "
    "constantly -- the BIOS leaves INTE45 = 0xDC, and every game measured "
    "replaces it with 0x32.",
    (
        Reg(0x000070, "INTE0AD", "INT0 / RTC alarm (low) + A/D converter (high)",
            DATASHEET, (
                Field("INTAD", 7, 4, fmt=_level),
                Field("INT0", 3, 0, fmt=_level,
                      note="INT0 is the POWER BUTTON; the same nibble carries the RTC alarm"),
            )),
        Reg(0x000071, "INTE45", "INT4 = VBlank (low) + INT5 = from the Z80 (high)",
            DATASHEET, (
                Field("INT5", 7, 4, fmt=_level, note="the sound CPU interrupting the main one"),
                Field("INT4", 3, 0, fmt=_level, note="VBlank"),
            )),
        Reg(0x000073, "INTET01", "Timer 0 (low) + timer 1 (high)", DATASHEET, (
            Field("INTT1", 7, 4, fmt=_level),
            Field("INTT0", 3, 0, fmt=_level),
        )),
        Reg(0x000074, "INTET23", "Timer 2 (low) + timer 3 (high)", DATASHEET, (
            Field("INTT3", 7, 4, fmt=_level),
            Field("INTT2", 3, 0, fmt=_level),
        )),
        Reg(0x000077, "INTES0", "Serial channel 0 -- the link cable", DATASHEET, (
            Field("INTRX0", 7, 4, fmt=_level, note="vector 0x18"),
            Field("INTTX0", 3, 0, fmt=_level, note="vector 0x19"),
        )),
        Reg(0x000079, "INTETC01", "Micro-DMA channels 0/1 transfer end", DATASHEET, (
            Field("INTTC1", 7, 4, fmt=_level),
            Field("INTTC0", 3, 0, fmt=_level),
        )),
        Reg(0x00007A, "INTETC23", "Micro-DMA channels 2/3 transfer end", DATASHEET, (
            Field("INTTC3", 7, 4, fmt=_level),
            Field("INTTC2", 3, 0, fmt=_level),
        )),
    ),
)

MICRO_DMA = Group(
    "Micro-DMA start vectors",
    "An interrupt whose vector index is written here is serviced by DMA and NEVER "
    "vectors the CPU. This is how raster scroll works without a single "
    "instruction -- and why a core that delivers such an interrupt normally sends "
    "the game into a stub nobody installed.",
    (
        Reg(0x00007C, "DMA0V", "Channel 0 trigger", DATASHEET,
            (Field("vector", 7, 0, fmt=_dma_vector),)),
        Reg(0x00007D, "DMA1V", "Channel 1 trigger", DATASHEET,
            (Field("vector", 7, 0, fmt=_dma_vector),)),
        Reg(0x00007E, "DMA2V", "Channel 2 trigger", DATASHEET,
            (Field("vector", 7, 0, fmt=_dma_vector),)),
        Reg(0x00007F, "DMA3V", "Channel 3 trigger", DATASHEET,
            (Field("vector", 7, 0, fmt=_dma_vector),)),
    ),
)

WATCHDOG = Group(
    "Watchdog",
    "The BIOS kicks it at the top of every VBlank. A game that stops kicking is "
    "reset by hardware -- which looks exactly like a crash if you are not "
    "watching this byte.",
    (
        Reg(0x00006E, "WDMOD", "Watchdog mode", DATASHEET, (
            Field("WDTE", 7, 7, {0: "disabled", 1: "enabled"}),
            Field("sleep marker", 4, 4, {0: "-", 1: "BIOS 'slept cleanly' marker"},
                  note="the BIOS sets bit 4 before halting; its INT0 handler tests it"),
        )),
        Reg(0x00006F, "WDCR", "Watchdog control -- write 0x4E to kick, 0xB1 to disable",
            DATASHEET),
    ),
)

SOUND_LINK = Group(
    "Sound CPU and link cable",
    "The Z80 and the T6W28 are held in reset until software writes 0x55. The "
    "cable-detect line is what a game probes before offering two-player.",
    (
        Reg(T6W28_RESET, "T6W28 reset", "0x55 = sound chip running", REVERSE),
        Reg(Z80_RESET, "Z80 reset", "0x55 = sound CPU running", REVERSE),
        Reg(Z80_NMI, "Z80 NMI", "any write raises one NMI on the sound CPU", REVERSE),
        Reg(Z80_COMM, "Z80 comm", "dual-port byte both CPUs can see", REVERSE),
        Reg(LINK_PORT, "Link port", "Serial handshake lines", REVERSE, (
            Field("cable detect", 2, 2, {0: "no cable", 1: "cable present"}),
        )),
    ),
)

SYSTEM = Group(
    "System RAM the BIOS maintains",
    "Not hardware registers -- BIOS-owned bytes in work RAM. They are here "
    "because they answer the questions the registers cannot: which console the "
    "game thinks it is on, and what it believes the battery is doing.",
    (
        Reg(0x006F80, "Battery voltage", "A/D result, 0x03FF = full scale", SPEC,
            width=2),
        Reg(0x006F82, "Sys Lever", "Joypad, as the BIOS VBlank handler leaves it",
            SPEC, tuple(
                Field(name, bit, bit, {0: "-", 1: name.upper()})
                for name, bit in reversed(JOYPAD_BITS)
            )),
        Reg(0x006F87, "Language", "BIOS language setting", SPEC,
            (Field("lang", 7, 0, {0: "Japanese", 1: "English"}),)),
        Reg(0x006F91, "OS version", "hardware type: < 0x10 means a mono NGP", SPEC),
    ),
)

GROUPS: tuple[Group, ...] = (VIDEO, TIMERS, INTERRUPTS, MICRO_DMA, WATCHDOG,
                             SOUND_LINK, SYSTEM)


def all_registers() -> tuple[Reg, ...]:
    return tuple(r for g in GROUPS for r in g.regs)


# ---------------------------------------------------------------- reading
@dataclass(frozen=True)
class FieldView:
    name: str
    bits: str
    raw: int
    text: str
    note: str


@dataclass(frozen=True)
class RegView:
    reg: Reg
    value: int
    fields: tuple[FieldView, ...]

    @property
    def hex(self) -> str:
        return f"{self.value:0{self.reg.width * 2}X}"

    @property
    def changed_from_reset(self) -> bool:
        return self.reg.reset is not None and self.value != self.reg.reset


def decode(reg: Reg, value: int) -> RegView:
    views = tuple(
        FieldView(f.name, f.bits_label(), f.extract(value),
                  f.describe(f.extract(value)), f.note)
        for f in reg.fields
    )
    return RegView(reg, value, views)


Reader = Callable[[int, int], bytes]


def read_group(group: Group, read: Reader) -> tuple[RegView, ...]:
    out = []
    for reg in group.regs:
        try:
            raw = read(reg.addr, reg.width)
        except Exception:
            continue
        if raw is None or len(raw) < reg.width:
            continue
        out.append(decode(reg, int.from_bytes(bytes(raw[:reg.width]), "little")))
    return tuple(out)


def read_all(read: Reader) -> dict[int, int]:
    """Every register in the map, as {address: value}. The input to `checks`."""
    values: dict[int, int] = {}
    for reg in all_registers():
        try:
            raw = read(reg.addr, reg.width)
        except Exception:
            continue
        if raw is None or len(raw) < reg.width:
            continue
        values[reg.addr] = int.from_bytes(bytes(raw[:reg.width]), "little")
    return values


# ---------------------------------------------------------------- checks
@dataclass(frozen=True)
class Check:
    """A state the documents call illegal, or that makes the picture wrong.

    `why` quotes the sentence that makes it a defect rather than a preference --
    without that a check is just an opinion with a red icon on it.
    """

    severity: str        # "error" | "warning" | "info"
    title: str
    why: str


def _get(v: dict[int, int], addr: int) -> int | None:
    return v.get(addr)


def checks(values: dict[int, int]) -> list[Check]:
    out: list[Check] = []

    wba_h, wsi_h = _get(values, 0x008002), _get(values, 0x008004)
    if wba_h is not None and wsi_h is not None and wba_h + wsi_h > 160:
        out.append(Check(
            "error", f"Window overflows horizontally (WBA.H+WSI.H = {wba_h + wsi_h} > 160)",
            "ngpcspec.txt: 'When the sum exceeds the hardware upper limit ... display "
            "and Vint/Hint generations are disrupted.'"))
    wba_v, wsi_v = _get(values, 0x008003), _get(values, 0x008005)
    if wba_v is not None and wsi_v is not None and wba_v + wsi_v > 152:
        out.append(Check(
            "error", f"Window overflows vertically (WBA.V+WSI.V = {wba_v + wsi_v} > 152)",
            "ngpcspec.txt: same rule, vertical limit 152."))

    bgc = _get(values, 0x008118)
    if bgc is not None and (bgc & 0xC0) != 0x80:
        out.append(Check(
            "info", f"BGC enable bits are 0x{bgc & 0xC0:02X}, not the enabled encoding 0x80",
            "ngpcspec.txt says the backdrop is then black. We draw it anyway (pass 248: "
            "Ogre Battle writes a blue backdrop with BGC = 0x00, and NeoPop carries the "
            "same 'always on' hack) -- so this is a flag, not a fault."))

    trun = _get(values, 0x000020)
    if trun is not None and not (trun & 0x80):
        out.append(Check(
            "warning", "Prescaler stopped (TRUN<PRRUN> = 0)",
            "8Bit.txt: 'Because the serial communication may [be] affected negatively, "
            "stopping the prescaler is prohibited.' Every timer is frozen too."))

    inte45 = _get(values, 0x000071)
    if inte45 is not None and (inte45 & 0x07) == 0:
        out.append(Check(
            "warning", "VBlank (INT4) is DISABLED -- level 0",
            "A level of 0 is not 'lowest priority', it is off. A game waiting on VBlank "
            "here will hang, and it will look like a CPU fault."))

    wdmod = _get(values, 0x00006E)
    if wdmod is not None and not (wdmod & 0x80):
        out.append(Check(
            "info", "Watchdog disabled",
            "Normal right after a BIOS hand-off; suspicious in the middle of a game, "
            "where it usually means the reset that should have happened will not."))

    for addr, name in ((0x00007C, "DMA0V"), (0x00007D, "DMA1V"),
                       (0x00007E, "DMA2V"), (0x00007F, "DMA3V")):
        v = _get(values, addr)
        if v:
            out.append(Check(
                "info", f"{name} armed on {_dma_vector(v)}",
                "That interrupt is serviced by DMA and never reaches the CPU. If you are "
                "hunting a handler that 'never runs', this is why."))

    ovr = _get(values, 0x008010)
    if ovr is not None and (ovr & 0x80):
        out.append(Check(
            "warning", "Character Over -- sprites were dropped on a line",
            "ngpcspec.txt: the line buffer ran out of time, so sprites disappear "
            "partially or completely. Real hardware does this too; it is a budget "
            "problem in the game, not necessarily an emulation bug."))

    return out


def format_report(read: Reader) -> str:
    """The whole map as text, for the Export button and for scripts."""
    lines: list[str] = []
    values = read_all(read)
    problems = checks(values)
    if problems:
        lines.append("CHECKS")
        for c in problems:
            lines.append(f"  [{c.severity}] {c.title}")
            lines.append(f"           {c.why}")
        lines.append("")
    for group in GROUPS:
        lines.append(group.name)
        for view in read_group(group, read):
            r = view.reg
            lines.append(f"  {r.addr:06X}  {r.name:<14} = {view.hex}   {r.summary}")
            for f in view.fields:
                lines.append(f"            {f.bits:<6} {f.name:<14} {f.raw:<4} {f.text}")
        lines.append("")
    return "\n".join(lines)
