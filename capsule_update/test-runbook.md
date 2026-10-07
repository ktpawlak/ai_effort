# Runbook: replicating the capsule update test on a freshly flashed Hamoa

Every command here was run during the 2026-10-07 session. Values that are
board- or PPA-specific are called out so they can be swapped.

Assumes: Hamoa at `192.168.1.123`, user `ubuntu`, password `changeme12`, sudo
**not** passwordless. Serial on `/dev/ttyUSB1` @115200. Capsule tooling at
`~/qualcomm/resolute/linux-qcom/linux-signed/debian/capsule`.

A convenient host-side helper, since every board command needs sudo:

```bash
cat > /tmp/hs.sh <<'EOF'
#!/bin/bash
sshpass -p changeme12 ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=10 ubuntu@192.168.1.123 \
  "echo changeme12 | sudo -S bash -c $(printf '%q' "$1") 2>&1" 2>/dev/null | grep -v "^\[sudo"
EOF
chmod +x /tmp/hs.sh
```

---

## Phase 0 — put our root certificate into `uefi_dtbs` (host, before flashing)

Only needed once per board image. Without it the firmware trusts only
Qualcomm's `CN = rootuser` and will reject our capsules.

```bash
cd ~/qualcomm/resolute/linux-qcom/linux-signed/debian/capsule

# PEM -> DER. The tool embeds raw bytes without parsing, so PEM would be wrong.
openssl x509 -in test-keys/root.crt -outform DER -out /tmp/our-root.cer

PYTHONPATH=. python3 -m qcom_capsule_tool.cli patch-capsule-cert \
    ~/qualcomm/ai_effort/qpa/boards/hamoa/nhlos/uefi_dtbs.xz \
    /tmp/our-root.cer \
    /tmp/uefi_dtbs-patched.xz
```

Expect:

```
[+] Detected ELF type : uefi_dtbs
[i] Segment SHA-384 updated at file 0x2c150
[+] uefi_dtbs: patched=2  skipped=1  errors=0
```

Then put it where the flashing XML expects it and flash:

```bash
cp /tmp/uefi_dtbs-patched.xz ~/qualcomm/ai_effort/qpa/boards/hamoa/nhlos/uefi_dtbs.xz
cd ~/qualcomm/ai_effort/qpa && ./flash-hamoa.sh
```

It lands in SPI NOR `uefi_dtb_a` and `uefi_dtb_b` (64 KB slots; the patched file
is ~19 KB). `uefi_dtbs_kvm.xz` is not referenced by `partition_spinor/` and does
not need patching.

To confirm what is embedded in any `uefi_dtbs.xz`:

```bash
xzcat uefi_dtbs.xz > /tmp/ud.elf
python3 - <<'EOF'
import libfdt
from elftools.elf.elffile import ELFFile
data=open('/tmp/ud.elf','rb').read()
for seg in ELFFile(open('/tmp/ud.elf','rb')).iter_segments():
    off,sz=seg['p_offset'],seg['p_filesz']
    if sz<4 or data[off:off+4]!=b'\xd0\x0d\xfe\xed': continue
    fdt=libfdt.Fdt(data[off:off+sz])
    o=fdt.path_offset('/fragment@6/__overlay__/uefi/uefiplat',
                      quiet=(libfdt.FDT_ERR_NOTFOUND,libfdt.FDT_ERR_BADPATH))
    if o<0: continue
    b=bytes(fdt.getprop(o,'QcCapsuleRootCert'))
    open('/tmp/embedded.der','wb').write(b[4:4+int.from_bytes(b[:4],'big')])
EOF
openssl x509 -inform DER -in /tmp/embedded.der -noout -subject
```

Ours reads `CN = Test Capsule Root CA`; stock reads `CN = rootuser`.

> The patch invalidates the image's MBN signature — the tool updates the segment
> hash but never re-signs. Fine on a test-key-fused board, fatal on a production
> one.

---

## Phase 1 — add the PPA (board)

Key fingerprint is the PPA owner's, shared across `capsule`/`capsule2`/`capsule3`.

```bash
curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x11A8E87D94803FE952C174E441D8E6A0B33F2037" \
    -o /tmp/ppakey.asc
scp /tmp/ppakey.asc ubuntu@192.168.1.123:/tmp/
```

```bash
/tmp/hs.sh 'install -m644 /tmp/ppakey.asc /etc/apt/keyrings/kuba-capsule.asc
cat > /etc/apt/sources.list.d/capsule3.sources <<EOF
Types: deb
URIs: https://ppa.launchpadcontent.net/kuba-t-pawlak/capsule3/ubuntu/
Suites: resolute
Components: main
Signed-By: /etc/apt/keyrings/kuba-capsule.asc
EOF
apt-get update'
```

---

## Phase 2 — install the kernel and capsule

`--no-install-recommends` is deliberate: it proves the capsule arrives through a
hard `Depends`, which is the packaging fix under test.

```bash
/tmp/hs.sh 'rm -f /var/lib/dtb-capsule/last-*
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends linux-image-qcom'
```

Expect `dtb-capsule-7.0.0-1017-qcom` among the unpacked packages. Verify:

```bash
/tmp/hs.sh 'dpkg -s linux-image-qcom | grep -E "^(Version|Depends|Recommends):"'
```

`Depends:` must list `dtb-capsule-…`; there must be no `Recommends:` line.

---

## Phase 3 — disable the GRUB devicetree override

Do this **now**, before the capsule reboot, so one reboot covers both. Otherwise
GRUB loads `/boot/dtb-*` and the capsule-delivered DTB never reaches Linux.

```bash
/tmp/hs.sh 'cp /boot/grub/grub.cfg /boot/grub/grub.cfg.capsule-test-bak
sed -i "s|^\(\s*\)devicetree\(\s.*\)$|\1#devicetree\2|" /boot/grub/grub.cfg
echo -n "active devicetree directives remaining: "
grep -cE "^\s*devicetree" /boot/grub/grub.cfg'
```

Must print `0`. On the test board there were five.

> `update-grub` regenerates `grub.cfg`, so any later kernel package operation
> brings the directives back. Re-run this step after one.

---

## Phase 4 — stage the capsule on the ESP the firmware actually reads

**Do not trust `/boot/efi`.** Both `/dev/sda1` and `/dev/nvme0n1p1` are labelled
`system-boot` and fstab mounts by label, so the mount flips between reboots.
Resolve by PARTUUID from `BootCurrent` instead:

```bash
/tmp/hs.sh 'cur=$(efibootmgr | awk "/^BootCurrent:/{print \$2}")
pu=$(efibootmgr -v | grep -i "^Boot${cur}" | grep -oiE "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" | head -1)
esp=$(blkid -t PARTUUID="$pu" -o device | head -1)
echo "firmware boots: $esp"
echo "/boot/efi is  : $(findmnt -no SOURCE /boot/efi)"'
```

On the test board `BootCurrent=0000` → PARTUUID `6ee0d619-e579-4707-bc23-63972c9333b7`
→ `/dev/sda1`.

Stage to that device explicitly, and clear any stale capsule from the other ESP:

```bash
/tmp/hs.sh 'cur=$(efibootmgr | awk "/^BootCurrent:/{print \$2}")
pu=$(efibootmgr -v | grep -i "^Boot${cur}" | grep -oiE "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" | head -1)
esp=$(blkid -t PARTUUID="$pu" -o device | head -1)

# wipe every ESP first so no stale capsule can be picked up
for d in /dev/sda1 /dev/nvme0n1p1; do
    mkdir -p /mnt/chk && mount $d /mnt/chk 2>/dev/null || continue
    find /mnt/chk -iname "*.cap" -delete 2>/dev/null
    umount /mnt/chk
done
rmdir /mnt/chk 2>/dev/null

mkdir -p /mnt/esp && mount "$esp" /mnt/esp
mkdir -p /mnt/esp/EFI/UpdateCapsule
cp /usr/share/dtb-capsule/hamoa/hamoa-dtb.cap /mnt/esp/EFI/UpdateCapsule/
sync
md5sum /mnt/esp/EFI/UpdateCapsule/hamoa-dtb.cap /usr/share/dtb-capsule/hamoa/hamoa-dtb.cap
umount /mnt/esp; rmdir /mnt/esp'
```

The two md5s must match.

---

## Phase 5 — arm the update

`OsIndications` bit 2 (`0x4`) tells the firmware to look for capsules on the ESP.

### Use `efivar` (preferred)

```bash
printf '\004\000\000\000\000\000\000\000' > /tmp/osind.bin
efivar -n 8be4df61-93ca-11d2-aa0d-00e098032b8c-OsIndications -f /tmp/osind.bin -w
efivar -n 8be4df61-93ca-11d2-aa0d-00e098032b8c-OsIndications -p
```

This is exactly what the shipped `dtb-capsule` postinst does, and `efivar` is a
hard `Depends` of that package, so it is always present on a board that has the
capsule installed.

Points on the syntax:

- `-n` takes **one** argument with the GUID first and the variable name appended:
  `<guid>-<name>`. There is no separate "variable name" and "namespace GUID"
  flag.
- The data file holds **only** the 8-byte value. `efivar` supplies the 4-byte
  attribute word itself (`NV|BS|RT`), which is why there is no attribute flag
  here. Do not confuse `-a` (`--append`) with `-A` (`--attributes`, and that one
  only applies to appends).
- Prefer this over a raw efivarfs write because `efivar -w` can **create** the
  variable if NVRAM has no entry for it yet.

`-p` should print the value with bit 2 set.

### `efi-updatevar` cannot do this

`efi-updatevar` (from `efitools`) is for Secure Boot key and signature databases
only. It has no variable-namespace or raw-value option — its `-g` flag is the
*owner GUID of an X509 certificate inside an EFI Signature List*, not the
variable's vendor GUID, and there is no `-v` flag at all. Asking it for
`OsIndications` gets you:

```
$ efi-updatevar -f osind.bin OsIndications
Invalid Variable OsIndications
Variable must be one of: PK KEK db dbx
```

The allowed set is hardcoded. Use `efivar`.

### Raw efivarfs fallback

If `efivar` is unavailable, efivarfs takes a plain 12-byte write: 4-byte
attribute word then the 8-byte value, both little-endian.

```bash
V=/sys/firmware/efi/efivars/OsIndications-8be4df61-93ca-11d2-aa0d-00e098032b8c
chattr -i "$V" 2>/dev/null || true
printf "\007\000\000\000\004\000\000\000\000\000\000\000" > "$V"
xxd "$V"
```

Must read back `0700 0000 0400 0000 0000 0000`. A `write error: Input/output
error` is harmless provided the readback is right.

| Offset | Len | Value | Meaning |
|---|---|---|---|
| 0 | 4 | `07 00 00 00` | attributes `NON_VOLATILE\|BOOTSERVICE_ACCESS\|RUNTIME_ACCESS` |
| 4 | 8 | `04 00 00 00 00 00 00 00` | bit 2, `EFI_OS_INDICATIONS_FILE_CAPSULE_DELIVERY_SUPPORTED` |

Four things that will bite you on the raw path:

- **Use octal escapes, not `\xHH`.** `printf` is a shell builtin and dash's does
  not understand `\x` — it passes the characters through literally, so you write
  48 bytes of ASCII instead of 12 bytes of binary. `\NNN` works in both bash and
  dash. The shipped postinst has a comment about exactly this.
- **The 4-byte attribute prefix is mandatory** and must match the variable's
  existing attributes, otherwise `EINVAL`.
- **It must be a single `write()`.** `printf … > "$V"` is one write of 12 bytes.
- **`chattr -i` first**, or you get `EPERM` even as root.

Note this raw form hardcodes `0x4` rather than doing a read-modify-write, so it
would clear any other `OsIndications` bit that happened to be set. The postinst
ORs bit 2 into the current value instead.

In practice you rarely need Phase 5 by hand at all: installing the capsule
package runs the postinst, which stages the capsule *and* sets the bit, with a
read-back check. Phase 5 is for re-arming after a reboot consumed the capsule,
or when staging a hand-built capsule as in Phase 9.

---

## Phase 6 — reboot with serial capture

`systemctl reboot` stalled ~8 minutes on `wireplumber` and the firmware phase was
missed. Use sysrq.

Host, first:

```bash
rm -f /tmp/cap.log
timeout 260 bash -c 'stty -F /dev/ttyUSB1 115200 raw -echo; cat /dev/ttyUSB1 > /tmp/cap.log' &
sleep 2
```

Then trigger it:

```bash
/tmp/hs.sh 'sync; echo b > /proc/sysrq-trigger' &
```

Boot takes roughly 135–145 s, plus a cold reset for TrialBoot, so allow ~250 s.

---

## Phase 7 — read the verdict off the serial log

```bash
strings -a /tmp/cap.log | sed 's/\x1b\[[0-9;]*m//g' \
  | grep -iE "mass-storage capsule|Update Success|Pkcs7Verify|Security Violation|TrialBoot|NewImage Version|Current Version"
```

Success looks like:

```
Loading mass-storage capsule file 'hamoa-dtb.cap'!
Starting to process mass-storage capsule file 'hamoa-dtb.cap'!
    NewImage Version                                  - 0x20000
    Current Version (partition)                       - 0x20000
        PartitionName      = dtb_a
      Update Success
  Phase 4: TrialBoot start.
```

> **ESRT cannot be used for this.** A rejected capsule is refused *before* an
> attempt is recorded, so `last_attempt_status` keeps the previous run's value
> and the firmware deletes the `.cap` either way — a failure can look exactly
> like a success. The serial log is the only reliable source.

---

## Phase 8 — confirm the DTB reached Linux

```bash
/tmp/hs.sh 'echo "running : $(tr -d "\0" < /sys/firmware/devicetree/base/qcom-dtb-capsule-provenance/dtb-provenance-sha256 2>/dev/null)"
echo "capsule : $(cat /usr/share/dtb-capsule/expected-dtb-sha256)"
echo "modules : $(cat /usr/lib/modules/$(uname -r)/dtb-provenance-sha256)"
systemctl start dtb-capsule-verify.service; sleep 3
cat /var/lib/dtb-capsule/last-verify-state'
```

All three hashes must be identical, and the verifier must report:

```
kver_match_state=ok
dtb_pairing_state=apply_confirmed
dtb_kver_content_match=ok
summary="OK: capsule applied and verified"
```

If the provenance node is missing entirely, GRUB is still overriding the DTB —
go back to Phase 3.

---

## Phase 9 — optional but recommended: prove authentication is real

A successful apply alone does not show the signature was checked; earlier
firmware accepted anything. Flip one bit in the PKCS#7 and confirm rejection.

Host:

```bash
scp ubuntu@192.168.1.123:/usr/share/dtb-capsule/hamoa/hamoa-dtb.cap /tmp/good.cap
python3 - <<'EOF'
import struct
d=bytearray(open('/tmp/good.cap','rb').read())
off=32+16+48+8                       # capsule hdr + FMP cap hdr + FMP image hdr + monotonic count
dwlen=struct.unpack_from('<I',d,off)[0]
mid=(off+24 + off+dwlen)//2          # middle of the PKCS#7 CertData
d[mid]^=0x01
open('/tmp/tampered.cap','wb').write(bytes(d))
print("flipped one bit at",mid)
EOF
scp /tmp/tampered.cap ubuntu@192.168.1.123:/tmp/
```

Stage `/tmp/tampered.cap` exactly as in Phase 4 (same destination filename),
re-arm as in Phase 5, reboot as in Phase 6. Expect:

```
FmpAuthenticatedHandlerPkcs7: Pkcs7Verify() failed
FmpDxe(...): CheckTheImage() - Authentication Failed Security Violation.
Failed to set the firmware payload 0. Status = Security Violation
Capsule process failed!
Deleting mass-storage capsule file 'hamoa-dtb.cap'!
```

Afterwards `ESRT last_attempt_status` becomes `1`. Clean both ESPs before
continuing (Phase 4's wipe loop).

If the tampered capsule *applies*, authentication is disabled on that firmware
and the Phase 7 success proved nothing about the certificate.

---

## Known-good reference values (1017.20, capsule3)

| Thing | Value |
|---|---|
| DTB provenance sha256 | `7020845700e1162666a879991ea781068b9f0730fdef6eb6a6072bcb2dd6ab84` |
| Capsule `FwVersion` | `0x20000` (fwver fix not yet built) |
| Hamoa FMP GUID | `0f6d58fc-2258-4d27-9e23-d77219b0897c` |
| UEFI build | `BOOT.MXF_UEFI.2.5-00690-HAMOA-1` |
| Boot ESP PARTUUID | `6ee0d619-e579-4707-bc23-63972c9333b7` (`/dev/sda1`) |
| Capsule size | 4198306 bytes |

## Quick failure triage

| Symptom | Cause |
|---|---|
| No `Loading mass-storage capsule` on serial | `OsIndications` not `0x4`, or capsule staged on the wrong ESP |
| `Pkcs7Verify() failed` with a genuine capsule | `uefi_dtbs` lacks our root, or was flashed to only one slot |
| `Update Success` but no provenance node | GRUB `devicetree` override still active |
| Provenance hash mismatch | capsule and `linux-modules` from different builds |
| ESP empty, ESRT says success, but nothing changed | capsule was rejected — check serial, not ESRT |
| `apt` installs no capsule | meta is a `Recommends` build, or an older capsule blocks via `Conflicts` |
