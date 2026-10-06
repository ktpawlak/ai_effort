# Could Launchpad assemble the capsule generically instead of signing ours?

Question: rather than Launchpad signing a capsule built by Qualcomm's tool,
could it take the always-required pieces (firmware volume + GUID) and assemble
and sign the capsule itself, generically, so that any vendor supplies the same
inputs?

Short answer: **yes, and we are already most of the way there.** The capsule
container is not Qualcomm-specific at all — it is plain edk2. Launchpad could
run stock `BaseTools/Source/Python/Capsule/GenerateCapsule.py` and get the same
bytes we ship today.

## What is actually vendor-specific

The capsule nests:

```
EFI_CAPSULE_HEADER                                  UEFI 2.10 §8.5.3
  EFI_FIRMWARE_MANAGEMENT_CAPSULE_HEADER            UEFI 2.10 §23.3
    EFI_FIRMWARE_MANAGEMENT_CAPSULE_IMAGE_HEADER    UEFI 2.10 §23.3
      EFI_FIRMWARE_IMAGE_AUTHENTICATION             UEFI 2.10 §23.1 (PKCS#7)
        FMP_PAYLOAD_HEADER 'MSS1'                   edk2 FmpDevicePkg
          <firmware volume>                         <- the only vendor part
```

Every layer above the firmware volume is standard. `MSS1` in particular is not
Qualcomm's: it is edk2's own `FmpPayloadHeaderLib`
(`FmpDevicePkg/Library/FmpPayloadHeaderLibV1/FmpPayloadHeaderLib.c`,
`FMP_PAYLOAD_HEADER_SIGNATURE SIGNATURE_32 ('M','S','S','1')`).

Even the 32-byte outer header, which the in-tree comment describes as a
historical quirk of these targets, is simply what edk2 emits:
`UefiCapsuleHeader.py` declares `_StructFormat = '<16sIIII'` (32 bytes, with a
reserved tail) and sets `HeaderSize` to it. There is no deviation to preserve.

The vendor-specific part is purely the *payload*: a firmware volume whose FFS
files carry `FvUpdate.xml` (naming `dtb_a` as the target and `dtb_b` as the
backup) and `SYSFW_VERSION.bin`. That is a contract with Qualcomm's FmpDxe, and
no generic tool can produce it.

## Measured proof of compatibility

Built a signed capsule with our tool and with stock edk2 `GenerateCapsule.py`
from the identical JSON descriptor and payload.

Stock edk2 parses our capsule completely:

```
EFI_CAPSULE_HEADER.CapsuleGuid   = 6DCBD5ED-E82D-4C44-BDA1-7194199AD92A
EFI_CAPSULE_HEADER.HeaderSize    = 00000020
...UpdateImageTypeId             = 0F6D58FC-2258-4D27-9E23-D77219B0897C
FMP_PAYLOAD_HEADER.Signature     = 3153534D (MSS1)
FMP_PAYLOAD_HEADER.FwVersion     = 000203F9
```

and our tool parses edk2's capsule equally completely. Both outputs are
**68130 bytes**, and:

| comparison | differing bytes |
|---|---|
| our tool vs stock edk2 | 258 |
| our tool vs **itself**, run twice | 258 |
| outside the PKCS#7 CertData region | **0** |

The 258 bytes are the PKCS#7 `signingTime` attribute and the RSA signature
value, which are non-deterministic — our tool disagrees with itself by exactly
the same amount. Outside the signature, the two capsules are byte-identical.
Stock edk2 is a drop-in replacement for the assembly step.

This is not accidental: `generate_capsule.py` was written to be interface- and
byte-compatible with edk2 (see its docstring), and it imports **nothing but the
Python standard library** — no Qualcomm module at all. It is 601 lines; the
other 4869 lines of the tool exist solely to build the firmware volume.

## What the current pipeline already does

The signing boundary has already been moved off "sign a finished capsule":

1. `-generate-` builds the FV (Qualcomm code).
2. `generate-capsule --emit-signable` writes
   `MSS1 header || FV || monotonic_count` — a generic UEFI FMP signing input
   with no capsule structure around it and no certificate involved.
3. The archive returns a detached PKCS#7 over exactly those bytes.
4. `signed-build` runs `generate-capsule --assemble` with the blob, the
   signature, `--guid`, `--image-index`, `--hardware-instance` and
   `--capflag`.

So Launchpad is **already signing a generic blob, not a Qualcomm capsule**.
What remains local is step 4, the assembly.

## What moving assembly to Launchpad would require

Launchpad would need, per payload, exactly the edk2 `GenerateCapsule.py` JSON
descriptor — nothing vendor-specific:

| input | ours | generic? |
|---|---|---|
| `Payload` (the FV) | `dtb.bin` in an FV | opaque blob, vendor builds it |
| `Guid` | `0F6D58FC-…` (ESRT `fw_class`) | yes |
| `FwVersion` | `0x203f9` | yes |
| `LowestSupportedVersion` | `0x0` | yes |
| `MonotonicCount` | `0` | yes |
| `HardwareInstance` | `0` | yes |
| `UpdateImageIndex` | `1` | yes |
| capsule flags | `PersistAcrossReset` | yes |

That is a small, fixed, vendor-neutral schema that any Ubuntu platform wanting
a signed capsule could supply.

### Why this is attractive

- **The signing key never signs attacker-chosen structure.** Today the archive
  signs a blob whose framing we assembled afterwards; if Launchpad assembles,
  it controls every header field around its own signature, so a malformed or
  hostile descriptor cannot cause a valid signature to end up wrapped in
  unexpected framing.
- **One implementation to audit** instead of per-vendor capsule builders.
- **It deletes our most security-sensitive code**: the 601-line assembler and
  the interim `test-keys` signing path both disappear from the kernel package.
- It is stock upstream code, maintained by tianocore.

### What it does not solve

- The FV still has to be built by vendor tooling and uploaded. Launchpad cannot
  validate its contents, only wrap it.
- `MonotonicCount` and `FwVersion` monotonicity would become an archive-side
  policy question — arguably a good place for it, since the archive is the only
  component that sees the whole upload history.
- edk2 `GenerateCapsule.py` wants `HardwareInstance`/`MonotonicCount`/
  `UpdateImageIndex` as hex *strings*; plain integers are rejected with
  "invalid syntax". A thin normalisation layer is needed, or the schema must be
  specified strictly.
- edk2 signs via `openssl smime` in-process, so Launchpad would need to
  substitute its own HSM-backed signer for that call rather than use
  `GenerateCapsule.py` wholesale. The clean split is: Launchpad does
  header assembly + signing, using edk2's header classes
  (`UefiCapsuleHeader.py`, `FmpCapsuleHeader.py`, `FmpAuthHeader.py`,
  `FmpPayloadHeader.py`) which are small, dependency-free and already proven
  byte-compatible above.

## Recommendation

Propose to Launchpad a capsule-signing mode that accepts
`{payload blob, GUID, FwVersion, LowestSupportedVersion, MonotonicCount,
HardwareInstance, UpdateImageIndex, flags}` and returns a finished capsule,
implemented with edk2's BaseTools capsule header classes. The evidence that it
produces exactly the bytes shipped devices already accept is above, and on our
side it would mean deleting `generate_capsule.py` and the whole local assemble
step rather than writing anything new.

## Appendix: the actual edk2 command sequence

Verified end to end: capsule built, parsed by both edk2 and the Qualcomm tool,
and the PKCS#7 signature verified against the root.

`GenerateCapsule.py` is not packaged in Ubuntu (`python3-virt-firmware` is a
different thing), so the tooling has to be vendored. Only six stdlib-only
files are needed — 2317 lines, ~115 KB — not the whole of BaseTools:

```bash
R=https://raw.githubusercontent.com/tianocore/edk2/master/BaseTools/Source/Python
mkdir -p edk2/Common/Uefi/Capsule edk2/Common/Edk2/Capsule edk2/Capsule
for f in Capsule/GenerateCapsule.py \
         Common/Uefi/Capsule/UefiCapsuleHeader.py \
         Common/Uefi/Capsule/FmpCapsuleHeader.py \
         Common/Uefi/Capsule/FmpAuthHeader.py \
         Common/Uefi/Capsule/CapsuleDependency.py \
         Common/Edk2/Capsule/FmpPayloadHeader.py; do
    curl -sfL "$R/$f" -o "edk2/$f"
done
find edk2 -type d -exec touch {}/__init__.py \;
```

The signer certificate must be a single PEM holding **both** the private key
and the certificate; `GenerateCapsule.py` passes it to `openssl smime -signer`
with no `-inkey`:

```bash
cat signer.key signer.crt > signer.pem
```

Then the whole capsule is one command — no JSON descriptor is required, every
field has a command-line flag:

```bash
PYTHONPATH=$PWD/edk2 python3 edk2/Capsule/GenerateCapsule.py \
    --encode \
    --guid                 0F6D58FC-2258-4D27-9E23-D77219B0897C \
    --fw-version           0x203F9 \
    --lsv                  0x0 \
    --monotonic-count      0x0 \
    --hardware-instance    0x0 \
    --update-image-index   0x1 \
    --capflag              PersistAcrossReset \
    --signer-private-cert  certs/signer.pem \
    --other-public-cert    certs/chain.pem \
    --trusted-public-cert  certs/root.crt \
    -o hamoa-dtb.cap \
    firmware.fv
```

Inspect it with either implementation:

```bash
PYTHONPATH=$PWD/edk2 python3 edk2/Capsule/GenerateCapsule.py --dump-info hamoa-dtb.cap
PYTHONPATH=debian/capsule python3 -m qcom_capsule_tool.cli \
    generate-capsule --dump-info hamoa-dtb.cap
```

Both report the same thing, and the signature verifies:

```
EFI_CAPSULE_HEADER.CapsuleGuid   = 6DCBD5ED-E82D-4C44-BDA1-7194199AD92A
...UpdateImageTypeId             = 0F6D58FC-2258-4D27-9E23-D77219B0897C
FMP_PAYLOAD_HEADER.Signature     = 3153534D (MSS1)
FMP_PAYLOAD_HEADER.FwVersion     = 000203F9
openssl smime -verify ... -> Verification successful
```

### Gotchas found while running it

- `--capflag` accepts only `PersistAcrossReset` and `InitiateReset`.
  `PopulateSystemTable` is *not* offered by edk2's CLI, though the header
  class supports it. Our capsules use `PersistAcrossReset`, so this does not
  bite us, but a platform needing the other flag would have to go through JSON
  or patch the choices list.
- In JSON mode, `HardwareInstance`, `MonotonicCount` and `UpdateImageIndex`
  must be hex *strings* (`"0x0"`); plain JSON integers fail with
  "invalid syntax". The CLI flags accept `0x`-prefixed values directly.
- `--signer-private-cert` wanting key+cert in one file is undocumented in
  `--help`; passing just the certificate fails with
  "Could not read signing key".

### The variant Launchpad would actually need

The command above signs in-process with the private key on disk, which an
archive signing service cannot do. The split that matches our existing
`--emit-signable` / `--assemble` handoff is:

- the builder produces `MSS1 || firmware.fv || monotonic_count` (what
  `FmpPayloadHeader.py` plus an 8-byte append gives);
- the signing service returns a detached PKCS#7 (DER) over exactly those bytes;
- the service then wraps it with `FmpAuthHeader.py`, `FmpCapsuleHeader.py` and
  `UefiCapsuleHeader.py`.

Those three header classes are 28 KB of dependency-free Python and are the only
part of edk2 that is strictly required; `GenerateCapsule.py` itself is just a
driver around them whose signing step would be replaced by the HSM call.
