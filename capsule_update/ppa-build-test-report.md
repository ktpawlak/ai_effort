# Testing the PPA capsule on hamoa (2026-10-05)

Target: [`ppa:kuba-t-pawlak/capsule`](https://launchpad.net/~kuba-t-pawlak/+archive/ubuntu/capsule),
`linux-signed-qcom 7.0.0-1016.19`, built from `d8fb7f8`.

**Tested on real hardware.** The board is not an SSH target in `~/.ssh/config`
— it is attached to this workstation over FTDI and documented in
`ai_effort/qpa` (`AGENTS.md`, `flash-hamoa.sh`), reachable at
`ubuntu@192.168.1.123`. Model string: *Qualcomm Technologies, Inc. Hamoa IoT
EVK*. Results in [On-hardware result](#on-hardware-result) below; the static
teardown that preceded it is kept because it predicted the outcome exactly.

## Where `d8fb7f8` sits in history

Before the capsule work in `linux-signed`:

```
d8fb7f8  UBUNTU: Ubuntu-qcom-7.0.0-1016.19     <-- PPA build
  e743fb7  Pin the FFS FileGuid ...            <-- introduced the empty-capsule bug
  64f9a31  Fix FileGuid pinning ...            <-- fixed it
  11f1900  reproducible + size guards
  6c634f2  FAT sector count
```

**This predates the empty-capsule regression**, and `XmlFwEntryValidation.py`
at `d8fb7f8` still has upstream's `raw_fwentry.FileGuid = meta_data_fwentry.FileGuid`.
Confirmed by inspection: the published capsules are 4,198,306 bytes, not 152.
So this PPA build is usable for testing, unlike anything from `e743fb7`…`f45c131`.

## What the published capsule actually contains

`dtb-capsule-7.0.0-1016-qcom_7.0.0-1016.19_arm64.deb` ships
`hamoa/hamoa-dtb.cap` and `purwa/purwa-dtb.cap`, both 4,198,306 bytes.

Parsing `hamoa-dtb.cap`:

| Field | Value | |
|---|---|---|
| `CapsuleGuid` | `6dcbd5ed-e82d-4c44-bda1-7194199ad92a` | ✅ FMP capsule ID |
| `Flags` | `0x00010000` | ✅ PersistAcrossReset |
| `CapsuleImageSize` | 4,198,306 | ✅ matches file |
| `PayloadItemCount` | 1, EmbeddedDriverCount 0 | ✅ |
| `UpdateImageTypeId` | `0f6d58fc-2258-4d27-9e23-d77219b0897c` | ✅ matches hamoa `capsule.env` |
| `UpdateImageIndex` | 1 | ✅ |
| auth `wCertType` | `0x0ef1`, `CertType` = PKCS7 GUID | ✅ well-formed |
| firmware payload | 4,195,728 bytes | ✅ real content |

Payload structure: `MSS1` FV header → FAT12 at `0x70` → `qclinux_fit.img`
(631,696 bytes) → trailing 1,312-byte `SYSFW_VERSION` FFS naming target
partition **`dtb_a`** (UTF-16 at offset 0x60), `FwVer` **0.0.2.0**, LSV 0.0.0.0.

The FIT carries **9 configurations / 10 images** — exactly the `--soc hamoa purwa`
set. The embedded `dtb.bin` is exactly 4,194,304 bytes, i.e. the partition size:
**no overflow**.

## 🔴 Blocker: signed with test keys

```
subject=O = INSECURE TEST KEYS - DO NOT TRUST, CN = Test Capsule Signer
issuer =O = INSECURE TEST KEYS - DO NOT TRUST, CN = Test Capsule Sub CA
        O = INSECURE TEST KEYS - DO NOT TRUST, CN = Test Capsule Root CA
```

This is the interim in-tree signing, not a Qualcomm OEM key. A production
hamoa verifies the PKCS7 against the OEM root provisioned in firmware, so
**it will reject this capsule**.

**That does not make the test pointless, and it is not dangerous.** Rejection
happens at signature verification, *before* anything is written to `dtb_a`, so
the board cannot be bricked by trying. What you get is an end-to-end exercise
of staging, `OsIndications`, firmware pickup and ESRT reporting — everything
except the final trust decision. Read the outcome from ESRT
`last_attempt_status`:

| Status | Meaning | Interpretation |
|---|---|---|
| 0 | Success | capsule was *applied* — only if the test root is enrolled |
| 1 | Unsuccessful | generic failure |
| 3 | Incorrect version | anti-rollback: device `fw_version` ≥ 0.0.2.0 |
| 4 | Invalid image format | our capsule/FV layout is wrong — a real bug |
| 5 | Auth error | the spec'd code for this — **but not what Hamoa reports** |

Predicted status 5. **The board actually reports 1** — Qualcomm's FmpDxe maps a
security violation onto `ErrorUnsuccessful` rather than the specific auth code,
so status 1 here is not the generic catch-all it looks like. Confirmed against
the serial console; see below.

## 🟠 Bug found in the published capsule: the FAT lies about its size

`mformat` derives the sector count from the image file size in **512-byte**
units and only then applies `-S`, so `-S 5` (4096-byte sectors) on a 4 MB file
produces a BPB describing 8192 × 4096 = **33,554,432 bytes — a 32 MB volume
inside a 4096KB partition**, an 8× overstatement. The shipped capsule reports
`32 759 808 bytes free` where 4,194,304 exist.

It appears to work only because the FIT lands in the first ~800 KB of a
partition that really is there. Anything sizing the volume from the BPB, or
writing into the "free" space, runs off the end of the partition.

Fixed in `6c634f2`: pass `-T` with the real count, and assert afterwards that
the geometry agrees with the image size. Verified — rebuilt `dtb.bin` now
declares 1024 × 4096 = 4,194,304 and reports 3,518,464 free; dropping `-T`
makes the build fail rather than ship.

**The PPA build has this bug.** It is not a reason to avoid testing (the FIT is
still read correctly), but a rebuilt package should be preferred once
convenient.

## On-device procedure

Staging is automatic — the `postinst` copies the capsule to
`/boot/efi/EFI/UpdateCapsule/qcom-dtb-$KVER.cap` and sets the `OsIndications`
capsule-delivery bit (0x4), verifying the write by read-back. `efivar` must be
installed or it logs a warning and does nothing.

```bash
# 0. pre-flight: record what the device reports BEFORE touching anything
cat /sys/firmware/efi/esrt/entries/entry*/fw_class
cat /sys/firmware/efi/esrt/entries/entry*/fw_version         # vs 0.0.2.0 -> status 3 risk
cat /sys/firmware/efi/esrt/entries/entry*/last_attempt_status
#    confirm one fw_class is 0F6D58FC-2258-4D27-9E23-D77219B0897C (hamoa)

# 1. install
sudo add-apt-repository ppa:kuba-t-pawlak/capsule
sudo apt install efivar linux-image-7.0.0-1016-qcom \
                 linux-modules-7.0.0-1016-qcom dtb-capsule-7.0.0-1016-qcom

# 2. confirm staging actually happened
ls -l /boot/efi/EFI/UpdateCapsule/
efivar -p -n 8be4df61-93ca-11d2-aa0d-00e098032b8c-OsIndications   # expect bit 0x4

# 3. reboot; firmware consumes the capsule and clears the directory
sudo reboot

# 4. read the verdict
systemctl status dtb-capsule-verify
journalctl -u dtb-capsule-verify -b
cat /sys/firmware/efi/esrt/entries/entry*/last_attempt_status     # expect 5
```

Step 0 matters: without the "before" reading there is no way to tell a stale
`last_attempt_status` from a fresh one.

## To actually get status 0

One of:

1. **Enrol the test root** on a dev/unlocked board — `Certificates/NewRoot.pub.pem`
   from the signing directory. Only viable where firmware allows re-provisioning.
2. **Sign with the OEM key**, i.e. the real Launchpad signing path this work is
   building towards.
3. **Disable capsule authentication** in firmware, if the board exposes it.

Nothing in the package needs to change for any of these; the signing key is the
only variable.

## On-hardware result

Board: Hamoa IQ-X7181 IoT EVK, `ubuntu@192.168.1.123`, initially running
7.0.0-1013-qcom. Control and flashing procedures are in `ai_effort/qpa`.

### Baseline, before touching anything

```
entry0  fw_class                     0f6d58fc-2258-4d27-9e23-d77219b0897c
        fw_version                   65536       (0x00010000)
        lowest_supported_fw_version  0
        last_attempt_status          0
```

`entry0.fw_class` is **exactly** the `FMP_GUID` in `hamoa/capsule.env`, which
settles that question on hardware rather than by assertion. The capsule
declares `FwVer` 131072 (`0x00020000`) > 65536, and the lowest-supported floor
is 0, so **anti-rollback passes** — status 3 was never a risk.

The other two ESRT entries are `abc50ba3-…` (fw_version 0) and `22c5bc99-…`
(fw_version 2097152, floor 1507328); neither is ours.

### Install and staging

`apt install linux-image-7.0.0-1016-qcom linux-modules-7.0.0-1016-qcom
dtb-capsule-7.0.0-1016-qcom` (plus `efivar`, which is **not** a dependency and
must be present or staging silently does nothing). The postinst did everything
right:

```
dtb-capsule: matched platform 'hamoa' via ESRT FMP_GUID
dtb-capsule: capsule staged at /boot/efi/EFI/UpdateCapsule/qcom-dtb-7.0.0-1016-qcom.cap
dtb-capsule: set OsIndications capsule-delivery bit via efivar (verified via read-back)
```

Platform auto-detection picked **hamoa**, not purwa — the staged file's sha256
matches `hamoa-dtb.cap` exactly. `OsIndications` read back as `04 00 …`.

### What firmware did

Board rebooted in 85 s onto 7.0.0-1016-qcom. The capsule was **consumed**
(directory gone), `OsIndications` **cleared**, `fw_version` **unchanged**, and
`last_attempt_status` went 0 → **1**.

Status 1 alone is ambiguous, so the UEFI log was captured over the serial
console (`/dev/ttyUSB1`, 115200) across a second attempt:

```
Selected FW GUID =: 0F6D58FC-2258-4D27-9E23-D77219B0897C
FmpAuthenticatedHandlerPkcs7: Pkcs7Verify() failed
FmpDxe(Qualcomm System Firmware Update Driver): CheckTheImage() - Authentication Failed Security Violation.
FmpDxe(Qualcomm System Firmware Update Driver): SetTheImage() - Check The Image failed with Security Violation.
Capsule process failed!
```

**This is the ideal negative result.** Firmware found the capsule, parsed it,
matched `UpdateImageTypeId` to the Qualcomm System Firmware Update Driver, and
got all the way to `CheckTheImage()` before refusing. A malformed capsule fails
*earlier*, with a format error. Everything structural — capsule header, FMP
layout, GUID, image index, FV/MSS1 wrapper, FAT, FIT, version — is **accepted
by production firmware**. The only thing wrong is the signing key.

### Three packaging bugs this exposed

Nothing reported any of the above to the user. Each fault hid the next, and
none is visible without hardware:

1. **`dtb-capsule-verify.service` shipped disabled.** It is installed by hand
   in `signed-install`, not via `dh_installsystemd`, so nothing generated the
   snippet `[Install] WantedBy=` relies on. It had never run.
2. **Once enabled, it was skipped** — `ConditionPathExists=/boot/efi/EFI/UpdateCapsule`
   assumed firmware clears the directory's *contents*. Hamoa removes the
   **directory**, so the condition was unsatisfiable in exactly the case the
   service exists for.
3. **It then lied.** The MOTD said *"ESRT confirmed apply"* for a capsule
   firmware had rejected — that string was written unconditionally on the
   no-provenance-node path.

Fixed in `19c2c6c`, each verified on the board: the enable through a real
`dpkg-reconfigure` from clean `deb-systemd-helper` state, the condition removal
across a reboot, and the message against the live rejected capsule. The MOTD
now reads:

```
detail: ESRT did not confirm apply (platform=hamoa status=1 [ErrorUnsuccessful]
        fw_version=65536 vs last_attempt_version=0); also no DTB provenance node ...
```

### Board state afterwards

Healthy, running 7.0.0-1016-qcom from the PPA, `systemctl is-system-running` =
`running`. `dtb_a` was **never written** — the capsule never passed
authentication, so the DTB partitions are untouched and `fw_version` is still
65536. The PPA remains configured and the 1016 kernel installed; 1013 is still
in GRUB.

---

## Part 2 — retest with authentication-disabled firmware

The board was reflashed with a UEFI build that does not validate capsule
signatures (`BOOT.MXF_UEFI.2.5-00690-HAMOA-1`, serial log reports
`FmpDxe(Qualcomm System Firmware Update Driver): Capsule authentication
disabled`). This removed the one blocker from Part 1 and let the capsule run
end to end for the first time.

### False start: the capsule was never staged

The first attempt looked like a *new* failure — `fw_version` unchanged,
`last_attempt_status` still 1, and a serial log saying:

```
GetPartitionsUnderUpdate: CapsulePendingList is empty.
No pending capsules found in EFI\UpdateCapsule folder
```

with no `Pkcs7Verify` or `Security Violation` lines at all. The reboot had
raced the staging step, so the firmware genuinely had nothing to find, and the
`last_attempt_status=1` was **stale** from the Part 1 rejection rather than a
fresh result.

Worth recording as a procedure note: always confirm both
`/boot/efi/EFI/UpdateCapsule/*.cap` and the `OsIndications` read-back *before*
rebooting, and clear `/var/lib/dtb-capsule/last-*` so the previous verdict
cannot be mistaken for the new one.

### The capsule applies

With staging confirmed (`capsule staged at …`, `OsIndications` = `04 00 …`):

```
Loading mass-storage capsule file 'qcom-dtb-7.0.0-1016-qcom.cap'!
FmpDxe(Qualcomm System Firmware Update Driver): Capsule authentication disabled
FmpDxe: CheckTheImage() - No dependency associated
        PartitionName      = dtb_a
        Version            = 0x20000
CapsulePendingList[0]: 2A1A52FC-AA0B-401C-A808-5EA0F91068F8
```

ESRT afterwards:

| field | before | after |
|---|---|---|
| `fw_version` | 65536 | **131072** |
| `last_attempt_version` | 0 | 131072 |
| `last_attempt_status` | 1 | **0** |

`/boot/efi/EFI/UpdateCapsule` emptied by firmware, as in Part 1.

**Taken with Part 1 this closes the question of capsule correctness.**
Production firmware accepted everything up to the signature; permissive
firmware accepted the signature too and wrote `dtb_a` at the expected version.
Nothing about the capsule's structure, payload, targeting or versioning is
wrong — only the key is.

### UEFI really does boot the capsule's FIT

The next boot's serial log shows the firmware parsing a FIT whose contents are
unmistakably ours:

```
ParseFitDt: Configuration From BoardParam
FindConfigToBoot: ... ConfigFdt "qcom,hamoa-evk-el2kvm"
FindConfigToBoot: Invalid Configuration ConfigFdt "qcom,purwa-evk-camx-el2kvm"
FindConfigToBoot: ... "qcom,hamoa-evk-el2kvm-staging"
FindConfigToBoot: i = 0 fdt fdt-hamoa-iot-evk.dtb str len = 21
```

Those compatible strings and that image name match the capsule's FIT exactly
(`conf-7` = `qcom,purwa-evk-camx-el2kvm`, `conf-9` =
`qcom,hamoa-evk-el2kvm-staging`, `fdt-hamoa-iot-evk.dtb` = 21 characters). No
board param matched, so it fell back to config index 0 (`conf-1`,
`fdt-hamoa-iot-evk.dtb` + the imx577 overlay) — which is why the running tree
gains `regulator-cam1`.

Both base DTBs inside the shipped capsule carry the provenance stamp, and it
matches what the package expects:

```
b4.dtb  Purwa IoT EVK  /qcom-dtb-capsule-provenance/dtb-provenance-sha256 = f9397b7a…
b5.dtb  Hamoa IoT EVK  /qcom-dtb-capsule-provenance/dtb-provenance-sha256 = f9397b7a…
/usr/share/dtb-capsule/expected-dtb-sha256                                = f9397b7a…
```

### …but GRUB throws it away

Despite all of that, `/sys/firmware/devicetree/base/qcom-dtb-capsule-provenance`
was **absent** after two clean reboots. The running tree had
`ubuntu,dtb-version = linux-qcom 7.0.0-1016.19` — which the *kernel package*
stamps, not the capsule build — so the kernel was clearly running some other
DTB.

`/boot/grub/grub.cfg` explains it:

```
devicetree	/boot/dtb-7.0.0-1016-qcom
  -> /boot/dtbs/7.0.0-1016-qcom/hamoa-iot-evk-camera-imx577.dtb
     sha256 b18dbcad…, no provenance node
```

flash-kernel installs a DTB into the root filesystem and points GRUB at it.
UEFI loads the capsule's DTB and installs it as the EFI configuration table,
and then **GRUB replaces it** before handing control to the kernel.

Confirmed by removing the directive and rebooting:

```
/sys/firmware/devicetree/base/qcom-dtb-capsule-provenance/dtb-provenance-sha256
  = f9397b7a4aa23d85fb91efa9aad9f7f231d17654963e541745706570f0777aec
```

— an exact match for `expected-dtb-sha256`. Restoring `grub.cfg` restores the
override. **The delivery mechanism works; a second DTB delivery mechanism on
the same system wins.**

This needs a product decision, and it is the most consequential finding of the
whole exercise. As shipped on this image the capsule updates `dtb_a` and the
running kernel ignores it, so the feature is a no-op for Linux. Options:

- stop flash-kernel installing a rootfs DTB on capsule-managed platforms, and
  drop the `devicetree` directive from the GRUB config; or
- keep both and accept the capsule serves only the firmware's own pre-boot DT
  use, not the OS.

### Two further defects found, fixed in `995d23f`

1. **Stale ESRT verdict.** The result cache was keyed on kernel version alone,
   so the `confirmed=0` cached from the Part 1 rejection was replayed over the
   successful apply — the service reported failure for a capsule that had just
   succeeded. Now additionally keyed on a fingerprint of the ESRT entries, so
   a changed firmware outcome is always re-checked while unchanged reboots
   still dedup. Verified by poisoning the cache: the stale verdict is
   discarded and re-evaluated to `confirmed=1`.

2. **Unhelpful diagnosis of the override.** A missing provenance node reported
   only "cannot verify DTB content provenance", which points suspicion at the
   capsule. The script now detects an active `devicetree` directive in
   `grub.cfg` and says the running kernel is not using the capsule's DTB.

### Provenance marker — closed in `4bf3e96`

`/usr/lib/modules/<kver>/dtb-provenance-sha256` was **not shipped by anything**,
so the strongest check the tooling has — does the running DTB belong to the
kernel currently booted — degraded to a warning even on a fully successful
apply, and had never once run. The same path, scanned across every installed
kernel, is also what identifies a rollback target, so that was dead too.

`-generate-` already computes the hash, so it now emits it under that name and
`signed-install` places it in the module directory of the kernel the capsule
was built from. `dtb-capsule-<abi>-<flavour>` is version-locked to one kernel,
so it contributes exactly one marker for its own kernel version.

`linux-modules` is still the better home — the hash is taken over the device
trees *that* package ships, and `build-capsule-payload.sh` already cross-checks
against it there and exits 1 on a mismatch. Until the kernel package records
it, that build-time cross-check stays skipped with a warning, and the line in
`signed-install` must be dropped if linux-modules ever starts shipping the
path, since dpkg will not let both own it.

Verified end to end:

| check | result |
|---|---|
| payload built from the board's own linux-modules DTBs | hash `f9397b7a…0777aec`, **identical to the PPA capsule's** |
| build-time cross-check, agreeing modules root | `provenance … confirmed against linux-modules` |
| build-time cross-check, disagreeing modules root | exits 1 with both values printed |
| `signed-install` output | `SIGNED/dtb-capsule/dtb-provenance-sha256 usr/lib/modules/7.0.0-1016-qcom` |
| `signed-install` with the file absent | `EE: … missing`, exit 1 |
| on the board, with the marker installed | `CONFIRMED: DTB's provenance sha256 matches the linux-modules-7.0.0-1016-qcom package actually installed on this device` |

State file now reads `dtb_pairing_state=apply_confirmed`,
`dtb_kver_content_match=ok`, `summary="OK: capsule applied and verified"`, and
the MOTD is correctly silent.

### `--soc` — decision taken

Left as `--soc hamoa,purwa`. The full platform set is 4 435 968 bytes against a
4096 KB `dtb_a`/`dtb_b` (`partitions.conf:88-89`), so widening it needs a flash
layout change, not a packaging change.

### Board state afterwards

Healthy, 7.0.0-1016-qcom, `dtb_a` now written at version 0x20000,
`grub.cfg` restored to its original form (backup left at
`/boot/grub/grub.cfg.capsule-test-bak`). The fixed `verify-capsule-result.sh`
is installed by hand at `/usr/share/dtb-capsule/`.

---

# Part 3 — 7.0.0-1017.20 PPA test (full ABI-upgrade path)

First test of a complete, freshly published ABI (`1017.20`) including the
`linux-meta` capsule dependency added after Part 2. Tested by upgrading the
Hamoa IQ-X7181 EVK in place from `1016` rather than flashing, so the upgrade
path itself was exercised.

## Result: the capsule chain works end to end

Proved on hardware that the DTB the kernel runs on came from the capsule, and
that it was built from the exact `linux-modules` package installed on the
device:

```
/proc/device-tree/qcom-dtb-capsule-provenance/dtb-provenance-sha256
  = 7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
/usr/share/dtb-capsule/expected-dtb-sha256
  = 7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84

dtb-capsule-verify: CONFIRMED: DTB's provenance sha256 matches the
  linux-modules-7.0.0-1017-qcom package actually installed on this device
```

The same hash is reproducible three independent ways — the marker shipped in
`linux-modules`, the capsule's `expected-dtb-sha256`, and a recomputation
straight from the shipped `.dtb`/`.dtbo` files — so the `LC_ALL=C` pinning and
the linux-modules/dtb-capsule handover both hold in a real build. The
`-signed-` package correctly declined to ship `dtb-provenance-sha256` because
`linux-modules` recorded it.

Firmware log confirms the write:

```
Loading mass-storage capsule file 'qcom-dtb-7.0.0-1017-qcom.cap'!
    PartitionName = dtb_a / BackupType Partition dtb_b
    Update Success
  Phase 4: TrialBoot start.
```

## Defect found and fixed: `Recommends` never installs the capsule

The `linux-meta` change from the previous session used `Recommends`. On target
that silently does nothing:

```
# apt-get install -s linux-image-qcom
Inst linux-modules-7.0.0-1017-qcom ...
Inst linux-image-qcom [7.0.0-1013.16] (7.0.0-1017.20 ...)
   <- no dtb-capsule at all

# apt-get install -s dtb-capsule-7.0.0-1017-qcom
Remv dtb-capsule-7.0.0-1016-qcom [7.0.0-1016.19]
Inst dtb-capsule-7.0.0-1017-qcom ...
```

`dtb-capsule-<abi>-qcom` carries `Conflicts/Provides/Replaces` on the virtual
name `dtb-capsule-qcom` so only one capsule is ever installed, which makes
installing the new one require *removing* the old one. APT will not perform a
removal to satisfy a `Recommends`, so the capsule would have stayed pinned to
whichever ABI it was first installed with while the kernel moved on — exactly
the staleness the version pinning exists to prevent.

Changed to `Depends`. This costs nothing in availability: `dtb-capsule-<abi>-qcom`
and `linux-image-<abi>-qcom` are both binaries of `linux-signed-qcom`, published
in the same event (both `2026-10-06T05:32:39` in this PPA), so the meta already
becomes uninstallable if that upload is missing.

## Defect found and fixed: capsule version was a constant

`build-capsule-payload.sh` hardcoded `fwver=0.0.2.0`, and
`SYSFW_VERSION_program.py` packs `a.b.c.d` as `(c << 16) | d`, ignoring `a`
and `b`. Every capsule ever built therefore declared `FwVersion 0x20000`:

```
NewImage Version                     - 0x20000
Current Version (partition)          - 0x20000
```

This firmware (`BOOT.MXF_UEFI.2.5-00690-HAMOA-1`) applies an equal-version
capsule anyway, which is why the update still landed, but:

  - a firmware enforcing monotonic versions — the whole point of the
    `FwVersion`/`LowestSupportedFwVersion` pair — would reject every update
    after the first;
  - ESRT `fw_version` never moves (still `131072` after this upgrade), so
    neither `fwupd` nor an operator can tell which DTB build is live.

Now derived from the ABI: `7.0.0-1017-qcom` -> `0.0.2.1017` = `0x203f9`,
verified by rebuilding the payload from the PPA's own linux-modules
(`Firmware Version is 0x203f9`) with `expected-dtb-sha256` unchanged.
Strictly greater than the `0x20000` already on existing boards, so the next
capsule is a valid upgrade for them.

## Environment hazard: two ESPs share one label

The EVK has two EFI system partitions, both labelled `system-boot`:

```
/dev/sda1       BLOCK_SIZE=4096  PARTLABEL=efi  LABEL=system-boot
/dev/nvme0n1p1  BLOCK_SIZE=512   PARTLABEL=efi  LABEL=system-boot
/etc/fstab:  LABEL=system-boot  /boot/efi/  vfat  defaults  0 1
```

`/boot/efi` was `sda1` before the reboot and `nvme0n1p1` after, so the mount is
not deterministic. The capsule was staged to, and consumed from, `sda1`; the
stale `1016` capsule left on `nvme0n1p1` then made the verifier warn that the
capsule had not been consumed, and suppressed its result caching.

The update still worked, but the postinst stages to whatever `/boot/efi`
happens to be, so on a machine where firmware reads the *other* ESP the capsule
would never be seen. Probably a flashing artifact of this particular rig rather
than a product condition, but worth confirming before relying on `/boot/efi`.

## Still open

  - **GRUB discards the capsule's DTB.** Unchanged from Part 2 and confirmed
    again here: the verifier's diagnosis fires correctly, and commenting out
    the seven `devicetree` directives in `grub.cfg` is what made the provenance
    node appear. As shipped, the capsule updates `dtb_a` and Linux ignores it.
    `grub.cfg` was restored afterwards.
  - Capsules still signed with the interim test keys.
  - `--soc` stays `hamoa,purwa`; widening needs a `dtb_a` layout change.

# Part 4 — capsule2 PPA (`~kuba-t-pawlak/capsule2`), meta `Depends` rebuild

Published 2026-10-06. All five sources (`kgsl`, `linux-qcom`, `linux-generate-qcom`,
`linux-signed-qcom`, `linux-meta-qcom`) at `7.0.0-1017.20`; the meta binaries were
republished at 11:10 UTC, after the rest at 08:42.

## Scope of change — confirmed

Diffing the two PPAs' `Packages` indices by SHA256:

- 16 binaries differ, **all of them from `linux-meta-qcom`**.
- 17 binaries are byte-identical, including the kernel, `linux-signed-qcom`'s
  `dtb-capsule-7.0.0-1017-qcom`, and `kgsl`.
- `capsule` additionally still carries the 1016 set, which `capsule2` does not.

So "only meta is different" holds exactly.

## The `Depends` fix is present

```
Package: linux-image-qcom
Version: 7.0.0-1017.20
Depends: linux-image-7.0.0-1017-qcom, linux-firmware, dtb-capsule-7.0.0-1017-qcom
```

No `Recommends` field at all. Verified three ways.

**Sandbox, fresh install, `--no-install-recommends`** (only a hard dependency can
pull the capsule):

```
Inst dtb-capsule-7.0.0-1017-qcom (7.0.0-1017.20 capsule2:26.04/resolute [arm64])
```

**Sandbox, the original defect** — 1016 capsule installed, install the 1017 meta.
This is the case that previously failed, because APT will not perform a *removal*
to satisfy a `Recommends`, and the two capsules conflict through the virtual
`dtb-capsule-qcom`:

```
Remv dtb-capsule-7.0.0-1016-qcom [7.0.0-1016.19]
Inst dtb-capsule-7.0.0-1017-qcom (7.0.0-1017.20 capsule2:...)
```

APT now does the removal. Defect closed.

**On the Hamoa board.** Removing only the capsule correctly drags the meta out,
which is the signature of a hard dependency:

```
Remv linux-image-qcom [7.0.0-1017.20]
Remv dtb-capsule-7.0.0-1017-qcom [7.0.0-1017.20]
```

After purging both and reinstalling with `--no-install-recommends`:

```
Unpacking dtb-capsule-7.0.0-1017-qcom (7.0.0-1017.20) ...
Unpacking linux-image-qcom (7.0.0-1017.20) ...
/usr/share/dtb-capsule/hamoa/hamoa-dtb.cap   4198306
/usr/share/dtb-capsule/purwa/purwa-dtb.cap   4198306
```

Provenance chain still agrees end to end:

```
linux-modules dtb-provenance-sha256 : 7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
capsule expected-dtb-sha256         : 7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
```

## Two problems found

### 1. The meta version went backwards, and a published version was reused

`capsule` ships the meta as `7.0.0-1017.20+1`; `capsule2` ships it as
`7.0.0-1017.20`. The `+1` upload in `capsule` **already contained the `Depends`
fix** — identical `Depends` line. So `capsule2` is not introducing the fix, it is
re-releasing it under a *lower* version.

Two consequences:

- With both PPAs enabled, `capsule` wins. Confirmed:

  ```
  linux-image-qcom:
    Candidate: 7.0.0-1017.20+1
       7.0.0-1017.20+1 500 .../capsule/ubuntu
       7.0.0-1017.20   500 .../capsule2/ubuntu
  ```

- More importantly, `7.0.0-1017.20` was **already published with different
  content** — the original `Recommends` meta, which is what the board had
  installed:

  ```
  Version: 7.0.0-1017.20
  Depends: linux-image-7.0.0-1017-qcom, linux-firmware
  Recommends: dtb-capsule-7.0.0-1017-qcom
  ```

  The upgrade happened to work here only because APT keeps the archive copy and
  the `/var/lib/dpkg/status` copy as distinct entries when their hashes differ,
  and prefers the archive at pin 500 over status at 100. That is an implementation
  detail, not a guarantee: anyone who already has `7.0.0-1017.20` and does not
  re-run an upgrade against this archive keeps the broken meta forever, and any
  tooling that compares version strings alone will see nothing to do.

  **Reusing a published version with different contents should be avoided.** The
  `+1` in `capsule` was the correct instinct; `capsule2` should carry `+2` (or a
  higher `.21`) rather than reverting to `.20`.

### 2. The `fwver` fix is still not built

`linux-signed-qcom`'s binaries are byte-identical between the two PPAs, so commit
`8cae83b` (derive the capsule version from the kernel ABI) is **not** in this
build. Both shipped capsules still carry the hardcoded value:

```
hamoa-dtb.cap   FwVersion=0x20000 (131072)  LSV=0x0
purwa-dtb.cap   FwVersion=0x20000 (131072)  LSV=0x0
```

The ESRT `fw_version` will therefore still read `131072` after a successful
update, exactly as in Part 3, and anti-rollback remains inert. This needs a
`linux-signed` version bump and a rebuild — it cannot go out as `7.0.0-1017.20`
since that is published.

## Verdict

The thing `capsule2` set out to change is correct and verified on hardware. It is
not yet a shippable build: the meta needs a version above `7.0.0-1017.20+1`, and
`linux-signed` needs the `fwver` fix rebuilt under a new version.

# Part 5 — capsule3 PPA on a board carrying our root certificate

Published 2026-10-06 15:35, all five sources rebuilt together, still at
`7.0.0-1017.20`. The board had been reflashed to an older image (kernel
`7.0.0-1013-qcom`, no capsule installed) and had a `uefi_dtbs` carrying **our**
`QcCapsuleRootCert` written to both `uefi_dtb_a` and `uefi_dtb_b`.

## Headline: the embedded root certificate works, and authentication is real

This is the first run where the capsule was actually *authenticated* rather
than waved through.

The genuine capsule applied:

```
Loading mass-storage capsule file 'hamoa-dtb.cap'!
FmpDxe(...): CheckTheImage() - No dependency associated in image.
    PartitionName      = dtb_a
      Update Success
  Phase 4: TrialBoot start. Time (ms): 30767
```

On its own that proves little — the previous UEFI build announced `Capsule
authentication disabled` and accepted anything. So a **negative control** was
run: the same capsule with a single bit flipped inside the PKCS#7 `CertData`
(offset 1353 of the 2450-byte signature, `0x98` → `0x99`), confirmed to fail
`openssl smime -verify` first. Staged and booted:

```
FmpAuthenticatedHandlerPkcs7: Pkcs7Verify() failed
FmpDxe(...): CheckTheImage() - Authentication Failed Security Violation.
FmpDxe(...): SetTheImage() - Check The Image failed with Security Violation.
Failed to set the firmware payload 0. Status = Security Violation
Capsule process failed!
Deleting mass-storage capsule file 'hamoa-dtb.cap'!
```

One flipped bit is the difference between `Update Success` and `Security
Violation`, on the same firmware, in the same session. Authentication is
enforced, and the genuine capsule passes it — which only happens because
`patch-capsule-cert` put our root into `uefi_dtbs`. The chain resolves as
intended: the capsule carries Sub CA + Signer, the device supplies the anchor.

Note the UEFI build string is unchanged, `BOOT.MXF_UEFI.2.5-00690-HAMOA-1`, and
`Secure Boot: Off` throughout — capsule authentication is independent of UEFI
Secure Boot here.

## The `Depends` fix, re-confirmed from a clean 1013 system

```
Unpacking linux-modules-7.0.0-1017-qcom (7.0.0-1017.20) ...
Unpacking dtb-capsule-7.0.0-1017-qcom (7.0.0-1017.20) ...
Unpacking linux-image-7.0.0-1017-qcom (7.0.0-1017.20) ...
Unpacking linux-image-qcom (7.0.0-1017.20) over (7.0.0-1013.16) ...
```

with `--no-install-recommends`, so only a hard dependency could have pulled the
capsule in. This is a stronger case than Part 4's, since the starting point was
a freshly flashed machine that had never seen the capsule package.

## Still outstanding

### The `fwver` fix is *still* not built

`8cae83b` is committed locally on top of `f051d4f` (`Ubuntu-qcom-7.0.0-1017.20`)
but capsule3 was built from the tag, so the capsules still carry:

```
hamoa-dtb.cap    FwVersion=0x20000 (131072)  LSV=0x0
purwa-dtb.cap    FwVersion=0x20000 (131072)  LSV=0x0
```

and the firmware duly logged `NewImage Version - 0x20000` against `Current
Version (partition) - 0x20000`. ESRT `fw_version` stayed `131072`. Three PPAs in
a row have now shipped without this.

### GRUB still discards the capsule DTB

No `qcom-dtb-capsule-provenance` node in the running device tree after a
successful apply, so the capsule remains a no-op for Linux until the `devicetree`
directives are dropped. Unchanged product decision.

## Two traps worth recording

### ESRT is not a reliable way to detect a rejected capsule

The first tampered run reported `last_attempt_status=0` with the ESP empty,
which reads exactly like success. It was not: the capsule is rejected *before*
an update attempt is recorded, so ESRT simply retained the values from the
previous genuine apply, and the firmware deletes the `.cap` whether it passes or
fails. Only the run whose firmware phase was captured on serial showed the
truth, after which ESRT did settle to `last_attempt_status=1`,
`last_attempt_version=0`.

**A capsule result must be read from the serial log, not from ESRT alone.**

### The dual-ESP label collision bit again

`/dev/sda1` and `/dev/nvme0n1p1` are both labelled `system-boot`, and `/boot/efi`
flipped from `sda1` to `nvme0n1p1` across a reboot mid-test, leaving a staged
capsule on the ESP the firmware was not reading. Cleanup had to be done on both
devices explicitly. Any staging step on this board should resolve the ESP by
`PARTUUID` against `efibootmgr`'s `BootCurrent` entry
(`6ee0d619-e579-4707-bc23-63972c9333b7` = `sda1`) rather than trusting
`/boot/efi`.

Also: `systemctl reboot` stalled for ~8 minutes on `wireplumber`, long enough to
miss the firmware phase on serial. `echo b > /proc/sysrq-trigger` after a `sync`
is the reliable way to catch it.

## Verdict

The certificate work is done and proven: Hamoa now validates our capsules
against our own root, and rejects anything else. The packaging fix is confirmed
from a clean machine. The build still cannot ship — it carries neither the
`fwver` fix nor a version above the already-published `7.0.0-1017.20`.

## Part 5b — closing the loop: GRUB override removed

The reflash restored a stock `grub.cfg`, so Part 5 above was run with the
`devicetree` directives back in place — which is why no provenance node appeared
in the running device tree. Five directives were active:

```
159:	devicetree	/boot/dtb-7.0.0-1017-qcom
181:		devicetree	/boot/dtb-7.0.0-1017-qcom
201:		devicetree	/boot/dtb-7.0.0-1017-qcom
222:		devicetree	/boot/dtb-7.0.0-1013-qcom
242:		devicetree	/boot/dtb-7.0.0-1013-qcom
```

All five were commented out (backup at `/boot/grub/grub.cfg.capsule-test-bak`)
and the board rebooted. The capsule-delivered DTB then reached Linux:

```
/sys/firmware/devicetree/base/qcom-dtb-capsule-provenance/dtb-provenance-sha256
                                    7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
/usr/share/dtb-capsule/expected-dtb-sha256
                                    7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
/usr/lib/modules/7.0.0-1017-qcom/dtb-provenance-sha256
                                    7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
```

Three independent sources, one hash. The shipped verifier agrees:

```
kver_match_state=ok
dtb_pairing_state=apply_confirmed
dtb_kver_content_match=ok
detail="provenance sha256 match"
summary="OK: capsule applied and verified"
```

and the motd hook stays silent, which is its healthy state.

### What this establishes

For the first time the entire path is proven end to end **with authentication
enforced at every step that has one**:

1. Our root certificate, written into `uefi_dtbs` by `patch-capsule-cert`, is
   the trust anchor the firmware actually uses.
2. A capsule signed by our keys authenticates and applies; one with a single
   flipped signature bit is rejected with `Security Violation`.
3. The firmware writes the payload to `dtb_a` in SPI NOR.
4. With the GRUB override out of the way, Linux boots that DTB.
5. Its provenance hash matches the kernel package it was built from, exactly.

The GRUB `devicetree` directive is therefore the single remaining thing that
makes capsule delivery inert, and it is purely a packaging/product decision — the
mechanism underneath it is working.

Two caveats on the change itself: `grub.cfg` is regenerated by
`update-grub`, so the directives will come back on the next kernel package
operation; and with them gone there is no GRUB-side fallback DTB, which is what
`dtb-capsule-recovery` exists to compensate for. The board has been left with
the override disabled so capsule behaviour remains observable.

---

## Part 6 — a capsule-delivered DTB survives an OS reflash

The board was reflashed back to the stock x05 image (kernel `7.0.0-1013`), yet
it still reports a provenance node that was only introduced in 1016:

```
$ cat /sys/firmware/devicetree/base/qcom-dtb-capsule-provenance/dtb-provenance-sha256
7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
```

That is not a flashing mistake. It is the DTB delivered by the 1017 capsule in
Part 5, still resident in the firmware's DTB slot.

### Ruling out every OS-side source

| Candidate source | Result |
|---|---|
| `dtb-capsule` package installed | **No** — only `linux-image/modules-7.0.0-1013-qcom` |
| `/usr/share/dtb-capsule`, `/var/lib/dtb-capsule` | **Absent** |
| A rootfs DTB carrying the node | **None** — scanned every `*.dtb` under `/usr/lib/firmware`, `/lib/firmware`, `/boot` |
| GRUB `devicetree` directive | **None generated** — the 1013 kernel package ships no DTB for one to point at (see Part 6b) |
| A staged `.cap` on either ESP | **None** |
| A DTB partition visible to Linux | **None** — `lsblk` shows only `efi`/`writable`/`persist` |

So nothing in the freshly written filesystem can account for it.

### Confirming it is the 1017 capsule payload

The provenance value is not the sha256 of a single DTB — `build-capsule-payload.sh`
computes it as a hash *of a manifest*:

```sh
( cd "$work/dtb" && LC_ALL=C sha256sum -- *.dtb *.dtbo | LC_ALL=C sort -k2,2 ) \
        > dtb-provenance-content-sha256sums.txt
provenance=$(sha256sum dtb-provenance-content-sha256sums.txt | cut -d' ' -f1)
```

which is why hashing `hamoa-iot-evk.dtb` on its own gives an unrelated
`e2b2ca75…`. The authoritative recorded value lives in the modules package.
Pulled fresh from the PPA, independently of any earlier note:

```
$ dpkg-deb --fsys-tarfile linux-modules-7.0.0-1017-qcom_7.0.0-1017.20_arm64.deb \
    | tar -xf - --wildcards './usr/lib/modules/*/dtb-provenance-sha256'
$ cat usr/lib/modules/7.0.0-1017-qcom/dtb-provenance-sha256
7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84
```

Exact match with what the 1013 system reports.

Corroborating, the live DTB is demonstrably not the one shipped with 1013:

```
481b02505db0d9e41161eaee6e887311bcbdd03230ee004dafada6c98bb9ad09  /sys/firmware/fdt            (258424 bytes)
6207bd0147f36c782fd70ed0acf7841f2a5d826bfb9d6368cf024070edd81182  …/7.0.0-1013-qcom/…/hamoa-iot-evk.dtb  (260567 bytes)
```

And ESRT still carries the previous run's state, since it too is firmware-resident:

```
entry0  fw_class 0f6d58fc-2258-4d27-9e23-d77219b0897c   <- our DTB capsule FMP GUID
        fw_version 131072   last_attempt_version 131072   last_attempt_status 1
```

### Why this happens

A capsule update writes to storage owned by platform firmware — the `dtb_a` /
`dtb_b` slots in SPI NOR. Flashing an OS image rewrites `sda`/`nvme0n1`
(`efi` + `writable`) and does not touch SPI NOR. The two are independent, so a
"clean image" is clean only in the filesystem sense. The platform keeps serving
the last successfully applied DTB.

This is correct behaviour and in fact the point of the feature: the DTB is
firmware state, not OS state.

### Consequences for testing

1. **Reflashing does not reset capsule state.** The board is currently running a
   1013 kernel against a 1017 DTB. Any test that assumes a reflash gives a
   pristine baseline is starting from an already-updated platform.
2. **ESRT starts non-zero.** `fw_version` is already `131072`, and
   `last_attempt_status 1` is residue from the Part 5 tamper test. Once the
   `fwver` fix lands and anti-rollback becomes meaningful, a capsule whose
   version does not exceed the stored one may legitimately be refused — on what
   looks like a fresh board.
3. **A true baseline needs the DTB slots restored**, via EDL/fastboot, the same
   way `uefi_dtbs` is handled in Phase 0 of the runbook. Note this is *separate*
   from the `uefi_dtbs` patching: that carries the root certificate, whereas
   `dtb_a`/`dtb_b` carry the payload.
4. **Kernel/DTB version skew is now possible in the field.** A user who applies
   a capsule and later reinstalls an older OS image keeps the newer DTB. Worth
   deciding whether that is acceptable or whether the kernel should check the
   provenance marker against its own expectation and warn.

---

## Part 6b — why GRUB did not override the DTB on the 1013 system

Part 6 raises an obvious objection: if GRUB was never modified after the
reflash, it should have served a DTB from disk and the firmware's DTB would
never have been seen. The resolution is that **the 1013 kernel package ships no
DTB at all**:

```
$ dpkg -L linux-image-7.0.0-1013-qcom | grep ^/boot
/boot
/boot/vmlinuz-7.0.0-1013-qcom
```

No `/boot/dtb-7.0.0-1013-qcom`, no `/boot/dtbs/7.0.0-1013-qcom/`.
`/etc/grub.d/10_linux` only emits

```sh
devicetree	${rel_dirname}/${dtb}
```

when such a file exists, so on a pure 1013 system `update-grub` produces **no
`devicetree` directive at all**. GRUB hands the kernel nothing, and the kernel
uses the DTB UEFI passed it — the capsule-written one in `dtb_a`.

The on-disk DTB machinery (`/boot/dtbs/<kver>/`, the `/boot/dtb` symlink, and
hence the GRUB directives) arrives with 1016+, alongside the capsule work. So
the five active `devicetree` lines seen in Part 5b were not stock: they were
created by `update-grub` when the 1017 packages were installed. There is no
contradiction between the two observations — they are simply different package
sets.

### Confirmed by the 1017 install that happened mid-investigation

`apt-get install -y linux-image-qcom` was run on the board at 11:05:20 UTC,
which installed 1017 plus `dtb-capsule-7.0.0-1017-qcom` and rebooted. After
that boot:

- `/boot/dtbs/7.0.0-1017-qcom/` exists and `/boot/dtb` points at
  `hamoa-iot-evk-camera-imx577.dtb`
- `grub-mkconfig` emits **5 active** `devicetree` lines and 0 commented ones
- the provenance node is **gone**
- the running DT contains `soc@0/cci@ac15000/i2c-bus@1/camera@1a` with an
  `imx577` compatible

That camera node exists only in `hamoa-iot-evk-camera-imx577.dtb` and not in
`hamoa-iot-evk.dtb`, so the kernel is demonstrably running the **on-disk** DTB,
not a firmware one. The capsule payload does contain provenance markers and
imx577 nodes, but a capsule-applied DTB would carry the provenance node — it
does not. GRUB overrode the firmware DTB, exactly as in Part 5.

(The `#devicetree` comments currently visible in `grub.cfg` were applied by hand
*after* that boot; `update-grub` generates them active.)

### Incidental but important: the capsule failed on this board

The same investigation surfaced that the 1017 capsule was staged, consumed and
**rejected**:

```
/var/lib/dtb-capsule/last-verify-state
detail="ESRT did not confirm apply (platform=hamoa status=1 [ErrorUnsuccessful]
         fw_version=131072 vs last_attempt_version=131072); also no DTB
         provenance node"
summary="UNKNOWN: cannot confirm capsule result"
```

This is expected: the reflash restored the stock `uefi_dtbs`, which carries
Qualcomm's `CN = rootuser` rather than our root. Our test-key-signed capsule
therefore no longer authenticates. Phase 0 of the runbook has to be redone
after every reflash — and note the asymmetry that makes this easy to miss:

- the **certificate** in `uefi_dtbs` **is** reset by a reflash → capsules stop verifying
- the **payload** in `dtb_a`/`dtb_b` is **not** reset → a stale DTB keeps being served

So a reflashed board both loses the ability to apply new capsules and retains
the result of the last one.

### Summary of DTB precedence observed

| Installed kernel | `/boot/dtb` present | GRUB directive | DTB the kernel uses |
|---|---|---|---|
| 1013 (stock x05) | no | none generated | firmware `dtb_a` — capsule DTB, provenance visible |
| 1016+ | yes | generated active | on-disk DTB, firmware DTB ignored |
| 1016+ with directives commented | yes | none active | firmware `dtb_a` — capsule DTB (Part 5b) |

---

## Part 7 — clean-room end-to-end pass on a freshly flashed board

The first run of the complete procedure from a blank board with no manual
repair at any step. Performed by the engineer; verified here from the device
afterwards, in the state the test left it.

Sequence run: reflash → patch the root certificate into `uefi_dtbs` → install
1017 from the PPA → comment the GRUB `devicetree` directives → reboot.

### Evidence collected from the board

Current boot started 11:51:39 UTC and has not been restarted since
(`journalctl --list-boots` boot 0, `uptime -s`), so everything below is from
the one boot that consumed the capsule.

| Check | Value | Meaning |
|---|---|---|
| ESRT entry0 `fw_class` | `0f6d58fc-2258-4d27-9e23-d77219b0897c` | our DTB FMP driver |
| ESRT `last_attempt_status` | **0** | success — contrast Part 5's `1 [ErrorUnsuccessful]` |
| ESRT `fw_version` / `last_attempt_version` | 131072 / 131072 | agree |
| `/boot/efi/EFI/UpdateCapsule/` | exists, **empty** | firmware consumed the staged capsule |
| `OsIndications` | `07000000 0000000000000000` | attrs 0x7, value **0** — firmware cleared the request bit |
| GRUB `devicetree` lines | 5, all commented | firmware DTB is what the kernel gets |
| running DT provenance node | `70208457…2dd6ab84` | capsule's DTB is live |
| `linux-modules-7.0.0-1017-qcom` marker | `70208457…2dd6ab84` | **exact match** |
| `dtb-capsule-verify.service` | ran 11:52:18, `CONFIRMED` | self-report agrees with manual check |

Verify-service cache (`/var/lib/dtb-capsule/last-verify-state`):

```
kver_match_state=ok
dtb_pairing_state=apply_confirmed
guid_conflict=false
dtb_kver_content_match=ok
detail="provenance sha256 match"
summary="OK: capsule applied and verified"
```

### Why this one counts

Every earlier run had at least one link proven only in isolation. This run
closes all of them at once, and each is independently checked rather than
inferred from the one next to it:

- firmware **authenticated** the capsule against our embedded root
  certificate — `last_attempt_status 0`, where the same capsule on a stock
  `uefi_dtbs` gave `1` (Part 5)
- firmware **consumed** it — staging directory emptied, `OsIndications`
  cleared back to 0 by firmware rather than by us
- the capsule's DTB is the one **actually in use** — provenance node present,
  which can only come from the capsule payload, since the `fdtput` stamp is
  applied during the capsule build and the on-disk DTB in `linux-modules`
  does not carry it
- the DTB is the **right** one — provenance matches the installed
  `linux-modules` byte-for-byte, so it is not a leftover from an earlier apply
- the tooling's **own verdict agrees** with the independent manual check,
  which is what Part 5 could not demonstrate because the service was reporting
  from a stale cache

### The ESP hazard did not bite — but only by luck

`/boot/efi` is `/dev/sda1`, and `BootCurrent: 0000` resolves to
`HD(1,GPT,6ee0d619-…)` on the same disk, so the postinst's hardcoded
`ESP_MOUNT=/boot/efi` happened to be the ESP firmware boots from. That was
confirmed, not assumed. The defect noted in Part 3 is untouched: nothing in
the postinst checks this, and on a board where the NVMe ESP wins the postinst
would report success while writing somewhere firmware never reads.

### Caveat on `fw_version`

`fw_version` was already 131072 before this test — it is firmware-resident and
survives a reflash (Part 6). It therefore proves nothing on its own here, and
is listed above only for completeness. The decisive evidence is
`last_attempt_status` flipping 1 → 0 and the provenance match. This is also a
reminder that the capsule version is still not derived from the kernel ABI on
any published PPA: the fix exists in git but has not been built.

### A side effect worth noting: firmware is not version-gating

Because `fw_version` was **already** 131072 and a capsule declaring that same
version was accepted and applied, this firmware does not require the incoming
version to exceed the installed one. Two consequences:

- **Good for now.** The hardcoded constant version has not blocked repeated
  testing, and will not block the next capsule either. Had firmware enforced
  strict monotonicity, every capsule after the first would have been refused
  until the ABI-derived version fix shipped.
- **Bad as a property.** There is no rollback protection from version gating:
  an older DTB capsule, correctly signed, will be accepted over a newer one.
  Downgrade resistance currently rests entirely on controlling who holds the
  signing key.

This is an observation about the firmware's behaviour, not a defect in our
packaging, but it should be stated explicitly rather than discovered later —
and it means the ABI-derived version is a correctness and diagnostics
improvement, not the security control it might look like.

### One measurement trap, recorded because it cost time

The provenance node is a **top-level** DT node,
`/sys/firmware/devicetree/base/qcom-dtb-capsule-provenance/dtb-provenance-sha256`,
not something under `/chosen`. Two ways to miss it:

- looking in `/proc/device-tree/chosen/` — wrong node, reads as absent
- `find /proc/device-tree -name '*provenance*'` — returns nothing even when the
  node exists, because `/proc/device-tree` is a **symlink** to
  `/sys/firmware/devicetree/base` and `find` does not follow symlinks without
  `-L`

Both produce a confident false negative on a board where the capsule worked
perfectly. Use the `/sys/firmware/devicetree/base/...` path directly, which is
what `verify-capsule-result.sh` does (`DT_PROVENANCE_DIR`, line 104).

### Verdict

The capsule mechanism is proven end to end on real hardware: signed by us,
authenticated by firmware against our embedded root certificate, applied,
and confirmed in the running kernel's device tree by both an independent
check and the package's own verifier.

What remains before this is shippable is unchanged and none of it is about
whether the mechanism works:

1. **Launchpad-side signing** — the local test key must be removed
2. **Production signing key** and its embedding into production `uefi_dtbs`,
   including the MBN re-signature
3. **The GRUB `devicetree` override** — still a product decision. The whole
   chain is defeated by an uncommented directive, and commenting it by hand is
   not a shipping answer
4. **The dual-ESP hazard** in the postinst
5. **A rebuild** carrying the ABI-derived capsule version

---

## Part 8 — independent reproduction from a pristine 1013 board

Part 7 was verified after the fact, in the state the engineer's run left behind.
This part is the same procedure driven end to end here, from a board flashed
back to 1013 with a patched `uefi_dtbs` and nothing else prepared, using
`ppa:kuba-t-pawlak/capsule3`.

It reproduces Part 7's result **and** catches a hazard firing that previous
parts had only described in theory.

### Why this baseline is stronger than Part 7's

| Baseline check | Value | Why it matters |
|---|---|---|
| Kernel | `7.0.0-1013-qcom` | pristine, pre-capsule |
| Packages | no `dtb-capsule`, no `efivar` | nothing staged, nothing armed |
| GRUB `devicetree` | **none generated** | 1013 ships no `/boot/dtb` (Part 6b) |
| Provenance node | **absent** | this flash *did* clear `dtb_a` |
| ESRT `fw_version` / `last_attempt_status` | 131072 / 0 | firmware-resident leftovers |

The absent provenance node is the important one. In Parts 5–7 a stale
capsule DTB was already in SPI NOR, so a present node was only weak evidence.
Here the node starts **absent**, so if it appears it can only have come from
the capsule applied during *this* run. That removes the main ambiguity.

Conversely `last_attempt_status` was **already 0**, so the 1 → 0 flip that
carried Part 7 is unavailable here. The two parts therefore have complementary
blind spots and together leave none.

### Steps, and what each proved

1. **`add-apt-repository ppa:kuba-t-pawlak/capsule3`**, `apt-get update`.
2. **`apt-get install -s linux-image-qcom`** — dry run. Pulled in
   `dtb-capsule-7.0.0-1017-qcom` *and* `efivar` automatically, re-confirming
   the `Depends` fix from Part 4 on a clean system. `linux-modules` is
   configured before `dtb-capsule`, so the provenance marker is in place when
   the postinst runs.
3. **Real install.** No manual staging and no manual arming:
   - `/boot/efi/EFI/UpdateCapsule/qcom-dtb-7.0.0-1017-qcom.cap`, 4198306 bytes
   - `OsIndications` = `07000000 0400000000000000` — attrs 0x7, value 0x4
   - `OsIndicationsSupported` = `0x4`, so firmware does advertise capsule-on-disk
4. **GRUB.** `update-grub` ran in the kernel postinst and generated **5 active
   `devicetree` directives**, exactly as expected on 1017. Commented out by
   hand — still the one unavoidable manual step, and still not a shipping
   answer.
5. **Reboot.**

### Result

| Check | Before | After |
|---|---|---|
| Provenance node | **absent** | **`70208457…2dd6ab84`** |
| `linux-modules` marker | `70208457…2dd6ab84` | — (match) |
| `expected-dtb-sha256` | `70208457…2dd6ab84` | — (match) |
| `OsIndications` value | `0x4` (armed) | **`0x0`** (firmware cleared it) |
| Capsule on firmware's ESP | staged | **consumed** |
| ESRT `last_attempt_status` | 0 | 0 |

`dtb-capsule-verify.service` ran unprompted at 13:24:38 and agreed:

```
dtb_pairing_state=apply_confirmed
dtb_kver_content_match=ok
summary="OK: capsule applied and verified"
```

**The capsule mechanism reproduces cleanly from a pristine board.**

---

## The dual-ESP hazard is no longer theoretical — it fired during this run

Flagged in Part 3 as a latent risk. It actually happened here, and the test
passed anyway **only by luck**.

`/etc/fstab` mounts the ESP by label:

```
LABEL=system-boot	/boot/efi/	vfat	defaults	0	1
```

and two partitions on two different disks carry that label:

| Device | PARTUUID | FS UUID |
|---|---|---|
| `/dev/sda1` | `6ee0d619-e579-4707-bc23-63972c9333b7` | `233B-EEA6` |
| `/dev/nvme0n1p1` | `9e0cd0e8-b4ad-472f-9ccc-e300de1aaad3` | `F223-A1CB` |

**The label resolved to a different disk across the reboot:**

- *before* reboot `/boot/efi` was `/dev/sda1` — so the postinst staged there
- *after* reboot `/boot/efi` was `/dev/nvme0n1p1`

Which one does firmware use? `BootCurrent: 0000` →
`HD(1,GPT,6ee0d619-e579-4707-bc23-63972c9333b7,…)`, which is **`sda1`**.
Confirmed by mounting it directly: its `EFI/UpdateCapsule/` is now **empty**,
i.e. firmware consumed what we staged. Meanwhile `nvme0n1p1` still carries an
**unconsumed** `.cap` dated `Oct 7 11:05` from an earlier run — written to a
partition firmware never reads, and sitting there indefinitely.

### Why this is serious

The run succeeded purely because `sda1` happened to win the label race at
install time. Had the ordering been reversed — as it demonstrably *was* one
boot later — the postinst would have:

- written the capsule to `nvme0n1p1`
- set `OsIndications` successfully
- verified its own write by reading it back successfully
- **exited 0 and reported success**

…and the capsule would simply never have been applied, with no error anywhere.
The next boot's verifier would report "no provenance node", which operators are
told to read as *GRUB is still overriding* (runbook Phase 8) — pointing the
investigation in precisely the wrong direction.

This is non-deterministic, so it will present as a flaky capsule update.

### The fix

`ESP_MOUNT="${ESP_MOUNT:-/boot/efi}"` in `dtb-capsule.postinst.in` line 17 must
stop being an assumption. The ESP firmware boots from is knowable exactly:

1. read `BootCurrent`, then the matching `Boot####` variable
2. extract the `HD(…,GPT,<PARTUUID>,…)` partition GUID from its device path
3. compare against the PARTUUID backing `$ESP_MOUNT`
   (`lsblk -no PARTUUID "$(findmnt -no SOURCE "$ESP_MOUNT")"`)
4. on mismatch, **fail loudly** rather than staging into a void

`efibootmgr` is already available at that point, and all of this is a few lines
of shell. Even a warning would be a large improvement on silence; refusing to
stage is better, because a capsule that silently does nothing is worse than an
install that stops and says why.

Separately, the duplicate `system-boot` label is itself worth fixing on these
boards — mounting the ESP by UUID or PARTUUID in `fstab` would remove the
non-determinism at the source, independently of any packaging change.

### It is worse than a misplaced file: EFI variables live on the ESP too

Found while clearing `OsIndications` during cleanup, with the *wrong* ESP
mounted:

```
Error: 'VolatileVars.bin' file not found in ESP partition.
       EFI variable changes won't persist reboots
```

Checking both partitions:

| ESP | `VolatileVars.bin` | `Persisted_Capsules.bin` |
|---|---|---|
| `sda1` (firmware boots this) | **present**, 160 bytes, mtime `13:21` | 70 MB |
| `nvme0n1p1` | **absent** | 70 MB |

That mtime is exactly when the postinst set `OsIndications` during this run.
So on this platform EFI variable writes are **backed by a file on the ESP**,
and both halves of arming a capsule depend on the right ESP being mounted:

1. the capsule is staged where firmware never reads, **and**
2. `OsIndications` is never persisted across the reboot

Critically, **neither existing safeguard can see this**. `efivar -w` returns
success, and the postinst's read-back verification passes, because both
operate on the kernel's efivarfs view rather than on what will survive a power
cycle. A check that reads back what it just wrote cannot detect a backing
store that was never updated.

This removes any argument for treating the ESP mismatch as a warning-only
condition. It is the one failure mode where everything the package can
currently observe reports success and nothing happens.

### Fix implemented

`linux-signed` commit *"dtb-capsule: stage to the ESP firmware actually boots
from"* resolves the ESP instead of assuming it — `BootCurrent` → `Boot####` →
the `HD(n,GPT,<partuuid>,…)` in its device path, compared against the PARTUUID
backing `ESP_MOUNT` — then:

- **match** → proceed silently, as before
- **mismatch, boot ESP mounted elsewhere** → stage there, with a warning
  naming both PARTUUIDs
- **mismatch, boot ESP not mounted** → refuse to stage and explain why
- **detection impossible** (no `efibootmgr`/`findmnt`/`lsblk`, no
  `BootCurrent`, non-GPT boot device) → proceed exactly as before

`efibootmgr` added to `Depends` (92 KB; the package is EFI-specific anyway).
`findmnt`/`lsblk` need no declaration — `util-linux` is `Essential: yes`.

All four branches were exercised on the board in the state that exposed the
bug, including running the real modified postinst: with the boot ESP unmounted
it refused and left the wrong ESP byte-identical; with it mounted it retargeted
and staged correctly; a correctly mounted ESP produced no warning; and with
`efibootmgr` hidden it degraded to the old behaviour.

---

## Part 9 — capsule4: the ABI-derived version lands, and firmware confirms the apply

Same procedure as Part 8, on a board reflashed again with a patched
`uefi_dtbs`, using `ppa:kuba-t-pawlak/capsule4` — the first PPA built from a
`linux-signed` carrying both the ABI-derived capsule version and the ESP
resolution fix. Package versions are still `7.0.0-1017.20` for compatibility
despite the changelog skipping versions; that only matters when comparing
builds by version string, which nothing here does.

### The evidence gap from Parts 7 and 8 is now closed

Every previous run was missing a firmware-side statement that *this* capsule
had been applied. `fw_version` sat at `131072` because the version was a
constant, so it could never move. It moved this time:

| | Before | After |
|---|---|---|
| ESRT `fw_version` | `131072` | **`132089`** |
| ESRT `last_attempt_version` | `131072` | **`132089`** |
| ESRT `last_attempt_status` | 0 | 0 |

The staged capsule declares `FwVersion = 132089` in its FMP payload header,
read directly from the `.cap`:

```
offset        = 2578          # "MSS1"
HeaderSize    = 16
FwVersion     = 132089  (0x203f9)
LowestSupport = 0
```

`0x203f9` is `0x20000 | 1017` — major 2, low half the kernel ABI. So the fix
in *"capsule: derive the capsule version from the kernel ABI"* is live and
behaving: the version now increments with the ABI instead of being frozen, and
firmware reports back the exact value the capsule declared.

This is the first run in which the firmware itself distinguishes "this capsule
applied" from "some capsule applied at some point". Combined with the
provenance node going absent → present, the result no longer rests on any
single indicator.

### Full result

| Check | Before | After |
|---|---|---|
| Provenance node | **absent** | **`70208457…2dd6ab84`** |
| `linux-modules` marker | `70208457…2dd6ab84` | match |
| ESRT `fw_version` | `131072` | **`132089`** |
| `OsIndications` | `0x4` (armed) | **`0x0`** (firmware cleared) |
| Capsule on ESP | staged | **consumed** |
| `/boot/efi` | `/dev/sda1` | `/dev/sda1` (correct ESP both times) |

```
dtb_pairing_state=apply_confirmed
dtb_kver_content_match=ok
summary="OK: capsule applied and verified"
```

### The ESP fix is shipped, and correctly silent

`dtb-capsule-7.0.0-1017-qcom` now declares
`Depends: linux-modules-… , efivar, efibootmgr`, and the installed postinst
contains the resolution helpers. On this board `/boot/efi` was `/dev/sda1`,
matching the `BootCurrent` PARTUUID, so the check **took the match branch and
said nothing** — which is the behaviour that matters in production. A guard
that fires on healthy systems would be worse than no guard, and this run is
the evidence it does not.

The mismatch branches were exercised separately in Part 8 against a live fault.

### One observation, not a conclusion

This boot took materially longer than the ~90 s of earlier runs — several
minutes before SSH returned. That is consistent with firmware actually writing
the DTB partitions rather than skipping a capsule whose version it already had,
which is what every previous run presented it with. It is only circumstantial;
nothing was instrumented to measure it, and it is recorded so a future slow
boot after staging is not mistaken for a hang.

### What this run does and does not settle

Settled: the mechanism works end to end, reproducibly, from a pristine board,
with the version now derived from the ABI and confirmed by firmware.

Not settled, and unchanged: the capsule is still signed with a local test key,
so this proves the *plumbing*, not the trust model. Launchpad-side signing,
the production key and its embedding into production `uefi_dtbs` with an MBN
re-signature, and the GRUB `devicetree` override all remain open. The override
in particular is still being worked around by hand on every test, and that is
not a shipping answer.

---

## Part 10 — capsule5 (1018): upgrading a board that *already* applied a capsule

PPA `ppa:kuba-t-pawlak/capsule5`, version `7.0.0-1018.21`, described as
"nothing new, just bumped version". The board was left by Part 9 at 1017 with
the capsule applied, so for the first time the starting state was *not* a
never-updated device. Three things came out of this that earlier runs could
not have shown.

### Result: pass, and the first re-application over an applied state

| Check | Before | After |
|---|---|---|
| Kernel | `7.0.0-1017-qcom` | `7.0.0-1018-qcom` |
| Provenance node | `70208457…2dd6ab84` | **`c7087de0…445f2ef9`** |
| `expected-dtb-sha256` (1018 pkg) | — | `c7087de0…445f2ef9` — matches |
| ESRT `fw_version` | `132089` | **`132090`** (`0x203fa` = `0x20000 \| 1018`) |
| ESRT `last_attempt_status` | 0 | 0, `last_attempt_version` 132090 |
| `OsIndications` | `0x4` (armed) | `0x0` (firmware cleared) |
| Capsule on boot ESP | staged | consumed |
| Verifier | — | `CONFIRMED … matches linux-modules-7.0.0-1018-qcom` |

Every previous run went absent→present on the provenance node. This one went
present→**changed**, from a known old value to a known new one. That is a
strictly stronger statement: it rules out "the node was already there from a
previous flash" as an explanation, and it proves the firmware will *re-apply*
a capsule over DTB partitions that already hold a capsule-delivered payload —
not just populate empty ones. `fw_version` incrementing a second time
(132089 → 132090) confirms Part 9's reading was not a one-off.

### The ESP guard fired for real, during a real `apt` install

This is the first time the fix from `df73135` ran outside a hand-constructed
test. At install time the `system-boot` label had resolved to the *wrong*
partition, and the postinst said:

```
dtb-capsule: ERROR: /boot/efi is PARTUUID 9e0cd0e8-b4ad-472f-9ccc-e300de1aaad3,
but firmware boots from PARTUUID 6ee0d619-e579-4707-bc23-63972c9333b7, which is
not mounted. Staging here would write the capsule where firmware never looks and
it would never be applied, with no error reported. Not staging. Mount the correct
ESP at /boot/efi and reinstall this package.
```

The install still completed (exit 0); only the staging step declined. Without
the guard this upgrade would have: copied the capsule to a partition firmware
never reads, "armed" `OsIndications` into an efivarfs whose backing store
(`VolatileVars.bin`, present only on the boot ESP) was not mounted, passed its
own read-back check, exited 0 — and then rebooted into a GRUB that had just
regenerated its `devicetree` directives pointing at 1018's on-disk DTB, which
would have produced a *correct-looking* 1018 DTB from entirely the wrong
mechanism. The failure would have been invisible from every angle.

Mounting `/dev/sda1` and re-running the postinst staged correctly and the guard
stayed silent, as it should on a healthy system.

Two side notes worth keeping:

- The 1017 **prerm** cleaned up after itself, removing the stale unconsumed
  capsule Part 8 had left on the wrong ESP.
- After the reboot the label flipped **back** to `nvme0n1p1`. So the guard will
  fire again on the next upgrade. The non-determinism is not settling down, and
  the image-side fix (mount the ESP by UUID, or stop duplicating the label)
  is still the right long-term answer.

### "Just a version bump" still changes all 468 DTB hashes

The provenance hash moved, which is expected — but it is worth understanding
*why*, because it bears on what the hash can and cannot tell you.

Comparing the 1017 and 1018 capsule payloads byte-for-byte: same size, and
only **476 bytes out of 4,198,306 differ (0.01%)**. Yet all 468 entries in
`dtb-provenance-content-sha256sums.txt` have different hashes. The reason is
visible in the bytes:

```
1017: b'om 7.0.0-1017.20\x00om,\x00\x00\x00\x01'
1018: b'om 7.0.0-1018.21\x00om,\x00\x00\x00\x01'
```

Every DTB embeds the package version string, so a version-only rebuild changes
~4 bytes in each of 468 files — which is almost exactly the 476 bytes observed.
The remaining differences are the RSA signature over the changed content, the
`MSS1` `FwVersion` field (`0x203f9` → `0x203fa`), and a FAT directory timestamp
in the embedded filesystem image.

So the DTB *content* is semantically unchanged between 1017 and 1018. The
practical consequences:

- **The provenance hash cannot answer "did the hardware description change?"**
  It changes on every rebuild by construction. It is a build identifier, not a
  content identifier. That is a defensible design — provenance should pin the
  build — but it should not be mistaken for change detection.
- **Every ABI bump ships a full 4 MB capsule and triggers a firmware write**,
  even when nothing about the hardware description changed. Across a fleet that
  is flash wear and added boot time for no functional benefit. If that matters,
  the capsule would need to be emitted only when the content hashes (excluding
  the version string) actually differ.
- The FAT timestamp means the payload is **not reproducible** even at a fixed
  version. Minor, but it would defeat a byte-for-byte rebuild check.

### Unchanged from previous runs

The GRUB `devicetree` override still had to be commented by hand — and this
time `update-grub` emitted **7** directives rather than 5, because a third
kernel is now installed. It regenerates on every kernel install, so the
workaround does not persist. This remains the single biggest gap between what
has been demonstrated and what could ship.

The boot after staging took 150 s, consistent with Part 9's slower boot and
with firmware actually writing the DTB partitions.

---

## Part 11 — two meta dependency defects, and why they were the same bug

Two symptoms were reported, which turn out to be one failure with two
contributing causes:

1. On the `capsule5` PPA, `linux-meta` built and published while `linux-signed`
   had not been dispatched yet, even though meta's binaries depend on what
   signed produces.
2. On a clean 1013 board, `apt install linux-qcom` from `capsule4` proposed a
   **mixed-flavour** set: generic headers alongside the *realtime* image and
   modules.

### Reproducing it

Rather than reason about it, the relationships were rebuilt in a throwaway apt
archive (`file:` repo, isolated `Dir::State`), with `dtb-capsule` deliberately
absent to model the publication window:

| # | `-rt` `Provides` | capsule relation | capsule published | Result |
|---|---|---|---|---|
| A | present | `Depends` | **no** | **RT image + generic headers** — reproduces the report |
| B | present | `Depends` | yes | correct generic set |
| C | removed | `Depends` | no | fails loudly: *"linux-image-qcom : Depends: dtb-capsule-… but it is not installable"* |
| D | removed | `Recommends` | no | **correct generic set, capsule simply skipped** |
| E | removed | `Recommends` | yes | correct generic set **+** capsule |

Scenario A produced the reported package list, so the mechanism is settled, not
inferred.

### Cause 1 — the capsule was a hard dependency, against its own stated design

`linux-image-qcom` carried `Depends: … dtb-capsule-${kernel-abi-version}-qcom`.
`dtb-capsule` is built by **linux-signed**, so while signed is still pending the
generic meta is uninstallable.

The commit that introduced it, `7e62ac4` *"meta: recommend the DTB capsule
package"*, argued explicitly for `Recommends` and gave as its first reason:

> "A missing or withdrawn capsule build must not make `linux-image-qcom`
> uninstallable and block kernel upgrades."

The change then landed in `Depends`. The package never behaved the way its own
commit message described, and the exact failure that message predicted is the
one that occurred.

### Cause 2 — the realtime meta advertised itself as the generic meta

`linux-image-qcom-rt` carried:

```
Provides: … linux-image-qcom${variant:suffix} (= ${binary:Version})
```

It is the **only** unsuffixed `qcom` token in `control.d/qcom-rt` — everything
else was renamed to `-rt` when the flavour was added in `bf08d4f` — and
`control.d/qcom` does not reciprocate it. Ubuntu does not do this either:
`linux-image-realtime` and `linux-image-lowlatency` Provide only the DKMS
module virtuals, never another flavour's image name. It reads as a copy-paste
leftover.

On its own it looks harmless. Combined with cause 1 it is what converted a
transient archive state into a *silently wrong install*: with the real
`linux-image-qcom` unsatisfiable, apt did not report a problem, it simply
satisfied the dependency from the realtime meta instead. The user asked for the
generic flavour by name and would have got a realtime kernel with generic
headers.

That asymmetry is the whole story: the generic meta depends on `dtb-capsule`
and the realtime meta does not, so the realtime path stayed installable exactly
when the generic path broke.

### Cause 3 — the build gate did not cover linux-signed

`Build-Depends-Arch` listed only main-build binaries. Nothing in `debian/rules`
consumes them, so they exist purely as a publication gate. But
`linux-image-qcom` depends on `linux-image-<abi>-qcom`, which **linux-signed**
produces — so meta could build and publish before signed had started, which is
precisely symptom 1.

### Fixes

Three commits in `linux-meta`, one concern each:

| Commit | Change |
|---|---|
| `c0168c8` | `dtb-capsule` → `Recommends` in `control.d/qcom`, as `7e62ac4` intended |
| `74bd86b` | drop the cross-flavour `Provides` from `control.d/qcom-rt` |
| `6751592` | add `linux-image-<abi>-qcom` to `Build-Depends-Arch` to order meta after signed |

Gating on one signed-produced binary is enough to order meta after the entire
signed build, which covers `dtb-capsule` from the same source package.
Deliberately *not* gating on `dtb-capsule` itself — that would re-create at
build time the hostage situation the `Recommends` change just removed at
install time.

Verified: `debian/control` regenerates cleanly, the binary package set is
unchanged at 16, both the `-7.0` and unversioned variants of each flavour
carry the fix, and the new gate appears in `dpkg-checkbuilddeps` output.
Note `debian/control` is an untracked build artifact in this tree — only the
`control.d/*` and `control.common` templates are committed.

### Worth noting

`Recommends` is installed by default by apt but **not** by
`--no-install-recommends`, which some image builds and CI use. Those will now
need `dtb-capsule-<abi>-qcom` named explicitly, or the capsule will silently
not be installed — a weaker guarantee than `Depends` gave, and the deliberate
price of not letting the capsule block kernel upgrades.

---

## Part 12 — retiring `kgsl-dkms` in favour of the in-tree module

`45aaa1fa5d16` made the kernel build compile kgsl and ship `msm_kgsl.ko.zst`
inside `linux-modules-<abi>-<flavour>`. That half worked: the module is in the
tree, but every existing system still has the `kgsl-dkms` package, which still
owns the `kgsl-dkms` name the Adreno userspace depends on. So DKMS rebuilds
`msm_kgsl` for each new kernel and then declines to install it over the in-tree
copy ("already installed (unversioned module)"). The rebuild is pure waste.

### The dead end that looked like the answer

The unsigned image stanza already carries `Provides: ... ${linux:rprovides}`,
and `debian.qcom/dkms-versions` already contains an entry using exactly the
idiom wanted here — `rprovides=v4l2loopback-dkms`, a DKMS *package* name. This
looks like the mechanism was already built and just needed an entry.

It is not. `rprovides` is driven by `all_built-in_dkms_modules`, and both that
variable and `all_dkms_modules` are **never assigned anywhere in the tree**:

```
$ grep -rn "all_dkms_modules" debian/ debian.qcom/ | grep -v '\$('
(nothing)
```

So every `$(foreach _m,$(all_dkms_modules),...)` loop is a no-op,
`${linux:rprovides}` always expands empty, and `dkms-versions` has no consumer
at all. The Ubuntu DKMS framework is present as skeleton only — which is why
`45aaa1fa5d16` bypassed it with a bespoke `do_dkms_kgsl` flag and a direct
`build_dkms` call. Reviving the framework to ship one `Provides` would have
been a much larger change than stating the relationship directly.

### Where the relationship belongs

Two candidates:

| Package | For | Against |
|---|---|---|
| signed `linux-image-<abi>-<flavour>` | inherits `Provides`/`Conflicts` from the unsigned package automatically — `linux-signed/debian/scripts/generate-depends` runs `apt-cache show` on the unsigned image and re-exports them as `${unsigned:*}` | the module does not live here |
| `linux-modules-<abi>-<flavour>` | the module *and* the glue live here; lifetimes match exactly | needs a `Provides:` line added, the stanza had none |

Chose `linux-modules`. The claim should sit on the package that actually
supplies the thing being claimed.

### Versioned Conflicts is load-bearing

Every ABI and flavour of `linux-modules` provides `kgsl-dkms`, so each kernel
provides the name every other kernel conflicts with. Proven in an apt sandbox:

| Form | second kernel installed alongside the first |
|---|---|
| `Conflicts: kgsl-dkms` | ❌ `Remv linux-modules-7.0.0-1019-qcom` — kernels evict each other |
| `Conflicts: kgsl-dkms (<< 999:0)` | ✅ both installed |

An unversioned `Provides` cannot satisfy a *versioned* relation, so the
versioned form matches only the real `kgsl-dkms`. The unversioned form would
have passed a single-upgrade test and destroyed the fallback kernel on the
*second* upgrade — worth stressing, because that is exactly the shape of bug
that escapes a quick manual check.

Sandbox results with the implemented fix:

```
# board-like state: kgsl-dkms + libadreno + linux-modules-1018
Remv kgsl-dkms [1.0-1] [libadreno:amd64 ]
Inst linux-modules-7.0.0-1019-qcom
# libadreno is NOT removed — the Provides satisfies its dependency
```

### `kgsl-dkms` shipped more than a module

The part that would have broken the GPU silently. `dpkg -L kgsl-dkms` on the
board, minus `/usr/src`:

| File | Load-bearing? |
|---|---|
| `/usr/lib/modprobe.d/kgsl-dkms.conf` — `options msm skip_gpu=1`, `softdep msm post: msm_kgsl` | **yes** — `CONFIG_DRM_MSM=m` and `modinfo msm` shows `parm: skip_gpu:bool`; without it DRM `msm` binds the GPU before KGSL |
| `/usr/lib/udev/rules.d/60-kgsl-dkms.rules` — `/dev/kgsl-3d0` → `root:video 0660` | **yes** — matches the live node exactly |
| `/usr/share/initramfs-tools/hooks/kgsl` | **no** — platform uses dracut; `initramfs-tools` is `not-installed` and `lsinitramfs` finds zero kgsl entries |

The first two are now shipped by `linux-modules`, ABI/flavour-namespaced
(`kgsl-<abi>-<flavour>.conf`, `60-kgsl-<abi>-<flavour>.rules`) so coexisting
kernels do not file-conflict. Verified that duplicate directives are harmless:
`modprobe --showconfig` with two identical files parses both without error, and
`skip_gpu=1 skip_gpu=1` is the same boolean value twice.

### Incidental bug found

`debian/rules` read `$(DROOT)/kgsl-modules.ignore`, but `DROOT` is never
assigned, so the path was `/kgsl-modules.ignore`, the `cat` always failed, and
the `do_dkms_kgsl=false` path never actually added `msm_kgsl` to
`modules.ignore`. Fixed in `b1382b350f03`.

### Commits (`linux-main`)

| Commit | Change |
|---|---|
| `b1382b350f03` | fix undefined `DROOT` in the kgsl disable path |
| `bbe91dbafa5d` | `Provides`/`Conflicts`/`Replaces` on `linux-modules` + ship modprobe.d and udev glue |

### Caveats

- **Rollback cost.** Removing `kgsl-dkms` runs `dkms remove`, which deletes
  `msm_kgsl.ko` from older kernels' `/lib/modules/*/updates/dkms/`. Booting a
  pre-`45aaa1fa5d16` kernel afterwards comes up without a GPU. Unavoidable
  given the package is being retired; needs to be in release notes.
- **Unversioned Provides.** Adreno's dependencies are currently unversioned
  (`Depends: ... kgsl-dkms`), so the bare `Provides` satisfies them. If they
  ever become versioned it will stop working — and making our `Provides`
  versioned to compensate would re-introduce the inter-kernel conflict.
- **Control/payload can diverge.** The `Provides` is hard-coded in the stub
  while the payload is gated on `do_dkms_kgsl`. That is consistent today
  (`do_dkms_kgsl = true` in `debian.qcom/rules.d/arm64.mk`, and arm64 is the
  only arch file), but `build_dkms` treats rc 9/77 as "skip", so a kgsl build
  failure would ship a kernel claiming `kgsl-dkms` without the module. Worth a
  build-time assertion later; `control-create` has no conditional mechanism to
  express it today.
