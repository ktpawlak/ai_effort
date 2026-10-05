# Testing the PPA capsule on hamoa (2026-10-05)

Target: [`ppa:kuba-t-pawlak/capsule`](https://launchpad.net/~kuba-t-pawlak/+archive/ubuntu/capsule),
`linux-signed-qcom 7.0.0-1016.19`, built from `d8fb7f8`.

**Hardware was not reachable from this workstation** — it is x86_64, and
`klumsy` / `tippi` / `balboa` are all behind the VPN (connect timeout / DNS
failure). So the capsule was taken apart and validated statically instead,
which turned out to settle the question anyway: see the signing blocker below.

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
| **5** | **Auth error** | **expected result with test keys — pipeline is good** |

So **status 5 is the pass condition for this build.** Status 4 would be the
interesting failure, meaning the payload layout is wrong independently of
signing. Status 3 means the device already has an equal-or-newer DTB firmware
version and `--fwver` needs bumping.

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
