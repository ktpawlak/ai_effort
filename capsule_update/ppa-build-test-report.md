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
| GRUB `devicetree` directive | **None** — `grub.cfg` is pristine (dated Aug 26, stock image) and contains no such line |
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
