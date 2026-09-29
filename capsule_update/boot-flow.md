# Qualcomm boot flow and partition layout

Where the firmware, the DTBs and the kernel actually live, and where the DTB
capsule update fits in.

Derived from the vendored partition table in the tree:
`debian.qcom/qcom-ptool/platforms/iq-x7181-evk/spinor/partitions.conf`
(IQ-X7181 EVK, SPI-NOR), plus the runtime scripts under
`debian.qcom/templates/` and `debian.qcom/dtb-capsule-runtime/`.

Platform mapping (`UpdateFvXml.py` `SUPPORTED_PLATFORMS`):
`hamoa` -> `IQ-X7181`, `purwa` -> `IQ-X5121`; both resolve to the ptool
directory `iq-x7181-evk`, and `NORUFS` / `NORNVME` both resolve to the
`spinor` subdirectory. One vendored `partitions.conf` therefore covers both
machines.

---

## 1. Storage topology — two separate media

```
┌─ SPI-NOR  64 MiB ──────────────┐   ┌─ UFS / NVMe ────────────────────┐
│  boot flash, GPT               │   │  OS storage, GPT                │
│  all firmware + Linux DTB      │   │                                 │
│                                │   │  ┌───────────────────────────┐  │
│  everything in diagram 2       │   │  │ ESP  (FAT32) /boot/efi    │  │
│                                │   │  │   EFI/ubuntu/grubaa64.efi │  │
│                                │   │  │   EFI/UpdateCapsule/*.cap │  │
│                                │   │  └───────────────────────────┘  │
│                                │   │  ┌───────────────────────────┐  │
│                                │   │  │ rootfs (ext4)             │  │
│                                │   │  │   /boot/vmlinuz-<kver>    │  │
│                                │   │  │   /boot/initrd.img-<kver> │  │
│                                │   │  └───────────────────────────┘  │
└────────────────────────────────┘   └─────────────────────────────────┘
        --disk --type=spinor
        --size=67108864
        --sector-size-in-bytes=4096
```

This split is why the capsule tool's storage type is spelled `NORUFS` /
`NORNVME` — NOR for boot, UFS or NVMe for the OS.

**The kernel is not on the SPI-NOR at all.** Only the DTB is. The kernel and
initrd live on the normal Ubuntu rootfs and are loaded by GRUB.

---

## 2. Boot chain, and which partition feeds each stage

```
  BootROM (PBL, on-die, immutable)
      │  verifies ──────────────────────────────┐
      ▼                                         │
  XBL_SC          xbl_s.melf        2300 KB  ───┤ ← XBL_SC_BACKUP
      │  reads                                  │
      ▼                                         │
  XBL_CONFIG      xbl_config.elf     400 KB  ───┤ ← XBL_CONFIG_BACKUP
      │                                         │
      │  loads the subsystem blobs:             │   all chain up to the
      ├── TZ        tz.mbn          8192 KB  ───┤   fused Qualcomm root key
      ├── HYP       hypvm.mbn       2436 KB  ───┤
      ├── AOP       aop.mbn          340 KB  ───┤
      ├── DEVCFG    devcfg_iot.mbn   128 KB  ───┤
      ├── CPUCP     cpucp.elf        952 KB  ───┤
      │     └── CPUCP_DTB   cpucp_dtbs.elf 48 KB ──┤   ← DTB #1
      ├── SHRM      shrm.elf         128 KB  ───┤
      ├── QUP       qupv3fw.elf       96 KB  ───┤
      ├── ADSP_UEFI adsp_lite.lzma  1332 KB  ───┤
      │     └── ADSP_UEFI_DTB adsp_dtbs.elf 96 KB ─┤ ← DTB #2
      └── ImageFv   imagefv.elf      136 KB  ───┘
      │
      ▼
  UEFI            uefi.elf          7168 KB  ←── UEFI_BACKUP
      │  ├── uefi_dtb    uefi_dtbs.xz   64 KB      ← DTB #3
      │  ├── uefisecapp  uefi_sec.mbn  220 KB
      │  ├── VarStore                  728 KB   (NVRAM: OsIndications,
      │  │                                       BootNext, BootOrder, ESRT)
      │  └── SYSFW_VERSION               4 KB   (firmware version block)
      │
      │  ══ capsule processing happens HERE, before any OS code runs ══
      │
      ▼  reads ESP on UFS / NVMe
  GRUB   EFI/ubuntu/grubaa64.efi
      │
      ▼  loads from rootfs
  Linux  vmlinuz-<kver> + initrd.img-<kver>
      ▲
      └── DTB passed in by UEFI, sourced from:

          dtb_a   dtb.bin   4096 KB     ← DTB #4 — the Linux DTB
          dtb_b   dtb.bin   4096 KB       true A/B pair
```

### Redundancy: two different schemes

| Suffix | Meaning | Used by |
|---|---|---|
| `_BACKUP` | fallback copy, not an alternating slot | XBL_SC, XBL_CONFIG, UEFI, TZ, HYP, AOP, DEVCFG, CPUCP, SHRM, QUP, ADSP, ImageFv, uefi_dtb, uefisecapp, APDP, MULTIIMG*, qweslicstore |
| `_a` / `_b` | true A/B slots | **`dtb_a` / `dtb_b` only** |

---

## 3. The DTB trap — there are four kinds

Easy to get wrong, and worth stating explicitly to anyone new to the platform:

| Partition | Size | Content | Consumer | Capsule updates it? |
|---|---|---|---|---|
| `CPUCP_DTB` | 48 KB | `cpucp_dtbs.elf` | CPU cluster power controller | no |
| `ADSP_UEFI_DTB` | 96 KB | `adsp_dtbs.elf` | ADSP firmware | no |
| `uefi_dtb` | 64 KB | `uefi_dtbs.xz` | UEFI itself | no |
| **`dtb_a` / `dtb_b`** | 4096 KB | `dtb.bin` | **Linux** | **yes** |

Only `dtb_a`/`dtb_b` carry the Linux devicetree, and they are the only pair
using true A/B slots.

`--update-partitions dtb` in the capsule tool resolves to this pair:
`find_base_names()` (`UpdateFvXml.py:145-149`) strips the `_a`/`_b` suffix, so
the sibling rename from a single `dtb` partition still resolves correctly.

Note also that `dtb_a`/`dtb_b` do **not** hold a raw `.dtb`. They hold
`dtb.bin` — a FAT wrapper containing a FIT image holding every qcom DTB, which
UEFI indexes into by board. See
[`fit-image-comparison.md`](fit-image-comparison.md) for how that image is
built and how it differs from the existing `qcom.itb`.

---

## 4. Capsule update flow

```
 postinst                      reboot                      next boot
 ────────                      ──────                      ─────────
 write .cap to                                    UEFI scans EFI/UpdateCapsule/
 ESP:/EFI/UpdateCapsule/  ───►              ───►  verify PKCS#7 ─► QcCapsuleRootCert
   qcom-dtb-<kver>.cap                                 │            (fused, immutable)
                                                       ▼
 set OsIndications bit 2                         write dtb.bin ─► dtb_a or dtb_b
 (0x4, FILE_CAPSULE_                                   │
  DELIVERY_SUPPORTED)                                  ▼
 via efivar ─► VarStore                          update SYSFW_VERSION + ESRT
                                                       │
                                                       ▼
                                                 GRUB ─► kernel ─► userspace
                                                       │
                                                       ▼
                                          dtb-capsule-verify.service reads ESRT
                                          (the only post-boot check that exists)
```

Paths from `debian.qcom/templates/dtb-capsule.postinst.in:17-18`:

```sh
ESP_MOUNT="${ESP_MOUNT:-/boot/efi}"
CAPSULE_DIR="${CAPSULE_DIR:-${ESP_MOUNT}/EFI/UpdateCapsule}"
```

Two things this makes visible:

1. **The capsule crosses media.** It is staged on UFS/NVMe (the ESP) and lands
   on SPI-NOR (`dtb_a`/`dtb_b`). Nothing in Linux performs that write — UEFI
   does it.
2. **Verification happens entirely in UEFI, before Linux starts.** GRUB and the
   kernel do no capsule checking whatsoever; there are no kernel patches, no
   GRUB script and no initramfs hook in the PR. The only post-boot check is the
   userspace `dtb-capsule-verify.service`, which runs after
   `multi-user.target` and merely *reports* on the ESRT result.

Consequence: if the firmware rejects the capsule, the system boots normally on
the old DTB and the only signal is an ESRT status code surfaced by that
service. See [`pr111-review.md`](pr111-review.md) for the failure-reporting
paths and [`capsule-signing-format.md`](capsule-signing-format.md) for what the
firmware actually verifies.

---

## 5. Why this matters for the capsule work

- The trust anchor `QcCapsuleRootCert` is consumed at the UEFI stage above,
  which is itself verified all the way back to a fused root. Nothing in the OS
  can influence that decision — which is why signing cannot be worked around
  in packaging.
- `SYSFW_VERSION` (4 KB) is the partition the capsule tool's step 1
  (`SYSFW_VERSION_program -Gen`) produces content for, and it is what makes
  `fwver` / `lsv` meaningful. A static `fwver` of `0.0.2.0` interacts with this
  partition, which is why the constant risks `ErrorIncorrectVersion`.
- `VarStore` is where `OsIndications` is written by `efivar` from the postinst.
  Without the `efivar` dependency, that write never happens and the staged
  capsule is silently ignored.
