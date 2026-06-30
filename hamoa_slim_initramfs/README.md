# Hamoa slim initramfs experiment (X1E80100 / kernel 7.0.0-1006-qcom)

Goal: shrink the Ubuntu initramfs on the Hamoa board from the default
`MODULES=most` image (~82 MB, ~2500 modules) to a minimal UFS-root image
(~25 MB, 26 modules), and fix the boot regressions that a slim image exposes.

Board: Hamoa IoT EVK, `ubuntu@192.168.1.123` (pw `changeme12`),
serial console `/dev/ttyUSB0` @ 115200 (kernel `console=ttyMSM0`),
root `/dev/sda2` ext4, root UUID `8e7d0df9-1dcf-4c7d-9c3b-e98b8bdf76c1`.

---

## TL;DR results

| Item | Status |
|------|--------|
| Default initramfs size | 82 MB / ~2500 modules (`MODULES=most`, dracut) |
| Slim initramfs built | 25 MB / 26 modules |
| Built-in clock controllers (camcc/dispcc/gpucc/videocc) | **applied + verified** (commit `8efb801`, kernel `1006.9`) |
| SMMU TLB-sync deadlock (modular-clk failure mode) | **root-caused** |
| Slim initramfs booting to userspace | **NOT confirmed** — still fails (see below) |
| Safe test harness (one-shot GRUB + `initrdfail` fallback) | **works — no reflash needed** |

The headline: the **kernel config change is sound and merged locally**, but the
slim initramfs has **not** been observed booting to userspace on this board.
Two distinct slim failure modes were seen (details below). The default 82 MB
image remains the reliable production image.

---

## 1. Why the default image is huge

Ubuntu 26.04 on this board uses **dracut** (not initramfs-tools) with
`MODULES=most`. dracut's `70kernel-modules/module-setup.sh` hard-codes, for
aarch64, whole driver subsystems (`=drivers/clk`, `=drivers/gpio`,
`=drivers/hwmon`, `=drivers/regulator`, …) regardless of the actual hardware,
plus the `copymods` module copies the entire module tree. Result: ~2500 modules.

`dracut --hostonly` barely helps (still ~2490 modules) because of that aarch64
policy. The only effective lever is `hostonly_mode=strict` with an explicit
`drivers=` list (see `hamoa-slim-dracut.conf`).

## 2. UFS root dependency closure (why only 26 modules are needed)

Walking `/dev/sda2` → `1d84000.ufshc` suppliers in sysfs
(`/sys/bus/platform/devices/1d84000.ufshc/supplier:*`) shows **every UFS
supplier is built into the kernel**:

| Supplier | Driver | Type |
|---|---|---|
| UFS QMP PHY (`1d80000.phy`) | `phy-qcom-qmp-ufs` | builtin |
| GCC clocks (`100000.clock-controller`) | `gcc-x1e80100` | builtin |
| apps SMMU (`15000000.iommu`) | `arm-smmu` | builtin |
| interconnect ×4 (`1600000`, `16e0000`, `26400000`, `interconnect-1`) | `qnoc-x1e80100` | builtin |
| RPMh (`17500000.rsc`) + regulators | `rpmh`, `qcom-rpmh-regulator` | builtin |
| TLMM pinctrl (`f100000.pinctrl`) | `x1e80100-tlmm` | builtin |
| disk / SCSI / fs | `sd_mod`, `scsi_mod`, `ext4`, `jbd2`, `mbcache`, `crc16` | builtin |

The **only** non-builtin pieces are the four UFS modules themselves —
`ufs_qcom`, `ufshcd-core`, `ufshcd-pltfrm`, `rpmb-core` — which are in the slim
`drivers=` list. The rest of the slim list is Qualcomm platform glue
(`socinfo`, `tee`, `qrtr*`, `mhi`, `rpmsg_*`, `llcc_qcom`, …) and Ubuntu
initramfs infra (`autofs4`, `dm_multipath`, `nls_iso8859_1`).

### UFS module autoload caveat (the `force_drivers` fix)

`ufs_qcom` is autoloaded by **udev coldplug** matching the device's OF modalias,
not via `modules-load.d`. The device's preferred compatible is
`qcom,x1e80100-ufshc`, but `ufs_qcom` only advertises the fallback aliases
`qcom,sm8550-ufshc` / `qcom,ufshc`. In a strict `drivers=` build the autoload
chain is fragile, so the slim conf adds:

```
force_drivers+=" ufs_qcom ufshcd_pltfrm "
```

which makes dracut emit `etc/cmdline.d/20-force_drivers.conf` with
`rd.driver.pre=ufs_qcom` / `rd.driver.pre=ufshcd_pltfrm` to force-modprobe the
UFS glue early. (Verified present in the built image; `modules.alias` also
carried the 8 `ufshc` entries and `modules.dep` was intact.)

## 3. Built-in clock controllers (the SMMU-deadlock fix)

### Problem

With a slim initramfs on the **stock (modular-clk)** kernel, the board
boot-loops spamming:

```
arm-scmi arm-scmi.0.auto: timed out in resp(...)   /  failed to get mon current frequency
arm-smmu 15000000.iommu: TLB sync timed out -- SMMU may be deadlocked
arm-smmu 15000000.iommu: TBU: power_status 0xff sync_inv_ack 0x1bf sync_inv_progress 0x0
```

Root cause: the apps_smmu (`15000000`, `qcom,x1e80100-smmu-500`) is built-in and
serves UFS/USB/PCIe/**camera ISP**/**display**. Each per-master SMMU **TBU** is
power-gated by the client's **GDSC**, supplied by the camera/display/gpu/video
clock controllers. When those controllers are **modules**, a built-in SMMU
client can issue a TLB sync against a still-unpowered TBU before the modular
GDSC provider registers → "SMMU may be deadlocked". `power_status 0xff` = the
TBU never reported powered/clocked. SCMI timeouts are collateral (the wedged
SMMU starves the IPCC mailbox IRQ; `icc_bwmon` then spams the freq error).

### Fix (committed)

Build the four controllers in, matching `CLK_X1E80100_GCC`. Edit
`debian.qcom/config/annotations` in `~/qualcomm/linux`:

```
CONFIG_CLK_X1E80100_CAMCC   policy<{'arm64': 'y'}>
CONFIG_CLK_X1E80100_DISPCC  policy<{'arm64': 'y'}>
CONFIG_CLK_X1E80100_GPUCC   policy<{'arm64': 'y'}>
CONFIG_SM_VIDEOCC_8550      policy<{'arm64': 'y'}>   # x1e80100 reuses sm8550 videocc
```

Validate, build, deploy:

```bash
cd ~/qualcomm/linux
python3 debian/scripts/misc/annotations --arch arm64 --flavour qcom \
    --query --config CONFIG_CLK_X1E80100_CAMCC      # -> "y"
git commit -am "UBUNTU: [Config] qcom: build x1e80100 camcc/dispcc/gpucc/videocc in"
~/qualcomm/ai_effort/qpa/cbd-deploy.sh --no-reboot  # build on CBD + install on board
```

Committed as `8efb801` ("UBUNTU: [Config] qcom: build x1e80100
camcc/dispcc/gpucc/videocc in"); CBD build `kpawlak-resolute-8efb801ad5fc-3844`;
kernel package `7.0.0-1006.9`.

Verified on the board: all four are `=y` in `/boot/config-$(uname -r)`, present
in `modules.builtin`, and no longer `.ko` files.

---

## 4. Build the slim initramfs

```bash
# On the board. Build to a SEPARATE file; never overwrite the working default.
sudo dracut --force --conf hamoa-slim-dracut.conf \
     /boot/initrd.img-$(uname -r).slim $(uname -r)

# Inspect
sudo lsinitrd /boot/initrd.img-$(uname -r).slim | grep -c '\.ko'   # -> 26
sudo lsinitrd /boot/initrd.img-$(uname -r).slim -f etc/cmdline.d/20-force_drivers.conf
```

The 3 `dracut[E]` lines about `net-lib`/`overlayfs` being omitted are expected
and harmless.

> Do **not** drop `hamoa-slim-dracut.conf` into `/etc/dracut.conf.d/` — that
> would make every `update-initramfs` / kernel upgrade produce the slim image
> for the DEFAULT initrd. Keep it out-of-tree and pass it with `--conf`.

## 5. Test it SAFELY (no reflash)

The reliable harness — a non-default GRUB entry pointing at the `.slim` file,
booted once via `grub-reboot`:

```bash
# Install the one-shot entry (edit KVER/UUID inside first)
sudo cp 42_slimtest /etc/grub.d/42_slimtest && sudo chmod +x /etc/grub.d/42_slimtest
sudo update-grub

# Boot the slim entry ONCE
sudo grub-reboot slimtest && sudo reboot
```

Why this is safe:
- The default entry still uses the **stock** 82 MB initrd.
- Ubuntu GRUB's `initrdfail` logic (grub.cfg lines ~20-25): if the slim boot
  does not reach `grub-initrd-fallback.service` in late userspace, the **next**
  boot is forced to the previous good entry (stock). So a failed slim boot
  self-heals on the next power-cycle.
- If the slim kernel hangs hard (no auto-reset), just power-cycle:
  `sudo ~/qualcomm/carmel-tools/alpaca.py off && sleep 3 && \
   sudo ~/qualcomm/carmel-tools/alpaca.py on` → boots stock.

### Telling slim vs stock boots apart (important when reading serial)

Capturing serial during these tests is timing-sensitive; use these markers in
the kernel log to know WHICH initrd actually booted:

| Marker | Stock | Slim |
|---|---|---|
| `Freeing initrd memory:` | `83760K` | `~25600K` |
| `crashkernel=` in cmdline | present | absent (42_slimtest omits it) |
| `copymods is deprecated` warning | present | absent |

Several "it reached initqueue / userspace" observations during this work turned
out to be **stock fallback boots** (initrdfail), identified by the 83760K /
crashkernel / copymods markers. Always confirm the initrd before trusting a
"success".

---

## 6. Results / open issue

- **Built-in clock controllers**: applied and verified. Sound change; keep it.
- **Slim initramfs**: still does **not** boot to userspace. Two failure modes:
  - **Modular-clk kernel (1006.8):** SMMU TLB-sync deadlock spam (captured).
  - **Built-in-clk kernel (1006.9):** the slim kernel produces **zero console
    output after the Gunyah hypervisor handoff** (`Exit EBS … Gunyah based
    bootup`) across multiple long continuous serial captures — a reproducible
    early silent hang, before `earlycon`. Same kernel + cmdline boot fine with
    the stock initrd, so it is initrd-specific. Not yet root-caused; suspect an
    initrd placement / hypervisor guest-memory interaction rather than a missing
    module (module set, `modules.alias`, `modules.dep`, and `force_drivers` are
    all correct).

### Suggested next debugging steps (not yet done)

1. Capture the slim kernel boot with a **single uninterrupted** serial log that
   spans EBS→kernel (the GRUB 30 s countdown + UEFI keep pushing the handoff to
   the end of fixed-length captures; reduce the `42_slimtest` GRUB timeout or
   capture ≥400 s in one shot).
2. Add `rd.debug rd.shell` (and remove `earlycon` vs add `keep_bootcon`) to the
   `42_slimtest` cmdline to get an emergency shell / more early output.
3. Compare the slim vs stock initrd load address / size assumptions under
   Gunyah; try building the slim image **without** `compress=zstd` (e.g.
   `compress=gzip`) to rule out a decompressor issue in the guest.
4. Bisect the slim `drivers=` list (the `force_drivers` early modprobe of
   `ufs_qcom` happens very early — temporarily drop `force_drivers` and rely on
   coldplug to see if the hang moves).

---

## 7. Recovery cheatsheet

- **Slim boot hung** → power-cycle; `initrdfail` boots stock automatically.
  ```bash
  sudo ~/qualcomm/carmel-tools/alpaca.py off && sleep 3 && sudo ~/qualcomm/carmel-tools/alpaca.py on
  ```
- **grubenv left dirty** (stale `next_entry`/`recordfail`):
  ```bash
  sudo grub-editenv /boot/grub/grubenv unset next_entry
  sudo grub-editenv /boot/grub/grubenv unset recordfail
  sudo grub-editenv /boot/grub/grubenv unset initrdfail
  ```
- **Board fully wedged / default also broken** → reflash (see
  `../qpa/SKILLS.md`, "Flash Hamoa"):
  ```bash
  sudo ~/qualcomm/carmel-tools/alpaca.py off && sleep 2 && sudo ~/qualcomm/carmel-tools/alpaca.py edl
  lsusb | grep 05c6:9008   # confirm EDL
  cd ~/qualcomm/hamoa/IQ-X.1.7-Ver.1.1-ubuntu-X1E80100-nhlos-bins/
  sudo qdl --storage ufs xbl_s_devprg_ns.melf \
      partition_ufs/rawprogram[0-9].xml partition_ufs/patch[1-9].xml --include partition_ufs/
  sudo ~/qualcomm/carmel-tools/alpaca.py off && sleep 3 && sudo ~/qualcomm/carmel-tools/alpaca.py on
  # First boot: default password `ubuntu` is expired -> change to `changeme12`.
  ```
  Reflash installs the STOCK kernel; reinstall the built-in-clk kernel with:
  ```bash
  cd ~/qualcomm/linux
  ~/qualcomm/ai_effort/qpa/cbd-deploy.sh --no-push \
      --build-id kpawlak-resolute-8efb801ad5fc-3844 --no-reboot
  ```

## 8. Files in this directory

- `hamoa-slim-dracut.conf` — the slim dracut config (with `force_drivers` fix).
- `42_slimtest` — one-shot-safe GRUB entry template (edit KVER + root UUID).
- On the board (left in place for further testing):
  `/boot/initrd.img-7.0.0-1006-qcom.slim` and `/etc/grub.d/42_slimtest`.

## 9. Note on the DPU display errors

During testing the serial log shows repeated
`[drm:dpu_encoder_phys_vid_wait_for_commit_done] vblank timeout` /
`enc41 frame done timeout`. These appear on the **stock** boot too (the board is
headless / no panel on that encoder) and are **unrelated** to the initramfs
work — cosmetic.
