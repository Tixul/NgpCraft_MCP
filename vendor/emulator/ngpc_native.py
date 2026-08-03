"""Headless CLI for the NATIVE core — the emulator an agent can actually drive.

`ngpc_emu.py` inspects: it opens a ROM or a save state and reports what is in it.
This runs the machine. Load the state a player captured one frame before a bug, hold
a button, advance frames, and look at what came out -- the same loop a person does by
hand, available to a tool.

    python ngpc_native.py run GAME.ngc --state bug.s0 --hold A --frames 30 --shot out.png

Everything is one command so one tool call answers "what happens if I press A here?".
Output is JSON on stdout with --json, so a caller never has to parse prose.

Two things it reports that are easy to get wrong elsewhere. It runs with the
SILICON-CALIBRATED cart wait-states on (`--timing free` opts out and says so in the
output), because the raw core defaults to free instruction fetch and a machine measured
that way is ~2.9x too fast. And every run carries an `hw_safety` block -- a starved
watchdog or a stack that crossed into the BIOS page, counted while the ROM runs on.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path

from core import native, rom_loader

# The player's save state (ngpc_shell.py, F2): magic, the CpuState struct, the
# sound/timer block (v2 only), then the working image from 0x0000. Same contract as
# core.savestate.load_shell_savestate.
SHELL_MAGIC = b"NGPCST02"       # v2: carries the sound CPU, the T6W28 and the timers
SHELL_MAGIC_V1 = b"NGPCST01"    # v1: CPU + memory only -- loads, but the sound will not
SHELL_MAGICS = (SHELL_MAGIC, SHELL_MAGIC_V1)
SHELL_MEM_LEN = 0x00C000

# SILICON TIMING. The core's fields default to free cart fetch (back-compat, see
# Machine::cart_wait) and the desktop shell switches these on for every ROM it loads.
# A headless run has no QSettings to read, so the same calibrated set is spelled out
# here -- keep it equal to CART_FETCH_WAIT / CART_DATA_WAIT / CART_LDIR_COST in
# ngpc_settings.py. Without them cart code runs ~2.9x too fast and any optimisation
# that only shortens the code measures as exactly zero.
SILICON_TIMING = {"cart_wait": 3, "cart_data_wait": 0, "ldir_cost": 14}

# The controller, as the hardware reports it at 0x00B0.
BUTTONS = {
    "UP": 0x01, "DOWN": 0x02, "LEFT": 0x04, "RIGHT": 0x08,
    "A": 0x10, "B": 0x20, "OPTION": 0x40,
}


def parse_buttons(spec: str | None) -> int:
    if not spec:
        return 0
    held = 0
    for name in spec.replace(",", "+").split("+"):
        key = name.strip().upper()
        if not key:
            continue
        if key not in BUTTONS:
            raise SystemExit(f"unknown button {name!r}; known: {', '.join(BUTTONS)}")
        held |= BUTTONS[key]
    return held


def load_state(machine: native.NativeMachine, path: Path) -> None:
    """Restore a player save state into a running machine."""
    blob = path.read_bytes()
    magic = blob[:len(SHELL_MAGIC)]
    if magic not in SHELL_MAGICS:
        raise SystemExit(f"{path} is not a {SHELL_MAGIC.decode()} save state")
    body = blob[len(magic):]
    cpu_t = type(machine.cpu())
    cpu_len = ctypes.sizeof(cpu_t)
    aux_len = ctypes.sizeof(native.AuxState) if magic == SHELL_MAGIC else 0
    if len(body) != cpu_len + aux_len + SHELL_MEM_LEN:
        raise SystemExit(
            f"{path}: expected {cpu_len + aux_len + SHELL_MEM_LEN} bytes after the "
            f"magic, got {len(body)}"
        )
    machine.write(0, body[cpu_len + aux_len:])
    machine.set_cpu(cpu_t.from_buffer_copy(body[:cpu_len]))
    # AFTER the image: writing it goes through the control registers, and 0x00BA is a
    # door ("fire one NMI at the sound CPU"), not storage. This is what cancels that.
    if aux_len:
        machine.set_aux_state(
            native.AuxState.from_buffer_copy(body[cpu_len:cpu_len + aux_len]))


def write_png(machine: native.NativeMachine, path: Path) -> None:
    """The frame as the core drew it, line by line, as the beam passed."""
    fb = machine.framebuffer()
    rows = bytearray()
    for px in fb:                                   # 12-bit 0BGR -> 8-bit RGB
        rows += bytes((((px >> 0) & 0xF) * 17, ((px >> 4) & 0xF) * 17, ((px >> 8) & 0xF) * 17))
    ppm = b"P6\n%d %d\n255\n" % (native.SCREEN_W, native.SCREEN_H) + bytes(rows)
    if path.suffix.lower() == ".ppm":
        path.write_bytes(ppm)
        return
    try:
        from PIL import Image                       # optional: PPM always works
    except ImportError:
        alt = path.with_suffix(".ppm")
        alt.write_bytes(ppm)
        raise SystemExit(f"Pillow not installed; wrote {alt} instead of {path}")
    import io
    Image.open(io.BytesIO(ppm)).save(path)


def apply_timing(machine: native.NativeMachine, mode: str) -> None:
    """Cart-flash wait-states: `silicon` (calibrated) or `free` (the raw core default).

    `free` exists to reproduce a measurement taken before wait-states, and for nothing
    else -- it is not a claim about hardware.
    """
    if mode != "silicon":
        return
    machine.set_cart_wait(SILICON_TIMING["cart_wait"])
    machine.set_cart_data_wait(SILICON_TIMING["cart_data_wait"])
    machine.set_ldir_cost(SILICON_TIMING["ldir_cost"])


def hw_report(machine: native.NativeMachine, limit: int = 8) -> dict:
    """The two hardware-safety findings: a starved watchdog, and a stack that crossed
    into the BIOS's own page. Counted, never fatal -- a real console does not stop at
    the instruction that commits them, so neither does this."""
    counts = {name: machine.hw_violations(bit)
              for bit, name in native.HW_KINDS.items()}
    out: dict = {"counts": counts, "clean": not any(counts.values())}
    if not out["clean"]:
        out["first"] = [{"kind": v.kind_name, "pc": v.pc, "detail": v.detail,
                         "cycle": v.cycle}
                        for v in machine.hw_violation_samples(limit)]
    return out


def cmd_run(args: argparse.Namespace) -> dict:
    rom = Path(args.rom)
    bios = Path(args.bios).read_bytes() if args.bios else None
    # Through the archive loader, so a .zip/.7z works here exactly as it does in the
    # app -- including "Pack.zip/Game.ngc" to name one title inside a multi-game
    # archive. A bare .ngc falls straight through to a plain read.
    machine = native.NativeMachine(rom_loader.read_rom_bytes(args.rom), bios=bios)
    apply_timing(machine, args.timing)          # BEFORE reset: it prices the boot too
    if args.hw_guard:
        machine.set_hw_guard(native.HW_WATCHDOG | native.HW_SYSTEM_STACK)
    machine.reset(bios_handoff=True)

    if args.state:
        load_state(machine, Path(args.state))

    held = parse_buttons(args.hold)
    summary = None
    for _ in range(max(0, args.frames)):
        machine.write(0x00B0, bytes([held & 0x7F]))
        summary = machine.run_frames(1)
        # Only when the caller ARMED the guard: without it these two are diagnostics
        # and the ROM is meant to run on, exactly as a console does.
        if args.hw_guard and summary.stop_status in (
                native.STATUS_SYSTEM_STACK_VIOLATION, native.STATUS_WATCHDOG_RESET):
            break

    cpu = machine.cpu()
    out = {
        "rom": str(rom),
        "bios": str(args.bios) if args.bios else None,
        "state": str(args.state) if args.state else None,
        "held": args.hold or "",
        "frames": args.frames,
        "pc": cpu.pc,
        "registers": {n: cpu.regs[i] for i, n in enumerate(native.REG_NAMES)},
        "stop_status": native.status_name(summary.stop_status) if summary else "no-frames-run",
        "frame_count": summary.frame_count if summary else 0,
        # Say which timing model produced these numbers, so a cycle figure is never
        # quoted without knowing whether the cart bus was billed.
        "timing": args.timing,
        "hw_safety": hw_report(machine),
    }
    if args.peek:
        addr, count = int(args.peek[0], 0), int(args.peek[1], 0)
        out["peek"] = {"address": addr, "count": count,
                       "bytes": machine.read(addr, count).hex()}
    if args.shot:
        write_png(machine, Path(args.shot))
        out["screenshot"] = str(args.shot)
    machine.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the native core and report what came out")
    run.add_argument("rom", help="path to a .ngc / .ngp, or an archive; "
                                 "'Pack.zip/Game.ngc' picks one title inside it")
    run.add_argument("--bios", help="a real bios.bin; some games need it (see README)")
    run.add_argument("--state", help="a player save state (.s0) to start from")
    run.add_argument("--frames", type=int, default=1, help="frames to advance (default 1)")
    run.add_argument("--hold", help="buttons held for every frame, e.g. 'A' or 'LEFT+B'")
    run.add_argument("--shot", help="write the resulting frame here (.png or .ppm)")
    run.add_argument("--peek", nargs=2, metavar=("ADDR", "COUNT"),
                     help="read COUNT bytes at ADDR after the run")
    run.add_argument("--timing", choices=("silicon", "free"), default="silicon",
                     help="cart-flash wait-states: 'silicon' (calibrated, the default) "
                          "or 'free' (no fetch cost -- the raw core default, ~2.9x too "
                          "fast; only for reproducing a pre-wait-state measurement)")
    run.add_argument("--hw-guard", action="store_true",
                     help="STOP the run on a hardware-safety finding (starved watchdog, "
                          "stack in the BIOS page) instead of only counting it")
    run.add_argument("--json", action="store_true", help="machine-readable output")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    if not native.available():
        raise SystemExit(
            "the native core is not built. Build it with:\n"
            "  cmake -S cpp -B cpp/build -G 'MinGW Makefiles' && cmake --build cpp/build"
        )
    result = args.func(args)
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            if key == "registers":
                print("registers:", " ".join(f"{n}={v:#010x}" for n, v in value.items()))
            elif key == "peek":
                print(f"peek {value['address']:#08x}+{value['count']}: {value['bytes']}")
            elif key == "hw_safety":
                if value["clean"]:
                    print("hw_safety: clean")
                else:
                    print("hw_safety: " + ", ".join(f"{k}={v}" for k, v in
                                                    value["counts"].items() if v))
                    for s in value["first"]:
                        print(f"  {s['kind']} at pc={s['pc']:#08x} "
                              f"detail={s['detail']:#x} cycle={s['cycle']}")
            elif value is not None:
                print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
