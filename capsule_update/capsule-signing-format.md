# Capsule signature format — why Launchpad cannot produce it

Deep-dive on the blocker summarised in
[`launchpad-port-plan.md`](launchpad-port-plan.md) section 3.

Short version: the Qualcomm capsule needs a PKCS#7 blob **with the certificate
chain embedded inside it**, because the UEFI firmware has no other way to get
those certificates. Every Launchpad detached-signature mode deliberately strips
them.

---

## 1. What the capsule format requires

`generate_capsule.py:134-162` (`sign_payload_openssl`) shells out to:

```sh
openssl smime -sign -binary -outform DER -md sha256 \
    -signer $OpenSslSignerPrivateCertFile \
    -certfile $OpenSslOtherPublicCertFile
```

What matters is what is **absent**: no `-nocerts`, and no `-noattr`.
The resulting PKCS#7 `SignedData` therefore contains:

- **`certificates [0] IMPLICIT`** — the signer (leaf) certificate from
  `-signer`, plus every intermediate supplied via `-certfile`.
- **`signedAttrs`** — content-type, message-digest and signing-time. The RSA
  signature covers the DER encoding of *those attributes*, not the payload
  bytes directly.
- **no encapsulated content** — `openssl smime -sign` is detached by default
  (`-nodetach` would embed the content).

### The signed blob

From `encode_payload()` (`generate_capsule.py:174-197`):

```
image   = b"MSS1" + pack("<3I", 16, fw_version, lowest_supported_version)
        + <firmware.fv>
to_sign = image + pack("<Q", monotonic_count)
cert    = PKCS#7(detached, DER, sha256) over to_sign
auth    = pack("<Q", monotonic_count)
        + pack("<IHH", 24 + len(cert), 0x0200, 0x0EF1)
        + EFI_CERT_TYPE_PKCS7_GUID.bytes_le
        + cert
payload = auth + image
```

The monotonic count is signed but travels separately in the authentication
header. Relevant constants (`generate_capsule.py:45-74`):

| Constant | Value |
|---|---|
| `EFI_CERT_TYPE_PKCS7_GUID` | `4aafd29d-68df-49ee-8aa9-347d375665a7` |
| `WIN_CERT_REVISION` | `0x0200` |
| `WIN_CERT_TYPE_EFI_GUID` | `0x0EF1` |
| `WIN_CERT_PREFIX_LEN` | 24 |
| `FMP_PAYLOAD_SIGNATURE` | `b"MSS1"` |
| `DEFAULT_HASH_ALGORITHM` | `sha256` |

So the structure is a standard `EFI_FIRMWARE_IMAGE_AUTHENTICATION`: a
`WIN_CERTIFICATE_UEFI_GUID` whose `CertType` is `EFI_CERT_TYPE_PKCS7_GUID`,
followed by the FMP payload.

Because the outer `EFI_CAPSULE_HEADER` lengths depend on `len(cert)`, the outer
headers must be assembled **after** signing. They cannot be pre-baked and
patched, which constrains any split-build design.

---

## 2. Why the certificates must be embedded

This is the crux.

When the device applies the capsule, the UEFI firmware calls its PKCS#7
verification path (EDK2 `Pkcs7Verify` / `AuthenticodeVerify` in `BaseCryptLib`)
with three inputs:

1. the PKCS#7 blob,
2. the content that was signed,
3. **one** trust anchor — `QcCapsuleRootCert`, burned into firmware.

The firmware has no certificate store, no filesystem lookup, no network, and no
AIA chasing. Its only source for the intermediate and leaf certificates needed
to walk

```
leaf (QcFMP signer) -> sub/intermediate -> QcCapsuleRootCert
```

is the `certificates` field **inside the PKCS#7 blob itself**. EDK2 populates
its `X509_STORE_CTX` from that field and verifies against the single supplied
root.

If the signature arrives with the certificates stripped, the firmware holds a
signature it cannot attribute to any key it is able to validate, and fails
closed. This is not a policy that can be relaxed — there is nowhere else for
those certificates to come from.

This is exactly why the Qualcomm flow passes `-certfile QcFMPSub.pub.pem`.

---

## 3. What Launchpad produces instead

From `lib/lp/archivepublisher/signing.py` (extension map `:291-304`,
implementations `:587-792`):

| Ext | Mode | Command | Output |
|---|---|---|---|
| `.efi` | UEFI | `sbsign --key --cert` | `.signed`, attached PE |
| `.ko` | KMOD | `kmodsign -D sha512` | `.sig` |
| `.opal` | OPAL | `kmodsign -D sha512` | `.sig` |
| `.sipl` | SIPL | `kmodsign -D sha512` | `.sig` |
| `.fit` | FIT | `mkimage -F -k <dir> -r` | `.signed`, in place |
| `.cv2-kernel` | CV2_KERNEL | signing service | `.sig` |
| `.android-kernel` | ANDROID_KERNEL | signing service | `.sig` |

The three detached modes (`.ko`, `.opal`, `.sipl`) all run `kmodsign -D`, which
produces the **kernel module** signature format. Confirmed directly in
`scripts/sign-file.c` in this tree (`:275-283`):

```c
flags = CMS_NOCERTS | CMS_NOATTR | CMS_DETACHED | CMS_BINARY ...
```

and the trailer at `:358-360`:

```c
sig_info.sig_len = htonl(sig_size);
BIO_write(bd, &sig_info, sizeof(sig_info));       /* struct module_signature */
BIO_write(bd, magic_number, sizeof(magic_number) - 1);
```

with `magic_number = "~Module signature appended~\n"` (`:55`).

### The mismatch

| | Capsule needs | `kmodsign -D` produces |
|---|---|---|
| Cert chain | embedded (`-signer` + `-certfile`) | **`CMS_NOCERTS`** — stripped |
| Signed attrs | present; signature covers `signedAttrs` | **`CMS_NOATTR`** — covers raw content |
| Framing | bare PKCS#7 inside `WIN_CERTIFICATE_UEFI_GUID` | + `struct module_signature` + magic string |
| Digest | `sha256` | `sha512`, hardcoded in Launchpad |

Any one of these is fatal on its own.

`CMS_NOCERTS` is entirely correct *for modules*: the kernel already holds the
signing certificate in its keyring and matches by key identifier, so embedding
certificates in every `.ko` would be pure bloat. It is simply the opposite of
what a capsule needs.

Note also that `kmodsign` has a `-d` flag that emits a bare `.p7s` without the
module trailer — but Launchpad never invokes it, so it is not reachable.

The other modes do not help either:

- `.efi` / `sbsign` produces an *attached* Authenticode signature embedded in a
  PE certificate table. Wrong container, and the capsule is not a PE binary.
- `.fit` / `mkimage -F -r` signs FIT image nodes in place. Wrong format
  entirely. (Also worth noting: the capsule's inner FIT is **not** signed at
  all — see [`capsule-signing-analysis.md`](capsule-signing-analysis.md).)

---

## 4. The trust-anchor problem, which survives any format fix

Even if Launchpad grew a capsule mode emitting a byte-perfect PKCS#7, the
device would still reject it: the chain must terminate at `QcCapsuleRootCert`,
and a Canonical-held key does not chain to a Qualcomm root.

A telling detail — `OpenSslTrustedPublicCertFile`:

- `generate_capsule.py:105` reads it,
- `:116-123` **requires** it (all three certs must be set, or none),
- `sign_payload_openssl()` never passes it to openssl.

It is validated and discarded. It represents the root that must *already* be
provisioned into the firmware — which is precisely what the PR's
currently-unused `patch_capsule_cert.py` exists to do.

So there are two independent requirements:

1. a signing service that emits this exact format, **and**
2. a device whose firmware trusts the resulting root.

The second is a provisioning/business question, not a packaging one. Either
Qualcomm signs Canonical's capsules, or Qualcomm provisions a Canonical-rooted
`QcCapsuleRootCert` into shipping firmware. Neither can be solved in
`debian/`.

### 4.1 No, the signature cannot be faked

A natural question is whether PR #111 signs with a throwaway self-signed CA —
which would imply the firmware is not actually checking, and that the "signing"
could simply be reproduced locally. It does not.

There is no certificate generation anywhere in the tree. The certs are genuine
Qualcomm FMP certificates injected from GitHub Secrets by PR #112
(`secrets.FMPCERT` / `FMPROOT` / `FMPSUB`, base64-decoded into
`debian.qcom/certs/`). `QcFMPCert.pem` carries a **private key** — the openssl
call passes `-signer` with no `-inkey`, so the key is read from that same file.

The fact that these are held as repository secrets, split leaf/sub/root, and
named to match the fused `QcCapsuleRootCert` is itself the evidence that the
firmware enforces. Without the real private key nothing producible will
validate against the fused root.

See `launchpad-port-plan.md` section 1.1, which also records the resulting
security escalation: `secrets: inherit` on a `pull_request` workflow building
fork merge refs on a self-hosted runner exposes that private key to arbitrary
fork-supplied code.

---

## 5. Consequences for the port

- Option (a) in the plan — a new Launchpad signing mode — is necessary but
  **not sufficient**; it must be paired with firmware re-provisioning.
  `cv2-kernel` and `android-kernel` are precedent that vendor-specific modes
  do get added.
- Option (b) — Qualcomm signs out of band, capsule ships as a prebuilt blob —
  sidesteps both problems and is the lowest-risk path to a *signed* capsule.
- Option (c) — unsigned, development only — is what section 8 of the plan
  builds. It sets `capsule_support = 0` instead of
  `CAPSULE_SUPPORT_AUTHENTICATION`, so it works only on unfused EVKs whose
  firmware skips FMP authentication. Since section 4.1 establishes that the
  firmware does enforce, verifying that the target board accepts an unsigned
  capsule is a prerequisite, not an afterthought.

The practical value of (c) is that it exercises the whole build, staging,
`OsIndications` and post-boot verification pipeline while the signing question
is negotiated separately. The signing step is the *last* of five pipeline
stages and the only one that touches a key, so nothing else in the design is
blocked by it.

### If a split build is ever implemented

`firmware.fv` (pipeline step 3) is produced before any certificate path is
written (step 4), so it is provably cert-independent and is the correct handoff
artifact to a signing service. But note from section 1 above: the outer capsule
headers depend on `len(cert)` and so must be assembled *after* signing. A
signing service therefore cannot simply return a blob to be pasted into a
pre-built capsule — `generate_capsule.py` would need a `--signature <der>` mode
that skips `sign_payload_openssl()` and assembles the headers around a supplied
signature.
