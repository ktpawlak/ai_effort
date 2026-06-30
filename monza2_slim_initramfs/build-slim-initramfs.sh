#!/bin/bash
# build-slim-initramfs.sh - Build a native-minimal (MODULES=dep) initramfs on Monza2.
#
# Monza2 (QCS8300, Ubuntu Noble 24.04) uses initramfs-tools, NOT dracut. Its native
# minimal mode (MODULES=dep) only changes WHICH modules are included; the /init shell
# script and the whole boot sequence are static package files, so the result boots
# identically to stock (verified: byte-for-byte identical init skeleton).
#
# This builds the slim image to a SEPARATE file so the stock/default initramfs is never
# touched. Pair it with the 42_slimtest GRUB entry to test safely.
#
# Run on the board (ubuntu@192.168.1.185).
set -euo pipefail

KVER="$(uname -r)"
OUT="/boot/initrd.img-${KVER}.slim"
CONFDIR="$(mktemp -d)"
trap 'rm -rf "$CONFDIR"' EXIT

echo "Kernel:  $KVER"
echo "Output:  $OUT"

# Isolate the initramfs-tools config and flip MODULES=dep there only, so the global
# /etc/initramfs-tools/initramfs.conf (and thus the default initrd) is untouched.
cp -a /etc/initramfs-tools/. "$CONFDIR/"
sed -i 's/^MODULES=.*/MODULES=dep/' "$CONFDIR/initramfs.conf"
echo "Config:  $(grep '^MODULES=' "$CONFDIR/initramfs.conf") (isolated; stock config unchanged)"

echo "Building (~30-60s)..."
sudo mkinitramfs -d "$CONFDIR" -o "$OUT" "$KVER"

echo
echo "=== result ==="
ls -lh "$OUT" "/boot/initrd.img-${KVER}" | awk '{print $5, $9}'
echo -n "slim modules: "; lsinitramfs "$OUT" 2>/dev/null | grep -c '\.ko' || true
echo
echo "Next: install ./42_slimtest, 'sudo update-grub', then"
echo "      'sudo grub-reboot slimtest && sudo reboot' to test it once."
