"""A Python console wired to the running console.

Look at what this project actually does when it needs an answer the debugger does
not have: `analyze_glitch_state.py`, `detect_hdiscontinuity.py`, `ripoam.py`,
`triage_vs_oracle.py`. Every one of them is a one-off script written outside the
tool, run against a dump, and then either kept forever or lost. Some of them
answered their question in four lines.

This is those four lines, with the machine already in scope. Nothing here is a new
capability -- everything it exposes, the window can already do -- but a debugger you
can only use through the buttons someone thought of in advance stops exactly where
your question starts.

Two halves, and they are separate on purpose:

  * `Console` -- evaluate a string, capture whatever it printed, never raise. An
    exception escaping into a Qt slot is fatal in this application (PyQt calls
    qFatal and the process dies with no traceback), so a REPL that let one through
    would be a crash generator with a prompt.
  * `build_namespace` -- what the code can see. Curated, documented, and listed by
    `help()`, because a namespace you have to guess at is not an API.
"""

from __future__ import annotations

import codeop
import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass


@dataclass(frozen=True)
class Result:
    output: str
    failed: bool
    incomplete: bool = False        # a block waiting for more lines


class Console:
    """Evaluates source against a namespace, capturing everything it emits."""

    def __init__(self, namespace: dict | None = None) -> None:
        self.namespace: dict = dict(namespace) if namespace else {}
        self._builtin_keys: set = set(self.namespace)
        self.history: list[str] = []

    def set_namespace(self, ns: dict) -> None:
        """Swap the machine-facing names, keeping whatever the user defined.

        A console that forgot your helper function every time a game was loaded
        would train you not to write one.
        """
        user = {k: v for k, v in self.namespace.items() if k not in self._builtin_keys}
        self.namespace = dict(ns)
        self._builtin_keys = set(ns)
        self.namespace.update(user)

    def is_complete(self, source: str) -> bool:
        """False while a block still wants more lines (`for x in y:` and nothing
        under it). Lets the widget show a continuation prompt instead of raising a
        syntax error at someone mid-thought."""
        try:
            return codeop.compile_command(source, "<console>", "exec") is not None
        except SyntaxError:
            return True          # broken, not unfinished -- let run() report it

    def run(self, source: str) -> Result:
        source = source.rstrip()
        if not source.strip():
            return Result("", False)
        if not self.is_complete(source):
            return Result("", False, incomplete=True)
        self.history.append(source)

        out = io.StringIO()
        failed = False
        try:
            with redirect_stdout(out), redirect_stderr(out):
                try:
                    # Try it as an expression first, so `m.cpu().pc` echoes its value
                    # the way a REPL should instead of silently evaluating to nothing.
                    code = compile(source, "<console>", "eval")
                except SyntaxError:
                    exec(compile(source, "<console>", "exec"), self.namespace)  # noqa: S102
                else:
                    value = eval(code, self.namespace)  # noqa: S307
                    if value is not None:
                        self.namespace["_"] = value
                        print(repr(value))
        except SystemExit:
            # `exit()` in a debugger console means "close the console", never
            # "tear down the emulator with a game running".
            return Result("(use the window's close button)", False)
        except BaseException as e:                              # noqa: BLE001
            failed = True
            # ⛔ The traceback is CAPTURED, never propagated. This runs inside a Qt
            # slot, and an exception reaching PyQt calls qFatal(): the process dies
            # with no message at all. A REPL that let one through would be a crash
            # generator with a prompt on it.
            #
            # `tb_next` drops OUR exec/eval frame, so the trace starts at the line
            # that was typed. Keeping it would put this file at the top of every
            # mistake and read as "the console is broken".
            tb = e.__traceback__.tb_next if e.__traceback__ is not None else None
            out.write("".join(traceback.format_exception(type(e), e, tb)))
        return Result(out.getvalue(), failed)


HELP = """\
The machine is already in scope. Everything here is what the window itself uses.

  m                the native machine     play      the player page (frames, pause)
  cpu()            CPU state              regs()    registers as a dict
  read(a, n=1)     bytes from the bus     write(a, data)   bytes onto the bus
  u8/u16/u32(a)    one value              poke(a, v)       one byte
  find(pattern, lo=0x200000, hi=0x3FFFFF) -> addresses of a byte pattern
  hexdump(a, n=64) printable hex
  dis(a, n=8)      TLCS-900 disassembly   z80dis(a, n=8)   sound-CPU disassembly
  step(n=1)        run n frames           screen()  the framebuffer as (152,160,3)
  on_frame(fn)     call fn() every EMULATED frame; returns a canceller
  hooks()          what is currently subscribed

Modules already imported: hwregs, tilemap_view, coverage_map, profile, movie,
z80dasm, z80_debug, np.

  hwregs.format_report(read)              every hardware register, decoded
  tilemap_view.read_plane(read, 0)        a whole scroll plane
  profile.profile(records, symbols)       cycles per function

`_` holds the last value. help() prints this."""


def build_namespace(machine, play=None, *, extra: dict | None = None) -> dict:
    """The names a console session starts with.

    `machine` may be None (no game running): the helpers then say so rather than
    raising AttributeError on None, because "no game running" is an answer and a
    traceback is not.
    """
    import numpy as np

    from core import (coverage_map, hwregs, movie, profile, tilemap_view,
                      z80_debug, z80dasm)
    from core.decode import decode_instruction_at

    def _need():
        if machine is None:
            raise RuntimeError("no game running")
        return machine

    def read(addr: int, n: int = 1) -> bytes:
        return bytes(_need().read(addr & 0xFFFFFF, n))

    def write(addr: int, data) -> None:
        _need().write(addr & 0xFFFFFF, bytes(data) if not isinstance(data, int)
                      else bytes([data & 0xFF]))

    def poke(addr: int, value: int) -> None:
        write(addr, bytes([value & 0xFF]))

    def u8(addr: int) -> int:
        return read(addr, 1)[0]

    def u16(addr: int) -> int:
        b = read(addr, 2)
        return b[0] | (b[1] << 8)

    def u32(addr: int) -> int:
        return int.from_bytes(read(addr, 4), "little")

    def find(pattern, lo: int = 0x200000, hi: int = 0x3FFFFF) -> list[int]:
        """Every address in [lo, hi] where `pattern` appears. Reads in one go."""
        if isinstance(pattern, str):
            pattern = pattern.encode()
        pattern = bytes(pattern)
        blob = read(lo, hi - lo + 1)
        out, at = [], blob.find(pattern)
        while at != -1:
            out.append(lo + at)
            at = blob.find(pattern, at + 1)
        return out

    def hexdump(addr: int, n: int = 64) -> str:
        data = read(addr, n)
        rows = []
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            rows.append(f"{addr + off:06X}  {chunk.hex(' '):<47}  {text}")
        return "\n".join(rows)

    class _Bus:
        def read_bytes(self, address: int, size: int = 1):
            class _R:
                pass
            r = _R()
            try:
                r.status, r.data = "ok", read(address, size)
            except Exception:
                r.status, r.data = "unmapped", None
            return r

    def dis(addr: int, n: int = 8) -> str:
        bus, pc, rows = _Bus(), addr & 0xFFFFFF, []
        for _ in range(n):
            try:
                d = decode_instruction_at(bus, pc)
                rows.append(f"{pc:06X}  {d.assembly or d.mnemonic or '??'}")
                pc += max(1, d.length)
            except Exception as e:                              # noqa: BLE001
                rows.append(f"{pc:06X}  ?? ({e})")
                pc += 1
        return "\n".join(rows)

    def z80dis(addr: int = 0, n: int = 8) -> str:
        rd = z80_debug.make_reader(read)
        return "\n".join(f"{i.addr:04X}  {i.hex:<12} {i.text}"
                         for i in z80dasm.disassemble(rd, addr, n))

    def cpu():
        return _need().cpu()

    def regs() -> dict:
        c = _need().cpu()
        names = ("XWA", "XBC", "XDE", "XHL", "XIX", "XIY", "XIZ", "XSP")
        out = {n: int(v) for n, v in zip(names, c.regs)}
        out["PC"] = int(c.pc)
        return out

    def step(n: int = 1):
        if play is None:
            raise RuntimeError("no player page attached")
        for _ in range(max(1, int(n))):
            play.step_forward()

    def screen():
        fb = _need().framebuffer()
        return np.asarray(fb, np.uint32).reshape(152, 160)

    def on_frame(fn):
        """Run `fn()` once per EMULATED frame. Returns a function that cancels it.

        Per-frame is the granularity the one-off scripts always needed and never
        had: sampling at the window's refresh rate counts eight of six hundred
        thousand instructions and calls it a measurement.
        """
        if play is None or not hasattr(play, "frame_hooks"):
            raise RuntimeError("no player page attached")
        play.frame_hooks.append(fn)

        def cancel():
            if fn in play.frame_hooks:
                play.frame_hooks.remove(fn)
        return cancel

    def hooks() -> list:
        return list(getattr(play, "frame_hooks", []))

    ns = {
        "__name__": "ngpc_console",
        "m": machine, "play": play, "np": np,
        "read": read, "write": write, "poke": poke,
        "u8": u8, "u16": u16, "u32": u32,
        "find": find, "hexdump": hexdump, "dis": dis, "z80dis": z80dis,
        "cpu": cpu, "regs": regs, "step": step, "screen": screen,
        "on_frame": on_frame, "hooks": hooks,
        "hwregs": hwregs, "tilemap_view": tilemap_view, "coverage_map": coverage_map,
        "profile": profile, "movie": movie, "z80dasm": z80dasm, "z80_debug": z80_debug,
        "help": lambda: print(HELP),
    }
    if extra:
        ns.update(extra)
    return ns
