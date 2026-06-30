#!/bin/bash
# trim-stock-initramfs.sh - Generator-agnostic initramfs shrink for Monza2 (PARITY ONLY).
#
# This is the method that was REQUIRED on Hamoa (where dracut's native minimal broke the
# boot). On Monza2 it is NOT needed -- `MODULES=dep` (see build-slim-initramfs.sh) already
# produces a smaller, working image. It is kept here for parity with the Hamoa analysis and
# for any board whose native minimal misbehaves.
#
# Idea: take the STOCK initramfs, delete every .ko that is not in the runtime set (lsmod)
# plus its dependency closure, and repack -- WITHOUT re-running mkinitramfs/dracut. The
# /init script, udev rules, conf and all boot logic are preserved exactly as the distro
# shipped them; only inert module files are removed.
#
# Run on the board (ubuntu@192.168.1.185). Produces /boot/initrd.img-$(uname -r).trimstock.
set -euo pipefail

KVER="$(uname -r)"
SRC="/boot/initrd.img-${KVER}"
OUT="/boot/initrd.img-${KVER}.trimstock"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Unpacking stock initramfs ($SRC)..."
# Ubuntu initrd is a concatenation of cpio segments (early[, early2...], main). On this
# board the .ko.zst modules live in the 'early' segment while 'main' holds init + module
# metadata (modules.dep, ...). Unpack all segments, then MERGE them (later segments overlay
# earlier ones) into a single tree so modules and their metadata sit together.
unmkinitramfs "$SRC" "$WORK/seg"
mkdir -p "$WORK/root"
if [ -d "$WORK/seg/main" ] || [ -d "$WORK/seg/early" ]; then
    for s in $(ls "$WORK/seg" | sort); do cp -a "$WORK/seg/$s/." "$WORK/root/"; done
else
    cp -a "$WORK/seg/." "$WORK/root/"          # single-segment image
fi

MODDEP="$(find "$WORK/root" -path "*/lib/modules/${KVER}/modules.dep" | head -1)"
[ -n "$MODDEP" ] || { echo "ERROR: modules.dep for $KVER not found in unpacked image" >&2; exit 1; }
MODDIR="$(dirname "$MODDEP")"
echo "Merged root: $WORK/root   modules dir: $MODDIR"
echo "Modules before: $(find "$MODDIR" -name '*.ko*' | wc -l)"

echo "Computing keep-set (runtime lsmod + dependency closure)..."
python3 - "$MODDIR" <<'PY'
import os, sys, subprocess
moddir = sys.argv[1]

# runtime set from lsmod (underscore-normalised)
loaded = set()
for line in subprocess.check_output(["lsmod"], text=True).splitlines()[1:]:
    loaded.add(line.split()[0].replace("-", "_"))

# parse modules.dep for the closure
depmap, name2path = {}, {}
depfile = os.path.join(moddir, "modules.dep")
with open(depfile) as f:
    for line in f:
        lhs, _, rhs = line.partition(":")
        p = lhs.strip()
        name = os.path.basename(p).split(".ko")[0].replace("-", "_")
        name2path[name] = p
        depmap[name] = [os.path.basename(d).split(".ko")[0].replace("-", "_")
                        for d in rhs.split()]

keep, stack = set(), [m for m in loaded if m in depmap]
while stack:
    m = stack.pop()
    if m in keep:
        continue
    keep.add(m)
    stack.extend(depmap.get(m, []))

# delete every .ko not in keep
removed = kept = 0
for dirpath, _, files in os.walk(moddir):
    for fn in files:
        if ".ko" not in fn:
            continue
        name = fn.split(".ko")[0].replace("-", "_")
        if name in keep:
            kept += 1
        else:
            os.remove(os.path.join(dirpath, fn))
            removed += 1
print(f"  runtime modules: {len(loaded)}  keep-closure: {len(keep)}  kept .ko: {kept}  removed: {removed}")
PY

echo "Modules after: $(find "$MODDIR" -name '*.ko*' | wc -l)"
echo "Repacking (single-segment zstd cpio)..."
# A single zstd-compressed cpio is a valid initrd; the kernel does not require the original
# multi-segment split. All files (init, scripts, firmware, trimmed modules) are preserved.
( cd "$WORK/root" && find . -print0 | cpio --null -o -H newc --quiet | zstd -q -19 -T0 ) | sudo tee "$OUT" > /dev/null

echo
echo "=== result ==="
ls -lh "$OUT" "$SRC" | awk '{print $5, $9}'
echo "Test with a GRUB entry pointing initrd at $OUT (see 42_slimtest as a template)."
