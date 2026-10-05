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

### Still outstanding

`/usr/lib/modules/<kver>/dtb-provenance-sha256` is **not shipped** by
`linux-modules`, so the strongest check — cross-checking the running DTB's
provenance against the kernel package actually installed — degrades to a
warning even on a fully successful apply. Worth closing, since that is the
check that ties the DTB to the kernel.

### Board state afterwards

Healthy, 7.0.0-1016-qcom, `dtb_a` now written at version 0x20000,
`grub.cfg` restored to its original form (backup left at
`/boot/grub/grub.cfg.capsule-test-bak`). The fixed `verify-capsule-result.sh`
is installed by hand at `/usr/share/dtb-capsule/`.
