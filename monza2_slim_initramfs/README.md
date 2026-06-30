# Monza2 (QCS8300) slim-initramfs analysis

Replication of the Hamoa initramfs-shrinking investigation on the **Monza2** board.
The headline result is the opposite of Hamoa: here the **native minimal mode just works**.

| | Hamoa | Monza2 |
|---|---|---|
| Board / SoC | Hamoa / X1E80100 | Monza2 / QCS8300 |
| IP | 192.168.1.123 | 192.168.1.185 |
| OS | Ubuntu Resolute 26.04 | **Ubuntu Noble 24.04.4 LTS** |
| Kernel | 7.0.0-1006-qcom | **6.8.0-1078-qcom** |
| Root storage | UFS (`/dev/sd*`) | **eMMC (`/dev/mmcblk0p71`, ext4)** |
| Boot chain | UEFI → GRUB | UEFI → GRUB (arm64-efi) |
| DTB source | GRUB `devicetree` line **required** | **firmware/ABL** (no `devicetree` line) |
| initramfs generator | **dracut** (systemd `/init`) | **initramfs-tools** (shell-script `/init`) |
| Stock initramfs | ~82 MB, ~2600 modules (`MODULES=most`) | **70 MB, 1893 modules (`MODULES=most`)** |
| Runtime `lsmod` | ~178 | **172 (~167/boot)** |
| Native minimal | **FAILS** (firmware watchdog reset) | **WORKS** (`MODULES=dep`, first try) |
| Working method | trim-stock (delete `.ko`, repack, no dracut) | `MODULES=dep` (native) **and** trim-stock — both boot |

## ★ Result

Both shrink approaches were built and **boot-verified** on the board. Each test boots a
**separate** initrd via a one-shot GRUB entry carrying a unique `initrd_variant=` cmdline
marker, while the default entry stays on the stock initrd (so a failed test can never brick
the board).

| Image | How | Modules | Size (`Freeing initrd memory`) | Boot |
|---|---|---:|---:|---|
| stock | `MODULES=most` (default) | 1893 | 70 MB | baseline |
| **slim** | `MODULES=dep` (native initramfs-tools) | 78 | **27 MB** (27080K) | ✅ clean |
| **trimstock** | stock minus inert `.ko`, repacked (no `mkinitramfs`) | 92 | **31 MB** (31480K) | ✅ clean |

Every booted variant reported `scmi timeouts: 0`, `smmu deadlock: 0`, `call traces: 0`,
`0` failed systemd units, root mounted from `/dev/mmcblk0p71 ext4`, and networking up
(`end0 192.168.1.185`). The `MODULES=dep` (`slim`) image is the recommended path — it is
the native, supported minimal mode and the smallest.

**Why even a tiny initramfs is safe here:** the entire root-mount path is **builtin**
(`CONFIG_EXT4_FS=y`, `CONFIG_MMC_BLOCK=y`, `CONFIG_MMC_SDHCI_MSM=y`, `CONFIG_BLK_DEV_DM=y`),
so the initramfs needs essentially *no* modules to reach the rootfs; everything else loads
after `switch_root` from `/usr/lib/modules`. (That is also why `trimstock`'s keep-set is
only 92: ~75 of the 167 runtime modules were never in the stock initramfs to begin with —
they load post-pivot from the real rootfs.)

## Why it works here but not on Hamoa

This is the crux, and it is **not** about how many modules are in the image (inert `.ko`
files never run). It is about whether the *generator rewrites the boot logic*:

* **initramfs-tools (`MODULES=dep`)** changes **only which `.ko` files are copied in**.
  The `/init` shell script and the entire early-boot sequence are static files shipped by
  the `initramfs-tools` package. Verified directly: the stock and slim images have a
  **byte-for-byte identical init skeleton** — the same 58 `init` / `scripts/*` / `bin/*` /
  `conf/*` files. Only the `.ko` set (and the firmware blobs tied to omitted modules)
  differs. Same boot path, fewer modules ⇒ it just works.

* **dracut (`hostonly_mode=strict` / `omit_dracutmodules` / `force_drivers`)** is a
  *generator*: it re-resolves dependencies and **re-authors the systemd-based init/udev
  sequence**. That changed early-boot timing on Hamoa, and a firmware/Gunyah watchdog
  reset the board at ~11 s before `switch_root`. On Hamoa the only safe shrink was to
  take the stock image and **delete `.ko` files without re-running dracut**, preserving
  its exact init.

Second reason the failure mode can't even appear here: the Hamoa SMMU deadlock was driven
by the builtin `scmi-qcom-memlat-devfreq` driver polling CPUCP over SCMI during a sparse
boot. On this QCS8300 kernel that driver **is not present at all** (no `.ko` in the tree,
0 SCMI timeouts, 0 SMMU deadlocks in any boot observed).

## How to reproduce

Everything is driven over SSH (`ubuntu@192.168.1.185`); a human watches the serial console
via `minicom -oD /dev/ttyUSB0` (the Bughopper console). The board is never bricked because
the **default GRUB entry stays on the stock initrd** and the slim image is tested via a
**separate, one-shot** entry.

```bash
# 1. Build the native-minimal image as a SEPARATE file (stock untouched):
./build-slim-initramfs.sh            # -> /boot/initrd.img-$(uname -r).slim, MODULES=dep

# 2. Install the test GRUB entry (clone of primary, repointed initrd + marker):
sudo cp 42_slimtest /etc/grub.d/42_slimtest && sudo chmod 755 /etc/grub.d/42_slimtest
sudo update-grub                     # NB: do NOT pipe through `head` — SIGPIPE kills the
                                     #     atomic write and the entry silently won't land

# 3. One-shot boot it (default remains stock; reverts automatically next reboot):
sudo grub-reboot slimtest && sudo reboot

# 4. After it comes back, verify it booted slim and is healthy:
grep -o 'initrd_variant=[a-z]*' /proc/cmdline       # -> initrd_variant=slim
sudo dmesg | grep 'Freeing initrd memory'           # -> ~27080K
systemctl --failed                                   # -> 0 loaded units failed
```

## Files

* `build-slim-initramfs.sh` — builds the `MODULES=dep` image to a separate `.slim` file
  using an isolated config dir, so the stock/default initramfs is never modified.
* `42_slimtest` — GRUB drop-in (`/etc/grub.d/42_slimtest`). Clone of the primary `Ubuntu`
  entry, repointed at `.slim`, with `initrd_variant=slim` on the kernel cmdline so you can
  confirm which image actually booted. **No `devicetree` line needed** (firmware supplies
  the DTB on this board).
* `43_trimstocktest` — GRUB drop-in for the trim-stock image (marker `initrd_variant=trimstock`).
* `trim-stock-initramfs.sh` — generator-agnostic method (unpack the stock multi-segment
  cpio, merge segments, delete non-runtime `.ko.zst`, repack as a single `cpio`/`zstd`
  archive — no `mkinitramfs`). This was *the* working method on Hamoa. On Monza2 it is **not
  required** (`MODULES=dep` already works and is smaller), but it is **boot-verified here**
  too (92 modules / 31 MB) and is kept for parity and for boards whose native minimal
  misbehaves.
* `modules.list` — the explicit hand list of all **77** modules in the `MODULES=dep`
  closure, grouped by subsystem with comments. Verified to be an exact match for the
  `MODULES=dep` set. Drop the bare names into `/etc/initramfs-tools/modules` and set
  `MODULES=list` to reproduce the slim image as a hand-maintained list.
* `modules.minimal.list` — the aggressive ~13-module hand minimum (Qualcomm platform + PHY
  only). Sufficient because root-mount is builtin; everything else in `modules.list` is
  generic LVM/RAID/crypt/iSCSI boilerplate this board never uses. Validate with a one-shot
  GRUB test entry before trusting it.
* `modules.full.txt` — the same 77 modules as full in-kernel paths (`kernel/.../foo.ko`),
  for reference.

## Gotchas (Monza2-specific)

* `update-initramfs`/`mkinitramfs` here are **initramfs-tools**, not the dracut wrapper
  used on Resolute. Inspect with `lsinitramfs <img>` (slow, ~90 s on the 70 MB image).
* `lsinitramfs <img> | grep -c '\.ko'` is the module count; the image is a zstd-compressed
  SVR4 cpio (`COMPRESS=zstd`).
* `update-grub` emits harmless `grub-probe: warning: Discarding improperly nested
  partition (...gpt68...)` lines on this eMMC layout — ignore them.
* GRUB default is hidden (`GRUB_TIMEOUT=0`); use `grub-reboot <id>` for one-shot testing
  rather than relying on interactive menu selection.
* Recovery if a test image ever hangs: the one-shot entry is consumed, so a power-cycle
  boots the stock default. Power control is `alpaca.py` via the Bughopper CBUS GPIOs
  (`Sulla_monza`); it shares the FTDI with the console, so release `minicom` first if you
  need to script a power cycle.
