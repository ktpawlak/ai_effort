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
| "Silent post-Gunyah hang" of the slim entry | **root-caused = test-harness bug** (custom GRUB entry was missing the `devicetree` line; fixed) |
| Slim image boots the kernel | **YES** (confirmed `Booting Linux … 1006.9` after the devicetree fix) |
| Slim booting to userspace | **NO — but now blocked by a *separate* SMMU TBU deadlock** (see below). The SCMI issue is fixed. |
| `CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ=m` fix | **APPLIED + VERIFIED** (commit `73e27f14398b`, CBD build `8994`). Eliminated the SCMI timeout storm on slim: **192 → 0** `mon current frequency` failures. |
| Remaining slim blocker | `arm-smmu 15000000.iommu: TLB sync timed out … TBU power_status 0xff` at ~12 s (dracut-initqueue) — now with **no SCMI precursor**, so a distinct TBU-power issue, not SCMI. |
| Safe test harness | one-shot GRUB + `initrdfail` is fragile (grubenv corruption); **menu-select is reliable** (15 s timeout set) |

The headline: the **kernel config change is sound and merged locally.** The
earlier "slim initramfs doesn't boot / silently hangs" result was **invalid** —
it was caused by a bug in the *test harness*, not the slim image: the
hand-written `42_slimtest` GRUB entry omitted the `devicetree` line, so the
kernel booted with no DTB, couldn't bring up the console, and hung before the
initramfs was ever unpacked. With that fixed, the slim image has **not yet had a
fair test**; the corrected one-shot entry is armed for a manual boot.

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

> **The `42_slimtest` entry MUST include the `devicetree` line** (see that file).
> On this board the kernel resolves `console=ttyMSM0`/`earlycon` through the
> DTB's `chosen/stdout-path`. A menuentry without `devicetree` boots with no DTB,
> produces **zero console output**, and hangs in early arch setup *before the
> initramfs is unpacked*. An earlier version of this entry omitted the line and
> produced a bogus "slim hangs silently" result — see §6.

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
the kernel log to know WHICH initrd actually booted. (The `42_slimtest` entry
now matches the stock cmdline including `crashkernel`, so use the initrd-size
and `copymods` markers.)

| Marker | Stock | Slim |
|---|---|---|
| `Freeing initrd memory:` | `83760K` | `~25600K` |
| `copymods is deprecated` warning | present | absent |

Several "it reached initqueue / userspace" observations during this work turned
out to be **stock fallback boots** (initrdfail), identified by the 83760K /
copymods markers. Always confirm the initrd before trusting a "success".

---

## 6. Results / open issue

- **Built-in clock controllers**: applied and verified. Sound change; keep it.
  Fixes the SMMU TLB-sync deadlock — a confirmed-slim boot now gets well past it.
- **"Silent post-Gunyah hang" — RESOLVED, was a test-harness bug.** The earlier
  conclusion that the slim image "hangs silently after the Gunyah handoff" was
  **wrong**. Cause: the hand-written `42_slimtest` GRUB entry omitted the
  `devicetree /boot/dtb-<KVER>` line that the stock entry has. Without a DTB the
  kernel cannot map its console (`console=ttyMSM0`/`earlycon` come from the DTB
  `chosen/stdout-path`), so it produced zero output and faulted in early arch
  setup — *upstream of, and unrelated to, the initramfs*. Tell-tale: the
  initramfs is unpacked late (`populate_rootfs`); a genuinely bad initramfs
  would still print the kernel banner + `Unpacking initramfs` + a panic. Total
  silence ⇒ the failure is before console init ⇒ not an initramfs problem.
- **Slim DOES boot the kernel now** (confirmed: `Booting Linux … 7.0.0-1006.9`
  after selecting the fixed slimtest entry). The remaining blocker is an **SCMI
  transport timeout**, not a missing module:

  ```
  arm-scmi arm-scmi.0.auto: timed out in resp(caller: do_xfer+0x15c/0x900)
  failed to get mon current frequency        # repeats every ~40ms from ~10.7s
  ... then the board resets at ~11s
  ```

  SCMI handshakes fine early (`Firmware version 0x20000` ~6 s, same as stock),
  then a consumer's periodic query starts timing out at ~10.7 s and the board
  resets at ~11 s.

### Root cause (CONFIRMED by experiment)

The failing message comes from **`drivers/devfreq/scmi-qcom-memlat-devfreq.c:295`**
(`scmi_qcom_devfreq_get_cur_freq` → `pr_err("failed to get mon current
frequency")`). This is the Qualcomm **SCMI memlat (memory-latency) devfreq**
driver (`CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ=y`, **builtin**). It manages
DDR/LLCC/DDR_QOS bus frequencies by talking to the **CPUCP** over the Qualcomm
**SCMI vendor protocol** (`QCOM_SCMI_GENERIC_EXT`), and its devfreq monitor polls
`MEMLAT_GET_CUR_FREQ` every ~40 ms.

Full failure chain on a slim boot:

1. SCMI handshakes fine at ~6 s (builtin, identical to stock).
2. At ~10 s, `dracut-initqueue` starts and the system goes **quiet** (only 26
   modules; CPUs drop to idle / low frequency).
3. The CPUCP stops servicing SCMI promptly → the memlat devfreq poll
   (`MEMLAT_GET_CUR_FREQ`) **times out** (`do_xfer` 30 ms) → "failed to get mon
   current frequency", repeating.
4. The wedged SCMI transport means **CPUCP-managed power** for the apps_smmu
   **TBUs** never comes up → `arm-smmu 15000000.iommu: TLB sync timed out … TBU
   power_status 0xff` (the SMMU "deadlock" is a **downstream symptom of SCMI**,
   not a clock-controller problem).
5. Boot stalls → SBSA watchdog (`sbsa-gwdt … 10s timeout`) or the failure
   cascade resets the board → `initrdfail` falls back to the stock entry.

Everything in this chain is **builtin and identical** in stock and slim. The
only variable is **how busy the CPUs are during the root-mount window**:

| Test (slim cmdline) | `mon current frequency` failures | Result |
|---|---|---|
| baseline | ~190+ | fails |
| `cpuidle.off=1` | ~190+ (no change) | fails — **idle ruled out** |
| `cpufreq.default_governor=performance` | **26** (big drop) | still fails |

→ **CPUCP SCMI responsiveness scales with AP frequency/activity.** The default
`MODULES=most` initramfs masks the bug because it keeps the CPUs pegged loading
~2500 modules; the slim image leaves them idle, so the CPUCP starves the SCMI
channel. This is fundamentally a **CPUCP-firmware/platform timing behaviour**
exposed by the unusually-idle slim boot — not a missing module.

### The memlat=module fix — APPLIED, fixed SCMI (commit `73e27f14398b`)

Built the memlat devfreq driver as a **module** instead of builtin:

```
# debian.qcom/config/annotations
CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ   policy<{'arm64': 'm'}>
```

It is not in the slim `drivers=` list, so it **does not probe during the slim
initramfs** — no early memlat SCMI polling. Built on CBD (`8994`), deployed, and
tested with a confirmed-slim boot (`Freeing initrd memory: 25400K`):

| Metric | builtin memlat | **memlat=module** |
|---|---|---|
| `mon current frequency` failures | ~190+ | **0** |
| SCMI `do_xfer` timeouts | many | **0** |

✅ **The SCMI timeout storm is gone.** This confirms the root-cause analysis: the
builtin memlat devfreq polling the CPUCP during the idle initramfs was what
wedged SCMI.

### Remaining blocker — a separate SMMU TBU-power deadlock

With SCMI now healthy, the confirmed-slim boot **still fails**, but on a
*different* fault that was previously masked:

```
[   12.28] arm-smmu 15000000.iommu: TLB sync timed out -- SMMU may be deadlocked
[   12.29] arm-smmu 15000000.iommu: TBU: power_status 0xff sync_inv_ack 0x1bf ...
```

- Appears at ~12 s, right after `dracut-initqueue` starts, repeating ~1 Hz, then
  the board resets and `initrdfail` falls back to stock.
- **No SCMI timeout precedes it now**, so it is *not* the SCMI cascade — it is a
  genuine apps_smmu **TBU left unpowered** (`power_status 0xff`) when a master
  does DMA during the root-mount window.
- This is the same signature as the very first boot-loop. The earlier conclusion
  that the built-in clock-controllers (`8efb801`) "fixed" it was drawn from
  **stock fallback** boots; on a real slim boot the deadlock is still present.

Likely suspects (next experiments):
1. **`force_drivers=ufs_qcom`** loads UFS abnormally early (before the normal
   power/probe ordering). Rebuild the slim image **without** `force_drivers`
   (rely on the `modules.alias` ufshc coldplug entries, which are present) and
   re-test — if the deadlock moves/clears, the forced-early UFS DMA was racing
   its TBU power-up.
2. Identify the exact master/SID behind the unpowered TBU (the SMMU print does
   not name it; enable `CONFIG_ARM_SMMU_QCOM_DEBUG` SID logging / `initcall_debug`
   and correlate the probe immediately before 12.28 s).
3. The TBU's GDSC provider may still be a module absent from slim (a master whose
   power domain comes from a clock/power controller not in the 26-module set).

### force_drivers / watchdog / PCIe experiments — CORRECTED understanding

⚠️ An intermediate conclusion here was **wrong** and is corrected below. The
SMMU deadlock is **not** caused by the forced-early UFS load.

What actually happens (verified across several boots):

| slim cmdline | SMMU deadlock | UFS loads? | why |
|---|---|---|---|
| `force_drivers=ufs_qcom` | 9× @ ~12.3 s | yes (early) | deadlock, then watchdog reset |
| no `force_drivers` | **appeared 0** | no | **misleading** — the SBSA watchdog reset the board at ~11.7 s *before* the ~12.3 s deadlock could occur |
| no force + `initcall_blacklist=sbsa_gwdt_init` (watchdog off) | **9× again** | **no** | deadlock returns once the early reset is removed |
| no force + watchdog off + `pci=off` | 9× | no | deadlock persists |

Corrected findings:

1. **The SMMU TBU deadlock (`15000000.iommu … power_status 0xff`) is independent
   of UFS / `force_drivers`.** It occurs at ~12.3 s even when `ufs_qcom` never
   loads. Removing `force_drivers` only *appeared* to fix it because the **SBSA
   watchdog resets the board at ~11.7 s** (the sparse slim boot never mounts root
   / `switch_root`s in time, so nothing pets the 10 s `sbsa-gwdt`), *before* the
   deadlock timestamp. So my earlier "force_drivers causes the deadlock" claim is
   **retracted.**
2. **The deadlocking master is likely PCIe** (`1bd0000.pcie`): the deadlock fires
   immediately after that controller's OPP/probe lines at ~11–12 s. But `pci=off`
   did **not** clear it (the qcom platform PCIe controller still probes), so this
   is not yet pinned/fixed.
3. **UFS does not autoload in the slim image** by any late mechanism tried (udev
   coldplug — despite the 8 matching `ufshc` `modules.alias` entries and the
   `80-drivers.rules` `kmod load` rule both being present — nor `rd.driver.post`).
   Only `force_drivers`/`rd.driver.pre` ever loads it. In the no-force boots the
   board also reset (watchdog) before coldplug had a chance, so this is partly
   confounded by the watchdog.

### Net state — multiple independent blockers

The slim initramfs on this board has turned out to have **several** independent,
SoC-specific problems, not one:

| # | Blocker | Status |
|---|---------|--------|
| 1 | GRUB entry missing `devicetree` (silent hang) | ✅ fixed (harness) |
| 2 | builtin memlat devfreq wedges SCMI when idle | ✅ fixed (`MEMLAT_DEVFREQ=m`, `73e27f14398b`) |
| 3 | apps_smmu TBU `power_status 0xff` deadlock @ ~12 s (PCIe-ish master) | ❌ open |
| 4 | UFS does not coldplug-autoload in strict slim image | ❌ open |
| 5 | SBSA watchdog resets @ ~12 s when root isn't mounted yet | ❌ open (masks #3/#4) |

Two of five are fixed. The remaining three are entangled (the watchdog masks the
SMMU fault; the SMMU fault and the UFS-load gap both block reaching `switch_root`).

### Honest assessment

A working slim initramfs on this X1E80100 board is **not achieved**. The
remaining faults are deep SoC power/clock/SMMU/PCIe interactions that only appear
because the slim boot leaves the system unusually idle/sparse during the
root-mount window — the same class of problem as the (now-fixed) memlat/SCMI bug.
Each further step costs a reflash-risky boot cycle. Recommendation: unless the
size saving is critical, **keep the stock `MODULES=most` image** (which is
reliable). If pursued, the next concrete steps are: (a) pin the master behind the
`15000000` TBU via `CONFIG_ARM_SMMU_QCOM_DEBUG` SID logging + `initcall_debug`,
(b) ensure that master's TBU power domain / interconnect path is up early, and
(c) get `ufs_qcom` to coldplug-load (try `add_drivers=` instead of `drivers=`).

### Levers tried (none a full fix)

- `cpuidle.off=1` — no effect on the SCMI issue (idle ruled out).
- `cpufreq.default_governor=performance` — cut SCMI failures ~7× (pre-memlat-fix).
- `initcall_blacklist=sbsa_gwdt_init` — stops the ~11.7 s reset but exposes the
  persistent ~12.3 s SMMU deadlock underneath.
- `pci=off` — did not stop the qcom PCIe controller probe / the deadlock.

### Runtime-set (~177 modules) test — the decisive experiment

Built the slim image from the **exact `lsmod` runtime set** (177 modules, 41 MB)
on the memlat-fixed kernel — i.e. "make the initramfs ≈ the booted system".

| | minimal (26) | **runtime (177)** | stock (~2500) |
|---|---|---|---|
| SMMU TBU deadlock | 9× | **0** ✅ | 0 |
| SCMI `mon freq` failures | 0 (memlat=m) | **0** ✅ | 0 |
| boots to userspace | no | **no** | yes |

The runtime set **does** eliminate the SMMU deadlock and SCMI faults — proving
those come from the minimal set stripping the power/PHY/interconnect provider
modules that the builtin early-probing masters need. So "minimal can't work,
more modules is the right direction" is **confirmed**.

**But the runtime set still does not boot** — it hits a *different, deeper*
failure: a **full hardware/Gunyah-hypervisor reset at ~10.6 s** during the mass
module coldplug in `dracut-initqueue`, going all the way back through UEFI. There
is **no kernel-visible cause** (no panic/oops/SMMU/watchdog message — the reset
is below the guest, at the Gunyah/firmware level). Watchdog levers
(`initcall_blacklist=sbsa_gwdt_init`, `sbsa_gwdt.timeout=60`) do **not** change
it, so it is not the SBSA watchdog. Tested 3× — always a clean full reset at
~10.6 s.

### Final conclusion

**Neither the minimal (26) nor the runtime (177) set produces a working boot.**
Each trim of the initramfs surfaces a new SoC/hypervisor-level fault that the
validated stock `MODULES=most` image does not hit:

- minimal → SMMU TBU deadlock (missing power providers) [+ the now-fixed
  memlat/SCMI and devicetree issues],
- runtime → a Gunyah/firmware-level reset during mass coldplug (opaque, no guest
  log).

On this **Gunyah-virtualized X1E80100** board a trimmed initramfs is **not
practical** without vendor/firmware-level debugging access. **Recommendation:
keep the stock `MODULES=most` image.** The one durable, upstream-worthy result
from this whole effort is the **`CONFIG_SCMI_QCOM_MEMLAT_DEVFREQ=m`** change — a
genuine latent bug (builtin memlat starves SCMI in any low-activity boot) worth
keeping regardless of the slim-initramfs goal.

### Levers tried (full list, none a complete fix)

- `cpuidle.off=1` — no effect (idle ruled out).
- `cpufreq.default_governor=performance` — cut SCMI failures ~7× (pre-memlat-fix).
- `initcall_blacklist=sbsa_gwdt_init` / `sbsa_gwdt.timeout=60` — do not change the
  runtime-set ~10.6 s reset (not the SBSA watchdog).
- `pci=off` — did not stop the qcom PCIe controller probe / the deadlock.

> Lesson learned: when hand-writing a GRUB entry on these boards, copy the
> stock `Ubuntu` menuentry's `linux` + `initrd` + `devicetree` lines verbatim
> and change only the initrd path.

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
