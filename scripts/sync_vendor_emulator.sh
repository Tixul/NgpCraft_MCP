#!/usr/bin/env bash
# Re-vendor the emulator snapshot from the upstream repo.
#
# The vendor tree is a HEADLESS SUBSET: the two CLI entry points, the Python core,
# the C++ core and the specs. No tests, no UI, no scripts -- and deliberately NOT
# core/lobby.py, which is the only core module that imports PyQt6 (nothing in the
# CLI import closure touches it, and a Qt dependency has no place in an MCP server).
#
# Usage:  bash scripts/sync_vendor_emulator.sh [path-to-upstream-repo]
set -euo pipefail

UP="${1:-C:/Users/wilfr/Documents/GitHub/Ngpcraft_emulator}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
V="$HERE/vendor/emulator"

[ -f "$UP/ngpc_emu.py" ] || { echo "not an emulator repo: $UP" >&2; exit 1; }

echo "upstream: $UP  ($(cd "$UP" && git rev-parse --short HEAD))"

# --- Python core -------------------------------------------------------------
rm -rf "$V/core/__pycache__"
for f in "$UP"/core/*.py "$UP"/core/*.json; do
  b="$(basename "$f")"
  [ "$b" = "lobby.py" ] && continue          # PyQt6 -- see header
  cp -p "$f" "$V/core/$b"
done

# --- C++ core ----------------------------------------------------------------
cp -p "$UP/cpp/CMakeLists.txt"     "$V/cpp/CMakeLists.txt"
cp -p "$UP/cpp/include/ngpc_core.h" "$V/cpp/include/ngpc_core.h"
rm -f "$V"/cpp/src/*
cp -p "$UP"/cpp/src/* "$V/cpp/src/"

# --- entry points + docs -----------------------------------------------------
cp -p "$UP/ngpc_emu.py"                "$V/ngpc_emu.py"
cp -p "$UP/ngpc_native.py"             "$V/ngpc_native.py"
cp -p "$UP/README.md"                  "$V/README.md"
cp -p "$UP/HARDWARE_COMPAT_POLICY.md"  "$V/HARDWARE_COMPAT_POLICY.md"
cp -p "$UP/PERF_TIMING_POLICY.md"      "$V/PERF_TIMING_POLICY.md"
cp -p "$UP/SAVE_POLICY.md"             "$V/SAVE_POLICY.md"

# --- specs -------------------------------------------------------------------
cp -p "$UP"/specs/*.md "$V/specs/"

echo "synced. now rebuild the core:"
echo "  cmake -S vendor/emulator/cpp -B vendor/emulator/cpp/build -G 'MinGW Makefiles' -DCMAKE_BUILD_TYPE=Release"
echo "  cmake --build vendor/emulator/cpp/build"
