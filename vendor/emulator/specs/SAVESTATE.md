# Savestate v2

Purpose:
- define the on-disk format for an emulator machine-state snapshot
- close the M0 gate item `savestate v1` that the ROADMAP flags as
  `a faire`
- v2 (2026-05-20) extends v1 additively with `iff_level`, `rfp` and
  flag `nf` to match the modeled SR shape from `CPU_STATE.md`. Per
  §6 the loader rejects unknown versions deliberately; v1 files are
  considered legacy artefacts and are not auto-upgraded.
- make the snapshot distinct from the cartridge-persistent in-game save
  defined in `SAVE_POLICY.md`
- keep the format honest about what the current emulator actually models;
  do not invent fields for subsystems that do not yet exist

Current source references:
- `../SAVE_POLICY.md` (section 2: savestate vs. persistent save distinction)
- `../HARDWARE_COMPAT_POLICY.md` (§4.1 reference vs. diagnostic separation)
- `CPU_STATE.md`
- `RESET_STATE.md`
- `EXECUTE.md`
- `ADDRESS_SPACE.md`

Current scope:
- a savestate is a point-in-time snapshot of the reference emulator state
- it is not a cartridge save: ROM-side flash persistence is covered
  separately by `SAVE_POLICY.md`
- v1 captures only the subsystems that the current emulator prototype
  actually models
- v1 is designed for the Python prototype; it must survive the eventual
  C++ core rewrite without a format break

## 1. Format envelope

Savestate files are UTF-8 JSON with the following root object:

```
{
  "format": "ngpc-emu-savestate",
  "format_version": "2026-05-20.v2",
  "created_at_utc": "<ISO-8601 timestamp>",
  "emulator": {
    "project": "NgpCraft_emulator",
    "prototype": "python",
    "commit": "<short SHA when known, null otherwise>"
  },
  "rom": { ... },
  "cpu": { ... },
  "memory": { ... },
  "quirks": { ... },
  "note": "<free-form operator note, optional>"
}
```

Mandatory top-level fields: `format`, `format_version`, `rom`, `cpu`,
`memory`.

## 2. ROM identity

```
"rom": {
  "path_when_saved": "<absolute path, informational only>",
  "file_size": <int>,
  "sha256": "<64-char hex digest of the ROM file bytes>",
  "header_title": "<title string as decoded by core/rom.py>",
  "header_entry_point": <int>,
  "header_mode_raw": <int>
}
```

A loader MUST verify that a ROM is currently available whose sha256 hash
matches `rom.sha256`. Path is informational only; matching is by content
hash, never by filename.

Versioning promise:
- a v1 savestate always carries the ROM hash
- loaders refuse to load a savestate against a mismatching ROM
- this is the minimum correctness guardrail before anything else

## 3. CPU state

```
"cpu": {
  "pc": <int>,
  "register_bank": "<bank id or null>",
  "sr_raw": <int or null>,
  "flags": {
    "sf": <bool or null>,
    "zf": <bool or null>,
    "vf": <bool or null>,
    "hf": <bool or null>,
    "cf": <bool or null>,
    "nf": <bool or null>
  },
  "registers": {
    "xwa": <int or null>,
    "xbc": <int or null>,
    "xde": <int or null>,
    "xhl": <int or null>,
    "xix": <int or null>,
    "xiy": <int or null>,
    "xiz": <int or null>,
    "xsp": <int or null>
  },
  "iff_enabled": <bool or null>,
  "iff_level": <int or null>,
  "rfp": <int or null>
}
```

Same shape as the current `NgpcCpuState` / `StatusFlags` /
`GeneralRegisters32` containers exposed by `core/cpu.py`:
- unknown fields are saved as `null`
- a loader MUST preserve the unknown/known distinction instead of
  filling nulls with zero
- `iff_enabled` is kept as a derived legacy convenience; `iff_level`
  (0..7, SR bits 12..14) is canonical, and `iff_enabled` is True iff
  `iff_level < 7`
- `rfp` is the Register File Pointer (0..3, SR bits 8..10)
- `nf` is the Add/Subtract flag (SR bit 1)

`register_bank` is kept nullable; the canonical bank index is `rfp`.

## 4. Memory

```
"memory": {
  "writable_overlay": {
    "0x004000": <int>,
    "0x004001": <int>,
    ...
  }
}
```

- the overlay is the writable runtime overlay produced by the current
  executor (`after_memory` in `ExecutionResult`)
- keys are lowercase `0xNNNNNN` hex strings padded to 6 digits
- values are single bytes `0..255`
- the overlay only contains cells that have been written during the
  session; unwritten cells stay out of the savestate

Not captured in v1:
- K2GE register RAM snapshot beyond what the overlay contains
- VRAM, OAM, palette
- BIOS workspace beyond what is already modeled by
  `core/memory.py._build_builtin_readable_bytes()`
- shared Z80 RAM beyond the overlay

These subsystems are intentionally excluded because the emulator does
not model them yet. Pretending to save them would violate the
reference-hardware-faithful rule in `HARDWARE_COMPAT_POLICY.md §2`.

## 5. Quirk database snapshot

```
"quirks": {
  "database_version": "2026-04-22.v3",
  "matched_on_last_step": { ... } or null
}
```

- `database_version` records which `core/quirks_db.json` was active
- `matched_on_last_step` echoes the `matched_quirk` payload from the
  last executed instruction when one was active, so diagnostic tools
  can inspect why execution stopped without replaying the full trace

## 6. Versioning rule

- the first shipped version was `2026-04-22.v1`
- current version is `2026-05-20.v2` — adds `iff_level`, `rfp`, `flags.nf`
- future schema changes bump the `format_version` string
- loaders MUST reject a savestate whose `format` is not
  `ngpc-emu-savestate`
- loaders MUST reject a `format_version` they do not recognize; the
  project deliberately does not define implicit upgrade paths at this
  stage
- when the Python prototype migrates to the C++ core, the format must
  be copied byte-for-byte before any field is added or removed; the
  rewrite itself does not get to break the format

## 7. Proposed CLI (not implemented in v1 yet)

- `python ngpc_emu.py savestate save <rom> <state.json> [--from <source>]`
  - `--from bootstrap` (default): writes a savestate derived from the
    current reset-info bootstrap
  - `--from run-until-exec <target_pc>` and related forms: writes a
    savestate derived from the final state of one run
- `python ngpc_emu.py savestate load <state.json>`
  - prints a summary of the loaded machine state
  - rejects mismatched ROM, unknown format, or unknown version
- `python ngpc_emu.py run-until-exec <rom> <target_pc> --seed-from <state.json>`
  - seeds CPU state and memory overlay from a savestate instead of
    from manual `--seed-reg` / `--seed-xsp` flags

These commands are deliberately left out of v1 implementation; this spec
only locks the format so that the implementation session can focus on
wiring without re-opening format questions.

## 8. Relation to other policies

- `SAVE_POLICY.md` describes cartridge-persistent save handling; a
  savestate is NOT a substitute for it and a savestate loader MUST NOT
  overwrite a cartridge save file
- `HARDWARE_COMPAT_POLICY.md` applies: a savestate must never be used
  to paper over an execution that real hardware would refuse
- `TRACE.md` and `TRACE_EXEC.md` cover runtime traces; a savestate is a
  single point, not a history, and must not be confused with a trace

## 9. Not modeled yet

- interrupt latency state, IRQ priority scheduler snapshot
- DMA channel progress
- K2GE scanline counter, VBlank state
- audio sample generator state
- timer reload values
- BIOS scratchpad that the emulator does not touch
- any information required for a cycle-exact reload that the emulator
  does not yet track

These gaps are expected and deliberate. Each one should graduate into
the savestate format only after the corresponding subsystem becomes a
first-class citizen of the reference emulator.

## 10. Next extensions (likely v2+)

- add `event_log` cross-reference once `EVENT_LOG.md` lands
- add `cpu.scanline_cycle`, `cpu.frame_index` when the timing model
  exists
- add `dma.channels` when DMA is emulated
- add `audio` block when the PSG / noise generator is emulated
- add `video.vblank_state` and related when K2GE timing is modeled
- consider a compact binary representation once JSON becomes the main
  bottleneck of headless regression workflows
