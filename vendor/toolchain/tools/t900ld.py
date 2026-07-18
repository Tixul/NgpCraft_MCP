#!/usr/bin/env python3
"""
t900ld.py — TLCS-900 Linker v0.1
NGPCraft Toolchain, Jalon 2

Usage:
  python3 t900ld.py file1.t9obj [file2.t9obj ...] -o output.bin -m ngpc.lcf [--map output.map]

Architecture (3 passes):
  Pass 1 — Read all .t9obj, group sections by type, collect PUBLIC symbols.
  Pass 2 — Parse .lcf, place sections in ROM/RAM regions, compute linker symbols.
  Pass 3 — Resolve EXTERN symbols, apply relocation patches, emit flat .bin.

Symbol addressing:
  - Abs sections (org != None): assembler already emitted absolute addresses.
    link_addr = sec.org, no rebasing needed.
  - Relocatable sections (org == None): assembler emits 0-based addresses.
    link_addr assigned by linker; symbols in [0, len(data)) get rebased to
    link_addr + original_value.  This heuristic works when each object file
    has at most one relocatable section (the common case in Jalon 2/3).
"""

import argparse
import json
import re
import sys
import os
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ObjSection:
    """A section loaded from a .t9obj file and placed by the linker."""
    name: str              # section name, lowercased (e.g. "f_code")
    stype: str             # "code", "data", "bss", …
    org: Optional[int]     # None = relocatable, int = abs address
    displacement: str      # "large", "near", …
    data: bytearray
    patches: list          # [{offset, ptype, sym, instr_end}, …]
    source: str            # original .asm filename
    obj_file: str          # .t9obj path
    align: int = 1         # alignement declare (`section ... align=N`)
    # Filled by linker pass 2
    link_addr: Optional[int] = None  # ROM or RAM address
    ram_addr:  Optional[int] = None  # RAM address (only for f_data dual-map)


@dataclass
class MemoryRegion:
    name: str
    org: int
    length: int
    cursor: int = 0

    def __post_init__(self):
        self.cursor = self.org


@dataclass
class SectionRule:
    """One placement directive from the .lcf [sections] block."""
    output_name: str         # e.g. "far_code"
    region: str              # "ram" or "rom"
    section_filter: list     # lowercased section names, e.g. ["f_code"]
    addr_expr: Optional[str] = None  # e.g. "far_area_end"


# ---------------------------------------------------------------------------
# LCF parser helpers
# ---------------------------------------------------------------------------

def _extract_block(text: str, keyword: str) -> str:
    """Extract the body of 'keyword { ... }' using brace counting.
    Handles nested braces correctly.  Returns '' if keyword not found.
    """
    m = re.search(keyword + r'\s*\{', text)
    if not m:
        return ''
    start = m.end()
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
        i += 1
    return text[start:i - 1]


# ---------------------------------------------------------------------------
# LCF parser
# ---------------------------------------------------------------------------

def parse_lcf(path: str):
    """Parse a simplified .lcf file.

    Returns:
        regions  : dict[str, MemoryRegion]
        rules    : list[SectionRule]  (in declaration order)
        globals_ : dict[str, int]     (global assignments like stack_top)
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    # Strip comments
    text = re.sub(r'#[^\n]*', '', text)

    regions: dict[str, MemoryRegion] = {}
    rules: list[SectionRule] = []
    globals_: dict[str, int] = {}

    # memory { name : org=0xXX, len=0xXX  ... }
    mem_body = _extract_block(text, 'memory')
    for line in mem_body.split('\n'):
        line = line.strip().rstrip(',')
        rm = re.match(
            r'(\w+)\s*:\s*org\s*=\s*(0x[0-9a-fA-F]+|\d+)'
            r'\s*,\s*len\s*=\s*(0x[0-9a-fA-F]+|\d+)',
            line
        )
        if rm:
            name = rm.group(1)
            org = int(rm.group(2), 0)
            length = int(rm.group(3), 0)
            regions[name] = MemoryRegion(name=name, org=org, length=length)

    # sections { out_name > region [addr=expr] { *(f_xxx) [*(f_yyy)] } ... }
    # Use brace-counting extraction to handle nested { } inside each rule.
    sec_body = _extract_block(text, 'sections')
    if sec_body:
        # Each rule: out_name > region [addr=expr] { *(f_xxx) }
        for rm in re.finditer(
            r'(\w+)\s*>\s*(\w+)\s*(?:addr\s*=\s*(\w+))?\s*\{([^}]*)\}',
            sec_body
        ):
            out_name   = rm.group(1)
            region_name = rm.group(2)
            addr_expr  = rm.group(3)    # may be None
            filter_str = rm.group(4)
            filters = [f.lower() for f in re.findall(r'\*\((\w+)\)', filter_str)]
            rules.append(SectionRule(
                output_name=out_name,
                region=region_name,
                section_filter=filters,
                addr_expr=addr_expr,
            ))

    # Global assignments outside memory/sections blocks: name = 0xXXXX
    for line in text.split('\n'):
        line = line.strip()
        gm = re.match(r'^(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*$', line)
        if gm:
            globals_[gm.group(1)] = int(gm.group(2), 0)

    return regions, rules, globals_


# ---------------------------------------------------------------------------
# Patch application (mirrors t900as._pass2 logic)
# ---------------------------------------------------------------------------

def _apply_patch(data: bytearray, offset: int, ptype: str,
                 target: int, instr_end_abs: int):
    """Patch section data in-place.  Raises ValueError on range error."""
    if ptype in ('JP_ABS24', 'CALL_ABS24'):
        addr = target & 0xFFFFFF
        data[offset + 1] = addr & 0xFF
        data[offset + 2] = (addr >> 8) & 0xFF
        data[offset + 3] = (addr >> 16) & 0xFF

    elif ptype in ('CALR_REL16', 'JRL_REL16'):
        disp = target - instr_end_abs
        if not (-32768 <= disp <= 32767):
            raise ValueError(f"{ptype} disp {disp} out of [-32768, 32767]")
        data[offset + 1] = disp & 0xFF
        data[offset + 2] = (disp >> 8) & 0xFF

    elif ptype in ('JR_REL8', 'DJNZ_REL8'):
        disp = target - instr_end_abs
        if not (-128 <= disp <= 127):
            raise ValueError(f"{ptype} disp {disp} out of [-128, 127]")
        idx = offset + 1 if ptype == 'JR_REL8' else offset + 2
        data[idx] = disp & 0xFF

    elif ptype in ('ABS8', 'ABS16', 'ABS24', 'ABS32'):
        nbytes = int(ptype[3:]) // 8
        # ⛔ On TRONQUAIT EN SILENCE. Un symbole en ROM vit à une adresse 24 bits
        # (0x2xxxxx) : le loger dans un champ ABS16 perd les 16 bits de poids fort
        # et l'accès part lire la RAM. C'est exactement ce qui rendait la tilemap
        # du menu vide (écran noir). Une troncature silencieuse est un piège :
        # on ÉCHOUE, l'émetteur doit choisir le bon mode d'adressage.
        if target >> (nbytes * 8):
            raise ValueError(
                f"{ptype} : l'adresse 0x{target:X} ne tient pas sur {nbytes} octets "
                f"(mode d'adressage trop court a l'emission)")
        addr = target & ((1 << (nbytes * 8)) - 1)
        for i in range(nbytes):
            data[offset + i] = (addr >> (8 * i)) & 0xFF

    elif ptype == 'HIGH16':          # (symbole >> 16) & 0xFFFF — mot haut d'un far-ptr
        val = (target >> 16) & 0xFFFF
        data[offset + 0] = val & 0xFF
        data[offset + 1] = (val >> 8) & 0xFF

    elif ptype == 'LOW16':           # symbole & 0xFFFF — mot bas d'un far-ptr
        val = target & 0xFFFF
        data[offset + 0] = val & 0xFF
        data[offset + 1] = (val >> 8) & 0xFF

    elif ptype == 'LD_R32_SYM':
        # LD XRR, SYMBOL — patch 4 bytes at offset (instruction is 40+R + 4 bytes imm)
        # patch records offset = start of the 4 imm bytes (= instr_start + 1)
        addr = target & 0xFFFFFFFF
        data[offset + 0] = (addr >>  0) & 0xFF
        data[offset + 1] = (addr >>  8) & 0xFF
        data[offset + 2] = (addr >> 16) & 0xFF
        data[offset + 3] = (addr >> 24) & 0xFF

    elif ptype == 'LD_R16_SYM':
        # LD RR, SYMBOL — patch 2 bytes at offset (instruction is 30+R + 2 bytes imm)
        # patch records offset = start of the 2 imm bytes (= instr_start + 1)
        val = target & 0xFFFF
        data[offset + 0] = (val >> 0) & 0xFF
        data[offset + 1] = (val >> 8) & 0xFF

    else:
        raise ValueError(f"Unknown patch type '{ptype}'")


# ---------------------------------------------------------------------------
# Linker main logic
# ---------------------------------------------------------------------------

def link(obj_files: list, lcf_path: str, output_bin: str,
         map_path: Optional[str]) -> bool:
    """Run the 3-pass linker.  Returns True on success."""

    errors: list[str] = []

    # ------------------------------------------------------------------
    # Pass 1 — Read all .t9obj, build per-object section lists and
    #          collect PUBLIC symbols into the global symbol table.
    # ------------------------------------------------------------------

    # Per-object data: list of (obj_path, publics:set, externs:dict, symbols:dict,
    #                           sections:list[ObjSection])
    loaded_objs = []

    for obj_path in obj_files:
        try:
            with open(obj_path, encoding="utf-8") as f:
                obj = json.load(f)
        except Exception as e:
            errors.append(f"Cannot read '{obj_path}': {e}")
            continue

        if obj.get("version") != 1:
            errors.append(f"'{obj_path}': unsupported .t9obj version {obj.get('version')!r}")
            continue

        source   = obj.get("source", os.path.basename(obj_path))
        publics  = set(obj.get("publics", []))
        externs  = dict(obj.get("externs", {}))
        symbols  = {k: int(v) for k, v in obj.get("symbols", {}).items()}
        sym_secs = {k: v.lower() for k, v in obj.get("sym_secs", {}).items()}

        obj_secs = []
        for sd in obj.get("sections", []):
            sec = ObjSection(
                name         = sd["name"].lower(),
                stype        = sd.get("stype", "code"),
                org          = sd.get("org"),       # None or int
                displacement = sd.get("displacement", "large"),
                data         = bytearray.fromhex(sd.get("data", "")),
                patches      = sd.get("patches", []),
                source       = source,
                obj_file     = obj_path,
                align        = int(sd.get("align", 1) or 1),
            )
            obj_secs.append(sec)

        loaded_objs.append((obj_path, publics, externs, symbols, obj_secs, sym_secs))

    if errors:
        _print_errors(errors)
        return False

    # ------------------------------------------------------------------
    # Pass 2 — Place sections; compute linker symbols; remap symbols from
    #          relocatable sections.
    # ------------------------------------------------------------------

    regions, rules, lcf_globals = parse_lcf(lcf_path)

    # Flat list of all ObjSection objects (order: per-file, then per-section)
    all_sections: list[ObjSection] = []
    for _, _, _, _, secs, _ in loaded_objs:
        all_sections.extend(secs)

    # Group sections by section name (for rule matching)
    sections_by_type: dict[str, list[ObjSection]] = {}
    for sec in all_sections:
        sections_by_type.setdefault(sec.name, []).append(sec)

    # Place sections according to LCF rules
    far_area_end_ram: Optional[int] = None    # set after placing far_area

    for rule in rules:
        region = regions.get(rule.region)
        if region is None:
            errors.append(f"LCF: unknown region '{rule.region}' for '{rule.output_name}'")
            continue

        to_place = []
        for fname in rule.section_filter:
            to_place.extend(sections_by_type.get(fname, []))

        rom_cursor_at_rule_start = region.cursor  # for far_data ROM anchor

        for sec in to_place:
            if sec.org is not None:
                # Abs section: use its declared address directly
                link_addr = sec.org
            else:
                # ⛔⛔ ON CONCATENAIT LES SECTIONS BOUT A BOUT, SANS ALIGNEMENT (2026-07-14).
                # tulink ALIGNE la contribution de CHAQUE module sur l'alignement declare
                # (`f_const section romdata large align=2,2`). Constate sur la ROM
                # officielle : le `f_const` de `main.asm` finit sur une adresse IMPAIRE,
                # tulink insere un `ff` avant d'accoler le module suivant.
                # Sans ca, TOUTES les constantes des modules suivants sont decalees d'un
                # octet vs la ROM Toshiba -> byte-match impossible.
                # (Le trou reste a 0xFF : l'image ROM est initialisee a 0xFF.)
                a = max(1, sec.align)
                if region.cursor % a:
                    region.cursor += a - (region.cursor % a)
                # Relocatable section: assign from region cursor
                link_addr = region.cursor

            sec.link_addr = link_addr

            # Dual-addressing for far_data: stored in ROM, loaded into RAM
            if rule.addr_expr == "far_area_end":
                if far_area_end_ram is None:
                    errors.append(
                        f"far_data ('{sec.source}') placed before far_area "
                        f"— cannot compute RAM address"
                    )
                    sec.ram_addr = region.cursor  # fallback
                else:
                    # ram_addr = RAM base + (ROM offset of this section within far_data group)
                    sec.ram_addr = far_area_end_ram + (link_addr - rom_cursor_at_rule_start)

            # Advance region cursor
            end = link_addr + len(sec.data)
            if end > region.cursor:
                region.cursor = end

        # After placing far_area, lock in its RAM end for far_data remapping
        if rule.output_name == "far_area":
            far_area_end_ram = region.cursor

    if errors:
        _print_errors(errors)
        return False

    # --- Remap PUBLIC symbols from relocatable sections ---
    #
    # For abs sections: assembler symbol = absolute address → no change.
    # For relocatable (org=None): assembler symbol = 0-based offset within section.
    #   After placement at link_addr: real address = link_addr + symbol_value,
    #   provided symbol_value ∈ [0, len(sec.data)).
    #   We use the first section whose range contains the symbol value.

    global_symbols: dict[str, int] = {}

    def _remap_sym(sym_val: int, obj_secs_list, sec_hint: str = None) -> Optional[int]:
        """Remap a raw symbol value (abs or section-relative) to an absolute address.
        sec_hint: section name the symbol was defined in (from sym_secs), for disambiguation.
        """
        # If we know which section the symbol belongs to, use it directly.
        if sec_hint is not None:
            for s in obj_secs_list:
                if s.name.lower() == sec_hint.lower() and s.link_addr is not None:
                    if s.org is not None:
                        return sym_val  # already absolute
                    # f_data sections have a RAM address (where crt0 copies them)
                    if s.ram_addr is not None:
                        return s.ram_addr + sym_val
                    return s.link_addr + sym_val
        # Try abs sections first
        for s in obj_secs_list:
            if s.org is not None and s.link_addr is not None:
                if s.org <= sym_val < s.org + max(len(s.data), 1):
                    return sym_val  # already absolute
        # Then relocatable sections
        for s in obj_secs_list:
            if s.org is None and s.link_addr is not None:
                if 0 <= sym_val <= len(s.data):
                    # f_data sections have a RAM address (where crt0 copies them)
                    if s.ram_addr is not None:
                        return s.ram_addr + sym_val
                    return s.link_addr + sym_val
        return sym_val  # fallback: use raw value

    # Per-object local symbol tables (non-public, non-extern) remapped to absolute addresses.
    # Used in pass 3 to resolve intra-module patches (JR to local labels, JP to local labels...).
    obj_local_syms: dict[str, dict[str, int]] = {}

    for obj_path, publics, externs, raw_symbols, obj_secs, sym_secs in loaded_objs:
        for sym_name in publics:
            sym_val = raw_symbols.get(sym_name)
            if sym_val is None:
                errors.append(f"'{obj_path}': PUBLIC symbol '{sym_name}' has no value")
                continue
            resolved = _remap_sym(sym_val, obj_secs, sec_hint=sym_secs.get(sym_name))
            if sym_name in global_symbols and global_symbols[sym_name] != resolved:
                errors.append(
                    f"Duplicate PUBLIC symbol '{sym_name}': "
                    f"0x{global_symbols[sym_name]:06X} vs 0x{resolved:06X}"
                )
            global_symbols[sym_name] = resolved

        # Build local symbol table for this object
        local_syms: dict[str, int] = {}
        for sym_name, sym_val in raw_symbols.items():
            if sym_name in publics:
                continue  # already in global_symbols
            local_syms[sym_name] = _remap_sym(sym_val, obj_secs, sec_hint=sym_secs.get(sym_name))
        obj_local_syms[obj_path] = local_syms

    # --- Linker-generated symbols ---
    linker_symbols: dict[str, int] = {}

    area_secs = sections_by_type.get("f_area", [])
    if area_secs and all(s.link_addr is not None for s in area_secs):
        linker_symbols["_Bss_START"] = min(s.link_addr for s in area_secs)
        linker_symbols["_Bss_END"]   = max(s.link_addr + len(s.data) for s in area_secs)
    else:
        ram = regions.get("ram")
        linker_symbols["_Bss_START"] = ram.org if ram else 0x4000
        linker_symbols["_Bss_END"]   = ram.org if ram else 0x4000

    data_secs = sections_by_type.get("f_data", [])
    if data_secs and all(s.link_addr is not None for s in data_secs):
        linker_symbols["_DataROM_START"]  = min(s.link_addr for s in data_secs)
        linker_symbols["_DataROM_END"]    = max(s.link_addr + len(s.data) for s in data_secs)
        ram_addrs = [s.ram_addr for s in data_secs if s.ram_addr is not None]
        linker_symbols["_DataRAM_START"] = min(ram_addrs) if ram_addrs else linker_symbols["_Bss_END"]
    else:
        rom = regions.get("rom")
        linker_symbols["_DataROM_START"] = rom.org if rom else 0x200000
        linker_symbols["_DataROM_END"]   = linker_symbols["_DataROM_START"]
        linker_symbols["_DataRAM_START"] = linker_symbols["_Bss_END"]

    if "stack_top" in lcf_globals:
        linker_symbols["_StackTop"] = lcf_globals["stack_top"]

    # Derived size symbols (16-bit, for LD R16, SYMBOL in crt0)
    linker_symbols["_Bss_SIZE"]     = linker_symbols["_Bss_END"]     - linker_symbols["_Bss_START"]
    linker_symbols["_DataROM_SIZE"] = linker_symbols["_DataROM_END"] - linker_symbols["_DataROM_START"]

    # Alias DOUBLE-underscore : en C ces symboles s'appellent `_Bss_START`… et
    # l'assembleur préfixe TOUT symbole C d'un `_` → le code référence
    # `__Bss_START`. On publie les deux orthographes pour lier quelle que soit
    # la convention du crt0 (tulink fait de même).
    for _n, _v in list(linker_symbols.items()):
        linker_symbols.setdefault("_" + _n, _v)

    # Full symbol table: linker symbols as base, PUBLIC symbols override
    full_symbols = {**linker_symbols, **global_symbols}

    if errors:
        _print_errors(errors)
        return False

    # ------------------------------------------------------------------
    # Pass 3 — Apply relocation patches, emit flat binary
    # ------------------------------------------------------------------

    for sec in all_sections:
        if sec.link_addr is None:
            # ⛔ FATAL, plus un simple warning : une section non placée laisse
            # ses symboles à 0 → `cal 0x000000` et la ROM crashe à la 2e
            # instruction (kuroi `SYSPATCH`/`VRAMQ_ASM`, sections custom des
            # .asm écrits main — tulink, lui, les place automatiquement).
            # Un lien qui échoue est un CADEAU ; une ROM silencieusement
            # cassée n'en est pas un. → ajouter la règle au .lcf.
            print(
                f"ERROR: section '{sec.name}' from '{sec.obj_file}' "
                f"not placed (no matching LCF rule) — ajoutez une règle .lcf",
                file=sys.stderr,
            )
            sys.exit(4)

        for patch in sec.patches:
            offset    = patch["offset"]
            ptype     = patch["ptype"]
            sym       = patch["sym"]
            instr_end = patch.get("instr_end", 0)  # relative to section start
            # `_s_slots + 0x6` : l'assembleur transmet le décalage, on l'ajoute à
            # l'adresse résolue du symbole (les tables/champs indexés en dépendent).
            addend    = patch.get("addend", 0)

            # Absolute address of instruction end (for relative patches)
            instr_end_abs = sec.link_addr + instr_end

            # Resolve symbol: global table first, then per-object local symbols
            target = full_symbols.get(sym)
            if target is None:
                target = obj_local_syms.get(sec.obj_file, {}).get(sym)
            if target is None:
                errors.append(
                    f"'{sec.obj_file}':{sec.name}+0x{offset:04x}: "
                    f"undefined symbol '{sym}'"
                )
                continue

            # For abs sections, patches on local symbols were already resolved by
            # the assembler (non-zero bytes).  EXTERN patches still have placeholder
            # zeros and need to be patched here.
            # For relocatable sections, ALL patches need updating because addresses
            # shifted.  Detect by checking if sec.org was None.
            needs_patch = (sec.org is None) or (sym in {s for _, p, _, _, _ in loaded_objs for s in p} - set(full_symbols)) or True
            # Simplest: always apply — the assembler left EXTERNs as zeros;
            # local abs patches are idempotent (writing the same value).

            try:
                _apply_patch(sec.data, offset, ptype, target + addend, instr_end_abs)
            except (ValueError, IndexError) as e:
                errors.append(
                    f"'{sec.obj_file}':{sec.name}+0x{offset:04x} "
                    f"ptype={ptype} sym={sym}: {e}"
                )

    if errors:
        _print_errors(errors)
        return False

    # --- Emit ROM binary ---
    # Convention (matches ngpc_romtool.py expectation):
    #   The binary starts at the lowest section address, NOT at rom.org.
    #   ngpc_romtool prepends the 64-byte header, so code at rom.org+0x40
    #   lands at file offset 0x40 = cart address rom.org+0x40. ✓
    #
    # Example: if sections are at 0x200040 and 0x200060, bin_base = 0x200040,
    #   binary is (0x200060+58 - 0x200040) bytes with 0xFF gap between them,
    #   romtool prepends header → file[0x40] = first code byte at cart 0x200040.

    rom = regions.get("rom")
    if rom is None:
        print("ERROR: no 'rom' region in linker script", file=sys.stderr)
        return False

    rom_secs = [
        s for s in all_sections
        if s.link_addr is not None
        and rom.org <= s.link_addr < rom.org + rom.length
        and len(s.data) > 0
    ]
    if not rom_secs:
        print("ERROR: no ROM sections placed — empty binary", file=sys.stderr)
        return False

    bin_base = min(s.link_addr for s in rom_secs)
    bin_end  = max(s.link_addr + len(s.data) for s in rom_secs)
    bin_size = bin_end - bin_base

    rom_image = bytearray(b'\xFF' * bin_size)

    for sec in rom_secs:
        off = sec.link_addr - bin_base
        end = off + len(sec.data)
        if end > bin_size:
            print(
                f"WARNING: section '{sec.name}' (0x{sec.link_addr:06X}+{len(sec.data)}) "
                f"overflows binary range — truncated",
                file=sys.stderr,
            )
            end = bin_size
        rom_image[off:end] = sec.data[:end - off]

    # Trim trailing 0xFF
    last = len(rom_image) - 1
    while last > 0 and rom_image[last] == 0xFF:
        last -= 1
    trimmed = bytes(rom_image[:last + 1])

    with open(output_bin, "wb") as f:
        f.write(trimmed)

    print(
        f"  output : {output_bin}  ({len(trimmed)} bytes,"
        f" bin base 0x{bin_base:06X}, ROM base 0x{rom.org:06X})"
    )

    # --- Write map file ---
    if map_path:
        with open(map_path, "w", encoding="utf-8") as mf:
            mf.write(f"# t900ld.py map file\n")
            mf.write(f"# inputs: {', '.join(obj_files)}\n\n")

            mf.write("=== Linker symbols ===\n")
            for name, addr in sorted(linker_symbols.items()):
                mf.write(f"  {name:<24s} 0x{addr:08X}\n")

            mf.write("\n=== Public symbols ===\n")
            for name, addr in sorted(global_symbols.items()):
                mf.write(f"  {name:<24s} 0x{addr:08X}\n")

            mf.write("\n=== Placed sections ===\n")
            for sec in all_sections:
                if sec.link_addr is not None:
                    ram_note = (
                        f"  (RAM 0x{sec.ram_addr:06X})" if sec.ram_addr is not None else ""
                    )
                    mf.write(
                        f"  {sec.name:<16s} ROM 0x{sec.link_addr:08X}"
                        f"  {len(sec.data):5d} bytes"
                        f"  [{sec.source}]{ram_note}\n"
                    )

        print(f"  map    : {map_path}")

    return True


def _print_errors(errors: list):
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="t900ld.py — TLCS-900 Linker v0.1, NGPCraft Toolchain"
    )
    p.add_argument(
        "obj_files", nargs="+", metavar="FILE.t9obj",
        help="Input .t9obj files (section grouping preserves file order)",
    )
    p.add_argument(
        "-o", "--output", required=True, metavar="OUTPUT.bin",
        help="Output flat binary",
    )
    p.add_argument(
        "-m", "--map-script", required=True, metavar="SCRIPT.lcf",
        help="Linker script (.lcf)",
    )
    p.add_argument(
        "--map", metavar="OUTPUT.map",
        help="Optional symbol/section map file",
    )
    args = p.parse_args()

    print(f"t900ld.py — linking {len(args.obj_files)} object(s)")
    for f in args.obj_files:
        print(f"  input  : {f}")

    ok = link(args.obj_files, args.map_script, args.output, args.map)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
