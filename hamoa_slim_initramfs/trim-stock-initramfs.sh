#!/bin/bash
# trim-stock-initramfs.sh — shrink the initramfs the RIGHT way on Hamoa.
#
# THE KEY INSIGHT (proven on the board): you cannot shrink the initramfs by
# rebuilding it with dracut hostonly/strict — those modes change the early-boot
# logic and the board's firmware watchdog then resets it at ~11 s. Instead, take
# the WORKING stock initramfs and just DELETE the inert .ko files you don't need,
# then repack with cpio/zstd. This preserves stock's exact init/udev/scripts/
# config/metadata, so it boots identically — only smaller.
#
# Result on this board: 82 MB / 2489 modules  ->  57 MB / 112 modules, boots fine
# (UFS enumerates, switch_root, login — verified).
#
# Run ON the board as root:  sudo ./trim-stock-initramfs.sh
# Produces /boot/initrd.img-<KVER>.trimstock ; add a GRUB entry (see 44_trimstock).

set -euo pipefail
KVER=$(uname -r)
STOCK=/boot/initrd.img-$KVER
OUT=/boot/initrd.img-$KVER.trimstock
WORK=/var/tmp/its

[ -f "$STOCK" ] || { echo "no $STOCK"; exit 1; }
rm -rf "$WORK"; mkdir -p "$WORK/unpack"

echo "[*] unpacking stock initramfs ($(du -h "$STOCK"|cut -f1)) ..."
( cd "$WORK/unpack" && zstd -dc "$STOCK" | cpio -idm --quiet )

echo "[*] removing .ko files not in the runtime (lsmod) dependency closure ..."
python3 - "$WORK/unpack" "$KVER" <<'PYEOF'
import os, sys, subprocess
work, KVER = sys.argv[1], sys.argv[2]
moddir = os.path.join(work, "usr/lib/modules", KVER)
def norm(p):
    n = os.path.basename(p)
    for e in (".zst", ".ko"):
        if n.endswith(e): n = n[:-len(e)]
    return n.replace("-", "_")
keep = set(norm(l.split()[0]) for l in
           subprocess.check_output(["lsmod"]).decode().splitlines()[1:] if l.strip())
dep = {}
with open(os.path.join(moddir, "modules.dep")) as f:
    for line in f:
        m, _, ds = line.strip().partition(":")
        if m: dep[norm(m)] = [norm(d) for d in ds.split()]
closure, stack = set(), list(keep)
while stack:
    m = stack.pop()
    if m in closure: continue
    closure.add(m)
    stack += [d for d in dep.get(m, []) if d not in closure]
removed = kept = 0
for root, _, files in os.walk(os.path.join(moddir, "kernel")):
    for fn in files:
        if ".ko" in fn:
            if norm(fn) in closure: kept += 1
            else: os.remove(os.path.join(root, fn)); removed += 1
print(f"    runtime={len(keep)} closure={len(closure)} kept={kept} removed={removed}")
PYEOF

echo "[*] repacking (cpio + zstd, no dracut) -> $OUT ..."
( cd "$WORK/unpack" && find . | cpio -o -H newc --quiet | zstd -3 -T0 -f -o "$OUT" )
rm -rf "$WORK"
echo "[*] done: $(du -h "$OUT"|cut -f1)  ($(lsinitrd "$OUT" 2>/dev/null | grep -c '\.ko') modules)"
echo "    Add a GRUB entry pointing at $OUT (with the devicetree line) and boot it."
