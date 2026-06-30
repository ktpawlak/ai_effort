#!/bin/sh
# hamoa-runtime-dracut.conf.sh — build a "runtime-set" initramfs for Hamoa.
#
# This builds an initramfs containing exactly the modules that are loaded on the
# RUNNING system (`lsmod`, ~177 modules / ~41 MB) instead of Ubuntu's default
# MODULES=most (~2500 / ~82 MB) or the over-trimmed minimal set (26 / ~25 MB).
#
# Rationale: the minimal set strips the power/PHY/interconnect provider modules
# that the SoC's BUILTIN early-probing masters (PCIe, display, ...) need to power
# their SMMU TBUs, so it dead-locks. The runtime set keeps them. (It still does
# not boot on this Gunyah-virtualised board — see README.md "Runtime-set test" —
# but it is the right direction and useful for experimentation.)
#
# Run ON the board (it reads the live lsmod):
#   sudo sh hamoa-runtime-dracut.conf.sh
# then build the runtime test entry's image:
#   sudo dracut --force --conf /tmp/hamoa-runtime.conf \
#        /boot/initrd.img-$(uname -r).runtime $(uname -r)

MODS=$(lsmod | tail -n +2 | awk '{print $1}' | sort | tr '\n' ' ')

cat > /tmp/hamoa-runtime.conf <<EOF
hostonly=yes
hostonly_mode=strict
compress=zstd
drivers="${MODS}"
force_drivers+=" ufs_qcom ufshcd_pltfrm "
omit_dracutmodules="kernel-network-modules overlayfs overlayfs-crypt plymouth copymods simpledrm i18n net-lib"
EOF

echo "Wrote /tmp/hamoa-runtime.conf with $(echo "$MODS" | wc -w) modules."
