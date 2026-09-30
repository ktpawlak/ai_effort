# Signed capsule — binary layout

Byte-level structure of `qcom-dtb-<kver>.cap`, derived from
`debian.qcom/scripts/qcom_capsule_tool/generate_capsule.py`
(constants `:42-74`, `encode_payload()` `:174-206`, `encode_capsule()`
`:209-252`).

Offsets assume a single payload, no embedded drivers — which is what the PR
produces.

---

## 1. Full layout

```
offset  size
══════════════════════════════════════════════════════════════════════════
0x0000    16  EFI_CAPSULE_HEADER
              ├─ CapsuleGuid = 6dcbd5ed-e82d-4c44-bda1-7194199ad92a
              │                EFI_FIRMWARE_MANAGEMENT_CAPSULE_ID_GUID
              │                (stored bytes_le)
0x0010     4  ├─ HeaderSize       = 0x20   (28B struct + 4B reserved)
0x0014     4  ├─ Flags            = 0x00010000  PersistAcrossReset
0x0018     4  ├─ CapsuleImageSize = 0x20 + len(body)
0x001C     4  └─ (reserved)
──────────────────────────────────────────────────────────────────────────
0x0020     4  EFI_FIRMWARE_MANAGEMENT_CAPSULE_HEADER
              ├─ Version              = 1
0x0024     2  ├─ EmbeddedDriverCount  = 0
0x0026     2  └─ PayloadItemCount     = 1
0x0028     8  ItemOffsetList[0]  → offset of the image header below
──────────────────────────────────────────────────────────────────────────
0x0030     4  EFI_FIRMWARE_MANAGEMENT_CAPSULE_IMAGE_HEADER   (48 bytes)
              ├─ Version           = 3
0x0034    16  ├─ UpdateImageTypeId = FMP GUID   ← identifies "the DTB"
              │                       0F6D58FC-2258-4D27-9E23-D77219B0897C
0x0044     1  ├─ UpdateImageIndex  = 1
0x0045     3  ├─ (pad)
0x0048     4  ├─ UpdateImageSize   = len(auth + image)
0x004C     4  ├─ UpdateVendorCodeSize = 0
0x0050     8  ├─ UpdateHardwareInstance = 0
0x0058     8  └─ ImageCapsuleSupport = 0x1  CAPSULE_SUPPORT_AUTHENTICATION
                                            ↑ 0 when unsigned
══════════════════════════════════════════════════════════════════════════
0x0060        EFI_FIRMWARE_IMAGE_AUTHENTICATION   ← the signature
           8  ├─ MonotonicCount = 0x2        (anti-replay; signed, sent in clear)
              │
              │  WIN_CERTIFICATE_UEFI_GUID
0x0068     4  ├─ dwLength   = 24 + len(PKCS#7)
0x006C     2  ├─ wRevision  = 0x0200
0x006E     2  ├─ wCertType  = 0x0EF1   WIN_CERT_TYPE_EFI_GUID
0x0070    16  ├─ CertType   = 4aafd29d-68df-49ee-8aa9-347d375665a7
              │                        EFI_CERT_TYPE_PKCS7_GUID
0x0080   var  └─ CertData = PKCS#7 SignedData (DER, detached)
                   ├─ digestAlgorithm  : sha256
                   ├─ encapContentInfo : EMPTY  ← detached
                   ├─ certificates [0] :
                   │     ├─ QcFMPCert   (leaf/signer, from -signer)
                   │     └─ QcFMPSub    (intermediate, from -certfile)
                   │     ↑↑ THE CRITICAL PART — firmware has no cert
                   │        store, so the chain to the fused root must
                   │        travel inside this blob
                   └─ signerInfos      :
                         ├─ signedAttrs : contentType, messageDigest,
                         │                signingTime
                         └─ signature   : RSA over DER(signedAttrs)
══════════════════════════════════════════════════════════════════════════
           4  FMP payload header
              ├─ Signature   = "MSS1"
           4  ├─ HeaderSize  = 16
           4  ├─ FwVersion   = 0.0.2.0     (static in the PR — see review)
           4  └─ LowestSupportedVersion = 0.0.0.0
──────────────────────────────────────────────────────────────────────────
         var  firmware.fv        ← EDK2 firmware volume
                └─ FFS file  (GUID currently random per build — bug)
                     └─ dtb.bin
                          └─ FAT16 wrapper
                               └─ qclinux_fit.img   ← FIT, UNSIGNED
                                    ├─ qcom-metadata.dtb
                                    ├─ fdt-hamoa-iot-evk.dtb
                                    ├─ fdt-lemans-evk.dtb
                                    └─ ... ~40 DTBs, no hashes
══════════════════════════════════════════════════════════════════════════
```

---

## 2. What the signature actually covers

The signed bytes are **not contiguous in the file**:

```
              ┌──────── signed ────────────────────────────┐
  auth hdr    │  "MSS1"+hdr   firmware.fv   │ MonotonicCount│
  (not signed)│  ─────────────────────────  │  (8B, appended│
              │        = "image"            │   for signing)│
              └────────────────────────────────────────────┘
                            │
                            ▼
                  sha256 ─► PKCS#7 ─► CertData (stored in auth hdr)
```

From `encode_payload()`:

```python
image   = b"MSS1" + pack("<3I", 16, fw_version, lowest_supported_version) + payload
to_sign = image + pack("<Q", monotonic_count)
cert    = PKCS#7(detached, DER, sha256) over to_sign
```

So the monotonic count is appended for the digest, then transmitted separately
at offset 0x60. It is signed but not adjacent to what it signs. The auth header
itself is excluded, since it carries the signature.

The signing call (`sign_payload_openssl()` `:134-162`):

```sh
openssl smime -sign -binary -outform DER -md sha256 \
    -signer $OpenSslSignerPrivateCertFile \
    -certfile $OpenSslOtherPublicCertFile
```

No `-nocerts` and no `-noattr` — which is why the chain and the signed
attributes are present. See
[`capsule-signing-format.md`](capsule-signing-format.md) for why that matters
and why no Launchpad mode reproduces it.

---

## 3. Signed vs unsigned

| | Signed | Unsigned |
|---|---|---|
| Auth header (0x60) | present, ~1 KB | **absent entirely** |
| `ImageCapsuleSupport` | `0x1` | `0x0` |
| Payload starts at | 0x60 + authlen | 0x60 |

This is a structural difference, not a flag flip. The unsigned capsule built
during testing was 67056 bytes against a 66944-byte FV — a 112-byte delta,
because there is no authentication header at all.

`ImageCapsuleSupport` is derived at assembly time from `descriptor.sign`
(`encode_capsule()` `:221`), so an unsigned build cannot be made to *claim*
authentication support. The firmware sees `0` and must decide whether to accept
an unauthenticated image — which is exactly the EVK question that gates the
interim plan.

---

## 4. Consequences for the Launchpad port

**Outer headers depend on `len(cert)`.** Both `CapsuleImageSize` (0x18) and
`UpdateImageSize` (0x48) include the signature length, and `ItemOffsetList`
shifts with it. A signing service therefore cannot return a blob to be pasted
into a pre-built capsule — the headers must be assembled *after* signing.

This is why `generate_capsule.py` needs a `--signature <der>` mode that skips
`sign_payload_openssl()` and assembles the capsule around an externally
supplied signature. Small change, but a prerequisite for **any** split build
where signing happens outside the package.

**`firmware.fv` is the correct handoff artifact.** It is produced by pipeline
step 3, before any certificate path is written in step 4, so it is provably
cert-independent. Everything above it in the diagram is assembled around the
signature.

**The inner FIT is unsigned.** Trust is transitive only at the moment of
application: the capsule signature covers the FIT bytes in transit, but once
written to `dtb_a`/`dtb_b` the FIT carries no integrity data of its own. See
[`capsule-signing-analysis.md`](capsule-signing-analysis.md).
