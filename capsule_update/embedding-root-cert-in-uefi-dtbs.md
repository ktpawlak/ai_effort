# Embedding our capsule root certificate into `uefi_dtbs`

The device decides whether to trust a capsule by checking its PKCS#7 against a
root certificate stored in firmware, in a device-tree property called
`QcCapsuleRootCert`. Out of the box the Hamoa image carries **Qualcomm's** test
root, so capsules signed by us are rejected. `qcom-capsule-tool
patch-capsule-cert` replaces that property.

Everything below was run and verified against
`qpa/boards/hamoa/nhlos/uefi_dtbs.xz`.

## Command

```
qcom-capsule-tool patch-capsule-cert <input.elf> <cert.cer> <output.elf>
                                     [--prop-name QcCapsuleRootCert]
                                     [--meta-ph 1]
```

Three positional arguments, no flags needed in the normal case. The ELF type
(`uefi_dtbs` vs `xbl_config`) is auto-detected, and `.xz` is handled
transparently on **both** input and output, so the compressed file that is
actually flashed can be used directly as input and output.

The defaults are the ones you want: `--prop-name QcCapsuleRootCert` is the
property the Hamoa firmware reads, and `--meta-ph` only applies to the
`xbl_config` path.

## The certificate must be DER, not PEM

This is the easiest thing to get wrong. The tool does **not** parse the
certificate — it calls `bin_to_hex()`, which emits a 4-byte big-endian length
followed by the file's raw bytes. Hand it a PEM and you will faithfully embed
the base64 text, including the `-----BEGIN CERTIFICATE-----` armour, and the
firmware will not parse it.

So convert first:

```bash
openssl x509 -in test-keys/root.crt -outform DER -out /tmp/our-root.cer
```

The in-image layout is confirmed by reading back what shipped: a 968-byte
property whose first four bytes are `000003c2` = 962, followed by 962 bytes of
DER, padded to a 4-byte boundary.

## Which certificate

The **root**, not the signer. The capsule already carries the rest of the
chain:

```
subject=... CN = Test Capsule Sub CA      issuer=... CN = Test Capsule Root CA
subject=... CN = Test Capsule Signer      issuer=... CN = Test Capsule Sub CA
```

so the device only needs the anchor. Confirmed by verifying the capsule that
`capsule2` actually ships against nothing but the root:

```
$ openssl smime -verify -binary -inform DER -in cap.p7 \
      -content cap.content -CAfile test-keys/root.crt
Verification successful
```

## What is in there today

```
subject=C = IN, ST = Karnataka, L = Bangalore, O = Qualcomm Technologies Inc,
        OU = QCT, CN = rootuser
notBefore=Sep 18 05:20:10 2024 GMT   notAfter=Sep 16 05:20:10 2034 GMT
```

Qualcomm's own test root, at `/fragment@6/__overlay__/uefi/uefiplat`.

## Full worked example (Hamoa)

```bash
cd ~/qualcomm/resolute/linux-qcom/linux-signed/debian/capsule

# 1. root cert -> DER
openssl x509 -in test-keys/root.crt -outform DER -out /tmp/our-root.cer

# 2. patch (xz in, xz out)
PYTHONPATH=. python3 -m qcom_capsule_tool.cli patch-capsule-cert \
    ~/qualcomm/ai_effort/qpa/boards/hamoa/nhlos/uefi_dtbs.xz \
    /tmp/our-root.cer \
    /tmp/uefi_dtbs-patched.xz
```

```
[+] Detected ELF type : uefi_dtbs
[!] Per-DTB SHA-384 not found in hash segment (non-fatal)
[i] Segment SHA-384 updated at file 0x2c150
[+] uefi_dtbs: patched=2  skipped=1  errors=0
```

`patched=2` is the number of DTBs in the ELF that carried the property;
`skipped=1` is a DTB that does not. Both are expected. Read back to confirm:

```
subject=O = INSECURE TEST KEYS - DO NOT TRUST, CN = Test Capsule Root CA
```

## Deploying it

`uefi_dtbs.xz` lives in **SPI NOR**, in two slots, and is written by the normal
EDL flashing flow — it is not something you can update from a running Linux:

```xml
<partition label="uefi_dtb_a" size_in_kb="64" filename="uefi_dtbs.xz"/>
<partition label="uefi_dtb_b" size_in_kb="64" filename="uefi_dtbs.xz"/>
```

So overwrite the file in the board tree under the name the XML references and
flash:

```bash
cp /tmp/uefi_dtbs-patched.xz ~/qualcomm/ai_effort/qpa/boards/hamoa/nhlos/uefi_dtbs.xz
cd ~/qualcomm/ai_effort/qpa && ./flash-hamoa.sh
```

Size is fine: the patched file is 19460 bytes against a 64 KB slot.

Note `uefi_dtbs_kvm.xz` sits in the same directory but is **not** referenced by
`partition_spinor/`, so it does not need patching for this board.

## The caveat that matters: this invalidates the image signature

`uefi_dtbs` is a signed Qualcomm MBN image. Its third program header is a hash
table segment carrying per-segment SHA-384 digests **plus a signature and an
attestation certificate chain**:

```
SECTOOLS SECP384R1 CURVE TEST ROOT01
General Use Test Key 0 (for testing only)
SecTools Test User
```

`patch-capsule-cert` updates the segment hash (`Segment SHA-384 updated at file
0x2c150`) but contains no signing code at all — it never re-signs the hash
table. After patching, the MBN signature no longer matches.

That is fine on this board, because it is fused for Qualcomm's *test* keys and
the current UEFI build has capsule authentication disabled; XBL will load the
modified image. It will **not** be fine on a production-fused device, where the
patched `uefi_dtbs` would be rejected outright. Shipping a real root cert to
real hardware therefore needs the image re-signed with the key the device is
fused to — that is a Qualcomm/OEM step, not something this tool does.

Related: the `[!] Per-DTB SHA-384 not found in hash segment` warnings mean this
particular image does not carry individual per-DTB digests, only the
whole-segment one, so there was nothing else to update.

## Summary

| Question | Answer |
|---|---|
| Where to run it | Against `qpa/boards/hamoa/nhlos/uefi_dtbs.xz`, before flashing |
| Arguments | `<input.elf> <cert.cer> <output.elf>`; defaults are correct |
| Which cert | The **root** (`test-keys/root.crt`), the capsule carries the rest |
| Format | **DER** — convert with `openssl x509 -outform DER` |
| How it reaches the device | EDL flash into SPI NOR `uefi_dtb_a` / `uefi_dtb_b` |
| Blocker for production | Breaks the MBN signature; needs re-signing by the fused key |
